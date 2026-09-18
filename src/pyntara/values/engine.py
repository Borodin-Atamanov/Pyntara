"""Values of the engine itself.

Everything the run of the engine counts with, independent of the task it
provisions: where the task state lives and where a deployed systemd unit is
written, how long a provisioning command and a curl call may take and how often
curl retries, the curl calls every download and every release query uses, the
shape of a report record and of the ssh command it carries, the journal
vocabulary the engine mirrors its own messages with, the owners and the
conversion factors of a file the run creates, the commands that answer a
package query, a service state query and a port query, and the queries of the
local network and of the system packages the shared helpers run.

A value that one task needs lives in the module of that task; a value here is
read by the engine itself and by many tasks at once, or by a shared helper
(pyntara.utils, pyntara.logger, pyntara.public_address, pyntara.augeas and
their neighbours).

The desktop user comes from the shared module, because several sections name
the same account.
"""

from __future__ import annotations

from pathlib import Path

# Directory for runtime task state, and the one directory the systemd unit
# files of the provisioned services are written to; several tasks deploy a
# unit and share that location.
TASK_DATA_ROOT: Path = Path("/var/lib/pyntara/task-data")
SYSTEMD_UNIT_DIR: Path = Path("/etc/systemd/system")

# Seconds the resilience notice stays visible.
NOTICE_TIMEOUT: int = 7

# The word a run force list may use instead of task names to force every task
# of the resolved run set. It is compared without case, like the task names
# beside it, and it is a value because the interface of a machine is described
# like the rest of the run: a deployment whose operators say every instead of
# all changes this value.
FORCE_ALL_KEYWORD: str = "all"

# Seconds a provisioning command may run before it is killed. The bound stays
# above the longest single download, so curl reports its own give-up instead of
# being killed from outside.
COMMAND_TIMEOUT_SECONDS: int = 8000

# Per-attempt timeout in seconds of a curl call that queries metadata, for
# example the releases API. A simple request to a server gets at least 60
# seconds before it is called unreachable.
CURL_TIMEOUT_SECONDS: int = 777

# Per-attempt timeout in seconds of a curl call that downloads a file. A
# download carries megabytes and a slow link must still finish it, so the
# download budget is far larger than the metadata budget.
CURL_DOWNLOAD_TIMEOUT_SECONDS: int = 7777

# How many times curl retries a download or release query before giving up. The
# shared curl flags add --retry-all-errors, so every failure is retried, not
# only timeouts and HTTP 5xx.
CURL_RETRIES: int = 17

# Seconds curl waits before the next attempt of a download or release query.
# Three seconds give a flapping server or a short outage the time to answer
# again without stalling the whole retry budget.
CURL_RETRY_DELAY_SECONDS: int = 3

# Seconds curl may spend establishing a connection before an attempt is
# abandoned. A host that refuses fails at once, while a host that drops the
# packets spends this whole value on every attempt (measured on the target
# machine: a blocked telegram.org costs 60 s per attempt of the retry budget
# above). A task that may meet a blocked destination therefore probes the host
# with a short budget of its own first (telegram_setup,
# REACHABILITY_PROBE_TIMEOUT_SECONDS) instead of leaning on this value, and a
# simple request to a server that answers gets at least 60 seconds before it is
# called unreachable.
CURL_CONNECT_TIMEOUT_SECONDS: int = 60

# Total seconds curl may keep retrying before it gives up on its own. The bound
# keeps a dead link from retrying until the outer command timeout kills the
# process; curl reports a clear give-up error first. It matches the download
# budget, so a retry of a large download is not cut short by the total window.
CURL_RETRY_MAX_TIME_SECONDS: int = 7777

# The write-out text of a download: the byte count, the total time and the
# average speed curl reports after the transfer. The leading newline separates
# it from the progress meter, which ends without one. It is a value of its own
# and not part of the command below, because curl reads its fields as %{name}
# and the command template is a format string.
CURL_DOWNLOAD_WRITE_OUT: str = (
    "\nDownloaded %{size_download} bytes in %{time_total}s"
    " at %{speed_download} bytes/s\n"
)

