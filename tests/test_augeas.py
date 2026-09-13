"""Unit tests for the shared augeas helpers.

The helpers drive an external tool, so the tests replace run_command and
read only the argv and the script the helper builds; no file is written
by the fake.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config

from pyntara import augeas as augeas_module


def test_the_tool_and_the_node_prefix_come_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The augtool argv and the node prefix of its program are engine
    # values: another command and another prefix are the argv and the
    # script the helper builds, so the tool vocabulary lives in one place
    # for the two ssh tasks while the reading still parses what the tool
    # printed.
    engine = replace(
        make_config().engine,
        augtool_command=("myaugtool", "--noautoload", "--nobackup"),
        augeas_files_node_prefix="/myfiles",
    )
    dropin_path = Path("/etc/ssh/sshd_config.d/pyntara.conf")
    node = f"{engine.augeas_files_node_prefix}{dropin_path}"
    captured: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        captured.append({"command": command, **kwargs})
        listing = (
            f'{node}/#comment = "owned by the test"\n'
            f'{node}/Port = "30222"\n'
        )
        return _FakeProc(0, listing)

    monkeypatch.setattr(augeas_module, "run_command", fake_run)
    directives, comment = augeas_module.read_dropin_state(
        engine, dropin_path, "Sshd.lns", 30.0
    )
    assert captured[0]["command"] == [
        "myaugtool",
        "--noautoload",
        "--nobackup",
    ]
    assert f"print {node}\n" in str(captured[0]["input"])
    assert comment == "owned by the test"
    assert directives == {"Port": "30222"}
