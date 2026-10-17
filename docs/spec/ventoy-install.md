# Ventoy install

There is a dedicated Ventoy installation task: ventoy_install. The task installs
the newest stable release of Ventoy for the desktop user, keeps one directory
per installed version under a stable link, and writes the menu entry that starts
the graphical window of Ventoy. The task belongs to the desktop and fast_desktop
install modes and has no dependency: nothing is installed from the Ubuntu
archive, because the archive carries no Ventoy package at all (measured on
Kubuntu 26.04 on 2026-10-16: apt-cache policy ventoy answers that the package
cannot be located).

## Source and version

Ventoy publishes only release archives: it has neither a package in the Ubuntu
archive nor an apt repository of its own, so the newest release of the
configured GitHub repository is the single source, and the installed version is
always the newest one instead of the version packaged for a distribution.

The newest release comes from the GitHub releases API through the shared
github_release module. The tag carries a v in front of the version (v1.1.18)
while the asset name and the version file of the release do not, so the
declared tag prefix is stripped before the version is used or compared. The
asset name is the declared template with the version substituted
(ventoy-1.1.18-linux.tar.gz). The archive carries the tools of every
architecture and the graphical launchers of every architecture, so the asset
never depends on the machine; only the name of the graphical launcher does
(VentoyGUI.x86_64), and it is built from the release spelling of the dpkg
architecture.

A release query that fails, an unparsable payload or a release without the
archive is a warning of a completed task. A machine that already carries a
complete tree keeps it, and the message says which version is kept; a machine
without a tree ends the task with the reason, because nothing else of the task
could run.

## What the release carries

The archive unpacks into one top directory (ventoy-1.1.18) with the graphical
launchers (VentoyGUI.<arch>), the console front end (Ventoy2Disk.sh), the web
front end (VentoyWeb.sh), the plugin server (VentoyPlugson.sh), the link tool
(VentoyVlnk.sh), the persistence image tools, the boot files, the version file
at ventoy/version and the tool trees of every architecture under tool/<arch>/.
The tools sit there compressed and the front ends inflate them on first use.

Every front end works from its own directory: the console front end changes into
the directory of the script, inflates the tools inside it and writes log.txt
there. The install directory is therefore written by the run as root and is
never a directory the desktop user could change, which also keeps those
inflated tools and the log out of the user home.

## Install directory and the version link

The install directory is /opt/ventoy. Each installed release lands in a
directory named after its version, and the symbolic link /opt/ventoy/current
names the active one. Every reader uses the link, so the path in the menu entry
never changes when a new version arrives.

The stable link was chosen over replacing one fixed directory in place for two
reasons. A replacement would have to either delete the previous tree, which the
rules of this project forbid, or keep a second fixed backup name that is a
special case of its own. A link over per-version directories makes the installed
version readable from the link alone, without running the program, and leaves
every previous tree on the disk, so a release the user did not want stays
recoverable.

The link is switched in a single step: the new link is created next to the old
one and renamed over it, so no reader meets a moment without a link and never a
link to a tree that is still being unpacked. The archive is unpacked into a
temporary directory inside the install directory, so the finished tree moves
into place by one rename on the same filesystem, and that temporary directory
never survives the step.

A version directory that a new unpack replaces is renamed with the declared
suffix (1.1.18-superseded, then a counter for the next one) and never deleted.
The same rename covers force mode and the repair of a damaged tree.

The install directory must not sit inside a sticky world-writable directory.
Measured on 2026-10-16: a symbolic link inside /dev/shm cannot be followed even
by root, because the kernel refuses it under the symlink protection of a sticky
directory, so the whole front end fails with a permission error. /opt is an
ordinary directory and is not affected.

## Unpacking and the acceptance query

The unpacking runs the declared tar command into the temporary directory. The
directory inside the archive is discovered rather than assumed, so a release
that renames its top directory still installs. The tree is used only when all
of the following hold: exactly one top directory came out of the archive, the
boot image, the version file and the console front end are present, the
graphical launcher of this architecture is present, and the version file agrees
with the version the release names.

Before the link is switched, the task asks the unpacked tree for its own version
with the console front end and the help flag. The front end prints the version
it carries and its usage without touching a disk, so the answer proves that the
archive was unpacked whole and that the packed tools inflate on this machine
(measured on 2026-10-16: the query printed Ventoy: 1.1.18 and the full usage).
The help flag is part of the query because the same front end without an
argument prints the same usage and exits one, which a check of the exit code
would read as a broken tree. Any other answer keeps the tree out of the install
directory and the link untouched.

## Download trust

