"""Unit tests for the tor_setup task.

All external resources (subprocess, pwd, filesystem paths) are mocked
via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The configuration is rendered from the
task code, so the tests compare against the same render the task writes.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.config import SshDirective
from pyntara.context import Context
from pyntara.tasks import tor_setup

# The onion address written into the hidden service hostname file by
# the subprocess fake after the first service start.
ADDRESS = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    retries: int = 3,
    check_attempts: int = 5,
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
        force_tasks=frozenset({"tor_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            tor_torrc_path=tmp_path / "etc" / "tor" / "torrc",
            tor_torrc_dropin_path=tmp_path / "etc" / "tor" / "pyntara.conf",
            tor_torrc_include_path=str(tmp_path / "etc" / "tor" / "pyntara.conf"),
            tor_hidden_service_dir=tmp_path / "var" / "lib" / "tor" / "ssh",
            tor_address_file_path=tmp_path / "var" / "lib" / "pyntara" / "tor_ssh_address",
            tor_install_retries=retries,
            tor_start_check_attempts=check_attempts,
            tor_start_check_retry_delay_seconds=0.0,
            ssh_daemon_directives=tuple(directives),
        ),
    )


def _write_torrc(ctx: Context, *, include: bool = False) -> None:
    """Write the main torrc fixture; optionally with the include line."""

    cfg = ctx.config.tor_setup
    cfg.torrc_path.parent.mkdir(parents=True, exist_ok=True)
    content = "Log notice syslog\n"
    if include:
        content += f"%include {cfg.torrc_include_path}\n"
    cfg.torrc_path.write_text(content, encoding="utf-8")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Context,
    *,
    installed: bool = False,
    enabled: bool = False,
    active: bool = False,
    fail_install: int = 0,
    active_becomes: bool = True,
    write_hostname: bool = True,
    tor_user_exists: bool = True,
    verify_ok: bool = True,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query reports the package state, apt-get install fails the
    first fail_install attempts, tor --verify-config reports the given
    result, and systemctl reports the enabled and active state from the
    flags. With active_becomes, the service turns active after the first
    start or restart; without it, the readiness loop runs out. With
    write_hostname, the first start writes the hidden service hostname
    file like a real Tor daemon. With tor_user_exists False,
    pwd.getpwnam raises KeyError.
    """

    calls: list[list[str]] = []
    install_attempts = 0
    started = False

    def fake_getpwnam(name: str) -> SimpleNamespace:
        del name
        if not tor_user_exists:
            raise KeyError("debian-tor")
        return SimpleNamespace(pw_uid=111, pw_gid=111)

    monkeypatch.setattr(tor_setup.pwd, "getpwnam", fake_getpwnam)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal install_attempts, started
        del kwargs
        calls.append(list(command))
        if command[0] == "dpkg-query":
            if installed:
                return _FakeProc(0, "install ok installed\n")
            return _FakeProc(1, "deinstall ok config-files\n")
        if command[0] == "apt-get":
            install_attempts += 1
            if install_attempts <= fail_install:
                raise subprocess.CalledProcessError(100, command)
            # The package postinst creates the main torrc file, like the
            # real tor package on the target system.
            torrc = ctx.config.tor_setup.torrc_path
            torrc.parent.mkdir(parents=True, exist_ok=True)
            if not torrc.is_file():
                torrc.write_text("Log notice syslog\n", encoding="utf-8")
            return _FakeProc(0)
        if command[0] == "runuser":
            # The verification runs as the Tor system user, because Tor
            # rejects a HiddenServiceDir owned by the daemon user when
            # run as root.
            if verify_ok:
                return _FakeProc(0, "Configuration valid\n")
            # Tor reports configuration errors on stdout, not stderr.
            return _FakeProc(1, "Reading config failed--see warnings above.\n", "")
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
                if write_hostname:
                    hidden = ctx.config.tor_setup.hidden_service_dir
                    hidden.mkdir(parents=True, exist_ok=True)
                    (hidden / "hostname").write_text(
                        f"{ADDRESS}\n", encoding="utf-8"
                    )
            return _FakeProc(0)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _write_state_as_rendered(ctx: Context) -> None:
    """Write the drop-in as rendered, the include line, the hostname and
    the saved address."""

    cfg = ctx.config.tor_setup
    ssh_port = tor_setup.ssh_port_from_directives(
        ctx.config.ssh_daemon_setup.directives
    )
    _write_torrc(ctx, include=True)
    cfg.torrc_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.torrc_dropin_path.write_text(
        tor_setup._render_config(cfg, ssh_port), encoding="utf-8"
    )
    cfg.hidden_service_dir.mkdir(parents=True, exist_ok=True)
    (cfg.hidden_service_dir / "hostname").write_text(
        f"{ADDRESS}\n", encoding="utf-8"
    )
    cfg.address_file_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.address_file_path.write_text(f"{ADDRESS}\n", encoding="utf-8")


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package is installed, the include line is present, the drop-in
    # matches its render, the hidden service directory exists, the saved
    # address file matches and the service is enabled and active: the
    # task skips and runs only the status queries.
    ctx = _ctx(tmp_path)
    _write_state_as_rendered(ctx)
    calls = _install_fake(
        monkeypatch, ctx, installed=True, enabled=True, active=True
    )
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "apt-get" for call in calls)
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )
    assert not any(call[0] == "runuser" for call in calls)