# The two curl calls every task of the run uses to obtain a release asset or
# release metadata, so the flags and the progress text of a download live in
# one place and can never diverge between tasks. The retry and timeout flags
# are inserted before the URL and the URL is always the last argument, because
# curl reads its options before the URL. {output_path} of the download command
# is the file the transfer writes and {write_out} is the text above.
CURL_DOWNLOAD_COMMAND: tuple[str, ...] = (
    "curl",
    "--fail",
    "--location",
    "--show-error",
    "--output",
    "{output_path}",
    "--write-out",
    "{write_out}",
)
CURL_QUERY_COMMAND: tuple[str, ...] = (
    "curl",
    "--fail",
    "--silent",
    "--show-error",
    "--location",
)

# The parallel query the run uses when it must ask several addresses at once,
# for example the public address services. --parallel runs the transfers
# together and --parallel-max keeps them all in flight; {parallel_max} is
# replaced by the number of URLs, {timeout_seconds} by the per-transfer bound
# and {write_out} by the text below. The URLs are the last arguments.
CURL_PARALLEL_COMMAND: tuple[str, ...] = (
    "curl",
    "--parallel",
    "--parallel-max",
    "{parallel_max}",
    "--silent",
    "--max-time",
    "{timeout_seconds}",
    "--write-out",
    "{write_out}",
)

# Write-out text of a parallel transfer: the marker followed by the effective
# URL, so a merged answer text can be split back into one block per service. It
# carries %{url_effective} and is therefore a value of its own, and it must
# carry the marker below, otherwise no answer can be attributed to the service
# that gave it.
CURL_PARALLEL_WRITE_OUT: str = "\n@@pyntara-source@@ %{url_effective}\n"

# The marker that separates the answers of a parallel query; it must be the
# marker inside CURL_PARALLEL_WRITE_OUT.
CURL_PARALLEL_SOURCE_MARKER: str = "@@pyntara-source@@"

# Answers of the boolean environment flags of the run (PYNTARA_SKIP_APT_UPDATE
# and any flag added later), compared without case and without surrounding
# spaces. Every other value, including an empty one, means false; the list
# being explicit is what stops a stray 0 from enabling a flag.
ENVIRONMENT_FLAG_TRUE_VALUES: tuple[str, ...] = ("1", "true", "yes")

# Format of the moment the run writes down, used where a timestamp of the run
# appears: the prefix of a progress line and the moment a collected report
# states. One value, so the console and the report name the same moment the
# same way.
DATETIME_FORMAT: str = "%Y-%m-%d-%H-%M-%S"

# Indentation of every JSON document the address commands of the report print,
# so the collector reads one shape and a person reads it by eye.
REPORT_JSON_INDENT: int = 2

# The ssh command one record carries: the text a person copies to reach an
# address over the channel the record belongs to. The client is verbose on
# purpose, the port is always written so a reader never needs a default, an
# anonymity channel adds the proxy option below, and the user stays absent
# because the operator chooses it. {port}, {address} and {proxy_option} are
# filled by the shared builder.
SSH_REPORT_COMMAND_FORMAT: str = "ssh -v -p {port}{proxy_option} {address}"
SSH_REPORT_PROXY_OPTION_FORMAT: str = ' -o ProxyCommand="{proxy_command}"'

# The netcat client routes the connection through the local SOCKS proxy of an
# anonymity router; {proxy} is the proxy address and %h and %p are the ssh
# placeholders for the target host and port.
SSH_REPORT_SOCKS_COMMAND_FORMAT: str = "nc -X 5 -x {proxy} %h %p"

# Host part of that proxy: both routers bind their SOCKS proxy to the loopback
# interface of the machine the report describes.
SSH_REPORT_PROXY_HOST: str = "127.0.0.1"

