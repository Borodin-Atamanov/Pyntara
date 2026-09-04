# Install and configure dnsproxy as the system-wide DNS resolver.

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tarfile
import time
from pathlib import Path
from string import Template
from typing import NamedTuple

from pyntara.config import DnsproxySetupConfig
from pyntara.config_edit import sync_directives_by_key
from pyntara.context import Context
from pyntara.logger import log_progress
from pyntara.models import TaskResult
from pyntara.utils import (
    curl_flags,
    dpkg_architecture,
    ensure_root_owner,
    run_command,
    service_is_active,
    service_is_enabled,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OS_RELEASE_PATH = Path("/etc/os-release")
VERSION_PATTERN = re.compile(r"v?(\d+\.\d+\.\d+)")
PROFILE_ID_PATTERN = re.compile(r"[0-9a-f]{6}\Z")

class DiscoveredDnsServers(NamedTuple):
    '''Validated DNS addresses found from the current network state.'''

    ipv4: tuple[str, ...]
    ipv6: tuple[str, ...]
    errors: tuple[str, ...]


def _add_valid_dns_tokens(
    tokens: list[str], addresses_v4: set[str], addresses_v6: set[str]
) -> None:
    for token in tokens:
        try:
            address = ipaddress.ip_address(token.strip("[](),"))
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        if address.version == 4:
            addresses_v4.add(str(address))
        else:
            addresses_v6.add(str(address))


def discover_dns_servers(
    cfg: DnsproxySetupConfig, timeout: float
) -> DiscoveredDnsServers:
    '''Discover DNS from both configured resolvectl and nmcli commands.

    Both commands are always called and their current-state outputs are combined.
    Duplicate addresses are removed, valid IPv4 and IPv6 addresses are sorted,
    and command diagnostics are returned. No files are read, system state is not
    changed, and DNS reachability is not tested.
    '''
    addresses_v4: set[str] = set()
    addresses_v6: set[str] = set()
    errors: list[str] = []
    commands = (
        ("resolvectl", cfg.resolvectl_dns_command),
        ("nmcli", cfg.nmcli_dns_command),
    )
    for name, command in commands:
        try:
            result = run_command(command, check=False, capture=True, timeout=timeout)
            if result.returncode != 0:
                errors.append(f"{name} exited with {result.returncode}")
            if name == "resolvectl":
                for line in result.stdout.splitlines():
                    _add_valid_dns_tokens(
                        line.split(":", 1)[-1].split(), addresses_v4, addresses_v6
                    )
            else:
                for line in result.stdout.splitlines():
                    match = re.match(r"IP[46]\.DNS(?:\[\d+\])?:(.*)$", line)
                    if match:
                        _add_valid_dns_tokens(match.group(1).split(), addresses_v4, addresses_v6)

        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name} failed: {exc}")
    return DiscoveredDnsServers(
        tuple(sorted(addresses_v4)), tuple(sorted(addresses_v6)), tuple(errors)
    )


def _release_json(
    repo: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> dict[str, object]:
    result = run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            *curl_flags(curl_timeout, retries, connect_timeout, retry_max_time),
            f"https://api.github.com/repos/{repo}/releases/latest",
        ],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch dnsproxy release: exit {result.returncode}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise TypeError("dnsproxy release response is not an object")
    return value


def _asset_for_architecture(release: dict[str, object], arch: str) -> tuple[str, str]:
    tag = release.get("tag_name")
    assets = release.get("assets")
    if not isinstance(tag, str) or not tag or not isinstance(assets, list):
        raise RuntimeError("dnsproxy release has no usable tag or assets")
    suffix = {"amd64": "amd64", "arm64": "arm64", "armhf": "arm7"}.get(arch)
    if suffix is None:
        raise RuntimeError(f"unsupported dnsproxy architecture: {arch}")
    expected = f"dnsproxy-linux-{suffix}-{tag}.tar.gz"
    for asset in assets:
        if (
            isinstance(asset, dict)
            and asset.get("name") == expected
            and isinstance(asset.get("browser_download_url"), str)
        ):
            return expected, asset["browser_download_url"]
    raise RuntimeError(f"release {tag} has no asset {expected}")


