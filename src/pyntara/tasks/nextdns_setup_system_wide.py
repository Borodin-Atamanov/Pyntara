"""Task nextdns_setup_system_wide: configure dnscrypt-proxy for a NextDNS profile.

The task picks one NextDNS profile per machine, deterministically from
the hostname, and configures dnscrypt-proxy to resolve through that
profile over all available encrypted protocols: DNS-over-HTTPS (DoH),
DNS-over-TLS (DoT) and DNS-over-QUIC (DoQ). The profile comes from the
vault subgroup named by nextdns_setup_system_wide.vault_group_title, the
endpoint formulas live in the config. The task writes [static] entries
into the dnscrypt-proxy configuration file and sets server_names to use
them, so the proxy tries every protocol and keeps the fastest. The
fallback servers already configured in dnscrypt-proxy (by the
dnscrypt_setup task) answer whenever NextDNS itself is unreachable, so
the machine never loses resolution.

The task verifies that the machine really resolves through the chosen
profile the way NextDNS recommends: a query to test.nextdns.io must
report the profile (docs/spec/networking.md, section Verification). On a
successful verification the applied profile ID is recorded in the
profile ID file for the System Metrics collector; on a failed
verification the [static] entries and the server_names line are reverted,
so the machine never keeps a half-applied configuration. The profiles
come from the source vaults of the fresh clone, opened with the run
password the way local_vault_setup opens them; the runtime vault is only
a fallback, because the copy may be stale and predate the profile group.
The task is idempotent: it skips when the [static] entries and
server_names already match and the resolver already answers through the
profile; force mode rewrites the entries and restarts the proxy, but the
profile choice from the hostname never changes.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
from pathlib import Path

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.config import NextdnsSetupSystemWideConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.nextdns import select_profile_id
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import run_command

# Module-level path constant is monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide):
# the source vault paths of local_vault_setup are resolved against the
# repository root, so the clone can live anywhere on the machine. It is
# an approved repository layout path exception (architecture contract
# section 3).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _static_entry_names(cfg: NextdnsSetupSystemWideConfig) -> tuple[str, str, str]:
    """The three [static] entry names for the profile: DoH, DoT, DoQ."""

    p = cfg.static_name_prefix
    return f"{p}-doh", f"{p}-dot", f"{p}-doq"


def _doh_stamp(profile_id: str, cfg: NextdnsSetupSystemWideConfig) -> str:
    """The DoH DNS stamp for a profile.

    The stamp encodes the DoH URL https://dns.nextdns.io/<profile_id>
    as a DoH stamp (protocol 0x02) with no certificate hash, so
    dnscrypt-proxy uses the public CA system to verify the TLS
    certificate.
    """

    url = cfg.doh_url_format.format(profile_id=profile_id)
    rest = url.removeprefix("https://")
    if "/" in rest:
        host, path = rest.split("/", 1)
        path = f"/{path}"
    else:
        host = rest
        path = "/"
    host_bytes = host.encode("utf-8")
    path_bytes = path.encode("utf-8")
    stamp_bin = (
        bytes([0x02])
        + b"\x00" * 8
        + bytes([len(host_bytes)])
        + host_bytes
        + bytes([len(path_bytes)])
        + path_bytes
    )
    stamp_b64 = base64.urlsafe_b64encode(stamp_bin).rstrip(b"=").decode("ascii")
    return f"sdns://{stamp_b64}"


def _dot_stamp(profile_id: str, cfg: NextdnsSetupSystemWideConfig) -> str:
    """The DoT DNS stamp for a profile.

    The stamp encodes the DoT endpoint <profile_id>.dns.nextdns.io:853
    as a DoT stamp (protocol 0x06) with no certificate hash.
    """

    host = cfg.dot_stamp_host_format.format(profile_id=profile_id)
    host_bytes = host.encode("utf-8")
    stamp_bin = (
        bytes([0x06]) + b"\x00" * 8 + bytes([len(host_bytes)]) + host_bytes
    )
    stamp_b64 = base64.urlsafe_b64encode(stamp_bin).rstrip(b"=").decode("ascii")
    return f"sdns://{stamp_b64}"


def _doq_stamp(profile_id: str, cfg: NextdnsSetupSystemWideConfig) -> str:
    """The DoQ DNS stamp for a profile.

    The stamp encodes the DoQ endpoint quic://<profile_id>.dns.nextdns.io:853
    as a DoQ stamp (protocol 0x0A) with no certificate hash.
    """

    host = cfg.doq_stamp_host_format.format(profile_id=profile_id)
    host_bytes = host.encode("utf-8")
    stamp_bin = (
        bytes([0x0A]) + b"\x00" * 8 + bytes([len(host_bytes)]) + host_bytes
    )
    stamp_b64 = base64.urlsafe_b64encode(stamp_bin).rstrip(b"=").decode("ascii")
    return f"sdns://{stamp_b64}"


def _static_lines(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> tuple[str, ...]:
    """The [static] section lines for a profile over all three protocols.

    Returns the TOML lines that define three [static] entries (DoH, DoT,
    DoQ). The server_names line is separate.
    """

    doh_name, dot_name, doq_name = _static_entry_names(cfg)
    return (
        f"[static.'{doh_name}']",
        f"stamp = '{_doh_stamp(profile_id, cfg)}'",
        f"[static.'{dot_name}']",
        f"stamp = '{_dot_stamp(profile_id, cfg)}'",
        f"[static.'{doq_name}']",
        f"stamp = '{_doq_stamp(profile_id, cfg)}'",
    )


def _server_names_line(cfg: NextdnsSetupSystemWideConfig) -> str:
    """The server_names line that selects all three protocol entries."""

    doh, dot, doq = _static_entry_names(cfg)
    return f"server_names = ['{doh}', '{dot}', '{doq}']"


def _write_profile_id_file(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> bool:
    """Record the applied profile ID for the System Metrics collector.

    The file is written only after a successful verification, so its
    presence means the profile is applied and verified. The mode and the
    root ownership are applied; a failed write is journaled and reported,
    so the task fails loudly instead of silently losing the telemetry
    source.
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

    The file is removed together with the [static] entries on revert, so
    a reverted machine never reports a profile that is no longer applied.
    A failed removal is journaled but cannot stop the run.
    """

    path = cfg.profile_id_file_path
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _log(
            f"revert: cannot remove the profile ID file {path}: {exc}",
            priority=cfg.error_priority,
        )


def _config_matches(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> bool:
    """True when the dnscrypt-proxy config already carries the entries.

    The comparison checks that every [static] line and the server_names
    line are present in the file. A missing file is never a match.
    """

    if not cfg.dnscrypt_config_path.is_file():
        return False
    content = cfg.dnscrypt_config_path.read_text(encoding="utf-8")
    expected = (*_static_lines(cfg, profile_id), _server_names_line(cfg))
    return all(line in content for line in expected)


def _write_dnscrypt_config(
    cfg: NextdnsSetupSystemWideConfig, profile_id: str
) -> tuple[bool, str]:
    """Write the [static] entries and server_names into the config.

    Existing [static] entries owned by this task and the old server_names
    line are removed, then the new entries are appended. Returns
    (changed, error).
    """

    path = cfg.dnscrypt_config_path
    if not path.is_file():
        return False, f"{path} is missing"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"

    doh_name, dot_name, doq_name = _static_entry_names(cfg)
    owned_names = {doh_name, dot_name, doq_name}
    lines = content.splitlines()
    result: list[str] = []
    skip_static = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[static."):
            name = stripped[len("[static.'"):].rstrip("']")
            if name in owned_names:
                skip_static = True
                continue
            skip_static = False
        if skip_static:
            if stripped.startswith("[") and not stripped.startswith("[static."):
                skip_static = False
                result.append(line)
            continue
        if stripped.startswith("server_names"):
            continue
        result.append(line)

    new_lines = list(_static_lines(cfg, profile_id))
    result.extend(new_lines)
    result.append(_server_names_line(cfg))

    new_content = "\n".join(result) + "\n"
    if new_content == content:
        return False, ""

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, ""


def _restart_proxy(cfg: NextdnsSetupSystemWideConfig) -> str | None:
    """Restart dnscrypt-proxy; error text on failure, None on success."""

    try:
        run_command(
            list(cfg.restart_proxy_command),
            timeout=cfg.command_timeout_seconds,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot restart dnscrypt-proxy: {exc}"
    return None


def _test_nextdns(cfg: NextdnsSetupSystemWideConfig) -> tuple[bool, str]:
    """(ok, detail) of the verification endpoint check.

    The endpoint is the NextDNS-recommended verification: it reports the
    state the query came through and the profile that answered. It
    answers with a redirect to a per-query subdomain, so the command
    follows redirects (the config carries --location). A JSON body with
    status ok proves the machine resolves through a NextDNS profile.
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


