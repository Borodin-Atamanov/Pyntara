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
from support import augtool_fake_run, make_context

from pyntara.context import Context
from pyntara.tasks import ssh_daemon_setup
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values.ssh_daemon_setup import SshDirective

PRIVATE_KEY_BYTES = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
    b"fake encrypted key material\n"
    b"-----END OPENSSH PRIVATE KEY-----\n"
)
PUBLIC_KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake Pyntara_mesh"

PF_PRIVATE_KEY_BYTES = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
    b"fake encrypted port-forwarding key material\n"
    b"-----END OPENSSH PRIVATE KEY-----\n"
)
PF_PUBLIC_KEY_LINE = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePf Pyntara_port_forwarding"
)
PF_OPTIONS = 'restrict,port-forwarding,permitlisten="*"'
PF_AUTHORIZED_LINE = f"{PF_OPTIONS} {PF_PUBLIC_KEY_LINE}"


# The augeas tool package name from the declared values, used by the
# subprocess fake to tell the main package from the augtool package.
AUGTOOL_PACKAGE = "augeas-tools"

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
    SshDirective(name="ClientAliveInterval", value="60"),
    SshDirective(name="ClientAliveCountMax", value="3"),
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
    test describe a single drift without restating the whole file. The
    ownership comment comes from the test document, so the expectation
    follows the config instead of repeating its value.
    """

    lines = [f"# {ssh_daemon_values.DROPIN_HEADER}"]
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    force: bool = False,
    skip_apt_update: bool = True,
    users: tuple[str, ...] = ("i", "j", "k"),
    directives: tuple[SshDirective, ...] = DEFAULT_DIRECTIVES,
) -> Context:
    """Context with a small safe config; the real file is never touched.

    The paths, the user list and the directive list of the section are
    declared values now, so the tests point them at the fixture tree
    through monkeypatch.
    """

    monkeypatch.setattr(
        ssh_daemon_values, "ROOT_SSH_DIR", tmp_path / "root" / ".ssh"
    )
    monkeypatch.setattr(
        ssh_daemon_values, "SSHD_CONFIG_PATH", tmp_path / "etc" / "ssh" / "sshd_config"
    )
    monkeypatch.setattr(
        ssh_daemon_values,
        "SSHD_CONFIG_DROPIN_PATH",
        tmp_path / "etc" / "ssh" / "sshd_config.d" / "pyntara.conf",
    )
    monkeypatch.setattr(ssh_daemon_values, "USERS", users)
    monkeypatch.setattr(ssh_daemon_values, "DIRECTIVES", directives)
    monkeypatch.setattr(ssh_daemon_values, "START_CHECK_RETRY_DELAY_SECONDS", 0.0)
    return make_context(
        task_name="ssh_daemon_setup",
        install_mode="server",
        force_tasks=frozenset({"ssh_daemon_setup"}) if force else frozenset(),
        repo_root=tmp_path,
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
    )


def _install_fixtures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Write the key fixtures; return the task data directory.

    The file names come from the declared values, the same values
    every test uses through _ctx, so the fixtures always match the names
    the task reads from its config.
    """

    data_dir = tmp_path / "task_data" / "ssh_daemon_setup"
    data_dir.mkdir(parents=True)
    (data_dir / ssh_daemon_values.PRIVATE_KEY_FILE_NAME).write_bytes(PRIVATE_KEY_BYTES)
    (data_dir / ssh_daemon_values.PUBLIC_KEY_FILE_NAME).write_text(PUBLIC_KEY_LINE + "\n")
    (data_dir / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME).write_bytes(
        PF_PRIVATE_KEY_BYTES
    )
    (data_dir / ssh_daemon_values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME).write_text(
        PF_PUBLIC_KEY_LINE + "\n"
    )
    return data_dir


def _write_sshd_config(ctx: Context, *, include: bool = True) -> None:
    """Write the fixture sshd_config with an optional Include directive."""

    ssh_daemon_values.SSHD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = "Port 22\n"
    if include:
        content += f"Include {ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent}/*.conf\n"
    ssh_daemon_values.SSHD_CONFIG_PATH.write_text(content, encoding="utf-8")


