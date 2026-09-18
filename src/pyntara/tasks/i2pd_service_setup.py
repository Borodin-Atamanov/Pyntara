"""Task i2pd_service_setup: install the newest i2pd release as a system service.

The task installs i2pd from the GitHub releases of the declared
repository, so the running version is always the newest release instead
of the distribution package. The latest release tag comes from the GitHub
releases API (https://api.github.com/repos/{repo}/releases/latest); the
package asset is chosen by the dpkg architecture and the distribution
codename from /etc/os-release, with the generic asset of the release as
the fallback, so a release without a build for this distribution still
installs. The package is downloaded from the official GitHub release
assets without a checksum verification: the source is trusted, and the
extra check would add a failure point without protecting the install.
The task owns the main configuration file at CONFIG_PATH:
it renders the template at task_data/i2pd_service_setup/i2pd.conf and
rewrites the file whenever the content differs, so manual edits are
reverted on the next run. CONFIG_PATH must match the --conf path of
the package unit, otherwise the rendered values are ignored. The task
also owns the tunnels file at TUNNELS_CONFIG_PATH with the SSH server
tunnel: the main configuration names that file through tunconf, so i2pd
reads exactly it wherever it is placed. The tunnel forwards to the local
SSH daemon on the port read from the ssh_daemon_setup Port directive,
never duplicated into the i2pd configuration.

The tunnel identity lives in the keys file. i2pd resolves the keys path
against its data directory (datadir), never as an absolute path, so the
tunnels file carries only the file name and the task reads the full path
in the declared data directory. The keys file is the binary PrivateKeys
record: the first 387 bytes are the IdentityEx (encryption key, signing
key and certificate), and the I2P address is the lowercase unpadded
base32 of the SHA-256 hash of that IdentityEx. The task parses the
certificate to learn the identity length, computes the address and
reports it. i2pd writes the identity only after the router is up, so
after a start the task waits for the file with the declared address
loop, saves the address into ADDRESS_FILE_PATH with the
declared mode and reports it in the same run, so the deployed address
command finds the saved value; a machine where the identity never
appears ends the wait and says the address is not available yet.

The service
is enabled and started or restarted immediately, and the task waits with
the declared readiness loop for it to become active, because the
forking service may take a moment to fork. The task is idempotent: it
skips when the installed version equals the newest release tag, the
configuration matches the rendered template, the tunnels file matches
its render, the tunnel keys file exists and the service is enabled and
active; force mode rewrites the configurations and restarts the
service but never reinstalls a matching version.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.github_release import asset_name_urls, fetch_latest_release, release_tag
from pyntara.i2pd import b32_address
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.ssh import ssh_port_from_directives as _ssh_port_from_ssh_config
from pyntara.utils import (
    apply_owner,
    download_command,
    dpkg_architecture,
    install_package_once,
    os_family_is_debian,
    read_os_release,
    refresh_apt_index,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
    version_from_output,
)
from pyntara.values import engine as engine_values
from pyntara.values import i2pd_service_setup as values

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide);
# the repository root comes from the context.


def _render_config(template_path: Path) -> str:
    """Render the configuration template with the declared values.

    Boolean options are rendered as the true/false spelling i2pd accepts,
    so the rendered file, the idempotency comparison and the written
    configuration share one representation. The template carries no shell
    variables of its own, so substitute cannot trip on stray dollar
    signs.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        log_level=values.LOG_LEVEL,
        bandwidth=str(values.BANDWIDTH),
        share=str(values.SHARE),
        tunnels_config_path=str(values.TUNNELS_CONFIG_PATH),
        http_enabled=(
            values.CONFIG_TRUE_VALUE
            if values.HTTP_ENABLED
            else values.CONFIG_FALSE_VALUE
        ),
        socks_proxy_enabled=(
            values.CONFIG_TRUE_VALUE
            if values.SOCKS_PROXY_ENABLED
            else values.CONFIG_FALSE_VALUE
        ),
        socks_proxy_port=str(values.SOCKS_PROXY_PORT),
    )


