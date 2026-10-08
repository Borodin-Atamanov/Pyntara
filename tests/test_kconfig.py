"""Unit tests for the shared KConfig access and the shared user wrapper.

The reader, the writer and the delete of pyntara.kconfig build one argv through
one builder, and every command that reaches the desktop user goes through the
wrapper of pyntara.utils. A task used to carry its own copy of both; these
tests pin the single copy, so a change reaches every section at once.
"""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeProc

from pyntara import kconfig
from pyntara.utils import as_user_command, home_environment
from pyntara.values import common as common_values


def test_the_reader_argv_comes_from_the_shared_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reader, the group selector and the key selector of the KConfig access
    # are values of the shared module: another set of commands is what the
    # builder returns.
    monkeypatch.setattr(
        common_values, "KREADCONFIG_COMMAND", ("my-reader", "--config", "{file_name}")
    )
    monkeypatch.setattr(common_values, "CONFIG_GROUP_FLAG", ("--section", "{group}"))
    monkeypatch.setattr(common_values, "CONFIG_KEY_FLAG", ("--entry", "{key}"))
    assert kconfig.kconfig_command(
        common_values.KREADCONFIG_COMMAND, "kwinrc", ("Group", "Sub"), "Key"
    ) == [
        "my-reader",
        "--config",
        "kwinrc",
        "--section",
        "Group",
        "--section",
        "Sub",
        "--entry",
        "Key",
    ]


def test_the_writer_argv_carries_its_extra_flags_before_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A section adds its own flags, such as the boolean type flag, through
    # extra_flags; the builder places them after the selectors.
    monkeypatch.setattr(
        common_values, "KWRITECONFIG_COMMAND", ("my-writer", "--config", "{file_name}")
    )
    monkeypatch.setattr(common_values, "CONFIG_GROUP_FLAG", ("--section", "{group}"))
    monkeypatch.setattr(common_values, "CONFIG_KEY_FLAG", ("--entry", "{key}"))
    assert kconfig.kconfig_command(
        common_values.KWRITECONFIG_COMMAND,
        "kdeglobals",
        ("Group",),
        "Key",
        extra_flags=common_values.CONFIG_BOOL_TYPE_FLAG,
    ) == [
        "my-writer",
        "--config",
        "kdeglobals",
        "--section",
        "Group",
        "--entry",
        "Key",
        "--type",
        "bool",
    ]


def test_the_user_wrapper_comes_from_the_shared_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The wrapper that runs a command as the desktop user is one shared value,
    # so another wrapper is the argv every section builds.
    monkeypatch.setattr(
        common_values, "RUNUSER_COMMAND", ("sudo", "-u", "{username}", "--")
    )
    assert as_user_command(["kreadconfig6", "--file", "kxkbrc"]) == [
        "sudo",
        "-u",
        common_values.DESKTOP_USERNAME,
        "--",
        "kreadconfig6",
        "--file",
        "kxkbrc",
    ]


def test_the_home_environment_names_the_desktop_user_home() -> None:
    assert home_environment() == {"HOME": common_values.DESKTOP_HOME_DIR}


def test_a_read_runs_the_reader_as_the_desktop_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The read runs the reader of the shared values through the user wrapper,
    # with the home of the desktop user in the environment, and returns the
    # trimmed answer.
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        seen.append((list(command), dict(kwargs)))
        return FakeProc(0, "  Flag\n")

    monkeypatch.setattr(kconfig, "run_command", fake_run)
    assert kconfig.read_config_value("kwinrc", ("Layout",), "LayoutList", timeout=7) == (
        "Flag"
    )
    command, kwargs = seen[0]
    assert command == [
        *as_user_command(["kreadconfig6", "--file", "kwinrc"]),
        "--group",
        "Layout",
        "--key",
        "LayoutList",
    ]
    assert kwargs["capture"] is True
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 7
    assert kwargs["extra_env"] == home_environment()


def test_a_write_and_a_delete_run_the_writer_as_the_desktop_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The write appends the value after the flags, the delete carries the
    # delete flag; both run through the user wrapper with the given session
    # environment.
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        commands.append(list(command))
        return FakeProc(0, "")

    monkeypatch.setattr(kconfig, "run_command", fake_run)
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    kconfig.write_config_value(
        "kwinrc",
        ("Plugins",),
        "Enabled",
        "true",
        timeout=7,
        extra_flags=common_values.CONFIG_BOOL_TYPE_FLAG,
        env=env,
    )
    kconfig.delete_config_value(
        "kwinrc",
        ("Plugins",),
        "Enabled",
        timeout=7,
        extra_flags=("--delete",),
        env=env,
    )
    assert commands == [
        [
            *as_user_command(["kwriteconfig6", "--file", "kwinrc"]),
            "--group",
            "Plugins",
            "--key",
            "Enabled",
            "--type",
            "bool",
            "true",
        ],
        [
            *as_user_command(["kwriteconfig6", "--file", "kwinrc"]),
            "--group",
            "Plugins",
            "--key",
            "Enabled",
            "--delete",
        ],
    ]
