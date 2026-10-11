"""Values of the install_gui_apps task.

The task installs the graphical applications of the machine from the Ubuntu
archive and pins the launcher of each to the Plasma taskbar of the desktop
user (docs/spec/install-gui-apps.md). Each application is one record of
GUI_APPS: its name for the run messages, the packages it needs and the name of
its menu entry. Every application is installed on its own, so one application
that cannot be installed never blocks the others, and adding an application is
one record here instead of a change in the task body.

The name of an application and its menu entry are declared because apt cannot
answer either one: dpkg knows packages, not menu entries, and the pin needs the
entry name to name the launcher. The archive of Kubuntu 26.04 ships the
graphical KeePassXC in keepassxc-full, which is the package that provides the
menu entry org.keepassxc.KeePassXC.desktop; the bare name keepassxc is a
metapackage that resolves to keepassxc-full or keepassxc-minimal, so the task
names the full package to be sure it installs the application with its entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuiApp:
    """One graphical application: its name, its packages and its menu entry.

    name is the label the run shows, packages are the archive packages that
    must be installed, and desktop_file_name is the file name of the menu entry
    under APPLICATIONS_DIRECTORY, which also names the panel launcher.
    """

    name: str
    packages: tuple[str, ...]
    desktop_file_name: str


# Directory the desktop reads the menu entries from and the directory the pin
# checks, so the entry is proven present before the launcher is claimed.
APPLICATIONS_DIRECTORY: Path = Path("/usr/share/applications")

# Prefix of a Plasma launcher id that names a menu entry, measured in the
# desktop entry ids chrome_setup and firefox_setup pin to the panel.
PANEL_LAUNCHER_ID_PREFIX: str = "applications:"

# The graphical applications of the machine. An all-mode value is not set here:
# the task belongs to the desktop and the fast_desktop modes, so it runs only
# where a desktop and a panel exist.
GUI_APPS: tuple[GuiApp, ...] = (
    GuiApp(
        name="KeePassXC",
        packages=("keepassxc-full",),
        desktop_file_name="org.keepassxc.KeePassXC.desktop",
    ),
)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "APPLICATIONS_DIRECTORY",
    "PANEL_LAUNCHER_ID_PREFIX",
    "GUI_APPS",
)
