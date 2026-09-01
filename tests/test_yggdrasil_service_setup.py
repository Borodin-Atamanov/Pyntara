"""Unit tests for the yggdrasil_service_setup task.

All external resources (subprocess, DNS, filesystem paths) are mocked via
monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tarfile
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.context import Context
from pyntara.tasks import yggdrasil_service_setup
from pyntara.utils import curl_flags

# The newest release tag and the version without the leading v; the asset
# and the version output use the bare version.
TAG = "v0.5.14"
VERSION = "0.5.14"
DEB_CONTENT = b"fake yggdrasil binary\n"
KEY_PEM = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"


def _release_json(*, tag: str = TAG) -> str:
    """The GitHub releases API payload used by the curl fake."""

    version = tag.removeprefix("v")
    assets: list[dict[str, str]] = []
    for arch in ("amd64", "arm64"):
        name = f"yggdrasil-{version}-{arch}.deb"
        assets.append(
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/yggdrasil-network/yggdrasil-go/"
                    f"releases/download/{tag}/{name}"
                ),
            }
        )
    return json.dumps({"tag_name": tag, "assets": assets})


def _make_peers_tarball(tmp_path: Path, uris: list[str]) -> Path:
    """A fake public-peers tarball with one markdown file of peer URIs."""

    tar_path = tmp_path / "peers.tar.gz"
    md_dir = tmp_path / "extract"
    md_dir.mkdir(exist_ok=True)
    content = "\n".join(f"* `{uri}`" for uri in uris) + "\n"
    (md_dir / "russia.md").write_text(content, encoding="utf-8")
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(md_dir, arcname="public-peers-master")
    return tar_path


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    retries: int = 3,
    batch_size: int = 100,
    target_count: int = 6,
    static_peers: tuple[str, ...] = (),
    address_save_retry_base_seconds: int = 1,
    address_save_retry_multiplier: int = 2,
    address_save_retry_max_seconds: int = 1,
    connection_wait_base_seconds: int = 1,
    connection_wait_multiplier: int = 2,
    connection_wait_max_seconds: int = 1,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=(
            frozenset({"yggdrasil_service_setup"}) if force else frozenset()
        ),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            yggdrasil_download_dir=tmp_path / "download",
            yggdrasil_config_path=tmp_path / "etc" / "yggdrasil" / "yggdrasil.conf",
            yggdrasil_private_key_path=tmp_path
            / "etc"
            / "yggdrasil"
            / "private-key.pem",
            yggdrasil_peers_full_path=tmp_path / "etc" / "yggdrasil" / "peers-full.txt",
            yggdrasil_install_retries=retries,
            yggdrasil_peer_batch_size=batch_size,
            yggdrasil_peer_target_count=target_count,
            yggdrasil_peer_probe_timeout_seconds=0.0,
            yggdrasil_static_peers=static_peers,
            yggdrasil_address_file_path=tmp_path
            / "var"
            / "lib"
            / "pyntara"
            / "yggdrasil_self_address",
            yggdrasil_address_save_retry_base_seconds=address_save_retry_base_seconds,
            yggdrasil_address_save_retry_multiplier=address_save_retry_multiplier,
            yggdrasil_address_save_retry_max_seconds=address_save_retry_max_seconds,
            yggdrasil_connection_wait_base_seconds=connection_wait_base_seconds,
            yggdrasil_connection_wait_multiplier=connection_wait_multiplier,
            yggdrasil_connection_wait_max_seconds=connection_wait_max_seconds,
        ),
    )


def _fake_getaddrinfo(host_map: dict[str, str]) -> object:
    """A getaddrinfo fake mapping hostnames to IP addresses."""

    def fake(host: str, port: int, **kwargs: object) -> list[object]:
        if host in host_map:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    (host_map[host], port),
                )
            ]
        raise socket.gaierror("no address")

    return fake


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    installed_version: str | None = VERSION,
    enabled: bool = True,
    active: bool = True,
    release_json: str = _release_json(),
    arch: str = "amd64",
    fail_install: int = 0,
    missing_binary: bool = False,
    peers_tarball: Path | None = None,
    journal_output: str = "",
    ctl_json: str = "",
    ctl_failures: int = 0,
    ctl_peers_json: str = "",
    peers_after_start: bool = False,
    host_map: dict[str, str] | None = None,
    version_output: str | None = None,
    active_becomes: bool = True,
    interface_exists: bool = False,
    nm_profile_exists: bool = False,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg reports the architecture, yggdrasil -version the installed
    version, yggdrasil -exportkey and -genconf answer the key flows,
    curl answers the release API and writes the fixture package or copies
    the fixture peers tarball, apt-get install fails the first
    fail_install attempts, systemctl reports the enabled and active state
    and runs start and restart, journalctl returns journal_output,
    yggdrasilctl getSelf returns ctl_json (empty for the first
    ctl_failures calls) and yggdrasilctl getPeers returns ctl_peers_json
    (empty until the service is started when peers_after_start is set).
    DNS resolution goes through host_map.
    """

    calls: list[list[str]] = []
    install_attempts = 0
    started = False
    getself_calls = 0

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal install_attempts, started, getself_calls
        del kwargs
        calls.append(list(command))
        if command[0] == "dpkg" and command[1] == "--print-architecture":
            return _FakeProc(0, f"{arch}\n")
        if command[0] == "yggdrasil":
            if command[1] == "-version":
                if missing_binary:
                    raise FileNotFoundError(command[0])
                if installed_version is None:
                    return _FakeProc(1, "")
                if version_output is not None:
                    return _FakeProc(0, version_output)
                return _FakeProc(0, f"Build version: {installed_version}\n")
            if "-exportkey" in command:
                return _FakeProc(0, KEY_PEM)
            if command[1] == "-genconf":
                return _FakeProc(0, '{"PrivateKey": "aa"}\n')
            if command[1] == "-useconf":
                return _FakeProc(0, KEY_PEM)
            return _FakeProc(0)
        if command[0] == "curl":
            if "--output" in command:
                target = Path(command[command.index("--output") + 1])
                if peers_tarball is not None and "public-peers" in command[-1]:
                    shutil.copyfile(peers_tarball, target)
                else:
                    target.write_bytes(DEB_CONTENT)
                return _FakeProc(0)
            return _FakeProc(0, release_json)
        if command[0] == "apt-get":
            if command[1] == "install":
                install_attempts += 1
                if install_attempts <= fail_install:
                    raise subprocess.CalledProcessError(100, command)
            return _FakeProc(0)
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                if enabled:
                    return _FakeProc(0, "enabled\n")
                return _FakeProc(1, "disabled\n")
            if command[1] == "is-active":
                if active or (active_becomes and started):
                    return _FakeProc(0, "active\n")
                return _FakeProc(1, "inactive\n")
            if command[1] in ("start", "restart"):
                started = True
            return _FakeProc(0)
        if command[0] == "journalctl":
            return _FakeProc(0, journal_output)
        if command[0] == "yggdrasilctl":
            if command[1] == "-json" and command[2] == "getPeers":
                if peers_after_start and not started:
                    return _FakeProc(0, "")
                return _FakeProc(0, ctl_peers_json)
            getself_calls += 1
            if getself_calls <= ctl_failures:
                return _FakeProc(0, "")
            return _FakeProc(0, ctl_json)
        if command[0] == "ip":
            if command[1] == "link" and command[2] == "show":
                if interface_exists:
                    return _FakeProc(0, "ygg: <POINTOPOINT> mtu 65535\n")
                return _FakeProc(1, "Device \"ygg\" does not exist\n")
            if command[1] == "link" and command[2] == "del":
                return _FakeProc(0)
        if command[0] == "nmcli":
            if command[1] == "connection" and command[2] == "show":
                if nm_profile_exists:
                    return _FakeProc(0, "connection.id: ygg\n")
                return _FakeProc(1, "unknown connection\n")
            if command[1] == "connection" and command[2] == "delete":
                return _FakeProc(0)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    if host_map is not None:
        monkeypatch.setattr(
            yggdrasil_service_setup.socket, "getaddrinfo", _fake_getaddrinfo(host_map)
        )
    return calls


