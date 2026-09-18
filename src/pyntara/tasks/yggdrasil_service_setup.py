"""Task yggdrasil_service_setup: install the newest yggdrasil release as a system service.

The task installs yggdrasil from the GitHub releases of the configured
repository, so the running version is always the newest release instead
of the distribution package. The latest release tag comes from the GitHub
releases API (https://api.github.com/repos/{repo}/releases/latest); the
deb asset yggdrasil-{version}-{arch}.deb is chosen by the dpkg
architecture, whose name matches the architecture part of the asset name.
The package is downloaded from the official GitHub release assets without
a checksum verification: the source is trusted, and the extra check would
add a failure point without protecting the install.

The task owns the configuration and the node identity. The package
postinst generates /etc/yggdrasil/yggdrasil.conf with a fresh key pair;
the task extracts the key once into a separate PEM file referenced by the
configured private key path field, so rewriting the configuration never
changes the node identity. The configuration is rendered as JSON from the
declared key names of CONFIG_DOCUMENT_KEYS: the TUN interface name and MTU, the admin socket,
the inbound listeners (tcp, tls, quic and ws on all stacks with random
ports) and the multicast discovery blocks.

The peer list comes from the official public-peers repository: the task
downloads the repository tarball, parses every markdown file into URI
strings, saves the full list next to the configuration for reference and
probes the peers in batches. Each batch is written into the
configuration, the service is restarted, and after the probe pause the
task reads the yggdrasil journal for Connected lines to find which peers
actually connected. When a batch reaches peer_target_count working peers,
the task keeps the target count with the lowest ping from the admin
socket and restarts the service with the final configuration. When no
batch reaches the target, the last tried batch stays in the
configuration and the task reports a warning. If the download fails, the
configured static_peers are used, and a run with neither is an error,
because a node without peers never joins the network.

The apt index is not refreshed, because the package depends only on
systemd. After the final restart the task saves the node self address
from the admin socket into the configured address file, the fallback of
the deployed address command when the live query fails. The save
retries the query with the geometric backoff while the configured retry
budget lasts, because the admin socket is not ready immediately after a
restart. The task marks the yggdrasil interface as unmanaged in
NetworkManager, so NetworkManager never assumes it as an external device
and no other task can persist an ephemeral profile for it. A crashed run
leaves the persistent TUN device behind with a saved NetworkManager
connection profile, so the task cleans the leftover interface up before
it starts the service, because otherwise yggdrasil panics on the already
assigned address; the netplan YAML that backs the profile is moved aside
as a .bak so it cannot regenerate the profile at the next boot. The task
is idempotent: it skips when the installed version equals the newest
release, the configuration exists with a non-empty peer list, the key
file exists, the saved address file exists and the service is enabled
and active; force mode reruns the whole peer selection.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
from pathlib import Path

from pyntara.context import Context
from pyntara.github_release import asset_name_urls, fetch_latest_release, release_tag
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    backoff_delay,
    download_command,
    dpkg_architecture,
    install_package_once,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    version_from_output,
)
from pyntara.values import engine as engine_values
from pyntara.values import yggdrasil_service_setup as values
from pyntara.yggdrasil import self_address_from_output

# The yggdrasil version string from yggdrasil -version, e.g. Build
# version: 0.5.14; the release tag carries a configured prefix, the asset
# and the version output do not.

# One peer URI inside a backtick line of the public-peers markdown files.
PEER_URI_PATTERN = re.compile(
    r"`((?:tcp|tls|quic|ws|wss|socks|sockstls|unix)://[^\s`]+)`"
)

# A Connected line of the yggdrasil journal:
# Connected outbound: <ygg-address>@<ip:port>, source <local-addr>.
# The ygg-address is the full peer IPv6 address with colons, e.g.
# 226:43e9:...:6ea6, and the remote part after the @ is the address of
# the peer we dialed.
CONNECTED_PATTERN = re.compile(
    r"Connected (?:outbound|inbound): [0-9a-f:]+@"
    r"(\[[0-9a-f:]+\]:\d+|[0-9.]+:\d+)"
)


def _select_asset(
    release: dict[str, object],
    version: str,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The asset name is a declared valueured template; the architecture
    part matches the dpkg architecture, so no codename-specific fallback
    is needed.
    """

    name = values.ASSET_NAME_TEMPLATE.format(version=version, arch=arch)
    url = dict(asset_name_urls(release)).get(name)
    return (name, url) if url else None