# Field names of one report record, by the meaning of the field. Every address
# command builds its records through them, so the collector and the commands
# agree on the shape in one place.
REPORT_RECORD_KEYS: dict[str, str] = {
    "channel": "channel",
    "address": "address",
    "port": "port",
    "proxy": "proxy",
    "ssh": "ssh",
    "note": "note",
    "server": "server",
    "local_port": "local_port",
    "remote_port": "remote_port",
    "family": "family",
    "interface": "interface",
    "scope": "scope",
    "word": "word",
    "in_country": "in_country",
    "values": "values",
    "answers": "answers",
    "source": "source",
    "document": "document",
    "reason": "reason",
}

# Words the public address report writes into the family field of its records,
# by the address family the model names (ipv4, ipv6). The report of a silent
# family carries the same word next to its reason, so the consumer of the
# telemetry reads a declared word and not a spelling of the code.
REPORT_FAMILY_WORDS: dict[str, str] = {"ipv4": "ipv4", "ipv6": "ipv6"}

# Query URL of the latest release of a GitHub repository; every release query
# of every task uses this one value, with {repo} replaced by the configured
# owner/name pair, so a mirror or a proxy is changed in one place instead of
# five task modules.
GITHUB_LATEST_RELEASE_URL: str = "https://api.github.com/repos/{repo}/releases/latest"

# URL template of one pinned release asset: {repo} is the owner/name pair of
# the task, {version} the pinned version without the leading v of the tag and
# {asset_name} the asset file name. A task that installs a pinned release
# instead of the latest one composes the download URL from this template, so a
# mirror or a proxy is changed in one place.
GITHUB_RELEASE_DOWNLOAD_URL: str = (
    "https://github.com/{repo}/releases/download/v{version}/{asset_name}"
)

# Spelling of a release asset architecture per Debian architecture name. A
# release asset carries the upstream spelling (x86_64, aarch64) while
# dpkg --print-architecture reports the Debian one (amd64, arm64); the mapping
# belongs to the engine, because every task that downloads a release asset
# maps the one architecture name the same way. An architecture the mapping does
# not name keeps the dpkg spelling.
RELEASE_ASSET_ARCHITECTURES: dict[str, str] = {
    "amd64": "x86_64",
    "arm64": "aarch64",
}

# Suffix of the file a download is written to before it is renamed to its final
# name. Every task that downloads a file uses the same suffix, so a half
# written download is never mistaken for a complete one.
PARTIAL_DOWNLOAD_FILE_SUFFIX: str = ".download"

# The os-release vocabulary the run reads to learn the distribution family: the
# fields of the file that name the distribution, and the values of those fields
# that mean a Debian-based system. A derivative declares the family in ID_LIKE
# instead of ID, so both fields are searched and a value with several words is
# matched word by word.
OS_RELEASE_FAMILY_KEYS: tuple[str, ...] = ("ID", "ID_LIKE")
OS_RELEASE_DEBIAN_FAMILY_NAMES: tuple[str, ...] = ("debian", "ubuntu")

# The Python interpreter of the managed system, the one that carries the system
# packages such as python3-dbus. The absolute path keeps a task that runs an
# embedded client independent of the caller PATH, where the project venv could
# shadow python3 with an interpreter that cannot see those packages.
SYSTEM_PYTHON: str = "/usr/bin/python3"

# Identifier under which the engine mirrors its own messages into the system
# journal (bootstrap contract, Logging). It stays distinct from the
# pyntara-install identifier of the bootstrap, so a journal query separates the
# installer from the run it launched. A deployed service announces itself under
# the identifier of its own section instead, which the task passes to
# pyntara.logger.configure_journal.
JOURNAL_IDENTIFIER: str = "pyntara-engine"

# A command that writes one line into the system journal, with the identifier of
# the message and the numeric priority of the syslog level as its placeholders.
# The console keeps working without a journal, so a machine that carries another
# journal tool names it here.
JOURNAL_PRIORITY_COMMAND: tuple[str, ...] = (
    "systemd-cat",
    "--identifier",
    "{identifier}",
    "--priority",
    "{priority}",
)

# Owner of a file the run creates as root: the uid and the gid the shared
# ensure_root_owner helper applies to it. The pair belongs to the engine and
# not to a task, because eight tasks give the same ownership.
ROOT_OWNER_UID: int = 0
ROOT_OWNER_GID: int = 0

