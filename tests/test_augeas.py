"""Unit tests for the shared augeas helpers.

The helpers drive an external tool, so the tests replace run_command and
read only the argv and the script the helper builds; no file is written
by the fake.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeProc as _FakeProc

from pyntara import augeas as augeas_module
from pyntara.values import engine as engine_values


def test_the_tool_and_the_node_prefix_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The augtool argv and the node prefix of its program are declared
    # values: another command and another prefix are the argv and the script
    # the helper builds, so the tool vocabulary lives in one place for the
    # two ssh tasks while the reading still parses what the tool printed.
    monkeypatch.setattr(
        engine_values, "AUGTOOL_COMMAND", ("myaugtool", "--noautoload", "--nobackup")
    )
    monkeypatch.setattr(engine_values, "AUGEAS_FILES_NODE_PREFIX", "/myfiles")
    dropin_path = Path("/etc/ssh/sshd_config.d/pyntara.conf")
    node = f"{engine_values.AUGEAS_FILES_NODE_PREFIX}{dropin_path}"
    captured: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        captured.append({"command": command, **kwargs})
        listing = f'{node}/#comment = "owned by the test"\n{node}/Port = "30222"\n'
        return _FakeProc(0, listing)

    monkeypatch.setattr(augeas_module, "run_command", fake_run)
    directives, comment = augeas_module.read_dropin_state(
        dropin_path, "Sshd.lns", 30.0, "#"
    )
    assert captured[0]["command"] == [
        "myaugtool",
        "--noautoload",
        "--nobackup",
    ]
    assert f"print {node}\n" in str(captured[0]["input"])
    assert comment == "owned by the test"
    assert directives == {"Port": "30222"}


def test_the_comment_label_sign_comes_from_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sign a comment node of the augtool listing carries is an argument of
    # the caller: another sign means the ownership comment is not recognized,
    # so the caller decides how a comment is spelled and a task whose files
    # carry it differently is answered there.
    dropin_path = Path("/etc/ssh/sshd_config.d/pyntara.conf")
    node = f"{engine_values.AUGEAS_FILES_NODE_PREFIX}{dropin_path}"

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        listing = f'{node}/#comment = "owned by the test"\n{node}/Port = "30222"\n'
        return _FakeProc(0, listing)

    monkeypatch.setattr(augeas_module, "run_command", fake_run)
    directives, comment = augeas_module.read_dropin_state(
        dropin_path, "Sshd.lns", 30.0, ";"
    )
    assert comment is None
    assert directives == {"#comment": "owned by the test", "Port": "30222"}


def test_the_include_keyword_comes_from_the_config(tmp_path: Path) -> None:
    # The keyword that pulls the drop-in in belongs to the syntax of the
    # edited file and is a config value: another keyword in the table finds
    # another directive, while the shipped one finds nothing of it.
    config = tmp_path / "my_config"
    config.write_text(
        "# managed\nPullIn /etc/ssh/sshd_config.d/*.conf\n", encoding="utf-8"
    )
    dropin = Path("/etc/ssh/sshd_config.d/pyntara.conf")
    assert augeas_module.include_covers_dropin(config, dropin, "#", "PullIn") is True
    assert augeas_module.include_covers_dropin(config, dropin, "#", "Include") is False
    commented = tmp_path / "other_config"
    commented.write_text("# PullIn /etc/ssh/sshd_config.d/*.conf\n", encoding="utf-8")
    assert (
        augeas_module.include_covers_dropin(commented, dropin, "#", "PullIn") is False
    )


def test_the_program_lines_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The proof of the value: every line of the augtool program is a declared
    # template, so another driver entry or another removal shape is answered
    # in the values and not in the helper.
    monkeypatch.setattr(engine_values, "AUGEAS_LENS_LINE", "MARK-LENS {lens}")
    monkeypatch.setattr(engine_values, "AUGEAS_INCL_LINE", "MARK-INCL {path}")
    monkeypatch.setattr(engine_values, "AUGEAS_LOAD_LINE", "MARK-LOAD")
    monkeypatch.setattr(engine_values, "AUGEAS_SAVE_LINE", "MARK-SAVE")
    monkeypatch.setattr(
        engine_values, "AUGEAS_DIRECTIVE_LINE", "MARK-DIRECTIVE {node} {name} {value}"
    )
    dropin_path = Path("/etc/ssh/sshd_config.d/pyntara.conf")
    captured: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        captured.append({"command": command, **kwargs})
        return _FakeProc(0, "")

    monkeypatch.setattr(augeas_module, "run_command", fake_run)
    augeas_module.write_dropin(
        dropin_path,
        (("Port", "30222"),),
        [],
        "Sshd.lns",
        "owned by the test",
        30.0,
    )
    script = str(captured[0]["input"])
    assert "MARK-LENS Sshd.lns" in script
    assert f"MARK-INCL {dropin_path}" in script
    assert "MARK-LOAD" in script
    assert "MARK-DIRECTIVE" in script
    assert "MARK-SAVE" in script
