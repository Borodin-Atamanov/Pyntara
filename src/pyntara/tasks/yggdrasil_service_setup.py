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
the task extracts the key once into a separate PEM file referenced by
PrivateKeyPath, so rewriting the configuration never changes the node
identity. The configuration is rendered as JSON from config.toml: the
TUN interface name and MTU, the admin socket, the inbound listeners
(tcp, tls, quic and ws on all stacks with random ports) and the multicast
discovery blocks.

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
restart. A crashed run leaves the persistent TUN device behind with a
saved NetworkManager connection profile, so the task cleans the leftover
interface up before it starts the service, because otherwise yggdrasil
panics on the already assigned address. The task is idempotent: it skips
when the installed version equals the newest release, the configuration
exists with a non-empty peer list, the key file exists, the saved
address file exists and the service is enabled and active; force mode
reruns the whole peer selection.
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

from pyntara.config import YggdrasilServiceSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    backoff_delay,
    curl_flags,
    dpkg_architecture,
    ensure_root_owner,
    install_package_once,
    run_command,
    service_is_active,
    service_is_enabled,
)
from pyntara.yggdrasil import self_address_from_output

# The yggdrasil version string from yggdrasil -version, e.g. Build
# version: 0.5.14; the release tag carries a leading v, the asset and the
# version output do not.
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")

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


def _release_tag(release: dict[str, object]) -> str:
    """The tag_name of a release payload; raises RuntimeError when absent."""

    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("release payload has no tag_name")
    return tag


def _asset_name_urls(release: dict[str, object]) -> dict[str, str]:
    """The name to download_url mapping of the release assets.

    Malformed asset entries are skipped; a name collision keeps the first
    entry, because the list is ordered as returned by the API.
    """

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TypeError("release payload has no assets array")
    result: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result.setdefault(name, url)
    return result


def _select_asset(
    release: dict[str, object],
    version: str,
    arch: str,
) -> tuple[str, str] | None:
    """The (name, url) of the .deb asset for this machine, or None.

    The asset name is yggdrasil-{version}-{arch}.deb; the architecture
    part matches the dpkg architecture, so no codename-specific fallback
    is needed.
    """

    name = f"yggdrasil-{version}-{arch}.deb"
    url = _asset_name_urls(release).get(name)
    return (name, url) if url else None


def _fetch_release_json(
    repo: str, timeout: float, curl_timeout: float, retries: int
) -> dict[str, object]:
    """The latest release payload from the GitHub releases API.

    Raises RuntimeError when the request fails or the payload is not
    usable JSON, so the caller reports the reason instead of a raw
    exception.
    """

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    result = run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            *curl_flags(curl_timeout, retries),
            url,
        ],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch {url}: exit {result.returncode}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cannot parse release JSON from {url}: {exc}") from None
    if not isinstance(data, dict):
        raise TypeError(f"unexpected release payload from {url}")
    return data


