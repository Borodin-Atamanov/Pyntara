# Vocalinux setup

There is a dedicated Vocalinux voice dictation task: vocalinux_setup. The task belongs to the desktop install mode and provisions Vocalinux for the desktop user from the official AppImage release, with the app config, the autostart and the Meta+S hotkey behavior equal to the working development machine.

## Why the AppImage

The official Vocalinux release publishes an AppImage asset built with the precompiled Vulkan pywhispercpp and the CPU fallback. It bundles its own CPython, GTK and the whisper.cpp libraries, so a target machine needs no GTK or Vulkan development stack: only the FUSE runtime that Kubuntu 26.04 provides out of the box. The task verified on the development machine that the AppImage runs under FUSE, initializes the Vulkan backend on an Intel Mesa iGPU, loads the small model onto the GPU and falls back to CPU when no Vulkan device is available. The install through the official installer would rebuild pywhispercpp from source for GPU support and pull the whole apt GTK toolchain; the AppImage is the ready external artifact that avoids that build.

## Release and install

The version is pinned in the task config and the task installs exactly that release, so the pinned app config template and the verified release stay in lockstep and an upgrade is a deliberate config change. The release tag is v followed by the configured version and the asset name is Vocalinux-version-arch.AppImage, where the arch is x86_64 or aarch64 and follows the dpkg architecture of the machine. The asset is downloaded from the official GitHub release of the pinned version into the root download_dir cache and copied into the install directory under the desktop user home, then chowned and chmodded to the user. The checksum of the release asset is not verified: the source is the official release of the pinned version, the house norm for trusted GitHub downloads.

The download goes to a sibling .download file in the cache and is renamed only after a successful transfer, so a cached file name always means a complete file. A superseded install of another version is moved into the user trash, never deleted. The model is not part of the install: the task does not download a speech model at all.

## Speech model

The app config selects the whisper_cpp engine with the small model. The model file itself is downloaded by Vocalinux, not by the task: the app downloads from its pinned Hugging Face revision, verifies the sha256 against its own manifest and renames the file into the user model directory only after verification. The task cannot usefully reproduce that flow without duplicating the app URL and digest logic, so it leaves the model to the app. On a typical target (Vulkan host with 8 GB RAM or more) the model the app recommends and offers on the first dictation is small, the same model the config selects; on a weaker machine the user picks small in the Settings model picker. The first dictation without a model shows a notification that offers the download, and Settings offers the same download with a progress dialog.

## System dependencies

The app types recognized text into the focused window on Wayland through host tools, so the task installs the Wayland injection toolchain: wtype, ydotool and wl-clipboard. The ydotool package ships its own udev rule that gives the input group access to /dev/uinput and a user systemd unit, so no extra rule is needed. The task enables and starts the ydotool user unit for the desktop user through its user manager with systemctl --user, never globally, because a global enable would start the injection daemon in greeter sessions too. A desktop session that cannot be reached is not an error: the unit then runs at the next login. The libkf6config-bin package provides kwriteconfig6, which the task uses to register the Meta+S consuming shortcut.

## Input group

The app-level hotkey listener reads the keyboard devices, and /dev/input and /dev/uinput belong to the input group on Kubuntu. The task adds the desktop user to that group with usermod. The membership takes effect only at the next login, so the task tells the user to log out and back in once.

## App config and autostart

The app config is written as the exact configured template app_config_template_file_name from the task data directory: engine whisper_cpp, model small, language ru, toggle mode on super+s, autostart into the tray, sound effects, text injection through the clipboard, model keepalive and the Russian initial prompt. Writing the full config keeps the target state equal to the verified development machine and turns the app first run off, so no onboarding dialog appears.

The autostart entry is rendered from the template autostart_template_file_name with $appimage. The app autostart manager would emit a broken Exec for an AppImage: it points at the FUSE mount path of a running instance or at a venv wrapper on PATH, both wrong for a self-contained AppImage. The task therefore writes the autostart desktop entry itself with the Exec pinned to the installed AppImage path and the --start-minimized flag, so Vocalinux starts into the tray at every login.

## Meta+S consuming shortcut

