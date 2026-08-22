# KDE dark appearance setup

There is a dedicated desktop task: kde_settings.

The task configures the dark appearance of the target user's KDE desktop: the color scheme that turns every Qt and KDE window dark, and the global theme (look and feel) that covers the whole desktop, including the panel, the widgets, the window decorations and the icons. It belongs to the desktop mode and depends on users_setup, so the target user exists. Both values are applied with the plasma-apply tools as the target user, so the config files stay owned by that user and the changes apply immediately when a desktop session is running.

## Target configuration

The applied values live in the kdeglobals KConfig file under the home directory of the target user:

1. The color scheme in the [General] group, ColorScheme key, set to the configured color_scheme. This is the palette every Qt and KDE window renders with.
2. The global theme in the [KDE] group, LookAndFeelPackage key, set to the configured look_and_feel package. The theme package carries the defaults for the panel theme, the window decorations and the icon theme, which Plasma resolves at runtime.

## Apply mechanism

The task reads the current values with kreadconfig6 as the target user and applies only what differs. The values are applied with the plasma-apply tools through runuser, with HOME set to the configured home_dir, so the files land in the user config directory and keep the user ownership:

1. plasma-apply-lookandfeel -a <look_and_feel> applies the whole dark global theme, including its default color scheme.
2. plasma-apply-colorscheme <color_scheme> applies the dark color scheme, writing the full palette into kdeglobals.

The global theme is applied before the color scheme, so the configured scheme wins after the theme apply. Missing packages (the provider of the plasma-apply tools and of kreadconfig6) are installed first, each failure being a fatal error.

## Apply immediately

The apply commands run with the session bus address of the target user, read from the environment of that user's kwin_wayland process, so the theme changes apply at once. When no kwin_wayland process is found (a session that is not running yet), the commands still write the config and the settings apply after the next login; this is not an error.

## Idempotency

The task reads every current value with kreadconfig6 and applies only what differs. The target state is reached when both kdeglobals values already match the configuration and the packages are installed; the task then skips with changed=False. Force mode applies both values regardless. Missing packages are installed first, each failure being a fatal error.

## Parameters

All parameters live in the [kde_settings] table of the config/ directory:

1. packages, the packages the task ensures are installed: the provider of the plasma-apply theme tools (plasma-workspace) and of kreadconfig6 (libkf6config-bin).
2. username and home_dir, the target user and that user's home directory.
3. color_scheme, the dark color scheme applied to all windows.
4. look_and_feel, the dark global theme applied to the whole desktop.

Future settings of the same task are added to the same table as new keys.