def _installed_version(timeout: float) -> str | None:
    """The installed yggdrasil version from yggdrasil -version, or None.

    A missing binary, a nonzero exit or a hang means yggdrasil is not
    installed: the task treats the version as absent and reinstalls it.
    The missing executable raises FileNotFoundError (an OSError), which
    subprocess raises regardless of check; the version triple is searched
    in stdout and stderr, because the exact output format may change.
    """

    try:
        result = run_command(
            ["yggdrasil", "-version"],
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
    download_dir: Path,
    name: str,
    url: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
) -> None:
    """Download the package into the download directory.

    Raises RuntimeError when curl fails, so the caller reports the
    reason.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_command(
            [
                "curl",
                "--fail",
                "--silent",
                "--location",
                "--show-error",
                "--output",
                str(download_dir / name),
                *curl_flags(curl_timeout, retries),
                url,
            ],
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
        ok, error = install_package_once(
            str(download_dir / name), install_timeout
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


def _render_config(
    cfg: YggdrasilServiceSetupConfig, peers: list[str]
) -> str:
    """Render the yggdrasil configuration JSON with the given peers.

    The key lives in the separate PEM file, so the rendered document
    carries PrivateKeyPath instead of the key material. Keys are emitted
    in a fixed order, so the rendered file, the idempotency comparison
    and the written configuration share one representation.
    """

    data = {
        "PrivateKeyPath": str(cfg.private_key_path),
        "AdminListen": cfg.admin_listen,
        "IfName": cfg.if_name,
        "IfMTU": cfg.if_mtu,
        "Listen": list(cfg.listen),
        "MulticastInterfaces": [
            {"Regex": entry.regex, "Beacon": entry.beacon, "Listen": entry.listen}
            for entry in cfg.multicast_interfaces
        ],
        "Peers": list(peers),
    }
    return json.dumps(data, indent=2) + "\n"


def _ensure_private_key(cfg: YggdrasilServiceSetupConfig, timeout: float) -> None:
    """Extract or generate the node private key into the PEM file.

    When the key file exists, nothing happens: the identity is kept.
    Otherwise the key is extracted from the existing package-generated
    configuration with yggdrasil -useconffile -exportkey, or generated
    through -genconf piped into -useconf -exportkey when the
    configuration is absent. Raises RuntimeError when the export fails.
    """

    if cfg.private_key_path.is_file():
        return
    if cfg.config_path.is_file():
        result = run_command(
            [
                "yggdrasil",
                "-useconffile",
                str(cfg.config_path),
                "-exportkey",
            ],
            check=False,
            capture=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cannot export private key: exit {result.returncode}")
        key_text = result.stdout
    else:
        generated = run_command(
            ["yggdrasil", "-genconf", "-json"],
            check=False,
            capture=True,
            timeout=timeout,
        )
        if generated.returncode != 0:
            raise RuntimeError(
                f"cannot generate config: exit {generated.returncode}"
            )
        exported = run_command(
            ["yggdrasil", "-useconf", "-exportkey"],
            check=False,
            capture=True,
            timeout=timeout,
            input=generated.stdout,
        )
        if exported.returncode != 0:
            raise RuntimeError(
                f"cannot export private key: exit {exported.returncode}"
            )
        key_text = exported.stdout
    cfg.private_key_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.private_key_path.write_text(key_text, encoding="utf-8")
    os.chmod(cfg.private_key_path, cfg.private_key_file_mode)
    ensure_root_owner(cfg.private_key_path)


def _write_config(cfg: YggdrasilServiceSetupConfig, peers: list[str]) -> None:
    """Write the rendered configuration into the configured path."""

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(_render_config(cfg, peers), encoding="utf-8")
    os.chmod(cfg.config_path, cfg.config_file_mode)
    ensure_root_owner(cfg.config_path)


def _config_has_peers(cfg: YggdrasilServiceSetupConfig) -> bool:
    """True when the current configuration exists with a non-empty Peers.

    A missing file or unparsable JSON is False, so the task reconfigures
    instead of skipping on a broken file.
    """

    try:
        data = json.loads(cfg.config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    peers = data.get("Peers")
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
    return list(
        dict.fromkeys(uri for uri in uris if _is_parseable_peer_uri(uri))
    )


def _download_peers(
    cfg: YggdrasilServiceSetupConfig,
    timeout: float,
    curl_timeout: float,
    retries: int,
) -> list[str]:
    """Download and parse the public-peers list; save it next to the config.

    Downloads the repository tarball with curl, extracts every markdown
    file and collects the backtick peer URIs. The full list is saved to
    peers_full_path for reference, while the configuration only ever
    carries the selected working peers. Raises RuntimeError when the
    download fails or yields no peers.
    """

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="yggdrasil-peers-", suffix=".tar.gz")
    os.close(tmp_fd)
    try:
        run_command(
            [
                "curl",
                "--fail",
                "--silent",
                "--location",
                "--show-error",
                "--output",
                tmp_name,
                *curl_flags(curl_timeout, retries),
                cfg.peers_tarball_url,
            ],
            timeout=timeout,
        )
        try:
            with tarfile.open(tmp_name, "r:gz") as archive:
                members = [
                    member
                    for member in archive.getmembers()
                    if member.isfile() and member.name.endswith(".md")
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
    cfg.peers_full_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.peers_full_path.write_text("\n".join(peers) + "\n", encoding="utf-8")
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
    except (ValueError, socket.gaierror):
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
    service_unit_name: str, probe_seconds: float, timeout: float
) -> set[tuple[str, int]]:
    """The (ip, port) pairs of Connected lines in the recent journal.

    Reads the yggdrasil journal for the last probe window; a failed
    journalctl call yields an empty set, so the batch is simply treated
    as having connected nothing.
    """

    result = run_command(
        [
            "journalctl",
            "-u",
            service_unit_name,
            "--since",
            f"-{int(probe_seconds)}s",
            "--no-pager",
            "--output=short-iso",
        ],
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


def _latencies_from_ctl(timeout: float) -> dict[tuple[str, int], float]:
    """The peer (ip, port) to latency map from yggdrasilctl getPeers.

    The admin socket reports the latency of each connected peer in
    nanoseconds; only entries whose remote host parses as an IP are kept,
    because a hostname cannot be matched against the journal addresses.
    A failed call yields an empty map, so the selection falls back to the
    batch order.
    """

    try:
        result = run_command(
            ["yggdrasilctl", "-json", "getPeers"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    entries = data.get("peers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    latencies: dict[tuple[str, int], float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        remote = entry.get("remote")
        if not isinstance(remote, str):
            continue
        ip_port = _resolve_uri_addrs(remote)
        latency = entry.get("latency")
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


def _cleanup_leftover_interface(
    cfg: YggdrasilServiceSetupConfig, timeout: float
) -> None:
    """Remove a stale yggdrasil interface before the service starts.

    A crashed run leaves the persistent TUN device behind with the node
    address still assigned, and a saved NetworkManager connection profile
    keeps the device alive and re-adds the address, so the next start
    panics with "failed to add address to link: file exists". The
    cleanup removes the interface only when no yggdrasil process owns
    it: the service is not active. The NetworkManager profile is deleted
    first, because it recreates the device otherwise, then the
    interface. Both steps are best-effort: a missing ip or nmcli, an
    absent profile or a failed delete leaves the interface in place and
    the start reports its own error.
    """

    try:
        exists = (
            run_command(
                ["ip", "link", "show", "dev", cfg.if_name],
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
    if service_is_active(cfg.service_unit_name, timeout):
        return
    _log(
        f"leftover interface {cfg.if_name} without a running service, "
        "cleaning up"
    )
    try:
        profile_exists = (
            run_command(
                ["nmcli", "connection", "show", cfg.if_name],
                check=False,
                capture=True,
                timeout=timeout,
            ).returncode
            == 0
        )
        if profile_exists:
            run_command(
                ["nmcli", "connection", "delete", cfg.if_name],
                check=False,
                capture=True,
                timeout=timeout,
            )
            _log(f"deleted NetworkManager connection {cfg.if_name}")
    except OSError:
        pass
    try:
        run_command(
            ["ip", "link", "del", cfg.if_name],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except OSError:
        pass


def _restart_service(cfg: YggdrasilServiceSetupConfig, timeout: float) -> None:
    """Restart the service, or start it cleanly when it is not running.

    The start path cleans a stale leftover interface up first, so a
    crashed previous run never blocks the start with its stale address.
    """

    if service_is_active(cfg.service_unit_name, timeout):
        run_command(
            ["systemctl", "restart", cfg.service_unit_name], timeout=timeout
        )
        return
    _cleanup_leftover_interface(cfg, timeout)
    run_command(["systemctl", "start", cfg.service_unit_name], timeout=timeout)


def _save_self_address(
    cfg: YggdrasilServiceSetupConfig, timeout: float
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

    deadline = time.monotonic() + cfg.address_save_retry_max_seconds
    attempts = 0
    while True:
        attempts += 1
        reason: str = ""
        address: str | None = None
        try:
            result = run_command(
                ["yggdrasilctl", "-json", "getSelf"],
                check=False,
                capture=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                reason = f"yggdrasilctl getSelf exited {result.returncode}"
            else:
                address = self_address_from_output(result.stdout)
                if address is None:
                    reason = "yggdrasilctl getSelf output has no self address"
        except (subprocess.TimeoutExpired, OSError):
            reason = "yggdrasilctl getSelf unavailable"
        if address is not None:
            cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.address_file_path.write_text(f"{address}\n", encoding="utf-8")
            cfg.address_file_path.chmod(cfg.address_file_mode)
            ensure_root_owner(cfg.address_file_path)
            _log(f"saving self address to {cfg.address_file_path}: {address}")
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log(f"{reason}, address file not written")
            return False
        pause = min(
            backoff_delay(
                attempts,
                cfg.address_save_retry_base_seconds,
                cfg.address_save_retry_multiplier,
                cfg.address_save_retry_max_seconds,
            ),
            remaining,
        )
        _log(f"{reason}, retrying in {pause}s")
        time.sleep(pause)


def _wait_for_connections(
    cfg: YggdrasilServiceSetupConfig, timeout: float
) -> int:
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

    deadline = time.monotonic() + cfg.connection_wait_max_seconds
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
                cfg.connection_wait_base_seconds,
                cfg.connection_wait_multiplier,
                cfg.connection_wait_max_seconds,
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

    cfg = ctx.config.yggdrasil_service_setup
    timeout = ctx.config.engine.command_timeout_seconds
    curl_timeout = ctx.config.engine.curl_timeout_seconds
    curl_retries = ctx.config.engine.curl_retries
    force = "yggdrasil_service_setup" in ctx.force_tasks
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
        release = _fetch_release_json(
            cfg.github_repo, timeout, curl_timeout, curl_retries
        )
        tag = _release_tag(release)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return done("yggdrasil not configured", False)
    version = tag.removeprefix("v")
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

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    needs_install = installed_version != version
    key_exists = cfg.private_key_path.is_file()
    config_ready = _config_has_peers(cfg)
    address_file_exists = cfg.address_file_path.is_file()
    _log(
        f"checking saved address file {cfg.address_file_path}: "
        f"{'present' if address_file_exists else 'missing'}"
    )
    # The node is healthy only when the service is active and the admin
    # socket reports at least one connected peer. A config with peers is
    # not enough: the peers may have gone stale, so the task must not
    # treat a dead node as already configured.
    has_connections = bool(_latencies_from_ctl(timeout))
    _log(
        "checking live connections: "
        f"{'present' if has_connections else 'none'}"
    )
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
        _log(f"downloading {asset_name} into {cfg.download_dir}")
        try:
            _download_asset(
                cfg.download_dir,
                asset_name,
                asset_url,
                timeout,
                curl_timeout,
                curl_retries,
            )
        except RuntimeError as exc:
            warnings.append(str(exc))
            return done("yggdrasil not configured", changed)
        _log("package downloaded")
        _log(f"installing package: apt-get install -y {asset_name}")
        ok, error = _install_deb(
            cfg.download_dir,
            asset_name,
            install_timeout=timeout,
            retries=cfg.install_retries,
        )
        if not ok:
            warnings.append(f"cannot install yggdrasil: {error}")
            return done("yggdrasil not configured", changed)
        _log("package installed")
        try:
            _cleanup_downloads(cfg.download_dir, asset_name)
        except OSError as exc:
            warnings.append(f"cannot remove downloaded files: {exc}")
            return done("yggdrasil not configured", True)
        changed = True

    _log(f"ensuring private key at {cfg.private_key_path}")
    try:
        _ensure_private_key(cfg, timeout)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return done("yggdrasil not configured", changed)
    if cfg.private_key_path.is_file():
        _log(f"private key ready: {cfg.private_key_path}")
    else:
        warnings.append(f"private key not created at {cfg.private_key_path}")
        return done("yggdrasil not configured", changed)

    if not enabled:
        _log(f"enabling service: systemctl enable {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "enable", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl enable failed: {exc}")
            return done("yggdrasil not configured", changed)
        _log("service enabled")
        changed = True

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
                f"starting service {cfg.service_unit_name} with the "
                "existing configuration"
            )
            _cleanup_leftover_interface(cfg, timeout)
            try:
                run_command(
                    ["systemctl", "start", cfg.service_unit_name], timeout=timeout
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"systemctl start failed: {exc}")
                return done("yggdrasil node not running", True)
            changed = True
        _log(
            f"waiting {cfg.peer_probe_timeout_seconds}s for connections"
        )
        time.sleep(cfg.peer_probe_timeout_seconds)
        if not service_is_active(cfg.service_unit_name, timeout):
            warnings.append(
                f"service {cfg.service_unit_name} did not become active "
                "after start"
            )
            return done("yggdrasil node not running", changed)
        if not _latencies_from_ctl(timeout):
            warnings.append(
                f"service {cfg.service_unit_name} is active but has no "
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

    _log(f"downloading peer list from {cfg.peers_tarball_url}")
    downloaded: list[str] | None = None
    try:
        downloaded = _download_peers(cfg, timeout, curl_timeout, curl_retries)
    except RuntimeError as exc:
        _log(f"peer list download failed, using static_peers: {exc}")
    if downloaded is None:
        if not cfg.static_peers:
            warnings.append(
                "cannot download the peer list and static_peers is empty; "
                "a yggdrasil node without peers never joins the network"
            )
            return done("yggdrasil not configured", changed)
        peers = list(cfg.static_peers)
        _log(f"using {len(peers)} static peers")
    else:
        peers = downloaded
        _log(f"peer list downloaded: {len(peers)} peers")
        _log(f"saving full peer list to {cfg.peers_full_path}")
        random.shuffle(peers)
        _log("peer list shuffled")

    def run_final_config(selected: list[str]) -> TaskResult:
        _log(f"writing configuration {cfg.config_path} with {len(selected)} peers")
        try:
            _write_config(cfg, selected)
        except OSError as exc:
            warnings.append(f"cannot write configuration: {exc}")
            return done("yggdrasil not configured", True)
        _log("configuration written")
        try:
            _restart_service(cfg, timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl restart failed: {exc}")
            return done("yggdrasil not configured", True)
        if not service_is_active(cfg.service_unit_name, timeout):
            warnings.append(
                f"service {cfg.service_unit_name} did not become active "
                "after restart"
            )
            return done("yggdrasil node not running", True)
        _log("service active")
        live = _wait_for_connections(cfg, timeout)
        if not live:
            warnings.append(
                f"service {cfg.service_unit_name} is active but has no "
                "live connections; rerun in force mode to re-select peers"
            )
        else:
            _log(f"service active with {live} live connections")
        _save_self_address(cfg, timeout)
        return done(
            f"yggdrasil {version} installed, {len(selected)} peers "
            f"configured, service {cfg.service_unit_name} active",
            True,
        )

    if downloaded is None:
        return run_final_config(peers)

    batch_size = cfg.peer_batch_size
    total_batches = (len(peers) + batch_size - 1) // batch_size
    if cfg.peer_max_batches > 0:
        total_batches = min(total_batches, cfg.peer_max_batches)
    _log(
        f"probing peers in batches of {batch_size}, "
        f"{total_batches} batch(es), target {cfg.peer_target_count} working"
    )

    last_batch: list[str] = []
    for batch_index in range(total_batches):
        batch = peers[
            batch_index * batch_size : (batch_index + 1) * batch_size
        ]
        if not batch:
            break
        last_batch = batch
        _log(
            f"batch {batch_index + 1}/{total_batches}: probing "
            f"{len(batch)} peers"
        )
        _log(f"writing configuration {cfg.config_path} with probe batch")
        try:
            _write_config(cfg, batch)
        except OSError as exc:
            warnings.append(f"cannot write configuration: {exc}")
            return done("yggdrasil not configured", True)
        _log("configuration written")
        try:
            _restart_service(cfg, timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl restart failed: {exc}")
            return done("yggdrasil not configured", True)
        _log(
            f"waiting {cfg.peer_probe_timeout_seconds}s for connections"
        )
        time.sleep(cfg.peer_probe_timeout_seconds)
        connected = _journal_connected_addrs(
            cfg.service_unit_name, cfg.peer_probe_timeout_seconds, timeout
        )
        _log(f"journal shows {len(connected)} connected peer address(es)")
        working: list[str] = []
        for uri in batch:
            addrs = _resolve_uri_addrs(uri)
            if any(addr in connected for addr in addrs):
                working.append(uri)
        _log(
            f"batch {batch_index + 1}: {len(working)} of {len(batch)} "
            f"peers connected"
        )
        if len(working) >= cfg.peer_target_count:
            latencies = _latencies_from_ctl(timeout)
            _log(f"read latencies for {len(latencies)} peer address(es)")
            best_peers = _pick_best_peers(
                working, latencies, cfg.peer_target_count
            )
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
    if not service_is_active(cfg.service_unit_name, timeout):
        warnings.append(
            f"service {cfg.service_unit_name} did not become active "
            "after restart"
        )
        return done("yggdrasil node not running", True)
    live = _wait_for_connections(cfg, timeout)
    if not live:
        warnings.append(
            f"service {cfg.service_unit_name} is active but has no "
            "live connections; rerun in force mode to re-select peers"
        )
    else:
        _log(f"service active with {live} live connections")
    _save_self_address(cfg, timeout)
    return done(
        f"yggdrasil {version} installed, no batch reached "
        f"{cfg.peer_target_count} working peers, keeping the last "
        f"batch of {len(last_batch)} peers",
        True,
    )
