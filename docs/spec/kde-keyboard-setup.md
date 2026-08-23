# KDE keyboard layout setup

There is a dedicated desktop task: kde_keyboard_setup.

The task configures the KDE keyboard layouts and the layout indicator of the target user: the layout list and the switch option in kxkbrc, the indicator display style in the keyboard layout applet of the Plasma panel, and optional per-layout hotkeys in kglobalshortcutsrc. It belongs to the desktop mode. The values are written with kwriteconfig6 as the target user, so the config files stay owned by that user, and the changes apply immediately through a kwin reload and a panel restart; the hotkeys are applied through the kglobalaccel daemon.

## Target configuration

Two KConfig files under the configured config_dir are managed:

1. kxkbrc, group [Layout]. LayoutList carries the configured layouts joined by commas, Options carries the XKB switch option, Use carries whether switching is enabled. The configured switch_option grp:caps_select means Caps Lock to the first layout and Shift+Caps Lock to the second layout, so the layout order in LayoutList decides which key gives English and which gives Russian.
2. The Plasma appletsrc, the keyboard layout applet. The applet is found by its plugin declaration; its displayStyle is set to the configured indicator_display_style, so the indicator shows the country flag instead of the layout name.
3. kglobalshortcutsrc, the global shortcuts file. Each entry of layout_switch_shortcuts maps a keyboard layout switcher action to a shortcut in Qt portable text format, so one hotkey switches straight to one layout.

## Write mechanism

kwriteconfig6 and kreadconfig6 run as the configured user through runuser, with HOME set to the configured home_dir, so the files land in the user config directory and keep the user ownership. The kxkbrc group is fixed; the applet configuration group is discovered from the appletsrc text: the section that declares plugin=<applet_plugin> holds its configuration in [Configuration][General] below that section. The Plasma applet ids are assigned at first panel start, so the group is never hardcoded. The hotkey entries are written into the [KDE Keyboard Layout Switcher] group of kglobalshortcutsrc, one entry per action: the shortcut, the default key none and the action name.

## Apply immediately

After a kxkbrc change the task runs the configured kwin_reload_command through the session bus of the target user, whose address is read from the environment of that user's kwin_wayland process. KWin owns the keyboard layout service on Wayland, so the reload makes the new layouts and the switch option take effect at once. When no kwin_wayland process is found (a session that is not running yet), the reload is skipped and the settings apply after the next login. After an indicator change the task runs the configured panel_restart_command, which restarts the Plasma panel so the applet re-reads its configuration.

When a desktop session is running, the supported layout hotkeys are also applied through the kglobalaccel daemon: an embedded python3-dbus script runs as the target user on the session bus, frees each hotkey from its current owner (the same action the System Settings shortcuts module would ask about) and assigns it to the configured action. This is the same call the System Settings GUI makes, so the shortcut works immediately without a session restart. The daemon writes the applied state back to kglobalshortcutsrc itself. Without a session the hotkey entries written by the task are read at the next login; a key that a default shortcut (for example the Activity Switcher Meta+Q) also claims is then confirmed once in System Settings, or the task is run again after login. Only shortcuts made of the modifiers Ctrl, Alt, Shift and Meta plus one alphanumeric key are applied live; other portable forms (function keys, named keys) are only written to the file.

## Idempotency

The task reads every current value with kreadconfig6 and writes only what differs. The hotkey entries are compared the same way; when a session is running, the live apply reports the before and after state of each action and counts only real changes. The target state is reached when every kxkbrc value, the displayStyle and every hotkey entry already match the configuration and the packages are installed; the task then skips with changed=False. Force mode rewrites every value and reloads regardless. Missing packages are installed first, each failure being a fatal error.

## Parameters

All parameters live in the [kde_keyboard_setup] table of the config/ directory:

1. packages, the packages the task ensures are installed: the provider of kwriteconfig6 and kreadconfig6 (libkf6config-bin), the DBus client used for the reload (qdbus-qt6) and the python3-dbus bindings used to apply the hotkeys live.
2. username, home_dir and config_dir, the target user and that user's home and config directories.
3. kxkbrc_file_name and appletsrc_file_name, the KConfig file names under config_dir.
4. applet_plugin, the Plasma applet whose display style is the layout indicator.
5. layouts, the layout list in the order Caps Lock and Shift+Caps Lock cycle through.
6. switch_option, the XKB switch option.
7. use_layout_switching, whether switching is enabled.
8. indicator_display_style, how the indicator shows the current layout.
9. kwin_reload_command, the command that makes kwin re-read the keyboard layout configuration.
10. panel_restart_command, the command that restarts the Plasma panel.
11. layout_switch_shortcuts, the layout hotkeys: a table of keyboard layout switcher action names to shortcuts in Qt portable text format, empty by default.