# Conversion factors the run counts with: the scale that turns a fraction into
# a percent, the bytes of a kibibyte and of a mebibyte, and the nanoseconds of
# a second, the factor that turns a file timestamp into the unit the kernel
# takes. They are values of the run, not of a machine, and the suite checks
# that the two byte factors agree.
PERCENT_SCALE: int = 100
BYTES_PER_KIB: int = 1024
BYTES_PER_MIB: int = 1048576
NANOSECONDS_PER_SECOND: int = 1000000000

# Syslog priority of a serious failure, 0 to 7, and of task progress actions,
# 0 to 7. Debug level keeps the journal detailed; errors stay at
# ERROR_PRIORITY.
ERROR_PRIORITY: int = 3
PROGRESS_PRIORITY: int = 7

# Seconds the desktop detection process check may take.
PROCESS_CHECK_TIMEOUT_SECONDS: int = 5

# Command that answers whether a process with an exact name is running: exit
# status 0 means running. {process_name} is replaced with the name, and the
# engine reads the exit status only.
PROCESS_CHECK_COMMAND: tuple[str, ...] = ("pgrep", "-x", "{process_name}")

# Seconds the engine pauses after showing a task title before running it.
TASK_START_DELAY_SECONDS: float = 0.5

# Process names whose presence marks a desktop session in the default install
# mode detection. A desktop session variable wins over these; the list is
# checked only when no session variable is set.
DESKTOP_DETECT_PROCESSES: tuple[str, ...] = (
    "kwin_wayland",
    "kwin_x11",
    "plasmashell",
    "gnome-shell",
)

# The DBus interface of the running KGlobalAccel daemon, by its parts: the bus
# name, the object path and the interface name. The keyboard tasks and the
# appearance task talk to that daemon through these values, and the clients
# under task_data/ receive them as substitutions, so no name of the desktop
# interface stands in code.
KGLOBALACCEL_BUS_NAME: str = "org.kde.kglobalaccel"
KGLOBALACCEL_OBJECT_PATH: str = "/kglobalaccel"
KGLOBALACCEL_INTERFACE_NAME: str = "org.kde.KGlobalAccel"

# Command that prints the environment of the desktop user's session manager,
# one KEY=VALUE per line; {username} is replaced with the shared
# DESKTOP_USERNAME. The machine is named as <user>@.host because the engine
# runs as root and root has its own session manager, which carries none of the
# desktop variables.
SESSION_ENVIRONMENT_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--machine",
    "{username}@.host",
    "--user",
    "show-environment",
)

# The session variables the run exports to every task and every child process.
# The list holds the variables of the desktop session only: HOME, PATH,
# SSH_AUTH_SOCK and the locale of the root process are never replaced, because
# they belong to the run and not to the desktop.
SESSION_ENVIRONMENT_KEYS: tuple[str, ...] = (
    "DBUS_SESSION_BUS_ADDRESS",
    "WAYLAND_DISPLAY",
    "DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_ID",
    "XDG_SESSION_CLASS",
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
    "XDG_MENU_PREFIX",
    "KDE_FULL_SESSION",
    "KDE_SESSION_VERSION",
)

# The session bus variable. It carries the address the DBus clients of the
# tasks connect to, so a task can reach a running service of the session with
# this one value alone.
SESSION_BUS_KEY: str = "DBUS_SESSION_BUS_ADDRESS"

# The display variables. A GUI tool reaches the compositor or the X server when
# at least one of them carries a value; without one Qt picks a platform plugin
# that cannot connect and the tool aborts. The session counts as live only when
# SESSION_BUS_KEY and one display key are present.
SESSION_DISPLAY_KEYS: tuple[str, ...] = ("WAYLAND_DISPLAY", "DISPLAY")