The archive is downloaded from the official GitHub release assets of the
declared repository into the root cache, with the transfer written to a partial
name and renamed only after a complete transfer, so a file under the final name
is always a whole archive and an interrupted transfer is resumed on the next
run. No checksum is verified, for the reason recorded for i2pd and yggdrasil:
the checksum file travels over the same channel as the archive and would add a
failure point without protecting the install.

The cache is emptied after a successful install when the run deletes its
downloads, and kept otherwise, so a repeated run reuses the file.

## Elevation and the menu entry

The menu entry at /usr/local/share/applications/ventoy.desktop starts the
graphical launcher of the active version as the desktop user, with the icon the
release carries, in the utility category. The entry is written only when its
rendered text differs, so a rerun of an unchanged machine writes nothing. The
category carries one main category only: desktop-file-validate answers a hint
about a second one, because an entry with two main categories can appear twice
in the menu (measured on 2026-10-16).

The entry does not wrap the launcher in pkexec. The launcher raises its own
privileges: when it does not run as root it calls pkexec on itself, passes its
whole environment to the new process as one argument, and the restarted copy
hands that environment to the real tool tree, so the Wayland and X11 variables
of the session survive the escalation (read in LinuxGUI/Ventoy2Disk/ventoy_gui.c
of the release). pkexec alone in the entry would start the window without
DISPLAY and XAUTHORITY, because the tool deliberately drops them for a program
that is not annotated as a graphical one.

The launcher requires DISPLAY to be set, and it forces GDK_BACKEND to x11 for
the GTK build. A session without an X server therefore cannot start the window,
which is not the case on the KDE machines of this project, where XWayland is
always present.

The window itself is a GTK or a Qt program, and the launcher picks the flavour
the linker cache offers, with the distribution file tool/distro_gui_type.json
deciding between the two when both are present. Both builds were started on
Kubuntu 26.04 on 2026-10-16 and stayed open, and neither reports a missing
library. A machine whose linker cache holds neither a GTK nor a Qt library
cannot show the window, so the task names that condition as a warning instead
of leaving a menu entry that silently does nothing.

## Idempotency and force mode

The target state is reached when the current link names a complete tree of the
newest release and the menu entry holds the rendered text; the task then
returns done with changed=False, downloads nothing and runs no unpacking.

Any other run brings the machine to the newest release, which is allowed for
this task: Ventoy carries no persistent identity on the machine, so an update
destroys nothing. The previous version directory stays on the disk.

Force mode unpacks and installs the version the release names even when it is
already active: the active directory is renamed aside and rebuilt from the
archive.

## What is not verified

The password dialog of polkit and the window that follows it are not part of
the acceptance of this task, because a provisioning run must not ask the user
for a password and cannot answer one. The task verifies that the release tree
answers its own version query, that the link names a complete tree, that the
menu entry starts that tree, and that the linker cache can show a window. The
step from the menu entry through the system dialog to the window is checked by
hand on the target machine.

Writing a USB drive is not verified either: it needs a drive the provisioning
run must not touch, and it is the action the user takes in the window.

## Parameters

All parameters live in src/pyntara/values/ventoy_install.py, and the system
config holds no copy of them. The release query and the archive download run
with the engine-wide curl settings of src/pyntara/values/engine.py, through the
shared github_release module and the shared download helper, so no task spells
a curl flag itself.

GITHUB_REPO, RELEASE_TAG_PREFIX and ARCHIVE_NAME_TEMPLATE describe the source
and the asset name. DOWNLOAD_DIR is the root cache of the archive.

INSTALL_DIR, CURRENT_LINK_NAME and CURRENT_LINK_PATH are the install directory
and the stable link, INSTALL_DIR_MODE the mode of the install directory,
EXTRACT_DIR_PREFIX the temporary unpack directory, SUPERSEDED_SUFFIX the suffix
of a replaced version directory, VERSION_FILE_RELATIVE_PATH the version file
inside a tree, TREE_MARKER_FILES the files every complete tree carries and
GUI_BINARY_NAME_TEMPLATE the name of the graphical launcher.

ARCHIVE_EXTRACT_COMMAND is the unpacking command, ACCEPTANCE_COMMAND with
ACCEPTANCE_EXPECTED_TEXT the version query of an unpacked tree,
LINKER_CACHE_QUERY_COMMAND and TOOLKIT_LIBRARY_NAMES the check that a window can
be shown.

DESKTOP_ENTRY_PATH, DESKTOP_TEMPLATE_FILE_NAME (the template under
task_data/ventoy_install/), DESKTOP_ENTRY_MODE and ICON_RELATIVE_PATH describe
the menu entry.
