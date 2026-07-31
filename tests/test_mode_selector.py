from __future__ import annotations

import io
from collections.abc import Callable

from pyntara.mode_selector import select_install_mode
from pyntara.models import InstallModesConfig


class _FakeStream(io.StringIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class _ScriptedKeyReader:
    def __init__(self, keys: list[str | None]) -> None:
        self._keys = keys
        self._index = 0

    def read_key(self, timeout_sec: float) -> str | None:
        _ = timeout_sec
        if self._index >= len(self._keys):
            return None
        key = self._keys[self._index]
        self._index += 1
        return key


class _NeverKeyReader:
    def read_key(self, timeout_sec: float) -> str | None:
        _ = timeout_sec
        return None


def _monotonic_from(values: list[float]) -> Callable[[], float]:
    index = 0
    last = values[-1]

    def _next() -> float:
        nonlocal index, last
        if index < len(values):
            last = values[index]
            index += 1
        return last

    return _next


def _install_modes() -> InstallModesConfig:
    return InstallModesConfig.model_validate(
        {
            "minimal": ["hostname"],
            "server": ["hostname", "users"],
            "desktop": ["hostname", "users"],
            "default_desktop_mode": "desktop",
            "default_server_mode": "server",
            "auto_select_timeout_sec": 11,
        }
    )


def test_selector_returns_desktop_default_when_not_tty() -> None:
    mode = select_install_mode(
        install_modes=_install_modes(),
        env={"DISPLAY": ":0"},
        stdin=_FakeStream(is_tty=False),
        stdout=_FakeStream(is_tty=False),
    )

    assert mode == "desktop"


def test_selector_returns_server_default_when_not_tty() -> None:
    mode = select_install_mode(
        install_modes=_install_modes(),
        env={},
        stdin=_FakeStream(is_tty=False),
        stdout=_FakeStream(is_tty=False),
    )

    assert mode == "server"


def test_selector_confirm_keeps_default_mode() -> None:
    mode = select_install_mode(
        install_modes=_install_modes(),
        env={},
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_ScriptedKeyReader(["ENTER"]),
        monotonic=_monotonic_from([0.0, 0.01, 0.02]),
    )

    assert mode == "server"


def test_selector_arrow_navigation_changes_selected_mode() -> None:
    mode = select_install_mode(
        install_modes=_install_modes(),
        env={},
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_ScriptedKeyReader(["DOWN", "ENTER"]),
        monotonic=_monotonic_from([0.0, 0.01, 0.02, 0.03]),
    )

    assert mode == "desktop"


def test_selector_auto_selects_after_timeout() -> None:
    mode = select_install_mode(
        install_modes=_install_modes(),
        env={},
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_NeverKeyReader(),
        monotonic=_monotonic_from([0.0, 6.0, 12.0, 12.1]),
    )

    assert mode == "server"
