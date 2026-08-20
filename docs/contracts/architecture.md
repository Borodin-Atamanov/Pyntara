# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## 1. Runtime boundaries

inst.sh - bootloader
pyntara.py (command entry and composition root)
config/ (config.toml loading and validation, including the task catalog)
task_catalog.py (mode defaults, task selection validation, dependency resolution)
context.py (Context construction)
task_runner.py (task discovery and execution)
tasks/*.py (one task per module)

## 2. Composition root

The run command in pyntara.py is the only place that reads the environment and assembles runtime state:

resolve the install mode (PYNTARA_INSTALL_MODE or the auto-detected default)
resolve the task set (PYNTARA_TASKS or the mode defaults, dependencies resolved)
resolve the force task list (PYNTARA_FORCE_TASKS)
create Context
launch the runner

No task may read the environment, create global singletons, or assemble runtime state itself.

## 3. Configuration

There are no CLI options. The engine configuration comes from two sources.

The config/ directory at the repository root is the single source of truth for the values used by the Python part: one TOML file per top-level section (engine.toml, cli_tools.toml, tasks.toml, ...), joined by pyntara.config.loader.render_config_source into a single document before parsing. The loader accepts both the directory form and a single file, so the deployed system config stays one file. The sections: engine.task_data_root (task data root), engine.notice_timeout (seconds the resilience notice stays visible), engine.command_timeout_seconds (ceiling for provisioning commands), engine.process_check_timeout_seconds (bound for the desktop detection process query), engine.task_start_delay_seconds (pause between the task banner and the task start), engine.desktop_detect_processes (process names whose presence marks a desktop session in the default mode detection) and per-task sections such as cli_tools.packages, cli_tools.package_status_timeout_seconds, cli_tools.package_install_retries, cli_tools.package_success_threshold_percent, add_extra_repos.components and add_extra_repos.ubuntu_hosts (Ubuntu archive components and hosts managed by add_extra_repos), zram_service (compressor, swap priority, memory fraction, fallback CPU count, alignment), swapfile_service_install (swapfile path, size formula, file mode and size tolerance), i2pd_service_setup (the GitHub repository github_repo, the download directory download_dir, the package service unit name service_unit_name, the owned configuration path config_path, the log level log_level, the web console and SOCKS proxy switches http_enabled and socks_proxy_enabled, the install retry count install_retries, the readiness loop start_check_attempts and start_check_retry_delay_seconds, the saved tunnel address file address_file_path and its mode address_file_mode, docs/spec/i2pd-service.md), yggdrasil_service_setup (the GitHub repository github_repo, the download directory download_dir, the package service unit name service_unit_name, the install retry count install_retries, the owned configuration path config_path and the PEM key path private_key_path with their file modes config_file_mode and private_key_file_mode, the TUN interface name if_name and MTU if_mtu, the admin socket admin_listen, the inbound listeners listen, the multicast blocks multicast_interfaces and the peer selection parameters peers_full_path, peers_tarball_url, peer_batch_size, peer_target_count, peer_probe_timeout_seconds, peer_max_batches, the fallback static_peers and the saved self address file address_file_path with its mode address_file_mode, docs/spec/yggdrasil-service.md), tor_setup (the package name package_name, the daemon instance unit name service_unit_name (tor@default.service), the main configuration file torrc_path that is never rewritten, the owned drop-in file torrc_dropin_path with its mode dropin_file_mode, the %include path torrc_include_path guaranteed in the main file, the log level log_level, the SOCKS proxy port socks_port, the virtual port onion_ssh_port and the introduction point count num_introduction_points of the SSH onion service, the hidden service directory hidden_service_dir with its mode hidden_service_dir_mode and owner tor_user, the install retry count install_retries, the readiness loop start_check_attempts and start_check_retry_delay_seconds, the saved onion address file address_file_path and its mode address_file_mode, the SSH port read from the ssh_daemon_setup directives, docs/spec/tor-service.md), ssh_daemon_setup (the package name package_name, the package status timeout and the install retry count package_status_timeout_seconds and install_retries, the service unit name service_unit_name, the systemd socket unit name socket_unit_name that the task disables so the configured port takes effect, and the readiness loop start_check_attempts and start_check_retry_delay_seconds, the checked main configuration path sshd_config_path and the owned drop-in path sshd_config_dropin_path with its file mode dropin_file_mode, the directives written through augeas directives, the repository key file names private_key_file_name and public_key_file_name with their deployment modes private_key_file_mode, public_key_file_mode, authorized_keys_file_mode and ssh_dir_mode, the root target directory root_ssh_dir, the target users users, docs/spec/ssh-daemon-setup.md), local_vault_setup (runtime secret vault source and target paths, file modes and error priority, docs/spec/secrets-model.md) and system_metrics_setup (the python version for the deployed venv, the syslog priority of a failed vault open, the deployment paths venv_dir, system_config_path and command_path, the spool path and modes, the unit file names, the journal identifiers, the channel queue directory names google_script_dir and main_sent_dir, the Google channel upload timeout google_script_timeout_seconds, the vault entry title google_script_key_entry_title, the deployment URL regex google_script_deployment_url_regex and the retry backoff of the send loop (base delay backoff_base_seconds, growth factor backoff_multiplier and ceiling backoff_max_seconds, docs/spec/system-metrics.md, section Schedule and retry) and the report collector system_metrics_setup.collector (the boot delay boot_delay_seconds, the daily send time daily_send_time, the readiness threshold threshold_percent, the collector retry backoff retry_base_seconds, retry_multiplier and retry_max_seconds, the per-command timeout command_timeout_seconds, the collector unit file names service_unit_name and timer_unit_name, the collector journal identifier journal_identifier, the lock path lock_file_path, the report file name report_file_name and the console module lists network_modules and system_modules, docs/spec/system-metrics.md, section Report collector). Module configuration is stored in config.toml: every task keeps its own section, so task parameters are configurable without code changes. The [[tasks]] section is the task catalog: one entry per task with name, description, dependencies and mode membership. The file is mandatory: a missing or invalid config stops the run, there are no defaults. Only the composition root reads the file; the values travel to tasks through Context.

Behavioral values must never be hardcoded inside task modules. Every value that affects behavior lives in config/ (the joined config document), including unit file names, journal identifiers, queue and spool directory names, file modes and paths, and external API contracts such as the Google web app deployment URL pattern. A hardcoded literal is allowed only in two cases: fixed machine contracts that are not configuration (system OS paths, repository layout paths, kernel sysfs interfaces) and values explicitly approved by the user, each approval recorded in this document. Everything else lives in config/. Duplicating the same value or the same logic across modules is forbidden: shared values and helpers live in one module and are imported, never copied.

Environment variables are the inst.sh interface for per-run selection and secrets:

PYNTARA_INSTALL_MODE - minimal, server or desktop. When unset, the mode is auto-detected (desktop when a desktop session or process is present, otherwise server). An unknown value shows the resilience notice and falls back to the auto-detected mode.
PYNTARA_TASKS - space-separated task names. When unset, the mode defaults are used. Unknown names are reported and ignored.
PYNTARA_FORCE_TASKS - space-separated task names that must rerun even when the target state is reached. Invalid names are reported and ignored. The keyword all (case-insensitive) forces every task of the resolved run set. Task names and the keyword are case-insensitive.
PYNTARA_SKIP_APT_UPDATE - 1, true or yes skips the apt index refresh that cli_tools and add_extra_repos run before package operations. Omit it in real runs so the index stays fresh; set it for test or offline runs.
PYNTARA_VAULT_PASSWORD, PYNTARA_VAULT_SOURCE - KeePass credentials resolved by inst.sh.

Approved fixed machine contracts (recorded user approvals):

1. REPO_ROOT of every task module (Path(__file__).resolve().parents[3]): the repository clone location. It is a repository layout path and is monkeypatched by the tests (docs/guides/developer-guide.md); the source vault paths of local_vault_setup are resolved against it.
2. The NextDNS service endpoint addresses used by nextdns_setup_system_wide: the IPv4 anycast servers 45.90.28.0 and 45.90.30.0, the IPv6 prefixes 2a07:a8c0 and 2a07:a8c1, the DoT endpoint pattern <id>.dns.nextdns.io and the verification endpoint https://test.nextdns.io/. They are an external API contract of the NextDNS service, not a behavior of the installer, so they live in pyntara.nextdns (docs/spec/networking.md) and are covered by the module tests.

## 4. Context contract

Context in context.py is a frozen dataclass. It is the only carrier for cross-cutting runtime dependencies:

install_mode
vault_password
vault_source
force_tasks (frozenset of task names)
task_data_root (Path)
skip_apt_update (bool; True skips the apt index refresh in cli_tools and add_extra_repos)
config (Config loaded from config.toml)

Context is passed explicitly to every task. Implicit reads of the environment inside task modules are forbidden.

## 5. Task contract

A task is a plain function:

task(ctx) -> TaskResult

TaskResult is a dataclass with fields:

success
changed
skipped (default False; True when the task module is missing and the task could not run)
message (optional)
error (optional)

No typing.Protocol, no ABC inheritance, no registry.

Task definitions live in the config/ directory under the [[tasks]] section (tasks.toml). Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file, which keeps default task sets ordered and rules out cycles. task_catalog.py holds only the logic that operates on the catalog: validate_mode, default_tasks, resolve.

Task-to-task data sharing is allowed only through Context fields or explicit arguments.

Full task model contract: docs/contracts/task-model.md.

## 6. Idempotency and side effects

Each task must be idempotent: repeated runs must not destroy an already configured system. A task checks the real system state (user exists, file present, service active) and skips changes when the goal is already reached. Force mode reruns a task even after completion.

Allowed explicit shared state channels:

encrypted vault files (secrets/*.vault)
task data files under task_data/<task-name>/
System Metrics file queue
named IPC command channels

Exchange boundaries between processes of different systemd services must be explicitly documented as an architecture contract.

The System Metrics service is the first long-running systemd service deployed from the pyntara code base. The system_metrics_setup task installs the package into the dedicated virtual environment at the configured system_metrics_setup.venv_dir with uv from the repository clone and refreshes the venv whenever its installed pyntara version differs from the repository version, so deployed services run the current code after every installer run; a refresh restarts the long-running service. The task renders the repository config/ directory into the configured system_metrics_setup.system_config_path, the single config of the target system: the deployed service reads it through pyntara.config.load_config, the same loader the installer uses, so both sides share one source of truth. The service unit system_metrics.service runs venv_dir/bin/python -m pyntara.metrics system_config_path. Every cycle dispatches the committed entries from main_outbox into the channel queues (google_script_dir for the Google Drive channel) through hard links and drains the Google Drive queue: each entry is uploaded with curl to the web app endpoint of the google_script_key vault entry, carrying the original name, the shared auth key and the Base64 content; an OK response moves the entry to main_sent_dir, any failure keeps it for the next cycle. The sender opens the runtime secret vault at /var/lib/pyntara/secrets/pyntara.vault, created by local_vault_setup, with the password from /etc/pyntara/pass only when the queue holds uploadable entries; a failed open is journaled through the shared pyntara.logger functions with the configured journal identifier at error_priority and the drain is skipped. The password value never appears in any message. When a cycle made at least one send attempt and none succeeded, the loop enters the retry mode: every cycle sends one randomly chosen uploadable entry and the pause grows geometrically from backoff_base_seconds by the backoff_multiplier factor per consecutive failed cycle, in whole seconds, capped at backoff_max_seconds; a cycle with a successful send or with no send attempt returns the loop to the normal mode and resets the counter. The backoff parameters live in config.toml (docs/spec/system-metrics.md, section Schedule and retry).

The queue commit path is split between a thin command and a root service, so any user can commit without privileges. The task generates the thin commit_system_metrics command file at the configured system_metrics_setup.command_path from a template with the spool path and the journal identifier embedded: the command needs no config access and no root. It publishes one regular non-empty file atomically into the spool directory system_metrics_setup.spool_dir (mode 1733, sticky, write and search for everyone, no listing) with mode 0600 and the commit time. The path unit system_metrics-ingest.path watches the spool with inotify and starts the oneshot service system_metrics-ingest.service on every file appearance; the service runs venv_dir/bin/python -m pyntara.metrics_ingest system_config_path and moves every spool file into the queue main_outbox with the strict queue modes and the configured suffix, then removes it from the spool. Rejected entries (not regular, empty, oversized) are removed from the spool and journaled; every action is journaled (docs/spec/system-metrics.md, section Queue architecture).

The report collector is a producer of the same queue. The timer system_metrics_collector.timer (deployed by the system_metrics_setup task) starts the oneshot service system_metrics_collector.service after boot and at the configured daily time; the service runs venv_dir/bin/python -m pyntara.metrics_collect system_config_path, collects the console modules of the system_metrics_setup.collector config (full output kept as is), waits with the geometric backoff until threshold_percent of the network modules answer or the retry window is exhausted, writes the report as the configured report_file_name into the system temp directory with mode 0600 and commits it through the commit_system_metrics command; the temporary file is always removed and a failed commit exits nonzero, so the systemd restart policy retries the collector. All waiting happens inside the service; the timer only schedules the start (OnBootSec from boot_delay_seconds and OnCalendar from daily_send_time). A non-blocking flock on the configured lock_file_path keeps a second instance from committing at the same time: the second instance exits quietly. The report is JSON: generated_at, ready_percent and the module results with status and output (docs/spec/system-metrics.md, section Report collector). The system temp directory of the report and the lock file parent directory /run/pyntara are approved fixed machine contracts of this exchange.

Forbidden patterns:

module-level mutable state for runtime business data
implicit environment reads inside task modules
hidden data exchange via ad-hoc globals

## 7. Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. An invalid environment value shows an error notice naming the problem and the applied fallback, waits a visible countdown (plain numbers, default 7 seconds, engine.notice_timeout in config/) so the user can interrupt with Ctrl-C, then continues with the fallback. Only a condition with no possible fallback stops the program. A missing or invalid config is such a condition: it stops the run because the engine cannot know what to provision.

## 8. Guardrails

The architecture is guarded by tests:

task catalog tests (mode defaults, dependency resolution, validation)
entry point tests (mode resolution, task set, force list, resilience rule)
task runner tests (missing modules skipped, failures, continue-on-error)

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.

## 9. Secrets management

Full secrets model specification: `docs/spec/secrets-model.md`.
Bootstrap-time vault resolution is specified in `docs/contracts/bootstrap.md` (section 12).
