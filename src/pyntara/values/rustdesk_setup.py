"""Values of the rustdesk_setup task.

The section describes the RustDesk client installed for unattended remote
access: the release repository and the deb it delivers, the calls of the client
binary, the service unit, the generated permanent password and the client options
applied through rustdesk --option (docs/spec/rustdesk-setup.md).

The home of the desktop user comes from the shared module; the directory of the
RustDesk configuration is declared as that home plus the relative path, so the
home is written once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyntara.values import common as common_values


@dataclass(frozen=True)
class RustdeskOption:
    """One client option of the section.

    key is the rustdesk option name, value the value applied through
    rustdesk --option. The task reads the current value and sets the option only
    when it differs, so the options are idempotent. An empty value is a value
    like any other: it clears the option, which is how RustDesk itself writes an
    option that carries nothing.
    """

    key: str
    value: str


# Owner and name pair of the repository whose newest release provides the client.
GITHUB_REPO: str = "rustdesk/rustdesk"

# Directory the downloaded deb is kept in during the install.
DOWNLOAD_DIR: Path = Path("/var/cache/pyntara/rustdesk")

# Name of the release asset of the installed version; {version} is the release
# version and {asset_arch} the release architecture the engine mapping
# release_asset_architectures names for this machine.
ASSET_NAME_TEMPLATE: str = "rustdesk-{version}-{asset_arch}.deb"

# Commands of the client binary: the version query, the machine ID query, the
# option read and write and the password set. The placeholders are the option key
# and value and the password, so the arguments the task passes are data and the
# argv is a value.
VERSION_CHECK_COMMAND: tuple[str, ...] = ("rustdesk", "--version")
MACHINE_ID_COMMAND: tuple[str, ...] = ("rustdesk", "--get-id")
GET_OPTION_COMMAND: tuple[str, ...] = ("rustdesk", "--option", "{key}")
SET_OPTION_COMMAND: tuple[str, ...] = (
    "rustdesk",
    "--option",
    "{key}",
    "{value}",
)
SET_PASSWORD_COMMAND: tuple[str, ...] = ("rustdesk", "--password", "{password}")

# Commands that stop, enable and start the service unit; {service_unit_name} is
# the unit of this section.
SERVICE_STOP_COMMAND: tuple[str, ...] = (
    "systemctl",
    "stop",
    "{service_unit_name}",
)
SERVICE_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SERVICE_START_COMMAND: tuple[str, ...] = (
    "systemctl",
    "start",
    "{service_unit_name}",
)

# Name of the identity file inside CONFIG_DIR. Force mode removes it, so the
# service generates a fresh machine ID on the next start.
IDENTITY_FILE_NAME: str = "RustDesk.toml"

# Seconds one machine ID probe may take inside the readiness loop: the query
# answers as soon as the per-session daemon owns the IPC, so the probe is bounded
# far below the command timeout.
READINESS_PROBE_TIMEOUT_SECONDS: int = 5

# File that carries the machine RustDesk ID for the network report and its mode.
ID_FILE_PATH: Path = Path("/var/lib/pyntara/rustdesk_id")
ID_FILE_MODE: int = 0o644

# Title of the runtime vault entry that carries the permanent password and, in
# its username field, the machine RustDesk ID. The entry must exist in the vault
# structure.
VAULT_ENTRY_TITLE: str = "rustdesk_password"

# Name of the rustdesk system service unit.
SERVICE_UNIT_NAME: str = "rustdesk.service"

# Number of random proquint words of the permanent password and the separator
# between them.
PASSWORD_WORDS: int = 6
PASSWORD_SEPARATOR: str = " "

# Directory that holds the rustdesk client configuration of the primary desktop
# user. It follows the home of the desktop user of the shared module, so that
# home is written once. Force mode removes the identity file inside it, so the
# service generates a fresh machine ID on the next start.
CONFIG_DIR: Path = Path(common_values.DESKTOP_HOME_DIR) / ".config/rustdesk"

# Seconds a single install command may run before it is killed, and seconds the
# apt index refresh may run.
INSTALL_TIMEOUT_SECONDS: int = 600
APT_UPDATE_TIMEOUT_SECONDS: int = 600

# Install attempts after the first one.
INSTALL_RETRIES: int = 2

# Readiness loop after the service start: attempts and the pause between them, in
# seconds. The rustdesk service becomes active when its root --service process
# starts, but the per-session --server process that owns the IPC and the machine
# ID appears a moment later.
START_CHECK_ATTEMPTS: int = 10
START_CHECK_RETRY_DELAY_SECONDS: float = 1.0

# Seconds the service is given to stay up before the final state check. RustDesk
# disables and stops its own unit a moment after the start while the stop-service
# option below carries its stopped value, so a state check taken right after the
# start cannot see that failure: the pause comes from this value and the unit is
# queried after it.
SERVICE_SETTLE_DELAY_SECONDS: float = 3.0

# Client options applied through rustdesk --option. The service-stopped flag comes
# first: RustDesk sets stop-service to Y when the service is stopped on purpose,
# and its own root service then disables and stops the unit at every start, so a
# machine left with the flag set is unreachable however often the service is
# enabled. Clearing the flag is the running value of the option.
OPTIONS: tuple[RustdeskOption, ...] = (
    RustdeskOption(key="stop-service", value=""),
    RustdeskOption(key="enable-udp-punch", value="Y"),
    RustdeskOption(key="enable-ipv6-punch", value="Y"),
    RustdeskOption(key="allow-linux-headless", value="Y"),
    RustdeskOption(key="direct-server", value="Y"),
    RustdeskOption(key="direct-access-port", value="21118"),
    RustdeskOption(key="enable-abr", value="Y"),
    RustdeskOption(key="access-mode", value="password"),
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "DOWNLOAD_DIR",
    "ASSET_NAME_TEMPLATE",
    "VERSION_CHECK_COMMAND",
    "MACHINE_ID_COMMAND",
    "GET_OPTION_COMMAND",
    "SET_OPTION_COMMAND",
    "SET_PASSWORD_COMMAND",
    "SERVICE_STOP_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "SERVICE_START_COMMAND",
    "IDENTITY_FILE_NAME",
    "READINESS_PROBE_TIMEOUT_SECONDS",
    "ID_FILE_PATH",
    "ID_FILE_MODE",
    "VAULT_ENTRY_TITLE",
    "SERVICE_UNIT_NAME",
    "PASSWORD_WORDS",
    "PASSWORD_SEPARATOR",
    "CONFIG_DIR",
    "INSTALL_TIMEOUT_SECONDS",
    "APT_UPDATE_TIMEOUT_SECONDS",
    "INSTALL_RETRIES",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "SERVICE_SETTLE_DELAY_SECONDS",
    "OPTIONS",
)
