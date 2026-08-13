"""Unit tests for the ssh_client_setup task.

All external resources (subprocess, filesystem paths) are mocked via
monkeypatch; the tests only touch temporary fixtures. The augtool
subprocess is faked with the shared lens simulator over the real
drop-in file, so the Host block rendering is covered end to end, and
ssh -G with a fixed effective-config output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import augtool_fake_run, make_config, make_context

from pyntara.config import SshDirective
from pyntara.context import Context
from pyntara.tasks import ssh_client_setup

DROPIN_HEADER = "# Managed by the Pyntara ssh_client_setup task."

DEFAULT_DIRECTIVES = (
    SshDirective(name="AddressFamily", value="any"),
    SshDirective(name="CheckHostIP", value="no"),
    SshDirective(name="Compression", value="yes"),
    SshDirective(name="ConnectionAttempts", value="17"),
    SshDirective(name="ConnectTimeout", value="31"),
    SshDirective(name="NumberOfPasswordPrompts", value="5"),
    SshDirective(name="PasswordAuthentication", value="yes"),
    SshDirective(name="TCPKeepAlive", value="yes"),
    SshDirective(name="ServerAliveInterval", value="61"),
    SshDirective(name="ServerAliveCountMax", value="17"),
    SshDirective(name="PreferredAuthentications", value="publickey,password"),
)

SSH_G_LINES = "".join(
    f"{directive.name.lower()} {directive.value.lower()}\n"
    for directive in DEFAULT_DIRECTIVES
)


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    directives: tuple[SshDirective, ...] = DEFAULT_DIRECTIVES,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"ssh_client_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            ssh_client_ssh_config_path=tmp_path / "etc" / "ssh" / "ssh_config",
            ssh_client_ssh_config_dropin_path=(
                tmp_path / "etc" / "ssh" / "ssh_config.d" / "pyntara.conf"
            ),
            ssh_client_directives=directives,
        ),
    )


def _write_ssh_config(ctx: Context, *, include: bool = True) -> None:
    """Write the fixture ssh_config with an optional Include directive."""

    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
    content = "Host *\n"
    if include:
        content += f"Include {cfg.ssh_config_dropin_path.parent}/*.conf\n"
    cfg.ssh_config_path.write_text(content, encoding="utf-8")


def _expected_dropin_content(*, overrides: dict[str, str] | None = None) -> str:
    """The drop-in exactly as the task renders the default directives.

    A directive in overrides replaces the default value, which lets a
    test describe a single drift without restating the whole file.
    """

    lines = [DROPIN_HEADER, "Host *"]
    for directive in DEFAULT_DIRECTIVES:
        value = (overrides or {}).get(directive.name, directive.value)
        lines.append(f"\t{directive.name} {value}")
    return "\n".join(lines) + "\n"


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ssh_g_output: str = SSH_G_LINES,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    augtool is simulated over the real drop-in file and ssh -G prints
    ssh_g_output; every other command is recorded and answered with
    success.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "augtool":
            return augtool_fake_run(command, kwargs.get("input"))
        del kwargs
        calls.append(list(command))
        if command[0] == "ssh":
            return _FakeProc(0, ssh_g_output)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_syncs_dropin_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No drop-in yet: the task writes the Host block with every
    # configured directive and verifies the effective configuration.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx)
    calls = _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    cfg = ctx.config.ssh_client_setup
    assert cfg.ssh_config_dropin_path.read_text(encoding="utf-8") == (
        _expected_dropin_content()
    )
    assert (cfg.ssh_config_dropin_path.stat().st_mode & 0o777) == 0o644
    assert ["ssh", "-G", "example.com"] in calls


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Include is present and the drop-in matches: the task skips and
    # never runs ssh -G.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx)
    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ssh_config_dropin_path.write_text(
        _expected_dropin_content(), encoding="utf-8"
    )
    calls = _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "ssh" for call in calls)


def test_force_rewrites_and_verifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode rewrites the drop-in and verifies it again even when
    # everything matches.
    ctx = _ctx(tmp_path, force=True)
    _write_ssh_config(ctx)
    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ssh_config_dropin_path.write_text(
        _expected_dropin_content(), encoding="utf-8"
    )
    calls = _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["ssh", "-G", "example.com"] in calls


def test_removes_stale_directive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directive that is no longer configured is removed from the
    # drop-in by augeas; the remaining file keeps the desired state.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx)
    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ssh_config_dropin_path.write_text(
        _expected_dropin_content() + "\tBanner /etc/issue.net\n",
        encoding="utf-8",
    )
    _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    content = cfg.ssh_config_dropin_path.read_text(encoding="utf-8")
    assert "Banner" not in content
    assert content == _expected_dropin_content()


def test_updates_changed_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directive with a different value is updated in place, the Host
    # block is not duplicated.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx)
    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ssh_config_dropin_path.write_text(
        _expected_dropin_content(overrides={"ConnectTimeout": "10"}),
        encoding="utf-8",
    )
    _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    content = cfg.ssh_config_dropin_path.read_text(encoding="utf-8")
    assert content == _expected_dropin_content()
    assert content.count("Host *") == 1


def test_empty_directives_removes_dropin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty directives list removes the owned drop-in.
    ctx = _ctx(tmp_path, directives=())
    _write_ssh_config(ctx)
    cfg = ctx.config.ssh_client_setup
    cfg.ssh_config_dropin_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ssh_config_dropin_path.write_text(
        _expected_dropin_content(), encoding="utf-8"
    )
    _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is True
    assert not cfg.ssh_config_dropin_path.exists()


def test_missing_include_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ssh_config without an Include covering the drop-in directory means
    # the drop-in would be ignored: the task fails loudly.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx, include=False)
    _install_fake(monkeypatch)
    result = ssh_client_setup.task(ctx)
    assert result.success is False
    assert "no Include directive" in (result.error or "")


def test_verify_reports_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ssh -G misses a configured directive: the task reports the drift
    # as an error instead of a silent success.
    ctx = _ctx(tmp_path)
    _write_ssh_config(ctx)
    ssh_g_output = "".join(
        f"{directive.name.lower()} {directive.value.lower()}\n"
        for directive in DEFAULT_DIRECTIVES
        if directive.name != "AddressFamily"
    )
    _install_fake(monkeypatch, ssh_g_output=ssh_g_output)
    result = ssh_client_setup.task(ctx)
    assert result.success is False
    assert "ssh -G reports addressfamily as unset" in (result.error or "")
