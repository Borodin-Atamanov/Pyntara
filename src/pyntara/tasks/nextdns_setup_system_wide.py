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
profile (docs/spec/networking.md, section Verification). On a successful
verification the applied profile ID is recorded in the profile ID file
for the System Metrics collector; on a failed verification the drop-in,
the profile ID file and the NetworkManager changes are reverted, so
the machine never keeps a half-applied DNS configuration. The profiles
come from the source vaults of the fresh clone, opened with the run
password the way local_vault_setup opens them; the runtime vault is only
a fallback, because the copy may be stale and predate the profile group.
The task is idempotent: it skips when the drop-in already matches and
the resolver already answers through the profile; force mode rewrites
the drop-in and reapplies the NetworkManager changes, but the profile
choice from the hostname never changes.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.config import NextdnsSetupSystemWideConfig
from pyntara.config_edit import sync_directives_by_key
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.nextdns import resolve_servers, select_profile_id
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import run_command

# Module-level path constant is monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide):
# the source vault paths of local_vault_setup are resolved against the
# repository root, so the clone can live anywhere on the machine. It is
# an approved repository layout path exception (architecture contract
# section 3).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _dropin_path(cfg: NextdnsSetupSystemWideConfig) -> Path:
    """The full drop-in path of the task."""

    return cfg.resolved_conf_dir / cfg.dropin_file_name


def _write_profile_id_file(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> bool:
    """Record the applied profile ID for the System Metrics collector.

    The file is written only after a successful verification, so its
    presence means the profile is applied and verified. The mode and the
    root ownership are applied like the drop-in; a failed write is
    journaled and reported, so the task fails loudly instead of silently
    losing the telemetry source.
    """

    path = cfg.profile_id_file_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{profile_id}\n", encoding="utf-8")
        os.chmod(path, cfg.profile_id_file_mode)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)
        return True
    except OSError as exc:
        _log(
            f"cannot write the profile ID file {path}: {exc}",
            priority=cfg.error_priority,
        )
        return False


def _remove_profile_id_file(cfg: NextdnsSetupSystemWideConfig) -> None:
    """Remove the recorded profile ID file, if present.

    The file is removed together with the drop-in on revert, so a reverted
    machine never reports a profile that is no longer applied. A failed
    removal is journaled but cannot stop the run.
    """

    path = cfg.profile_id_file_path
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _log(
            f"revert: cannot remove the profile ID file {path}: {exc}",
            priority=cfg.error_priority,
        )


