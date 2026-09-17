# Install and configure dnsproxy as the system-wide DNS resolver.

from __future__ import annotations

import ipaddress
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

from pyntara.config import EngineConfig
from pyntara.config_edit import sync_directives_by_key
from pyntara.context import Context
from pyntara.github_release import asset_name_urls, fetch_latest_release, release_tag
from pyntara.logger import log_progress
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    download_command,
    dpkg_architecture,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    version_from_output,
)
from pyntara.values import common as common_values
from pyntara.values import dnsproxy_setup as values
from pyntara.values import missing_value_names

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


def _resolvectl_dns_tokens(output: str) -> list[str]:
    """Names of the DNS servers in the output of the resolvectl query.

    Every line of that output carries its value after a colon, so the part
    behind the first colon is taken as a list of tokens; a line without a
    colon contributes nothing.
    """

    tokens: list[str] = []
    for line in output.splitlines():
        tokens.extend(line.split(":", 1)[-1].split())
    return tokens


def _nmcli_dns_tokens(output: str) -> list[str]:
    """Names of the DNS servers in the output of the nmcli query.

    Only the lines of the two DNS property fields carry addresses, so every
    other line of the answer (a device line, a route line) is skipped. The
    field name itself is the vocabulary of the tool and is matched as a
    pattern.
    """

    tokens: list[str] = []
    for line in output.splitlines():
        match = re.match(r"IP[46]\.DNS(?:\[\d+\])?:(.*)$", line)
        if match:
            tokens.extend(match.group(1).split())
    return tokens


