"""[kde_settings] table parser.

The section carries the parameters of the kde_settings task: the packages
it requires, the target user whose KDE configuration is edited, the dark
color scheme applied to all windows, the dark global theme that covers
the whole desktop, the input and keyboard settings (NumLock on startup,
touchpad preferences, the Wayland virtual keyboard) and the command that
reloads kwin. The kconfig list carries additional KConfig values applied
by the task as records; future settings of the same task are added either
as new keys or as kconfig records.
"""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KCONFIG_TYPES: tuple[str, ...] = ("string", "bool")


@dataclass(frozen=True)
class KConfigRecord:
    """One KConfig value applied by the kde_settings task.

    file is the config file name under the target user config directory;
    group is the list of group segments that lead to the key; key and
    value name the key and the string form of its value; type is string
    or bool, the latter storing the key as a boolean; delete, when true,
    removes the key instead of writing it and value stays empty.
    """

    file: str
    group: tuple[str, ...]
    key: str
    value: str
    type: str
    delete: bool


@dataclass(frozen=True)
class KdeSettingsConfig:
    """Parameters of the kde_settings task.

    packages are the packages the task ensures are installed (the provider
    of the plasma-apply theme tools and the KConfig reader); username and
    home_dir identify the user whose desktop config is edited; user_dirs
    maps the XDG user directories to their target paths; color_scheme is
    the dark scheme applied to all windows; look_and_feel is the dark
    global theme that covers the whole desktop; look_and_feel_light is the
    light global theme the day and night switch alternates to;
    automatic_look_and_feel, when true, makes the task enable the native
    KDE day and night theme switch and leaves the current theme to that
    switch; cursor_theme is the mouse cursor theme applied to the desktop
    session with plasma-apply-cursortheme, so it wins over the theme
    default that the day and night switch writes; cursor_theme_light is
    the cursor theme written into the light theme defaults, so the switch
    applies it on the light theme too; numlock_on_boot is the
    NumLock state at Plasma startup; touchpad_click_method and
    touchpad_disable_on_external_mouse are the touchpad preferences
    applied to every touchpad found; virtual_keyboard_enabled,
    virtual_keyboard_input_method and virtual_keyboard_locales configure
    the Wayland virtual keyboard; kwin_reload_command makes kwin re-read
    its configuration; kconfig carries additional KConfig values applied
    as records; the sddm_* values configure the login screen autologin and
    theme, and sddm_conf_file and sddm_theme_conf_file are the system files
    that carry them; the remaining paths name the files and directories the
    task reads and writes under home_dir and the system copy of the global
    themes with the directory inside a theme that holds its defaults.
    """

    packages: tuple[str, ...]
    username: str
    home_dir: str
    user_dirs: dict[str, str]
    color_scheme: str
    look_and_feel: str
    look_and_feel_light: str
    automatic_look_and_feel: bool
    cursor_theme: str
    cursor_theme_light: str
    numlock_on_boot: str
    touchpad_click_method: str
    touchpad_disable_on_external_mouse: bool
    virtual_keyboard_enabled: bool
    virtual_keyboard_input_method: str
    virtual_keyboard_locales: tuple[str, ...]
    kwin_reload_command: tuple[str, ...]
    sddm_conf_file: Path
    sddm_theme_conf_file: Path
    user_config_dir: str
    user_kwin_scripts_dir: Path
    user_look_and_feel_dir: Path
    user_places_file: Path
    user_dirs_file: str
    konsole_profile_path: Path
    script_file_mode: int
    default_file_mode: int
    system_look_and_feel_dir: Path
    theme_defaults_dir: Path
    sddm_autologin_user: str
    sddm_autologin_session: str
    sddm_theme: str
    sddm_theme_cursor_size: str
    sddm_theme_cursor_theme: str
    sddm_theme_font: str
    kdeglobals_file_name: str
    kcminputrc_file_name: str
    kwinrc_file_name: str
    plasma_keyboard_file_name: str
    global_shortcuts_file_name: str
    general_group: tuple[str, ...]
    kde_group: tuple[str, ...]
    mouse_group: tuple[str, ...]
    keyboard_group: tuple[str, ...]
    wayland_group: tuple[str, ...]
    virtual_keyboard_group: tuple[str, ...]
    plugins_group: tuple[str, ...]
    desktops_group: tuple[str, ...]
    look_and_feel_package_key: str
    color_scheme_key: str
    automatic_look_and_feel_key: str
    automatic_look_and_feel_idle_interval_key: str
    numlock_key: str
    input_method_key: str
    input_method_locales_key: str
    cursor_theme_key: str
    click_method_key: str
    touchpad_disable_external_mouse_key: str
    desktop_count_key: str
    kconfig_true_value: str
    kconfig_false_value: str
    numlock_values: dict[str, str]
    click_method_values: dict[str, str]
    automatic_theme_switch_idle_interval: str
    places_root_tag: str
    places_bookmark_tag: str
    places_title_tag: str
    places_metadata_path: str
    places_metadata_owner_attribute: str
    places_hidden_element: str
    places_hidden_value: str
    places_namespaces: dict[str, str]
    kwin_scripts: tuple[str, ...]
    kwin_script_files: tuple[str, ...]
    kwin_script_hotkeys: tuple[str, ...]
    kwin_script_actions: tuple[str, ...]
    places_hidden: tuple[str, ...] = ()
    places_metadata_owner: str = ""
    kconfig: tuple[KConfigRecord, ...] = ()
