"""Unit tests for the i2pd_service_setup task.

All external resources (os-release, subprocess, filesystem paths) are
mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The configuration template is rendered
from a fixture, so the tests never read the repository template.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import (
    i2pd_keys_b32_address,
    i2pd_keys_file_bytes,
    make_config,
    make_context,
)

from pyntara.config import SshDirective
from pyntara.context import Context
from pyntara.i2pd import b32_address
from pyntara.tasks import i2pd_service_setup

I2PD_TEMPLATE = """\
loglevel = $log_level

bandwidth = $bandwidth
share = $share

tunconf = $tunnels_config_path

[http]
enabled = $http_enabled

[socksproxy]
enabled = $socks_proxy_enabled
"""

TUNNELS_TEMPLATE = """\
[$tunnel_name]
type = server
host = $tunnel_host
port = $tunnel_port
keys = $tunnel_keys_path
"""

# The newest release tag; the tests treat the generic asset and the
# codename-specific asset of this tag as available.
TAG = "2.61.0"
DEB_CONTENT = b"fake i2pd binary\n"


def _release_json(
    *,
    tag: str = TAG,
    codename_asset: bool = True,
    generic_asset: bool = True,
) -> str:
    """The GitHub releases API payload used by the curl fake."""

    assets: list[dict[str, str]] = []
    if codename_asset:
        name = f"i2pd_{tag}-1resolute1_amd64.deb"
        assets.append(
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/PurpleI2P/i2pd/releases/download/"
                    f"{tag}/{name}"
                ),
            }
        )
    if generic_asset:
        name = f"i2pd_{tag}-1_amd64.deb"
        assets.append(
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/PurpleI2P/i2pd/releases/download/"
                    f"{tag}/{name}"
                ),
            }
        )
    return json.dumps({"tag_name": tag, "assets": assets})


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    skip_apt_update: bool = True,
    retries: int = 3,
    check_attempts: int = 5,
    codename: str = "resolute",
    ssh_port: str | None = "30222",
) -> Context:
    """Context with a small safe config; the real file is never touched.

    ssh_port is the sshd Port directive value; None omits the directive,
    so the missing-port path can be exercised.
    """

    directives = [SshDirective(name="PubkeyAuthentication", value="yes")]
    if ssh_port is not None:
        directives.insert(0, SshDirective(name="Port", value=ssh_port))
    return make_context(
        install_mode="server",
        force_tasks=frozenset({"i2pd_service_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            i2pd_download_dir=tmp_path / "download",
            i2pd_config_path=tmp_path / "etc" / "i2pd" / "i2pd.conf",
            i2pd_tunnels_config_path=tmp_path / "etc" / "i2pd" / "tunnels.conf",
            i2pd_tunnel_keys_path=tmp_path / "etc" / "i2pd" / "ssh.dat",
            i2pd_address_file_path=tmp_path / "var" / "lib" / "pyntara" / "i2pd_ssh_address",
            i2pd_install_retries=retries,
            i2pd_start_check_attempts=check_attempts,
            i2pd_start_check_retry_delay_seconds=0.0,
            ssh_daemon_directives=tuple(directives),
        ),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    codename: str = "resolute",
) -> dict[str, object]:
    """Point the task at temporary fixtures; return the fixture paths."""

    os_release = tmp_path / "os-release"
    os_release.write_text(
        f'ID=ubuntu\nVERSION_CODENAME="{codename}"\n', encoding="utf-8"
    )
    monkeypatch.setattr(i2pd_service_setup, "OS_RELEASE_PATH", os_release)
    template = tmp_path / "task_data" / "i2pd_service_setup" / "i2pd.conf"
    template.parent.mkdir(parents=True)
    template.write_text(I2PD_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(i2pd_service_setup, "TEMPLATE_PATH", template)
    tunnels_template = (
        tmp_path / "task_data" / "i2pd_service_setup" / "tunnels.conf"
    )
    tunnels_template.write_text(TUNNELS_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(
        i2pd_service_setup, "TUNNELS_TEMPLATE_PATH", tunnels_template
    )
    return {
        "os_release": os_release,
        "template": template,
        "tunnels_template": tunnels_template,
    }


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed_version: str | None = TAG,
    enabled: bool = True,
    active: bool = True,
    release_json: str = _release_json(),
    arch: str = "amd64",
    fail_install: int = 0,
    active_becomes: bool = True,
    missing_binary: bool = False,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg reports the architecture, i2pd --version the installed version
    (None means not installed), curl answers the release API and writes
    the fixture package file, apt-get install fails the first
    fail_install attempts, and systemctl reports the enabled and active
    state from the flags. With active_becomes, the service turns active
    after the first start or restart; without it, the readiness loop
    runs out. With missing_binary, the i2pd call raises
    FileNotFoundError like a real missing executable.
    """

    calls: list[list[str]] = []
    install_attempts = 0
    started = False

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal install_attempts, started
        del kwargs
        calls.append(list(command))
        if command[0] == "dpkg" and command[1] == "--print-architecture":
            return _FakeProc(0, f"{arch}\n")
        if command[0] == "i2pd":
            if missing_binary:
                raise FileNotFoundError(command[0])
            if installed_version is None:
                return _FakeProc(1, "")
            return _FakeProc(0, f"i2pd version {installed_version}\n")
        if command[0] == "curl":
            if "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_bytes(DEB_CONTENT)
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
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _write_state_as_rendered(ctx: Context) -> None:
    """Write both configs as rendered, the tunnel keys and the saved address."""

    cfg = ctx.config.i2pd_service_setup
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        i2pd_service_setup._render_config(cfg), encoding="utf-8"
    )
    ssh_port = i2pd_service_setup._ssh_port_from_ssh_config(
        ctx.config.ssh_daemon_setup.directives
    )
    cfg.tunnels_config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnels_config_path.write_text(
        i2pd_service_setup._render_tunnels_config(cfg, ssh_port),
        encoding="utf-8",
    )
    cfg.tunnel_keys_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnel_keys_path.write_bytes(i2pd_keys_file_bytes())
    address = b32_address(cfg.tunnel_keys_path)
    if address:
        cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.address_file_path.write_text(f"{address}\n", encoding="utf-8")


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installed version equals the newest release, both configuration
    # files match their renders, the tunnel keys file exists and the
    # service is enabled and active: the task skips and runs only the
    # status queries.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_state_as_rendered(ctx)
    calls = _install_fake(monkeypatch, installed_version=TAG, enabled=True, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "apt-get" for call in calls)
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )


