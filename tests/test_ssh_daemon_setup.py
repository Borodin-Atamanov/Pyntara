"""Unit tests for the ssh_daemon_setup task.

All external resources (subprocess, filesystem paths, user database) are
mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The key files are fixtures, so the
tests never read the repository keys. The augtool subprocess is faked
with a small lens simulator that parses and writes the real drop-in
file, so the augeas interaction is covered end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import augtool_fake_run, make_config, make_context

from pyntara.config import SshDirective
from pyntara.context import Context
from pyntara.tasks import ssh_daemon_setup

PRIVATE_KEY_BYTES = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
    b"fake encrypted key material\n"
    b"-----END OPENSSH PRIVATE KEY-----\n"
)
PUBLIC_KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake Pyntara_mesh"

DROPIN_HEADER = "# Managed by the Pyntara ssh_daemon_setup task."

DEFAULT_DIRECTIVES = (
    SshDirective(name="Port", value="30222"),
    SshDirective(name="PubkeyAuthentication", value="yes"),
    SshDirective(name="PermitRootLogin", value="prohibit-password"),
    SshDirective(name="PasswordAuthentication", value="no"),
    SshDirective(name="X11Forwarding", value="yes"),
    SshDirective(name="UseDNS", value="no"),
    SshDirective(name="PermitTunnel", value="yes"),
    SshDirective(name="MaxStartups", value="11:30:151"),
    SshDirective(name="LoginGraceTime", value="360"),
    SshDirective(name="GatewayPorts", value="yes"),
    SshDirective(name="Compression", value="yes"),
    SshDirective(name="ClientAliveInterval", value="157"),
    SshDirective(name="ClientAliveCountMax", value="17"),
    SshDirective(name="AllowTcpForwarding", value="yes"),
    SshDirective(name="AddressFamily", value="any"),
)

SSHD_T_LINES = "".join(
    f"{directive.name.lower()} {directive.value.lower()}\n"
    for directive in DEFAULT_DIRECTIVES
)


def _expected_dropin_content(*, overrides: dict[str, str] | None = None) -> str:
    """The drop-in exactly as the task renders the default directives.

    A directive in overrides replaces the default value, which lets a
    test describe a single drift without restating the whole file.
    """

    lines = [DROPIN_HEADER]
    for directive in DEFAULT_DIRECTIVES:
        value = (overrides or {}).get(directive.name, directive.value)
        lines.append(f"{directive.name} {value}")
    return "\n".join(lines) + "\n"


class _FakePwRecord:
    """Stand-in for pwd.struct_passwd: only the fields used by the task."""

    def __init__(self, name: str, uid: int, gid: int, home: str) -> None:
        self.pw_name = name
        self.pw_uid = uid
        self.pw_gid = gid
        self.pw_dir = home


class _FakePwd:
    """Stand-in for the pwd module: getpwnam over a fixed user table."""

    def __init__(self, users: dict[str, _FakePwRecord]) -> None:
        self._users = users

    def getpwnam(self, user: str) -> _FakePwRecord:
        if user not in self._users:
            raise KeyError(user)
        return self._users[user]


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    skip_apt_update: bool = True,
    users: tuple[str, ...] = ("i", "j", "k"),
    directives: tuple[SshDirective, ...] = DEFAULT_DIRECTIVES,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"ssh_daemon_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
        config=make_config(
            task_data_root=tmp_path,
            ssh_daemon_root_ssh_dir=tmp_path / "root" / ".ssh",
            ssh_daemon_sshd_config_path=tmp_path / "etc" / "ssh" / "sshd_config",
            ssh_daemon_sshd_config_dropin_path=(
                tmp_path / "etc" / "ssh" / "sshd_config.d" / "pyntara.conf"
            ),
            ssh_daemon_users=users,
            ssh_daemon_directives=directives,
            ssh_daemon_start_check_retry_delay_seconds=0.0,
        ),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Write the key fixtures; return the task data directory.

    The file names come from the make_config defaults, the same defaults
    every test uses through _ctx, so the fixtures always match the names
    the task reads from its config.
    """

    cfg = make_config().ssh_daemon_setup
    data_dir = tmp_path / "task_data" / "ssh_daemon_setup"
    data_dir.mkdir(parents=True)
    (data_dir / cfg.private_key_file_name).write_bytes(PRIVATE_KEY_BYTES)
    (data_dir / cfg.public_key_file_name).write_text(PUBLIC_KEY_LINE + "\n")
    monkeypatch.setattr(ssh_daemon_setup, "SSH_DATA_DIR", data_dir)
    return data_dir