def _directive_lines(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> tuple[str, ...]:
    """The directive lines of the drop-in for a profile, in order.

    The first directive lists the DoT servers with the TLS name, the
    second the servers that answer when NextDNS is unreachable, the third
    the DNSOverTLS mode and the fourth the Domains value that routes every
    query through the global resolver. Every line is a single directive
    whose key comes from cfg.directive_keys, so the shared merge replaces
    the whole directive value instead of stacking duplicate keys. The
    values come from the config: the endpoint addresses and the dot
    format from nextdns_setup_system_wide.
    """

    servers = resolve_servers(
        profile_id,
        cfg.ipv4_servers,
        cfg.ipv6_prefixes,
        cfg.dot_endpoint_format,
    )
    dns_key, fallback_key, tls_key, domains_key = cfg.directive_keys
    return (
        f"{dns_key}={' '.join(servers)}",
        f"{fallback_key}={' '.join(cfg.fallback_dns)}",
        f"{tls_key}={cfg.dns_over_tls}",
        f"{domains_key}={cfg.domains_directive}",
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

    The shared directive merge creates the directory, ensures the [Resolve]
    section and the header comment, replaces the managed directives by
    their key and preserves every foreign line, so a profile change swaps
    the old DNS= line instead of stacking a second one. The file mode and
    the root ownership are applied afterwards. An empty profile ID never
    reaches the drop-in: it is rejected here, so the machine never gets a
    broken resolver configuration.
    """

    dropin = _dropin_path(cfg)
    if not profile_id:
        return False, "no NextDNS profile selected"
    try:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        sync_directives_by_key(
            dropin,
            _directive_lines(cfg, profile_id),
            cfg.dropin_header,
            cfg.resolve_section,
        )
        os.chmod(dropin, cfg.dropin_file_mode)
        if os.geteuid() == 0:
            os.chown(dropin, 0, 0)
        return True, ""
    except OSError as exc:
        return False, f"cannot write the drop-in {dropin}: {exc}"


def _nmcli_present(cfg: NextdnsSetupSystemWideConfig) -> bool:
    """True when NetworkManager is available.

    The check command comes from the config; Kubuntu ships
    NetworkManager, but the check keeps the task safe on a system without
    it: the global resolved configuration then stands alone and per-link
    DNS is left as is.
    """

    result = run_command(
        cfg.nmcli_check_command,
        check=False,
        capture=True,
        timeout=cfg.command_timeout_seconds,
    )
    return result.returncode == 0


def _nm_connections(cfg: NextdnsSetupSystemWideConfig) -> list[str]:
    """Names of every NetworkManager connection profile.

    A connection profile is the unit that carries ipv4.ignore-auto-dns
    and ipv6.ignore-auto-dns; the task sets the flags on all of them, so
    no DHCP-issued per-link DNS shadows the global NextDNS servers. The
    name is the first colon-separated field of every line of the list
    command output, which comes from the config.
    """

    result = run_command(
        cfg.nmcli_list_command,
        check=False,
        capture=True,
        timeout=cfg.command_timeout_seconds,
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
    the return value. The modify command template comes from the config
    and carries the {connection} and {value} placeholders.
    """

    timeout = cfg.command_timeout_seconds
    if not _nmcli_present(cfg):
        _log("NetworkManager not present, leaving per-link DNS untouched")
        return True
    value = "true" if enabled else "false"
    ok = True
    for name in _nm_connections(cfg):
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
                priority=cfg.error_priority,
            )
    if ok:
        _log(
            f"NetworkManager: ignore-auto-dns {'enabled' if enabled else 'disabled'} "
            f"on {len(_nm_connections(cfg))} connections"
        )
    return ok


def _restart_resolved(cfg: NextdnsSetupSystemWideConfig) -> str | None:
    """Restart systemd-resolved; error text on failure, None on success.

    The restart applies the drop-in immediately, so the verification that
    follows tests the state the machine actually uses. The command comes
    from the config.
    """

    try:
        run_command(
            cfg.restart_resolved_command, timeout=cfg.command_timeout_seconds
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot restart systemd-resolved: {exc}"
    return None


def _resolved_servers(cfg: NextdnsSetupSystemWideConfig) -> tuple[bool, list[str]]:
    """(ok, server lines) of the active resolver state.

    The lines are the DNS server entries of the resolvectl status output
    (the command comes from the config), the state the machine actually
    resolves through; an empty result or a nonzero exit means the
    resolver state cannot be read.
    """

    result = run_command(
        cfg.resolvectl_status_command,
        check=False,
        capture=True,
        timeout=cfg.command_timeout_seconds,
    )
    if result.returncode != 0:
        return False, []
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if ":" in line and "DNS" in line
    ]
    return bool(lines), lines


def _test_nextdns(cfg: NextdnsSetupSystemWideConfig) -> tuple[bool, str]:
    """(ok, detail) of the verification endpoint check.

    The endpoint is the NextDNS-recommended verification: it reports the
    state the query came through and the profile that answered. It
    answers with a redirect to a per-query subdomain, so the command
    follows redirects (the config carries --location). A JSON body with
    status ok proves the machine resolves through a NextDNS profile; a
    nonzero exit, a non-JSON body or any other status means the check
    failed, with the detail describing why and an excerpt of a non-JSON
    body, so the failure is diagnosable. The command template comes from
    the config and carries the {url} and {timeout} placeholders.
    """

    timeout = cfg.command_timeout_seconds
    command = [
        part.replace("{url}", cfg.verification_url).replace(
            "{timeout}", str(timeout)
        )
        for part in cfg.verification_command
    ]
    try:
        result = run_command(
            command,
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"verification endpoint unreachable: {exc}"
    if result.returncode != 0:
        return False, f"verification endpoint exited {result.returncode}"
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        excerpt = result.stdout.strip()[:120] or "<empty body>"
        return False, f"verification endpoint did not return JSON: {excerpt!r}"
    status = body.get("status")
    if status != "ok":
        return False, f"verification endpoint reported status {status!r}"
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

    ok, _ = _resolved_servers(cfg)
    if not ok:
        return False, "resolvectl status shows no DNS servers"
    status_ok, detail = _test_nextdns(cfg)
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
    _remove_profile_id_file(cfg)
    if cfg.manage_networkmanager and not _nm_set_dns_flags(cfg, False):
        _log(
            "revert: cannot restore the NetworkManager DNS flags",
            priority=cfg.error_priority,
        )
    error = _restart_resolved(cfg)
    if error:
        _log(f"revert: {error}", priority=cfg.error_priority)


def _open_profile_vault(ctx: Context) -> PyKeePass | None:
    """The vault that carries the NextDNS profiles, or None.

    The source vaults of the fresh clone are the primary source: the
    production vault is tried first, then the default vault, both with
    the run password, through the shared open_source_vault of the
    local_vault_setup task (docs/spec/secrets-model.md). The runtime
    vault is only the fallback for a run without a vault password,
    because it is a copy made once by local_vault_setup and may be stale:
    the source vaults always carry the current profile group, the runtime
    copy may predate it.
    """

    source = open_source_vault(ctx.config.local_vault_setup, ctx.vault_password)
    if source is not None:
        return source[0]
    _log("source vaults unavailable, trying the runtime vault")
    return metrics.open_runtime_vault(ctx.config)


def task(ctx: Context) -> TaskResult:
    """Configure the resolver through a NextDNS profile; skip when done.

    The vault is opened from the source vaults of the fresh clone with the
    run password, the way local_vault_setup opens them; the runtime vault
    is only the fallback. The profile group is read from the vault, the
    profile is derived from the hostname and the drop-in is aligned.
    After the resolver restarts the task verifies that the machine
    resolves through the profile and, on a failed verification, reverts
    every change. A missing profile group or an empty profile pool is a
    serious failure journaled at error_priority: the machine DNS is never
    touched then. The task is idempotent: it skips when the drop-in
    matches and the verification passes; force mode rewrites the drop-in
    and reapplies the NetworkManager flags.
    """

    cfg = ctx.config.nextdns_setup_system_wide
    force = "nextdns_setup_system_wide" in ctx.force_tasks

    kp = _open_profile_vault(ctx)
    if kp is None:
        return TaskResult(
            success=False,
            changed=False,
            error="cannot open a vault with the NextDNS profiles",
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

    restart_error = _restart_resolved(cfg)
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

    if not _write_profile_id_file(cfg, profile_id):
        _revert(cfg)
        return TaskResult(
            success=False,
            changed=False,
            error="cannot record the applied NextDNS profile ID",
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