def _render_tunnels_config(ssh_port: int, template_path: Path) -> str:
    """Render the tunnels template with the SSH server tunnel.

    The tunnel port is the sshd listen port read from the ssh_daemon_setup
    directives by the caller, so the tunnel always forwards to the daemon
    that actually runs and the two can never diverge. The keys value is
    the file name only: i2pd resolves every keys path against its data
    directory, never as an absolute path, so the full declared path
    would point into a directory that does not exist.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        tunnel_name=values.TUNNEL_NAME,
        tunnel_host=values.TUNNEL_HOST,
        tunnel_port=ssh_port,
        tunnel_keys_path=Path(values.TUNNEL_KEYS_PATH).name,
    )


# i2pd prints its version as a dotted triple in the --version output.
def _select_asset(
    release: dict[str, object],
    tag: str,
    codename: str | None,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The candidate names come from the declared templates, formatted
    with the release tag, the codename and the architecture: the
    codename-specific asset wins, because it is built against this
    distribution, and the generic asset is the fallback. A distribution
    without a codename leaves the generic candidate alone.
    """

    assets = dict(asset_name_urls(release))
    candidates: list[str] = []
    if codename:
        candidates.append(
            values.CODENAME_ASSET_NAME_TEMPLATE.format(
                release_tag=tag, codename=codename, arch=arch
            )
        )
    candidates.append(
        values.GENERIC_ASSET_NAME_TEMPLATE.format(release_tag=tag, arch=arch)
    )
    for candidate in candidates:
        if candidate in assets:
            return candidate, assets[candidate]
    return None


