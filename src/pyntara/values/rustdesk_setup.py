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

# KDE Wayland screen sharing without a dialog. On a KDE Wayland session the
# portal asks the person in front of the machine to allow a capture session, and
# that question stands between an unattended machine and a remote operator, even
# though the RustDesk access mode already accepts the connection by password. The
# answer the desktop keeps is a record in the permission store of the desktop
# user, keyed by a token the client presents; the task writes that record and the
# token, so the first connection already starts with no dialog
# (docs/spec/rustdesk-setup.md, Screen sharing without a dialog).

# The permission store of the portal and the table the screen share record lives
# in: a documented DBus interface of the desktop, reached through the session bus
# the run exports for the desktop user.
SCREENCAST_PERMISSION_TABLE: str = "screencast"

# The permission the record carries and the data argument around the payload.
# The map grants the screen share to the empty application name, which is the
# name an application that is not sandboxed has, and RustDesk is such an
# application. The data is the RestoreData struct of KDE: the session word, the
# version and a variant holding the payload bytes.
SCREENCAST_PERMISSION_ARGUMENT: str = "{'': ['yes']}"
SCREENCAST_RESTORE_SESSION: str = "KDE"
SCREENCAST_RESTORE_VERSION: int = 1
SCREENCAST_RESTORE_DATA_ARGUMENT_TEMPLATE: str = (
    "<('{restore_session}', uint32 {restore_version}, <@ay [{payload_bytes}]>)>"
)

# The RestoreData payload of one KDE screen share consent, measured on Kubuntu
# 26.04 with KDE Plasma 6.6 on 2026-10-10: the payload names the output at the
# origin, the position KDE gives the primary output, so the portal restores the
# primary screen. The bytes are the QDataStream serialization of the payload map,
# written as one string and split for reading only.
SCREENCAST_RESTORE_DATA_HEX: str = (
    "000000030000000e006f0075007400700075007400730000000900000000010000000a"
    "00000000060030007800300000000c0072006500670069006f006e0000001300000000"
    "0000000000ffffffffffffffff0000000e00770069006e0064006f0077007300010000"
    "0000000019514c6973743c57696e646f77526573746f7265496e666f3e0000000000"
)

# The two DBus calls of the step, built by the same helper as every other
# command. {table}, {token}, {app_permissions} and {data} are data.
LOOKUP_PERMISSION_RECORD_COMMAND: tuple[str, ...] = (
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.freedesktop.impl.portal.PermissionStore",
    "--object-path",
    "/org/freedesktop/impl/portal/PermissionStore",
    "--method",
    "org.freedesktop.impl.portal.PermissionStore.Lookup",
    "{table}",
    "{token}",
)
SET_PERMISSION_RECORD_COMMAND: tuple[str, ...] = (
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.freedesktop.impl.portal.PermissionStore",
    "--object-path",
    "/org/freedesktop/impl/portal/PermissionStore",
    "--method",
    "org.freedesktop.impl.portal.PermissionStore.Set",
    "{table}",
    "true",
    "{token}",
    "{app_permissions}",
    "{data}",
)

# The RustDesk local configuration of the desktop user, the section the client
# keeps its own options in and the key that carries the restore token. The task
# writes the token there, and the client presents it to the portal.
LOCAL_CONFIG_FILE_NAME: str = "RustDesk_local.toml"
LOCAL_CONFIG_FILE_MODE: int = 0o600
RESTORE_TOKEN_KEY: str = "wayland-restore-token"
RESTORE_TOKEN_OPTIONS_SECTION: str = "options"

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
    "SCREENCAST_PERMISSION_TABLE",
    "SCREENCAST_PERMISSION_ARGUMENT",
    "SCREENCAST_RESTORE_SESSION",
    "SCREENCAST_RESTORE_VERSION",
    "SCREENCAST_RESTORE_DATA_ARGUMENT_TEMPLATE",
    "SCREENCAST_RESTORE_DATA_HEX",
    "LOOKUP_PERMISSION_RECORD_COMMAND",
    "SET_PERMISSION_RECORD_COMMAND",
    "LOCAL_CONFIG_FILE_NAME",
    "LOCAL_CONFIG_FILE_MODE",
    "RESTORE_TOKEN_KEY",
    "RESTORE_TOKEN_OPTIONS_SECTION",
)
