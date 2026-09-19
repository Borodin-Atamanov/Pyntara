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