Vocalinux observes keys at the app level and cannot swallow them, so super+s alone would type the S letter into the focused field (the ы letter on the Russian layout). The working machine solves this with an empty KDE global shortcut: a no-op desktop file net.local.echo.desktop (echo_desktop_relative_path and echo_desktop_template_file_name) registered under the configured shortcut_group_name with the configured shortcut_action_name set to the configured shortcut_key_sequence. Plasma then owns the combination, nothing reaches the focused field, and the Vocalinux listener still sees the raw key and toggles. The task writes the empty desktop file and sets the configured key of the configured shortcuts_file_name through kwriteconfig6 as the user. The shortcut applies at the next login, the same re-login that the input group membership needs.

## Idempotency

A rerun changes nothing when the pinned AppImage file is present, the packages are installed, the user is in the input group, the ydotool user unit is active, the config and the autostart and the empty-action files already hold the target content and the Meta+S key already matches. The AppImage is never downloaded twice: the cached file under its asset name serves the copy into the user home. Force mode rewrites the user files, re-registers the shortcut and reinstalls the AppImage from the cache.

## Install location

The install directory is the configured appimage_dir_relative_path under the desktop user home, next to the app data directory of the same version layout that the app itself uses, and the app config, the autostart entry and the empty action desktop file live under the same home at their configured relative paths. The fleet desktop machine has a single desktop user, configured as username and home_dir in the task config.

## Parameters

All parameters live in the [vocalinux_setup] table of the config/ directory.

username - the desktop user who runs Vocalinux and owns the install
home_dir - the home directory of that user; the install, the app config and the autostart entry are derived under it
download_dir - the root cache that keeps the AppImage of the pinned version
version - the pinned Vocalinux release, without the leading v of the release tag
github_repo - the owner and name pair of the release repository; the asset download URL is composed from it and the engine template github_release_download_url, so a mirror is a config change
asset_name_template - the name of the release asset with {version} and {asset_arch} substituted; the architecture part comes from the engine mapping release_asset_architectures, because every task that downloads a release asset maps the dpkg architecture the same way
packages - the system tools the app needs on Wayland plus the kwriteconfig6 provider
input_group - the group that owns /dev/input and /dev/uinput on Kubuntu
service_unit_name - the ydotool user unit enabled for the desktop user
appimage_dir_relative_path - the install directory of the AppImage under home_dir
app_config_relative_path - the app config file under that home
autostart_relative_path - the autostart entry under that home
echo_desktop_relative_path - the empty action desktop file that consumes the shortcut, under that home
app_config_template_file_name - the app config template under task_data/vocalinux_setup/ of the clone
autostart_template_file_name - the autostart entry template under the same directory, rendered with $appimage
echo_desktop_template_file_name - the empty action desktop file template under the same directory
shortcuts_file_name - the KConfig file of the empty shortcut
shortcut_group_name - the group inside that file
shortcut_entry_name - the entry inside the group, the desktop file name of the action
shortcut_action_name - the action key the shortcut is stored under
shortcut_key_sequence - the key sequence the action holds, so Plasma consumes it
user_file_mode - the mode of the written app config and autostart entry
executable_file_mode - the mode of the installed AppImage
package_status_timeout_seconds - seconds a single package status query may run
package_install_retries - install attempts after the first one for each package
runuser_command - the prefix that runs a command as the desktop user, with {username} filled in at the call site
kreadconfig_command - the reader of a KConfig entry, with {file_name} filled in at the call site
kwriteconfig_command - the writer of a KConfig entry, with {file_name} filled in at the call site
config_group_flag - the group selector of a KConfig call, with {group} filled in at the call site
config_key_flag - the key selector of a KConfig call, with {key} filled in at the call site
mkdir_command - the maker of a directory, with {path} filled in at the call site
chown_command - the owner writer of a file, with {owner} and {path} filled in at the call site
chmod_command - the mode writer of a file, with {file_mode} and {path} filled in at the call site
group_members_command - the reader of the group membership of the user, with {username} filled in at the call site
group_add_command - the command that adds the user to input_group, with {input_group} and {username} filled in at the call site
service_active_command - the state query of the user unit, with {username} and {service_unit_name} filled in at the call site
service_enable_command - the enable and start of the user unit, with {username} and {service_unit_name} filled in at the call site
