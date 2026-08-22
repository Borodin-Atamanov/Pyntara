"""[kde_settings] table parser.

The section carries the parameters of the kde_settings task: the packages
it requires, the target user whose KDE configuration is edited, the dark
color scheme applied to all windows and the dark global theme that covers
the whole desktop. Future settings of the same task are added to this
table as new keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import (
    ConfigError,
    _nonempty_string_field,
    _string_list,
)


@dataclass(frozen=True)
class KdeSettingsConfig:
    """Parameters of the kde_settings task.

    packages are the packages the task ensures are installed (the provider
    of the plasma-apply theme tools and the KConfig reader); username and
    home_dir identify the user whose desktop config is edited; color_scheme
    is the dark scheme applied to all windows; look_and_feel is the dark
    global theme that covers the whole desktop.
    """

    packages: tuple[str, ...]
    username: str
    home_dir: str
    color_scheme: str
    look_and_feel: str


def _kde_settings_table(raw: object) -> KdeSettingsConfig:
    """Validate the [kde_settings] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[kde_settings] section is missing or not a table")
    return KdeSettingsConfig(
        packages=_string_list(raw.get("packages"), "kde_settings.packages"),
        username=_nonempty_string_field(
            raw.get("username"), "kde_settings.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "kde_settings.home_dir"
        ),
        color_scheme=_nonempty_string_field(
            raw.get("color_scheme"), "kde_settings.color_scheme"
        ),
        look_and_feel=_nonempty_string_field(
            raw.get("look_and_feel"), "kde_settings.look_and_feel"
        ),
    )
