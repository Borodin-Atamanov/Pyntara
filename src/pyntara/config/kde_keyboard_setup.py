"""[kde_keyboard_setup] table parser.

The section carries the parameters of the kde_keyboard_setup task: the
packages it requires, the target user whose KDE configuration is edited,
the KConfig files it manages, the keyboard layouts and switch options
written to kxkbrc, the indicator display style written to the Plasma
appletsrc, and the commands that apply the changes immediately.
"""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KdeKeyboardSetupConfig:
    """Parameters of the kde_keyboard_setup task.

    packages are the packages the task ensures are installed (the provider
    of kwriteconfig6 and the DBus client used for reloads); username,
    home_dir and config_dir identify the user whose desktop config is
    edited; kxkbrc_file_name and appletsrc_file_name are the KConfig files
    under config_dir that the task manages; applet_plugin is the Plasma
    applet whose display style is the layout indicator; layouts,
    switch_option, reset_old_options, switch_mode and
    use_layout_switching are the kxkbrc values;
    indicator_display_style is the appletsrc value; kwin_reload_command
    and panel_restart_command make the changes apply immediately;
    layout_switch_shortcuts maps keyboard switcher action names to the
    shortcuts that switch straight to one layout.
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
    reset_old_options: bool
    switch_mode: str
    use_layout_switching: bool
    indicator_display_style: str
    kwin_reload_command: tuple[str, ...]
    panel_restart_command: tuple[str, ...]
    layout_switch_shortcuts: dict[str, str]