def test_missing_binary_is_treated_as_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing i2pd binary raises FileNotFoundError from subprocess; the
    # task treats it as not installed and proceeds with the install
    # instead of crashing.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        installed_version=None,
        enabled=False,
        active=False,
        missing_binary=True,
    )
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_installs_new_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # i2pd is not installed and the service is not enabled: the task
    # downloads the codename-specific asset, installs it with apt, writes
    # the configuration, enables and starts the service.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch, installed_version=None, enabled=False, active=False
    )
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert TAG in (result.message or "")
    asset = f"i2pd_{TAG}-1resolute1_amd64.deb"
    assert ["apt-get", "install", "-y", str(ctx.config.i2pd_service_setup.download_dir / asset)] in calls
    assert ["systemctl", "enable", "i2pd.service"] in calls
    assert ["systemctl", "start", "i2pd.service"] in calls
    config = ctx.config.i2pd_service_setup.config_path
    assert config.read_text(encoding="utf-8") == (
        i2pd_service_setup._render_config(ctx.config.i2pd_service_setup)
    )
    # The downloaded file is removed after the successful install.
    assert not (ctx.config.i2pd_service_setup.download_dir / asset).exists()


def test_update_reinstalls_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older release is installed: the task installs the newer one and
    # restarts the running service.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_state_as_rendered(ctx)
    calls = _install_fake(monkeypatch, installed_version="2.60.0", active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert ["systemctl", "start", "i2pd.service"] not in calls


def test_install_gives_up_after_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # apt always fails: the task tries one initial attempt plus the
    # configured retries, then reports the failure.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, installed_version=None, fail_install=99)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "cannot install i2pd" in (result.error or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 4


def test_install_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first apt attempt fails, the retry succeeds.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, installed_version=None, fail_install=1)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 2


