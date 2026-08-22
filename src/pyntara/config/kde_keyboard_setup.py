"""[kde_keyboard_setup] table parser.

The section carries the parameters of the kde_keyboard_setup task: the
packages it requires, the target user whose KDE configuration is edited,
the KConfig files it manages, the keyboard layouts and switch options
written to kxkbrc, the indicator display style written to the Plasma
appletsrc, and the commands that apply the changes immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _nonempty_string_field


@dataclass(frozen=True)
class KdeKeyboardSetupConfig:
    """Parameters of the kde_keyboard_setup task.

    packages are the packages the task ensures are installed (the provider
    of kwriteconfig6 and the DBus client used for reloads); username,
    home_dir and config_dir identify the user whose desktop config is
    edited; kxkbrc_file_name and appletsrc_file_name are the KConfig files
    under config_dir that the task manages; applet_plugin is the Plasma
    applet whose display style is the layout indicator; layouts,
    switch_option and use_layout_switching are the kxkbrc values;
    indicator_display_style is the appletsrc value; kwin_reload_command
    and panel_restart_command make the changes apply immediately.
    """

    packages: tuple[str, ...]
    username: str
    home_dir: str
    config_dir: str
    kxkbrc_file_name: str
    appletsrc_file_name: str
    applet_plugin: str
    layouts: tuple[str, ...]
    switch_option: str
    use_layout_switching: bool
    indicator_display_style: str
    kwin_reload_command: tuple[str, ...]
    panel_restart_command: tuple[str, ...]


def _string_list(raw: object, name: str) -> tuple[str, ...]:
    """Validate a non-empty array of non-empty strings."""

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name} must be a non-empty array of strings")
    if not all(isinstance(part, str) and part.strip() for part in raw):
        raise ConfigError(f"{name} must be non-empty strings")
    return tuple(part.strip() for part in raw)


def _bool_field(raw: object, name: str) -> bool:
    """Validate a boolean config value."""

    if not isinstance(raw, bool):
        raise ConfigError(f"{name} must be a boolean")
    return raw


def _kde_keyboard_setup_table(raw: object) -> KdeKeyboardSetupConfig:
    """Validate the [kde_keyboard_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[kde_keyboard_setup] section is missing or not a table")
    return KdeKeyboardSetupConfig(
        packages=_string_list(raw.get("packages"), "kde_keyboard_setup.packages"),
        username=_nonempty_string_field(
            raw.get("username"), "kde_keyboard_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "kde_keyboard_setup.home_dir"
        ),
        config_dir=_nonempty_string_field(
            raw.get("config_dir"), "kde_keyboard_setup.config_dir"
        ),
        kxkbrc_file_name=_nonempty_string_field(
            raw.get("kxkbrc_file_name"), "kde_keyboard_setup.kxkbrc_file_name"
        ),
        appletsrc_file_name=_nonempty_string_field(
            raw.get("appletsrc_file_name"),
            "kde_keyboard_setup.appletsrc_file_name",
        ),
        applet_plugin=_nonempty_string_field(
            raw.get("applet_plugin"), "kde_keyboard_setup.applet_plugin"
        ),
        layouts=_string_list(raw.get("layouts"), "kde_keyboard_setup.layouts"),
        switch_option=_nonempty_string_field(
            raw.get("switch_option"), "kde_keyboard_setup.switch_option"
        ),
        use_layout_switching=_bool_field(
            raw.get("use_layout_switching"),
            "kde_keyboard_setup.use_layout_switching",
        ),
        indicator_display_style=_nonempty_string_field(
            raw.get("indicator_display_style"),
            "kde_keyboard_setup.indicator_display_style",
        ),
        kwin_reload_command=_string_list(
            raw.get("kwin_reload_command"), "kde_keyboard_setup.kwin_reload_command"
        ),
        panel_restart_command=_string_list(
            raw.get("panel_restart_command"),
            "kde_keyboard_setup.panel_restart_command",
        ),
    )