def _installed_version(path: Path, timeout: float) -> str | None:
    try:
        result = run_command(
            [str(path), "--version"], check=False, capture=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = VERSION_PATTERN.search(result.stdout + result.stderr)
    return match.group(1) if match else None


def _version_from_tag(tag: str) -> str:
    match = VERSION_PATTERN.search(tag)
    if not match:
        raise RuntimeError(f"cannot parse dnsproxy release version: {tag}")
    return match.group(1)


def _download_binary(
    cfg: DnsproxySetupConfig,
    url: str,
    name: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> Path:
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    archive = cfg.download_dir / name
    run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--output",
            str(archive),
            *curl_flags(curl_timeout, retries, connect_timeout, retry_max_time),
            url,
        ],
        timeout=timeout,
    )
    extract_dir = cfg.download_dir / "extract"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir()
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(extract_dir, filter="data")
    candidates = list(extract_dir.rglob("dnsproxy"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError("dnsproxy archive does not contain exactly one binary")
    staged = cfg.download_dir / "dnsproxy.staged"
    shutil.copyfile(candidates[0], staged)
    staged.chmod(0o755)
    return staged


def _upstreams(cfg: DnsproxySetupConfig, profile_id: str) -> tuple[str, ...]:
    return tuple(
        format_string.format(profile_id=profile_id)
        for format_string in (
            cfg.doh_url_format,
            cfg.dot_host_format,
            cfg.doq_host_format,
        )
    )


def _protocol_forms(addresses: tuple[str, ...]) -> tuple[str, ...]:
    '''Protocol forms of each address, one argument per protocol.

    Every address yields four forms: plain DNS on port 53, DoT on 853, DoH
    on 443 and DoQ on 853. IPv6 hosts are enclosed in square brackets.
    DNSCrypt is not generated because a bare IP is not enough for it and
    the pool carries no stamps. The same forms feed both the bootstrap and
    the fallback resolver groups.
    '''
    forms: list[str] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RuntimeError(f"invalid bootstrap address {address!r}: {exc}") from exc
        host = f"[{ip}]" if ip.version == 6 else str(ip)
        plain = host if ip.version == 6 else address
        forms.extend(
            (
                plain,
                f"tls://{host}:853",
                f"https://{host}:443/dns-query",
                f"quic://{host}:853",
            )
        )
    return tuple(forms)


def _plain_udp_forms(addresses: tuple[str, ...]) -> tuple[str, ...]:
    '''Bare address forms for plain UDP DNS on port 53.

    Used for the provider DNS discovered from the current network, which
    is not known to answer the encrypted protocols. IPv6 hosts are
    enclosed in square brackets, matching the plain form generated by
    _protocol_forms.
    '''
    forms: list[str] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid discovered DNS address {address!r}: {exc}"
            ) from exc
        forms.append(f"[{ip}]" if ip.version == 6 else address)
    return tuple(forms)


def _command(
    cfg: DnsproxySetupConfig, profile_id: str, discovered: DiscoveredDnsServers
) -> list[str]:
    command = [str(cfg.binary_path), "--port=" + str(cfg.listen_port)]
    for address in cfg.listen_addresses:
        command.append("--listen=" + address)
    for upstream in _upstreams(cfg, profile_id):
        command.append("--upstream=" + upstream)
    pool_forms = _protocol_forms(cfg.bootstrap_resolvers)
    provider_forms = _plain_udp_forms((*discovered.ipv4, *discovered.ipv6))
    for fallback in (*pool_forms, *provider_forms):
        command.append("--fallback=" + fallback)
    command.extend(
        (
            "--upstream-mode=" + cfg.upstream_mode,
            "--timeout=" + str(cfg.timeout_seconds) + "s",
        )
    )
    if cfg.cache_enabled:
        command.append("--cache")
        command.append("--cache-size=" + str(cfg.cache_size_bytes))
    for bootstrap in (*pool_forms, *provider_forms):
        command.append("--bootstrap=" + bootstrap)
    return command


def _render_service(
    cfg: DnsproxySetupConfig, profile_id: str, discovered: DiscoveredDnsServers
) -> str:
    template_path = REPO_ROOT / cfg.service_template_path
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_start=" ".join(_command(cfg, profile_id, discovered)),
        service_restart_seconds=cfg.service_restart_seconds,
        log_rate_limit_interval_seconds=cfg.log_rate_limit_interval_seconds,
        log_rate_limit_burst=cfg.log_rate_limit_burst,
    )


