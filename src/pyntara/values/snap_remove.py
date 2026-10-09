"""Values of the snap_remove task.

The task removes the snap subsystem from the machine: the daemon and its KDE
Discover integration, every snap the daemon carries and the directories that
hold the snap state, so the space the snaps occupied returns to the machine.
The purge of the snapd package does the work through the postrm of that
package, which stops the snap units, unmounts every snap and removes /snap,
/var/snap, /var/cache/snapd and /var/lib/snapd. The task therefore removes no
snap on its own: a snap that another snap builds on cannot be removed alone,
and the package resolves that ordering itself.

The libraries stay. libsnapd-glib-2-1 is required by cups-daemon,
libpipewire-0.3-modules and libwireplumber-0.5-0, and libsnappy1v5 is a
compression library that has nothing to do with snap.
"""

from __future__ import annotations

from pathlib import Path

# The packages whose whole purpose is snap: the daemon and tooling that run
# snaps, and the snap backend of KDE Discover, which is the only package that
# depends on the daemon (kubuntu-desktop only recommends it). A library other
# software uses is not in this list.
PACKAGE_NAMES: tuple[str, ...] = ("snapd", "plasma-discover-backend-snap")

# Directories that hold the snap state and the snap files. The purge of snapd
# removes every one of them through the package postrm, and the task reads them
# to confirm that the space was returned.
SNAP_PATH_NAMES: tuple[Path, ...] = (
    Path("/snap"),
    Path("/var/snap"),
    Path("/var/cache/snapd"),
    Path("/var/lib/snapd"),
)

# The flags of the purge that the shared factory of utils.py does not add
# itself. The Mozilla source registered by firefox_setup offers a firefox
# candidate without an epoch, so apt replaces the transitional Ubuntu package
# with the Mozilla build during the purge, which is a downgrade that a plain -y
# refuses and would fail the whole purge; the flag lets that replacement
# through and the machine keeps a working browser. The wait for the package
# lock is not repeated here: the factory owns it.
APT_PURGE_EXTRA_FLAGS: tuple[str, ...] = ("--allow-downgrades",)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_NAMES",
    "SNAP_PATH_NAMES",
    "APT_PURGE_EXTRA_FLAGS",
)
