# KDE appearance and input setup

There is a dedicated desktop task: kde_settings.

The task configures the dark appearance and the input and keyboard settings of the target user's KDE desktop: the color scheme that turns every Qt and KDE window dark, the global theme (look and feel) that covers the whole desktop, the NumLock state on startup, the touchpad preferences and the Wayland virtual keyboard. It belongs to the desktop mode. The appearance values are applied with the plasma-apply tools and the input values with kwriteconfig6, all as the target user, so the config files stay owned by that user and the changes apply when a desktop session is running.

## Target configuration

The applied values live in KConfig files under the config directory of the target user:

kdeglobals carries the appearance. The color scheme in the [General] group, ColorScheme key, set to the configured color_scheme, is the palette every Qt and KDE window renders with. The global theme in the [KDE] group, LookAndFeelPackage key, set to the configured look_and_feel package, carries the defaults for the panel theme, the window decorations and the icon theme.
kcminputrc carries the input settings. The NumLock state on Plasma startup in the [Keyboard] group, NumLock key, is written as 0 (on), 1 (off) or 2 (unchanged) from numlock_on_boot. The touchpad preferences, ClickMethod and DisableEventsOnExternalMouse, are written into every [Libinput][id][serial][name] group whose device name ends with Touchpad, so the task works on any target hardware regardless of the touchpad model or its presence.
kwinrc and plasmakeyboardrc carry the Wayland virtual keyboard. The input method in the [Wayland] group, InputMethod key, is written with its plain name: the GUI writes the [$e] flag, kwriteconfig6 escapes it, and kreadconfig6 reads both forms through the plain key, so the plain form keeps the comparison idempotent. The enabled locales in plasmakeyboardrc, [General] enabledLocales key, are joined by commas.

## Apply mechanism

The task reads every current value with kreadconfig6 as the target user and applies only what differs, through runuser with HOME set to the configured home_dir. The appearance is applied with the plasma-apply tools, the global theme first and then the color scheme so the configured scheme wins. The input values are written with kwriteconfig6. Missing packages (the provider of the plasma-apply tools and of the KConfig tools) are installed first, each failure being a fatal error.

## Apply immediately

The apply commands run with the session bus address of the target user, read from the environment of that user's kwin_wayland process, so the theme changes apply at once. When the Wayland input method changed, the task runs the configured kwin_reload_command as a best effort; the NumLock state applies at the next Plasma startup and the input device settings at the next login, which is inherent to their nature. When no kwin_wayland process is found (a session that is not running yet), the commands still write the config and the settings apply after the next login; this is not an error. The touchpad settings can only be written once kcminputrc exists, that is after the first login opens the input devices; until then the task skips them with a message.

## Idempotency

The task reads every current value with kreadconfig6 and applies only what differs. The target state is reached when every configured value already matches the configuration and the packages are installed; the task then skips with changed=False. Force mode applies every value regardless. Missing packages are installed first, each failure being a fatal error.

## Parameters

All parameters live in the [kde_settings] table of the config/ directory:

packages, the packages the task ensures are installed: the provider of the plasma-apply theme tools (plasma-workspace) and of the KConfig tools (libkf6config-bin).
username and home_dir, the target user and that user's home directory.
color_scheme, the dark color scheme applied to all windows.
look_and_feel, the dark global theme applied to the whole desktop.
numlock_on_boot, the NumLock state on Plasma startup: on, off or unchanged.
touchpad_click_method, the touchpad click method: clickfinger, clickareas or none.
touchpad_disable_on_external_mouse, whether the touchpad is disabled while an external mouse is connected.
virtual_keyboard_enabled, whether the Wayland virtual keyboard is enabled.
virtual_keyboard_input_method, the input method that provides the virtual keyboard.
virtual_keyboard_locales, the enabled locales of the virtual keyboard.
kwin_reload_command, the command that makes kwin re-read its configuration.

Future settings of the same task are added to the same table as new keys.
