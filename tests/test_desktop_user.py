"""Tests for the desktop account resolution of the run.

The run resolves the desktop account of the machine once before the tasks and
overwrites the shared pair, so one package provisions any account. Every probe
of the resolver is replaced here: no test asks the real machine who is logged
in, and no test writes the shared values.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pyntara.values.kde_keyboard_setup as kde_keyboard_values
from pyntara import pyntara as pyntara_module
from pyntara.pyntara import get_desktop_username_and_home
from pyntara.values import common as common_values

FALLBACK_PAIR = (common_values.DESKTOP_USERNAME, common_values.DESKTOP_HOME_DIR)


class _FakePwdModule:
    """Stand-in for pwd: getpwnam and getpwall over a fixed account table."""

    def __init__(self, accounts: tuple[SimpleNamespace, ...]) -> None:
        self._accounts = {account.pw_name: account for account in accounts}

    def getpwnam(self, name: str) -> SimpleNamespace:
        try:
            return self._accounts[name]
        except KeyError:
            raise KeyError(name) from None

    def getpwall(self) -> list[SimpleNamespace]:
        return list(self._accounts.values())


def _account(name: str, uid: int, home: str) -> SimpleNamespace:
    return SimpleNamespace(pw_name=name, pw_uid=uid, pw_dir=home)


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


@pytest.fixture(autouse=True)
def _no_environment_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without the two environment signals."""

    monkeypatch.delenv("PYNTARA_DESKTOP_USER", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)


@pytest.fixture
def report_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the messages the resolver reports, without touching the logger."""

    messages: list[str] = []
    monkeypatch.setattr(
        pyntara_module,
        "log_event",
        lambda message, **kwargs: messages.append(message),
    )
    return messages


def test_the_override_names_the_desktop_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYNTARA_DESKTOP_USER", "kubuntu")
    monkeypatch.setenv("SUDO_USER", "someone-else")
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_the_sudo_invoker_names_the_desktop_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUDO_USER", "kubuntu")
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_the_seated_session_names_the_desktop_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: "/usr/bin/loginctl")

    def fake_run_command(command, **kwargs):
        if "list-sessions" in command:
            return _completed(" 1 1000 kubuntu seat0 1736 user tty1 no -\n")
        return _completed("Name=kubuntu\nSeat=seat0\nClass=user\n")

    monkeypatch.setattr(pyntara_module, "run_command", fake_run_command)
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_a_session_without_a_seat_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: "/usr/bin/loginctl")

    def fake_run_command(command, **kwargs):
        if "list-sessions" in command:
            return _completed(" 2 1000 kubuntu - 1742 manager - no -\n")
        return _completed("Name=kubuntu\nSeat=\nClass=user\n")

    monkeypatch.setattr(pyntara_module, "run_command", fake_run_command)
    # No seated session, but the machine carries one human account.
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_the_single_human_account_names_the_desktop_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule(
            (
                _account("root", 0, "/root"),
                _account("kubuntu", 1000, "/home/kubuntu"),
            )
        ),
    )
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_several_human_accounts_keep_the_declared_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule(
            (
                _account("first", 1000, "/home/first"),
                _account("second", 1001, "/home/second"),
            )
        ),
    )
    assert get_desktop_username_and_home() == FALLBACK_PAIR


def test_an_unknown_account_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYNTARA_DESKTOP_USER", "ghost")
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    assert get_desktop_username_and_home() == ("kubuntu", "/home/kubuntu")


def test_a_failed_query_keeps_the_declared_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: "/usr/bin/loginctl")

    def fake_run_command(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(pyntara_module, "run_command", fake_run_command)
    monkeypatch.setattr(pyntara_module, "pwd", _FakePwdModule(()))
    assert get_desktop_username_and_home() == FALLBACK_PAIR


def test_the_resolution_reports_the_chosen_account(
    monkeypatch: pytest.MonkeyPatch, report_log: list[str]
) -> None:
    monkeypatch.setenv("PYNTARA_DESKTOP_USER", "kubuntu")
    monkeypatch.setattr(
        pyntara_module,
        "pwd",
        _FakePwdModule((_account("kubuntu", 1000, "/home/kubuntu"),)),
    )
    get_desktop_username_and_home()
    assert any("kubuntu" in message and "/home/kubuntu" in message for message in report_log)


def test_the_resolution_reports_the_declared_fallback(
    monkeypatch: pytest.MonkeyPatch, report_log: list[str]
) -> None:
    monkeypatch.setattr(pyntara_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pyntara_module, "pwd", _FakePwdModule(()))
    get_desktop_username_and_home()
    assert any("No desktop user detected" in message for message in report_log)


def _package_source_paths() -> list[Path]:
    package_file = pyntara_module.__file__
    assert package_file is not None
    root = Path(package_file).resolve().parent
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def test_no_module_binds_the_desktop_pair_by_value() -> None:
    # The run resolves the pair by assigning the shared module attributes, so a
    # module that imports the names by value would keep the stale fallback. The
    # pair must always be read through the module.
    offenders: list[str] = []
    for path in _package_source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pyntara.values.common":
                for imported in node.names:
                    if imported.name in ("DESKTOP_USERNAME", "DESKTOP_HOME_DIR"):
                        offenders.append(f"{path.name}: {imported.name}")
    assert not offenders, f"the desktop pair is imported by value: {offenders}"


def test_a_values_module_derives_its_path_from_the_resolved_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The main risk of the design: a values module that derives a path at import
    # time must see the resolved home. The module is executed fresh under the
    # resolved pair, so the derivation is proved without touching the shipped
    # module object.
    resolved_home = tmp_path / "home" / "kubuntu"
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(resolved_home))
    source = Path(kde_keyboard_values.__file__)
    spec = importlib.util.spec_from_file_location("kde_keyboard_values_probe", source)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.CONFIG_DIR == resolved_home / ".config"
