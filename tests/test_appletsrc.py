"""Unit tests for the shared appletsrc reading.

Plasma nests a panel applet as [Containments][N][Applets][M], and that position
differs per machine, so the group of an applet is never a written value: it is
found by the plugin the section declares. Four sections need that walk, so the
test pins the single copy and the value that spells the plugin key.
"""

from __future__ import annotations

import pytest

from pyntara import appletsrc
from pyntara.values import common as common_values

_SAMPLE_APPLETSRC = """[Containments][1]
plugin=org.kde.plasma.folder

[Containments][2]
plugin=org.kde.plasma.panel

[Containments][2][Applets][22]
plugin=org.kde.plasma.digitalclock

[Containments][2][Applets][22][Configuration][Appearance]
use24hFormat=1

[Containments][2][Applets][3]
plugin=org.kde.plasma.kickoff

[Containments][2][Applets][5]
plugin=org.kde.plasma.icontasks

[Containments][2][Applets][7]
plugin=org.kde.plasma.systemtray

[Containments][2][Applets][7][Applets][15]
plugin=org.kde.plasma.digitalclock
"""


def test_an_applet_group_is_found_by_the_plugin_its_section_declares() -> None:
    # Every matching section is returned, so an applet a panel shows twice gets
    # the setting on both, and a panel carrying two task manager widgets pins
    # both.
    assert appletsrc.applet_groups(
        _SAMPLE_APPLETSRC, ("org.kde.plasma.kickoff",)
    ) == (("Containments", "2", "Applets", "3"),)
    assert appletsrc.applet_groups(
        _SAMPLE_APPLETSRC, ("org.kde.plasma.digitalclock",)
    ) == (
        ("Containments", "2", "Applets", "22"),
        ("Containments", "2", "Applets", "7", "Applets", "15"),
    )
    assert appletsrc.applet_groups(_SAMPLE_APPLETSRC, ("org.kde.plasma.missing",)) == ()


def test_the_plugin_key_of_the_walk_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The key that names the applet plugin in an appletsrc section is a value of
    # the foreign file the sections edit: another key in the shared module is
    # the line the walk looks for.
    monkeypatch.setattr(common_values, "APPLET_PLUGIN_KEY", "applet")
    text = (
        "[Containments][2]\n"
        "applet=org.kde.plasma.icontasks\n"
        "\n"
        "[Containments][2][Applets][5]\n"
        "plugin=org.kde.plasma.icontasks\n"
    )
    assert appletsrc.applet_groups(
        text, ("org.kde.plasma.icontasks",)
    ) == (("Containments", "2"),)
