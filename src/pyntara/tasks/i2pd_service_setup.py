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
the package unit, otherwise the rendered values are ignored. The task
also owns the tunnels file at tunnels_config_path with the SSH server
tunnel: the main configuration names that file through tunconf, so i2pd
reads exactly it wherever it is placed. The tunnel forwards to the local
SSH daemon on the port read from the ssh_daemon_setup Port directive,
never duplicated into the i2pd configuration.

The tunnel identity lives in the keys file. i2pd resolves the keys path
against its data directory (datadir), never as an absolute path, so the
tunnels file carries only the file name and the task reads the full path
in the configured data directory. The keys file is the binary PrivateKeys
record: the first 387 bytes are the IdentityEx (encryption key, signing
key and certificate), and the I2P address is the lowercase unpadded
base32 of the SHA-256 hash of that IdentityEx. The task parses the
certificate to learn the identity length, computes the address and
reports it. i2pd writes the identity only after the router is up, so
after a start the task waits for the file with the configured address
loop, saves the address into the configured address_file_path with the
configured mode and reports it in the same run, so the deployed address
command finds the saved value; a machine where the identity never
appears ends the wait and says the address is not available yet.

The service
is enabled and started or restarted immediately, and the task waits with
the configured readiness loop for it to become active, because the
forking service may take a moment to fork. The task is idempotent: it
skips when the installed version equals the newest release tag, the
configuration matches the rendered template, the tunnels file matches
its render, the tunnel keys file exists and the service is enabled and
active; force mode rewrites the configurations and restarts the
service but never reinstalls a matching version.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from string import Template

from pyntara.config import EngineConfig, I2pdServiceSetupConfig
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
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide);
# the repository root comes from the context and the os-release path from
# the config.


def _render_config(cfg: I2pdServiceSetupConfig, template_path: Path) -> str:
    """Render the configuration template with the configured values.

    Boolean options are rendered as the true/false spelling i2pd accepts,
    so the rendered file, the idempotency comparison and the written
    configuration share one representation. The template carries no shell
    variables of its own, so substitute cannot trip on stray dollar
    signs.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        log_level=cfg.log_level,
        bandwidth=str(cfg.bandwidth),
        share=str(cfg.share),
        tunnels_config_path=str(cfg.tunnels_config_path),
        http_enabled=(
            cfg.config_true_value if cfg.http_enabled else cfg.config_false_value
        ),
        socks_proxy_enabled=(
            cfg.config_true_value
            if cfg.socks_proxy_enabled
            else cfg.config_false_value
        ),
        socks_proxy_port=str(cfg.socks_proxy_port),
    )


def _render_tunnels_config(
    cfg: I2pdServiceSetupConfig, ssh_port: int, template_path: Path
) -> str:
    """Render the tunnels template with the SSH server tunnel.

    The tunnel port is the sshd listen port read from the ssh_daemon_setup
    directives by the caller, so the tunnel always forwards to the daemon
    that actually runs and the two can never diverge. The keys value is
    the file name only: i2pd resolves every keys path against its data
    directory, never as an absolute path, so the full configured path
    would point into a directory that does not exist.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        tunnel_name=cfg.tunnel_name,
        tunnel_host=cfg.tunnel_host,
        tunnel_port=ssh_port,
        tunnel_keys_path=Path(cfg.tunnel_keys_path).name,
    )


# i2pd prints its version as a dotted triple in the --version output.
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")


def _select_asset(
    cfg: I2pdServiceSetupConfig,
    release: dict[str, object],
    tag: str,
    codename: str | None,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The candidate names come from the configured templates, formatted
    with the release tag, the codename and the architecture: the
    codename-specific asset wins, because it is built against this
    distribution, and the generic asset is the fallback. A distribution
    without a codename leaves the generic candidate alone.
    """

    assets = dict(asset_name_urls(release))
    candidates: list[str] = []
    if codename:
        candidates.append(
            cfg.codename_asset_name_template.format(
                release_tag=tag, codename=codename, arch=arch
            )
        )
    candidates.append(
        cfg.generic_asset_name_template.format(release_tag=tag, arch=arch)
    )
    for candidate in candidates:
        if candidate in assets:
            return candidate, assets[candidate]
    return None


def _installed_version(
    cfg: I2pdServiceSetupConfig, timeout: float
) -> str | None:
    """The installed i2pd version from the configured version command.

    A missing binary, a nonzero exit or a hang means i2pd is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            list(cfg.version_command),
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
    engine: EngineConfig,
    download_dir: Path,
    name: str,
    url: str,
    timeout: float,
) -> None:
    """Download the package into the download directory.

    The command is the engine-wide download call, so the flags and the
    progress text are the same as in every other download of the run.
    Raises RuntimeError when curl fails, so the caller reports the
    reason.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_command(
            download_command(engine, download_dir / name, url),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot download {url}: {exc}") from None


def _install_deb(
    engine: EngineConfig,
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
            refresh_apt_index(engine, update_timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(retries + 1):
        ok, error = install_package_once(
            engine, str(download_dir / name), install_timeout
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


def _write_config(
    cfg: I2pdServiceSetupConfig,
    template_path: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write the rendered configuration into the configured path."""

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        _render_config(cfg, template_path), encoding="utf-8"
    )
    apply_owner(cfg.config_path, owner_uid, owner_gid)