def _installed_version(timeout: float) -> str | None:
    """The installed i2pd version from the declared version command.

    A missing binary, a nonzero exit or a hang means i2pd is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            list(values.VERSION_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    return version_from_output(result.stdout + "\n" + result.stderr)


def _download_asset(
    download_dir: Path,
    name: str,
    url: str,
    timeout: float,
) -> None:
    """Download the package into the download directory.

    The command is the declared download call, so the flags and the
    progress text are the same as in every other download of the run.
    Raises RuntimeError when curl fails, so the caller reports the
    reason.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_command(
            download_command(download_dir / name, url),
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
            refresh_apt_index(update_timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(retries + 1):
        ok, error = install_package_once(str(download_dir / name), install_timeout)
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


def _write_config(
    template_path: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write the rendered configuration into the declared path."""

    values.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.CONFIG_PATH.write_text(_render_config(template_path), encoding="utf-8")
    apply_owner(values.CONFIG_PATH, owner_uid, owner_gid)


def _read_tunnels_config(tunnels_config_path: Path) -> str | None:
    """Current content of the tunnels configuration, or None when absent."""

    try:
        return tunnels_config_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_tunnels_config(
    ssh_port: int,
    template_path: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write the rendered tunnels configuration into the declared path."""

    values.TUNNELS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.TUNNELS_CONFIG_PATH.write_text(
        _render_tunnels_config(ssh_port, template_path), encoding="utf-8"
    )
    apply_owner(values.TUNNELS_CONFIG_PATH, owner_uid, owner_gid)


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


def _wait_tunnel_address() -> str | None:
    """The .b32.i2p address once i2pd wrote the tunnel identity file.

    The keys file appears only after the first start of the router, so
    the decode is repeated with a pause of
    ADDRESS_CHECK_RETRY_DELAY_SECONDS between two attempts until
    ADDRESS_CHECK_ATTEMPTS run out; the last decode is the result either
    way, and None means the identity is still not there.
    """

    address = b32_address(values.TUNNEL_KEYS_PATH, values.ADDRESS_SUFFIX)
    for _ in range(values.ADDRESS_CHECK_ATTEMPTS):
        if address:
            return address
        time.sleep(values.ADDRESS_CHECK_RETRY_DELAY_SECONDS)
        address = b32_address(values.TUNNEL_KEYS_PATH, values.ADDRESS_SUFFIX)
    return address


def _saved_address_matches(address_file_path: Path, address: str | None) -> bool:
    """True when the saved address file carries exactly the address.

    A missing address never matches, so the task stays active until the
    identity exists and the file is written; a missing or unreadable
    file is treated as not matching, so the task writes it.
    """

    if address is None:
        return False
    try:
        saved = address_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return saved == address


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Install the newest i2pd release and run it as a service; skip when done.

    The goal is reached when the installed version equals the newest
    release tag, the configuration file matches the rendered template,
    the tunnels file matches its render, the tunnel keys file exists and
    the service is enabled and active; the task then returns
    changed=False. Otherwise it downloads the matching .deb asset from
    the release, installs it, writes the configuration and the tunnels
    file, enables the service, starts or restarts it and waits for it to
    become active. The .b32.i2p address of the tunnel is read from the
    keys file and reported; i2pd writes the identity only after the
    router is up, so a start is followed by the declared identity wait,
    and a machine where the file never appears reports that the address
    is not available yet instead of hanging.
    Every step is reported to stdout:
    measurements and decisions as single lines that include their
    result, long-running commands as a line before and a line after. A
    step that cannot run is reported as a warning of a completed task:
    the missing mechanism skips that step alone and every independent
    step still runs, so the runner continues with the remaining tasks
    and never stops here.
    """

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    warnings: list[str] = []
    missing_commands = [
        name
        for name, command in (
            ("VERSION_COMMAND", values.VERSION_COMMAND),
            ("SERVICE_ENABLE_COMMAND", values.SERVICE_ENABLE_COMMAND),
            ("SERVICE_START_COMMAND", values.SERVICE_START_COMMAND),
            ("SERVICE_RESTART_COMMAND", values.SERVICE_RESTART_COMMAND),
        )
        if not command
    ]
    if missing_commands:
        warnings.append(
            "the i2pd_service_setup commands are not configured: "
            + ", ".join(missing_commands)
        )
    task_data_path = task_data_dir(ctx.repo_root, ctx.task_name)
    config_template_path = task_data_path / values.CONFIG_TEMPLATE_FILE_NAME
    tunnels_template_path = task_data_path / values.TUNNELS_TEMPLATE_FILE_NAME
    for missing_template in (config_template_path, tunnels_template_path):
        if not missing_template.is_file():
            warnings.append(f"missing task data template: {missing_template}")
    force = ctx.task_name in ctx.force_tasks

    os_release: dict[str, str] = {}
    try:
        os_release = read_os_release(values.OS_RELEASE_FILE_PATH)
    except OSError as exc:
        warnings.append(f"cannot read {values.OS_RELEASE_FILE_PATH}: {exc}")
    debian_family = os_family_is_debian(os_release)
    if os_release and not debian_family:
        warnings.append(
            "i2pd deb packages require a Debian-based distribution; "
            + " ".join(
                f"os-release {key}={os_release.get(key, '')}"
                for key in engine_values.OS_RELEASE_FAMILY_KEYS
            )
        )
    _log(
        f"reading {values.OS_RELEASE_FILE_PATH}: ID={os_release.get('ID', '')}, "
        f"{values.OS_RELEASE_CODENAME_KEY}="
        f"{os_release.get(values.OS_RELEASE_CODENAME_KEY, '')}"
    )
    arch = ""
    try:
        arch = dpkg_architecture(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"cannot determine dpkg architecture: {exc}")
    _log(f"reading dpkg architecture: {arch or 'unknown'}")

    tag = ""
    release: dict[str, object] = {}
    if debian_family and arch:
        try:
            release = fetch_latest_release(values.GITHUB_REPO)
            tag = release_tag(release)
        except RuntimeError as exc:
            warnings.append(str(exc))
    _log(f"checking latest release: {tag or 'unknown'}")

    codename = os_release.get(values.OS_RELEASE_CODENAME_KEY)
    selected = None
    if debian_family and arch and tag:
        selected = _select_asset(release, tag, codename, arch)
        if selected is None:
            warnings.append(
                f"release {tag} has no .deb asset for arch {arch}, "
                f"codename {codename or 'generic'}"
            )
    asset_name = ""
    asset_url = ""
    if selected is not None:
        asset_name, asset_url = selected
        _log(f"selected asset: {asset_name}")

    installed_version = (
        _installed_version(timeout) if values.VERSION_COMMAND else None
    )
    _log(f"checking installed version: {installed_version or 'not installed'}")

    target_config = (
        _render_config(config_template_path)
        if config_template_path.is_file()
        else None
    )
    current_config = _read_config(values.CONFIG_PATH)
    # An install rewrites the package conffile, so the configuration is
    # rewritten after an install even when it matched before.
    config_changed = target_config is not None and (
        force or installed_version != tag or current_config != target_config
    )
    ssh_port: int | None = None
    try:
        ssh_port = _ssh_port_from_ssh_config()
    except RuntimeError as exc:
        warnings.append(str(exc))
    if ssh_port is not None:
        _log(f"reading SSH listen port from ssh_daemon_setup directives: {ssh_port}")

    target_tunnels = (
        _render_tunnels_config(ssh_port, tunnels_template_path)
        if tunnels_template_path.is_file() and ssh_port is not None
        else None
    )
    current_tunnels = _read_tunnels_config(values.TUNNELS_CONFIG_PATH)
    tunnels_changed = target_tunnels is not None and (
        force or current_tunnels != target_tunnels
    )
    keys_exist = values.TUNNEL_KEYS_PATH.is_file()
    address = b32_address(values.TUNNEL_KEYS_PATH, values.ADDRESS_SUFFIX)
    _log(
        f"checking tunnel identity file {values.TUNNEL_KEYS_PATH}: "
        f"{'present' if keys_exist else 'missing'}"
    )
    _log(
        f"checking saved address file {values.ADDRESS_FILE_PATH}: "
        f"{'matches' if _saved_address_matches(values.ADDRESS_FILE_PATH, address) else 'missing or stale'}"
    )

    enabled = service_is_enabled(values.SERVICE_UNIT_NAME, timeout)
    active = service_is_active(values.SERVICE_UNIT_NAME, timeout)
    _log(
        f"checking autorun service {values.SERVICE_UNIT_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = bool(asset_name) and installed_version != tag
    if (
        not force
        and not needs_install
        and not config_changed
        and not tunnels_changed
        and keys_exist
        and enabled
        and active
        and _saved_address_matches(values.ADDRESS_FILE_PATH, address)
    ):
        _log("target state already reached, skipping")
        return _result(changed=False, message="already configured", warnings=warnings)

    changed = False
    if needs_install:
        _log(f"downloading {asset_name} into {values.DOWNLOAD_DIR}")
        downloaded = True
        try:
            _download_asset(
                values.DOWNLOAD_DIR,
                asset_name,
                asset_url,
                timeout,
            )
        except RuntimeError as exc:
            warnings.append(str(exc))
            downloaded = False
        if downloaded:
            _log("package downloaded")
            _log(f"installing package: apt-get install -y {asset_name}")
            ok, error = _install_deb(
                values.DOWNLOAD_DIR,
                asset_name,
                install_timeout=timeout,
                update_timeout=timeout,
                retries=values.INSTALL_RETRIES,
                skip_update=ctx.skip_apt_update,
            )
            if ok:
                _log("package installed")
                changed = True
                try:
                    _cleanup_downloads(values.DOWNLOAD_DIR, asset_name)
                except OSError as exc:
                    warnings.append(f"cannot remove downloaded files: {exc}")
            else:
                warnings.append(f"cannot install i2pd: {error}")

    if config_changed:
        _log(f"writing configuration {values.CONFIG_PATH}")
        try:
            _write_config(config_template_path, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot write configuration: {exc}")
        else:
            _log("configuration written")
            changed = True

    if tunnels_changed and ssh_port is not None:
        _log(f"writing tunnels configuration {values.TUNNELS_CONFIG_PATH}")
        try:
            _write_tunnels_config(
                ssh_port, tunnels_template_path, owner_uid, owner_gid
            )
        except OSError as exc:
            warnings.append(f"cannot write tunnels configuration: {exc}")
        else:
            _log("tunnels configuration written")
            changed = True

    if not enabled:
        if values.SERVICE_ENABLE_COMMAND:
            enable_argv = substituted_command(
                values.SERVICE_ENABLE_COMMAND,
                {"service_unit_name": values.SERVICE_UNIT_NAME},
            )
            _log(f"enabling service: {' '.join(enable_argv)}")
            try:
                run_command(enable_argv, timeout=timeout)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as exc:
                warnings.append(f"{enable_argv[0]} enable failed: {exc}")
            else:
                _log("service enabled")
                changed = True
        else:
            warnings.append(
                f"cannot enable {values.SERVICE_UNIT_NAME}: "
                "SERVICE_ENABLE_COMMAND is empty"
            )

    if (
        not active
        or needs_install
        or config_changed
        or tunnels_changed
        or not keys_exist
        or force
    ):
        action = "restart" if active else "start"
        service_command = (
            values.SERVICE_RESTART_COMMAND
            if active
            else values.SERVICE_START_COMMAND
        )
        if service_command:
            service_argv = substituted_command(
                service_command, {"service_unit_name": values.SERVICE_UNIT_NAME}
            )
            _log(f"{action}ing service: {' '.join(service_argv)}")
            started = True
            try:
                run_command(service_argv, timeout=timeout)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as exc:
                warnings.append(f"{service_argv[0]} {action} failed: {exc}")
                started = False
            if started:
                _log(f"service {action}ed")
                _log(
                    f"waiting for service to become active (up to "
                    f"{values.START_CHECK_ATTEMPTS} checks)"
                )
                if _wait_active(
                    values.SERVICE_UNIT_NAME,
                    values.START_CHECK_ATTEMPTS,
                    values.START_CHECK_RETRY_DELAY_SECONDS,
                    timeout,
                ):
                    _log("service active")
                    changed = True
                else:
                    warnings.append(
                        f"{values.SERVICE_UNIT_NAME} did not become active after "
                        f"{values.START_CHECK_ATTEMPTS} checks"
                    )
        else:
            warnings.append(
                f"cannot {action} {values.SERVICE_UNIT_NAME}: "
                f"SERVICE_{action.upper()}_COMMAND is empty"
            )

    address = b32_address(values.TUNNEL_KEYS_PATH, values.ADDRESS_SUFFIX)
    if address is None:
        _log(
            f"waiting for the tunnel identity file {values.TUNNEL_KEYS_PATH} "
            f"(up to {values.ADDRESS_CHECK_ATTEMPTS} checks)"
        )
        address = _wait_tunnel_address()
        _log(f"tunnel address: {address or 'not available yet'}")
    if address and not _saved_address_matches(values.ADDRESS_FILE_PATH, address):
        try:
            values.ADDRESS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            values.ADDRESS_FILE_PATH.write_text(f"{address}\n", encoding="utf-8")
            values.ADDRESS_FILE_PATH.chmod(values.ADDRESS_FILE_MODE)
            apply_owner(values.ADDRESS_FILE_PATH, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot write tunnel address file: {exc}")
        else:
            _log(
                f"writing tunnel address file {values.ADDRESS_FILE_PATH}: {address}"
            )
            changed = True

    if address:
        _log(f"SSH tunnel address: {address}")
        message = (
            f"i2pd {tag or 'unknown version'} installed, "
            f"service {values.SERVICE_UNIT_NAME} active, "
            f"SSH tunnel address {address}"
        )
    else:
        message = (
            f"i2pd {tag or 'unknown version'} installed, "
            f"service {values.SERVICE_UNIT_NAME} active, "
            "SSH tunnel address appears after the first start"
        )

    return _result(changed=changed, message=message, warnings=warnings)