def _read_profile_id(cfg: DnsproxySetupConfig) -> str | None:
    '''The NextDNS profile id recorded by nextdns_setup_system_wide.

    The profile id file is the shared single source of truth written by
    the nextdns_setup_system_wide task; dnsproxy never opens the vault
    itself, so both tasks always agree on the profile. A missing or
    malformed file returns None and the task stops before any change.
    '''

    try:
        value = cfg.profile_id_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if PROFILE_ID_PATTERN.fullmatch(value) is None:
        return None
    return value


def _listening_pids(cfg: DnsproxySetupConfig, timeout: float) -> set[int]:
    '''PIDs of processes listening on the configured resolver port.

    The TCP and the UDP listen states are scanned through ss. A line
    belongs to the port when its local address ends with the configured
    port; every pid token in that line is collected. A failing ss
    command yields an empty set, so a missing tool cannot stop the run.
    '''

    pids: set[int] = set()
    for command in (cfg.ss_tcp_listen_command, cfg.ss_udp_listen_command):
        try:
            result = run_command(
                list(command), check=False, capture=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            if fields[3].split(":")[-1] != str(cfg.listen_port):
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.add(int(match.group(1)))
    return pids


def _free_listen_port(
    cfg: DnsproxySetupConfig, timeout: float, progress_priority: int
) -> str | None:
    '''Stop whatever listens on the resolver port; error text or None.

    The port belongs to dnsproxy; a leftover process from an earlier
    test run or a broken deployment would block a fresh start. Every
    process listening on the port is stopped and the user is told what
    was stopped. A port that stays occupied after the stop is an error.
    '''

    pids = _listening_pids(cfg, timeout)
    if not pids:
        return None
    ordered = sorted(pids)
    log_progress(
        f"port {cfg.listen_port} is occupied by PID(s) "
        f"{', '.join(str(pid) for pid in ordered)}; stopping them",
        priority=progress_priority,
    )
    for pid in ordered:
        run_command([*cfg.kill_command, str(pid)], check=False, timeout=timeout)
    remaining = _listening_pids(cfg, timeout)
    if remaining:
        return (
            f"port {cfg.listen_port} is still occupied by PID(s) "
            f"{', '.join(str(pid) for pid in sorted(remaining))} after the stop"
        )
    log_progress(
        f"stopped the process(es) holding port {cfg.listen_port}",
        priority=progress_priority,
    )
    return None


def _dns_probe_answers(cfg: DnsproxySetupConfig, timeout: float) -> bool:
    '''True when dnsproxy on the local port answers a direct A query.

    The probe sends one plain DNS query for the verification domain to
    the local listener over UDP and requires a matching response with an
    answer section. It runs before the resolver cutover, so a dnsproxy
    that is up but cannot resolve is caught while the system still uses
    its previous DNS. Only the standard library is used, so no extra
    package is needed on the target.
    '''

    ident = int.from_bytes(os.urandom(2), "big")
    header = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    question = b"".join(
        struct.pack(">B", len(label)) + label
        for label in cfg.verification_domain.rstrip(".").encode("ascii").split(b".")
    )
    query = header + question + struct.pack(">BHH", 0, 1, 1)
    for _ in range(cfg.start_check_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(cfg.start_check_retry_delay_seconds)
                sock.sendto(query, ("127.0.0.1", cfg.listen_port))
                data, _ = sock.recvfrom(4096)
        except OSError:
            time.sleep(cfg.start_check_retry_delay_seconds)
            continue
        if len(data) < 12:
            time.sleep(cfg.start_check_retry_delay_seconds)
            continue
        response_id, flags, _, answer_count = struct.unpack(">HHHH", data[:8])
        if (
            response_id == ident
            and (flags & 0x8000)
            and (flags & 0x000F) == 0
            and answer_count > 0
        ):
            return True
        time.sleep(cfg.start_check_retry_delay_seconds)
    return False


def _nmcli_available(cfg: DnsproxySetupConfig, timeout: float) -> bool:
    '''True when nmcli runs successfully.

    A missing or broken nmcli makes NetworkManager management impossible;
    the caller then skips NM changes instead of raising.
    '''

    try:
        result = run_command(
            list(cfg.nmcli_check_command), check=False, timeout=timeout
        )
    except OSError:
        return False
    return result.returncode == 0


def _disable_auto_dns_active(
    cfg: DnsproxySetupConfig, timeout: float, progress_priority: int
) -> list[tuple[str, str]]:
    '''Ignore auto DNS on active connections; the changed (UUID, device) pairs.

    NetworkManager is queried for the active connections by UUID, so a
    profile name repeated in the catalog cannot redirect the change to
    the wrong profile. The loopback connection is skipped. Every active
    connection that does not already ignore auto DNS is modified and
    reapplied to its running device, because a profile-only change keeps
    the DHCP-provided DNS on the per-link scope until the connection is
    reapplied. The changed pairs are returned for the revert. A missing
    nmcli changes nothing.
    '''

    if not _nmcli_available(cfg, timeout):
        log_progress(
            "nmcli unavailable, NetworkManager auto DNS management skipped",
            priority=progress_priority,
        )
        return []
    listing = run_command(
        list(cfg.nmcli_active_list_command), capture=True, timeout=timeout
    ).stdout.splitlines()
    changed: list[tuple[str, str]] = []
    for line in listing:
        fields = line.split(":")
        if len(fields) < 2 or not fields[1] or fields[0] == "lo":
            continue
        uuid = fields[1]
        device = fields[2] if len(fields) > 2 else ""
        state = run_command(
            [
                part.replace("{connection}", uuid)
                for part in cfg.nmcli_dns_state_command
            ],
            check=False,
            capture=True,
            timeout=timeout,
        )
        values = {
            part.split(":", 1)[-1].strip()
            for part in state.stdout.splitlines()
            if part.strip()
        }
        if values == {"yes"}:
            continue
        command = [
            part.replace("{connection}", uuid).replace("{value}", "true")
            for part in cfg.nmcli_modify_command
        ]
        run_command(command, timeout=timeout)
        if device:
            run_command(
                [
                    part.replace("{device}", device)
                    for part in cfg.nmcli_reapply_command
                ],
                timeout=timeout,
            )
        changed.append((uuid, device))
    return changed


def _restore_auto_dns(
    cfg: DnsproxySetupConfig, changed: list[tuple[str, str]], timeout: float
) -> None:
    for uuid, device in changed:
        command = [
            part.replace("{connection}", uuid).replace("{value}", "false")
            for part in cfg.nmcli_modify_command
        ]
        run_command(command, check=False, timeout=timeout)
        if device:
            run_command(
                [
                    part.replace("{device}", device)
                    for part in cfg.nmcli_reapply_command
                ],
                check=False,
                timeout=timeout,
            )


def _global_block_lines(status_lines: list[str]) -> list[str]:
    '''The lines of the Global block of resolvectl status output.

    The block starts at the line Global and ends at the first empty line
    or Link block, whichever comes first. A missing Global marker yields
    an empty list.
    '''

    started = False
    block: list[str] = []
    for line in status_lines:
        stripped = line.strip()
        if not started:
            if stripped == "Global":
                started = True
            continue
        if not stripped or stripped.startswith("Link "):
            break
        block.append(line)
    return block


def _resolved_uses_dnsproxy(cfg: DnsproxySetupConfig, timeout: float) -> str | None:
    '''Error text when systemd-resolved does not route through dnsproxy.

    Three facts from the Global block of resolvectl status prove that
    every system query goes through dnsproxy: the resolv.conf mode is
    stub, so applications resolve through systemd-resolved, the global
    DNS points at our loopback listener, so systemd-resolved forwards to
    dnsproxy, and the wildcard routing domain ~. is present, so no query
    can fall through to a default-route per-link server. Any of the
    three missing means the routing guarantee is broken.
    '''

    result = run_command(
        list(cfg.resolvectl_status_command),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return f"cannot read resolvectl status: exited {result.returncode}"
    global_text = "\n".join(_global_block_lines(result.stdout.splitlines()))
    if "resolv.conf mode: stub" not in global_text:
        return "systemd-resolved does not use the stub resolv.conf mode"
    dns_scope_lines = " ".join(
        line
        for line in global_text.splitlines()
        if line.lstrip().startswith(("Current DNS Server", "DNS Servers"))
    )
    loopback_v4 = f"127.0.0.1:{cfg.listen_port}"
    loopback_v6 = f"[::1]:{cfg.listen_port}"
    if loopback_v4 not in dns_scope_lines and loopback_v6 not in dns_scope_lines:
        return (
            "systemd-resolved global DNS does not point at "
            f"127.0.0.1:{cfg.listen_port}"
        )
    domain_lines = " ".join(
        line
        for line in global_text.splitlines()
        if line.lstrip().startswith("DNS Domain")
    )
    if "~." not in domain_lines:
        return "systemd-resolved global DNS has no ~. routing domain"
    return None


def _per_link_dns_addresses(output: str) -> set[str]:
    '''Validated DNS addresses on the per-link scopes of resolvectl dns.

    Only Link lines carry per-link servers; the Global and empty scopes
    are skipped. Each token is validated as an IP address so a truncated
    token such as 810:100::15 can never match as a substring of a longer
    address such as 2800:810:100::15.
    '''

    addresses: set[str] = set()
    for line in output.splitlines():
        if not line.lstrip().startswith("Link "):
            continue
        for token in line.split(":", 1)[-1].split():
            try:
                address = ipaddress.ip_address(token.strip("[]"))
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified:
                continue
            addresses.add(str(address))
    return addresses


def _verify_system(
    cfg: DnsproxySetupConfig,
    discovered: DiscoveredDnsServers,
    timeout: float,
) -> tuple[str | None, str | None]:
    '''Error and warning text after the resolver cutover.

    The functional check queries the verification domain through
    systemd-resolved. The routing check then reads the Global block of
    resolvectl status: a stub resolv.conf mode, a global DNS pointing at
    our loopback listener and the wildcard routing domain ~. prove that
    systemd-resolved routes every query through dnsproxy. Surviving
    per-link provider DNS is not an error by itself: without a routing
    domain on the per-link scope it does not compete with the global ~.,
    so the leftover servers are reported as a warning, not a failure.
    '''

    command = [
        part.replace("{domain}", cfg.verification_domain)
        for part in cfg.verification_command
    ]
    result = run_command(command, check=False, capture=True, timeout=timeout)
    if result.returncode != 0:
        excerpt = (result.stdout + result.stderr).strip()[:200] or "<no output>"
        return f"system DNS verification failed: {excerpt}", None
    route_error = _resolved_uses_dnsproxy(cfg, timeout)
    if route_error is not None:
        return (
            (
                "systemd-resolved would not route queries through dnsproxy: "
                f"{route_error}"
            ),
            None,
        )
    warnings: list[str] = []
    if cfg.append_provider_dns and (discovered.ipv4 or discovered.ipv6):
        state = run_command(
            list(cfg.resolvectl_dns_command),
            check=False,
            capture=True,
            timeout=timeout,
        )
        if state.returncode != 0:
            warnings.append(
                "cannot read per-link DNS state: resolvectl exited "
                f"{state.returncode}"
            )
        else:
            leftover = [
                address
                for address in (*discovered.ipv4, *discovered.ipv6)
                if address in _per_link_dns_addresses(state.stdout)
            ]
            if leftover:
                warnings.append(
                    "per-link DNS still lists provider resolver(s) "
                    f"{', '.join(leftover)}; systemd-resolved routes queries "
                    "through dnsproxy, but removing these servers from the "
                    "active connection makes the configuration clean"
                )
    return None, "; ".join(warnings) if warnings else None


def _service_log(cfg: DnsproxySetupConfig, timeout: float) -> str:
    '''The last service journal lines, for a failed start diagnosis.'''

    command = [
        part.replace("{unit}", cfg.service_unit_name)
        for part in cfg.service_log_command
    ]
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout + result.stderr).strip()[-400:]


def _revert(
    cfg: DnsproxySetupConfig,
    dropin_changed: bool,
    auto_dns_changed: list[tuple[str, str]],
    timeout: float,
    progress_priority: int,
    error_priority: int,
) -> None:
    '''Undo the resolver cutover; never raises.

    The drop-in is removed when this run wrote it, the modified
    NetworkManager connections return to their previous auto DNS
    handling, systemd-resolved is restarted and the dnsproxy service is
    stopped. Every step is journaled; a failed step is reported but
    cannot stop the revert.
    '''

    if dropin_changed:
        path = cfg.resolved_conf_dir / cfg.resolved_dropin_file_name
        try:
            path.unlink()
            log_progress(
                "reverted: removed the resolver drop-in",
                priority=progress_priority,
            )
        except OSError as exc:
            log_progress(
                f"revert: cannot remove {path}: {exc}", priority=error_priority
            )
    if auto_dns_changed:
        try:
            _restore_auto_dns(cfg, auto_dns_changed, timeout)
            log_progress(
                "reverted: restored NetworkManager auto DNS handling",
                priority=progress_priority,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_progress(
                f"revert: cannot restore NetworkManager: {exc}",
                priority=error_priority,
            )
    try:
        run_command(list(cfg.restart_resolved_command), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log_progress(
            f"revert: cannot restart systemd-resolved: {exc}",
            priority=error_priority,
        )
    try:
        run_command(
            ["systemctl", "stop", cfg.service_unit_name],
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_progress(f"revert: cannot stop dnsproxy: {exc}", priority=error_priority)


def _write_resolver_dropin(cfg: DnsproxySetupConfig) -> bool:
    path = cfg.resolved_conf_dir / cfg.resolved_dropin_file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = sync_directives_by_key(
        path,
        (*cfg.resolved_dns_directives, cfg.resolved_domains_directive),
        cfg.resolved_dropin_header,
        cfg.resolved_section,
    )
    path.chmod(cfg.resolved_dropin_file_mode)
    ensure_root_owner(path)
    return changed


def _wait_active(cfg: DnsproxySetupConfig, timeout: float) -> bool:
    for _ in range(cfg.start_check_attempts):
        time.sleep(cfg.start_check_retry_delay_seconds)
        if service_is_active(cfg.service_unit_name, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    cfg = ctx.config.dnsproxy_setup
    timeout = ctx.config.engine.command_timeout_seconds
    curl_timeout = ctx.config.engine.curl_timeout_seconds
    curl_retries = ctx.config.engine.curl_retries
    connect_timeout = ctx.config.engine.curl_connect_timeout_seconds
    retry_max_time = ctx.config.engine.curl_retry_max_time_seconds
    error_priority = ctx.config.engine.error_priority
    progress_priority = ctx.config.engine.progress_priority
    profile_id = _read_profile_id(cfg)
    if profile_id is None:
        return TaskResult(
            success=False,
            error=(
                "cannot read the NextDNS profile id from "
                f"{cfg.profile_id_file_path}; nextdns_setup_system_wide "
                "must run first"
            ),
        )
    try:
        release = _release_json(
            cfg.github_repo,
            timeout,
            curl_timeout,
            curl_retries,
            connect_timeout,
            retry_max_time,
        )
        tag = str(release["tag_name"])
        asset_name, asset_url = _asset_for_architecture(
            release, dpkg_architecture(timeout)
        )
        target_version = _version_from_tag(tag)
    except (
        RuntimeError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        return TaskResult(success=False, error=str(exc))
    installed = _installed_version(cfg.binary_path, timeout)
    changed = False
    dropin_changed = False
    auto_dns_changed: list[tuple[str, str]] = []
    verify_warning: str | None = None
    cut_over = False
    try:
        if installed != target_version:
            staged = _download_binary(
                cfg,
                asset_url,
                asset_name,
                timeout,
                curl_timeout,
                curl_retries,
                connect_timeout,
                retry_max_time,
            )
            cfg.binary_path.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(cfg.binary_path)
            ensure_root_owner(cfg.binary_path)
            changed = True
        discovered = (
            discover_dns_servers(cfg, timeout)
            if cfg.append_provider_dns
            else DiscoveredDnsServers((), (), ())
        )
        service_path = cfg.service_unit_path
        service_content = _render_service(cfg, profile_id, discovered)
        if (
            not service_path.exists()
            or service_path.read_text(encoding="utf-8") != service_content
        ):
            service_path.write_text(service_content, encoding="utf-8")
            ensure_root_owner(service_path)
            run_command(list(cfg.daemon_reload_command), timeout=timeout)
            changed = True
        active = service_is_active(cfg.service_unit_name, timeout)
        if not active:
            error = _free_listen_port(cfg, timeout, progress_priority)
            if error is not None:
                return TaskResult(success=False, changed=changed, error=error)
        if not service_is_enabled(cfg.service_unit_name, timeout):
            run_command(
                ["systemctl", "enable", cfg.service_unit_name], timeout=timeout
            )
            changed = True
        if not active or changed or ctx.force_tasks.intersection({"dnsproxy_setup"}):
            run_command(
                ["systemctl", "restart" if active else "start", cfg.service_unit_name],
                timeout=timeout,
            )
            if not _wait_active(cfg, timeout):
                excerpt = _service_log(cfg, timeout)
                detail = f"; service log: {excerpt}" if excerpt else ""
                run_command(
                    ["systemctl", "stop", cfg.service_unit_name],
                    check=False,
                    timeout=timeout,
                )
                return TaskResult(
                    success=False,
                    changed=True,
                    error="dnsproxy service did not become active" + detail,
                )
            if not _dns_probe_answers(cfg, timeout):
                run_command(
                    ["systemctl", "stop", cfg.service_unit_name],
                    check=False,
                    timeout=timeout,
                )
                return TaskResult(
                    success=False,
                    changed=True,
                    error=(
                        "dnsproxy started but does not answer direct DNS "
                        "queries; the system resolver was not changed"
                    ),
                )
            changed = True
        if _write_resolver_dropin(cfg):
            dropin_changed = True
            changed = True
        cut_over = True
        if dropin_changed:
            run_command(list(cfg.restart_resolved_command), timeout=timeout)
        if cfg.manage_networkmanager:
            auto_dns_changed = _disable_auto_dns_active(
                cfg, timeout, progress_priority
            )
        verify_error, verify_warning = _verify_system(cfg, discovered, timeout)
        if verify_error is not None:
            detail = (
                f"{verify_error}; the resolver drop-in and the dnsproxy "
                "service were kept, so the system stays on dnsproxy; "
                "check the systemd-resolved routing and rerun the task"
            )
            log_progress(
                f"dnsproxy setup failed: {detail}", priority=error_priority
            )
            return TaskResult(
                success=False,
                changed=True,
                error=f"dnsproxy setup failed: {detail}",
            )
    except (OSError, subprocess.SubprocessError, tarfile.TarError, RuntimeError) as exc:
        if cut_over:
            _revert(
                cfg,
                dropin_changed,
                auto_dns_changed,
                timeout,
                progress_priority,
                error_priority,
            )
        return TaskResult(
            success=False, changed=changed, error=f"dnsproxy setup failed: {exc}"
        )
    return TaskResult(
        success=True,
        changed=changed,
        message=f"dnsproxy active with NextDNS profile {profile_id}",
        warnings=(verify_warning,) if verify_warning else (),
    )