def _write_sshd_config(ctx: Context, *, include: bool = True) -> None:
    """Write the fixture sshd_config with an optional Include directive."""

    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_path.parent.mkdir(parents=True, exist_ok=True)
    content = "Port 22\n"
    if include:
        content += f"Include {cfg.sshd_config_dropin_path.parent}/*.conf\n"
    cfg.sshd_config_path.write_text(content, encoding="utf-8")


def _write_dropin_as_desired(ctx: Context) -> None:
    """Write the drop-in exactly as the task would render it."""

    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [DROPIN_HEADER]
    lines.extend(f"{directive.name} {directive.value}" for directive in cfg.directives)
    cfg.sshd_config_dropin_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    enabled: bool = True,
    active: bool = True,
    socket_enabled: bool = False,
    socket_active: bool = False,
    fail_install: int = 0,
    active_becomes: bool = True,
    reload_fails: bool = False,
    sshd_t_output: str = SSHD_T_LINES,
    ss_port_ok: bool = True,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg reports the package state, apt-get install fails the first
    fail_install attempts, systemctl reports the enabled and active
    states of the service and the socket from the flags, sshd -T
    prints sshd_t_output, ss -tlnp reports the configured listener
    unless ss_port_ok is False, and augtool is simulated over the real
    drop-in file. With active_becomes, the service turns active after
    the first start; without it, the readiness loop runs out. With
    reload_fails, systemctl reload raises like an unsupported or failed
    reload.
    """

    calls: list[list[str]] = []
    install_attempts = 0
    started = False

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        nonlocal install_attempts, started
        if command[0] == "augtool":
            return augtool_fake_run(command, kwargs.get("input"))
        del kwargs
        calls.append(list(command))
        if command[0] == "dpkg-query":
            if installed:
                return _FakeProc(0, "install ok installed\n")
            return _FakeProc(1, "deinstall ok config-files\n")
        if command[0] == "apt-get":
            if command[1] == "install":
                install_attempts += 1
                if install_attempts <= fail_install:
                    raise subprocess.CalledProcessError(100, command)
            return _FakeProc(0)
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                if command[-1] == "ssh.socket":
                    if socket_enabled:
                        return _FakeProc(0, "enabled\n")
                    return _FakeProc(1, "disabled\n")
                if enabled:
                    return _FakeProc(0, "enabled\n")
                return _FakeProc(1, "disabled\n")
            if command[1] == "is-active":
                if command[-1] == "ssh.socket":
                    if socket_active:
                        return _FakeProc(0, "active\n")
                    return _FakeProc(1, "inactive\n")
                if active or (active_becomes and started):
                    return _FakeProc(0, "active\n")
                return _FakeProc(1, "inactive\n")
            if command[1] == "start":
                started = True
            if command[1] == "reload" and reload_fails:
                raise subprocess.CalledProcessError(5, command)
            return _FakeProc(0)
        if command[0] == "sshd":
            return _FakeProc(0, sshd_t_output)
        if command[0] == "ss":
            if ss_port_ok:
                return _FakeProc(
                    0,
                    "LISTEN 0 4096 0.0.0.0:30222 0.0.0.0:* "
                    'users:(("sshd",pid=1,fd=3))\n',
                )
            return _FakeProc(
                0,
                "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* "
                'users:(("sshd",pid=1,fd=3))\n',
            )
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _install_users(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Install the fake user database for users i, j and k."""

    records = {
        "i": _FakePwRecord("i", 1000, 1000, str(tmp_path / "home" / "i")),
        "j": _FakePwRecord("j", 1001, 1001, str(tmp_path / "home" / "j")),
        "k": _FakePwRecord("k", 1002, 1002, str(tmp_path / "home" / "k")),
    }
    monkeypatch.setattr(ssh_daemon_setup, "pwd", _FakePwd(records))


def _deploy_keys_directories(ctx: Context, tmp_path: Path) -> list[Path]:
    """Pre-deploy the keys into root and every configured user .ssh dir."""

    cfg = ctx.config.ssh_daemon_setup
    directories = [cfg.root_ssh_dir]
    directories.extend(
        tmp_path / "home" / user / ".ssh" for user in ("i", "j", "k")
    )
    for ssh_dir in directories:
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / cfg.private_key_file_name).write_bytes(PRIVATE_KEY_BYTES)
        (ssh_dir / cfg.public_key_file_name).write_text(PUBLIC_KEY_LINE + "\n")
        (ssh_dir / "authorized_keys").write_text(
            PUBLIC_KEY_LINE + "\n", encoding="utf-8"
        )
    return directories


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package is installed, the Include is present, the drop-in
    # matches through augeas, the socket is disabled, the keys are in
    # place and the service is enabled and active: the task skips and
    # runs only the status queries.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _write_dropin_as_desired(ctx)
    _deploy_keys_directories(ctx, tmp_path)
    calls = _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "apt-get" for call in calls)
    assert not any(
        call[0] == "systemctl"
        and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )


def test_installs_package_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package is missing: the task installs it, writes the drop-in
    # through augeas, enables and starts the service and verifies the
    # effective configuration and the listener.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False, enabled=False, active=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["apt-get", "install", "-y", "openssh-server"] in calls
    assert ["systemctl", "enable", "ssh.service"] in calls
    assert ["systemctl", "start", "ssh.service"] in calls
    assert ["sshd", "-T"] in calls
    assert ["ss", "-tlnp"] in calls
    cfg = ctx.config.ssh_daemon_setup
    assert cfg.sshd_config_dropin_path.read_text(encoding="utf-8") == (
        _expected_dropin_content()
    )
    assert "openssh-server" in (result.message or "")


def test_install_retries_after_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # apt-get install fails the first attempts; the task retries until the
    # configured retry count is exhausted, then succeeds.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False, fail_install=2)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    install_calls = [
        call for call in calls if call[:2] == ["apt-get", "install"]
    ]
    assert len(install_calls) == 3


def test_install_fails_after_all_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every install attempt fails: the task returns an error result.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False, fail_install=99)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "cannot install" in (result.error or "")
    assert len([c for c in calls if c[:2] == ["apt-get", "install"]]) == 4


def test_apt_update_runs_unless_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without skip_apt_update the apt index is refreshed before the
    # install; with the flag the refresh is skipped.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, skip_apt_update=False)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False)
    ssh_daemon_setup.task(ctx)
    assert ["apt-get", "update"] in calls

    ctx_skipped = _ctx(tmp_path, skip_apt_update=True)
    calls_skipped = _install_fake(monkeypatch, installed=False)
    ssh_daemon_setup.task(ctx_skipped)
    assert ["apt-get", "update"] not in calls_skipped


def test_writes_dropin_and_deploys_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The task writes the drop-in through augeas and deploys the keys to
    # root and to every existing user with the configured modes.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    cfg = ctx.config.ssh_daemon_setup
    assert cfg.sshd_config_dropin_path.read_text(encoding="utf-8") == (
        _expected_dropin_content()
    )
    assert (cfg.sshd_config_dropin_path.stat().st_mode & 0o777) == 0o644
    directories = _deploy_keys_directories(ctx, tmp_path)
    cfg = ctx.config.ssh_daemon_setup
    for ssh_dir in directories:
        assert (ssh_dir / cfg.private_key_file_name).read_bytes() == PRIVATE_KEY_BYTES
        assert (ssh_dir / cfg.public_key_file_name).read_text(encoding="utf-8") == (
            PUBLIC_KEY_LINE + "\n"
        )
        assert (ssh_dir / "authorized_keys").read_text(encoding="utf-8") == (
            PUBLIC_KEY_LINE + "\n"
        )
        assert (ssh_dir.stat().st_mode & 0o777) == 0o700
        assert (ssh_dir / cfg.private_key_file_name).stat().st_mode & 0o777 == 0o600
        assert (ssh_dir / cfg.public_key_file_name).stat().st_mode & 0o777 == 0o644
        assert (ssh_dir / "authorized_keys").stat().st_mode & 0o777 == 0o600


def test_authorized_keys_has_no_duplicates_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second run with everything in place skips; the authorized_keys
    # file keeps a single key line instead of accumulating duplicates.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    first = ssh_daemon_setup.task(ctx)
    assert first.changed is True
    second = ssh_daemon_setup.task(ctx)
    assert second.success is True
    assert second.changed is False
    cfg = ctx.config.ssh_daemon_setup
    directories = [cfg.root_ssh_dir]
    directories.extend(
        tmp_path / "home" / user / ".ssh" for user in ("i", "j", "k")
    )
    for ssh_dir in directories:
        lines = (ssh_dir / "authorized_keys").read_text(encoding="utf-8").splitlines()
        assert lines.count(PUBLIC_KEY_LINE) == 1


def test_missing_user_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A configured user that does not exist is skipped with a log line;
    # the other users and root still get their keys and the task succeeds.
    _install_fixtures(monkeypatch, tmp_path)
    records = {"i": _FakePwRecord("i", 1000, 1000, str(tmp_path / "home" / "i"))}
    monkeypatch.setattr(ssh_daemon_setup, "pwd", _FakePwd(records))
    ctx = _ctx(tmp_path, users=("i", "ghost"))
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    cfg = ctx.config.ssh_daemon_setup
    assert (tmp_path / "home" / "i" / ".ssh" / "authorized_keys").is_file()
    assert not (tmp_path / "home" / "ghost" / ".ssh").exists()
    assert (cfg.root_ssh_dir / "authorized_keys").is_file()


def test_missing_include_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sshd_config without an Include covering the drop-in directory means
    # the drop-in would be ignored: the task fails loudly.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx, include=False)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "no Include directive" in (result.error or "")


def test_missing_key_files_are_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A key file missing from the repository data directory stops the
    # task with an explicit error instead of deploying half a key pair.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    cfg = ctx.config.ssh_daemon_setup
    (Path(ssh_daemon_setup.SSH_DATA_DIR) / cfg.public_key_file_name).unlink()
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "missing in" in (result.error or "")


def test_empty_directives_removes_dropin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty directives list removes the owned drop-in, so the task can
    # revoke its own settings.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, directives=())
    _write_sshd_config(ctx)
    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True)
    cfg.sshd_config_dropin_path.write_text("Old setting yes\n", encoding="utf-8")
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not cfg.sshd_config_dropin_path.exists()


def test_enable_start_and_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is disabled and inactive: the task enables and starts it
    # and waits for it to become active.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, enabled=False, active=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "enable", "ssh.service"] in calls
    assert ["systemctl", "start", "ssh.service"] in calls
    assert ["systemctl", "reload", "ssh.service"] not in calls
    assert ["systemctl", "restart", "ssh.service"] not in calls


def test_reload_when_active_and_non_port_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is active and only a non-port directive changed: the
    # task reloads the daemon, so existing connections survive.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True)
    cfg.sshd_config_dropin_path.write_text(
        _expected_dropin_content(overrides={"PasswordAuthentication": "yes"}),
        encoding="utf-8",
    )
    calls = _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "reload", "ssh.service"] in calls
    assert ["systemctl", "restart", "ssh.service"] not in calls


def test_port_change_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Port directive changed while the service is active: a restart
    # is required, because reload does not rebind the listen socket.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True)
    cfg.sshd_config_dropin_path.write_text(
        _expected_dropin_content(overrides={"Port": "22"}),
        encoding="utf-8",
    )
    calls = _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "ssh.service"] in calls
    assert ["systemctl", "reload", "ssh.service"] not in calls


def test_socket_disabled_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The socket owns the listen port, so it must be disabled for the
    # configured port to take effect; the running service is restarted
    # afterwards, because it still holds the socket file descriptor.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _write_dropin_as_desired(ctx)
    _deploy_keys_directories(ctx, tmp_path)
    calls = _install_fake(
        monkeypatch, socket_enabled=True, socket_active=True, active=True
    )
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "disable", "--now", "ssh.socket"] in calls
    assert ["systemctl", "restart", "ssh.service"] in calls


def test_socket_untouched_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A disabled and inactive socket is part of the target state: the
    # task skips without touching it.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _write_dropin_as_desired(ctx)
    _deploy_keys_directories(ctx, tmp_path)
    calls = _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not any(
        call[0] == "systemctl" and "disable" in call for call in calls
    )


def test_sshd_t_verification_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The effective configuration reported by sshd -T does not match a
    # configured directive (for example overridden by another file):
    # the task fails loudly instead of pretending the state is reached.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, sshd_t_output="port 22\n")
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "sshd -T reports" in (result.error or "")


def test_listener_missing_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # After a start nothing listens on the configured port: the task
    # reports the failure instead of claiming success.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, active=False, ss_port_ok=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "no listener on port" in (result.error or "")


def test_reload_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed reload is an explicit error result, not a silent skip.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    cfg = ctx.config.ssh_daemon_setup
    # Only a non-port directive differs, so the task takes the reload
    # path and the failed reload surfaces as an error.
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True)
    cfg.sshd_config_dropin_path.write_text(
        _expected_dropin_content(overrides={"PasswordAuthentication": "yes"}),
        encoding="utf-8",
    )
    _install_fake(monkeypatch, active=True, reload_fails=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "reload failed" in (result.error or "")


def test_service_never_becomes_active_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The readiness loop runs out: the task reports the failure.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, active=False, active_becomes=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is False
    assert "did not become active" in (result.error or "")


def test_force_rewrites_dropin_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode rewrites the drop-in and restarts the active service
    # even when everything matches; the installed package is never
    # reinstalled.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, force=True)
    _write_sshd_config(ctx)
    _write_dropin_as_desired(ctx)
    _deploy_keys_directories(ctx, tmp_path)
    calls = _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "ssh.service"] in calls
    assert not any(call[:2] == ["apt-get", "install"] for call in calls)


def test_augtool_removes_stale_directive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directive that is no longer configured is removed from the
    # drop-in by augeas; the remaining file keeps the desired state.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _write_sshd_config(ctx)
    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_dropin_path.parent.mkdir(parents=True)
    cfg.sshd_config_dropin_path.write_text(
        _expected_dropin_content() + "Banner /etc/issue.net\n",
        encoding="utf-8",
    )
    _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    content = cfg.sshd_config_dropin_path.read_text(encoding="utf-8")
    assert "Banner" not in content
    assert content == _expected_dropin_content()


def test_include_matches_relative_pattern(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A relative Include pattern resolves against the directory of
    # sshd_config and still covers the drop-in.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    cfg = ctx.config.ssh_daemon_setup
    cfg.sshd_config_path.parent.mkdir(parents=True)
    cfg.sshd_config_path.write_text(
        "Include sshd_config.d/*.conf\n", encoding="utf-8"
    )
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
