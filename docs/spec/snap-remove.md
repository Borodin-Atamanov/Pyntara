# Snap removal

There is a dedicated snap removal task: snap_remove.

The task removes the snap subsystem from the machine and returns the space the
snaps occupied. Installed means nothing there; the target state is reached when
the snapd daemon, its KDE Discover integration, every snap they carried and the
directories that held the snap state are gone.

## Mission

Ubuntu installs Firefox and Thunderbird as snaps and pulls a whole runtime with
them: the daemon snapd mounts the snaps as separate filesystems, keeps its own
background service with automatic updates, and duplicates libraries, themes and
a mail client that the user never asked for. Once firefox_setup replaces the
browser with the Mozilla deb, the snap subsystem serves nothing on a desktop,
and on a server or a minimal machine it is baggage from the start. The mission
is a machine that carries only what the user needs: no snap daemon, no snap
runtime, no duplicate application, and the room the snaps held returned to the
filesystem.

## Why the package purge does the whole work

The purge of the snapd package runs the postrm of that package, and that script
does everything the removal needs: it stops every snap unit, unmounts every
snap and removes the mount directories, then removes /snap, /snap/bin,
/var/snap, /var/cache/snapd and the whole /var/lib/snapd. The task therefore
does not enumerate the snaps and does not remove them one by one: a snap that
other snaps build on cannot be removed alone, so a hand-written loop would need
a correct ordering of the base snaps and several passes, while the package
resolves that ordering itself. This is the shared tool doing the job it was
written for, and the task stays a thin, honest caller.

The package list is the daemon and its Discover integration,
plasma-discover-backend-snap, which is the only package that depends on the
daemon; kubuntu-desktop recommends it and is not removed with it. The libraries
stay: libsnapd-glib-2-1 is required by cups-daemon, libpipewire-0.3-modules and
libwireplumber-0.5-0, and libsnappy1v5 is a compression library unrelated to
snap. A machine whose packages are already gone is left alone.

## Order and dependency

The task takes no dependency. The catalog orders it after firefox_setup, so in
a desktop run the browser is the Mozilla deb before the snap subsystem goes
away, and a browser snap is never removed before its replacement exists. The
task belongs to the minimal, server and desktop modes and is absent from
fast_desktop, the quick set.

The task does not depend on firefox_setup by design, so it also runs on a
machine where that task never ran. On such a machine the transitional Ubuntu
package firefox has a PreDepends on snapd, so the purge acts on that package
too. The purge runs with --allow-downgrades: when the Mozilla source that
firefox_setup registers is present, apt has a firefox candidate without an
epoch and replaces the transitional package with the Mozilla build instead of
removing it, which is a downgrade that a plain -y refuses and would fail the
whole purge; the machine then keeps a working browser. When no such candidate
exists the transitional package goes with the daemon, and the machine is left
without a browser; this is the documented consequence of running the task
without firefox_setup first, and the task adds no check for it.

## Idempotency

The target state is reached when the snap packages are gone and the snap
directories do not exist; the task then changes nothing and says the subsystem
is not installed. A second run on a machine the task already cleaned is a plain
done result.

## Reporting

The task reports the packages it removed. A purge that fails returns a warning
with the text of the failure, so the run exits nonzero and the incomplete
removal is visible; a snap directory that survives the purge is named in a
warning, so the task never claims a state it did not reach. The space the snaps
held is not measured: a free-space figure read around the purge is noise on a
machine that writes anything else in between, and the removal itself is what
returns the room.

## Parameters

All parameters live in src/pyntara/values/snap_remove.py.

package_names - the packages whose whole purpose is snap: the snapd daemon and the Discover snap backend
snap_path_names - the directories that hold the snap state, read to confirm the space was returned
apt_purge_command - the apt purge command; the package names are appended at the call site