def test_skip_apt_update_skips_index_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # skip_apt_update is set: apt-get update never runs before the install.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, skip_apt_update=True)
    calls = _install_fake(monkeypatch, installed_version=None)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert not any(call[0] == "apt-get" and call[1] == "update" for call in calls)


def test_config_rewritten_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version matches but the configuration file is absent: the task
    # writes it and restarts the running service.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ctx.config.i2pd_service_setup.config_path.is_file()
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_force_rewrites_config_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured, but the task is forced: the
    # configuration is rewritten and the service restarted, while the
    # matching version is not reinstalled.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, force=True)
    _write_state_as_rendered(ctx)
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_service_never_active_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is installed but never reports active after start: the
    # readiness loop runs out and the task reports the failure.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, check_attempts=3)
    calls = _install_fake(
        monkeypatch, installed_version=None, active=False, active_becomes=False
    )
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "did not become active" in (result.error or "")
    starts = [
        call for call in calls if call[0] == "systemctl" and call[1] == "start"
    ]
    assert len(starts) == 1


def test_non_debian_os_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The os-release names a non-Debian family: the task refuses to install
    # a deb package and never queries the release.
    _install_fixtures(monkeypatch, tmp_path, codename="rolling")
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=arch\nID_LIKE=archlinux\n', encoding="utf-8")
    monkeypatch.setattr(i2pd_service_setup, "OS_RELEASE_PATH", os_release)
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, installed_version=None)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "Debian-based" in (result.error or "")
    assert not any(call[0] == "curl" for call in calls)


def test_no_matching_asset_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The release has no asset for this machine: the task reports the
    # missing asset and stops.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    release = _release_json(codename_asset=False, generic_asset=False)
    calls = _install_fake(monkeypatch, installed_version=None, release_json=release)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "no .deb asset" in (result.error or "")
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_generic_asset_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The release has only the generic asset: it is selected when the
    # codename-specific build is absent.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    release = _release_json(codename_asset=False)
    calls = _install_fake(
        monkeypatch,
        installed_version=None,
        enabled=False,
        active=False,
        release_json=release,
    )
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "apt-get" and "i2pd_2.61.0-1_amd64.deb" in call[-1]
        for call in calls
    )


def test_release_json_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The releases API fails: the task reports the fetch error.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(22, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "cannot fetch" in (result.error or "")


def test_select_asset_prioritizes_codename() -> None:
    # The codename-specific asset wins over the generic one; without a
    # codename only the generic asset matches; an unknown architecture
    # matches nothing.
    release = json.loads(_release_json())
    specific_asset = i2pd_service_setup._select_asset(
        release, TAG, "resolute", "amd64"
    )
    assert specific_asset is not None
    specific, _ = specific_asset
    assert specific == f"i2pd_{TAG}-1resolute1_amd64.deb"
    generic_asset = i2pd_service_setup._select_asset(release, TAG, None, "amd64")
    assert generic_asset is not None
    generic, _ = generic_asset
    assert generic == f"i2pd_{TAG}-1_amd64.deb"
    assert (
        i2pd_service_setup._select_asset(release, TAG, "resolute", "s390x")
        is None
    )


def test_render_config_bool_spelling() -> None:
    # Booleans render as the lowercase true/false spelling i2pd accepts;
    # the main configuration names the owned tunnels file through tunconf
    # and carries the configured bandwidth limit and transit share.
    ctx = _ctx(Path("/tmp"))
    config = i2pd_service_setup._render_config(
        ctx.config.i2pd_service_setup
    )
    assert "loglevel = warn\n" in config
    assert "bandwidth = 12500\n" in config
    assert "share = 1\n" in config
    assert (
        f"tunconf = {ctx.config.i2pd_service_setup.tunnels_config_path}\n"
        in config
    )
    assert "[http]\nenabled = false\n" in config
    assert "[socksproxy]\nenabled = true\n" in config


def test_render_tunnels_config_uses_ssh_port() -> None:
    # The tunnels render carries the tunnel section, the forward host, the
    # sshd port read from the ssh_daemon_setup directives and the keys
    # value as the file name only, because i2pd resolves every keys path
    # against its data directory.
    ctx = _ctx(Path("/tmp"))
    config = i2pd_service_setup._render_tunnels_config(
        ctx.config.i2pd_service_setup, 30222
    )
    assert "[ssh]\ntype = server\n" in config
    assert f"host = {ctx.config.i2pd_service_setup.tunnel_host}\n" in config
    assert "port = 30222\n" in config
    assert (
        f"keys = {ctx.config.i2pd_service_setup.tunnel_keys_path.name}\n"
        in config
    )


def test_first_run_message_without_keys_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # On the first run the keys file does not exist yet: the task writes
    # the tunnels file, starts the service and reports that the address
    # appears after the first start.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch, installed_version=None, enabled=False, active=False
    )
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert "appears after the first start" in (result.message or "")
    assert ctx.config.i2pd_service_setup.tunnels_config_path.is_file()
    assert ["systemctl", "start", "i2pd.service"] in calls