def _write_dropin_as_desired(ctx: Context) -> None:
    """Write the drop-in exactly as the task would render it."""

    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {ssh_daemon_values.DROPIN_HEADER}"]
    lines.extend(f"{directive.name} {directive.value}" for directive in ssh_daemon_values.DIRECTIVES)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    augeas_installed: bool = True,
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

    dpkg reports the main package state and, when augeas_installed is
    False, reports the augeas package as missing too; apt-get install
    fails the first fail_install attempts, systemctl reports the enabled
    and active
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
            present = augeas_installed if command[-1] == AUGTOOL_PACKAGE else installed
            if present:
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
                'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n',
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

    directories = [ssh_daemon_values.ROOT_SSH_DIR]
    directories.extend(tmp_path / "home" / user / ".ssh" for user in ("i", "j", "k"))
    for ssh_dir in directories:
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / ssh_daemon_values.PRIVATE_KEY_FILE_NAME).write_bytes(PRIVATE_KEY_BYTES)
        (ssh_dir / ssh_daemon_values.PUBLIC_KEY_FILE_NAME).write_text(PUBLIC_KEY_LINE + "\n")
        (ssh_dir / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME).write_bytes(
            PF_PRIVATE_KEY_BYTES
        )
        (ssh_dir / ssh_daemon_values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME).write_text(
            PF_PUBLIC_KEY_LINE + "\n"
        )
        (ssh_dir / "authorized_keys").write_text(
            PUBLIC_KEY_LINE + "\n" + PF_AUTHORIZED_LINE + "\n",
            encoding="utf-8",
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
    ctx = _ctx(monkeypatch, tmp_path)
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
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
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
    ctx = _ctx(monkeypatch, tmp_path)
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
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.read_text(encoding="utf-8") == (
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
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False, fail_install=2)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    install_calls = [call for call in calls if call[:2] == ["apt-get", "install"]]
    assert len(install_calls) == 3


def test_install_fails_after_all_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every install attempt fails: the task reports the failure as a
    # warning of a completed task and still writes the drop-in, because
    # the directives are the part of the machine it owns.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False, fail_install=99)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("cannot install" in warning for warning in result.warnings)
    assert len([c for c in calls if c[:2] == ["apt-get", "install"]]) == 4
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.is_file()


def test_apt_update_runs_unless_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without skip_apt_update the apt index is refreshed before the
    # install; with the flag the refresh is skipped.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path, skip_apt_update=False)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, installed=False)
    ssh_daemon_setup.task(ctx)
    assert ["apt-get", "update"] in calls

    ctx_skipped = _ctx(monkeypatch, tmp_path, skip_apt_update=True)
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
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.read_text(encoding="utf-8") == (
        _expected_dropin_content()
    )
    assert (ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.stat().st_mode & 0o777) == 0o644
    directories = _deploy_keys_directories(ctx, tmp_path)
    for ssh_dir in directories:
        assert (ssh_dir / ssh_daemon_values.PRIVATE_KEY_FILE_NAME).read_bytes() == PRIVATE_KEY_BYTES
        assert (ssh_dir / ssh_daemon_values.PUBLIC_KEY_FILE_NAME).read_text(encoding="utf-8") == (
            PUBLIC_KEY_LINE + "\n"
        )
        assert (ssh_dir / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME).read_bytes() == (
            PF_PRIVATE_KEY_BYTES
        )
        assert (ssh_dir / ssh_daemon_values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME).read_text(
            encoding="utf-8"
        ) == (PF_PUBLIC_KEY_LINE + "\n")
        assert (ssh_dir / "authorized_keys").read_text(encoding="utf-8") == (
            PUBLIC_KEY_LINE + "\n" + PF_AUTHORIZED_LINE + "\n"
        )
        assert (ssh_dir.stat().st_mode & 0o777) == 0o700
        assert (ssh_dir / ssh_daemon_values.PRIVATE_KEY_FILE_NAME).stat().st_mode & 0o777 == 0o600
        assert (ssh_dir / ssh_daemon_values.PUBLIC_KEY_FILE_NAME).stat().st_mode & 0o777 == 0o644
        assert (
            ssh_dir / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
        ).stat().st_mode & 0o777 == 0o600
        assert (ssh_dir / "authorized_keys").stat().st_mode & 0o777 == 0o600


def test_authorized_keys_has_no_duplicates_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second run with everything in place skips; the authorized_keys
    # file keeps a single key line instead of accumulating duplicates.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    first = ssh_daemon_setup.task(ctx)
    assert first.changed is True
    second = ssh_daemon_setup.task(ctx)
    assert second.success is True
    assert second.changed is False
    directories = [ssh_daemon_values.ROOT_SSH_DIR]
    directories.extend(tmp_path / "home" / user / ".ssh" for user in ("i", "j", "k"))
    for ssh_dir in directories:
        lines = (ssh_dir / "authorized_keys").read_text(encoding="utf-8").splitlines()
        assert lines.count(PUBLIC_KEY_LINE) == 1
        assert lines.count(PF_AUTHORIZED_LINE) == 1


def test_missing_user_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A configured user that does not exist is skipped with a log line;
    # the other users and root still get their keys and the task succeeds.
    _install_fixtures(monkeypatch, tmp_path)
    records = {"i": _FakePwRecord("i", 1000, 1000, str(tmp_path / "home" / "i"))}
    monkeypatch.setattr(ssh_daemon_setup, "pwd", _FakePwd(records))
    ctx = _ctx(monkeypatch, tmp_path, users=("i", "ghost"))
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert (tmp_path / "home" / "i" / ".ssh" / "authorized_keys").is_file()
    assert not (tmp_path / "home" / "ghost" / ".ssh").exists()
    assert (ssh_daemon_values.ROOT_SSH_DIR / "authorized_keys").is_file()


def test_missing_include_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sshd_config without an Include covering the drop-in directory is
    # reported as a warning and the drop-in is written anyway: the
    # directives start to work the moment the directive appears.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx, include=False)
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("no Include directive" in warning for warning in result.warnings)
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.is_file()


def test_missing_key_files_are_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A key file missing from the repository data directory is reported
    # as a warning; no key pair is deployed, while the drop-in and the
    # service state are still handled.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    (
        Path(ctx.repo_root)
        / "task_data"
        / "ssh_daemon_setup"
        / ssh_daemon_values.PUBLIC_KEY_FILE_NAME
    ).unlink()
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("missing in" in warning for warning in result.warnings)
    assert not ssh_daemon_values.ROOT_SSH_DIR.exists()
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.is_file()


def test_missing_port_forwarding_key_files_are_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A port-forwarding key file missing from the repository data
    # directory is reported as a warning, so no machine deploys a half
    # mesh, while the rest of the task completes.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch)
    (
        Path(ctx.repo_root)
        / "task_data"
        / "ssh_daemon_setup"
        / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
    ).unlink()
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("port-forwarding key files" in warning for warning in result.warnings)
    assert not ssh_daemon_values.ROOT_SSH_DIR.exists()
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.is_file()


def test_dropin_header_comes_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another header in the [ssh_daemon_setup] table is the ownership
    # comment the rendered drop-in carries, so the value is not a constant
    # of the module.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    monkeypatch.setattr(ssh_daemon_values, "DROPIN_HEADER", "Owned by the test")
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    content = ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.read_text(encoding="utf-8")
    assert content.startswith("# Owned by the test\n")


def test_empty_directives_removes_dropin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty directives list removes the owned drop-in, so the task can
    # revoke its own settings.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path, directives=())
    _write_sshd_config(ctx)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text("Old setting yes\n", encoding="utf-8")
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.exists()


def test_enable_start_and_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The service is disabled and inactive: the task enables and starts it
    # and waits for it to become active.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, enabled=False, active=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "enable", "ssh.service"] in calls
    assert ["systemctl", "start", "ssh.service"] in calls
    assert ["systemctl", "reload", "ssh.service"] not in calls
    assert ["systemctl", "restart", "ssh.service"] not in calls


def test_commands_come_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The daemon query, the listener query and the three systemctl calls of
    # a first run are declared values: another command line in the section is
    # exactly the argv the task runs.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    configured = {
        "EFFECTIVE_CONFIG_COMMAND": ("sshd", "-T", "-C", "user=root"),
        "LISTENING_SOCKETS_COMMAND": ("ss", "-tlnp", "-4"),
        "SOCKET_DISABLE_COMMAND": (
            "systemctl",
            "disable",
            "--now",
            "{socket_unit_name}",
            "--quiet",
        ),
        "SERVICE_ENABLE_COMMAND": (
            "systemctl",
            "enable",
            "{service_unit_name}",
            "--quiet",
        ),
        "SERVICE_START_COMMAND": (
            "systemctl",
            "start",
            "{service_unit_name}",
            "--no-block",
        ),
    }
    for name, command in configured.items():
        monkeypatch.setattr(ssh_daemon_values, name, command)
    calls = _install_fake(monkeypatch, enabled=False, active=False, socket_enabled=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert ["sshd", "-T", "-C", "user=root"] in calls
    assert ["ss", "-tlnp", "-4"] in calls
    assert [
        "systemctl",
        "disable",
        "--now",
        ssh_daemon_values.SOCKET_UNIT_NAME,
        "--quiet",
    ] in calls
    assert [
        "systemctl",
        "enable",
        ssh_daemon_values.SERVICE_UNIT_NAME,
        "--quiet",
    ] in calls
    assert [
        "systemctl",
        "start",
        ssh_daemon_values.SERVICE_UNIT_NAME,
        "--no-block",
    ] in calls


@pytest.mark.parametrize(
    "overrides, outcome",
    [
        ({"Port": "22"}, "restart"),
        ({"PasswordAuthentication": "yes"}, "reload"),
    ],
)
def test_restart_and_reload_commands_come_from_the_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overrides: dict[str, str],
    outcome: str,
) -> None:
    # The restart and the reload of the service are declared values too:
    # another command line in the section is the argv the task runs.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    configured = {
        "SERVICE_RESTART_COMMAND": (
            "systemctl",
            "restart",
            "--no-block",
            "{service_unit_name}",
        ),
        "SERVICE_RELOAD_COMMAND": (
            "systemctl",
            "reload",
            "{service_unit_name}",
            "--quiet",
        ),
    }
    for name, command in configured.items():
        monkeypatch.setattr(ssh_daemon_values, name, command)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text(
        _expected_dropin_content(overrides=overrides),
        encoding="utf-8",
    )
    calls = _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    expected = {
        "restart": [
            "systemctl",
            "restart",
            "--no-block",
            ssh_daemon_values.SERVICE_UNIT_NAME,
        ],
        "reload": [
            "systemctl",
            "reload",
            ssh_daemon_values.SERVICE_UNIT_NAME,
            "--quiet",
        ],
    }[outcome]
    assert expected in calls


def test_reload_when_active_and_non_port_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is active and only a non-port directive changed: the
    # task reloads the daemon, so existing connections survive.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text(
        _expected_dropin_content(overrides={"PasswordAuthentication": "yes"}),
        encoding="utf-8",
    )
    calls = _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "reload", "ssh.service"] in calls
    assert ["systemctl", "restart", "ssh.service"] not in calls


def test_port_change_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The Port directive changed while the service is active: a restart
    # is required, because reload does not rebind the listen socket.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text(
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
    ctx = _ctx(monkeypatch, tmp_path)
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
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _write_dropin_as_desired(ctx)
    _deploy_keys_directories(ctx, tmp_path)
    calls = _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not any(call[0] == "systemctl" and "disable" in call for call in calls)


