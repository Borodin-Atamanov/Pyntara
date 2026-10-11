# Install GUI apps

There is a task install_gui_apps. It belongs to the desktop and the fast_desktop
modes, installs the graphical applications declared in
src/pyntara/values/install_gui_apps.py from the Ubuntu archive and pins the
launcher of each to the Plasma taskbar of the desktop user. The task takes no
dependency; the catalog places it after the browser tasks, because the browser
is the program these applications are used next to.

## Declared applications

Each application is one record of GUI_APPS: a name for the run messages, the
packages it needs and the name of its menu entry. The name and the menu entry
are declared because apt cannot answer either one: dpkg knows packages, not menu
entries, and the pin needs the entry name. Adding an application is one record
in the values module and no change in the task body.

KeePassXC is the application installed today. The archive of Kubuntu 26.04 ships
the graphical application in keepassxc-full, which provides the menu entry
org.keepassxc.KeePassXC.desktop; the bare name keepassxc is a metapackage that
resolves to keepassxc-full or keepassxc-minimal. The task names keepassxc-full,
so it installs the application together with the entry the launcher points at.

## Install

Every application is installed on its own, so one application whose package
cannot be installed never blocks the others. The install path is the shared one
of pyntara.package_set: it asks dpkg which of the packages are missing, installs
exactly those through apt with the package-lock wait, refreshes the apt index
unless the run skips it, and reports every package that still failed with the
reason apt gave. A failed package is a warning of the completed task and the run
carries on.

## Taskbar pinning

The launcher of an application is pinned only when its menu entry is present on
the disk: pinning a launcher whose entry is absent would put a button on the
panel that starts nothing. A missing entry is a warning that names the path. The
pin itself is the shared flow of pyntara.plasma_panel: it gives the launcher to
the running panel through the shell scripting interface and falls back to the
appletsrc of the desktop user when no session runs, so the icon appears at once
with a session and at the next login without one. The mechanism is the one
chrome_setup and firefox_setup use.

## Idempotency record

The target state is reached when every declared package is installed and every
launcher is in the taskbar of the desktop user; the task then changes nothing.
Force mode does not change the result: apt refreshes the packages and the task
generates no persistent identity, so there is no state that a force mode would
rebuild afresh.

## Parameters

applications_directory - the directory of the menu entries the pin checks
panel_launcher_id_prefix - the prefix that turns a menu entry name into a Plasma launcher id
gui_apps - the declared applications, each with its name, its packages and its menu entry
appletsrc_file_name, appletsrc_relative_path, taskbar_plugin_names, appletsrc_launchers_key, appletsrc_launcher_group - read from the shared values module (pyntara.values.common), because chrome_setup, firefox_setup and vocalinux_setup read the same facts of the Plasma appletsrc