def discover_dns_servers(timeout: float) -> DiscoveredDnsServers:
    '''Discover DNS from both configured resolvectl and nmcli commands.

    Both commands are always called and their current-state outputs are combined.
    Duplicate addresses are removed, valid IPv4 and IPv6 addresses are sorted,
    and command diagnostics are returned. No files are read, system state is not
    changed, and DNS reachability is not tested. Each command carries the reader
    of its own output, and a diagnostic names the program the configured command
    starts with, so no program name written in the code can disagree with the
    configured command.
    '''
    addresses_v4: set[str] = set()
    addresses_v6: set[str] = set()
    errors: list[str] = []
    probes = (
        (values.RESOLVECTL_DNS_COMMAND, _resolvectl_dns_tokens),
        (values.NMCLI_DNS_COMMAND, _nmcli_dns_tokens),
    )
    for command, parse_tokens in probes:
        program = command[0]
        try:
            result = run_command(command, check=False, capture=True, timeout=timeout)
            if result.returncode != 0:
                errors.append(f"{program} exited with {result.returncode}")
            _add_valid_dns_tokens(
                parse_tokens(result.stdout), addresses_v4, addresses_v6
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{program} failed: {exc}")
    return DiscoveredDnsServers(
        tuple(sorted(addresses_v4)), tuple(sorted(addresses_v6)), tuple(errors)
    )


def _asset_for_architecture(
    payload: dict[str, object], arch: str
) -> tuple[str, str]:
    """The (name, url) of the dnsproxy tarball for this architecture.

    The asset name comes from the configured template; the architecture
    spelling comes from the configured table, because dnsproxy names its
    architectures itself and prefixes them. An architecture the table does
    not name is an error, not a fallback to another architecture.
    """

    tag = release_tag(payload)
    assets = dict(asset_name_urls(payload))
    asset_arch = values.ASSET_ARCHITECTURE_NAMES.get(arch)
    if asset_arch is None:
        raise RuntimeError(f"unsupported dnsproxy architecture: {arch}")
    expected = values.ASSET_NAME_TEMPLATE.format(
        asset_arch=asset_arch, release_tag=tag
    )
    if expected in assets:
        return expected, assets[expected]
    raise RuntimeError(f"release {tag} has no asset {expected}")


def _installed_version(path: Path, timeout: float) -> str | None:
    try:
        result = run_command(
            substituted_command(
                values.INSTALLED_VERSION_COMMAND, {"binary": str(path)}
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return version_from_output(result.stdout + result.stderr)


def _version_from_tag(tag: str) -> str:
    version = version_from_output(tag)
    if version is None:
        raise RuntimeError(f"cannot parse dnsproxy release version: {tag}")
    return version


def _download_binary(
    engine: EngineConfig,
    url: str,
    name: str,
    timeout: float,
) -> Path:
    values.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    archive = values.DOWNLOAD_DIR / name
    run_command(
        download_command(engine, archive, url),
        timeout=timeout,
    )
    extract_dir = values.DOWNLOAD_DIR / values.EXTRACT_DIR_NAME
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir()
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(extract_dir, filter="data")
    candidates = list(extract_dir.rglob(values.BINARY_FILE_NAME))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError("dnsproxy archive does not contain exactly one binary")
    staged = values.DOWNLOAD_DIR / values.STAGED_BINARY_FILE_NAME
    shutil.copyfile(candidates[0], staged)
    staged.chmod(values.STAGED_BINARY_FILE_MODE)
    return staged


def _upstreams(profile_id: str) -> tuple[str, ...]:
    return tuple(
        format_string.format(profile_id=profile_id)
        for format_string in (
            values.DOH_URL_FORMAT,
            values.DOT_HOST_FORMAT,
            values.DOQ_HOST_FORMAT,
        )
    )


def _protocol_forms(addresses: tuple[str, ...]) -> tuple[str, ...]:
    '''Protocol forms of each address, one argument per configured form.

    Every address yields one argument per entry of
    bootstrap_form_templates, with {host} replaced by the address; an IPv6
    host is enclosed in square brackets. The shipped templates are plain
    DNS on port 53, DoT on 853, DoH on 443 and DoQ on 853. DNSCrypt is not
    generated because a bare IP is not enough for it and the pool carries
    no stamps. The same forms feed both the bootstrap and the fallback
    resolver groups.
    '''

    forms: list[str] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RuntimeError(f"invalid bootstrap address {address!r}: {exc}") from exc
        host = f"[{ip}]" if ip.version == 6 else str(ip)
        forms.extend(
            template.format(host=host)
            for template in values.BOOTSTRAP_FORM_TEMPLATES
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


def _flag(name: str, value: str | None = None) -> str:
    """The flag of the daemon, with its value filled in.

    The flags of the daemon live in DAEMON_FLAG_TEMPLATES, so the flag a value
    is passed with is a value of the section; a flag that takes no value is
    returned as it stands.
    """

    template = values.DAEMON_FLAG_TEMPLATES[name]
    if value is None:
        return template
    return template.format(value=value)


def _command(profile_id: str, discovered: DiscoveredDnsServers) -> list[str]:
    command = [
        str(values.BINARY_PATH),
        _flag("port", str(values.LISTEN_PORT)),
    ]
    for address in values.LISTEN_ADDRESSES:
        command.append(_flag("listen", address))
    for upstream in _upstreams(profile_id):
        command.append(_flag("upstream", upstream))
    pool_forms = _protocol_forms(values.BOOTSTRAP_RESOLVERS)
    provider_forms = _plain_udp_forms((*discovered.ipv4, *discovered.ipv6))
    for fallback in (*pool_forms, *provider_forms):
        command.append(_flag("fallback", fallback))
    command.extend(
        (
            _flag("upstream_mode", values.UPSTREAM_MODE),
            _flag("timeout", str(values.TIMEOUT_SECONDS)),
        )
    )
    if values.CACHE_ENABLED:
        command.append(_flag("cache"))
        command.append(_flag("cache_size", str(values.CACHE_SIZE_BYTES)))
    for bootstrap in (*pool_forms, *provider_forms):
        command.append(_flag("bootstrap", bootstrap))
    return command


def _render_service(
    profile_id: str,
    discovered: DiscoveredDnsServers,
    template_path: Path,
) -> str:
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_start=" ".join(_command(profile_id, discovered)),
        service_restart_seconds=values.SERVICE_RESTART_SECONDS,
        log_rate_limit_interval_seconds=values.LOG_RATE_LIMIT_INTERVAL_SECONDS,
        log_rate_limit_burst=values.LOG_RATE_LIMIT_BURST,
    )


def _read_profile_id() -> str | None:
    '''The NextDNS profile id recorded by nextdns_setup_system_wide.

    The profile id file is the shared single source of truth written by
    the nextdns_setup_system_wide task; dnsproxy never opens the vault
    itself, so both tasks always agree on the profile. A missing or
    malformed file returns None and the task stops before any change.
    '''

    try:
        value = (
            common_values.PROFILE_ID_FILE_PATH.read_text(encoding="utf-8").strip()
        )
    except OSError:
        return None
    if PROFILE_ID_PATTERN.fullmatch(value) is None:
        return None
    return value


def _listening_pids(timeout: float) -> set[int]:
    '''PIDs of processes listening on the resolver port.

    The TCP and the UDP listen states are scanned through ss. A line
    belongs to the port when its local address ends with the configured
    port; every pid token in that line is collected. A failing ss
    command yields an empty set, so a missing tool cannot stop the run.
    '''

    pids: set[int] = set()
    for command in (values.SS_TCP_LISTEN_COMMAND, values.SS_UDP_LISTEN_COMMAND):
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
            if fields[3].split(":")[-1] != str(values.LISTEN_PORT):
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.add(int(match.group(1)))
    return pids


def _free_listen_port(timeout: float, progress_priority: int) -> str | None:
    '''Stop whatever listens on the resolver port; error text or None.

    The port belongs to dnsproxy; a leftover process from an earlier
    test run or a broken deployment would block a fresh start. Every
    process listening on the port is stopped and the user is told what
    was stopped. A port that stays occupied after the stop is an error.
    '''

    pids = _listening_pids(timeout)
    if not pids:
        return None
    ordered = sorted(pids)
    log_progress(
        f"port {values.LISTEN_PORT} is occupied by PID(s) "
        f"{', '.join(str(pid) for pid in ordered)}; stopping them",
        priority=progress_priority,
    )
    for pid in ordered:
        run_command(
            [*values.KILL_COMMAND, str(pid)], check=False, timeout=timeout
        )
    remaining = _listening_pids(timeout)
    if remaining:
        return (
            f"port {values.LISTEN_PORT} is still occupied by PID(s) "
            f"{', '.join(str(pid) for pid in sorted(remaining))} after the stop"
        )
    log_progress(
        f"stopped the process(es) holding port {values.LISTEN_PORT}",
        priority=progress_priority,
    )
    return None


def _dns_probe_answers(timeout: float) -> bool:
    '''True when dnsproxy on the local port answers a direct A query.

    The probe sends one plain DNS query for the verification domain to
    the local listener over UDP and requires a matching response with an
    answer section. It runs before the resolver cutover, so a dnsproxy
    that is up but cannot resolve is caught while the system still uses
    its previous DNS. Only the standard library is used, so no extra
    package is needed on the target.
    '''

    ident = int.from_bytes(os.urandom(values.PROBE_IDENT_BYTES), "big")
    header = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    question = b"".join(
        struct.pack(">B", len(label)) + label
        for label in values.VERIFICATION_DOMAIN.rstrip(".").encode("ascii").split(b".")
    )
    query = header + question + struct.pack(">BHH", 0, 1, 1)
    for _ in range(values.START_CHECK_ATTEMPTS):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(values.START_CHECK_RETRY_DELAY_SECONDS)
                sock.sendto(query, (values.PROBE_ADDRESS, values.LISTEN_PORT))
                data, _ = sock.recvfrom(4096)
        except OSError:
            time.sleep(values.START_CHECK_RETRY_DELAY_SECONDS)
            continue
        if len(data) < 12:
            time.sleep(values.START_CHECK_RETRY_DELAY_SECONDS)
            continue
        response_id, flags, _, answer_count = struct.unpack(">HHHH", data[:8])
        if (
            response_id == ident
            and (flags & 0x8000)
            and (flags & 0x000F) == 0
            and answer_count > 0
        ):
            return True
        time.sleep(values.START_CHECK_RETRY_DELAY_SECONDS)
    return False


def _nmcli_available(timeout: float) -> bool:
    '''True when nmcli runs successfully.

    A missing or broken nmcli makes NetworkManager management impossible;
    the caller then skips NM changes instead of raising.
    '''

    try:
        result = run_command(
            list(values.NMCLI_CHECK_COMMAND), check=False, timeout=timeout
        )
    except OSError:
        return False
    return result.returncode == 0


def _tun_device_names(timeout: float) -> set[str]:
    '''Device names of type tun reported by the nmcli device status.

    The auto DNS sweep must never modify a tun device. NetworkManager
    only holds such an interface as an assumed external connection, and
    a connection modify on a netplan-managed host persists the assumed
    profile into a permanent auto-connect profile, so the next start of
    the owning service panics on the already assigned address. A failed
    query returns an empty set, which makes the sweep treat every active
    connection as a regular uplink.
    '''

    try:
        status = run_command(
            list(values.NMCLI_DEVICE_STATUS_COMMAND),
            capture=True,
            timeout=timeout,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in status.splitlines():
        fields = line.split(":")
        if len(fields) >= 2 and fields[1] == values.TUN_DEVICE_TYPE:
            names.add(fields[0])
    return names


def _disable_auto_dns_active(
    timeout: float, progress_priority: int
) -> list[tuple[str, str]]:
    '''Ignore auto DNS on active connections; the changed (UUID, device) pairs.

    NetworkManager is queried for the active connections by UUID, so a
    profile name repeated in the catalog cannot redirect the change to
    the wrong profile. The loopback connection is skipped. Tun devices
    are skipped too: they carry no DHCP-provided DNS, and modifying the
    assumed external connection of an interface owned by another service
    would persist it as a permanent NetworkManager profile. Every other
    active connection that does not already ignore auto DNS is modified
    and reapplied to its running device, because a profile-only change
    keeps the DHCP-provided DNS on the per-link scope until the
    connection is reapplied. The changed pairs are returned for the
    revert. A missing nmcli changes nothing.
    '''

    if not _nmcli_available(timeout):
        log_progress(
            "nmcli unavailable, NetworkManager auto DNS management skipped",
            priority=progress_priority,
        )
        return []
    tun_devices = _tun_device_names(timeout)
    listing = run_command(
        list(values.NMCLI_ACTIVE_LIST_COMMAND), capture=True, timeout=timeout
    ).stdout.splitlines()
    changed: list[tuple[str, str]] = []
    for line in listing:
        fields = line.split(":")
        if (
            len(fields) < 2
            or not fields[1]
            or fields[0] == values.LOOPBACK_CONNECTION_NAME
        ):
            continue
        uuid = fields[1]
        device = fields[2] if len(fields) > 2 else ""
        if device in tun_devices:
            continue
        state = run_command(
            substituted_command(
                values.NMCLI_DNS_STATE_COMMAND, {"connection": uuid}
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        answers = {
            part.split(":", 1)[-1].strip()
            for part in state.stdout.splitlines()
            if part.strip()
        }
        if answers == {values.NMCLI_AUTO_DNS_IGNORED_VALUE}:
            continue
        run_command(
            substituted_command(
                values.NMCLI_MODIFY_COMMAND,
                {
                    "connection": uuid,
                    "value": values.NMCLI_IGNORE_AUTO_DNS_VALUE,
                },
            ),
            timeout=timeout,
        )
        if device:
            run_command(
                substituted_command(
                    values.NMCLI_REAPPLY_COMMAND, {"device": device}
                ),
                timeout=timeout,
            )
        changed.append((uuid, device))
    return changed


def _restore_auto_dns(
    changed: list[tuple[str, str]], timeout: float
) -> None:
    for uuid, device in changed:
        run_command(
            substituted_command(
                values.NMCLI_MODIFY_COMMAND,
                {
                    "connection": uuid,
                    "value": values.NMCLI_RESTORE_AUTO_DNS_VALUE,
                },
            ),
            check=False,
            timeout=timeout,
        )
        if device:
            run_command(
                substituted_command(
                    values.NMCLI_REAPPLY_COMMAND, {"device": device}
                ),
                check=False,
                timeout=timeout,
            )


def _global_block_lines(
    status_lines: list[str], global_marker: str, link_prefix: str
) -> list[str]:
    '''The lines of the Global block of resolvectl status output.

    The block starts at the line the config names as the global marker and
    ends at the first empty line or per-link line, whichever comes first. A
    missing marker yields an empty list. Both names belong to the output of
    the tool and are config values.
    '''

    started = False
    block: list[str] = []
    for line in status_lines:
        stripped = line.strip()
        if not started:
            if stripped == global_marker:
                started = True
            continue
        if not stripped or stripped.startswith(link_prefix):
            break
        block.append(line)
    return block


def _resolved_uses_dnsproxy(timeout: float) -> str | None:
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
        list(values.RESOLVECTL_STATUS_COMMAND),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return f"cannot read resolvectl status: exited {result.returncode}"
    global_text = "\n".join(
        _global_block_lines(
            result.stdout.splitlines(),
            values.RESOLVED_STATUS_GLOBAL_MARKER,
            values.RESOLVED_STATUS_LINK_PREFIX,
        )
    )
    if values.RESOLVED_STUB_MODE_LINE not in global_text:
        return "systemd-resolved does not use the stub resolv.conf mode"
    dns_scope_lines = " ".join(
        line
        for line in global_text.splitlines()
        if line.lstrip().startswith(values.RESOLVED_STATUS_DNS_SERVER_LABELS)
    )
    loopback_v4 = f"127.0.0.1:{values.LISTEN_PORT}"
    loopback_v6 = f"[::1]:{values.LISTEN_PORT}"
    if loopback_v4 not in dns_scope_lines and loopback_v6 not in dns_scope_lines:
        return (
            "systemd-resolved global DNS does not point at "
            f"127.0.0.1:{values.LISTEN_PORT}"
        )
    domain_lines = " ".join(
        line
        for line in global_text.splitlines()
        if line.lstrip().startswith(values.RESOLVED_STATUS_DNS_DOMAIN_LABEL)
    )
    if values.RESOLVED_WILDCARD_DOMAIN not in domain_lines:
        return "systemd-resolved global DNS has no ~. routing domain"
    return None


def _per_link_dns_addresses(output: str, link_prefix: str) -> set[str]:
    '''Validated DNS addresses on the per-link scopes of resolvectl dns.

    Only the lines the config marks as per-link carry servers; the Global
    and empty scopes are skipped. Each token is validated as an IP address
    so a truncated token such as 810:100::15 can never match as a substring
    of a longer address such as 2800:810:100::15.
    '''

    addresses: set[str] = set()
    for line in output.splitlines():
        if not line.lstrip().startswith(link_prefix):
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
        part.replace("{domain}", values.VERIFICATION_DOMAIN)
        for part in values.VERIFICATION_COMMAND
    ]
    result = run_command(command, check=False, capture=True, timeout=timeout)
    if result.returncode != 0:
        excerpt = (
            result.stdout + result.stderr
        ).strip()[: values.VERIFICATION_ERROR_EXCERPT_LENGTH] or "<no output>"
        return f"system DNS verification failed: {excerpt}", None
    route_error = _resolved_uses_dnsproxy(timeout)
    if route_error is not None:
        return (
            (
                "systemd-resolved would not route queries through dnsproxy: "
                f"{route_error}"
            ),
            None,
        )
    warnings: list[str] = []
    if values.APPEND_PROVIDER_DNS and (discovered.ipv4 or discovered.ipv6):
        state = run_command(
            list(values.RESOLVECTL_DNS_COMMAND),
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
                if address
                in _per_link_dns_addresses(
                    state.stdout, values.RESOLVED_STATUS_LINK_PREFIX
                )
            ]
            if leftover:
                warnings.append(
                    "per-link DNS still lists provider resolver(s) "
                    f"{', '.join(leftover)}; systemd-resolved routes queries "
                    "through dnsproxy, but removing these servers from the "
                    "active connection makes the configuration clean"
                )
    return None, "; ".join(warnings) if warnings else None


def _service_log(timeout: float) -> str:
    '''The last service journal lines, for a failed start diagnosis.'''

    command = [
        part.replace("{unit}", values.SERVICE_UNIT_NAME)
        for part in values.SERVICE_LOG_COMMAND
    ]
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout + result.stderr).strip()[
        -values.SERVICE_LOG_EXCERPT_LENGTH :
    ]


def _revert(
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
        path = values.RESOLVED_CONF_DIR / values.RESOLVED_DROPIN_FILE_NAME
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
            _restore_auto_dns(auto_dns_changed, timeout)
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
        run_command(list(values.RESTART_RESOLVED_COMMAND), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log_progress(
            f"revert: cannot restart systemd-resolved: {exc}",
            priority=error_priority,
        )
    try:
        run_command(
            substituted_command(
                values.SERVICE_STOP_COMMAND,
                {"service_unit_name": values.SERVICE_UNIT_NAME},
            ),
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_progress(f"revert: cannot stop dnsproxy: {exc}", priority=error_priority)


def _write_resolver_dropin(owner_uid: int, owner_gid: int) -> bool:
    path = values.RESOLVED_CONF_DIR / values.RESOLVED_DROPIN_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = sync_directives_by_key(
        path,
        (*values.RESOLVED_DNS_DIRECTIVES, values.RESOLVED_DOMAINS_DIRECTIVE),
        values.RESOLVED_DROPIN_HEADER,
        values.RESOLVED_SECTION,
    )
    path.chmod(values.RESOLVED_DROPIN_FILE_MODE)
    apply_owner(path, owner_uid, owner_gid)
    return changed


def _wait_active(engine: EngineConfig, timeout: float) -> bool:
    for _ in range(values.START_CHECK_ATTEMPTS):
        time.sleep(values.START_CHECK_RETRY_DELAY_SECONDS)
        if service_is_active(engine, values.SERVICE_UNIT_NAME, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    absent = missing_value_names(
        values, values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
        return TaskResult(
            success=True,
            message="the dnsproxy_setup values are not declared, nothing was changed",
            warnings=(
                "the dnsproxy_setup values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = ctx.config.engine.command_timeout_seconds
    owner_uid = ctx.config.engine.root_owner_uid
    owner_gid = ctx.config.engine.root_owner_gid
    error_priority = ctx.config.engine.error_priority
    progress_priority = ctx.config.engine.progress_priority
    profile_id = _read_profile_id()
    if profile_id is None:
        # Without the profile id dnsproxy has no upstream to answer from,
        # so nothing is deployed and the reason is reported.
        warning = (
            "cannot read the NextDNS profile id from "
            f"{common_values.PROFILE_ID_FILE_PATH}; nextdns_setup_system_wide "
            "must run first"
        )
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    try:
        release = fetch_latest_release(values.GITHUB_REPO, ctx.config.engine)
        tag = release_tag(release)
        asset_name, asset_url = _asset_for_architecture(
            release, dpkg_architecture(ctx.config.engine, timeout)
        )
        target_version = _version_from_tag(tag)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        # The release or the architecture query failed: the binary cannot
        # be fetched, so the system resolver is left untouched.
        return TaskResult(
            success=True,
            changed=False,
            message=str(exc),
            warnings=(str(exc),),
        )
    installed = _installed_version(values.BINARY_PATH, timeout)
    changed = False
    dropin_changed = False
    auto_dns_changed: list[tuple[str, str]] = []
    verify_warning: str | None = None
    cut_over = False
    try:
        if installed != target_version:
            staged = _download_binary(
                ctx.config.engine,
                asset_url,
                asset_name,
                timeout,
            )
            values.BINARY_PATH.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(values.BINARY_PATH)
            apply_owner(values.BINARY_PATH, owner_uid, owner_gid)
            changed = True
        discovered = (
            discover_dns_servers(timeout)
            if values.APPEND_PROVIDER_DNS
            else DiscoveredDnsServers((), (), ())
        )
        service_path = values.SERVICE_UNIT_PATH
        service_content = _render_service(
            profile_id,
            discovered,
            ctx.repo_root / values.SERVICE_TEMPLATE_PATH,
        )
        if (
            not service_path.exists()
            or service_path.read_text(encoding="utf-8") != service_content
        ):
            service_path.write_text(service_content, encoding="utf-8")
            apply_owner(service_path, owner_uid, owner_gid)
            run_command(list(values.DAEMON_RELOAD_COMMAND), timeout=timeout)
            changed = True
        active = service_is_active(
            ctx.config.engine, values.SERVICE_UNIT_NAME, timeout
        )
        if not active:
            error = _free_listen_port(timeout, progress_priority)
            if error is not None:
                # A busy listen port keeps the old daemon alive: nothing is
                # cut over and the reason is reported.
                return TaskResult(
                    success=True,
                    changed=changed,
                    message=error,
                    warnings=(error,),
                )
        if not service_is_enabled(
            ctx.config.engine, values.SERVICE_UNIT_NAME, timeout
        ):
            run_command(
                substituted_command(
                    values.SERVICE_ENABLE_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
            changed = True
        if not active or changed or ctx.task_name in ctx.force_tasks:
            service_command = (
                values.SERVICE_RESTART_COMMAND
                if active
                else values.SERVICE_START_COMMAND
            )
            run_command(
                substituted_command(
                    service_command,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
            if not _wait_active(ctx.config.engine, timeout):
                excerpt = _service_log(timeout)
                detail = f"; service log: {excerpt}" if excerpt else ""
                run_command(
                    substituted_command(
                        values.SERVICE_STOP_COMMAND,
                        {"service_unit_name": values.SERVICE_UNIT_NAME},
                    ),
                    check=False,
                    timeout=timeout,
                )
                return TaskResult(
                    success=True,
                    changed=True,
                    message="dnsproxy service did not become active" + detail,
                    warnings=(
                        "dnsproxy service did not become active" + detail,
                    ),
                )
            if not _dns_probe_answers(timeout):
                run_command(
                    substituted_command(
                        values.SERVICE_STOP_COMMAND,
                        {"service_unit_name": values.SERVICE_UNIT_NAME},
                    ),
                    check=False,
                    timeout=timeout,
                )
                return TaskResult(
                    success=True,
                    changed=True,
                    message=(
                        "dnsproxy started but does not answer direct DNS "
                        "queries; the system resolver was not changed"
                    ),
                    warnings=(
                        (
                            "dnsproxy started but does not answer direct DNS "
                            "queries; the system resolver was not changed"
                        ),
                    ),                )
            changed = True
        if _write_resolver_dropin(owner_uid, owner_gid):
            dropin_changed = True
            changed = True
        cut_over = True
        if dropin_changed:
            run_command(list(values.RESTART_RESOLVED_COMMAND), timeout=timeout)
        if values.MANAGE_NETWORKMANAGER:
            auto_dns_changed = _disable_auto_dns_active(
                timeout, progress_priority
            )
        verify_error, verify_warning = _verify_system(discovered, timeout)
        if verify_error is not None:
            detail = (
                f"{verify_error}; the resolver drop-in and the dnsproxy "
                "service were kept, so the system stays on dnsproxy; "
                "check the systemd-resolved routing and rerun the task"
            )
            log_progress(
                f"dnsproxy setup failed: {detail}", priority=error_priority
            )
            # The drop-in and the service were reverted by the run that
            # produced this error, so the machine stays on the resolver it
            # had before; the reason is reported as a warning.
            return TaskResult(
                success=True,
                changed=True,
                message=f"dnsproxy setup failed: {detail}",
                warnings=(f"dnsproxy setup failed: {detail}",),
            )
    except (OSError, subprocess.SubprocessError, tarfile.TarError, RuntimeError) as exc:
        if cut_over:
            _revert(
                dropin_changed,
                auto_dns_changed,
                timeout,
                progress_priority,
                error_priority,
            )
        detail = f"dnsproxy setup failed: {exc}"
        return TaskResult(
            success=True,
            changed=changed,
            message=detail,
            warnings=(detail,),
        )
    return TaskResult(
        success=True,
        changed=changed,
        message=f"dnsproxy active with NextDNS profile {profile_id}",
        warnings=(verify_warning,) if verify_warning else (),
    )
