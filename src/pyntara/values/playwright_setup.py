"""Values of the playwright_setup task.

playwright-cli is installed for the desktop user, so an agent can drive the
visible Google Chrome that chrome_setup installs with the Chrome DevTools
Protocol listener on the loopback address. nodejs and npm come from the Ubuntu
archive, and the npm package CLI_PACKAGE goes into the user prefix
HOME_DIR/USER_PREFIX_RELATIVE_PATH through NPM_INSTALL_COMMAND, run as the
desktop user through RUNUSER_COMMAND, so the binary lands at
CLI_BIN_RELATIVE_PATH inside that prefix and no root owned npm prefix is ever
used. The version is not chased: npm installs the latest release, and a rerun
whose packages are installed and whose binary answers CLI_VERSION_COMMAND
changes nothing.
"""

from __future__ import annotations

# The desktop user the tool is installed for.
USERNAME: str = "i"

# The home directory of that user.
HOME_DIR: str = "/home/i"

# The apt packages that provide the npm runtime on the target.
PACKAGES: tuple[str, ...] = ("nodejs", "npm")

# Seconds the dpkg status query may take.
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; the total number of attempts
# is this count plus one.
PACKAGE_INSTALL_RETRIES: int = 3

# The npm package that provides the playwright-cli binary.
CLI_PACKAGE: str = "@playwright/cli"

# Path of the user prefix that receives the install, relative to HOME_DIR.
USER_PREFIX_RELATIVE_PATH: str = ".local"

# Path of the playwright-cli binary, relative to that prefix.
CLI_BIN_RELATIVE_PATH: str = "bin/playwright-cli"

# Command that runs another command as the desktop user; the username and the
# home directory placeholders are replaced with the two values above.
RUNUSER_COMMAND: tuple[str, ...] = (
    "runuser",
    "-u",
    "{username}",
    "--",
    "env",
    "HOME={home_dir}",
)

# Command that prints the version of the installed binary; the binary path
# placeholder is replaced. An answer is the proof that the tool is installed.
CLI_VERSION_COMMAND: tuple[str, ...] = ("{cli_bin}", "--version")

# Command that installs the npm package into the user prefix; the package and
# the prefix placeholders are replaced.
NPM_INSTALL_COMMAND: tuple[str, ...] = (
    "npm",
    "install",
    "-g",
    "{cli_package}",
    "--prefix",
    "{prefix}",
)

# Seconds a single npm install command may run before it is killed.
NPM_INSTALL_TIMEOUT_SECONDS: int = 900

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "USERNAME",
    "HOME_DIR",
    "PACKAGES",
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "PACKAGE_INSTALL_RETRIES",
    "CLI_PACKAGE",
    "USER_PREFIX_RELATIVE_PATH",
    "CLI_BIN_RELATIVE_PATH",
    "RUNUSER_COMMAND",
    "CLI_VERSION_COMMAND",
    "NPM_INSTALL_COMMAND",
    "NPM_INSTALL_TIMEOUT_SECONDS",
)
