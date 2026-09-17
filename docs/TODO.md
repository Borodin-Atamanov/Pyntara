# TODO

Planned future work. После реализации - удаляем из этого файла. EMPTY FILE.

## scrcpy_setup task (plan approved 2026-09-17)

Described goal: a scrcpy_setup task that installs scrcpy for the desktop
user from the newest GitHub release of Genymobile/scrcpy, falling back to
the Ubuntu archive, and gives the user a menu entry.
Implied goal: the user plugs in an Android phone, starts scrcpy from the
KDE menu and controls the device; a machine without GitHub access still
gets a working scrcpy from apt; when both sources fail the machine is left
unchanged and the report names the reason.

Facts the design rests on (probed 2026-09-17): release v4.1 ships a
self-contained linux-x86_64 archive (client, scrcpy-server, its own adb)
with a machine-generated SHA256SUMS.txt; the archive runs on the target
machine with no missing shared library; there is no linux aarch64 asset, so
the archive is the fallback there; apt carries scrcpy 3.3.4 plus
android-udev-rules; the release archive carries no desktop entry; the client
and the server must carry the exact same version.

Decisions: version identity from the symbolic link target in the user
prefix instead of a version process; one acceptance probe before the link is
switched; checksum mismatch retries the download once and then keeps the
current installation instead of falling back (an integrity alarm never
downgrades); availability failures (query, asset, download, extraction,
probe) fall back to apt; the archive tree directory is discovered, never
guessed; two menu entries like upstream; superseded version directories go
to the trash.

Stages: 1 release path, 2 apt fallback and Android USB rules, 3 menu entries
and file ownership, 4 spec and documentation index, 5 live run, full suite,
merge into main.