def test_installs_and_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Tor is not installed and the service is not enabled: the task
    # installs the package, adds the include line, writes the drop-in,
    # verifies the configuration, prepares the hidden service directory,
    # enables and starts the service, then saves the address.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, ctx)
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ADDRESS in (result.message or "")
    assert ["apt-get", "install", "-y", "tor"] in calls
    assert ["runuser", "-u", "debian-tor", "--", "tor", "--verify-config"] in calls
    assert ["systemctl", "enable", "tor@default.service"] in calls
    assert ["systemctl", "start", "tor@default.service"] in calls
    cfg = ctx.config.tor_setup
    assert cfg.torrc_dropin_path.is_file()
    assert f"%include {cfg.torrc_include_path}" in cfg.torrc_path.read_text(
        encoding="utf-8"
    )
    assert cfg.hidden_service_dir.is_dir()
    assert cfg.address_file_path.read_text(encoding="utf-8").strip() == ADDRESS


def test_include_line_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The target state is reached with the include line already present:
    # the task skips and the main torrc keeps exactly one %include line.
    ctx = _ctx(tmp_path)
    _write_state_as_rendered(ctx)
    _install_fake(monkeypatch, ctx, installed=True, enabled=True, active=True)
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert ctx.config.tor_setup.torrc_path.read_text(
        encoding="utf-8"
    ).count("%include") == 1


def test_dropin_rewritten_when_missing_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package is installed and the service is active but the drop-in
    # is absent: the task writes it, verifies and restarts the running
    # service.
    ctx = _ctx(tmp_path)
    _write_state_as_rendered(ctx)
    ctx.config.tor_setup.torrc_dropin_path.unlink()
    calls = _install_fake(
        monkeypatch, ctx, installed=True, enabled=True, active=True
    )
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["runuser", "-u", "debian-tor", "--", "tor", "--verify-config"] in calls
    assert ["systemctl", "restart", "tor@default.service"] in calls
    assert ["systemctl", "start", "tor@default.service"] not in calls


def test_verify_config_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # tor --verify-config reports an invalid configuration: the task
    # fails instead of silently accepting a broken drop-in, and the
    # message carries the Tor output from stdout.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, ctx, verify_ok=False)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "tor --verify-config" in (result.error or "")
    assert "Reading config failed" in (result.error or "")
    assert not any(
        call[0] == "systemctl" and call[1] in ("start", "restart")
        for call in calls
    )


def test_missing_main_torrc_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package is installed but the main configuration file is absent:
    # the include line cannot be guaranteed, so the task fails instead of
    # pretending the drop-in is connected.
    ctx = _ctx(tmp_path)
    _install_fake(monkeypatch, ctx, installed=True)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "is missing" in (result.error or "")


