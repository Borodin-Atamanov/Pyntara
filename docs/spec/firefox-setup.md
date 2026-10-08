# Firefox setup

There is a dedicated Firefox setup task: firefox_setup. The task belongs to the
desktop install mode, installs Firefox from the official Mozilla apt repository
in place of the snap, removes the snap version when it is present, applies the
browser defaults of the firefox-default-settings repository, sets the packaged
entry as the default browser of the desktop user and pins the Firefox launcher
to the Plasma taskbar. The task takes no dependency: the browser is not started
through the local proxy, unlike chrome_setup.

## Why the official Mozilla apt repository

Firefox is not in the Ubuntu archive as a normal package: Kubuntu ships the snap
wrapper firefox (1:1snap1) whose preinst installs the snap. The fresh deb source
is the official Mozilla repository, whose stable channel is updated continuously
and installs through apt.

## Repository registration

The task writes the armored signing key to keyring_path, the deb822 source to
apt_source_path (rendered from the template under task_data/firefox_setup/ with
the keyring path substituted) and the apt preferences file to
apt_preferences_path. The preferences file pins the origin packages.mozilla.org
with priority 1000: the Ubuntu transitional package carries the epoch 1 and
would otherwise win the install, because its version compares higher than the
Mozilla version.

An earlier scheme wrote a single-line source of the same repository at
legacy_source_path, with the signing key in /etc/apt/keyrings. While both files
stand, apt refuses to read the whole source list ("Conflicting values set for
option Signed-By regarding source ... mozilla"), so no package operation works
on the machine. The task moves that file next to itself under
legacy_source_backup_suffix, a name apt ignores, so nothing is deleted and the
move is reversible by hand; a machine without the file is left alone.

## Snap removal

The task asks whether the snap is present with snap list first and removes it
with snap remove --terminate, which stops a browser of that snap that is still
running. The presence check is needed because snap remove answers the success
code even for a snap that is not installed (the message snap "firefox" is not
installed with exit code 0, measured on liveusb_test on 2026-10-08), so that
exit code alone cannot tell a removal from a no-op. A machine without the snap is
left alone; a snap that is present and cannot be removed is a warning of the run,
and the deb install is unaffected.

## Install

The package is installed with apt-get when the real browser is missing or in
force mode, with --allow-downgrades so the Mozilla build replaces the Ubuntu
transitional package. The presence of the package alone is not the test: the
Ubuntu archive ships only the transitional package firefox (1:1snap1), whose
presence installs the snap and provides no browser, so the task treats the
browser as installed only when the Mozilla binary browser_binary_path exists. A
machine that still has no browser after the install is reported as a warning.
The install runs before the snap is removed, so the machine never loses its
browser (measured on liveusb_test on 2026-10-08).

## Defaults repository

The browser defaults live in the git repository named by settings_repo_url on
the branch settings_repo_ref. The task clones it into the root cache
settings_dir and updates it on every run, so the applied defaults follow the
repository main without a version chase.

The system/ subtree is deployed under system_root with the relative paths
preserved, root-owned mode 0644, written only when the bytes differ. It carries
the machine policy at usr/lib/firefox/distribution/policies.json and the
AutoConfig entry point with its defaults file at
usr/lib/firefox/defaults/pref/autoconfig.js and usr/lib/firefox/mozilla.cfg.

The machine policy is the single place that sets the default search engine and
the installed extensions. SearchEngines.Default sets the application default
engine (appDefaultEngineId) and leaves the user choice (defaultEngineId) alone,
so the user can still pick another engine later. ExtensionSettings with
installation_mode force_installed and an install_url makes Firefox download and
install the listed extension into every profile.

The AutoConfig file sets interface defaults with defaultPref, so every one of
them stays changeable by the user: the compact toolbar density, the sidebar
behaviour and the disabled upload of telemetry data. A setting added to that
file reaches the machine without a code change.

## Default browser

The task writes the browser into the mimeapps.list of the desktop user, in the
group Default Applications, for the keys x-scheme-handler/http,
x-scheme-handler/https and text/html, with the KConfig writer kwriteconfig6.
The xdg-settings tool is not used: on Kubuntu 26.04 it takes a KDE branch that
calls qtpaths, which is not installed (only qtpaths6 is), and the step fails
(measured on liveusb_test on 2026-10-08). Every key is read first, so a machine
that already points at the entry is left alone.

## Taskbar pinning

The task pins the launcher applications:firefox.desktop to the Plasma taskbar of
the desktop user, through the applet itself when a shell is running and into the
appletsrc of the user otherwise; the mechanism is the one of chrome_setup.

## First run

The first-run flow of Firefox is not suppressed: the repository carries no
policy for it, so a fresh profile shows the ordinary first windows of the
browser. This was decided deliberately and can be changed by adding the
DontCheckDefaultBrowser and SkipTermsOfUse policies to the repository policy.

## Idempotency record

The target state is reached when the apt source, the keyring and the preferences
file are present, the snap is absent, the package is installed, the defaults
repository is up to date, the deployed bytes match, Firefox is the default
browser and the launcher sits in the taskbar launchers; the task then changes
nothing. Force mode reinstalls the package and rewrites the deployed files.

## Parameters

username - the desktop user whose browser defaults and launcher are configured
home_dir - the home directory of that user
settings_repo_url, settings_repo_ref, settings_dir - the defaults repository, its branch and the clone cache
settings_system_tree_relative_path - the tree inside the repository deployed under system_root
mozilla_key_url, keyring_path, apt_source_path, apt_preferences_path - the Mozilla repository registration
legacy_source_path, legacy_source_backup_suffix - the leftover single-line source of the same repository and the suffix it is moved under
apt_source_template_file_name, apt_preferences_template_file_name - the templates under task_data/firefox_setup/
package_name, process_name, snap_name, browser_binary_path - the package, its process, the snap it replaces and the binary of the real build
apt_install_command, snap_remove_command, process_check_command - the command templates
settings_clone_command, settings_fetch_command, settings_revision_command, settings_reset_command - the git command templates
desktop_file_name, panel_launcher_id, mimeapps_file_name, default_browser_group, default_browser_mime_keys - the desktop entry, the default-browser entries and the panel launcher
appletsrc_file_name, appletsrc_relative_path, taskbar_plugin_names, appletsrc_launchers_key, appletsrc_launcher_group, kreadconfig_command, kwriteconfig_command, config_group_flag, config_key_flag - read from the shared values module (pyntara.values.common), because chrome_setup, vocalinux_setup, kde_settings and kde_keyboard_setup use the same facts of the Plasma appletsrc and the KConfig tools
runuser_command - the wrapper that runs a command as the desktop user
file_mode - the mode of every deployed configuration file
