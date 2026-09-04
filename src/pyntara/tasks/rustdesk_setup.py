"""Task rustdesk_setup: install and configure the RustDesk client.

The task installs the newest RustDesk client release from the GitHub
releases of the configured repository as a deb and configures it for
unattended remote access through the public RustDesk rendezvous server.
The controlled machine registers its ID with the public server, so any
RustDesk client (a phone or another machine) reaches it by typing the ID
and the permanent password, without configuring a server or a key. The
permanent password is a per-machine secret: the task generates
password_words random proquint words joined by password_separator, stores
them in the runtime vault entry named by vault_entry_title and applies
them through rustdesk --password, so every machine of the fleet gets its
own credential that survives in the vault backup. The machine ID is
written to id_file_path, so the System Metrics collector includes it in
the network report (docs/spec/rustdesk-setup.md).

The client options (UDP hole punching, IPv6 punching, direct access,
headless Linux, adaptive bitrate and the access mode) come from the
[rustdesk_setup.options] tables of the config and are applied through
rustdesk --option; the task reads the current value and sets the option
only when it differs, so the options are idempotent.

The task is idempotent: a normal run keeps the installed version, the
generated password and the machine ID (a persistent identity per the
task model contract); force mode regenerates the password and removes the
rustdesk identity file inside config_dir, so the service generates a
fresh machine ID. The service rustdesk.service is enabled and started so
the machine is reachable unattended. A failure is an error TaskResult:
the runner continues with the remaining tasks and never stops here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from pyntara import metrics
from pyntara.config import RustdeskSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    APT_NONINTERACTIVE_ENV,
    CURL_DOWNLOAD_WRITE_OUT,
    curl_flags,
    dpkg_architecture,
    ensure_root_owner,
    install_package_once,
    proquint_encode,
    run_command,
    service_is_active,
    service_is_enabled,
)

# The rustdesk --version output is a bare dotted triple, e.g. 1.4.9.
VERSION_PATTERN = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

# The release tag carries no leading v for rustdesk releases; the
# normalization strips one anyway, so a future v-prefixed tag still
# compares equal to the version output.
TAG_VERSION_PATTERN = re.compile(r"^v?")

# rustdesk deb asset names use the upstream architecture spelling, while
# dpkg reports the Debian one; the mapping covers the common targets and
# any other architecture falls back to the dpkg spelling as is.
DPKG_TO_ASSET_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}


def _normalized_version(value: str) -> str:
    """The version with an optional leading v stripped."""

    return TAG_VERSION_PATTERN.sub("", value)


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
    """The (name, url) of the rustdesk deb for this machine, or None.

    The asset name is rustdesk-{version}-{arch}.deb; the architecture
    part uses the upstream spelling mapped from the dpkg architecture.
    """

    asset_arch = DPKG_TO_ASSET_ARCH.get(arch, arch)
    name = f"rustdesk-{version}-{asset_arch}.deb"
    url = _asset_name_urls(release).get(name)
    return (name, url) if url else None


def _fetch_release_json(
    repo: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> dict[str, object]:
    """The latest release payload from the GitHub releases API.

    Raises RuntimeError when the request fails or the payload is not
    usable JSON, so the caller reports the reason instead of a raw
    exception.
    """

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    result = run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            *curl_flags(curl_timeout, retries, connect_timeout, retry_max_time),
            url,
        ],
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
    """The installed rustdesk version from rustdesk --version, or None.

    A missing binary, a nonzero exit or a hang means rustdesk is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    is caught together with the timeout.
    """

    try:
        result = run_command(
            ["rustdesk", "--version"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    match = VERSION_PATTERN.search(result.stdout)
    return match.group(1) if match else None


def _download_deb(
    download_dir: Path,
    name: str,
    url: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
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
                "--location",
                "--show-error",
                "--output",
                str(download_dir / name),
                "--write-out",
                CURL_DOWNLOAD_WRITE_OUT,
                *curl_flags(curl_timeout, retries, connect_timeout, retry_max_time),
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


def _cleanup_download(download_dir: Path, name: str) -> None:
    """Remove the downloaded package after a successful install."""

    try:
        (download_dir / name).unlink()
    except FileNotFoundError:
        pass


def _machine_id(timeout: float) -> str | None:
    """The machine RustDesk ID from rustdesk --get-id, or None.

    The command needs the running rustdesk daemon, so it may return None
    before the service is ready; the readiness loop retries it.
    """

    try:
        result = run_command(
            ["rustdesk", "--get-id"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _get_option(key: str, timeout: float) -> str | None:
    """The current value of a rustdesk option, or None.

    A missing value or a failed query means the option is not set, so the
    caller sets it.
    """

    try:
        result = run_command(
            ["rustdesk", "--option", key],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _set_option(key: str, value: str, timeout: float) -> bool:
    """Set one rustdesk option through rustdesk --option; True on success."""

    try:
        run_command(
            ["rustdesk", "--option", key, value],
            check=True,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot set rustdesk option {key}: {exc}")
        return False
    return True


def _apply_options(cfg: RustdeskSetupConfig, timeout: float) -> tuple[bool, str]:
    """Apply the configured options; return (changed, error).

    Each option is read first and set only when it differs, so a rerun
    that already carries the values changes nothing.
    """

    changed = False
    for option in cfg.options:
        current = _get_option(option.key, timeout)
        if current == option.value:
            continue
        if not _set_option(option.key, option.value, timeout):
            return False, f"cannot set rustdesk option {option.key}"
        _log(f"set rustdesk option {option.key} to {option.value!r}")
        changed = True
    return changed, ""


def _set_password(password: str, timeout: float) -> tuple[bool, str]:
    """Set the permanent rustdesk password; return (success, error_text).

    The password is a secret, so the command is never logged
    (project rules, General engineering requirements).
    """

    try:
        run_command(
            ["rustdesk", "--password", password],
            check=True,
            capture=True,
            timeout=timeout,
            log_command=False,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return True, ""


def _ensure_vault_password(
    ctx: Context, force: bool
) -> tuple[str | None, str | None, bool]:
    """The permanent rustdesk password; returns (password, warning, regenerated).

    The password lives in the runtime vault entry named by
    vault_entry_title. A normal run reuses the stored value, so the
    machine keeps its password; force mode (or a missing entry) generates
    a fresh password of password_words proquint words joined by
    password_separator, writes it into the entry and saves the vault, and
    regenerated is True. A vault that cannot be opened returns
    (None, warning, False): the password is not changed, so an existing
    access credential is never lost silently.
    """

    cfg = ctx.config.rustdesk_setup
    kp = metrics.open_runtime_vault(ctx.config)
    if kp is None:
        return None, "runtime vault unavailable: rustdesk password not stored", False
    entry = kp.find_entries(
        title=cfg.vault_entry_title,
        group=kp.root_group,
        recursive=False,
        first=True,
    )
    if entry is not None and entry.password and not force:
        _log("reusing the stored rustdesk password")
        return entry.password, None, False
    password = proquint_encode(
        os.urandom(2 * cfg.password_words), separator=cfg.password_separator
    )
    note = (
        "Permanent RustDesk access password of this machine, generated by "
        "the rustdesk_setup task as "
        f"{cfg.password_words} random proquint words. Applied through "
        "rustdesk --password; the machine is reachable by its RustDesk ID "
        "with this password from any RustDesk client."
    )
    if entry is not None:
        entry.password = password
        _log("regenerating the stored rustdesk password")
    else:
        kp.add_entry(
            kp.root_group,
            cfg.vault_entry_title,
            "",
            password,
            notes=note,
        )
        _log("generating and storing a fresh rustdesk password")
    kp.save(filename=str(ctx.config.local_vault_setup.local_vault_path))
    return password, None, True


def _write_id_file(cfg: RustdeskSetupConfig, machine_id: str, force: bool) -> bool:
    """Write the machine ID to id_file_path; return True when written.

    A normal run writes only when the file is missing or stale, so the
    report carries the current ID without touching a matching file;
    force mode always rewrites.
    """

    try:
        saved = cfg.id_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        saved = None
    if saved == machine_id and not force:
        return False
    cfg.id_file_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.id_file_path.write_text(f"{machine_id}\n", encoding="utf-8")
    ensure_root_owner(cfg.id_file_path)
    cfg.id_file_path.chmod(cfg.id_file_mode)
    _log(f"wrote rustdesk ID {machine_id} to {cfg.id_file_path}")
    return True


def _reset_identity(cfg: RustdeskSetupConfig) -> None:
    """Remove the rustdesk identity file so a fresh ID is generated.

    The identity (the key pair and the derived machine ID) lives in
    RustDesk.toml inside config_dir; removing it makes the service
    generate a new identity on the next start. Called only in force mode,
    after the service is stopped.
    """

    identity_path = cfg.config_dir / "RustDesk.toml"
    try:
        identity_path.unlink()
        _log("force: removed the rustdesk identity file")
    except FileNotFoundError:
        pass


def _wait_ready(cfg: RustdeskSetupConfig, timeout: float) -> bool:
    """Wait until the rustdesk daemon answers --get-id; True when ready.

    The rustdesk.service becomes active when its root --service process
    starts, but the per-session --server process that owns the IPC and
    the machine ID appears a moment later; the loop polls it up to
    start_check_attempts times with the configured pause.
    """

    for _ in range(cfg.start_check_attempts):
        if _machine_id(min(timeout, 5.0)):
            return True
        time.sleep(cfg.start_check_retry_delay_seconds)
    return False


def task(ctx: Context) -> TaskResult:
    """Install and configure rustdesk; done when the newest release runs.

    The target state is reached when the installed version equals the
    newest release tag, the service is enabled and active, every
    configured option is applied, the permanent password is stored in the
    runtime vault and applied, and the machine ID file matches the current
    ID; the task then returns changed=False. A missing version, a version
    mismatch or force mode downloads and installs the newest deb; force
    mode additionally regenerates the password and the machine identity.
    Every step is reported to stdout with its result; a failure is an
    error TaskResult, so the runner continues with the remaining tasks.
    """

    cfg = ctx.config.rustdesk_setup
    timeout = ctx.config.engine.command_timeout_seconds
    curl_timeout = ctx.config.engine.curl_timeout_seconds
    curl_retries = ctx.config.engine.curl_retries
    connect_timeout = ctx.config.engine.curl_connect_timeout_seconds
    retry_max_time = ctx.config.engine.curl_retry_max_time_seconds
    force = "rustdesk_setup" in ctx.force_tasks
    changed = False

    try:
        release = _fetch_release_json(
            cfg.github_repo,
            timeout,
            curl_timeout,
            curl_retries,
            connect_timeout,
            retry_max_time,
        )
        tag = _normalized_version(_release_tag(release))
    except (RuntimeError, TypeError) as exc:
        return TaskResult(success=False, error=str(exc))
    _log(f"checking latest rustdesk release: {tag}")

    installed = _installed_version(timeout)
    _log(f"checking installed rustdesk version: {installed or 'not installed'}")

    if installed != tag:
        try:
            arch = dpkg_architecture(timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(success=False, error=f"cannot read dpkg architecture: {exc}")
        selected = _select_asset(release, tag, arch)
        if selected is None:
            return TaskResult(
                success=False,
                error=(
                    f"no rustdesk deb asset for architecture {arch} in "
                    f"release {tag}"
                ),
            )
        name, url = selected
        _log(f"downloading rustdesk {tag} deb")
        try:
            _download_deb(
                cfg.download_dir,
                name,
                url,
                timeout,
                curl_timeout,
                curl_retries,
                connect_timeout,
                retry_max_time,
            )
        except RuntimeError as exc:
            return TaskResult(success=False, error=str(exc))
        _log("installing rustdesk deb")
        ok, error = _install_deb(
            cfg.download_dir,
            name,
            install_timeout=cfg.install_timeout_seconds,
            update_timeout=cfg.apt_update_timeout_seconds,
            retries=cfg.install_retries,
            skip_update=ctx.skip_apt_update,
        )
        if not ok:
            return TaskResult(
                success=False, changed=True, error=f"rustdesk install failed: {error}"
            )
        _cleanup_download(cfg.download_dir, name)
        _log("rustdesk installed")
        changed = True
    else:
        _log("newest rustdesk version already installed")

    # Force mode regenerates the machine identity: stop the service,
    # remove the identity file, then start it again below.
    if force:
        run_command(
            ["systemctl", "stop", cfg.service_unit_name],
            check=False,
            timeout=timeout,
        )
        _reset_identity(cfg)
        changed = True

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    if not enabled:
        _log(f"enabling service {cfg.service_unit_name}")
        run_command(
            ["systemctl", "enable", cfg.service_unit_name],
            check=True,
            timeout=timeout,
        )
        changed = True
    if not active:
        _log(f"starting service {cfg.service_unit_name}")
        run_command(
            ["systemctl", "start", cfg.service_unit_name],
            check=True,
            timeout=timeout,
        )
        changed = True

    if not _wait_ready(cfg, timeout):
        return TaskResult(
            success=False,
            changed=changed,
            error="rustdesk daemon did not answer after the service start",
        )
    _log("rustdesk daemon ready")

    options_changed, options_error = _apply_options(cfg, timeout)
    if options_error:
        return TaskResult(
            success=True, changed=changed, warnings=(options_error,)
        )
    if options_changed:
        changed = True

    password, password_warning, password_regenerated = _ensure_vault_password(
        ctx, force
    )
    if password is None:
        return TaskResult(
            success=True,
            changed=changed,
            warnings=(password_warning or "rustdesk password unavailable",),
        )
    _log("applying the permanent rustdesk password")
    ok, password_error = _set_password(password, timeout)
    if not ok:
        return TaskResult(
            success=True,
            changed=True,
            warnings=(f"cannot set rustdesk password: {password_error}",),
        )
    if password_regenerated:
        changed = True

    machine_id = _machine_id(timeout)
    if machine_id is None:
        return TaskResult(
            success=True,
            changed=changed,
            warnings=("cannot read the rustdesk machine ID",),
        )
    _log(f"rustdesk machine ID: {machine_id}")
    if _write_id_file(cfg, machine_id, force):
        changed = True

    message = f"rustdesk ready, ID {machine_id}" if changed else "already configured"
    return TaskResult(success=True, changed=changed, message=message, warnings=())
