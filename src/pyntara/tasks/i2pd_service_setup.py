"""Task i2pd_service_setup: install the newest i2pd release as a system service.

The task installs i2pd from the GitHub releases of the configured
repository, so the running version is always the newest release instead
of the distribution package. The latest release tag comes from the GitHub
releases API (https://api.github.com/repos/{repo}/releases/latest); the
package asset is chosen by the dpkg architecture and the distribution
codename from /etc/os-release, with the generic asset of the release as
the fallback, so a release without a build for this distribution still
installs. The package is downloaded from the official GitHub release
assets without a checksum verification: the source is trusted, and the
extra check would add a failure point without protecting the install.
The task owns the main configuration file at the configured config_path:
it renders the template at task_data/i2pd_service_setup/i2pd.conf and
rewrites the file whenever the content differs, so manual edits are
reverted on the next run. The config_path must match the --conf path of
the package unit, otherwise the rendered values are ignored. The service
is enabled and started or restarted immediately, and the task waits with
the configured readiness loop for it to become active, because the
forking service may take a moment to fork. The task is idempotent: it
skips when the installed version equals the newest release tag, the
configuration matches the rendered template and the service is enabled
and active; force mode rewrites the configuration and restarts the
service but never reinstalls a matching version.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from string import Template

from pyntara.config import I2pdServiceSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    APT_NONINTERACTIVE_ENV,
    dpkg_architecture,
    ensure_root_owner,
    install_package_once,
    os_family_is_debian,
    read_os_release,
    run_command,
    service_is_active,
    service_is_enabled,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# /etc/os-release is a fixed machine contract (architecture contract
# section 3); the repository layout path is fixed by the repo itself.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "task_data" / "i2pd_service_setup" / "i2pd.conf"
OS_RELEASE_PATH = Path("/etc/os-release")

# i2pd prints its version as a dotted triple in the --version output.
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")


def _render_config(cfg: I2pdServiceSetupConfig) -> str:
    """Render the configuration template with the configured values.

    Boolean options are rendered as the true/false spelling i2pd accepts,
    so the rendered file, the idempotency comparison and the written
    configuration share one representation. The template carries no shell
    variables of its own, so substitute cannot trip on stray dollar
    signs.
    """

    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        log_level=cfg.log_level,
        http_enabled="true" if cfg.http_enabled else "false",
        socks_proxy_enabled="true" if cfg.socks_proxy_enabled else "false",
    )


def _release_tag(release: dict[str, object]) -> str:
    """The tag_name of a release payload; raises RuntimeError when absent."""

    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("release payload has no tag_name")
    return tag


def _asset_name_urls(release: dict[str, object]) -> list[tuple[str, str]]:
    """The (name, download_url) pairs of the release assets.

    Malformed asset entries are skipped; the list is ordered as returned
    by the API.
    """

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TypeError("release payload has no assets array")
    result: list[tuple[str, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result.append((name, url))
    return result


def _select_asset(
    release: dict[str, object],
    tag: str,
    codename: str | None,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The codename-specific asset i2pd_{tag}-1{codename}1_{arch}.deb wins,
    because it is built against this distribution; the generic asset
    i2pd_{tag}-1_{arch}.deb is the fallback.
    """

    assets = dict(_asset_name_urls(release))
    candidates: list[str] = []
    if codename:
        candidates.append(f"i2pd_{tag}-1{codename}1_{arch}.deb")
    candidates.append(f"i2pd_{tag}-1_{arch}.deb")
    for candidate in candidates:
        if candidate in assets:
            return candidate, assets[candidate]
    return None


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
    """The installed i2pd version from i2pd --version, or None.

    A missing binary, a nonzero exit or a hang means i2pd is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            ["i2pd", "--version"],
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
    update_timeout: float,
    retries: int,
    skip_update: bool,
) -> tuple[bool, str]:
    """Install the downloaded deb; return (success, error_text).

    The apt index is refreshed once before the install, so dependencies
    resolve from a fresh index; skip_update=True disables the refresh for
    test or offline runs. Each attempt uses the shared noninteractive
    apt environment; total attempts are one initial plus retries.
    """

    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_NONINTERACTIVE_ENV,
                timeout=update_timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
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


def _read_config(config_path: Path) -> str | None:
    """Current content of the configuration file, or None when absent."""

    try:
        return config_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_config(cfg: I2pdServiceSetupConfig) -> None:
    """Write the rendered configuration into the configured path."""

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(_render_config(cfg), encoding="utf-8")
    ensure_root_owner(cfg.config_path)


def _wait_active(
    service_name: str,
    attempts: int,
    retry_delay_seconds: float,
    timeout: float,
) -> bool:
    """True when the service reports active within the readiness loop.

    The forking service may report activating for a moment after start,
    so the check is repeated with a pause until attempts run out.
    """

    for _ in range(attempts):
        time.sleep(retry_delay_seconds)
        if service_is_active(service_name, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    """Install the newest i2pd release and run it as a service; skip when done.

    The goal is reached when the installed version equals the newest
    release tag, the configuration file matches the rendered template
    and the service is enabled and active; the task then returns
    changed=False. Otherwise it downloads the matching .deb asset from
    the release, installs it, writes the configuration, enables the
    service, starts or restarts it and waits for it to become active.
    Every step is reported to stdout:
    measurements and decisions as single lines that include their
    result, long-running commands as a line before and a line after. Any
    failure is returned as an error TaskResult: the runner continues
    with the remaining tasks and never stops here.
    """

    cfg = ctx.config.i2pd_service_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "i2pd_service_setup" in ctx.force_tasks

    try:
        os_release = read_os_release(OS_RELEASE_PATH)
    except OSError as exc:
        return TaskResult(
            success=False, error=f"cannot read {OS_RELEASE_PATH}: {exc}"
        )
    if not os_family_is_debian(os_release):
        return TaskResult(
            success=False,
            error=(
                "i2pd deb packages require a Debian-based distribution; "
                f"os-release ID={os_release.get('ID', '')} "
                f"ID_LIKE={os_release.get('ID_LIKE', '')}"
            ),
        )
    _log(
        f"reading {OS_RELEASE_PATH}: ID={os_release.get('ID', '')}, "
        f"VERSION_CODENAME={os_release.get('VERSION_CODENAME', '')}"
    )
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
    _log(f"checking latest release: {tag}")

    codename = os_release.get("VERSION_CODENAME")
    selected = _select_asset(release, tag, codename, arch)
    if selected is None:
        return TaskResult(
            success=False,
            error=(
                f"release {tag} has no .deb asset for arch {arch}, "
                f"codename {codename or 'generic'}"
            ),
        )
    asset_name, asset_url = selected
    _log(f"selected asset: {asset_name}")

    installed_version = _installed_version(timeout)
    _log(f"checking installed version: {installed_version or 'not installed'}")

    target_config = _render_config(cfg)
    current_config = _read_config(cfg.config_path)
    # An install rewrites the package conffile, so the configuration is
    # rewritten after an install even when it matched before.
    config_changed = force or installed_version != tag or (
        current_config != target_config
    )
    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = installed_version != tag
    if not force and not needs_install and not config_changed and enabled and active:
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
            update_timeout=timeout,
            retries=cfg.install_retries,
            skip_update=ctx.skip_apt_update,
        )
        if not ok:
            return TaskResult(success=False, error=f"cannot install i2pd: {error}")
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

    if config_changed:
        _log(f"writing configuration {cfg.config_path}")
        try:
            _write_config(cfg)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write configuration: {exc}",
            )
        _log("configuration written")
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

    if not active or needs_install or config_changed or force:
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
        _log(
            f"waiting for service to become active (up to "
            f"{cfg.start_check_attempts} checks)"
        )
        if not _wait_active(
            cfg.service_unit_name,
            cfg.start_check_attempts,
            cfg.start_check_retry_delay_seconds,
            timeout,
        ):
            return TaskResult(
                success=False,
                changed=True,
                error=(
                    f"{cfg.service_unit_name} did not become active after "
                    f"{cfg.start_check_attempts} checks"
                ),
            )
        _log("service active")
        changed = True

    return TaskResult(
        success=True,
        changed=changed,
        message=(
            f"i2pd {tag} installed, service {cfg.service_unit_name} active"
        ),
    )
