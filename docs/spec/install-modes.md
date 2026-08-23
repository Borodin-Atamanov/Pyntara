# Installation modes and task selection

The system offers 3 installation options:
minimal
server
desktop

## Mode selection

The installer runs non-interactively. The mode is fixed by the PYNTARA_INSTALL_MODE environment variable.

When PYNTARA_INSTALL_MODE is omitted, the mode is auto-detected from the system:
on desktop systems, desktop mode is used
on server systems, server mode is used

An unknown PYNTARA_INSTALL_MODE value shows the resilience notice and falls back to the auto-detected mode ([Resilience rule](../simplified-architecture.md#resilience-rule)).

## Task selection

The task set is fixed by the PYNTARA_TASKS environment variable: space-separated task names. Dependencies are resolved inside the engine, so the effective set is always complete and ordered.

When PYNTARA_TASKS is omitted, the default task set of the chosen mode is used: the tasks whose modes field lists that mode.

Each task has:
explicit ordering
name
human-readable description

Task set and metadata are defined in the config/ directory under the [[tasks]] section. Unknown task names show the resilience notice and are ignored.

## Force task selection

The force task list is fixed by the PYNTARA_FORCE_TASKS environment variable: space-separated task names that must rerun even when the target state is already reached. The keyword all forces every task of the resolved run set. Task names and the keyword are case-insensitive. Invalid names (unknown or not part of the run set) show the resilience notice and are ignored.