def test_address_reported_when_keys_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With the keys file present, the task message carries the computed
    # .b32.i2p address of the tunnel.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, force=True)
    _write_state_as_rendered(ctx)
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert i2pd_keys_b32_address() in (result.message or "")
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert (
        ctx.config.i2pd_service_setup.address_file_path.read_text(
            encoding="utf-8"
        ).strip()
        == i2pd_keys_b32_address()
    )


def test_stale_address_file_is_rewritten_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything matches except the saved address file, which carries a
    # stale value: the task rewrites the file with the current address
    # and reports changed, without reinstalling and without restarting
    # the service.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    cfg = ctx.config.i2pd_service_setup
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        i2pd_service_setup._render_config(cfg), encoding="utf-8"
    )
    ssh_port = i2pd_service_setup._ssh_port_from_ssh_config(
        ctx.config.ssh_daemon_setup.directives
    )
    cfg.tunnels_config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnels_config_path.write_text(
        i2pd_service_setup._render_tunnels_config(cfg, ssh_port),
        encoding="utf-8",
    )
    cfg.tunnel_keys_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnel_keys_path.write_bytes(i2pd_keys_file_bytes())
    cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.address_file_path.write_text("stale.b32.i2p\n", encoding="utf-8")
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert (
        cfg.address_file_path.read_text(encoding="utf-8").strip()
        == i2pd_keys_b32_address()
    )
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )
    assert not any(
        call[0] == "systemctl" and call[1] in ("start", "restart")
        for call in calls
    )


def test_missing_keys_file_restarts_even_when_configs_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Both configs match their renders but the keys file is absent (i2pd
    # did not create it): the task restarts the service so i2pd
    # regenerates the identity, and never reinstalls the matching
    # version.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    cfg = ctx.config.i2pd_service_setup
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path.write_text(
        i2pd_service_setup._render_config(cfg), encoding="utf-8"
    )
    ssh_port = i2pd_service_setup._ssh_port_from_ssh_config(
        ctx.config.ssh_daemon_setup.directives
    )
    cfg.tunnels_config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.tunnels_config_path.write_text(
        i2pd_service_setup._render_tunnels_config(cfg, ssh_port),
        encoding="utf-8",
    )
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_ssh_port_change_rewrites_tunnels_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The sshd Port directive changed from the value the tunnels file was
    # rendered with: the tunnels file is rewritten with the new port and
    # the service restarted, without a reinstall.
    _install_fixtures(monkeypatch, tmp_path)
    _write_state_as_rendered(_ctx(tmp_path, ssh_port="30222"))
    ctx = _ctx(tmp_path, ssh_port="30333")
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    tunnels = ctx.config.i2pd_service_setup.tunnels_config_path.read_text(
        encoding="utf-8"
    )
    assert "port = 30333\n" in tunnels
    assert ["systemctl", "restart", "i2pd.service"] in calls
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_missing_ssh_port_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The ssh_daemon_setup config has no Port directive: the tunnel
    # cannot target a known port, so the task reports the error and
    # touches nothing.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, ssh_port=None)
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "no Port directive" in (result.error or "")
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )


def test_non_numeric_ssh_port_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The sshd Port directive is not a number: the task reports the error
    # instead of rendering a broken tunnel.
    _install_fixtures(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, ssh_port="abc")
    calls = _install_fake(monkeypatch, installed_version=TAG, active=True)
    result = i2pd_service_setup.task(ctx)
    assert result.success is False
    assert "not a number" in (result.error or "")
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )
