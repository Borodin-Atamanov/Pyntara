# KDE keyboard layout setup

There is a dedicated desktop task: kde_keyboard_setup.

The task configures the KDE keyboard layouts and the layout indicator of the target user: the layout list and the switch option in kxkbrc, the indicator display style in the keyboard layout applet of the Plasma panel, and optional per-layout hotkeys in kglobalshortcutsrc. It belongs to the desktop mode. The values are written with kwriteconfig6 as the target user, so the config files stay owned by that user, and the changes apply immediately through a kwin reload and a panel restart; the hotkeys are applied through the kglobalaccel daemon.

## Target configuration

Two KConfig files under the configured config_dir are managed:

kxkbrc, group [Layout]. The task writes the complete group as KDE produces it: LayoutList carries the configured layouts joined by commas, Options carries the XKB switch option, Use carries whether switching is enabled, ResetOldOptions and SwitchMode carry the reset flag and the switch mode, and DisplayNames and VariantList carry one empty entry per layout. The complete group matters because kwin applies the switch option at session start only when the group is complete; a minimal group leaves a freshly installed session on the default single layout. The configured switch_option grp:caps_select means Caps Lock to the first layout and Shift+Caps Lock to the second layout, so the layout order in LayoutList decides which key gives English and which gives Russian.  
The Plasma appletsrc, the keyboard layout applet. The applet is found by its plugin declaration; its displayStyle is set to the configured indicator_display_style, so the indicator shows the country flag instead of the layout name.  
kglobalshortcutsrc, the global shortcuts file. Each entry of layout_switch_shortcuts maps a keyboard layout switcher action to a shortcut in Qt portable text format, so one hotkey switches straight to one layout.

## Write mechanism

kwriteconfig6 and kreadconfig6 run as the configured user through runuser, with HOME set to the configured home_dir, so the files land in the user config directory and keep the user ownership. The kxkbrc group is fixed; the applet configuration group is discovered from the appletsrc text: the section that declares plugin=<applet_plugin> holds its configuration in [Configuration][General] below that section. The Plasma applet ids are assigned at first panel start, so the group is never hardcoded. The hotkey entries are written into the [KDE Keyboard Layout Switcher] group of kglobalshortcutsrc, one entry per action: the shortcut, the default key none and the action name.

## Apply immediately

After a kxkbrc change the task runs the configured kwin_reload_command through the session bus of the target user, whose address is read from the environment of that user's kwin_wayland process. KWin owns the keyboard layout service on Wayland, so the reload re-reads kxkbrc and applies the layout list. The switch option is not applied by the reload: kwin reads it at session start, so on a fresh install it takes effect at the next login. When no kwin_wayland process is found (a session that is not running yet), the reload is skipped and the settings apply after the next login. After an indicator change the task runs the configured panel_restart_command, which restarts the Plasma panel so the applet re-reads its configuration.

When a desktop session is running, the supported layout hotkeys are also applied through the kglobalaccel daemon: an embedded python3-dbus script runs as the target user on the session bus under the system interpreter /usr/bin/python3 (the absolute path keeps the client independent of the caller PATH, where the project venv could shadow python3 with an interpreter that cannot import the python3-dbus bindings), frees each hotkey from its current owner (the same action the System Settings shortcuts module would ask about) and assigns it to the configured action. Keyboard combinations are set aggressively: whatever action or process previously owned a combination the task claims, it is cleared and the combination is assigned to the configured action. This is the same call the System Settings GUI makes, so the shortcut works immediately without a session restart. The daemon writes the applied state back to kglobalshortcutsrc itself. Without a session the hotkey entries written by the task are read at the next login; a key that a default shortcut (for example the Activity Switcher Meta+Q) also claims is then confirmed once in System Settings, or the task is run again after login. Only shortcuts made of the modifiers Ctrl, Alt, Shift and Meta plus one alphanumeric key are applied live; other portable forms (function keys, named keys) are only written to the file. A step that cannot be performed is reported as a warning and the task still completes: the shortcut entry is already written to the file, so a failed live apply only delays the effect until the next login, and the warning carries the client error output for diagnosis. A failed package install is likewise a warning, and the config writes are then skipped, because the kwriteconfig6 provider and the DBus client are the mechanism of the whole task.

## Idempotency

The task reads every current value with kreadconfig6 and writes only what differs. The hotkey entries are compared the same way; when a session is running, the live apply reports the before and after state of each action and counts only real changes. The target state is reached when every kxkbrc value, the displayStyle and every hotkey entry already match the configuration and the packages are installed; the task then returns done with changed=False. Force mode rewrites every value and reloads regardless. Missing packages are installed first; each failure is a warning and the remaining independent steps still run.

## Parameters

All parameters live in the [kde_keyboard_setup] table of the config/ directory:

packages, the packages the task ensures are installed: the provider of kwriteconfig6 and kreadconfig6 (libkf6config-bin), the DBus client used for the reload (qdbus-qt6) and the python3-dbus bindings used to apply the hotkeys live.  
username, home_dir and config_dir, the target user and that user's home and config directories.  
kxkbrc_file_name and appletsrc_file_name, the KConfig file names under config_dir.  
applet_plugin, the Plasma applet whose display style is the layout indicator.  
layouts, the layout list in the order Caps Lock and Shift+Caps Lock cycle through.  
switch_option, the XKB switch option.  
reset_old_options, whether kwin resets the previous XKB options before applying the configured one; together with switch_mode and the per-layout DisplayNames and VariantList it completes the [Layout] group that makes the option apply at session start.  
switch_mode, the KDE switch mode written into kxkbrc.  
use_layout_switching, whether switching is enabled.  
indicator_display_style, how the indicator shows the current layout.  
kwin_reload_command, the command that makes kwin re-read the keyboard layout configuration.  
panel_restart_command, the command that restarts the Plasma panel.  
layout_switch_shortcuts, the layout hotkeys: a table of keyboard layout switcher action names to shortcuts in Qt portable text format, empty by default.