def test_sshd_t_verification_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The effective configuration reported by sshd -T does not match a
    # configured directive (for example overridden by another file):
    # the task reports it as a warning instead of pretending the state
    # is reached.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, sshd_t_output="port 22\n")
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("sshd -T reports" in warning for warning in result.warnings)


def test_listener_missing_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # After a start nothing listens on the configured port: the task
    # reports the reason in the warnings.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, active=False, ss_port_ok=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("no listener on port" in warning for warning in result.warnings)


def test_reload_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed reload is reported as a warning of a completed task.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    # Only a non-port directive differs, so the task takes the reload
    # path and the failed reload surfaces as a warning.
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text(
        _expected_dropin_content(overrides={"PasswordAuthentication": "yes"}),
        encoding="utf-8",
    )
    _install_fake(monkeypatch, active=True, reload_fails=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("reload failed" in warning for warning in result.warnings)


def test_service_never_becomes_active_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The readiness loop runs out: the task reports the reason.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    _install_fake(monkeypatch, active=False, active_becomes=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("did not become active" in warning for warning in result.warnings)


def test_force_rewrites_dropin_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode rewrites the drop-in and restarts the active service
    # even when everything matches; the installed package is never
    # reinstalled.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path, force=True)
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
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.write_text(
        _expected_dropin_content() + "Banner /etc/issue.net\n",
        encoding="utf-8",
    )
    _install_fake(monkeypatch, active=True)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    content = ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.read_text(encoding="utf-8")
    assert "Banner" not in content
    assert content == _expected_dropin_content()


def test_include_matches_relative_pattern(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A relative Include pattern resolves against the directory of
    # sshd_config and still covers the drop-in.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    ssh_daemon_values.SSHD_CONFIG_PATH.parent.mkdir(parents=True)
    ssh_daemon_values.SSHD_CONFIG_PATH.write_text("Include sshd_config.d/*.conf\n", encoding="utf-8")
    _install_fake(monkeypatch)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True


def test_installs_augtool_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The server package is installed but augtool is missing: the task
    # installs the augeas package itself and reaches the drop-in and the
    # key deployment instead of stopping with a warning.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, augeas_installed=False)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["apt-get", "install", "-y", AUGTOOL_PACKAGE] in calls
    assert ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.read_text(encoding="utf-8") == (
        _expected_dropin_content()
    )
    assert (ssh_daemon_values.ROOT_SSH_DIR / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME).is_file()


def test_augtool_install_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # augtool is missing and the package install fails after all retries:
    # the task reports it as a warning, skips the drop-in alone and still
    # deploys the keys.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, augeas_installed=False, fail_install=99)
    result = ssh_daemon_setup.task(ctx)
    assert result.success is True
    assert any("cannot install" in warning for warning in result.warnings)
    assert any(AUGTOOL_PACKAGE in warning for warning in result.warnings)
    assert len([c for c in calls if c[:2] == ["apt-get", "install"]]) == 4
    assert not ssh_daemon_values.SSHD_CONFIG_DROPIN_PATH.exists()
    assert (ssh_daemon_values.ROOT_SSH_DIR / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME).is_file()


def test_augtool_install_respects_apt_update_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The augtool package install honours PYNTARA_SKIP_APT_UPDATE: the
    # apt index is refreshed without the flag and skipped with it.
    _install_fixtures(monkeypatch, tmp_path)
    _install_users(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path, skip_apt_update=False)
    _write_sshd_config(ctx)
    calls = _install_fake(monkeypatch, augeas_installed=False)
    ssh_daemon_setup.task(ctx)
    assert ["apt-get", "update"] in calls

    ctx_skipped = _ctx(monkeypatch, tmp_path, skip_apt_update=True)
    calls_skipped = _install_fake(monkeypatch, augeas_installed=False)
    ssh_daemon_setup.task(ctx_skipped)
    assert ["apt-get", "update"] not in calls_skipped
