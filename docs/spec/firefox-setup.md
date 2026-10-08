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

## Snap removal

The task removes the snap version with snap remove. A machine that never had the
snap answers no matching snaps installed, which is the normal state of a fresh
machine and not a failure; any other error is a warning of the run.

## Install

The package is installed with apt-get when it is missing or in force mode, with
--allow-downgrades so the Mozilla build replaces the Ubuntu transitional
package. An installed package is left alone and apt keeps it current on later
runs.

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

The task sets the packaged entry firefox.desktop as the default browser of the
desktop user with xdg-settings, writing the mimeapps.list of that user. The
current value is read first, so a machine that already points at the entry is
left alone.

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
apt_source_template_file_name, apt_preferences_template_file_name - the templates under task_data/firefox_setup/
package_name, process_name, snap_name - the package, its process and the snap it replaces
apt_install_command, snap_remove_command, process_check_command - the command templates
settings_clone_command, settings_fetch_command, settings_revision_command, settings_reset_command - the git command templates
desktop_file_path, desktop_file_name, panel_launcher_id, default_browser_command, default_browser_query_command - the desktop entry, the default-browser setting and the panel launcher
appletsrc_file_name, appletsrc_relative_path, taskbar_plugin_names, appletsrc_launchers_key, appletsrc_launcher_group - the Plasma appletsrc
kreadconfig_command, kwriteconfig_command, config_group_flag, config_key_flag - the KConfig vocabulary
runuser_command - the wrapper that runs a command as the desktop user
file_mode - the mode of every deployed configuration file