# Vocabulary of the UPnP client (miniupnpc) the tasks drive through
# pyntara.upnp: the calls of the client with the {command} placeholder for the
# binary the caller names, the field of its status output that carries the
# router internet address, the protocols of a mapping and the arrow of a
# mapping line of its list output. The add call carries the internal and the
# external port separately, because a rule may publish a service on another
# number than the one it listens on.
UPNPC_STATUS_COMMAND: tuple[str, ...] = ("{command}", "-s")
UPNPC_MAPPING_LIST_COMMAND: tuple[str, ...] = ("{command}", "-l")
UPNPC_MAPPING_ADD_COMMAND: tuple[str, ...] = (
    "{command}",
    "-e",
    "{description}",
    "-a",
    "{internal_address}",
    "{internal_port}",
    "{external_port}",
    "{protocol}",
)
UPNPC_EXTERNAL_ADDRESS_KEY: str = "ExternalIPAddress"
UPNPC_PROTOCOL_NAMES: tuple[str, ...] = ("TCP", "UDP")
UPNPC_MAPPING_ARROW: str = "->"

# Queries of the local network the shared helper pyntara.public_address runs:
# the addresses of this machine, the networks it is connected to and its
# default route. The route query of a family carries that family as a flag, so
# the family is the {family} placeholder of the template.
LOCAL_ADDRESSES_COMMAND: tuple[str, ...] = (
    "ip",
    "-o",
    "addr",
    "show",
    "scope",
    "global",
)
DIRECTLY_CONNECTED_NETWORKS_COMMAND: tuple[str, ...] = (
    "ip",
    "-o",
    "{family}",
    "route",
    "show",
    "proto",
    "kernel",
)
DEFAULT_ROUTE_COMMAND: tuple[str, ...] = ("ip", "-4", "route", "show", "default")

# Keyword that marks the source address of a route in the output of that
# command (the src word of iproute2). The public address is read from the token
# that follows it.
DEFAULT_ROUTE_SOURCE_KEY: str = "src"

# The augeas driver the shared helper pyntara.augeas runs, and the node prefix
# of the file it edits: every node of the augtool program is the prefix
# followed by the absolute path of the edited file.
AUGTOOL_COMMAND: tuple[str, ...] = ("augtool", "--noautoload")
AUGEAS_FILES_NODE_PREFIX: str = "/files"

# Lines of the augtool program the helper feeds on stdin, one template per line
# shape, with the placeholders the helper fills: the driver of a load entry,
# the file the load entry includes, the load call, the ownership comment of the
# node, a container node with its value, one directive, a directive inside a
# container, the removal of a name outside and inside a container, the print of
# a node, and the save call that writes the tree back.
AUGEAS_LENS_LINE: str = "set /augeas/load/entry/lens {lens}"
AUGEAS_INCL_LINE: str = "set /augeas/load/entry/incl {path}"
AUGEAS_LOAD_LINE: str = "load"
AUGEAS_PRINT_LINE: str = "print {node}"
AUGEAS_SAVE_LINE: str = "save"
AUGEAS_COMMENT_LINE: str = 'set {node}/#comment "{header}"'
AUGEAS_CONTAINER_LINE: str = "set {node}/{container}[last()] {value}"
AUGEAS_DIRECTIVE_LINE: str = 'set {node}/{name} "{value}"'
AUGEAS_CONTAINER_DIRECTIVE_LINE: str = (
    'set {node}/{container}[last()]/{name}[last()] "{value}"'
)
AUGEAS_REMOVE_LINE: str = "rm {node}/{name}"
AUGEAS_CONTAINER_REMOVE_LINE: str = "rm {node}/{container}/{name}"

# Address vocabulary of the collector modules that report the local addresses
# of a machine (pyntara.network_addresses, and the local address and directly
# connected network readers of pyntara.public_address): the iproute2 query in
# JSON form, the mapping of the command line family flag to the family the
# report uses, the mapping of that family to the family name iproute2 prints,
# which is also the word the plain address query prints, and the scope values
# iproute2 reports for a link scope and a host scope address, the two scopes a
# reader never connects through.
INTERFACE_ADDRESSES_COMMAND: tuple[str, ...] = ("ip", "-j", "addr", "show")
ADDRESS_FAMILY_BY_FLAG: dict[str, str] = {"4": "ipv4", "6": "ipv6"}
IPROUTE2_ADDRESS_FAMILY_NAMES: dict[str, str] = {
    "ipv4": "inet",
    "ipv6": "inet6",
}
LINK_SCOPE_NAME: str = "link"
HOST_SCOPE_NAME: str = "host"