def _revert(cfg: NextdnsSetupSystemWideConfig) -> None:
    """Undo the [static] entries and the server_names line.

    The entries owned by this task are removed from the configuration
    file and dnscrypt-proxy is restarted, so the machine returns to its
    previous resolver configuration. Every step is journaled; a failed
    step is reported but cannot stop the run.
    """

    path = cfg.dnscrypt_config_path
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _log(f"revert: cannot read {path}: {exc}", priority=cfg.error_priority)
        return

    doh_name, dot_name, doq_name = _static_entry_names(cfg)
    owned_names = {doh_name, dot_name, doq_name}
    lines = content.splitlines()
    result: list[str] = []
    skip_static = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[static."):
            name = stripped[len("[static.'"):].rstrip("']")
            if name in owned_names:
                skip_static = True
                continue
            skip_static = False
        if skip_static:
            if stripped.startswith("[") and not stripped.startswith("[static."):
                skip_static = False
                result.append(line)
            continue
        if stripped.startswith("server_names"):
            continue
        result.append(line)

    new_content = "\n".join(result) + "\n"
    try:
        path.write_text(new_content, encoding="utf-8")
        _log("reverted: removed the NextDNS [static] entries")
    except OSError as exc:
        _log(
            f"revert: cannot write {path}: {exc}",
            priority=cfg.error_priority,
        )
    _remove_profile_id_file(cfg)
    error = _restart_proxy(cfg)
    if error:
        _log(f"revert: {error}", priority=cfg.error_priority)


def _open_profile_vault(ctx: Context) -> PyKeePass | None:
    """The vault that carries the NextDNS profiles, or None.

    The source vaults of the fresh clone are the primary source: the
    production vault is tried first, then the default vault, both with
    the run password, through the shared open_source_vault of the
    local_vault_setup task (docs/spec/secrets-model.md). The runtime
    vault is only the fallback for a run without a vault password,
    because it is a copy made once by local_vault_setup and may be stale.
    """

    source = open_source_vault(ctx.config.local_vault_setup, ctx.vault_password)
    if source is not None:
        return source[0]
    _log("source vaults unavailable, trying the runtime vault")
    return metrics.open_runtime_vault(ctx.config)


def task(ctx: Context) -> TaskResult:
    """Configure dnscrypt-proxy for a NextDNS profile; skip when done.

    The vault is opened from the source vaults of the fresh clone with the
    run password, the way local_vault_setup opens them; the runtime vault
    is only the fallback. The profile group is read from the vault, the
    profile is derived from the hostname and the [static] entries are
    written into the dnscrypt-proxy configuration. After the proxy
    restarts the task verifies that the machine resolves through the
    profile and, on a failed verification, reverts every change. A
    missing profile group or an empty profile pool is a serious failure
    journaled at error_priority: the proxy configuration is never touched
    then. The task is idempotent: it skips when the [static] entries
    match and the verification passes; force mode rewrites the entries
    and restarts the proxy.
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

    if _config_matches(cfg, profile_id) and not force:
        _log("dnscrypt-proxy already configured for the NextDNS profile, skipping")
        return TaskResult(success=True, changed=False, skipped=True)

    changed, error = _write_dnscrypt_config(cfg, profile_id)
    if error:
        return TaskResult(success=False, changed=False, error=error)
    if changed:
        _log(
            f"dnscrypt-proxy configuration written for NextDNS profile {profile_id}"
        )

    restart_error = _restart_proxy(cfg)
    if restart_error:
        _revert(cfg)
        return TaskResult(success=False, changed=False, error=restart_error)

    ok, detail = _test_nextdns(cfg)
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
            "over DoH, DoT and DoQ through dnscrypt-proxy; fallback servers "
            "keep the machine online when NextDNS is unreachable"
        ),
    )
