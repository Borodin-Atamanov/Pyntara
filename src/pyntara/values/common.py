"""Values several tasks need.

A value two or more tasks need is written once and read here; a value one task
needs lives in the module of that task. A task that reads this module checks it
together with its own, so a missing shared value is reported in plain words
like any other. If a task ever needs another number than the shared one, it
declares its own value in its own module, and that is a decision, not a
convenience.

A file mode, a path, a command or a record type shared by several tasks belongs
here by the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SshDirective:
    """One directive of an ssh configuration file: a keyword and its value.

    Both ssh tasks keep their directive lists as tuples of these records, so
    the type lives here rather than in the values module of one of them, which
    would make the other section read a neighbour's module.
    """

    name: str
    value: str


# Seconds the dpkg status query may take while a task checks whether a package
# is installed.
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; the total number of attempts
# is this count plus one.
PACKAGE_INSTALL_RETRIES: int = 3

# Paths of the source KeePass vaults, relative to the clone root. Two tasks
# resolve them: local_vault_setup builds the runtime vault from the first one
# that opens, and nextdns_setup_system_wide reads the NextDNS profiles from it.
SOURCE_VAULT_PRODUCTION: str = "secrets/production.vault"
SOURCE_VAULT_DEFAULT: str = "secrets/default.vault"

# The desktop user of the machine and the home directory of that account. Eight
# sections carry an install under that home or run a command as that user
# (chrome_setup, kde_keyboard_setup, kde_settings, playwright_setup,
# scrcpy_setup, sotavpn_setup, telegram_setup, vocalinux_setup), so the pair is
# written once here.
DESKTOP_USERNAME: str = "i"
DESKTOP_HOME_DIR: str = "/home/i"

# Modes of two deployed files of the desktop tasks: the menu entry a task writes
# into the user directory is read by the desktop and carries 0644, the delivered
# binaries are executed and carry 0755. Telegram Desktop and scrcpy both deploy
# that pair, and vocalinux follows, so the modes are written once here.
LAUNCHER_FILE_MODE: int = 0o644
EXECUTABLE_FILE_MODE: int = 0o755

# Name of the line in /proc/meminfo that carries the installed RAM, with the
# separator the file uses. Two tasks read the total size from it, the swapfile
# section and the zram section, so the name is written once here; a kernel that
# renames the line is answered here.
MEMINFO_TOTAL_KEY: str = "MemTotal:"

# Name of the KConfig file of the global shortcuts. Two desktop sections write
# it, the keyboard section and the settings section, so the name is written once
# here; it names a file of KDE and belongs to no section.
SHORTCUTS_FILE_NAME: str = "kglobalshortcutsrc"

# The boolean spelling of the KConfig files, used by every section that writes a
# flag into one of them, and the KDE spelling is a word and not 1 or 0.
KCONFIG_TRUE_VALUE: str = "true"
KCONFIG_FALSE_VALUE: str = "false"

# Path and mode of the file that records the selected NextDNS profile ID. Two
# sections use it: nextdns_setup_system_wide writes the ID that its vault group
# selection chose, and dnsproxy_setup reads it to build the encrypted upstream
# addresses. The file is readable by the reader and not writable by it.
PROFILE_ID_FILE_PATH: Path = Path("/var/lib/pyntara/nextdns_profile_id")
PROFILE_ID_FILE_MODE: int = 0o644

# The names the tasks read. The list lives next to the values it names and is
# read by every task that uses this module.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "PACKAGE_INSTALL_RETRIES",
    "SOURCE_VAULT_PRODUCTION",
    "SOURCE_VAULT_DEFAULT",
    "DESKTOP_USERNAME",
    "DESKTOP_HOME_DIR",
    "LAUNCHER_FILE_MODE",
    "EXECUTABLE_FILE_MODE",
    "MEMINFO_TOTAL_KEY",
    "SHORTCUTS_FILE_NAME",
    "KCONFIG_TRUE_VALUE",
    "KCONFIG_FALSE_VALUE",
    "PROFILE_ID_FILE_PATH",
    "PROFILE_ID_FILE_MODE",
)
