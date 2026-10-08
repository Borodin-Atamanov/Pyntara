"""Reading of the Plasma appletsrc of the desktop user.

Plasma nests a panel applet as [Containments][N][Applets][M], and that position
differs per machine, so the group of an applet is never a value that can be
written down: it is found by the plugin name the applet section declares. Four
sections need that walk (chrome_setup, firefox_setup, kde_settings and
kde_keyboard_setup), so it lives here once and a section adds only the group it
writes below the applet.
"""

from __future__ import annotations

from collections.abc import Sequence

from pyntara.values import common as common_values


def applet_groups(
    text: str, plugin_names: Sequence[str]
) -> tuple[tuple[str, ...], ...]:
    """The group of every appletsrc section declaring one of the plugins.

    Every matching applet is returned, so a panel that shows the same applet
    twice receives the setting on both, and a panel carrying two task manager
    widgets pins both.
    """

    groups: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    marker = f"{common_values.APPLET_PLUGIN_KEY}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = tuple(part for part in stripped[1:-1].split("][") if part)
        elif (
            stripped.startswith(marker)
            and stripped.removeprefix(marker) in plugin_names
        ):
            groups.append(current)
    return tuple(groups)
