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

