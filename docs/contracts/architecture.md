# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## 1. Runtime boundaries

inst.sh - bootloader
pyntara.py (command entry and composition root)
config.py (config.toml loading and validation, including the task catalog)
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

config.toml at the repository root is the single source of truth for the values used by the Python part: engine.task_data_root (task data root), engine.notice_timeout (seconds the resilience notice stays visible), engine.command_timeout_seconds (ceiling for provisioning commands), engine.process_check_timeout_seconds (bound for the desktop detection process query), engine.task_start_delay_seconds (pause between the task banner and the task start), engine.desktop_detect_processes (process names whose presence marks a desktop session in the default mode detection) and per-task sections such as cli_tools.packages, cli_tools.package_status_timeout_seconds, cli_tools.package_install_retries, cli_tools.package_success_threshold_percent, add_extra_repos.components and add_extra_repos.ubuntu_hosts (Ubuntu archive components and hosts managed by add_extra_repos), zram_service (compressor, swap priority, memory fraction, fallback CPU count, alignment), swapfile_service_install (swapfile path, size formula, file mode and size tolerance), local_vault_setup (runtime secret vault source and target paths, file modes and error priority, docs/spec/secrets-model.md) and system_metrics_setup (check interval of the long-running System Metrics service, the python version for the deployed venv, the syslog priorities of the vault check, the deployment paths venv_dir, system_config_path and command_path, the spool path and modes, the unit file names and the journal identifiers, docs/spec/system-metrics.md). Module configuration is stored in config.toml: every task keeps its own section, so task parameters are configurable without code changes. The [[tasks]] section is the task catalog: one entry per task with name, description, dependencies and mode membership. The file is mandatory: a missing or invalid config stops the run, there are no defaults. Only the composition root reads the file; the values travel to tasks through Context.

Behavioral values must never be hardcoded inside task modules. Every value that affects behavior lives in config.toml by default, including unit file names, journal identifiers, queue and spool directory names, file modes and paths. A module-level constant is allowed only for fixed machine contracts that are not configuration: system OS paths (for example the systemd unit directory), repository layout paths and kernel sysfs interfaces. Any other module constant requires explicit user approval and must be recorded in this document. Duplicating the same value or the same logic across modules is forbidden: shared values and helpers live in one module and are imported, never copied.

Environment variables are the inst.sh interface for per-run selection and secrets:

PYNTARA_INSTALL_MODE - minimal, server or desktop. When unset, the mode is auto-detected (desktop when a desktop session or process is present, otherwise server). An unknown value shows the resilience notice and falls back to the auto-detected mode.
PYNTARA_TASKS - space-separated task names. When unset, the mode defaults are used. Unknown names are reported and ignored.
PYNTARA_FORCE_TASKS - space-separated task names that must rerun even when the target state is reached. Invalid names are reported and ignored.
PYNTARA_SKIP_APT_UPDATE - 1, true or yes skips the apt index refresh that cli_tools and add_extra_repos run before package operations. Omit it in real runs so the index stays fresh; set it for test or offline runs.
PYNTARA_VAULT_PASSWORD, PYNTARA_VAULT_SOURCE - KeePass credentials resolved by inst.sh.

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

Task definitions live in config.toml under the [[tasks]] section. Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file, which keeps default task sets ordered and rules out cycles. task_catalog.py holds only the logic that operates on the catalog: validate_mode, default_tasks, resolve.

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

The System Metrics service is the first long-running systemd service deployed from the pyntara code base. The system_metrics_setup task installs the package into the dedicated virtual environment at the configured system_metrics_setup.venv_dir with uv from the repository clone and copies the repository config.toml to the configured system_metrics_setup.system_config_path, the single config of the target system: the deployed service reads it through pyntara.config.load_config, the same loader the installer uses, so both sides share one source of truth. The service unit system_metrics.service runs venv_dir/bin/python -m pyntara.metrics system_config_path and every check_interval_seconds verifies that the runtime secret vault at /var/lib/pyntara/secrets/pyntara.vault, created by local_vault_setup, opens with the password from /etc/pyntara/pass; the outcome is journaled through the shared pyntara.logger functions with the configured journal identifier at the configured syslog priorities (error_priority on failure, success_priority on success). The password value never appears in any message.

The queue commit path is split between a thin command and a root service, so any user can commit without privileges. The task generates the thin commit_system_metrics command file at the configured system_metrics_setup.command_path from a template with the spool path and the journal identifier embedded: the command needs no config access and no root. It publishes one regular non-empty file atomically into the spool directory system_metrics_setup.spool_dir (mode 1733, sticky, write and search for everyone, no listing) with mode 0600 and the commit time. The path unit system_metrics-ingest.path watches the spool with inotify and starts the oneshot service system_metrics-ingest.service on every file appearance; the service runs venv_dir/bin/python -m pyntara.metrics_ingest system_config_path and moves every spool file into the queue main_outbox with the strict queue modes and the configured suffix, then removes it from the spool. Rejected entries (not regular, empty, oversized) are removed from the spool and journaled; every action is journaled (docs/spec/system-metrics.md, section Queue architecture).

Forbidden patterns:

module-level mutable state for runtime business data
implicit environment reads inside task modules
hidden data exchange via ad-hoc globals

## 7. Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. An invalid environment value shows an error notice naming the problem and the applied fallback, waits a visible countdown (plain numbers, default 7 seconds, engine.notice_timeout in config.toml) so the user can interrupt with Ctrl-C, then continues with the fallback. Only a condition with no possible fallback stops the program. A missing or invalid config.toml is such a condition: it stops the run because the engine cannot know what to provision.

## 8. Guardrails

The architecture is guarded by tests:

task catalog tests (mode defaults, dependency resolution, validation)
entry point tests (mode resolution, task set, force list, resilience rule)
task runner tests (missing modules skipped, failures, continue-on-error)

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.

## 9. Secrets management

Full secrets model specification: `docs/spec/secrets-model.md`.
Bootstrap-time vault resolution is specified in `docs/contracts/bootstrap.md` (section 12).
