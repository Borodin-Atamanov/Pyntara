# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## Runtime boundaries

inst.sh - bootloader  
pyntara.py (command entry and composition root)  
config/ (config.toml loading and validation, including the task catalog)  
task_catalog.py (mode defaults, task selection validation, dependency resolution)  
context.py (Context construction)  
task_runner.py (task discovery and execution)  
tasks/*.py (one task per module)

## Composition root

The run command in pyntara.py is the only place that reads the environment and assembles runtime state:

resolve the install mode (PYNTARA_INSTALL_MODE or the auto-detected default)  
resolve the task set (PYNTARA_TASKS or the mode defaults, dependencies resolved)  
resolve the force task list (PYNTARA_FORCE_TASKS)  
create Context  
launch the runner

No task may read the environment, create global singletons, or assemble runtime state itself.

## Configuration

There are no CLI options. The engine configuration comes from two sources.

The config/ directory at the repository root is the single source of truth for the values used by the Python part: one TOML file per top-level section (engine.toml, cli_tools.toml, tasks.toml, ...), joined by pyntara.config.loader.render_config_source into a single document before parsing. The loader accepts both the directory form and a single file, so the deployed system config stays one file. The section map lives in [Config section map](../guides/project-structure.md#config-section-map); per-task parameters are documented in each spec's Parameters section (docs/spec/). Module configuration is stored in config.toml: every task keeps its own section, so task parameters are configurable without code changes. The [[tasks]] section is the task catalog: one entry per task with name, description, dependencies and mode membership. The file is mandatory: a missing or invalid config stops the run, there are no defaults. Only the composition root reads the file; the values travel to tasks through Context.

Behavioral values must never be hardcoded inside task modules. Every value that affects behavior lives in config/ (the joined config document), including unit file names, journal identifiers, queue and spool directory names, file modes and paths, and external API contracts such as the Google web app deployment URL pattern. All variables and constants live in config/ except explicit exceptions approved by the user, each approval recorded in this document under Approved exceptions. A hardcoded literal is allowed only as such a recorded user-approved exception; every other literal is a violation. A constant found outside config/ without a recorded approval is a violation to fix on discovery: move it into config/ immediately, never leave it in place. Duplicating the same value or the same logic across modules is forbidden: shared values and helpers live in one module and are imported, never copied.

Environment variables are the inst.sh interface for per-run selection and secrets:

PYNTARA_INSTALL_MODE - minimal, server or desktop. When unset, the mode is auto-detected (desktop when a desktop session or process is present, otherwise server). An unknown value shows the resilience notice and falls back to the auto-detected mode.  
PYNTARA_TASKS - space-separated task names. When unset, the mode defaults are used. Unknown names are reported and ignored.  
PYNTARA_FORCE_TASKS - space-separated task names that must rerun even when the target state is reached. Invalid names are reported and ignored. The keyword all (case-insensitive) forces every task of the resolved run set. Task names and the keyword are case-insensitive.  
PYNTARA_SKIP_APT_UPDATE - 1, true or yes skips the apt index refresh that cli_tools and add_extra_repos run before package operations. Omit it in real runs so the index stays fresh; set it for test or offline runs.  
PYNTARA_VAULT_PASSWORD, PYNTARA_VAULT_SOURCE - KeePass credentials resolved by inst.sh.

Approved exceptions (recorded user approvals):

REPO_ROOT of every task module (Path(__file__).resolve().parents[3]): the repository clone location. It is a repository layout path (a fixed machine contract, not configuration: the clone must be locatable before the config is read) and is monkeypatched by the tests (docs/guides/developer-guide.md); the source vault paths of local_vault_setup are resolved against it.  
The NextDNS profile ID shape: exactly six lowercase hex digits, validated by pyntara.nextdns. It is a format invariant of the NextDNS service, not a behavior of the installer, so it stays in code; every other NextDNS value (the vault group title, the profile ID file path and mode) lives in the [nextdns_setup_system_wide] config table.

## Context contract

Context in context.py is a frozen dataclass. It is the only carrier for cross-cutting runtime dependencies:

install_mode  
vault_password  
vault_source  
force_tasks (frozenset of task names)  
task_data_root (Path)  
skip_apt_update (bool; True skips the apt index refresh in cli_tools and add_extra_repos)  
config (Config loaded from config.toml)

Context is passed explicitly to every task. Implicit reads of the environment inside task modules are forbidden.

## Task contract

A task is a plain function:

task(ctx) -> TaskResult

TaskResult is a dataclass with fields:

success  
changed  
skipped (default False; True when the task module is missing and the task could not run)  
message (optional)  
error (optional)  
warnings (optional tuple of strings; the steps of a completed task that could not be performed)

No typing.Protocol, no ABC inheritance, no registry.

A recoverable failure is never fatal: a task that could not perform a step reports it in warnings and still completes, and the runner converts a task that returns success=False or raises into a completed result carrying the reason in warnings. The run always continues with the remaining tasks, and the entry point counts the tasks with warnings and exits nonzero, so an incomplete configuration is visible to scripts without ever stopping the provisioning. Only a missing or invalid config, detected before any task runs, stops the run.

Task definitions live in the config/ directory under the [[tasks]] section (tasks.toml). Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file, which keeps default task sets ordered and rules out cycles. task_catalog.py holds only the logic that operates on the catalog: validate_mode, default_tasks, resolve.

Task-to-task data sharing is allowed only through Context fields or explicit arguments.

Full task model contract: [Task model and idempotency](task-model.md).

## Idempotency and side effects

Each task must be idempotent: repeated runs must not destroy an already configured system. A task checks the real system state (user exists, file present, service active) and is a plain done result when it tracked that the target state is reached, whether it changed anything or not; a run that finds the target state already reached is also done. A task whose steps could not all be performed returns a done result with warnings, not a plain done result, and the entry point exits nonzero. A normal run may update a version to the newest release and run a migration, because these bring the system to the intended state and do not destroy it; force mode reruns a task even after completion. A normal run must never regenerate a persistent identity (a hostname, a private key, an overlay network address); regeneration is reserved for force mode, which tears the identity down and builds it afresh (docs/contracts/task-model.md, section [Idempotency](task-model.md#idempotency)).

Allowed explicit shared state channels:

encrypted vault files (secrets/*.vault)  
task data files under task_data/<task-name>/  
System Metrics file queue  
named IPC command channels

Exchange boundaries between processes of different systemd services must be explicitly documented as an architecture contract.

The System Metrics service is the first long-running systemd service deployed from the pyntara code base. The system_metrics_setup task installs it into a dedicated venv (system_metrics_setup.venv_dir) and renders the repository config/ into system_metrics_setup.system_config_path, the single config of the target system, which the deployed service reads through pyntara.config.load_config, the same loader the installer uses, so both sides share one source of truth. The queue, the channel senders and the report collector are specified in [System Metrics](../spec/system-metrics.md). The sender opens the runtime secret vault /var/lib/pyntara/secrets/pyntara.vault, created by local_vault_setup, with the password from /etc/pyntara/pass; the password value never appears in any message.

The queue commit path is split between a thin command and a root service, so any user can commit without privileges: the task generates the thin commit_system_metrics command that publishes a regular non-empty file atomically into the spool, and a root ingest service moves spool files into the queue (see [Queue architecture](../spec/system-metrics.md#queue-architecture)).

The report collector is a producer of the same queue: a systemd timer starts a service that collects the configured console modules, waits for enough network modules to answer and commits the JSON report (see [Report collector](../spec/system-metrics.md#report-collector)). The system temp directory of the report and the lock file parent directory /run/pyntara are approved fixed machine contracts of this exchange.

Forbidden patterns:

module-level mutable state for runtime business data  
implicit environment reads inside task modules  
hidden data exchange via ad-hoc globals

## Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. An invalid environment value shows an error notice naming the problem and the applied fallback, waits a visible countdown (plain numbers, default 7 seconds, engine.notice_timeout in config/) so the user can interrupt with Ctrl-C, then continues with the fallback. Only a condition with no possible fallback stops the program. A missing or invalid config is such a condition: it stops the run because the engine cannot know what to provision.

## Guardrails

The architecture is guarded by tests:

task catalog tests (mode defaults, dependency resolution, validation)  
entry point tests (mode resolution, task set, force list, resilience rule, warnings exit code)  
task runner tests (missing modules skipped, failures converted to warnings, continue-on-error)

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.

## Secrets management

Full secrets model specification: [Secrets model](../spec/secrets-model.md).  
Bootstrap-time vault resolution is specified in [Secrets files](bootstrap.md#secrets-files).