@pytest.fixture(autouse=True)
def _fake_time(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Fake the task time helpers so retry loops never sleep in tests.

    The fake monotonic clock advances one second per reading and the
    fake sleep records the requested pause without waiting, so the
    geometric backoff of the address save retries runs fast and its
    pauses are asserted directly.
    """

    sleeps: list[float] = []
    now = [0.0]

    def fake_monotonic() -> float:
        now[0] += 1.0
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(yggdrasil_service_setup.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(yggdrasil_service_setup.time, "sleep", fake_sleep)
    return sleeps


def _write_ready_state(ctx: Context) -> None:
    """Write the config with peers, the key and the saved address file."""

    cfg = ctx.config.yggdrasil_service_setup
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        yggdrasil_service_setup._render_config(cfg, ["tcp://1.2.3.4:1234"]),
        encoding="utf-8",
    )
    cfg.private_key_path.write_text(KEY_PEM, encoding="utf-8")
    cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.address_file_path.write_text("201:dead:beef::1\n", encoding="utf-8")


SELF_ADDRESS = "201:1234:5678:9abc:def0:1234:5678:9abc"


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version matches, the config has peers, the key exists, the
    # service is enabled and active and the admin socket reports a live
    # connection: the task skips and runs only the status queries.
    ctx = _ctx(tmp_path)
    _write_ready_state(ctx)
    ctl = json.dumps(
        {
            "peers": [
                {"remote": "tcp://10.0.0.1:1001", "up": True, "latency": 5000000}
            ]
        }
    )
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=True,
        ctl_peers_json=ctl,
        host_map={"10.0.0.1": "10.0.0.1"},
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    expected_flags = curl_flags(
        ctx.config.engine.curl_timeout_seconds, ctx.config.engine.curl_retries
    )
    release_calls = [
        call
        for call in calls
        if call[0] == "curl" and "releases/latest" in " ".join(call)
    ]
    assert release_calls
    assert all(flag in release_calls[0] for flag in expected_flags)
    assert not any(call[0] == "apt-get" for call in calls)
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )


def test_active_service_without_connections_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is active and the config has peers, but the admin
    # socket reports no connections: the task does not re-select peers
    # and completes with a warning that a force rerun is needed.
    ctx = _ctx(tmp_path)
    _write_ready_state(ctx)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=True,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any("no connections" in warning for warning in result.warnings)
    # No peer list download, no config rewrite.
    assert not any(
        call[0] == "curl" and "public-peers" in call[-1] for call in calls
    )
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    assert "1.2.3.4" in config_text


def test_inactive_service_with_ready_state_starts_without_peer_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is inactive but the config already has peers: the task
    # starts the service with the existing config, waits for connections
    # and does not re-select peers or rewrite the config.
    ctx = _ctx(tmp_path)
    _write_ready_state(ctx)
    ctl_peers = json.dumps(
        {
            "peers": [
                {"remote": "tcp://10.0.0.1:1001", "up": True, "latency": 5000000}
            ]
        }
    )
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=False,
        ctl_peers_json=ctl_peers,
        peers_after_start=True,
        host_map={"10.0.0.1": "10.0.0.1"},
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "start", "yggdrasil.service"] in calls
    # No peer list download, no config rewrite.
    assert not any(
        call[0] == "curl" and "public-peers" in call[-1] for call in calls
    )
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    assert "1.2.3.4" in config_text


def test_missing_binary_is_treated_as_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing yggdrasil binary raises FileNotFoundError from subprocess;
    # the task treats it as not installed and proceeds with the install
    # instead of crashing. The peer download fails and static_peers is
    # used as the fallback.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        missing_binary=True,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )
    assert "1.2.3.4" in (ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    ))


def test_installs_new_release_with_static_peers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # yggdrasil is not installed; the peer download fails, so the
    # configured static peers land in the configuration.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    calls = _install_fake(
        monkeypatch, tmp_path, installed_version=None, enabled=False, active=False
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "enable", "yggdrasil.service"] in calls
    assert ["systemctl", "start", "yggdrasil.service"] in calls
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    assert "tls://1.2.3.4:1234" in config_text
    assert ctx.config.yggdrasil_service_setup.private_key_path.is_file()
    assert not (ctx.config.yggdrasil_service_setup.download_dir / "yggdrasil-0.5.14-amd64.deb").exists()


def test_saves_self_address_after_provisioning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # After the final restart the task saves the node self address from
    # the admin socket into the configured file, the fallback of the
    # deployed address command.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        ctl_json=json.dumps({"address": SELF_ADDRESS}),
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert (
        ctx.config.yggdrasil_service_setup.address_file_path.read_text(
            encoding="utf-8"
        ).strip()
        == SELF_ADDRESS
    )
    assert any(
        call[0] == "yggdrasilctl" and call[1] == "-json" and call[2] == "getSelf"
        for call in calls
    )


def test_self_address_save_failure_does_not_fail_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The admin socket query never parses: the task stays successful
    # and the address file is not written, so the save is best-effort.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    _install_fake(
        monkeypatch, tmp_path, installed_version=None, enabled=False, active=False
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not ctx.config.yggdrasil_service_setup.address_file_path.exists()


def test_self_address_save_retries_until_socket_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _fake_time: list[float],
) -> None:
    # The admin socket answers only after two failed getSelf queries:
    # the save retries with the geometric backoff and writes the address
    # once the query succeeds.
    ctx = _ctx(
        tmp_path,
        static_peers=("tls://1.2.3.4:1234",),
        address_save_retry_max_seconds=67,
    )
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        ctl_json=json.dumps({"address": SELF_ADDRESS}),
        ctl_failures=2,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert (
        ctx.config.yggdrasil_service_setup.address_file_path.read_text(
            encoding="utf-8"
        ).strip()
        == SELF_ADDRESS
    )
    getself_calls = [
        call
        for call in calls
        if call[0] == "yggdrasilctl"
        and call[1] == "-json"
        and call[2] == "getSelf"
    ]
    assert len(getself_calls) == 3
    assert _fake_time == [1, 2]


def test_skip_requires_address_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is ready except the saved address file: the task does
    # not skip, runs the provisioning and writes the address file.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    cfg = ctx.config.yggdrasil_service_setup
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        yggdrasil_service_setup._render_config(cfg, ["tcp://1.2.3.4:1234"]),
        encoding="utf-8",
    )
    cfg.private_key_path.write_text(KEY_PEM, encoding="utf-8")
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=True,
        ctl_json=json.dumps({"address": SELF_ADDRESS}),
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert result.message != "already configured"
    assert (
        ctx.config.yggdrasil_service_setup.address_file_path.read_text(
            encoding="utf-8"
        ).strip()
        == SELF_ADDRESS
    )
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_installs_new_release_with_downloaded_peers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The peer list downloads; the first batch has enough working peers,
    # so the final config keeps the target count with the lowest ping.
    host_map = {
        "10.0.0.1": "10.0.0.1",
        "10.0.0.2": "10.0.0.2",
        "10.0.0.3": "10.0.0.3",
    }
    uris = [
        "tcp://10.0.0.1:1001",
        "tcp://10.0.0.2:1002",
        "tcp://10.0.0.3:1003",
    ]
    tarball = _make_peers_tarball(tmp_path, uris)
    journal = (
        "2026-08-13 Connected outbound: "
        "226:43e9:3739:64a4:db0c:4147:abfe:6ea6@10.0.0.1:1001, "
        "source 192.168.85.146:54588\n"
        "2026-08-13 Connected outbound: "
        "201:e165:5940:ce70:e2f:19c5:67b:812e@10.0.0.2:1002, "
        "source [::]:36046\n"
    )
    ctl = json.dumps(
        {
            "peers": [
                {
                    "remote": "tcp://10.0.0.1:1001",
                    "up": True,
                    "latency": 5000000,
                },
                {
                    "remote": "tcp://10.0.0.2:1002",
                    "up": True,
                    "latency": 10000000,
                },
            ]
        }
    )
    ctx = _ctx(tmp_path, batch_size=3, target_count=2)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        peers_tarball=tarball,
        journal_output=journal,
        ctl_peers_json=ctl,
        host_map=host_map,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    # The lowest-latency working peers are kept.
    assert "10.0.0.1:1001" in config_text
    assert "10.0.0.2:1002" in config_text
    assert "10.0.0.3:1003" not in config_text
    # The full list is saved next to the config.
    full = ctx.config.yggdrasil_service_setup.peers_full_path.read_text(
        encoding="utf-8"
    )
    assert "10.0.0.1:1001" in full
    # journalctl and yggdrasilctl were queried.
    assert any(call[0] == "journalctl" for call in calls)
    assert any(call[0] == "yggdrasilctl" for call in calls)


def test_no_batch_reaches_target_keeps_last_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No batch has enough working peers; the last tried batch stays in
    # the configuration and the task reports a warning.
    uris = ["tcp://10.0.0.1:1001", "tcp://10.0.0.2:1002"]
    tarball = _make_peers_tarball(tmp_path, uris)
    ctx = _ctx(tmp_path, batch_size=1, target_count=6)
    monkeypatch.setattr(yggdrasil_service_setup.random, "shuffle", lambda x: None)
    _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        peers_tarball=tarball,
        journal_output="",
        host_map={"10.0.0.1": "10.0.0.1", "10.0.0.2": "10.0.0.2"},
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert "no batch reached" in (result.message or "")
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    assert "10.0.0.2:1002" in config_text


def test_download_fails_and_no_static_peers_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The peer download fails and static_peers is empty: the task
    # completes with a warning, because a node without peers never joins
    # the network.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch, tmp_path, installed_version=None, enabled=False, active=False
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any("static_peers is empty" in warning for warning in result.warnings)
    assert not any(
        call[0] == "systemctl" and call[1] in ("start", "restart")
        for call in calls
    )


def test_install_gives_up_after_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # apt always fails: the task tries one initial attempt plus the
    # configured retries, then reports the failure.
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, tmp_path, installed_version=None, fail_install=99)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any("cannot install yggdrasil" in warning for warning in result.warnings)
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 4


def test_install_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first apt attempt fails, the retry succeeds.
    ctx = _ctx(tmp_path, retries=3, static_peers=("tls://1.2.3.4:1234",))
    calls = _install_fake(monkeypatch, tmp_path, installed_version=None, fail_install=1)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 2


def test_no_matching_asset_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The release has no asset for this architecture: the task completes
    # with a warning about the missing asset.
    ctx = _ctx(tmp_path)
    release = json.dumps({"tag_name": TAG, "assets": []})
    calls = _install_fake(
        monkeypatch, tmp_path, installed_version=None, release_json=release
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any(
        "has no yggdrasil-0.5.14-amd64.deb asset" in warning
        for warning in result.warnings
    )
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_release_json_failure_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The releases API fails: the task completes with a warning about the
    # fetch error.
    ctx = _ctx(tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(22, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any("cannot fetch" in warning for warning in result.warnings)


def test_force_reruns_peer_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is ready, but the task is forced: the peer list is
    # downloaded and probed again, the config is rewritten.
    host_map = {"10.0.0.1": "10.0.0.1"}
    uris = ["tcp://10.0.0.1:1001"]
    tarball = _make_peers_tarball(tmp_path, uris)
    journal = (
        "2026-08-13 Connected outbound: "
        "226:43e9:3739:64a4:db0c:4147:abfe:6ea6@10.0.0.1:1001, "
        "source 192.168.85.146:54588\n"
    )
    ctx = _ctx(tmp_path, force=True, batch_size=1, target_count=1)
    _write_ready_state(ctx)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=True,
        peers_tarball=tarball,
        journal_output=journal,
        host_map=host_map,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "journalctl" for call in calls)
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_service_never_active_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service never becomes active after the final restart: the task
    # completes with a warning.
    ctx = _ctx(tmp_path, static_peers=("tls://1.2.3.4:1234",))
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=False,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert any("did not become active" in warning for warning in result.warnings)
    assert any(
        call[0] == "systemctl" and call[1] == "start" for call in calls
    )


def test_select_asset_by_architecture() -> None:
    # The asset name uses the bare version and the dpkg architecture;
    # the leading v of the tag is stripped before the lookup.
    release = json.loads(_release_json())
    selected = yggdrasil_service_setup._select_asset(release, VERSION, "amd64")
    assert selected == (
        f"yggdrasil-{VERSION}-amd64.deb",
        (
            "https://github.com/yggdrasil-network/yggdrasil-go/releases/"
            f"download/{TAG}/yggdrasil-{VERSION}-amd64.deb"
        ),
    )
    assert (
        yggdrasil_service_setup._select_asset(release, VERSION, "s390x") is None
    )


def test_render_config_fields() -> None:
    # The rendered config carries the key path, the interface settings,
    # the listeners, the multicast blocks and the peers, and never the
    # key material.
    ctx = _ctx(Path("/tmp"))
    rendered = yggdrasil_service_setup._render_config(
        ctx.config.yggdrasil_service_setup, ["tcp://1.2.3.4:1234"]
    )
    data = json.loads(rendered)
    assert data["PrivateKeyPath"] == str(
        ctx.config.yggdrasil_service_setup.private_key_path
    )
    assert data["IfName"] == "ygg"
    assert data["IfMTU"] == 65535
    assert "tls://[::]:0" in data["Listen"]
    assert data["MulticastInterfaces"][0]["Regex"] == ".*"
    assert data["MulticastInterfaces"][0]["Beacon"] is True
    assert data["Peers"] == ["tcp://1.2.3.4:1234"]
    assert "PrivateKey" not in data


def test_ensure_private_key_extracts_from_existing_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The key file is missing but the config exists: the task extracts
    # the key with yggdrasil -useconffile -exportkey and writes the PEM.
    ctx = _ctx(tmp_path)
    ctx.config.yggdrasil_service_setup.config_path.parent.mkdir(parents=True)
    ctx.config.yggdrasil_service_setup.config_path.write_text("{}", encoding="utf-8")
    calls = _install_fake(monkeypatch, tmp_path)
    yggdrasil_service_setup._ensure_private_key(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert ctx.config.yggdrasil_service_setup.private_key_path.read_text(
        encoding="utf-8"
    ) == KEY_PEM
    assert any(
        call[0] == "yggdrasil" and call[1] == "-useconffile" for call in calls
    )
    assert not any(call[1] == "-genconf" for call in calls)


def test_ensure_private_key_generates_when_no_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Neither the key nor the config exists: the task generates a config
    # and exports the key from it.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, tmp_path)
    yggdrasil_service_setup._ensure_private_key(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert ctx.config.yggdrasil_service_setup.private_key_path.read_text(
        encoding="utf-8"
    ) == KEY_PEM
    assert any(
        call[0] == "yggdrasil" and call[1] == "-genconf" for call in calls
    )


def test_parse_md_peers_extracts_and_deduplicates() -> None:
    # Backtick URIs are extracted and deduplicated; template peers with
    # placeholder hosts, which crash yggdrasil at startup, are dropped.
    text = (
        "* `tcp://1.2.3.4:1000`\n"
        "* `tls://[2001:db8::1]:1001` with a note\n"
        "* `tcp://1.2.3.4:1000`\n"
        "* `sockstls://[proxyhost]:[proxyport]/[host]:[port]`\n"
        "* `socks://[username]:[password]@[proxyhost]:[proxyport]/[host]:[port]`\n"
    )
    assert yggdrasil_service_setup._parse_md_peers(text) == [
        "tcp://1.2.3.4:1000",
        "tls://[2001:db8::1]:1001",
    ]


def test_is_parseable_peer_uri() -> None:
    # A real peer URI with a host and port is parseable; the template
    # peers with placeholder hosts are not.
    assert yggdrasil_service_setup._is_parseable_peer_uri(
        "tcp://peer.example:1001"
    )
    assert yggdrasil_service_setup._is_parseable_peer_uri(
        "tls://[2001:db8::1]:1001"
    )
    assert not yggdrasil_service_setup._is_parseable_peer_uri(
        "sockstls://[proxyhost]:[proxyport]/[host]:[port]"
    )
    assert not yggdrasil_service_setup._is_parseable_peer_uri(
        "socks://[username]:[password]@[proxyhost]:[proxyport]/[host]:[port]"
    )


def test_resolve_uri_addrs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A resolvable URI yields its addresses; an unresolvable or malformed
    # URI yields an empty list.
    monkeypatch.setattr(
        yggdrasil_service_setup.socket,
        "getaddrinfo",
        _fake_getaddrinfo({"peer.example": "10.0.0.1"}),
    )
    assert yggdrasil_service_setup._resolve_uri_addrs(
        "tcp://peer.example:1001"
    ) == [("10.0.0.1", 1001)]
    assert yggdrasil_service_setup._resolve_uri_addrs("tcp://gone.example:1001") == []
    assert yggdrasil_service_setup._resolve_uri_addrs("tcp://peer.example") == []


def test_journal_connected_addrs_parses_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Connected lines yield their remote (ip, port) pairs; a failed
    # journalctl call yields an empty set.
    journal = (
        "Connected outbound: "
        "226:43e9:3739:64a4:db0c:4147:abfe:6ea6@10.0.0.1:1001, "
        "source 192.168.85.146:54588\n"
        "Connected inbound: "
        "201:e165:5940:ce70:e2f:19c5:67b:812e@[2001:db8::1]:1002, "
        "source [::]:36046\n"
    )
    calls = _install_fake(monkeypatch, tmp_path, journal_output=journal)
    assert yggdrasil_service_setup._journal_connected_addrs(
        "yggdrasil.service", 30, 10
    ) == {("10.0.0.1", 1001), ("2001:db8::1", 1002)}
    assert any(call[0] == "journalctl" for call in calls)


def test_latencies_from_ctl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The admin socket latency map is parsed by (ip, port); a failed call
    # yields an empty map.
    ctl = json.dumps(
        {
            "peers": [
                {"remote": "tcp://10.0.0.1:1001", "up": True, "latency": 5000000}
            ]
        }
    )
    monkeypatch.setattr(
        yggdrasil_service_setup.socket,
        "getaddrinfo",
        _fake_getaddrinfo({"10.0.0.1": "10.0.0.1"}),
    )
    calls = _install_fake(monkeypatch, tmp_path, ctl_peers_json=ctl)
    assert yggdrasil_service_setup._latencies_from_ctl(10) == {
        ("10.0.0.1", 1001): 5000000.0
    }
    assert any(call[0] == "yggdrasilctl" for call in calls)


def test_pick_best_peers_sorts_by_latency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Peers with a known latency come first in ascending order; peers
    # without a latency stay at the end in batch order.
    monkeypatch.setattr(
        yggdrasil_service_setup.socket,
        "getaddrinfo",
        _fake_getaddrinfo({"10.0.0.1": "10.0.0.1", "10.0.0.2": "10.0.0.2"}),
    )
    latencies = {
        ("10.0.0.1", 1001): 9000000.0,
        ("10.0.0.2", 1002): 1000000.0,
    }
    picked = yggdrasil_service_setup._pick_best_peers(
        ["tcp://10.0.0.1:1001", "tcp://10.0.0.2:1002"],
        latencies,
        1,
    )
    assert picked == ["tcp://10.0.0.2:1002"]


def test_config_has_peers(tmp_path: Path) -> None:
    # A config with a non-empty Peers array is ready; a missing, broken
    # or empty-peer config is not.
    ctx = _ctx(tmp_path)
    cfg = ctx.config.yggdrasil_service_setup
    assert yggdrasil_service_setup._config_has_peers(cfg) is False
    cfg.config_path.parent.mkdir(parents=True)
    cfg.config_path.write_text('{"Peers": []}', encoding="utf-8")
    assert yggdrasil_service_setup._config_has_peers(cfg) is False
    cfg.config_path.write_text('{"Peers": ["tcp://1.2.3.4:1000"]}', encoding="utf-8")
    assert yggdrasil_service_setup._config_has_peers(cfg) is True
    cfg.config_path.write_text("not json", encoding="utf-8")
    assert yggdrasil_service_setup._config_has_peers(cfg) is False


def test_installed_version_parsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version triple is extracted from the -version output; a missing
    # binary, a nonzero exit and an unrecognized output report None.
    monkeypatch.setattr(
        "pyntara.utils.subprocess.run",
        lambda *a, **k: _FakeProc(0, "Build version: 0.5.14\n"),
    )
    assert yggdrasil_service_setup._installed_version(10) == "0.5.14"
    monkeypatch.setattr(
        "pyntara.utils.subprocess.run", lambda *a, **k: _FakeProc(1, "")
    )
    assert yggdrasil_service_setup._installed_version(10) is None
    monkeypatch.setattr(
        "pyntara.utils.subprocess.run", lambda *a, **k: _FakeProc(0, "unknown output")
    )
    assert yggdrasil_service_setup._installed_version(10) is None


def test_cleanup_leftover_interface_deletes_profile_and_iface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A stale interface with a saved NetworkManager profile and no running
    # service is removed: the profile first, then the interface, so the
    # next start does not panic on the already assigned address.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        active=False,
        interface_exists=True,
        nm_profile_exists=True,
    )
    yggdrasil_service_setup._cleanup_leftover_interface(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert ["nmcli", "connection", "delete", "ygg"] in calls
    assert ["ip", "link", "del", "ygg"] in calls


def test_cleanup_leftover_interface_keeps_running_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A live interface owned by a running service is never touched.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        active=True,
        interface_exists=True,
        nm_profile_exists=True,
    )
    yggdrasil_service_setup._cleanup_leftover_interface(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert not any(call[0] == "nmcli" for call in calls)
    assert not any(call[0] == "ip" and call[2] == "del" for call in calls)


def test_cleanup_leftover_interface_skipped_without_iface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without an interface there is nothing to clean up.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        active=False,
        interface_exists=False,
    )
    yggdrasil_service_setup._cleanup_leftover_interface(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert not any(call[0] == "nmcli" for call in calls)
    assert not any(call[0] == "ip" and call[2] == "del" for call in calls)


def test_cleanup_leftover_interface_without_nmcli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine without NetworkManager has no nmcli binary: the profile
    # step is skipped and the interface is still deleted, because nothing
    # recreates it.
    ctx = _ctx(tmp_path)
    calls: list[list[str]] = []

    def fake_subprocess_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if command[0] == "nmcli":
            raise FileNotFoundError(command[0])
        if command[0] == "ip" and command[2] == "show":
            return _FakeProc(0, "ygg: <POINTOPOINT> mtu 65535\n")
        if command[0] == "ip" and command[2] == "del":
            return _FakeProc(0)
        if command[0] == "systemctl":
            return _FakeProc(1, "inactive\n")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_subprocess_run)
    yggdrasil_service_setup._cleanup_leftover_interface(
        ctx.config.yggdrasil_service_setup, 10
    )
    assert ["ip", "link", "del", "ygg"] in calls


def test_ready_state_with_leftover_interface_cleans_and_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The config already has peers but the service is down and a stale
    # interface with a NetworkManager profile blocks the start: the task
    # cleans the leftover up, starts the service and reports live
    # connections from the existing configuration.
    ctx = _ctx(tmp_path)
    _write_ready_state(ctx)
    ctl_peers = json.dumps(
        {
            "peers": [
                {"remote": "tcp://10.0.0.1:1001", "up": True, "latency": 5000000}
            ]
        }
    )
    calls = _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=VERSION,
        enabled=True,
        active=False,
        ctl_peers_json=ctl_peers,
        peers_after_start=True,
        host_map={"10.0.0.1": "10.0.0.1"},
        interface_exists=True,
        nm_profile_exists=True,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["nmcli", "connection", "delete", "ygg"] in calls
    assert ["ip", "link", "del", "ygg"] in calls
    assert ["systemctl", "start", "yggdrasil.service"] in calls


def test_wait_for_connections_returns_live_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _fake_time: list[float]
) -> None:
    # Live peers are reported immediately without a retry pause.
    cfg = _ctx(tmp_path).config.yggdrasil_service_setup
    live = {("10.0.0.1", 1001): 1.0, ("10.0.0.2", 1002): 2.0}
    monkeypatch.setattr(
        yggdrasil_service_setup, "_latencies_from_ctl", lambda timeout: live
    )
    assert yggdrasil_service_setup._wait_for_connections(cfg, 10) == 2
    assert _fake_time == []


def test_wait_for_connections_gives_up_without_peers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _fake_time: list[float]
) -> None:
    # No peer appears within the retry budget: the helper returns 0 and
    # records the backoff pauses, so the caller can warn the user.
    cfg = _ctx(
        tmp_path, connection_wait_max_seconds=3
    ).config.yggdrasil_service_setup
    monkeypatch.setattr(
        yggdrasil_service_setup, "_latencies_from_ctl", lambda timeout: {}
    )
    assert yggdrasil_service_setup._wait_for_connections(cfg, 10) == 0
    # The helper retried within the budget and then gave up instead of
    # looping forever; the exact pause count depends on the fake clock
    # reads, so only the retry itself is asserted.
    assert len(_fake_time) >= 1


def test_wait_for_connections_retries_until_peers_appear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _fake_time: list[float]
) -> None:
    # The first query sees no peers; the retry sees one and returns the
    # count.
    cfg = _ctx(
        tmp_path, connection_wait_max_seconds=3
    ).config.yggdrasil_service_setup
    state = {"calls": 0}

    def fake_latencies(timeout: float) -> dict[tuple[str, int], float]:
        del timeout
        state["calls"] += 1
        if state["calls"] == 1:
            return {}
        return {("10.0.0.1", 1001): 1.0}

    monkeypatch.setattr(
        yggdrasil_service_setup, "_latencies_from_ctl", fake_latencies
    )
    assert yggdrasil_service_setup._wait_for_connections(cfg, 10) == 1
    assert state["calls"] == 2


def test_final_config_without_live_connections_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Peers connected during the batch, but the admin socket reports none
    # after the final restart: the task completes with a warning instead
    # of silently reporting a healthy node.
    uris = ["tcp://10.0.0.1:1001", "tcp://10.0.0.2:1002"]
    tarball = _make_peers_tarball(tmp_path, uris)
    journal = (
        "2026-08-13 Connected outbound: "
        "226:43e9:3739:64a4:db0c:4147:abfe:6ea6@10.0.0.1:1001, "
        "source 192.168.85.146:54588\n"
        "2026-08-13 Connected outbound: "
        "226:43e9:3739:64a4:db0c:4147:abfe:6ea7@10.0.0.2:1002, "
        "source 192.168.85.146:54589\n"
    )
    ctx = _ctx(tmp_path, batch_size=3, target_count=2)
    _install_fake(
        monkeypatch,
        tmp_path,
        installed_version=None,
        enabled=False,
        active=False,
        peers_tarball=tarball,
        journal_output=journal,
        host_map={"10.0.0.1": "10.0.0.1", "10.0.0.2": "10.0.0.2"},
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        "has no live connections" in warning for warning in result.warnings
    )
    config_text = ctx.config.yggdrasil_service_setup.config_path.read_text(
        encoding="utf-8"
    )
    assert "10.0.0.1:1001" in config_text