def _installed_version(timeout: float) -> str | None:
    """The installed yggdrasil version from the configured version call, or None.

    A missing binary, a nonzero exit or a hang means yggdrasil is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            list(values.INSTALLED_VERSION_COMMAND),
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
    retries: int,
) -> tuple[bool, str]:
    """Install the downloaded deb; return (success, error_text).

    The apt index is not refreshed: the package depends only on systemd,
    which is always installed, so a refresh would add a failure point
    without resolving anything. Each attempt uses the shared
    noninteractive apt environment; total attempts are one initial plus
    retries.
    """

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


def _render_config(peers: list[str]) -> str:
    """Render the yggdrasil configuration document with the given peers.

    The key lives in the separate PEM file, so the rendered document
    carries the configured private key path instead of the key material.
    The key names are declared values, so the schema of the document is
    visible there; keys are emitted in a fixed order, so the rendered
    file, the idempotency comparison and the written configuration share
    one representation.
    """

    keys = values.CONFIG_DOCUMENT_KEYS
    data = {
        keys["private_key_path"]: str(values.PRIVATE_KEY_PATH),
        keys["admin_listen"]: values.ADMIN_LISTEN,
        keys["if_name"]: values.IF_NAME,
        keys["if_mtu"]: values.IF_MTU,
        keys["listen"]: list(values.LISTEN),
        keys["multicast_interfaces"]: [
            {
                keys["multicast_regex"]: entry.regex,
                keys["multicast_beacon"]: entry.beacon,
                keys["multicast_listen"]: entry.listen,
            }
            for entry in values.MULTICAST_INTERFACES
        ],
        keys["peers"]: list(peers),
    }
    return json.dumps(data, indent=values.CONFIG_JSON_INDENT) + values.LINE_SEPARATOR


def _ensure_private_key(
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Extract or generate the node private key into the PEM file.

    When the key file exists, nothing happens: the identity is kept.
    Otherwise the key is extracted from the existing package-generated
    configuration, or generated from a fresh document when the
    configuration is absent. Raises RuntimeError when an export fails.
    """

    if values.PRIVATE_KEY_PATH.is_file():
        return
    if values.CONFIG_PATH.is_file():
        result = run_command(
            substituted_command(
                values.EXPORT_KEY_FROM_CONFIG_COMMAND,
                {"config_path": str(values.CONFIG_PATH)},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cannot export private key: exit {result.returncode}")
        key_text = result.stdout
    else:
        generated = run_command(
            list(values.GENERATE_CONFIG_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
        if generated.returncode != 0:
            raise RuntimeError(f"cannot generate config: exit {generated.returncode}")
        exported = run_command(
            list(values.EXPORT_KEY_FROM_STDIN_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
            input=generated.stdout,
        )
        if exported.returncode != 0:
            raise RuntimeError(f"cannot export private key: exit {exported.returncode}")
        key_text = exported.stdout
    values.PRIVATE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.PRIVATE_KEY_PATH.write_text(key_text, encoding="utf-8")
    os.chmod(values.PRIVATE_KEY_PATH, values.PRIVATE_KEY_FILE_MODE)
    apply_owner(values.PRIVATE_KEY_PATH, owner_uid, owner_gid)


def _write_config(
    peers: list[str],
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write the rendered configuration into the configured path."""

    values.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.CONFIG_PATH.write_text(_render_config(peers), encoding="utf-8")
    os.chmod(values.CONFIG_PATH, values.CONFIG_FILE_MODE)
    apply_owner(values.CONFIG_PATH, owner_uid, owner_gid)


def _config_has_peers() -> bool:
    """True when the current configuration exists with a non-empty Peers.

    A missing file or unparsable JSON is False, so the task reconfigures
    instead of skipping on a broken file.
    """

    try:
        data = json.loads(values.CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    peers = data.get(values.CONFIG_DOCUMENT_KEYS["peers"])
    return isinstance(peers, list) and len(peers) > 0


def _is_parseable_peer_uri(uri: str) -> bool:
    """True when the URI has a host and a port that urllib accepts.

    The public-peers markdown files contain configuration templates
    with placeholder hosts such as [proxyhost]:[proxyport] and
    [username]:[password]@[proxyhost]; yggdrasil crashes on such a peer
    at startup, so they are dropped at parse time.
    """

    try:
        parsed = urllib.parse.urlparse(uri)
    except ValueError:
        return False
    return bool(parsed.hostname and parsed.port)


def _parse_md_peers(text: str) -> list[str]:
    """The peer URIs inside a markdown file, deduplicated in order.

    Template peers with placeholder hosts are dropped, because yggdrasil
    aborts on them at startup and the whole node would never connect.
    """

    uris = PEER_URI_PATTERN.findall(text)
    return list(dict.fromkeys(uri for uri in uris if _is_parseable_peer_uri(uri)))


def _download_peers(
    timeout: float,
) -> list[str]:
    """Download and parse the public-peers list; save it next to the config.

    Downloads the repository tarball with the declared download command,
    extracts every markdown file and collects the backtick peer URIs. The
    full list is saved to peers_full_path for reference, while the
    configuration only ever carries the selected working peers. Raises
    RuntimeError when the download fails or yields no peers.
    """

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=values.PEERS_TARBALL_TEMP_PREFIX,
        suffix=values.PEERS_TARBALL_TEMP_SUFFIX,
    )
    os.close(tmp_fd)
    try:
        run_command(
            download_command(Path(tmp_name), values.PEERS_TARBALL_URL),
            timeout=timeout,
        )
        try:
            with tarfile.open(tmp_name, "r:gz") as archive:
                members = [
                    member
                    for member in archive.getmembers()
                    if member.isfile()
                    and member.name.endswith(values.PEER_MARKDOWN_SUFFIX)
                ]
                peers: list[str] = []
                for member in members:
                    file = archive.extractfile(member)
                    if file is None:
                        continue
                    text = file.read().decode("utf-8", errors="replace")
                    peers.extend(_parse_md_peers(text))
        except (tarfile.TarError, OSError) as exc:
            raise RuntimeError(f"cannot parse peers tarball: {exc}") from None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot download peers list: {exc}") from None
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass
    peers = list(dict.fromkeys(peers))
    if not peers:
        raise RuntimeError("peers list is empty")
    values.PEERS_FULL_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.PEERS_FULL_PATH.write_text(
        values.LINE_SEPARATOR.join(peers) + values.LINE_SEPARATOR, encoding="utf-8"
    )
    return peers


def _parse_ip_port(addr: str) -> tuple[str, int] | None:
    """Split an ip:port or [ipv6]:port string into (ip, port)."""

    if addr.startswith("["):
        host, _, rest = addr[1:].partition("]")
        try:
            return (host, int(rest.lstrip(":")))
        except ValueError:
            return None
    host, sep, port = addr.rpartition(":")
    if not sep:
        return None
    try:
        return (host, int(port))
    except ValueError:
        return None


def _resolve_uri_addrs(uri: str) -> list[tuple[str, int]]:
    """The (ip, port) pairs of a peer URI after DNS resolution.

    A malformed URI, a missing port or an unresolvable host yields an
    empty list, so such peers can never be marked as working. The whole
    parse is guarded, because urllib may raise ValueError on unusual
    hosts and socket may raise gaierror on unresolvable names.
    """

    try:
        parsed = urllib.parse.urlparse(uri)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return []
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except ValueError, socket.gaierror:
        return []
    result: list[tuple[str, int]] = []
    for info in infos:
        sockaddr = info[4]
        ip = str(sockaddr[0])
        port = int(sockaddr[1])
        if (ip, port) not in result:
            result.append((ip, port))
    return result


def _journal_connected_addrs(
    probe_seconds: float,
    timeout: float,
) -> set[tuple[str, int]]:
    """The (ip, port) pairs of Connected lines in the recent journal.

    Reads the configured journal query for the last probe window; a
    failed call yields an empty set, so the batch is simply treated as
    having connected nothing.
    """

    result = run_command(
        substituted_command(
            values.JOURNAL_CONNECTED_QUERY_COMMAND,
            {
                "service_unit_name": values.SERVICE_UNIT_NAME,
                "probe_seconds": str(int(probe_seconds)),
            },
        ),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return set()
    addrs: set[tuple[str, int]] = set()
    for match in CONNECTED_PATTERN.finditer(result.stdout):
        parsed = _parse_ip_port(match.group(1))
        if parsed is not None:
            addrs.add(parsed)
    return addrs


def _latencies_from_ctl(
    timeout: float,
) -> dict[tuple[str, int], float]:
    """The peer (ip, port) to latency map from the configured peers call.

    The admin socket reports the latency of each connected peer in
    nanoseconds; only entries whose remote host parses as an IP are kept,
    because a hostname cannot be matched against the journal addresses.
    A failed call yields an empty map, so the selection falls back to the
    batch order.
    """

    keys = values.ADMIN_OUTPUT_KEYS
    try:
        result = run_command(
            list(values.PEERS_LATENCY_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired, OSError:
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    entries = data.get(keys["peers"]) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    latencies: dict[tuple[str, int], float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        remote = entry.get(keys["remote"])
        if not isinstance(remote, str):
            continue
        ip_port = _resolve_uri_addrs(remote)
        latency = entry.get(keys["latency"])
        if not isinstance(latency, (int, float)) or not ip_port:
            continue
        for addr in ip_port:
            latencies[addr] = float(latency)
    return latencies


def _pick_best_peers(
    working: list[str],
    latencies: dict[tuple[str, int], float],
    target_count: int,
) -> list[str]:
    """The target_count working peers with the lowest ping.

    Peers are sorted by the minimum latency over their resolved
    addresses; peers without a latency keep the batch order at the end.
    """

    def key(uri: str) -> float:
        addrs = _resolve_uri_addrs(uri)
        values = [latencies[addr] for addr in addrs if addr in latencies]
        return min(values) if values else float("inf")

    return sorted(working, key=key)[:target_count]


def _ensure_interface_unmanaged(
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> bool:
    """Mark the yggdrasil interface as unmanaged in NetworkManager.

    NetworkManager assumes any external interface that appears with an
    address, and a connection modify by another task can persist that
    ephemeral assumed profile into a permanent auto-connect connection.
    The saved profile then recreates the interface with the node address
    before the service starts, so yggdrasil panics on the already
    assigned address. The drop-in marks the interface as unmanaged, so
    NetworkManager never assumes it. The write is idempotent: matching
    content leaves the file untouched and only a real change triggers a
    reload. True when the drop-in is in place; a machine without nmcli
    or an unwritable drop-in directory reports False and the caller
    keeps the warning.
    """

    body = values.NM_UNMANAGED_CONF_BODY.format(interface_name=values.IF_NAME)
    changed = False
    try:
        if (
            values.NM_UNMANAGED_CONF_PATH.is_file()
            and values.NM_UNMANAGED_CONF_PATH.read_text(encoding="utf-8") == body
        ):
            return True
        values.NM_UNMANAGED_CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
        values.NM_UNMANAGED_CONF_PATH.write_text(body, encoding="utf-8")
        os.chmod(values.NM_UNMANAGED_CONF_PATH, values.NM_UNMANAGED_CONF_FILE_MODE)
        apply_owner(values.NM_UNMANAGED_CONF_PATH, owner_uid, owner_gid)
        changed = True
        _log(f"marked interface {values.IF_NAME} as unmanaged in NetworkManager")
    except OSError as exc:
        _log(f"cannot write the NetworkManager unmanaged rule: {exc}")
        return False
    if changed:
        try:
            run_command(
                list(values.NMCLI_RELOAD_COMMAND),
                check=False,
                capture=True,
                timeout=timeout,
            )
        except OSError:
            pass
    return True


def _cleanup_leftover_interface(
    timeout: float,
) -> None:
    """Remove a stale yggdrasil interface before the service starts.

    A crashed run leaves the persistent TUN device behind with the node
    address still assigned, and a saved NetworkManager connection profile
    keeps the device alive and re-adds the address, so the next start
    panics with "failed to add address to link: file exists". The
    cleanup removes the interface only when no yggdrasil process owns
    it: the service is not active. The NetworkManager profile is deleted
    first, because it recreates the device otherwise, then the
    interface. A netplan YAML that backs the profile for the interface
    is moved aside as a .bak, because netplan only reads *.yaml and
    would otherwise regenerate the profile at the next boot. Every step
    is best-effort: a missing ip or nmcli, an absent profile, a missing
    netplan directory or a failed delete leaves the interface in place
    and the start reports its own error.
    """

    try:
        exists = (
            run_command(
                substituted_command(
                    values.IP_LINK_SHOW_COMMAND,
                    {"interface_name": values.IF_NAME},
                ),
                check=False,
                capture=True,
                timeout=timeout,
            ).returncode
            == 0
        )
    except OSError:
        exists = False
    if not exists:
        return
    if service_is_active(values.SERVICE_UNIT_NAME, timeout):
        return
    _log(f"leftover interface {values.IF_NAME} without a running service, cleaning up")
    try:
        profile_exists = (
            run_command(
                substituted_command(
                    values.NMCLI_CONNECTION_SHOW_COMMAND,
                    {"connection_name": values.IF_NAME},
                ),
                check=False,
                capture=True,
                timeout=timeout,
            ).returncode
            == 0
        )
        if profile_exists:
            run_command(
                substituted_command(
                    values.NMCLI_CONNECTION_DELETE_COMMAND,
                    {"connection_name": values.IF_NAME},
                ),
                check=False,
                capture=True,
                timeout=timeout,
            )
            _log(f"deleted NetworkManager connection {values.IF_NAME}")
    except OSError:
        pass
    try:
        run_command(
            substituted_command(
                values.IP_LINK_DELETE_COMMAND,
                {"interface_name": values.IF_NAME},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except OSError:
        pass
    try:
        if values.NETPLAN_DIR_PATH.is_dir():
            marker = values.NETPLAN_INTERFACE_MARKER.format(interface_name=values.IF_NAME)
            for candidate in values.NETPLAN_DIR_PATH.glob(f"*{values.NETPLAN_FILE_SUFFIX}"):
                try:
                    text = candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
                if marker not in text:
                    continue
                backup = candidate.with_name(candidate.name + values.NETPLAN_BACKUP_SUFFIX)
                candidate.replace(backup)
                _log(
                    f"moved netplan profile {candidate.name} for interface "
                    f"{values.IF_NAME} to {backup.name}"
                )
    except OSError:
        pass


def _restart_service(timeout: float) -> None:
    """Restart the service, or start it cleanly when it is not running.

    The start path cleans a stale leftover interface up first, so a
    crashed previous run never blocks the start with its stale address.
    """

    if service_is_active(values.SERVICE_UNIT_NAME, timeout):
        run_command(
            substituted_command(
                values.SERVICE_RESTART_COMMAND,
                {"service_unit_name": values.SERVICE_UNIT_NAME},
            ),
            timeout=timeout,
        )
        return
    _cleanup_leftover_interface(timeout)
    run_command(
        substituted_command(
            values.SERVICE_START_COMMAND,
            {"service_unit_name": values.SERVICE_UNIT_NAME},
        ),
        timeout=timeout,
    )


def _save_self_address(
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> bool:
    """Save the node self address into the configured file; True when saved.

    The saved file is the fallback of the deployed address command when
    the live admin socket query fails at collection time. The admin
    socket is not ready immediately after a restart, so the query is
    repeated with the geometric backoff while the total retry budget
    lasts: the first wait is address_save_retry_base_seconds, every
    further failure multiplies the pause by
    address_save_retry_multiplier until the budget
    address_save_retry_max_seconds is spent. The save stays best-effort:
    a budget that runs out leaves the file untouched and returns False,
    never failing the task, so the address file is not a failure point
    of the provisioning.
    """

    deadline = time.monotonic() + values.ADDRESS_SAVE_RETRY_MAX_SECONDS
    attempts = 0
    while True:
        attempts += 1
        reason: str = ""
        address: str | None = None
        try:
            result = run_command(
                list(values.SELF_ADDRESS_COMMAND),
                check=False,
                capture=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                reason = f"the self address query exited {result.returncode}"
            else:
                address = self_address_from_output(
                    result.stdout, values.ADMIN_OUTPUT_KEYS["address"]
                )
                if address is None:
                    reason = "the self address query reported no address"
        except subprocess.TimeoutExpired, OSError:
            reason = "the self address query is unavailable"
        if address is not None:
            values.ADDRESS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            values.ADDRESS_FILE_PATH.write_text(
                address + values.LINE_SEPARATOR, encoding="utf-8"
            )
            values.ADDRESS_FILE_PATH.chmod(values.ADDRESS_FILE_MODE)
            apply_owner(values.ADDRESS_FILE_PATH, owner_uid, owner_gid)
            _log(f"saving self address to {values.ADDRESS_FILE_PATH}: {address}")
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log(f"{reason}, address file not written")
            return False
        pause = min(
            backoff_delay(
                attempts,
                values.ADDRESS_SAVE_RETRY_BASE_SECONDS,
                values.ADDRESS_SAVE_RETRY_MULTIPLIER,
                values.ADDRESS_SAVE_RETRY_MAX_SECONDS,
            ),
            remaining,
        )
        _log(f"{reason}, retrying in {pause}s")
        time.sleep(pause)


def _wait_for_connections(timeout: float) -> int:
    """The live peer count, retried until the configured budget runs out.

    After the final restart the peers need a moment to re-establish
    their connections, so the getPeers query is repeated with the
    geometric backoff while the total retry budget
    connection_wait_max_seconds lasts, mirroring the address save retry.
    Returns the number of live connections at the end, 0 when none
    connected within the budget, so the caller can warn the user that
    the node is not reachable from the mesh instead of reporting a
    healthy node.
    """

    deadline = time.monotonic() + values.CONNECTION_WAIT_MAX_SECONDS
    attempts = 0
    while True:
        attempts += 1
        live = _latencies_from_ctl(timeout)
        if live:
            return len(live)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0
        pause = min(
            backoff_delay(
                attempts,
                values.CONNECTION_WAIT_BASE_SECONDS,
                values.CONNECTION_WAIT_MULTIPLIER,
                values.CONNECTION_WAIT_MAX_SECONDS,
            ),
            remaining,
        )
        _log(f"no live connections yet, retrying in {pause}s")
        time.sleep(pause)


def task(ctx: Context) -> TaskResult:
    """Install the newest yggdrasil release, configure it and pick working peers.

    The goal is reached when the installed version equals the newest
    release, the configuration exists with a non-empty peer list, the key
    file exists, the saved self address file exists and the service is
    enabled and active; the task then returns changed=False. Otherwise it
    downloads and installs the matching .deb asset, extracts the node key,
    downloads the public peer list, probes it in batches and keeps the
    target number of working peers with the lowest ping, then saves the
    node self address from the admin socket into the configured file as
    the fallback of the deployed address command. Every step is reported
    to stdout: measurements and decisions as single lines that include
    their result, long-running commands as a line before and a line after.
    A step that cannot be performed is reported as a warning and the task
    still completes, because a recoverable failure must never stop the
    provisioning; the entry point counts the warnings and exits nonzero.
    """

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    force = ctx.task_name in ctx.force_tasks
    warnings: list[str] = []

    def done(message: str, changed: bool) -> TaskResult:
        """A completed result carrying the collected warnings."""

        return TaskResult(
            success=True,
            changed=changed,
            message=message,
            warnings=tuple(warnings),
        )

    try:
        arch = dpkg_architecture(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"cannot determine dpkg architecture: {exc}")
        return done("yggdrasil not configured", False)
    _log(f"reading dpkg architecture: {arch}")

    try:
        release = fetch_latest_release(values.GITHUB_REPO)
        tag = release_tag(release)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return done("yggdrasil not configured", False)
    version = tag.removeprefix(values.RELEASE_TAG_PREFIX)
    _log(f"checking latest release: {version}")

    selected = _select_asset(release, version, arch)
    if selected is None:
        warnings.append(
            f"release {version} has no yggdrasil-{version}-{arch}.deb asset"
        )
        return done("yggdrasil not configured", False)
    asset_name, asset_url = selected
    _log(f"selected asset: {asset_name}")

    installed_version = _installed_version(timeout)
    _log(f"checking installed version: {installed_version or 'not installed'}")

    enabled = service_is_enabled(values.SERVICE_UNIT_NAME, timeout)
    active = service_is_active(values.SERVICE_UNIT_NAME, timeout)
    _log(
        f"checking autorun service {values.SERVICE_UNIT_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = installed_version != version
    key_exists = values.PRIVATE_KEY_PATH.is_file()
    config_ready = _config_has_peers()
    address_file_exists = values.ADDRESS_FILE_PATH.is_file()
    _log(
        f"checking saved address file {values.ADDRESS_FILE_PATH}: "
        f"{'present' if address_file_exists else 'missing'}"
    )
    # The node is healthy only when the service is active and the admin
    # socket reports at least one connected peer. A config with peers is
    # not enough: the peers may have gone stale, so the task must not
    # treat a dead node as already configured.
    has_connections = bool(_latencies_from_ctl(timeout))
    _log(f"checking live connections: {'present' if has_connections else 'none'}")
    if (
        not force
        and not needs_install
        and enabled
        and active
        and key_exists
        and config_ready
        and address_file_exists
        and has_connections
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    if needs_install:
        _log(f"downloading {asset_name} into {values.DOWNLOAD_DIR}")
        try:
            _download_asset(
                values.DOWNLOAD_DIR,
                asset_name,
                asset_url,
                timeout,
            )
        except RuntimeError as exc:
            warnings.append(str(exc))
            return done("yggdrasil not configured", changed)
        _log("package downloaded")
        _log(f"installing package: apt-get install -y {asset_name}")
        ok, error = _install_deb(
            values.DOWNLOAD_DIR,
            asset_name,
            install_timeout=timeout,
            retries=values.INSTALL_RETRIES,
        )
        if not ok:
            warnings.append(f"cannot install yggdrasil: {error}")
            return done("yggdrasil not configured", changed)
        _log("package installed")
        try:
            _cleanup_downloads(values.DOWNLOAD_DIR, asset_name)
        except OSError as exc:
            warnings.append(f"cannot remove downloaded files: {exc}")
            return done("yggdrasil not configured", True)
        changed = True

    _log(f"ensuring private key at {values.PRIVATE_KEY_PATH}")
    try:
        _ensure_private_key(timeout, owner_uid, owner_gid)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return done("yggdrasil not configured", changed)
    if values.PRIVATE_KEY_PATH.is_file():
        _log(f"private key ready: {values.PRIVATE_KEY_PATH}")
    else:
        warnings.append(f"private key not created at {values.PRIVATE_KEY_PATH}")
        return done("yggdrasil not configured", changed)

    if not enabled:
        _log(f"enabling service: systemctl enable {values.SERVICE_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SERVICE_ENABLE_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl enable failed: {exc}")
            return done("yggdrasil not configured", changed)
        _log("service enabled")
        changed = True

    if not _ensure_interface_unmanaged(timeout, owner_uid, owner_gid):
        warnings.append(
            f"cannot mark interface {values.IF_NAME} as unmanaged in "
            "NetworkManager; a later run may panic on an assumed "
            "interface"
        )

    # Outside force mode, when the configuration already carries peers
    # and the saved address file exists, the task never re-selects peers,
    # rewrites the configuration or touches the address file: it only
    # brings the service up with the existing config and waits for
    # connections. Re-selecting peers is reserved for force mode and for
    # a first run where no peer config exists yet.
    if (
        not force
        and not needs_install
        and key_exists
        and config_ready
        and address_file_exists
    ):
        if not active:
            _log(
                f"starting service {values.SERVICE_UNIT_NAME} with the "
                "existing configuration"
            )
            _cleanup_leftover_interface(timeout)
            try:
                run_command(
                    substituted_command(
                        values.SERVICE_START_COMMAND,
                        {"service_unit_name": values.SERVICE_UNIT_NAME},
                    ),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"systemctl start failed: {exc}")
                return done("yggdrasil node not running", True)
            changed = True
        _log(f"waiting {values.PEER_PROBE_TIMEOUT_SECONDS}s for connections")
        time.sleep(values.PEER_PROBE_TIMEOUT_SECONDS)
        if not service_is_active(values.SERVICE_UNIT_NAME, timeout):
            warnings.append(
                f"service {values.SERVICE_UNIT_NAME} did not become active after start"
            )
            return done("yggdrasil node not running", changed)
        if not _latencies_from_ctl(timeout):
            warnings.append(
                f"service {values.SERVICE_UNIT_NAME} is active but has no "
                "connections; rerun in force mode to re-select peers"
            )
            return done("yggdrasil running without live connections", changed)
        _log("service active with live connections")
        return TaskResult(
            success=True,
            changed=True,
            message=(
                f"yggdrasil {version} running with live connections "
                "from the existing configuration"
            ),
        )

    _log(f"downloading peer list from {values.PEERS_TARBALL_URL}")
    downloaded: list[str] | None = None
    try:
        downloaded = _download_peers(
                    timeout,
        )
    except RuntimeError as exc:
        _log(f"peer list download failed, using static_peers: {exc}")
    if downloaded is None:
        if not values.STATIC_PEERS:
            warnings.append(
                "cannot download the peer list and static_peers is empty; "
                "a yggdrasil node without peers never joins the network"
            )
            return done("yggdrasil not configured", changed)
        peers = list(values.STATIC_PEERS)
        _log(f"using {len(peers)} static peers")
    else:
        peers = downloaded
        _log(f"peer list downloaded: {len(peers)} peers")
        _log(f"saving full peer list to {values.PEERS_FULL_PATH}")
        random.shuffle(peers)
        _log("peer list shuffled")

    def run_final_config(selected: list[str]) -> TaskResult:
        _log(f"writing configuration {values.CONFIG_PATH} with {len(selected)} peers")
        try:
            _write_config(selected, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot write configuration: {exc}")
            return done("yggdrasil not configured", True)
        _log("configuration written")
        try:
            _restart_service(timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl restart failed: {exc}")
            return done("yggdrasil not configured", True)
        if not service_is_active(values.SERVICE_UNIT_NAME, timeout):
            warnings.append(
                f"service {values.SERVICE_UNIT_NAME} did not become active after restart"
            )
            return done("yggdrasil node not running", True)
        _log("service active")
        live = _wait_for_connections(timeout)
        if not live:
            warnings.append(
                f"service {values.SERVICE_UNIT_NAME} is active but has no "
                "live connections; rerun in force mode to re-select peers"
            )
        else:
            _log(f"service active with {live} live connections")
        _save_self_address(timeout, owner_uid, owner_gid)
        return done(
            f"yggdrasil {version} installed, {len(selected)} peers "
            f"configured, service {values.SERVICE_UNIT_NAME} active",
            True,
        )

    if downloaded is None:
        return run_final_config(peers)

    batch_size = values.PEER_BATCH_SIZE
    total_batches = (len(peers) + batch_size - 1) // batch_size
    if values.PEER_MAX_BATCHES > 0:
        total_batches = min(total_batches, values.PEER_MAX_BATCHES)
    _log(
        f"probing peers in batches of {batch_size}, "
        f"{total_batches} batch(es), target {values.PEER_TARGET_COUNT} working"
    )

    last_batch: list[str] = []
    for batch_index in range(total_batches):
        batch = peers[batch_index * batch_size : (batch_index + 1) * batch_size]
        if not batch:
            break
        last_batch = batch
        _log(f"batch {batch_index + 1}/{total_batches}: probing {len(batch)} peers")
        _log(f"writing configuration {values.CONFIG_PATH} with probe batch")
        try:
            _write_config(batch, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot write configuration: {exc}")
            return done("yggdrasil not configured", True)
        _log("configuration written")
        try:
            _restart_service(timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl restart failed: {exc}")
            return done("yggdrasil not configured", True)
        _log(f"waiting {values.PEER_PROBE_TIMEOUT_SECONDS}s for connections")
        time.sleep(values.PEER_PROBE_TIMEOUT_SECONDS)
        connected = _journal_connected_addrs(
            values.PEER_PROBE_TIMEOUT_SECONDS,
            timeout,
        )
        _log(f"journal shows {len(connected)} connected peer address(es)")
        working: list[str] = []
        for uri in batch:
            addrs = _resolve_uri_addrs(uri)
            if any(addr in connected for addr in addrs):
                working.append(uri)
        _log(f"batch {batch_index + 1}: {len(working)} of {len(batch)} peers connected")
        if len(working) >= values.PEER_TARGET_COUNT:
            latencies = _latencies_from_ctl(timeout)
            _log(f"read latencies for {len(latencies)} peer address(es)")
            best_peers = _pick_best_peers(working, latencies, values.PEER_TARGET_COUNT)
            _log(
                f"batch {batch_index + 1}: keeping {len(best_peers)} peers "
                "with the lowest ping"
            )
            return run_final_config(best_peers)
        _log(
            f"batch {batch_index + 1} reached only {len(working)} working "
            f"peers, trying the next batch"
        )

    _log(
        "no batch reached the target; keeping the last tried batch "
        f"({len(last_batch)} peers) in the configuration"
    )
    if not service_is_active(values.SERVICE_UNIT_NAME, timeout):
        warnings.append(
            f"service {values.SERVICE_UNIT_NAME} did not become active after restart"
        )
        return done("yggdrasil node not running", True)
    live = _wait_for_connections(timeout)
    if not live:
        warnings.append(
            f"service {values.SERVICE_UNIT_NAME} is active but has no "
            "live connections; rerun in force mode to re-select peers"
        )
    else:
        _log(f"service active with {live} live connections")
    _save_self_address(timeout, owner_uid, owner_gid)
    return done(
        f"yggdrasil {version} installed, no batch reached "
        f"{values.PEER_TARGET_COUNT} working peers, keeping the last "
        f"batch of {len(last_batch)} peers",
        True,
    )
