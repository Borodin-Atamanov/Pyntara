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

from ._fields import (
    CLICK_METHODS,
    NUMLOCK_STATES,
    ConfigError,
    _bool_field,
    _enum_field,
    _nonempty_string_field,
    _string_list,
    _string_map,
)

# The value types a kconfig record can carry. bool adds the kwriteconfig6
# --type bool flag so the key is stored as a boolean, not as a string;
# string is the plain form used for every other value.
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
    theme in the system files /etc/sddm.conf and
    /etc/sddm.conf.d/20-kubuntu.conf.
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
    sddm_autologin_user: str
    sddm_autologin_session: str
    sddm_theme: str
    sddm_theme_cursor_size: str
    sddm_theme_cursor_theme: str
    sddm_theme_font: str
    kconfig: tuple[KConfigRecord, ...] = ()


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
        user_dirs=_string_map(raw.get("user_dirs"), "kde_settings.user_dirs"),
        color_scheme=_nonempty_string_field(
            raw.get("color_scheme"), "kde_settings.color_scheme"
        ),
        look_and_feel=_nonempty_string_field(
            raw.get("look_and_feel"), "kde_settings.look_and_feel"
        ),
        look_and_feel_light=_nonempty_string_field(
            raw.get("look_and_feel_light"), "kde_settings.look_and_feel_light"
        ),
        automatic_look_and_feel=_bool_field(
            raw.get("automatic_look_and_feel"),
            "kde_settings.automatic_look_and_feel",
        ),
        cursor_theme=_nonempty_string_field(
            raw.get("cursor_theme"), "kde_settings.cursor_theme"
        ),
        cursor_theme_light=_nonempty_string_field(
            raw.get("cursor_theme_light"), "kde_settings.cursor_theme_light"
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
        sddm_autologin_user=_nonempty_string_field(
            raw.get("sddm_autologin_user"), "kde_settings.sddm_autologin_user"
        ),
        sddm_autologin_session=_nonempty_string_field(
            raw.get("sddm_autologin_session"),
            "kde_settings.sddm_autologin_session",
        ),
        sddm_theme=_nonempty_string_field(
            raw.get("sddm_theme"), "kde_settings.sddm_theme"
        ),
        sddm_theme_cursor_size=_nonempty_string_field(
            raw.get("sddm_theme_cursor_size"),
            "kde_settings.sddm_theme_cursor_size",
        ),
        sddm_theme_cursor_theme=_nonempty_string_field(
            raw.get("sddm_theme_cursor_theme"),
            "kde_settings.sddm_theme_cursor_theme",
        ),
        sddm_theme_font=_nonempty_string_field(
            raw.get("sddm_theme_font"), "kde_settings.sddm_theme_font"
        ),
        kconfig=_kconfig_records(raw.get("kconfig")),
    )


def _kconfig_record(raw: object, index: int) -> KConfigRecord:
    """Validate one [[kde_settings.kconfig]] record."""

    name = f"kde_settings.kconfig[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a table")
    file_name = _nonempty_string_field(raw.get("file"), f"{name}.file")
    group = _string_list(raw.get("group"), f"{name}.group")
    key = _nonempty_string_field(raw.get("key"), f"{name}.key")
    delete = _bool_field(raw.get("delete", False), f"{name}.delete")
    if delete:
        if raw.get("value") is not None:
            raise ConfigError(f"{name} must not have a value when delete is true")
        value = ""
    else:
        value = _nonempty_string_field(raw.get("value"), f"{name}.value")
    value_type = _enum_field(
        raw.get("type", "string"), f"{name}.type", KCONFIG_TYPES
    )
    return KConfigRecord(
        file=file_name,
        group=group,
        key=key,
        value=value,
        type=value_type,
        delete=delete,
    )


def _kconfig_records(raw: object) -> tuple[KConfigRecord, ...]:
    """Validate the [[kde_settings.kconfig]] records; empty when absent."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[kde_settings] kconfig must be an array of tables")
    return tuple(_kconfig_record(record, index) for index, record in enumerate(raw))
