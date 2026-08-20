"""Task nextdns_setup_system_wide: configure the machine resolver through NextDNS.

The task picks one NextDNS profile per machine, deterministically from
the hostname, and configures systemd-resolved to resolve through that
profile over DNS-over-TLS: the profile comes from the vault subgroup
named by nextdns_setup_system_wide.vault_group_title, the endpoint
formulas live in pyntara.nextdns. A drop-in in the resolved.conf.d
directory carries the DNS= entries with the TLS server name, the
FallbackDNS= servers that keep the machine online when NextDNS is
unreachable, DNSOverTLS= and Domains=~. The drop-in is merged, never
rewritten wholesale: the managed directives (DNS, FallbackDNS,
DNSOverTLS, Domains) are replaced by their key, every other line in the
file survives, so a profile change swaps the old DNS= line instead of
stacking a second one. When manage_networkmanager is set, NetworkManager
is told to ignore the DNS servers it receives from DHCP, so the global
NextDNS servers are actually used instead of being shadowed by per-link
DNS.

The task verifies that the machine really resolves through the chosen
profile the way NextDNS recommends: resolvectl status must list the
configured servers and a query to test.nextdns.io must report the
profile (docs/spec/networking.md, section Verification). On a failed
verification the drop-in and the NetworkManager changes are reverted, so
the machine never keeps a half-applied DNS configuration. The task is
idempotent: it skips when the drop-in already matches and the resolver
already answers through the profile; force mode rewrites the drop-in and
reapplies the NetworkManager changes, but the profile choice from the
hostname never changes.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from pyntara.config import NextdnsSetupSystemWideConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.nextdns import resolve_servers, select_profile_id
from pyntara.utils import run_command

# The Resolve section header of systemd-resolved drop-ins.
RESOLVE_SECTION = "[Resolve]"
# The header comment that marks the drop-in as owned by this task.
DROPIN_HEADER = "# Managed by the Pyntara nextdns_setup_system_wide task."


def _dropin_path(cfg: NextdnsSetupSystemWideConfig) -> Path:
    """The full drop-in path of the task."""

    return cfg.resolved_conf_dir / cfg.dropin_file_name


def _directive_lines(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> tuple[str, ...]:
    """The directive lines of the drop-in for a profile, in order.

    DNS= lists the DoT servers with the TLS name, FallbackDNS= the
    servers that answer when NextDNS is unreachable, DNSOverTLS= the
    configured mode and Domains=~ routes every query through the global
    resolver. Every line is a single directive with a unique key, so the
    merge in _write_dropin can replace the whole directive value instead
    of stacking duplicate keys.
    """

    return (
        f"DNS={' '.join(resolve_servers(profile_id))}",
        f"FallbackDNS={' '.join(cfg.fallback_dns)}",
        f"DNSOverTLS={cfg.dns_over_tls}",
        "Domains=~.",
    )


# Directive keys the task owns inside its drop-in: a line with one of these
# keys is replaced, every other line (comments, Cache=, foreign settings)
# is preserved by the merge.
MANAGED_DIRECTIVE_KEYS: frozenset[str] = frozenset(
    {"DNS", "FallbackDNS", "DNSOverTLS", "Domains"}
)


def _dropin_matches(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> bool:
    """True when the drop-in already carries every expected directive.

    The comparison checks the managed directive lines only: a line that
    equals the expected value is present, a differing or commented line is
    not a match. A missing file is never a match, so an absent drop-in is
    always written.
    """

    dropin = _dropin_path(cfg)
    if not dropin.is_file():
        return False
    content = dropin.read_text(encoding="utf-8")
    expected = _directive_lines(cfg, profile_id)
    return all(line in content.splitlines() for line in expected)


def _write_dropin(
    cfg: NextdnsSetupSystemWideConfig, profile_id: str
) -> tuple[bool, str]:
    """Merge the managed directives into the drop-in; return (changed, error).

    The directory is created when absent, the [Resolve] header and the
    header comment are ensured, then every managed directive is replaced
    by its key and the missing ones are appended. Foreign lines (comments
    and settings the task does not own) are preserved as they are, so the
    task never rewrites a file wholesale; a profile change replaces the
    old DNS= line instead of stacking a second one. The file mode and the
    root ownership are applied afterwards. An empty profile ID never
    reaches the drop-in: it is rejected here, so the machine never gets a
    broken resolver configuration.
    """

    dropin = _dropin_path(cfg)
    if not profile_id:
        return False, "no NextDNS profile selected"
    try:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            dropin.read_text(encoding="utf-8").splitlines()
            if dropin.exists()
            else []
        )
        kept: list[str] = []
        has_header = False
        has_section = False
        for line in existing:
            stripped = line.strip()
            if stripped == DROPIN_HEADER:
                has_header = True
                kept.append(line)
                continue
            if stripped == RESOLVE_SECTION:
                has_section = True
                kept.append(line)
                continue
            if stripped.startswith("#"):
                kept.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in MANAGED_DIRECTIVE_KEYS:
                continue
            kept.append(line)
        merged: list[str] = []
        if not has_header:
            merged.append(DROPIN_HEADER)
        if not has_section:
            merged.append(RESOLVE_SECTION)
        merged.extend(_directive_lines(cfg, profile_id))
        merged.extend(kept)
        dropin.write_text("\n".join(merged) + "\n", encoding="utf-8")
        os.chmod(dropin, cfg.dropin_file_mode)
        if os.geteuid() == 0:
            os.chown(dropin, 0, 0)
        return True, ""
    except OSError as exc:
        return False, f"cannot write the drop-in {dropin}: {exc}"


def _nmcli_present(timeout: float) -> bool:
    """True when the nmcli command is available.

    Kubuntu ships NetworkManager, but the check keeps the task safe on a
    system without it: the global resolved configuration then stands
    alone and per-link DNS is left as is.
    """

    result = run_command(
        ["nmcli", "--version"], check=False, capture=True, timeout=timeout
    )
    return result.returncode == 0


def _nm_connections(timeout: float) -> list[str]:
    """Names of every NetworkManager connection profile.

    A connection profile is the unit that carries ipv4.ignore-auto-dns
    and ipv6.ignore-auto-dns; the task sets the flags on all of them, so
    no DHCP-issued per-link DNS shadows the global NextDNS servers. The
    name is the first colon-separated field of every line.
    """

    result = run_command(
        ["nmcli", "-t", "-f", "NAME", "connection", "show"],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return []
    return [line.split(":", 1)[0] for line in result.stdout.splitlines() if line]


def _nm_set_dns_flags(cfg: NextdnsSetupSystemWideConfig, enabled: bool) -> bool:
    """Set the ignore-auto-dns flags on every NM connection; True on success.

    Both the IPv4 and the IPv6 flag are set to the same value on every
    connection profile, so per-link DNS from DHCP is either fully ignored
    or fully restored. The task never touches other NetworkManager
    settings. A single failed connection does not fail the whole task:
    the others are still configured and the failure is reported through
    the return value.
    """

    timeout = cfg.command_timeout_seconds
    if not _nmcli_present(timeout):
        _log("NetworkManager not present, leaving per-link DNS untouched")
        return True
    value = "true" if enabled else "false"
    ok = True
    for name in _nm_connections(timeout):
        result = run_command(
            [
                "nmcli",
                "connection",
                "modify",
                name,
                "ipv4.ignore-auto-dns",
                value,
                "ipv6.ignore-auto-dns",
                value,
            ],
            check=False,
            capture=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            ok = False
            _log(
                f"NetworkManager: cannot set ignore-auto-dns on connection "
                f"{name!r}: {result.stderr.strip()}",
                priority=cfg.error_priority,
            )
    if ok:
        _log(
            f"NetworkManager: ignore-auto-dns {'enabled' if enabled else 'disabled'} "
            f"on {len(_nm_connections(timeout))} connections"
        )
    return ok


def _restart_resolved(timeout: float) -> str | None:
    """Restart systemd-resolved; error text on failure, None on success.

    The restart applies the drop-in immediately, so the verification that
    follows tests the state the machine actually uses.
    """

    try:
        run_command(
            ["systemctl", "restart", "systemd-resolved"], timeout=timeout
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot restart systemd-resolved: {exc}"
    return None


def _resolved_servers(cfg: NextdnsSetupSystemWideConfig, timeout: float) -> tuple[bool, list[str]]:
    """(ok, server lines) of the active resolver state.

    The lines are the DNS server entries of resolvectl status, the state
    the machine actually resolves through; an empty result or a nonzero
    exit means the resolver state cannot be read.
    """

    result = run_command(
        ["resolvectl", "status"], check=False, capture=True, timeout=timeout
    )
    if result.returncode != 0:
        return False, []
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if ":" in line and "DNS" in line
    ]
    return bool(lines), lines


def _test_nextdns(cfg: NextdnsSetupSystemWideConfig, timeout: float) -> tuple[bool, str]:
    """(ok, detail) of the test.nextdns.io check.

    The endpoint is the NextDNS-recommended verification: it reports the
    state the query came through and the profile that answered. A JSON
    body with status ok proves the machine resolves through a NextDNS
    profile; a nonzero curl exit, a non-JSON body or any other status
    means the check failed, with the detail describing why.
    """

    try:
        result = run_command(
            ["curl", "--fail", "--silent", "--show-error", "--max-time", str(timeout), "https://test.nextdns.io/"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"test.nextdns.io unreachable: {exc}"
    if result.returncode != 0:
        return False, f"test.nextdns.io exited {result.returncode}"
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "test.nextdns.io did not return JSON"
    status = body.get("status")
    if status != "ok":
        return False, f"test.nextdns.io reported status {status!r}"
    profile = body.get("profile")
    return True, f"status ok, profile {profile!r}"


def _verify(cfg: NextdnsSetupSystemWideConfig) -> tuple[bool, str]:
    """(ok, detail) of the running resolver through the chosen profile.

    The verification is the point of the task: not that the config files
    look right, but that the machine actually resolves through NextDNS.
    resolvectl status must list the configured servers and the
    test.nextdns.io check must report status ok, the way NextDNS
    recommends. The two checks together prove both the transport and the
    profile.
    """

    timeout = cfg.command_timeout_seconds
    ok, _ = _resolved_servers(cfg, timeout)
    if not ok:
        return False, "resolvectl status shows no DNS servers"
    status_ok, detail = _test_nextdns(cfg, timeout)
    if not status_ok:
        return False, detail
    return True, "resolver answers through the NextDNS profile"


def _revert(cfg: NextdnsSetupSystemWideConfig) -> None:
    """Undo the drop-in and the NetworkManager changes.

    The drop-in is removed, the ignore-auto-dns flags that the task
    enabled are disabled again and systemd-resolved is restarted, so the
    machine returns to its previous resolver configuration. Every step is
    journaled; a failed step is reported but cannot stop the run.
    """

    dropin = _dropin_path(cfg)
    try:
        dropin.unlink(missing_ok=True)
        _log(f"reverted: removed the drop-in {dropin}")
    except OSError as exc:
        _log(f"revert: cannot remove {dropin}: {exc}", priority=cfg.error_priority)
    if cfg.manage_networkmanager and not _nm_set_dns_flags(cfg, False):
        _log(
            "revert: cannot restore the NetworkManager DNS flags",
            priority=cfg.error_priority,
        )
    error = _restart_resolved(cfg.command_timeout_seconds)
    if error:
        _log(f"revert: {error}", priority=cfg.error_priority)


def task(ctx: Context) -> TaskResult:
    """Configure the resolver through a NextDNS profile; skip when done.

    The vault is opened through the shared runtime-vault opener of the
    System Metrics service (pyntara.metrics.open_runtime_vault), the
    profile group is read from the vault, the profile is derived from the
    hostname and the drop-in is aligned. After the resolver restarts the
    task verifies that the machine resolves through the profile and, on a
    failed verification, reverts every change. A missing profile group or
    an empty profile pool is a serious failure journaled at
    error_priority: the machine DNS is never touched then. The task is
    idempotent: it skips when the drop-in matches and the verification
    passes; force mode rewrites the drop-in and reapplies the
    NetworkManager flags.
    """

    cfg = ctx.config.nextdns_setup_system_wide
    timeout = cfg.command_timeout_seconds
    force = "nextdns_setup_system_wide" in ctx.force_tasks

    import socket

    from pyntara import metrics

    kp = metrics.open_runtime_vault(ctx.config)
    if kp is None:
        return TaskResult(
            success=False,
            changed=False,
            error="cannot open the runtime vault: the NextDNS profile cannot be read",
        )
    group = kp.find_groups(name=cfg.vault_group_title, first=True)
    if group is None:
        return TaskResult(
            success=False,
            changed=False,
            error=f"vault group {cfg.vault_group_title!r} not found",
        )
    profile_ids = tuple(
        sorted(
            (
                entry.username
                for entry in group.entries
                if entry.username and entry.username.strip()
            ),
            key=str.casefold,
        )
    )
    if not profile_ids:
        return TaskResult(
            success=False,
            changed=False,
            error=f"vault group {cfg.vault_group_title!r} has no profiles",
        )
    profile_id = select_profile_id(socket.gethostname(), profile_ids)
    if profile_id is None:
        return TaskResult(
            success=False,
            changed=False,
            error="cannot derive a NextDNS profile from the hostname",
        )

    dropin = _dropin_path(cfg)
    if _dropin_matches(cfg, profile_id) and not force:
        _log("drop-in already matches, skipping")
        return TaskResult(success=True, changed=False, skipped=True)

    changed, error = _write_dropin(cfg, profile_id)
    if error:
        return TaskResult(success=False, changed=False, error=error)
    if changed:
        _log(f"drop-in {dropin} written for profile {profile_id}")

    restart_error = _restart_resolved(timeout)
    if restart_error:
        _revert(cfg)
        return TaskResult(success=False, changed=False, error=restart_error)

    if cfg.manage_networkmanager and not _nm_set_dns_flags(cfg, True):
        _revert(cfg)
        return TaskResult(
            success=False,
            changed=False,
            error="cannot apply the NetworkManager DNS flags",
        )

    ok, detail = _verify(cfg)
    if not ok:
        _log(f"verification failed: {detail}", priority=cfg.error_priority)
        _revert(cfg)
        return TaskResult(
            success=False,
            changed=False,
            error=f"NextDNS verification failed: {detail}",
        )

    _log(f"NextDNS profile {profile_id} active: {detail}")
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"System DNS resolves through the NextDNS profile {profile_id} "
            f"over DNS-over-TLS; {len(cfg.fallback_dns)} fallback servers "
            "keep the machine online when NextDNS is unreachable"
        ),
    )