def _read_tunnels_config(tunnels_config_path: Path) -> str | None:
    """Current content of the tunnels configuration, or None when absent."""

    try:
        return tunnels_config_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_tunnels_config(
    cfg: I2pdServiceSetupConfig,
    ssh_port: int,
    template_path: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write the rendered tunnels configuration into the configured path."""

    cfg.tunnels_config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnels_config_path.write_text(
        _render_tunnels_config(cfg, ssh_port, template_path), encoding="utf-8"
    )
    apply_owner(cfg.tunnels_config_path, owner_uid, owner_gid)


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


def _wait_tunnel_address(cfg: I2pdServiceSetupConfig) -> str | None:
    """The .b32.i2p address once i2pd wrote the tunnel identity file.

    The keys file appears only after the first start of the router, so
    the decode is repeated with a pause of
    address_check_retry_delay_seconds between two attempts until
    address_check_attempts run out; the last decode is the result either
    way, and None means the identity is still not there.
    """

    address = b32_address(cfg.tunnel_keys_path)
    for _ in range(cfg.address_check_attempts):
        if address:
            return address
        time.sleep(cfg.address_check_retry_delay_seconds)
        address = b32_address(cfg.tunnel_keys_path)
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
    router is up, so a start is followed by the configured identity wait,
    and a machine where the file never appears reports that the address
    is not available yet instead of hanging.
    Every step is reported to stdout:
    measurements and decisions as single lines that include their
    result, long-running commands as a line before and a line after. Any
    failure is returned as an error TaskResult: the runner continues
    with the remaining tasks and never stops here.
    """

    cfg = ctx.config.i2pd_service_setup
    timeout = ctx.config.engine.command_timeout_seconds
    owner_uid = ctx.config.engine.root_owner_uid
    owner_gid = ctx.config.engine.root_owner_gid
    missing_commands = [
        name
        for name, command in (
            ("version_command", cfg.version_command),
            ("service_enable_command", cfg.service_enable_command),
            ("service_start_command", cfg.service_start_command),
            ("service_restart_command", cfg.service_restart_command),
        )
        if not command
    ]
    if missing_commands:
        return TaskResult(
            success=False,
            error=(
                "the i2pd_service_setup commands are not configured: "
                + ", ".join(missing_commands)
            ),
        )
    task_data_path = task_data_dir(ctx.repo_root, ctx.task_name)
    template_path = task_data_path / cfg.config_template_file_name
    tunnels_template_path = task_data_path / cfg.tunnels_template_file_name
    for missing_template in (template_path, tunnels_template_path):
        if not missing_template.is_file():
            return TaskResult(
                success=False,
                error=f"missing task data template: {missing_template}",
            )
    force = ctx.task_name in ctx.force_tasks

    try:
        os_release = read_os_release(cfg.os_release_file_path)
    except OSError as exc:
        return TaskResult(
            success=False, error=f"cannot read {cfg.os_release_file_path}: {exc}"
        )
    if not os_family_is_debian(ctx.config.engine, os_release):
        return TaskResult(
            success=False,
            error=(
                "i2pd deb packages require a Debian-based distribution; "
                + " ".join(
                    f"os-release {key}={os_release.get(key, '')}"
                    for key in ctx.config.engine.os_release_family_keys
                )
            ),
        )
    _log(
        f"reading {cfg.os_release_file_path}: ID={os_release.get('ID', '')}, "
        f"{cfg.os_release_codename_key}="
        f"{os_release.get(cfg.os_release_codename_key, '')}"
    )
    try:
        arch = dpkg_architecture(ctx.config.engine, timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False, error=f"cannot determine dpkg architecture: {exc}"
        )
    _log(f"reading dpkg architecture: {arch}")

    try:
        release = fetch_latest_release(cfg.github_repo, ctx.config.engine)
        tag = release_tag(release)
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    _log(f"checking latest release: {tag}")

    codename = os_release.get(cfg.os_release_codename_key)
    selected = _select_asset(cfg, release, tag, codename, arch)
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

    installed_version = _installed_version(cfg, timeout)
    _log(f"checking installed version: {installed_version or 'not installed'}")

    target_config = _render_config(cfg, template_path)
    current_config = _read_config(cfg.config_path)
    # An install rewrites the package conffile, so the configuration is
    # rewritten after an install even when it matched before.
    config_changed = force or installed_version != tag or (
        current_config != target_config
    )
    try:
        ssh_port = _ssh_port_from_ssh_config(
            ctx.config.ssh_daemon_setup.directives
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    _log(
        f"reading SSH listen port from ssh_daemon_setup directives: {ssh_port}"
    )

    target_tunnels = _render_tunnels_config(cfg, ssh_port, tunnels_template_path)
    current_tunnels = _read_tunnels_config(cfg.tunnels_config_path)
    tunnels_changed = force or current_tunnels != target_tunnels
    keys_exist = cfg.tunnel_keys_path.is_file()
    address = b32_address(cfg.tunnel_keys_path)
    _log(
        f"checking tunnel identity file {cfg.tunnel_keys_path}: "
        f"{'present' if keys_exist else 'missing'}"
    )
    _log(
        f"checking saved address file {cfg.address_file_path}: "
        f"{'matches' if _saved_address_matches(cfg.address_file_path, address) else 'missing or stale'}"
    )

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = installed_version != tag
    if (
        not force
        and not needs_install
        and not config_changed
        and not tunnels_changed
        and keys_exist
        and enabled
        and active
        and _saved_address_matches(cfg.address_file_path, address)
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    if needs_install:
        _log(f"downloading {asset_name} into {cfg.download_dir}")
        try:
            _download_asset(
                ctx.config.engine,
                cfg.download_dir,
                asset_name,
                asset_url,
                timeout,
            )
        except RuntimeError as exc:
            return TaskResult(success=False, error=str(exc))
        _log("package downloaded")
        _log(f"installing package: apt-get install -y {asset_name}")
        ok, error = _install_deb(
            ctx.config.engine,
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
            _write_config(cfg, template_path, owner_uid, owner_gid)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write configuration: {exc}",
            )
        _log("configuration written")
        changed = True

    if tunnels_changed:
        _log(f"writing tunnels configuration {cfg.tunnels_config_path}")
        try:
            _write_tunnels_config(
                cfg, ssh_port, tunnels_template_path, owner_uid, owner_gid
            )
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write tunnels configuration: {exc}",
            )
        _log("tunnels configuration written")
        changed = True

    if not enabled:
        enable_argv = substituted_command(
            cfg.service_enable_command,
            {"service_unit_name": cfg.service_unit_name},
        )
        _log(f"enabling service: {' '.join(enable_argv)}")
        try:
            run_command(enable_argv, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"{enable_argv[0]} enable failed: {exc}",
            )
        _log("service enabled")
        changed = True

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
            cfg.service_restart_command
            if active
            else cfg.service_start_command
        )
        service_argv = substituted_command(
            service_command, {"service_unit_name": cfg.service_unit_name}
        )
        _log(f"{action}ing service: {' '.join(service_argv)}")
        try:
            run_command(service_argv, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"{service_argv[0]} {action} failed: {exc}",
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

    address = b32_address(cfg.tunnel_keys_path)
    if address is None:
        _log(
            f"waiting for the tunnel identity file {cfg.tunnel_keys_path} "
            f"(up to {cfg.address_check_attempts} checks)"
        )
        address = _wait_tunnel_address(cfg)
        _log(f"tunnel address: {address or 'not available yet'}")
    if address and not _saved_address_matches(cfg.address_file_path, address):
        try:
            cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.address_file_path.write_text(f"{address}\n", encoding="utf-8")
            cfg.address_file_path.chmod(cfg.address_file_mode)
            apply_owner(cfg.address_file_path, owner_uid, owner_gid)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write tunnel address file: {exc}",
            )
        _log(f"writing tunnel address file {cfg.address_file_path}: {address}")
        changed = True

    if address:
        _log(f"SSH tunnel address: {address}")
        message = (
            f"i2pd {tag} installed, service {cfg.service_unit_name} active, "
            f"SSH tunnel address {address}"
        )
    else:
        message = (
            f"i2pd {tag} installed, service {cfg.service_unit_name} active, "
            "SSH tunnel address appears after the first start"
        )

    return TaskResult(success=True, changed=changed, message=message)
