"""Values of the System Metrics service, its collector and its telemetry PDF.

The service is the only deployed reader of its own parameters, so the values
live here, next to the code that runs on the target machine. The deployed units
run the modules of this package with the interpreter of the dedicated venv and
no config argument at all: every parameter of a run is a value of this module.

The queue layout, the retry mode of the send loop, the spool of the commit
command, the Google Drive channel and the printed layout of the telemetry PDF
are all described here, in the terms of docs/spec/system-metrics.md. The
collector and the PDF carry their own records, because their fields belong
together and no reader mixes them with the section fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Retry mode of the send loop: after a cycle with send attempts and no success
# the service sends one random uploadable entry per cycle and the pause grows.
# The first failed cycle waits BACKOFF_BASE_SECONDS, every further consecutive
# failure multiplies the pause by the integer BACKOFF_MULTIPLIER until
# BACKOFF_MAX_SECONDS; all values are whole seconds
# (docs/spec/system-metrics.md, section Schedule and retry).
BACKOFF_BASE_SECONDS: int = 2
BACKOFF_MULTIPLIER: int = 2
BACKOFF_MAX_SECONDS: int = 14400

# Syslog priority of a failed vault open by the senders, 0 to 7.
ERROR_PRIORITY: int = 3

# Python version of the interpreter of the dedicated venv the task creates,
# written as the minor version the distribution carries (Kubuntu 26.04 ships
# python3.14). A bare major version would leave the choice to uv, which prefers
# a CPython it downloaded itself; VENV_CREATE_COMMAND passes
# --no-managed-python for the same reason.
PYTHON_VERSION: str = "3.14"
VENV_DIR: Path = Path("/usr/local/lib/pyntara/venv")

# Layout of the interpreter inside that directory: the path of the python
# binary relative to VENV_DIR, so every module that runs the deployed code
# composes it the same way.
VENV_PYTHON_RELATIVE_PATH: str = "bin/python"

# Path of the system command commit_system_metrics. The task generates a thin
# bash command file at this path from the command template with the spool path
# and the journal identifier embedded, so any user can commit without config
# access and without root privileges. COMMIT_COMMAND is the hand-off command
# that passes one file to the queue, with {command_path} and {file}
# substituted, so the collector service and the final commit task run the same
# argv.
COMMAND_PATH: Path = Path("/usr/local/bin/commit_system_metrics")
COMMIT_COMMAND: tuple[str, ...] = ("{command_path}", "{file}")

# Name of the runtime vault backup the final task commits into System Metrics
# after the run. The {hostname} placeholder is replaced with the machine
# hostname at commit time, so the sent file is named <hostname>.kdbx, and
# VAULT_BACKUP_FILE_MODE is the mode of the temporary copy while the commit
# command reads it.
VAULT_BACKUP_FILE_NAME: str = "{hostname}.kdbx"
VAULT_BACKUP_FILE_MODE: int = 0o600

# Root of the System Metrics queue on the target machine, the strict modes of
# its directories and entries, the per-entry size limit and the drain order of
# the senders. The ingest service moves committed files from the spool into
# MAIN_OUTBOX_DIR inside this directory; the deployed service fans them out
# into the channel queues and archives sent files
# (docs/spec/system-metrics.md, section Queue architecture). Queue entries
# carry potential secrets, so SYSTEM_METRICS_DIR_MODE and QUEUE_FILE_MODE are
# the strictest possible, root only.
SYSTEM_METRICS_DIR: Path = Path("/var/lib/pyntara/metrics")
SYSTEM_METRICS_DIR_MODE: int = 0o700
QUEUE_FILE_MODE: int = 0o600
MAX_QUEUE_FILE_SIZE_BYTES: int = 104857600
SEND_ORDER: str = "oldest_first"
SEND_ORDER_NEWEST_FIRST: str = "newest_first"

# Length of the random alphanumeric suffix appended to the original file name
# of every queue entry after a dot, and the alphabet it is drawn from. The
# suffix lets entries with identical original names coexist; the sender strips
# it before upload, so the remote server receives the original name, and
# letters and digits keep an entry name readable and free of quoting.
QUEUE_FILE_SUFFIX_LENGTH: int = 12
QUEUE_FILE_SUFFIX_ALPHABET: str = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

# Directory of the System Metrics spool: the intake directory where the
# generated commit_system_metrics command publishes files for the ingest
# service to move into the main outbox. The mode is 1733: sticky, write and
# search for everyone, no listing, so spool entry names stay private; the
# commit command creates entries with QUEUE_FILE_MODE.
# SPOOL_DIR_PERMISSION_MASK is the mask of the spool mode check, and it covers
# the special bits of the mode, including the sticky bit of 1733, so a
# directory that lost or gained one is reported as wrong.
SPOOL_DIR: Path = Path("/var/spool/system_metrics")
SPOOL_DIR_MODE: int = 0o1733
SPOOL_DIR_PERMISSION_MASK: int = 0o7777

# File mode of the generated commit_system_metrics command file, and the mask
# of its check: the special bits are masked away, so only the permission bits
# have to match.
COMMAND_FILE_MODE: int = 0o755
COMMAND_PERMISSION_MASK: int = 0o777

# Names of the long-running service unit, the ingest oneshot service and the
# ingest path unit the task manages.
SERVICE_UNIT_NAME: str = "system_metrics.service"
INGEST_SERVICE_UNIT_NAME: str = "system_metrics-ingest.service"
INGEST_PATH_UNIT_NAME: str = "system_metrics-ingest.path"

# Names of the unit and command templates the task reads from
# task_data/system_metrics_setup/ of the clone and renders on the target
# machine: the long-running service, the ingest oneshot, the ingest path
# watcher, the report collector, its timer and the generated commit command.
UNIT_TEMPLATE_FILE_NAME: str = "system_metrics.service"
INGEST_UNIT_TEMPLATE_FILE_NAME: str = "system_metrics-ingest.service"
INGEST_PATH_TEMPLATE_FILE_NAME: str = "system_metrics-ingest.path"
COLLECTOR_UNIT_TEMPLATE_FILE_NAME: str = "system_metrics_collector.service"
COLLECTOR_TIMER_TEMPLATE_FILE_NAME: str = "system_metrics_collector.timer"
COMMIT_COMMAND_TEMPLATE_FILE_NAME: str = "commit_system_metrics.sh"

# Commands the task runs against the units above: the reload of systemd and
# the enable, restart and start of a unit, whose name is the {unit_name}
# placeholder of the template. One shape serves the service, the path unit and
# the collector timer, because the task runs the same call on each of them.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{unit_name}",
)
SYSTEMCTL_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "{unit_name}",
)
SYSTEMCTL_START_COMMAND: tuple[str, ...] = ("systemctl", "start", "{unit_name}")

# Commands the deployed units run: the send loop service, the ingest oneshot
# service and the report collector service, each with the interpreter of the
# venv as its {python} placeholder and the path of the single system config as
# its {config_path} one. The modules still read the sections that live in the
# config document, so the path stays an argument; they take every value of this
# module from here.
SEND_SERVICE_COMMAND: tuple[str, ...] = (
    "{python}",
    "-m",
    "pyntara.metrics",
    "{config_path}",
)
INGEST_SERVICE_COMMAND: tuple[str, ...] = (
    "{python}",
    "-m",
    "pyntara.metrics_ingest",
)
COLLECTOR_SERVICE_COMMAND: tuple[str, ...] = (
    "{python}",
    "-m",
    "pyntara.metrics_collect",
    "{config_path}",
)

# Commands of the deployed venv: the check that the venv imports the package
# and reports the repository version, the creation of the venv with the
# configured interpreter, the sync from the repository lockfile and the
# arguments that reinstall the local package when the venv is refreshed.
VENV_VERSION_COMMAND: tuple[str, ...] = (
    "{python}",
    "-c",
    "import pyntara; print(pyntara.__version__)",
)
VENV_CREATE_COMMAND: tuple[str, ...] = (
    "{uv}",
    "venv",
    "{venv_dir}",
    "--python",
    "{python_version}",
    "--no-managed-python",
)
VENV_SYNC_COMMAND: tuple[str, ...] = (
    "{uv}",
    "sync",
    "--project",
    "{repo_root}",
    "--active",
    "--locked",
    "--no-dev",
    "--no-editable",
)
VENV_REINSTALL_FLAGS: tuple[str, ...] = ("--reinstall-package", "pyntara")

# Journal identifier of the System Metrics services and of the commit command,
# so the journal of a machine separates the two producers.
SERVICE_JOURNAL_IDENTIFIER: str = "system_metrics"
COMMIT_JOURNAL_IDENTIFIER: str = "commit_system_metrics"

# Names of the queue directories inside SYSTEM_METRICS_DIR: the intake queue
# where the ingest service publishes committed files, the temp directory of the
# ingest, and the channel and archive directories of the senders.
MAIN_OUTBOX_DIR: str = "main_outbox"
TEMP_DIR: str = "temp"
MAIN_SENT_DIR: str = "main_sent"

# Prefix of the temporary files the commit command creates in the spool before
# publishing; entries with this prefix are never ingested. TEMP_NAME_RANDOM_BYTES
# is the number of random bytes in the temporary name the ingest gives a copy
# before it publishes it, hex-encoded into the name, and QUEUE_LINK_ATTEMPTS is
# the number of publication attempts before the ingest gives up on a unique
# queue name.
SPOOL_TEMP_PREFIX: str = ".commit-"
TEMP_NAME_RANDOM_BYTES: int = 8
QUEUE_LINK_ATTEMPTS: int = 5

# Name of the Google Drive channel queue directory inside SYSTEM_METRICS_DIR.
# The dispatcher links every main outbox entry here; the Google sender drains
# it into the web app and moves sent entries to the sent archive.
GOOGLE_SCRIPT_DIR: str = "google_script"

# curl timeout of a single Google Drive channel upload, in seconds, and the
# upload call itself: a curl command whose {timeout_seconds}, {file_name} and
# {key} the sender fills with the timeout above, the original name of the entry
# and the shared auth key. The form fields travel as --data-urlencode arguments
# and the content arrives on stdin through data@-, because a payload argument
# would hit the argv length limit of the kernel; the endpoint URL is the last
# argument.
GOOGLE_SCRIPT_TIMEOUT_SECONDS: int = 7777
GOOGLE_SCRIPT_UPLOAD_COMMAND: tuple[str, ...] = (
    "curl",
    "--location",
    "--max-time",
    "{timeout_seconds}",
    "--silent",
    "--show-error",
    "--data-urlencode",
    "filename={file_name}",
    "--data-urlencode",
    "pass={key}",
    "--data-urlencode",
    "data@-",
)

# Title of the vault entry that carries the Google Drive web app credentials:
# url holds the endpoint, password holds the shared auth key
# (docs/spec/secrets-model.md). The value names an entry of the
# [vault_structure] table.
GOOGLE_SCRIPT_KEY_ENTRY_TITLE: str = "google_script_key"

# Python regular expression of the Google web app deployment URL. The pattern
# carries exactly one capture group: the deploy helper extracts the deployment
# ID from the group. GOOGLE_SCRIPT_ANSWER_OK_PREFIX is the answer prefix that
# means the file was stored, and GOOGLE_SCRIPT_ANSWER_EXCERPT_CHARS is the
# longest excerpt of a refusing answer the sender prints: a provider that
# answers with a whole HTML page leaves one readable line in the journal of the
# machine instead of that page.
GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX: str = (
    r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]+)/exec$"
)
GOOGLE_SCRIPT_ANSWER_OK_PREFIX: str = "OK "
GOOGLE_SCRIPT_ANSWER_EXCERPT_CHARS: int = 200

# Name of the committed encrypted telemetry PDF, with {hostname} replaced by
# the machine hostname at collection time. The collector builds the PDF next to
# the report and commits it through the same command, so a failure of the PDF
# never stops the report (docs/spec/system-metrics.md, section Telemetry PDF).
TELEMETRY_PDF_REPORT_FILE_NAME: str = "{hostname}.pdf"

# Title of the runtime vault entry whose password encrypts the telemetry PDF
# (docs/spec/secrets-model.md), and the titles of the runtime vault entries the
# encrypted PDF carries: the machine-only secrets the operator needs to connect
# to the machine. An entry absent from the vault is skipped, so a machine
# without one of them simply omits it from the PDF.
TELEMETRY_PASSWORD_ENTRY_TITLE: str = "telemetry_password"
TELEMETRY_PDF_VAULT_ENTRY_TITLES: tuple[str, ...] = (
    "three_x_ui_credentials",
    "xray_connection",
    "rustdesk_password",
)


@dataclass(frozen=True)
class CollectorModule:
    """One console command of the report collector.

    name identifies the module in the report; command is the argv of the
    command without a shell, so no command line is ever interpreted. The
    collector runs the command, keeps its full output and classifies the result
    as ok, empty or error (docs/spec/system-metrics.md, section Report
    collector).
    """

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Collector:
    """Report collector parameters of the System Metrics service.

    The collector is a producer of the System Metrics queue: the systemd timer
    (timer_unit_name) starts the oneshot service (service_unit_name) after boot
    and at every time of daily_send_times, so a report is built when the
    machine comes up and at the hours the operator names, while new data of a
    producer is sent by the running service on its own. The service runs the
    configured console commands, keeps their full output, waits up to the retry
    window for threshold_percent of the network modules to answer, writes the
    report as report_file_name and commits it through the commit_system_metrics
    command. All waiting happens inside the service: boot_delay_seconds only
    sets the OnBootSec of the timer; retry_base_seconds, retry_multiplier and
    retry_max_seconds are the geometric backoff of the retries, in whole
    seconds; command_timeout_seconds bounds a single console command and one
    commit call; start_command starts the collector service once after
    provisioning, with {service_unit_name} substituted, and is deliberately
    non-blocking. journal_identifier is the journal identifier of the collector
    service; lock_file_path is the flock lock that keeps a second instance from
    committing. report_keys are the field names of the report document by the
    meaning of each field and report_status_words are the words the status of a
    module result carries, so the shape of the document lives here and the
    collector only fills it.

    network_modules are the console commands whose full output forms the
    network section of the report, and system_modules the ones of the system
    section; ready_percent counts only the network modules, so the collector
    waits only for addresses. A module reports ok when the command exited 0
    with non-empty output, empty when it exited 0 with empty output, error
    otherwise. The output of every module is trimmed of leading and trailing
    whitespace before it enters the report, while internal newlines are
    preserved; a module whose command prints a JSON document contributes it as
    structured data. Commands are argv arrays, never shell lines.
    """

    boot_delay_seconds: int
    daily_send_times: tuple[str, ...]
    threshold_percent: int
    retry_base_seconds: int
    retry_multiplier: int
    retry_max_seconds: int
    command_timeout_seconds: int
    service_unit_name: str
    timer_unit_name: str
    start_command: tuple[str, ...]
    journal_identifier: str
    lock_file_path: Path
    report_file_name: str
    report_file_mode: int
    report_keys: dict[str, str]
    report_status_words: dict[str, str]
    network_modules: tuple[CollectorModule, ...]
    system_modules: tuple[CollectorModule, ...]


@dataclass(frozen=True)
class TelemetryPdf:
    """Printed layout of the encrypted telemetry PDF.

    font and font_size are the printed font, line_width_chars is the line
    length in characters and margin is the page margin in points. section_ssh,
    section_secrets and section_json are the section headings in the order the
    document carries them, nextdns_module_name names the collector module whose
    output the PDF shows next to the hostname, and field_order is the order of
    the KeePass entry fields printed under every secret
    (docs/spec/system-metrics.md, section Telemetry PDF).
    """

    font: str
    font_size: int
    line_width_chars: int
    margin: int
    section_ssh: str
    section_secrets: str
    section_json: str
    nextdns_module_name: str
    field_order: tuple[str, ...]


# Path of the single system config; the deployed service reads it through the
# same loader as the installer while the sections still live in the config
# document, and the two report commands below take it as their argument.
SYSTEM_CONFIG_PATH: Path = Path("/etc/pyntara/config.toml")

# Command line entry of a deployed module: the interpreter of the venv.
# Built from VENV_DIR and VENV_PYTHON_RELATIVE_PATH, so the interpreter
# path of the target machine has one home and no module command carries a
# copy of it.
def deployed_python_path() -> str:
    """The interpreter line of a deployed module command."""

    return str(VENV_DIR / VENV_PYTHON_RELATIVE_PATH)

# Network modules: console commands whose full output forms the network section
# of the report. Unconfigured sources are simply absent, so a module is added
# or removed here without a code change. The ipv4 and ipv6 modules report every
# address of their family with its interface, its scope and the ssh command
# that connects to it. The i2pd, yggdrasil, tor_onion, port_forwarding and upnp
# modules report the channels that reach this machine, each as a record that
# carries the address, the port and the ssh command. The public_address and
# country modules still read their own section of the system config, so they
# take its path as their last argument; the others read declared values and
# take none. The nextdns and rustdesk modules print an identifier from the file
# the task of that component writes after a successful install.
# System modules: console commands whose full output forms the system section
# of the report. Their status never affects ready_percent and the waiting.
COLLECTOR: Collector = Collector(
    boot_delay_seconds=30,
    daily_send_times=("12:00:00", "00:00:00"),
    threshold_percent=50,
    retry_base_seconds=2,
    retry_multiplier=2,
    retry_max_seconds=600,
    command_timeout_seconds=120,
    service_unit_name="system_metrics_collector.service",
    timer_unit_name="system_metrics_collector.timer",
    start_command=(
        "systemctl",
        "start",
        "--no-block",
        "{service_unit_name}",
    ),
    journal_identifier="system_metrics_collector",
    lock_file_path=Path("/run/pyntara/system_metrics_collector.lock"),
    report_file_name="network-{hostname}.json",
    report_file_mode=0o600,
    report_keys={
        "generated_at": "generated_at",
        "ready_percent": "ready_percent",
        "network": "network",
        "system": "system",
        "name": "name",
        "status": "status",
        "output": "output",
    },
    report_status_words={"ok": "ok", "empty": "empty", "error": "error"},
    network_modules=(

        CollectorModule(
            name="ipv4",
            command=(deployed_python_path(), "-m", "pyntara.network_addresses", "4"),
        ),
        CollectorModule(
            name="ipv6",
            command=(deployed_python_path(), "-m", "pyntara.network_addresses", "6"),
        ),
        CollectorModule(
            name="public_address",
            command=(
                deployed_python_path(),
                "-m",
                "pyntara.public_address_report",
            ),
        ),
        CollectorModule(
            name="country",
            command=(
                deployed_python_path(),
                "-m",
                "pyntara.country_report",
            ),
        ),
        CollectorModule(
            name="i2pd",
            command=(deployed_python_path(), "-m", "pyntara.i2pd_address"),
        ),
        CollectorModule(
            name="yggdrasil",
            command=(deployed_python_path(), "-m", "pyntara.yggdrasil_address"),
        ),
        CollectorModule(
            name="tor_onion",
            command=(deployed_python_path(), "-m", "pyntara.tor_address"),
        ),
        CollectorModule(
            name="nextdns",
            command=("cat", "/var/lib/pyntara/nextdns_profile_id"),
        ),
        CollectorModule(
            name="port_forwarding",
            command=(deployed_python_path(), "-m", "pyntara.port_forwarding_state"),
        ),
        CollectorModule(
            name="upnp",
            command=(deployed_python_path(), "-m", "pyntara.upnp_forwarding_state"),
        ),
        CollectorModule(
            name="rustdesk",
            command=("cat", "/var/lib/pyntara/rustdesk_id"),
        ),
    ),
    system_modules=(

        CollectorModule(name="hostname", command=("hostname",)),
        CollectorModule(name="memory", command=("free", "-h")),
        CollectorModule(name="cpu", command=("lscpu",)),
        CollectorModule(name="disk", command=("df", "-h", "/")),
    ),
)

TELEMETRY_PDF: TelemetryPdf = TelemetryPdf(
    font="Courier",
    font_size=12,
    line_width_chars=72,
    margin=36,
    section_ssh="SSH",
    section_secrets="SECRETS",
    section_json="NETWORK.JSON",
    nextdns_module_name="nextdns",
    field_order=("username", "password", "url", "notes"),
)

# The names the deployed modules and the task read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MULTIPLIER",
    "BACKOFF_MAX_SECONDS",
    "ERROR_PRIORITY",
    "PYTHON_VERSION",
    "VENV_DIR",
    "VENV_PYTHON_RELATIVE_PATH",
    "SYSTEM_CONFIG_PATH",
    "COMMAND_PATH",
    "COMMIT_COMMAND",
    "VAULT_BACKUP_FILE_NAME",
    "VAULT_BACKUP_FILE_MODE",
    "SYSTEM_METRICS_DIR",
    "SYSTEM_METRICS_DIR_MODE",
    "QUEUE_FILE_MODE",
    "MAX_QUEUE_FILE_SIZE_BYTES",
    "SEND_ORDER",
    "SEND_ORDER_NEWEST_FIRST",
    "QUEUE_FILE_SUFFIX_LENGTH",
    "QUEUE_FILE_SUFFIX_ALPHABET",
    "SPOOL_DIR",
    "SPOOL_DIR_MODE",
    "SPOOL_DIR_PERMISSION_MASK",
    "COMMAND_FILE_MODE",
    "COMMAND_PERMISSION_MASK",
    "SERVICE_UNIT_NAME",
    "INGEST_SERVICE_UNIT_NAME",
    "INGEST_PATH_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "INGEST_UNIT_TEMPLATE_FILE_NAME",
    "INGEST_PATH_TEMPLATE_FILE_NAME",
    "COLLECTOR_UNIT_TEMPLATE_FILE_NAME",
    "COLLECTOR_TIMER_TEMPLATE_FILE_NAME",
    "COMMIT_COMMAND_TEMPLATE_FILE_NAME",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_RESTART_COMMAND",
    "SYSTEMCTL_START_COMMAND",
    "SEND_SERVICE_COMMAND",
    "INGEST_SERVICE_COMMAND",
    "COLLECTOR_SERVICE_COMMAND",
    "VENV_VERSION_COMMAND",
    "VENV_CREATE_COMMAND",
    "VENV_SYNC_COMMAND",
    "VENV_REINSTALL_FLAGS",
    "SERVICE_JOURNAL_IDENTIFIER",
    "COMMIT_JOURNAL_IDENTIFIER",
    "MAIN_OUTBOX_DIR",
    "TEMP_DIR",
    "MAIN_SENT_DIR",
    "SPOOL_TEMP_PREFIX",
    "TEMP_NAME_RANDOM_BYTES",
    "QUEUE_LINK_ATTEMPTS",
    "GOOGLE_SCRIPT_DIR",
    "GOOGLE_SCRIPT_TIMEOUT_SECONDS",
    "GOOGLE_SCRIPT_UPLOAD_COMMAND",
    "GOOGLE_SCRIPT_KEY_ENTRY_TITLE",
    "GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX",
    "GOOGLE_SCRIPT_ANSWER_OK_PREFIX",
    "GOOGLE_SCRIPT_ANSWER_EXCERPT_CHARS",
    "TELEMETRY_PDF_REPORT_FILE_NAME",
    "TELEMETRY_PASSWORD_ENTRY_TITLE",
    "TELEMETRY_PDF_VAULT_ENTRY_TITLES",
    "COLLECTOR",
    "TELEMETRY_PDF",
)
