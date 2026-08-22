"""[kde_settings] table parser.

The section carries the parameters of the kde_settings task: the packages
it requires, the target user whose KDE configuration is edited, the dark
color scheme applied to all windows, the dark global theme that covers
the whole desktop, the input and keyboard settings (NumLock on startup,
touchpad preferences, the Wayland virtual keyboard) and the command that
reloads kwin. Future settings of the same task are added to this table as
new keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import (
    CLICK_METHODS,
    NUMLOCK_STATES,
    ConfigError,
    _bool_field,
    _enum_field,
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
    global theme that covers the whole desktop; numlock_on_boot is the
    NumLock state at Plasma startup; touchpad_click_method and
    touchpad_disable_on_external_mouse are the touchpad preferences
    applied to every touchpad found; virtual_keyboard_enabled,
    virtual_keyboard_input_method and virtual_keyboard_locales configure
    the Wayland virtual keyboard; kwin_reload_command makes kwin re-read
    its configuration.
    """

    packages: tuple[str, ...]
    username: str
    home_dir: str
    color_scheme: str
    look_and_feel: str
    numlock_on_boot: str
    touchpad_click_method: str
    touchpad_disable_on_external_mouse: bool
    virtual_keyboard_enabled: bool
    virtual_keyboard_input_method: str
    virtual_keyboard_locales: tuple[str, ...]
    kwin_reload_command: tuple[str, ...]


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
        numlock_on_boot=_enum_field(
            raw.get("numlock_on_boot"),
            "kde_settings.numlock_on_boot",
            NUMLOCK_STATES,
        ),
        touchpad_click_method=_enum_field(
            raw.get("touchpad_click_method"),
            "kde_settings.touchpad_click_method",
            CLICK_METHODS,
        ),
        touchpad_disable_on_external_mouse=_bool_field(
            raw.get("touchpad_disable_on_external_mouse"),
            "kde_settings.touchpad_disable_on_external_mouse",
        ),
        virtual_keyboard_enabled=_bool_field(
            raw.get("virtual_keyboard_enabled"),
            "kde_settings.virtual_keyboard_enabled",
        ),
        virtual_keyboard_input_method=_nonempty_string_field(
            raw.get("virtual_keyboard_input_method"),
            "kde_settings.virtual_keyboard_input_method",
        ),
        virtual_keyboard_locales=_string_list(
            raw.get("virtual_keyboard_locales"),
            "kde_settings.virtual_keyboard_locales",
        ),
        kwin_reload_command=_string_list(
            raw.get("kwin_reload_command"), "kde_settings.kwin_reload_command"
        ),
    )
