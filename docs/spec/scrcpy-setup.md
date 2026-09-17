# scrcpy setup

The scrcpy_setup task installs the scrcpy Android screen mirroring client for
the desktop user and gives that user a way to start it from the KDE menu. The
primary source is the newest release of the configured GitHub repository,
because its Linux archive is self-contained: it carries the client, the server
the client pushes to the device and its own adb, so nothing has to be compiled
and no system dependency has to be installed. The Ubuntu archive is the
fallback for a machine where the release cannot be reached or has no asset for
its architecture. The task belongs to the desktop install mode.

## Release install

The newest release tag, its asset list and the checksum file of the same
release come from the GitHub releases API through the shared
github_release module, so no task spells a release query itself. The asset name
is the configured archive_name_template with the release tag and the release
spelling of the dpkg architecture substituted, the architecture mapping being
the engine release_asset_architectures value. A release that carries no asset
for this architecture is a release the machine cannot use, which is exactly the
case the fallback exists for.

The archive is downloaded into the configured root cache through the engine
download call, unpacked into a temporary directory, and moved into the install
directory under a subdirectory named after the version. The directory inside
the archive is discovered rather than assumed: a release whose top directory is
renamed still installs, and only the archive name is a value of the config. The
client and the bundled adb get the configured executable mode, and the whole
tree is given to the desktop user, like every other file the task writes into
that home.

The client of the unpacked tree is asked its version command before the command
link is switched. The task does not parse a version out of the answer: scrcpy
prints a two part version, which the shared three part parser does not read, and
the version the task compares for its own decisions is read from the symbolic
link instead. A client that does not answer within the command timeout is
treated as a tree the machine cannot use: the tree is moved into the user trash
and the fallback runs. The client and the server of one release must carry the
exactly same version, which is why the archive is deployed as one unit and never
file by file.

The command the desktop user starts is a symbolic link in the user prefix
(command_relative_path) that points at the client inside the version directory.
The new link is created next to the old one and renamed over it, so a reader
never sees a missing command, and the version directory of a superseded release
is moved into the user trash after the switch, never deleted.

## Checksum verification

The release publishes a checksum file next to the archive, and the task
verifies the downloaded archive against it: the published digest is read from
the line that names the archive, and the digest of the downloaded file is
printed by the configured checksum command, so one ready tool and one string
comparison do the whole check. A mismatch discards the download and repeats it
once, because a truncated transfer is the ordinary cause.

A repeated mismatch is an integrity failure and is handled differently from
every other failure: the release is abandoned, the current installation is left
untouched and the reason is reported. The machine is never moved to the Ubuntu
archive in this case, because an integrity alarm must not replace a newer
installation with an older one.

## Fallback to the Ubuntu archive

The fallback runs when the release path is unavailable, that is a failed release
query, an unknown machine architecture, no asset for that architecture, a failed
download or unpacking, or a client that does not answer. It installs the
configured fallback_packages through the shared install_packages helper, with
the apt index refreshed once unless the run skips the refresh, so the client and
its dependencies come from the archive with its normal retry behaviour. A
package that still fails is a warning: the task completes and names what could
not be installed.

The version of the fallback install is not chased and not compared with the
release tag: the archive is the maintained source and receives its updates
through the regular apt upgrade, exactly like the other archive based tasks. The
client of the fallback install is asked its version command once, as the
acceptance check of that path, and its answer is reported.

A release source that is unreachable is not a reason to change a machine that
already carries an installed release: when the command link points at a complete
release tree, the task keeps it and reports that the newest release could not be
checked.

## Idempotency and force mode

The installed version is the name of the version directory the command link
points at, so it is read without running the client and without the network. A
run whose installed version equals the newest release tag and whose version
directory carries the client, the server and the adb changes nothing and
downloads nothing. Any other run brings the machine to the newest release,
which is allowed for this task: scrcpy carries no persistent identity, so
updating it destroys nothing.

Force mode reinstalls the release the tag names even when that version is
already installed: the version directory is built again from the archive and the
command link is switched to it.

## Android USB rules

The release archive carries no udev rules, and without them a desktop user
cannot reach a device connected over the cable. The task installs the
configured udev_rules_package_name package when it is missing, in both paths,
and reports a failure with what will not work, so the missing rules are visible
in the run instead of appearing later as a permission error. A machine that
reaches its device over the network (adb connect) does not need them.

## Menu entries

Two entries are written under the user home, like the ones upstream installs:
the plain entry and a console entry. Both render the configured templates under
task_data/scrcpy_setup/ and substitute absolute paths, because a desktop entry
expands neither a tilde nor an environment variable and the menu does not
promise a shell environment. The console entry keeps the client messages on
screen (--pause-on-exit=if-error) and exists for the case where the client
refuses to start: the plain entry would show that message nowhere.

The entry starts the client with no argument. With exactly one device listed by
adb the client selects it; with several it exits and names the flags that select
one (-s, -d, -e) without asking anything, so no entry needs a terminal for its
normal use. When the release tree is not installed and the fallback client is
used, the icon is the configured theme_icon_name, because the distribution
places its icon in the system theme rather than next to the binary.

## What is not verified

Mirroring a real device is not part of the acceptance of this task, because the
provisioning machine has no Android device attached. The task verifies that the
client runs and answers, that its own adb is present next to it and that the
menu entries point at an existing client. A client that starts but cannot talk
to a particular phone (a device without USB debugging enabled, a device that
refuses the RSA prompt) is outside what the task can decide, and the message the
client prints is the operator's evidence.

## Parameters

All parameters live in the [scrcpy_setup] table of the config/ directory. The
release query, the archive download and the checksum command run through the
engine settings, so no task spells a curl or a hash flag itself.

username and home_dir - the desktop user whose home holds the install

github_repo - the owner and name pair of the release repository

archive_name_template - the archive name with {asset_arch} and {release_tag} substituted

checksum_file_name - the checksum file of the same release

fallback_packages - the packages of the Ubuntu archive installed when the release path is unavailable

udev_rules_package_name - the Android USB rules package installed when it is missing

apt_binary_path - the client the Ubuntu archive installs

theme_icon_name - the icon name the desktop resolves when the release tree is not installed

download_dir - the root cache of the downloaded release file

install_dir_relative_path - the directory that holds one subdirectory per version

command_relative_path - the symbolic link the desktop user starts

launcher_relative_path and console_launcher_relative_path - the two menu entries

launcher_template_file_name and console_launcher_template_file_name - their templates under task_data/scrcpy_setup/

binary_file_name, server_file_name, adb_file_name and icon_file_name - the files the archive carries

extract_dir_prefix - the prefix of the temporary unpacking directory

trash_dir_relative_path - the user trash a superseded version directory is moved into

version_command - the client query, with {binary}

checksum_command - the digest command, with {file}

archive_extract_command - the unpacking command, with {archive} and {extract_dir}

launcher_file_mode and executable_file_mode - the modes of the deployed files

package_status_timeout_seconds and package_install_retries - the bounds of the fallback path