# The debian package tools every task that installs a package uses: the
# architecture query and the package status query. Their arguments are the
# flags of the tools, so they live here once and no task spells them. The
# status query carries the literal ${Status}: the braces are doubled because
# the substitution helper formats the command as a template.
DPKG_ARCHITECTURE_COMMAND: tuple[str, ...] = ("dpkg", "--print-architecture")
PACKAGE_STATUS_QUERY_COMMAND: tuple[str, ...] = (
    "dpkg-query",
    "-W",
    "-f=${{Status}}",
    "{package}",
)

# The apt calls of the package helpers: the index refresh and the install of
# one package. The environment is the variable apt needs to never ask a
# question on the target machine, where nobody watches the terminal.
APT_UPDATE_COMMAND: tuple[str, ...] = ("apt-get", "update")
APT_INSTALL_COMMAND: tuple[str, ...] = ("apt-get", "install", "-y", "{package}")
APT_NONINTERACTIVE_ENVIRONMENT: dict[str, str] = {
    "DEBIAN_FRONTEND": "noninteractive",
}

# The service state queries of the shared helpers: the two systemctl calls and
# the outputs that count as enabled or running. A derivative that spells a
# state differently reports it here instead of in the code.
SYSTEMCTL_IS_ENABLED_COMMAND: tuple[str, ...] = (
    "systemctl",
    "is-enabled",
    "{unit}",
)
SYSTEMCTL_IS_ACTIVE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "is-active",
    "{unit}",
)
SYSTEMD_ENABLED_STATES: tuple[str, ...] = ("enabled", "enabled-runtime")
SYSTEMD_ACTIVE_STATE: str = "active"

# The port and process queries of the shared helpers and the stop call they use
# when the listener turns out to be the managed service.
SOCKET_LISTENER_COMMAND: tuple[str, ...] = ("ss", "-tlnp", "sport = :{port}")
SYSTEMCTL_MAIN_PID_COMMAND: tuple[str, ...] = (
    "systemctl",
    "show",
    "-p",
    "MainPID",
    "--value",
    "{unit}",
)
SYSTEMCTL_STOP_COMMAND: tuple[str, ...] = ("systemctl", "stop", "{unit}")

# Seconds the run waits for an unknown process to release a port after the
# termination signal before it is killed outright, and the pause between two
# lookups while it waits.
PORT_KILL_GRACE_SECONDS: int = 5
PORT_KILL_POLL_SECONDS: float = 0.2

