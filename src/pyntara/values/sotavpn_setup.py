"""Values of the sotavpn_setup task.

The section turns the paid Sota Connect account of the source vault into a pool
of remote exits of the 3x-ui panel: the bridge program runs for the desktop user
and serves the server list of the account on a subscription address, the panel
subscribes to that address, and the nodes join the load balancer pool the
three_x_ui_xray_setup section built (docs/spec/sotavpn-setup.md).

The desktop user and his home are declared in the shared module. The panel
vocabulary this task uses is declared in the values module of the
three_x_ui_xray_setup section, so both are imported and never copied.
"""

from __future__ import annotations

# Command that runs another command as the desktop user; {username} and
# {home_dir} are replaced with the account and the home directory of the shared
# module. The session environment of the run passes through this wrapper, and the
# task adds the user manager variables when the run has none.
RUNUSER_COMMAND: tuple[str, ...] = (
    "runuser",
    "-u",
    "{username}",
    "--",
    "env",
    "HOME={home_dir}",
)

# Archive of the bridge repository the task downloads each run. The repository
# publishes no releases, so the branch archive is the source of the latest code
# and of the version the task compares with the installed one.
ARCHIVE_URL: str = (
    "https://codeload.github.com/Borodin-Atamanov/"
    "sotavpn-subscription-for-any-client/tar.gz/refs/heads/main"
)

# Prefix and suffix of the downloaded archive file, so the file is recognizable
# in the temporary directory the download uses.
ARCHIVE_TEMP_PREFIX: str = "sotavpn-bridge"
ARCHIVE_TEMP_SUFFIX: str = ".tar.gz"

# Installer of the bridge inside the archive and the command that runs it as the
# desktop user: {python} is filled from the engine interpreter (system_python) and
# {installer_path} with the path of the extracted installer.
INSTALLER_FILE_NAME: str = "install_sotavpn_bridge.py"
INSTALLER_COMMAND: tuple[str, ...] = ("{python}", "{installer_path}", "install")

# Name of the user service the installer creates and the command that reads its
# state. The unit name mirrors INSTALL_NAME of the bridge settings; the state is
# read as root through the user manager of the account, which needs no live
# session.
SERVICE_UNIT_NAME: str = "sotavpn-bridge.service"
USER_SERVICE_IS_ACTIVE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--machine",
    "{username}@.host",
    "--user",
    "is-active",
    "{unit}",
)

# Directory of the user-mode installation, relative to the home of the desktop
# user, and the settings file inside it. Both mirror the USER_INSTALL set of the
# bridge settings; the task reads the HTTP port from that file instead of holding
# the value.
USER_INSTALL_RELATIVE_PATH: str = ".local/share/sotavpn-bridge"
SETTINGS_FILE_NAME: str = "settings.py"

# Name of the value line that carries the plain HTTP port the bridge serves. The
# task reads the settings file of the installed bridge and never writes it.
SETTINGS_HTTP_PORT_KEY: str = "HTTP_PORT"

# Title of the source vault entry whose password carries the access key of the
# Sota account. The entry must exist in the vault structure. An absent entry or
# an empty password means the pool is not configured for this run: the task
# reports it and changes nothing.
KEY_ENTRY_TITLE: str = "sotavpn_uuid"

# Subscription address of the bridge on the loopback interface: it carries the
# access key and asks for the raw answer, which is the list of vless links the
# panel turns into outbounds. The port is read from the installed settings file,
# not held here.
SUBSCRIPTION_URL_TEMPLATE: str = "http://127.0.0.1:{port}/sub/{key}/raw"

# The outbound subscription the task creates in the panel: its label, the refresh
# interval in seconds (five minutes is the floor of the panel refresh job) and the
# flags of the call. allow_private is required, because without it the panel
# refuses the loopback address of the bridge. The panel names the outbounds of
# this subscription with the prefix the pool of the three_x_ui_xray_setup section
# covers (pool_member_prefix), so the nodes join that pool by themselves.
#
# The four flags are booleans and not the 1 and 0 of a switch setting, and the
# reason is where they go: they are fields of the JSON body this task posts to
# the panel API, and json.dumps writes a Python True as the JSON word true while
# it writes 1 as the number 1, which the panel field does not take. The 1 and 0
# rule covers a switch a program of this repository reads, not a field of a
# foreign protocol.
SUBSCRIPTION_REMARK: str = "sota-bridge"
SUBSCRIPTION_UPDATE_INTERVAL_SECONDS: int = 300
SUBSCRIPTION_ENABLED: bool = True
SUBSCRIPTION_ALLOW_PRIVATE: bool = True
SUBSCRIPTION_ALLOW_INSECURE: bool = False
SUBSCRIPTION_PREPEND: bool = False

# Seconds the task waits for the panel to fetch the node list after the refresh
# call: the bridge answers from its own cache, and a cold cache means the vendor is
# asked first, which takes a few seconds, usually about twenty. A failed vendor
# call is repeated by the bridge several times with a pause between the attempts,
# so a slow vendor exceeds a short budget; a list that has not arrived within this
# budget is reported as a fact and not as a defect, because the panel fetches the
# subscription again on its own schedule.
SUBSCRIPTION_FETCH_WAIT_SECONDS: int = 90

# Seconds the bridge HTTP listener may take to answer after the installer ran, and
# the pause between two readiness probes.
BRIDGE_READY_WAIT_SECONDS: int = 60
READINESS_CHECK_DELAY_SECONDS: int = 1

# The names the task reads. The list lives next to the values it names and is read
# by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "RUNUSER_COMMAND",
    "ARCHIVE_URL",
    "ARCHIVE_TEMP_PREFIX",
    "ARCHIVE_TEMP_SUFFIX",
    "INSTALLER_FILE_NAME",
    "INSTALLER_COMMAND",
    "SERVICE_UNIT_NAME",
    "USER_SERVICE_IS_ACTIVE_COMMAND",
    "USER_INSTALL_RELATIVE_PATH",
    "SETTINGS_FILE_NAME",
    "SETTINGS_HTTP_PORT_KEY",
    "KEY_ENTRY_TITLE",
    "SUBSCRIPTION_URL_TEMPLATE",
    "SUBSCRIPTION_REMARK",
    "SUBSCRIPTION_UPDATE_INTERVAL_SECONDS",
    "SUBSCRIPTION_ENABLED",
    "SUBSCRIPTION_ALLOW_PRIVATE",
    "SUBSCRIPTION_ALLOW_INSECURE",
    "SUBSCRIPTION_PREPEND",
    "SUBSCRIPTION_FETCH_WAIT_SECONDS",
    "BRIDGE_READY_WAIT_SECONDS",
    "READINESS_CHECK_DELAY_SECONDS",
)
