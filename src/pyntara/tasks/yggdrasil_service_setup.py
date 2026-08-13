"""Task yggdrasil_service_setup: install the newest yggdrasil release as a system service.

The task installs yggdrasil from the GitHub releases of the configured
repository, so the running version is always the newest release instead
of the distribution package. The latest release tag comes from the GitHub
releases API (https://api.github.com/repos/{repo}/releases/latest); the
deb asset yggdrasil-{version}-{arch}.deb is chosen by the dpkg
architecture, whose name matches the architecture part of the asset name.
The package is downloaded from the official GitHub release assets without
a checksum verification: the source is trusted, and the extra check would
add a failure point without protecting the install. The package owns the
configuration and the node keys: its postinst generates /etc/yggdrasil/
yggdrasil.conf with a fresh key pair and enables and starts the service,
so the task never writes the configuration and keeps the node identity
across reinstalls. The apt index is not refreshed, because the package
depends only on systemd, which is always installed. The task is
idempotent: it skips when the installed version equals the newest release
tag and the service is enabled and active; force mode restarts the
service but never reinstalls a matching version.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    dpkg_architecture,
    install_package_once,
    run_command,
    service_is_active,
    service_is_enabled,
)

# The yggdrasil version string from yggdrasil -version, e.g. Build
# version: 0.5.14; the release tag carries a leading v, the asset and the
# version output do not.
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")


def _release_tag(release: dict[str, object]) -> str:
    """The tag_name of a release payload; raises RuntimeError when absent."""

    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("release payload has no tag_name")
    return tag


def _asset_name_urls(release: dict[str, object]) -> dict[str, str]:
    """The name to download_url mapping of the release assets.

    Malformed asset entries are skipped; a name collision keeps the first
    entry, because the list is ordered as returned by the API.
    """

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TypeError("release payload has no assets array")
    result: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result.setdefault(name, url)
    return result


def _select_asset(
    release: dict[str, object],
    version: str,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The asset name is yggdrasil-{version}-{arch}.deb; the architecture
    part matches the dpkg architecture, so no codename-specific fallback
    is needed.
    """

    name = f"yggdrasil-{version}-{arch}.deb"
    url = _asset_name_urls(release).get(name)
    return (name, url) if url else None


def _fetch_release_json(repo: str, timeout: float) -> dict[str, object]:
    """The latest release payload from the GitHub releases API.

    Raises RuntimeError when the request fails or the payload is not
    usable JSON, so the caller reports the reason instead of a raw
    exception.
    """

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    result = run_command(
        ["curl", "--fail", "--silent", "--show-error", url],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch {url}: exit {result.returncode}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cannot parse release JSON from {url}: {exc}") from None
    if not isinstance(data, dict):
        raise TypeError(f"unexpected release payload from {url}")
    return data


def _installed_version(timeout: float) -> str | None:
    """The installed yggdrasil version from yggdrasil -version, or None.

    A missing binary, a nonzero exit or a hang means yggdrasil is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            ["yggdrasil", "-version"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    match = VERSION_PATTERN.search(result.stdout + "\n" + result.stderr)
    return match.group(1) if match else None


def _download_asset(
    download_dir: Path, name: str, url: str, timeout: float
) -> None:
    """Download the package into the download directory.

    Raises RuntimeError when curl fails, so the caller reports the
    reason.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_command(
            [
                "curl",
                "--fail",
                "--silent",
                "--location",
                "--show-error",
                "--output",
                str(download_dir / name),
                url,
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot download {url}: {exc}") from None


def _install_deb(
    download_dir: Path,
    name: str,
    *,
    install_timeout: float,
    retries: int,
) -> tuple[bool, str]:
    """Install the downloaded deb; return (success, error_text).

    The apt index is not refreshed: the package depends only on systemd,
    which is always installed, so a refresh would add a failure point
    without resolving anything. Each attempt uses the shared
    noninteractive apt environment; total attempts are one initial plus
    retries.
    """

    ok = False
    error = ""
    for _ in range(retries + 1):
        ok, error = install_package_once(
            str(download_dir / name), install_timeout
        )
        if ok:
            break
    return ok, error


def _cleanup_downloads(download_dir: Path, name: str) -> None:
    """Remove the downloaded package.

    The file is a diagnostic for a failed install; after a successful
    install it is stale and is removed so the download directory never
    accumulates old versions.
    """

    try:
        (download_dir / name).unlink()
    except FileNotFoundError:
        pass


def task(ctx: Context) -> TaskResult:
    """Install the newest yggdrasil release and run it as a service; skip when done.

    The goal is reached when the installed version equals the newest
    release tag and the service is enabled and active; the task then
    returns changed=False. Otherwise it downloads the matching .deb asset
    from the release, installs it, enables the service, starts or
    restarts it and checks once that it became active. Every step is
    reported to stdout: measurements and decisions as single lines that
    include their result, long-running commands as a line before and a
    line after. Any failure is returned as an error TaskResult: the
    runner continues with the remaining tasks and never stops here.
    """

    cfg = ctx.config.yggdrasil_service_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "yggdrasil_service_setup" in ctx.force_tasks

    try:
        arch = dpkg_architecture(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False, error=f"cannot determine dpkg architecture: {exc}"
        )
    _log(f"reading dpkg architecture: {arch}")

    try:
        release = _fetch_release_json(cfg.github_repo, timeout)
        tag = _release_tag(release)
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    version = tag.removeprefix("v")
    _log(f"checking latest release: {version}")

    selected = _select_asset(release, version, arch)
    if selected is None:
        return TaskResult(
            success=False,
            error=(
                f"release {version} has no yggdrasil-{version}-{arch}.deb asset"
            ),
        )
    asset_name, asset_url = selected
    _log(f"selected asset: {asset_name}")

    installed_version = _installed_version(timeout)
    _log(f"checking installed version: {installed_version or 'not installed'}")

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = installed_version != version
    if not force and not needs_install and enabled and active:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    if needs_install:
        _log(f"downloading {asset_name} into {cfg.download_dir}")
        try:
            _download_asset(cfg.download_dir, asset_name, asset_url, timeout)
        except RuntimeError as exc:
            return TaskResult(success=False, error=str(exc))
        _log("package downloaded")
        _log(f"installing package: apt-get install -y {asset_name}")
        ok, error = _install_deb(
            cfg.download_dir,
            asset_name,
            install_timeout=timeout,
            retries=cfg.install_retries,
        )
        if not ok:
            return TaskResult(success=False, error=f"cannot install yggdrasil: {error}")
        _log("package installed")
        try:
            _cleanup_downloads(cfg.download_dir, asset_name)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=True,
                error=f"cannot remove downloaded files: {exc}",
            )
        changed = True

    if not enabled:
        _log(f"enabling service: systemctl enable {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "enable", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl enable failed: {exc}",
            )
        _log("service enabled")
        changed = True

    if not active or needs_install or force:
        action = "restart" if active else "start"
        _log(
            f"{action}ing service: systemctl {action} {cfg.service_unit_name}"
        )
        try:
            run_command(
                ["systemctl", action, cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl {action} failed: {exc}",
            )
        _log(f"service {action}ed")
        if not service_is_active(cfg.service_unit_name, timeout):
            return TaskResult(
                success=False,
                changed=True,
                error=(
                    f"service {cfg.service_unit_name} did not become active "
                    "after " + action
                ),
            )
        _log("service active")
        changed = True

    return TaskResult(
        success=True,
        changed=changed,
        message=(
            f"yggdrasil {version} installed, service {cfg.service_unit_name} active"
        ),
    )
