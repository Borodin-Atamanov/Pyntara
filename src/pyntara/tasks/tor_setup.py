"""Task tor_setup: install Tor and publish an SSH onion service.

The task installs the Tor package from the Ubuntu archive and runs it
as a system service. The distribution package is used on purpose:
Tor publishes no GitHub release assets to chase, the Ubuntu archive
carries the current stable series and receives upstream security
updates through the regular apt upgrade, so the always-newest mechanic
of the i2pd and yggdrasil tasks would add complexity without a benefit.

The task never rewrites the main configuration file at the configured
torrc_path: it only guarantees the configured %include line through
the shared add_line_to_file helper, which appends the line when it is
absent and leaves every other line untouched, so unrelated content and
comments of the file survive. The owned settings are rendered into the
drop-in at torrc_dropin_path and rewritten whenever the rendered
content differs, so manual edits of the drop-in are reverted on the
next run. The rendered options: the SOCKS proxy bound to the loopback
interface, the log level and the SSH onion service. The service
forwards to the local SSH daemon on the port read from the
ssh_daemon_setup Port directive, never duplicated into the tor
configuration, so the two can never diverge; the virtual port clients
connect to is configured. After a change of the drop-in or the include
line the task verifies the whole configuration with tor --verify-config,
so a directive the running Tor does not know is reported as an error
instead of being silently accepted.

The onion service identity lives in the hidden service directory. The
task creates the directory inside the Tor data directory, where the
AppArmor profile of the package grants Tor write access, sets its mode
and ownership to the Tor system user. Tor generates the identity (the
keys and the hostname file) on the first start; the task never removes
or overwrites the contents, so the onion address survives restarts and
reconfigurations. On the first run the hostname file does not exist
yet, so the message says the address appears after the first start, and
the next run reports it. Once the address is known, the task saves it
into the configured address_file_path with the configured mode, so the
deployed address command can fall back to the saved value when the
hostname file cannot be read.

The service unit comes from the package; the task never renders or
writes it. The task enables the unit when it is not enabled, then
starts it when it is inactive or restarts it when it is active and the
configuration or the package changed, and waits with the configured
readiness loop for the unit to report active, because the forking
service may take a moment to fork. The task is idempotent: it skips
when the package is installed, the %include line is present in the
main configuration, the drop-in matches the render, the hidden service
directory exists, the saved address file matches the current address
and the service is enabled and active; force mode rewrites the drop-in
and restarts the service but never reinstalls the package.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import time
from pathlib import Path

from pyntara.augeas import apply_owner
from pyntara.config import TorSetupConfig
from pyntara.config_edit import add_line_to_file
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.ssh import ssh_port_from_directives
from pyntara.tor import onion_address_from_hostname_file
from pyntara.utils import (
    ensure_root_owner,
    install_package_once,
    package_is_installed,
    run_command,
    service_is_active,
    service_is_enabled,
)


def _render_config(cfg: TorSetupConfig, ssh_port: int) -> str:
    """Render the drop-in file with the configured values.

    The render starts with the ownership comment, so a manual edit is
    easy to spot. The forward target of the onion service is the sshd
    listen port read from the ssh_daemon_setup directives by the
    caller, so the service always forwards to the daemon that actually
    runs. HiddenServiceDir precedes the per-service options, because
    they apply to the service using the most recent HiddenServiceDir.
    The order of the lines is fixed, because the idempotency
    comparison is textual.
    """

    return "\n".join(
        [
            "# Managed by the Pyntara tor_setup task.",
            f"SocksPort 127.0.0.1:{cfg.socks_port}",
            f"Log {cfg.log_level} syslog",
            f"HiddenServiceDir {cfg.hidden_service_dir}",
            "HiddenServiceVersion 3",
            f"HiddenServiceNumIntroductionPoints {cfg.num_introduction_points}",
            f"HiddenServicePort {cfg.onion_ssh_port} 127.0.0.1:{ssh_port}",
            "",
        ]
    )


def _ensure_torrc_include(cfg: TorSetupConfig) -> tuple[bool, str | None]:
    """Guarantee the %include line in the main configuration.

    The shared add_line_to_file helper appends the line when it is
    absent and leaves every other line untouched, so the main file is
    never rewritten as a whole. A missing main file is an error: the
    drop-in would then be silently ignored, and the helper would not
    create the file. Returns (changed, error).
    """

    if not cfg.torrc_path.is_file():
        return False, f"{cfg.torrc_path} is missing"
    include_line = f"%include {cfg.torrc_include_glob}"
    try:
        changed = add_line_to_file(cfg.torrc_path, include_line)
    except OSError as exc:
        return False, f"cannot update {cfg.torrc_path}: {exc}"
    return changed, None


def _verify_config(timeout: float) -> str | None:
    """Error text when the tor configuration is invalid; None when OK.

    tor --verify-config parses the whole configuration, the main file
    and every included file, and exits nonzero on an invalid option or
    a conflicting value, so the check is independent of the Tor version
    and of the files the task does not own.
    """

    try:
        result = run_command(
            ["tor", "--verify-config"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot run tor --verify-config: {exc}"
    if result.returncode != 0:
        return (
            f"tor --verify-config exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return None


def _read_dropin(dropin_path: Path) -> str | None:
    """Current content of the drop-in file, or None when absent."""

    try:
        return dropin_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_dropin(cfg: TorSetupConfig, ssh_port: int) -> None:
    """Write the rendered drop-in into the configured path."""

    cfg.torrc_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.torrc_dropin_path.write_text(
        _render_config(cfg, ssh_port), encoding="utf-8"
    )
    os.chmod(cfg.torrc_dropin_path, cfg.dropin_file_mode)
    ensure_root_owner(cfg.torrc_dropin_path)


def _ensure_hidden_service_dir(cfg: TorSetupConfig) -> None:
    """Create the hidden service directory with mode and owner.

    The directory must live inside the Tor data directory, the only
    place the AppArmor profile of the package grants Tor write access,
    and must be owned by the Tor system user, so Tor can write the keys
    and the hostname file. The identity inside is never touched: the
    onion address must survive restarts. The ownership is applied
    through the shared helper, which skips the chown outside root.
    """

    cfg.hidden_service_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.hidden_service_dir, cfg.hidden_service_dir_mode)
    try:
        record = pwd.getpwnam(cfg.tor_user)
    except KeyError:
        raise RuntimeError(f"tor user {cfg.tor_user} does not exist")
    apply_owner(cfg.hidden_service_dir, record.pw_uid, record.pw_gid)


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


def _saved_address_matches(address_file_path: Path, address: str | None) -> bool:
    """True when the saved address file carries exactly the address.

    A missing address never matches, so the task stays active until the
    hostname file exists and the address is written; a missing or
    unreadable file is treated as not matching, so the task writes it.
    """

    if address is None:
        return False
    try:
        saved = address_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return saved == address


def task(ctx: Context) -> TaskResult:
    """Install Tor and publish the SSH onion service; skip when done.

    The goal is reached when the package is installed, the %include line
    is present in the main configuration, the drop-in matches the
    rendered content, the hidden service directory exists, the saved
    address file matches the current address and the service is enabled
    and active; the task then returns changed=False. Otherwise it
    installs the package, guarantees the %include line, writes the
    drop-in, verifies the configuration with tor --verify-config,
    prepares the hidden service directory, enables the service, starts
    or restarts it and waits for it to become active. The onion address
    is read from the hostname file and reported; before the first start
    created the file the message says the address appears after the
    first start. Every step is reported to stdout: measurements and
    decisions as single lines that include their result, long-running
    commands as a line before and a line after. Any failure is returned
    as an error TaskResult: the runner continues with the remaining
    tasks and never stops here.
    """

    cfg = ctx.config.tor_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "tor_setup" in ctx.force_tasks

    installed = package_is_installed(cfg.package_name, timeout)
    _log(
        f"checking package {cfg.package_name}: "
        f"{'installed' if installed else 'missing'}"
    )

    try:
        ssh_port = ssh_port_from_directives(
            ctx.config.ssh_daemon_setup.directives
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    _log(
        f"reading SSH listen port from ssh_daemon_setup directives: {ssh_port}"
    )

    changed = False
    if not installed:
        _log(f"installing package {cfg.package_name}")
        ok = False
        error = ""
        for _ in range(cfg.install_retries + 1):
            ok, error = install_package_once(cfg.package_name, timeout)
            if ok:
                break
        if not ok:
            return TaskResult(
                success=False,
                error=f"cannot install {cfg.package_name}: {error}",
            )
        _log("package installed")
        changed = True

    # The package postinst creates /etc/tor/torrc, so the %include line
    # is guaranteed only after the install; a still missing main file is
    # an error, because the drop-in would be silently ignored.
    include_changed, include_error = _ensure_torrc_include(cfg)
    if include_error is not None:
        return TaskResult(success=False, changed=changed, error=include_error)
    _log(
        f"checking %include line in {cfg.torrc_path}: "
        f"{'present' if not include_changed else 'added'}"
    )

    target_config = _render_config(cfg, ssh_port)
    current_config = _read_dropin(cfg.torrc_dropin_path)
    config_changed = force or current_config != target_config

    dir_exists = cfg.hidden_service_dir.is_dir()
    address = onion_address_from_hostname_file(cfg.hidden_service_dir / "hostname")
    _log(
        f"checking hidden service directory {cfg.hidden_service_dir}: "
        f"{'present' if dir_exists else 'missing'}"
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

    if (
        not force
        and installed
        and not include_changed
        and not config_changed
        and dir_exists
        and enabled
        and active
        and _saved_address_matches(cfg.address_file_path, address)
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    if include_changed:
        _log(f"adding %include line to {cfg.torrc_path}")
        changed = True

    if config_changed:
        _log(f"writing drop-in {cfg.torrc_dropin_path}")
        try:
            _write_dropin(cfg, ssh_port)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write drop-in: {exc}",
            )
        _log("drop-in written")
        changed = True

    if config_changed or include_changed or force:
        verify = _verify_config(timeout)
        if verify is not None:
            return TaskResult(success=False, changed=changed, error=verify)
        _log("configuration verified through tor --verify-config")

    _log(f"preparing hidden service directory {cfg.hidden_service_dir}")
    try:
        _ensure_hidden_service_dir(cfg)
    except (OSError, RuntimeError) as exc:
        return TaskResult(
            success=False,
            changed=changed,
            error=f"cannot prepare hidden service directory: {exc}",
        )
    _log("hidden service directory ready")

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

    if (
        not active
        or config_changed
        or include_changed
        or address is None
        or force
    ):
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

    address = onion_address_from_hostname_file(cfg.hidden_service_dir / "hostname")
    if address and not _saved_address_matches(cfg.address_file_path, address):
        try:
            cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.address_file_path.write_text(f"{address}\n", encoding="utf-8")
            cfg.address_file_path.chmod(cfg.address_file_mode)
            ensure_root_owner(cfg.address_file_path)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write address file: {exc}",
            )
        _log(f"writing address file {cfg.address_file_path}: {address}")
        changed = True

    if address:
        _log(f"SSH onion address: {address}")
        message = (
            f"tor {cfg.package_name} installed, service {cfg.service_unit_name} "
            f"active, SSH onion address {address}"
        )
    else:
        message = (
            f"tor {cfg.package_name} installed, service {cfg.service_unit_name} "
            "active, SSH onion address appears after the first start"
        )

    return TaskResult(success=True, changed=changed, message=message)