# The names the readers read. The list lives next to the values it names.
READ_VALUE_NAMES: tuple[str, ...] = (
    "APT_INSTALL_COMMAND",
    "APT_NONINTERACTIVE_ENVIRONMENT",
    "APT_UPDATE_COMMAND",
    "ADDRESS_FAMILY_BY_FLAG",
    "AUGEAS_COMMENT_LINE",
    "AUGEAS_CONTAINER_DIRECTIVE_LINE",
    "AUGEAS_CONTAINER_LINE",
    "AUGEAS_CONTAINER_REMOVE_LINE",
    "AUGEAS_DIRECTIVE_LINE",
    "AUGEAS_FILES_NODE_PREFIX",
    "AUGEAS_INCL_LINE",
    "AUGEAS_LENS_LINE",
    "AUGEAS_LOAD_LINE",
    "AUGEAS_PRINT_LINE",
    "AUGEAS_REMOVE_LINE",
    "AUGEAS_SAVE_LINE",
    "AUGTOOL_COMMAND",
    "BYTES_PER_KIB",
    "BYTES_PER_MIB",
    "COMMAND_TIMEOUT_SECONDS",
    "CURL_CONNECT_TIMEOUT_SECONDS",
    "CURL_DOWNLOAD_COMMAND",
    "CURL_DOWNLOAD_TIMEOUT_SECONDS",
    "CURL_DOWNLOAD_WRITE_OUT",
    "CURL_PARALLEL_COMMAND",
    "CURL_PARALLEL_SOURCE_MARKER",
    "CURL_PARALLEL_WRITE_OUT",
    "CURL_QUERY_COMMAND",
    "CURL_RETRIES",
    "CURL_RETRY_DELAY_SECONDS",
    "CURL_RETRY_MAX_TIME_SECONDS",
    "CURL_TIMEOUT_SECONDS",
    "DATETIME_FORMAT",
    "DEFAULT_ROUTE_COMMAND",
    "DEFAULT_ROUTE_SOURCE_KEY",
    "DESKTOP_DETECT_PROCESSES",
    "DIRECTLY_CONNECTED_NETWORKS_COMMAND",
    "DPKG_ARCHITECTURE_COMMAND",
    "ENVIRONMENT_FLAG_TRUE_VALUES",
    "ERROR_PRIORITY",
    "FORCE_ALL_KEYWORD",
    "GITHUB_LATEST_RELEASE_URL",
    "GITHUB_RELEASE_DOWNLOAD_URL",
    "HOST_SCOPE_NAME",
    "INTERFACE_ADDRESSES_COMMAND",
    "IPROUTE2_ADDRESS_FAMILY_NAMES",
    "JOURNAL_IDENTIFIER",
    "JOURNAL_PRIORITY_COMMAND",
    "KGLOBALACCEL_BUS_NAME",
    "KGLOBALACCEL_INTERFACE_NAME",
    "KGLOBALACCEL_OBJECT_PATH",
    "LINK_SCOPE_NAME",
    "LOCAL_ADDRESSES_COMMAND",
    "NANOSECONDS_PER_SECOND",
    "NOTICE_TIMEOUT",
    "OS_RELEASE_DEBIAN_FAMILY_NAMES",
    "OS_RELEASE_FAMILY_KEYS",
    "PACKAGE_STATUS_QUERY_COMMAND",
    "PARTIAL_DOWNLOAD_FILE_SUFFIX",
    "PERCENT_SCALE",
    "PORT_KILL_GRACE_SECONDS",
    "PORT_KILL_POLL_SECONDS",
    "PROCESS_CHECK_COMMAND",
    "PROCESS_CHECK_TIMEOUT_SECONDS",
    "PROGRESS_PRIORITY",
    "RELEASE_ASSET_ARCHITECTURES",
    "REPORT_FAMILY_WORDS",
    "REPORT_JSON_INDENT",
    "REPORT_RECORD_KEYS",
    "ROOT_OWNER_GID",
    "ROOT_OWNER_UID",
    "SESSION_BUS_KEY",
    "SESSION_DISPLAY_KEYS",
    "SESSION_ENVIRONMENT_COMMAND",
    "SESSION_ENVIRONMENT_KEYS",
    "SSH_REPORT_COMMAND_FORMAT",
    "SSH_REPORT_PROXY_HOST",
    "SSH_REPORT_PROXY_OPTION_FORMAT",
    "SSH_REPORT_SOCKS_COMMAND_FORMAT",
    "SYSTEMD_ACTIVE_STATE",
    "SYSTEMD_ENABLED_STATES",
    "SYSTEMD_UNIT_DIR",
    "SYSTEMCTL_IS_ACTIVE_COMMAND",
    "SYSTEMCTL_IS_ENABLED_COMMAND",
    "SYSTEMCTL_MAIN_PID_COMMAND",
    "SYSTEMCTL_STOP_COMMAND",
    "SYSTEM_PYTHON",
    "SOCKET_LISTENER_COMMAND",
    "TASK_DATA_ROOT",
    "TASK_START_DELAY_SECONDS",
    "UPNPC_EXTERNAL_ADDRESS_KEY",
    "UPNPC_MAPPING_ADD_COMMAND",
    "UPNPC_MAPPING_ARROW",
    "UPNPC_MAPPING_LIST_COMMAND",
    "UPNPC_PROTOCOL_NAMES",
    "UPNPC_STATUS_COMMAND",
)
