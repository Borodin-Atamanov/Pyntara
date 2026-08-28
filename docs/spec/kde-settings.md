# KDE appearance and input setup

There is a dedicated desktop task: kde_settings.

The task configures the target user's KDE desktop to the recorded manual setup: the light and dark theme switching, the color scheme and the global theme, the NumLock state, the touchpad preferences, the Wayland virtual keyboard, the window effects and their shortcuts, the night color, the lock screen, the power management, the desktop wallpaper, the XDG user directories, the Konsole profile and the SDDM login screen. It belongs to the desktop mode and depends on users_setup, so the target user exists.

## Target configuration

The KConfig values live in files under the config directory of the target user and are applied with kwriteconfig6 as that user, so the files stay owned by the user:

kdeglobals carries the theme. The color scheme and the global theme come from color_scheme and look_and_feel and are applied with the plasma-apply tools. When automatic_look_and_feel is set, the task enables the native KDE day and night switch (kdeglobals [KDE] AutomaticLookAndFeel) instead of applying a fixed theme, so a run never overwrites the current theme. The task copies the dark and light themes (look_and_feel and look_and_feel_light) into the user look and feel directory, where a copy wins over the system one, and writes the configured cursor themes into the copy defaults, so the switch applies the right cursor with the theme itself.  
kcminputrc carries the input settings: the NumLock state on startup, the touchpad preferences written into every touchpad device group, so the task works on any target hardware, and the mouse cursor theme, applied with plasma-apply-cursortheme after the kconfig records so it wins over the theme default that the day and night switch writes.  
kwinrc and plasmakeyboardrc carry the Wayland virtual keyboard.  
The generic kconfig records apply every other KConfig value: the [[kde_settings.kconfig]] array of tables names a file, the group segments, the key and the string form of the value, with an optional bool type and an optional delete that removes the key. The records cover the window effects and their parameters, the night color, the window behavior, the lock screen, the power management, the wallpaper slideshow, the spell check language, the activity history, the Konsole default profile, the file manager preferences (dolphinrc), the Kate editor and its LSP client (katerc), the file operations confirmations (kiorc), the service menu actions (kservicemenurc) and the trash limits (ktrashrc).  
The window effect shortcuts and the window switcher live in kglobalshortcutsrc and are written in the KDE primary,alternate,description format. A configured shortcut wins over any other action: after the records are applied, the task scans kglobalshortcutsrc and unbinds every action that shares a configured primary key, wherever that action lives, so the shortcut works on any target machine. The window switcher records set the primary and alternative Alt+Tab bindings and the TabBox layouts; the keyboard layout switch records set the per-layout hotkeys. Keyboard combinations are set aggressively: whatever action or process previously owned a combination the task or a KWin script claims, it is cleared and the combination is assigned to the configured action.

The task installs two KWin scripts into the user local share kwin scripts directory, window-grow-shrink and window-restore-tracker, and enables them in kwinrc [Plugins]. window-grow-shrink grows and shrinks the active window by 5 pixels on each side with Meta+Ctrl+Up and Meta+Ctrl+Down and remembers each new size as the restore size. window-restore-tracker re-remembers the restore geometry on maximize and tile events, so dragging a maximized or tiled window returns it to its current size instead of the old one. The task is maintained by Borodin-Atamanov; contact email bikog(not for spam)@pm.me.

The user-level plain files:

user-dirs.dirs folds the XDG user directories into Downloads from the user_dirs map, keeping unrelated lines and comments.  
The Pyntara Konsole profile is rendered from the task_data template task_data/kde_settings/Pyntara.profile with the configured home directory and written under the user local share directory.  
user-places.xbel, the Dolphin Places panel, gets the system places from places_hidden hidden by their bookmark title: the task adds the IsHidden marker to the matching entries and leaves every other entry, the automatic device separators and the user bookmarks, untouched. A missing file is not an error: the desktop creates it at the first login, and the next run applies the hiding.  

The system files:

sddm.conf and sddm.conf.d/20-kubuntu.conf carry the login screen autologin and theme and are written as root from the sddm_* parameters.

## Apply mechanism

The task reads every current value with kreadconfig6 and applies only what differs, through runuser with HOME set to the configured home_dir for the user files and directly as root for the system files. The appearance is applied with the plasma-apply tools when the automatic switch is off. Missing packages are installed first, each failure being a fatal error.

## Apply immediately

The apply commands run with the session bus address of the target user, so the theme changes apply at once; without a session the tools still write the config and the settings apply after the next login. The NumLock state applies at the next Plasma startup, the input device settings at the next login, and the SDDM settings at the next boot. When the Wayland input method changed or a KWin script was installed or enabled, the task runs the configured kwin_reload_command as a best effort, so the scripts load on the running session; without a session they load at the next login. When a session is running, the script hotkeys are released from their current owners in the running KGlobalAccel daemon through python3-dbus, so the scripts grab the keys immediately; without a session the freed records apply at the next login.

## Idempotency

The task reads every current value and applies only what differs. The target state is reached when every configured value already matches and the packages are installed; the task then returns done with changed=False. Force mode applies every value regardless.

## Parameters

All parameters live in the [kde_settings] table of the config/ directory:

packages, the packages the task ensures are installed.  
username and home_dir, the target user and that user's home directory.  
user_dirs, the XDG user directories folded into Downloads.  
places_hidden, the Dolphin Places panel system entries hidden by title in user-places.xbel.  
color_scheme and look_and_feel, the dark theme values that describe the night side of the day and night switch.  
look_and_feel_light, the light theme the day and night switch alternates to.  
automatic_look_and_feel, whether the native day and night switch is enabled.  
cursor_theme, the mouse cursor theme applied to the desktop session with plasma-apply-cursortheme and written into the dark theme defaults.  
cursor_theme_light, the mouse cursor theme written into the light theme defaults.  
numlock_on_boot, the NumLock state on Plasma startup.  
touchpad_click_method and touchpad_disable_on_external_mouse, the touchpad preferences.  
virtual_keyboard_enabled, virtual_keyboard_input_method and virtual_keyboard_locales, the Wayland virtual keyboard.  
kwin_reload_command, the command that makes kwin re-read its configuration.  
sddm_autologin_user, sddm_autologin_session, sddm_theme, sddm_theme_cursor_size, sddm_theme_cursor_theme and sddm_theme_font, the SDDM login screen values.  
kconfig, the array of records that apply arbitrary KConfig values.
