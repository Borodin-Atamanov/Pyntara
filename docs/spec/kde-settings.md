# KDE appearance and input setup

There is a dedicated desktop task: kde_settings.

The task configures the target user's KDE desktop to the recorded manual setup: the light and dark theme switching, the color scheme and the global theme, the NumLock state, the touchpad preferences, the Wayland virtual keyboard, the window effects and their shortcuts, the night color, the lock screen, the power management, the desktop wallpaper, the XDG user directories, the Konsole profile and the SDDM login screen. It belongs to the desktop mode and depends on users_setup, so the target user exists.

## Target configuration

The KConfig values live in files under the config directory of the target user and are applied with kwriteconfig6 as that user, so the files stay owned by the user:

kdeglobals carries the theme. The color scheme and the global theme come from color_scheme and look_and_feel and are applied with the plasma-apply tools. When automatic_look_and_feel is set, the task enables the native KDE day and night switch (kdeglobals [KDE] AutomaticLookAndFeel) instead of applying a fixed theme, so a run never overwrites the current theme. The task copies the dark and light themes (look_and_feel and look_and_feel_light) into the user look and feel directory, where a copy wins over the system one, and writes the configured cursor themes into the copy defaults, so the switch applies the right cursor with the theme itself.  
kcminputrc carries the input settings: the NumLock state on startup, the touchpad preferences written into every touchpad device group, so the task works on any target hardware, and the mouse cursor theme, applied with plasma-apply-cursortheme after the kconfig records so it wins over the theme default that the day and night switch writes.  
kwinrc and plasmakeyboardrc carry the Wayland virtual keyboard.  
The generic kconfig records apply every other KConfig value: the [[kde_settings.kconfig]] array of tables names a file, the group segments, the key and the string form of the value, with an optional bool type and an optional delete that removes the key. The records cover the window effects and their parameters, the night color, the window behavior, the lock screen, the power management, the wallpaper slideshow, the spell check language, the activity history, the Konsole default profile, the file manager preferences (dolphinrc), the Kate editor and its LSP client (katerc), the file operations confirmations (kiorc), the service menu actions (kservicemenurc) and the trash limits (ktrashrc).  
The window effect shortcuts and the window switcher live in kglobalshortcutsrc and are written in the KDE primary,alternate,description format. A configured shortcut wins over any other action: after the records are applied, the task scans kglobalshortcutsrc and unbinds every action that holds a configured key in any of its shortcut slots, wherever that action lives, so the shortcut works on any target machine. The window switcher records set the primary and alternative Alt+Tab bindings and the TabBox layouts; the keyboard layout switch records set the per-layout hotkeys. Keyboard combinations are set aggressively: whatever action or process previously owned a combination the task or a KWin script claims, it is cleared and the combination is assigned to the configured action.

The task installs two KWin scripts into the user local share kwin scripts directory, window-grow-shrink and window-restore-tracker, and enables them in kwinrc [Plugins]. window-grow-shrink grows and shrinks the active window by 5 pixels on each side with Meta+Ctrl+Up and Meta+Ctrl+Down and remembers each new size as the restore size. window-restore-tracker re-remembers the restore geometry on maximize and tile events, so dragging a maximized or tiled window returns it to its current size instead of the old one. The task is maintained by Borodin-Atamanov; contact email bikog(not for spam)@pm.me.

The user-level plain files:

user-dirs.dirs folds the XDG user directories into Downloads from the user_dirs map, keeping unrelated lines and comments.  
The Pyntara Konsole profile is rendered from the task_data template task_data/kde_settings/Pyntara.profile with the configured home directory and written under the user local share directory.  
user-places.xbel, the Dolphin Places panel, gets the system places from places_hidden hidden by their bookmark title: the task adds the IsHidden marker to the matching entries and leaves every other entry, the automatic device separators and the user bookmarks, untouched. A missing file is not an error: the desktop creates it at the first login, and the next run applies the hiding.  

The system files:

sddm.conf and sddm.conf.d/20-kubuntu.conf carry the login screen autologin and theme and are written as root from the sddm_* parameters.

## Apply mechanism

The task reads every current value with kreadconfig6 and applies only what differs, through runuser with HOME set to the configured home_dir for the user files and directly as root for the system files. The appearance is written into the config files with kwriteconfig6 and then applied to the running session with the plasma-apply tools when the automatic switch is off; the tools run only when a session is present, as a best effort that never fails the task. Each settings step runs independently: a step that fails through an external tool error or an environment error is reported as a warning and the remaining independent steps still run, because one bad setting must not stop the rest. Missing packages are installed first, each attempted individually; a package that cannot be installed is reported in the warnings and the task stops its own settings, because its mechanism is incomplete, and completes as done with warnings.

## Apply immediately

