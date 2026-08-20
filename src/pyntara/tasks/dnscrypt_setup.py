"""Task dnscrypt_setup: run dnscrypt-proxy as the system-wide DNS resolver.

The task installs dnscrypt-proxy from the Ubuntu archive and runs it as
a system service. The package uses systemd socket activation: the socket
unit owns the listening socket and passes it to the service, which runs
as the unprivileged _dnscrypt-proxy user. The task changes the socket
listen address to listen_address (all interfaces, so machines on the
network can use the proxy too) through a systemd drop-in that resets the
package ListenStream and ListenDatagram and sets them to the configured
address; the non-standard port needs no privileged binding. The proxy
resolves through the encrypted servers of its sources and falls back to
fallback_resolvers, the configured plain DNS servers, whenever the
encrypted servers are unreachable, so the machine never loses
resolution. The task edits the package configuration file in place
through the shared TOML root-directive helper: it only guarantees the
fallback_resolvers line in the root table and leaves every other line
and section of the file untouched.

The task then points systemd-resolved at the local address of the proxy
through a drop-in in the resolved.conf.d directory: the DNS directive
names the proxy and the Domains directive routes every query through the
global resolver. When manage_networkmanager is set, NetworkManager is
told to ignore the DNS servers it receives from DHCP, so the global
proxy is actually used instead of being shadowed by per-link DNS. The
task verifies that the proxy service is active and that a real DNS query
through the local resolver succeeds; on a failed verification the task
reports the error and leaves the system as is, because reverting to the
previous broken resolver would not restore working DNS. The task is
idempotent: it skips when the package is installed, the socket drop-in
matches, the configuration carries the fallback resolvers, the service
is active, the resolved drop-in matches and the verification passes;
force mode rewrites the drop-ins and restarts the service but never
reinstalls a matching package.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from pyntara.config import DnscryptSetupConfig
from pyntara.config_edit import sync_directives_by_key, sync_toml_root_directive
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    ensure_root_owner,
    install_package_once,
    package_is_installed,
    run_command,
    service_is_active,
    service_is_enabled,
)


def _socket_dropin_path(cfg: DnscryptSetupConfig) -> Path:
    """The full socket drop-in path of the task."""

    return cfg.socket_dropin_dir / cfg.socket_dropin_file_name


def _resolved_dropin_path(cfg: DnscryptSetupConfig) -> Path:
    """The full resolved drop-in path of the task."""

    return cfg.resolved_conf_dir / cfg.dropin_file_name


def _socket_directives(cfg: DnscryptSetupConfig) -> tuple[str, ...]:
    """The socket drop-in directives that set the listen address.

    The first two lines reset the package ListenStream and ListenDatagram
    (an empty value clears the list inherited from the main unit), the
    last two set them to the configured address, so the socket listens
    only on listen_address and never on the package default.
    """

    return (
        "ListenStream=",
        "ListenDatagram=",
        f"ListenStream={cfg.listen_address}",
        f"ListenDatagram={cfg.listen_address}",
    )


def _fallback_directive(cfg: DnscryptSetupConfig) -> str:
    """The fallback_resolvers TOML line of the proxy configuration.

    The resolvers are rendered as a single-line TOML array of quoted
    strings, the form dnscrypt-proxy parses; the line lives in the root
    table of the configuration file.
    """

    quoted = ", ".join(f"'{server}'" for server in cfg.fallback_resolvers)
    return f"fallback_resolvers = [{quoted}]"


def _resolved_directives(cfg: DnscryptSetupConfig) -> tuple[str, ...]:
    """The resolved drop-in directives that point at the proxy.

    The DNS directive names the local address of the proxy and the
    Domains directive routes every query through the global resolver.
    """

    return (
        cfg.dns_directive,
        f"Domains={cfg.domains_directive}",
    )


def _socket_dropin_matches(cfg: DnscryptSetupConfig) -> bool:
    """True when the socket drop-in already carries every directive.

    A missing file is never a match, so an absent drop-in is always
    written.
    """

    dropin = _socket_dropin_path(cfg)
    if not dropin.is_file():
        return False
    content = dropin.read_text(encoding="utf-8")
    return all(line in content.splitlines() for line in _socket_directives(cfg))


def _config_has_fallback(cfg: DnscryptSetupConfig) -> bool:
    """True when the proxy configuration carries the fallback line.

    The comparison checks the exact rendered line, so a differing or
    commented line is not a match.
    """

    if not cfg.config_path.is_file():
        return False
    return _fallback_directive(cfg) in cfg.config_path.read_text(
        encoding="utf-8"
    ).splitlines()


def _resolved_dropin_matches(cfg: DnscryptSetupConfig) -> bool:
    """True when the resolved drop-in already carries every directive."""

    dropin = _resolved_dropin_path(cfg)
    if not dropin.is_file():
        return False
    content = dropin.read_text(encoding="utf-8")
    return all(line in content.splitlines() for line in _resolved_directives(cfg))


def _write_socket_dropin(cfg: DnscryptSetupConfig) -> tuple[bool, str]:
    """Merge the socket directives into the drop-in; return (changed, error).

    The shared directive merge creates the directory, ensures the [Socket]
    section and the header comment, replaces the managed directives by
    their key and preserves every foreign line. The file mode and the
    root ownership are applied afterwards.
    """

    dropin = _socket_dropin_path(cfg)
    try:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        sync_directives_by_key(
            dropin,
            _socket_directives(cfg),
            cfg.socket_dropin_header,
            cfg.socket_section,
        )
        os.chmod(dropin, cfg.socket_dropin_file_mode)
        ensure_root_owner(dropin)
        return True, ""
    except OSError as exc:
        return False, f"cannot write the socket drop-in {dropin}: {exc}"


def _write_proxy_config(cfg: DnscryptSetupConfig) -> tuple[bool, str]:
    """Guarantee the fallback_resolvers line in the proxy configuration.

    The shared TOML root-directive helper replaces an existing
    fallback_resolvers line or inserts the line after the server_names
    anchor, so it stays in the root table and never lands inside a later
    [section]; every other line and section of the package file survives.
    A missing configuration file is an error: the package ships it, and
    the task must not fabricate a proxy configuration from scratch.
    """

    if not cfg.config_path.is_file():
        return False, f"{cfg.config_path} is missing"
    try:
        sync_toml_root_directive(
            cfg.config_path,
            _fallback_directive(cfg),
            "server_names",
        )
        return True, ""
    except OSError as exc:
        return False, f"cannot update {cfg.config_path}: {exc}"


def _write_resolved_dropin(cfg: DnscryptSetupConfig) -> tuple[bool, str]:
    """Merge the resolved directives into the drop-in; return (changed, error)."""

    dropin = _resolved_dropin_path(cfg)
    try:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        sync_directives_by_key(
            dropin,
            _resolved_directives(cfg),
            cfg.dropin_header,
            cfg.resolve_section,
        )
        os.chmod(dropin, cfg.dropin_file_mode)
        ensure_root_owner(dropin)
        return True, ""
    except OSError as exc:
        return False, f"cannot write the resolved drop-in {dropin}: {exc}"


def _nmcli_present(cfg: DnscryptSetupConfig, timeout: float) -> bool:
    """True when NetworkManager is available."""

    result = run_command(
        cfg.nmcli_check_command,
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _nm_connections(cfg: DnscryptSetupConfig, timeout: float) -> list[str]:
    """Names of every NetworkManager connection profile."""

    result = run_command(
        cfg.nmcli_list_command,
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return []
    return [line.split(":", 1)[0] for line in result.stdout.splitlines() if line]


def _nm_set_dns_flags(
    cfg: DnscryptSetupConfig, enabled: bool, timeout: float, error_priority: int
) -> bool:
    """Set the ignore-auto-dns flags on every NM connection; True on success."""

    if not _nmcli_present(cfg, timeout):
        _log("NetworkManager not present, leaving per-link DNS untouched")
        return True
    value = "true" if enabled else "false"
    ok = True
    for name in _nm_connections(cfg, timeout):
        command = [
            part.replace("{connection}", name).replace("{value}", value)
            for part in cfg.nmcli_modify_command
        ]
        result = run_command(
            command,
            check=False,
            capture=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            ok = False
            _log(
                f"NetworkManager: cannot set ignore-auto-dns on connection "
                f"{name!r}: {result.stderr.strip()}",
                priority=error_priority,
            )
    if ok:
        _log(
            f"NetworkManager: ignore-auto-dns {'enabled' if enabled else 'disabled'} "
            f"on {len(_nm_connections(cfg, timeout))} connections"
        )
    return ok


def _daemon_reload(cfg: DnscryptSetupConfig, timeout: float) -> str | None:
    """Reload systemd so the socket drop-in takes effect; error or None."""

    try:
        run_command(cfg.daemon_reload_command, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot reload systemd: {exc}"
    return None


def _restart_resolved(cfg: DnscryptSetupConfig, timeout: float) -> str | None:
    """Restart systemd-resolved; error text on failure, None on success."""

    try:
        run_command(cfg.restart_resolved_command, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot restart systemd-resolved: {exc}"
    return None


def _wait_active(
    cfg: DnscryptSetupConfig, timeout: float
) -> bool:
    """True when the proxy service reports active within the readiness loop."""

    for _ in range(cfg.start_check_attempts):
        time.sleep(cfg.start_check_retry_delay_seconds)
        if service_is_active(cfg.service_unit_name, timeout):
            return True
    return False


def _verify(cfg: DnscryptSetupConfig, timeout: float) -> tuple[bool, str]:
    """(ok, detail) of the running proxy through a real DNS query.

    The verification is the point of the task: not that the config files
    look right, but that the proxy actually resolves. The service must be
    active and a query through the local resolver must succeed, so the
    machine really resolves through the proxy. The query is retried with
    the readiness loop parameters, because right after a restart the proxy
    may report active while it is still loading its server sources and is
    not yet able to answer; a query that succeeds on a later attempt is a
    pass.
    """

    if not service_is_active(cfg.service_unit_name, timeout):
        return False, f"{cfg.service_unit_name} is not active"
    command = list(cfg.verification_command)
    for attempt in range(cfg.start_check_attempts):
        if attempt:
            time.sleep(cfg.start_check_retry_delay_seconds)
        try:
            result = run_command(
                command,
                check=False,
                capture=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_error = f"verification query failed: {exc}"
            continue
        if result.returncode != 0:
            last_error = f"verification query exited {result.returncode}"
            continue
        if not result.stdout.strip():
            last_error = "verification query returned no answer"
            continue
        return True, "proxy resolves a real DNS query"
    return False, last_error


def task(ctx: Context) -> TaskResult:
    """Install dnscrypt-proxy and point the system at it; skip when done.

    The goal is reached when the package is installed, the socket drop-in
    matches, the proxy configuration carries the fallback resolvers, the
    service is enabled and active, the resolved drop-in matches and the
    verification passes; the task then returns changed=False. Otherwise
    it installs the package, writes the socket drop-in, guarantees the
    fallback resolvers in the proxy configuration, reloads systemd,
    enables and starts the socket and the service, waits for the service
    to become active, writes the resolved drop-in, applies the
    NetworkManager flags, restarts systemd-resolved and verifies that a
    real DNS query resolves. On a failed verification the task reports
    the error and leaves the system as is. Every step is reported to
    stdout.
    """

    cfg = ctx.config.dnscrypt_setup
    timeout = ctx.config.engine.command_timeout_seconds
    error_priority = ctx.config.engine.error_priority
    force = "dnscrypt_setup" in ctx.force_tasks

    if not package_is_installed(cfg.package_name, timeout):
        ok = False
        error = ""
        for _ in range(cfg.install_retries + 1):
            ok, error = install_package_once(cfg.package_name, timeout)
            if ok:
                break
        if not ok:
            return TaskResult(
                success=False,
                changed=False,
                error=f"cannot install {cfg.package_name}: {error}",
            )
        _log(f"installed {cfg.package_name}")

    if (
        _socket_dropin_matches(cfg)
        and _config_has_fallback(cfg)
        and service_is_enabled(cfg.service_unit_name, timeout)
        and service_is_active(cfg.service_unit_name, timeout)
        and _resolved_dropin_matches(cfg)
        and not force
    ):
        ok, detail = _verify(cfg, timeout)
        if ok:
            _log("dnscrypt-proxy already configured and resolving, skipping")
            return TaskResult(success=True, changed=False, skipped=True)

    changed, error = _write_socket_dropin(cfg)
    if error:
        return TaskResult(success=False, changed=False, error=error)
    if changed:
        _log(f"socket drop-in {_socket_dropin_path(cfg)} written")

    changed, error = _write_proxy_config(cfg)
    if error:
        return TaskResult(success=False, changed=False, error=error)
    if changed:
        _log(f"fallback resolvers written into {cfg.config_path}")

    reload_error = _daemon_reload(cfg, timeout)
    if reload_error:
        return TaskResult(success=False, changed=False, error=reload_error)

    # The socket drop-in changed the listen address, so an already-active
    # socket must be restarted for the new address to take effect: systemd
    # keeps the old socket file descriptors until the unit is restarted.
    # The service is restarted too when it is active, so it reconnects to
    # the restarted socket.
    for unit in (cfg.socket_unit_name, cfg.service_unit_name):
        if not service_is_enabled(unit, timeout):
            run_command(
                ["systemctl", "enable", unit],
                timeout=timeout,
            )
            _log(f"enabled {unit}")
        if service_is_active(unit, timeout):
            run_command(
                ["systemctl", "restart", unit],
                timeout=timeout,
            )
            _log(f"restarted {unit}")
        else:
            run_command(
                ["systemctl", "start", unit],
                timeout=timeout,
            )
            _log(f"started {unit}")

    if not _wait_active(cfg, timeout):
        return TaskResult(
            success=False,
            changed=False,
            error=f"{cfg.service_unit_name} did not become active",
        )

    changed, error = _write_resolved_dropin(cfg)
    if error:
        return TaskResult(success=False, changed=False, error=error)
    if changed:
        _log(f"resolved drop-in {_resolved_dropin_path(cfg)} written")

    if cfg.manage_networkmanager and not _nm_set_dns_flags(
        cfg, True, timeout, error_priority
    ):
        return TaskResult(
            success=False,
            changed=False,
            error="cannot apply the NetworkManager DNS flags",
        )

    restart_error = _restart_resolved(cfg, timeout)
    if restart_error:
        return TaskResult(success=False, changed=False, error=restart_error)

    ok, detail = _verify(cfg, timeout)
    if not ok:
        _log(
            f"verification failed: {detail}",
            priority=error_priority,
        )
        return TaskResult(
            success=False,
            changed=False,
            error=f"dnscrypt-proxy verification failed: {detail}",
        )

    _log(f"dnscrypt-proxy active: {detail}")
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"System DNS resolves through dnscrypt-proxy on "
            f"{cfg.listen_address}; {len(cfg.fallback_resolvers)} fallback "
            "servers keep the machine online when encrypted servers are "
            "unreachable"
        ),
    )
