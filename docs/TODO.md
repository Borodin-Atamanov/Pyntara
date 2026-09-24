# TODO

Planned future work. После реализации - удаляем из этого файла.

## Cover the task_data programs with mypy

The four Python programs under task_data/ (kde_settings/list_desktop_ids.py,
keyring_setup/configure_login_keyring.py, ffmpeg_setup/wayrecord.py,
kde_keyboard_setup/apply_hotkeys.py) are outside the type check today, because
the mypy gate covers src and tests only. Adding task_data to the gate reported
78 errors in those four files on 2026-09-19: missing annotations, calls into
untyped functions and imports of dbus and PyQt6 with no stubs. Bring the
programs that run on the target machine to strict typing and add the stub
overrides, so a deployed program is checked like the application code
(docs/guides/developer-guide.md, the mypy gate). Planned as the step after the
swapfile storage work, which is why the new program of that work is written
typed from the start.

## Make the System Metrics service survive a missing piece

The service must collect and send on every machine and must not die when
something it needs is absent or rejected. Today it stands on four supports: the
deployment venv built with uv from the clone, the runtime vault and the password
that opens it, the network, and the Google Drive channel whose queue drains
through the deployed Google web app (the Telegram channel is not implemented
yet). The collector is a timer and the ingest is an inotify path unit beside
them. Task: walk every support, decide what the service does when that support
is missing, unreadable, unreachable or refusing, and turn each case into a
reported warning with a retry instead of a dead service or a silently lost
report. Worth checking first: a machine without the venv, without the password
file, without network, with a revoked Google deployment and with an unwritable
queue directory (docs/spec/system-metrics.md).

## Bring the desktop settings to the running session

The desktop tasks write config files, and a running KDE component that owns such
a file writes its own memory back over it later. Measured on 2026-09-19: one
kwriteconfig6 --notify on kxkbrc made every running component save its state
(plasma-org.kde.plasma.desktop-appletsrc 19:15:27, katerc 19:15:58,
kglobalshortcutsrc 19:15:59, kwinrc 19:16:12), which dropped values the run had
written. A read-only audit of 335 configured desktop values on a provisioned
machine found ten that did not match: six were wrong paths inside the values
themselves (the home of the recording machine; fixed with the home_dir
placeholder), one was a tab box layout name this KDE no longer ships, and the
rest were that overwrite. The plan: after the desktop tasks write their config,
make the owners read it, that is restart the compositor through
org.kde.KWin.replace, restart plasmashell for appletsrc and powerdevil for
powerdevilrc, start the session services that stay inactive, and give the
KGlobalAccel combinations again, because the actions that switch a keyboard
layout exist only once kwin holds the layouts; then decide how a written file
survives its owner, and wait by polling the real state instead of sleeping,
because the restart takes minutes on a weak machine. The recipe measured so far
is in docs/spec/kde-keyboard-setup.md. Needs a live KDE stand: the machine used
for these measurements is gone.

## Install antivirus antirootkit.

Find the best solutions, choose the best, create task to install it.