The appearance values are written into kdeglobals and kcminputrc with kwriteconfig6 first, so they apply after the next login even when no session runs. The plasma-apply tools then run with the live session environment of the target user when a session is running: the session environment is read once for the whole run from the session manager of the desktop user (the [engine] table names that account and the command that prints its environment) and exported to every task and every child process, so even a run started over SSH without a desktop environment reaches the running compositor and the theme changes apply at once. The task reads the same environment for its own tools; when no live session is found, the environment the GUI tools would receive is absent, nothing is started against a display that is not there, and the values apply at the next login. A crashing or missing plasma-apply tool loses only the live switch, never the persistent value, so it is reported and does not fail the task; a failed kwin reload or desktop count change is likewise reported as a warning. The kwinrc and kdeglobals writes add the kwriteconfig6 --notify flag when a session is running, so kwin re-reads the file on every write and enables or disables effects and their parameters immediately; without a session the flag is omitted and the settings apply at the next login. The NumLock state applies at the next Plasma startup, the input device settings at the next login, and the SDDM settings at the next boot. When the Wayland input method changed or a KWin script was installed or enabled, the task runs the configured kwin_reload_command as a best effort, so the scripts load on the running session; without a session they load at the next login. When the desktop count differs, the task creates the missing desktops at the end of the desktop list through the VirtualDesktopManager DBus API, so the existing desktops keep their place and names, and removes the trailing extras; the desktop ids are read through an embedded python3-dbus client, because qdbus6 cannot render the desktop list type. When a session is running, the script hotkeys are released from their current owners in the running KGlobalAccel daemon through python3-dbus under the system interpreter named by system_python of the [engine] table (an absolute path keeps the client independent of the caller PATH, where the project venv could shadow python3 with an interpreter that cannot import the python3-dbus bindings), so the scripts grab the keys immediately; without a session the freed records apply at the next login.

## Idempotency

The task reads every current value and applies only what differs. The target state is reached when every configured value already matches and the packages are installed; the task then returns done with changed=False. Force mode applies every value regardless.

## Parameters

All parameters live in the [kde_settings] table of the config/ directory:

packages, the packages the task ensures are installed.  
username and home_dir, the target user and that user's home directory.  
user_dirs, the XDG user directories folded into Downloads.  
places_hidden, the Dolphin Places panel system entries hidden by title in user-places.xbel.  
places_namespaces and places_metadata_owner, the XBEL prefixes with the namespace address each one is declared with, and the owner an entry metadata block must carry before the task may hide that place.  
kdeglobals_file_name, kcminputrc_file_name, kwinrc_file_name, plasma_keyboard_file_name and global_shortcuts_file_name with general_group, kde_group, mouse_group, keyboard_group, wayland_group, virtual_keyboard_group, plugins_group and desktops_group, the KConfig files and groups the task writes.  
look_and_feel_package_key, color_scheme_key, automatic_look_and_feel_key, automatic_look_and_feel_idle_interval_key, numlock_key, input_method_key, input_method_locales_key, cursor_theme_key, click_method_key, touchpad_disable_external_mouse_key and desktop_count_key, the keys it reads and writes inside them, with kconfig_true_value and kconfig_false_value, the boolean spelling of those files.  
numlock_values and click_method_values, the value each configuration choice takes in the file, and automatic_theme_switch_idle_interval, the idle wait of the native day and night switch.  
places_root_tag, places_bookmark_tag, places_title_tag, places_metadata_path, places_metadata_owner_attribute, places_hidden_element and places_hidden_value, the structure of user-places.xbel the task matches on.  
kwin_scripts, kwin_script_files, kwin_script_hotkeys and kwin_script_actions, the KWin scripts installed, the files each one carries, the combinations the scripts claim and the actions that own them.  
script_file_mode and default_file_mode, the modes of the user files the task writes as octal strings: the KWin script files are readable by the desktop session, the files that carry this machine's own settings stay private to the user.  
color_scheme and look_and_feel, the dark theme values that describe the night side of the day and night switch.  
look_and_feel_light, the light theme the day and night switch alternates to.  
automatic_look_and_feel, whether the native day and night switch is enabled.  
cursor_theme, the mouse cursor theme applied to the desktop session with plasma-apply-cursortheme and written into the dark theme defaults.  
cursor_theme_light, the mouse cursor theme written into the light theme defaults.  
numlock_on_boot, the NumLock state on Plasma startup.  
touchpad_click_method and touchpad_disable_on_external_mouse, the touchpad preferences.  
virtual_keyboard_enabled, virtual_keyboard_input_method and virtual_keyboard_locales, the Wayland virtual keyboard.  
kwin_reload_command, the command that makes kwin re-read its configuration.  
sddm_autologin_user, sddm_autologin_session, sddm_theme, sddm_theme_cursor_size, sddm_theme_cursor_theme and sddm_theme_font, the SDDM login screen values; sddm_conf_file and sddm_theme_conf_file are the system files that carry them.  
user_config_dir, user_kwin_scripts_dir, user_look_and_feel_dir, user_places_file, user_dirs_file and konsole_profile_path, the paths the task reads and writes under home_dir; a relative path starts at that home.  
system_look_and_feel_dir and theme_defaults_dir, the system copy of the global themes and the directory inside a theme that carries its defaults, from which the task copies a theme whose defaults hold the configured cursor theme.  
The interpreter that runs the embedded DBus client is system_python of the [engine] table, the Python of the managed system, because the python3-dbus bindings install into the system Python only.  
The live session reaches the task through the [engine] table: desktop_username is the account whose live session the run reads, session_environment_command is the command that prints that session's environment (systemctl --machine=<user>@.host --user show-environment by default), session_environment_keys are the session variables the run exports to every task and every child process, and session_bus_key with session_display_keys decide whether a session counts as live.  
kconfig, the array of records that apply arbitrary KConfig values.
