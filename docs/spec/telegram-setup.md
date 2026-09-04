# Telegram Desktop setup

There is a dedicated Telegram Desktop installation task: telegram_setup. The task belongs to the desktop install mode and installs the official static Linux build of Telegram Desktop for the desktop user, writes a launcher entry that starts it from the application menu, downloads an icon and leaves the built-in auto-update enabled, so the client keeps itself current.

## Why the official static build

The described goal needs the latest version and auto-update. The official static build from the Telegram website is the only Linux build with the built-in updater enabled: it ships the Updater binary next to the Telegram binary, and its auto-update setting defaults to on. Archive and Snap builds are compiled with the updater disabled, receive their updates through the package manager and lag behind the latest release, so they cannot satisfy the goal.

## Release resolution and install

The configured latest_url is the official download link. A request to it answers a redirect to the newest versioned archive tsetup.<version>.tar.xz, so the redirect is the single source of the latest release and no version list is tracked anywhere. The task resolves the redirect with a HEAD request that reports the final url through curl --write-out, takes the archive name from that url and downloads the archive from the resolved url only when it is not already cached. The download goes to a sibling .download file and is renamed only after a successful transfer, so a cached archive name always means a complete archive.

The archive carries two files under a Telegram/ prefix: the Telegram binary and the Updater binary. The task extracts the archive to a temporary directory and copies each file into the install directory under the desktop user home when it differs from what is already there. The install directory and its files are owned by the desktop user, which is exactly what the built-in updater needs: it replaces the two files in place when it applies a release.

## Idempotency record

The archive of the last installed release stays in the root download_dir under its own name, and that name doubles as the idempotency record. A rerun compares the name the redirect resolves to with the cached archives: when the archive of the current release is present and the Telegram binary and the launcher entry exist, the latest release is already installed and the task changes nothing, so a current install is never downloaded again. When the redirect points to a newer archive, the task downloads it, installs the files, writes the launcher entry and removes every stale cached archive. The task is therefore idempotent without a separate marker file and without reading a version out of the binary.

A self-update applied by the client between two provisioning runs is safe: the client fetches the same redirect the task uses, so the next run sees the archive it no longer caches, downloads it once and reinstalls the same release over itself, which the byte comparison turns into a no-op.

## Launcher entry and icon

The launcher entry is written to home_dir/.local/share/applications/telegramdesktop.desktop with the Exec path pointing at the installed Telegram binary and the Icon path pointing at the downloaded icon, so Telegram appears in the KDE application menu. The entry content is stable and idempotent: a matching file is left alone. The icon is the official Telegram icon downloaded from the tdesktop repository into home_dir/.local/share/icons/telegram-desktop.png; a failed icon download is a warning, never a fatal error, because the launcher still starts Telegram and the icon is retried on the next run.

## Auto-update

Auto-update is enabled by default in the official build, and the user-writable install directory is what lets it work: the built-in Updater applies a release by replacing the Telegram and Updater files in the install directory, which the desktop user owns. The toggle lives in the client at Settings, Advanced, Update automatically and defaults to on; the task does not write the client internal settings, because the default already enables the behavior and the client owns that state.

## Install location

The install directory is home_dir/.local/share/Telegram under the desktop user home, the same pattern a self-updating client like Steam uses, and the launcher entry and the icon live under the same home. The fleet desktop machine has a single desktop user, configured as username and home_dir in the task config, and the auto-update writes in place without any special permission on system directories.

## Force mode

Force mode bypasses the already-installed shortcut and reinstalls the release the redirect points to. It never touches the user chat data, which lives separately in the TelegramDesktop data directory of the user home.

## Parameters

All parameters live in the [telegram_setup] table of the config/ directory.

username - the desktop user who runs the client and owns the install
home_dir - the home directory of that user; the install directory, the launcher entry and the icon are derived under it
download_dir - the root cache that keeps the archive of the last installed version, whose name doubles as the idempotency record
latest_url - the official download link that redirects to the newest tsetup archive
icon_url - the official Telegram icon url