def test_install_gives_up_after_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # apt always fails: the task tries one initial attempt plus the
    # configured retries, then reports the failure.
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, ctx, fail_install=99)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "cannot install tor" in (result.error or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 4


def test_install_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first apt attempt fails, the retry succeeds.
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, ctx, fail_install=1)
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 2


def test_no_apt_update_without_skip_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The task never refreshes the apt index: add_extra_repos already
    # refreshed it in the same run, so apt-get update never appears.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, ctx)
    result = tor_setup.task(ctx)
    assert result.success is True
    assert not any(call[0] == "apt-get" and call[1] == "update" for call in calls)


def test_first_start_reports_address_appears_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The hostname file does not appear after the first start: the task
    # reports that the address appears after the first start and writes
    # no address file.
    ctx = _ctx(tmp_path)
    _install_fake(monkeypatch, ctx, write_hostname=False)
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert "appears after the first start" in (result.message or "")
    assert not ctx.config.tor_setup.address_file_path.exists()


def test_hidden_service_dir_gets_configured_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The task creates the hidden service directory with the configured
    # mode, so Tor accepts the onion service.
    ctx = _ctx(tmp_path)
    _install_fake(monkeypatch, ctx)
    result = tor_setup.task(ctx)
    assert result.success is True
    mode = stat.S_IMODE(
        ctx.config.tor_setup.hidden_service_dir.stat().st_mode
    )
    assert mode == ctx.config.tor_setup.hidden_service_dir_mode


def test_missing_tor_user_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The configured Tor system user does not exist: the task fails
    # instead of leaving a directory Tor cannot write.
    ctx = _ctx(tmp_path)
    _install_fake(monkeypatch, ctx, tor_user_exists=False)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "tor user" in (result.error or "")


def test_service_that_stays_inactive_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service never reports active within the readiness loop: the
    # task reports the failure instead of a silent success.
    ctx = _ctx(tmp_path)
    _install_fake(monkeypatch, ctx, active_becomes=False)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "did not become active" in (result.error or "")


def test_missing_ssh_port_directive_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ssh_daemon_setup has no Port directive: the forward target is
    # unknown, so the task fails explicitly.
    ctx = _ctx(tmp_path, ssh_port=None)
    _install_fake(monkeypatch, ctx)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "no Port directive" in (result.error or "")


def test_non_numeric_ssh_port_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The sshd Port directive is not a number: the forward target is
    # invalid, so the task fails explicitly.
    ctx = _ctx(tmp_path, ssh_port="abc")
    _install_fake(monkeypatch, ctx)
    result = tor_setup.task(ctx)
    assert result.success is False
    assert "not a number" in (result.error or "")


def test_force_mode_restarts_and_rewrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode rewrites the configuration and restarts the service
    # even when the target state is reached, but never reinstalls the
    # package.
    ctx = _ctx(tmp_path, force=True)
    _write_state_as_rendered(ctx)
    calls = _install_fake(
        monkeypatch, ctx, installed=True, enabled=True, active=True
    )
    result = tor_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "tor@default.service"] in calls
    assert not any(call[0] == "apt-get" for call in calls)


def test_render_config_uses_ssh_port_and_virtual_port(
    tmp_path: Path,
) -> None:
    # The rendered drop-in forwards the virtual port to the local sshd
    # port read from the ssh_daemon_setup directives, and the per-service
    # options follow HiddenServiceDir.
    ctx = _ctx(tmp_path)
    cfg = ctx.config.tor_setup
    ssh_port = tor_setup.ssh_port_from_directives(
        ctx.config.ssh_daemon_setup.directives
    )
    rendered = tor_setup._render_config(cfg, ssh_port)
    assert f"SocksPort 127.0.0.1:{cfg.socks_port}" in rendered
    assert f"HiddenServiceDir {cfg.hidden_service_dir}" in rendered
    assert (
        f"HiddenServiceNumIntroductionPoints {cfg.num_introduction_points}"
        in rendered
    )
    assert (
        f"HiddenServicePort {cfg.onion_ssh_port} 127.0.0.1:{ssh_port}"
        in rendered
    )
    assert rendered.index("HiddenServiceDir") < rendered.index(
        "HiddenServicePort"
    )
    assert rendered.endswith("\n")
