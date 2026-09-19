# Installation modes and task selection

The mode names are declared once, in the task catalog (the MODES value of
src/pyntara/values/tasks.py), and every catalog record names the modes it belongs
to. The vocabulary is open: the names in use are minimal, server, desktop and
fast_desktop, and a mode added later is a new name in the catalog together with
the records that belong to it, never a change to this document.

A mode is selected by a value written by hand, so a written name is read without
case and with a hyphen and an underscore counting as one separator: fast-desktop,
Fast_Desktop and fast_desktop all name the same mode, and the run carries the
declared spelling.

fast_desktop is the quick set: a working system that is reachable from outside,
without the long heavy installs. It is selected like any other mode, through
PYNTARA_INSTALL_MODE, and it is never auto-detected, because a bare machine
carries no signal that asks for speed.

## Mode selection

The installer runs non-interactively. The mode is fixed by the PYNTARA_INSTALL_MODE environment variable.

When PYNTARA_INSTALL_MODE is omitted, the mode is auto-detected from the system:
on desktop systems, desktop mode is used  
on server systems, server mode is used

An unknown PYNTARA_INSTALL_MODE value shows the resilience notice, which names the declared modes, and falls back to the auto-detected mode ([Resilience rule](../simplified-architecture.md#resilience-rule)). The run reports that substitution as a warning of its own and fails its exit code, because the applied task set can be much larger than the one that was asked for.

## Task selection

The task set is fixed by the PYNTARA_TASKS environment variable: space-separated task names. Dependencies are resolved inside the engine, so the effective set is always complete and ordered.

When PYNTARA_TASKS is omitted, the default task set of the chosen mode is used: the tasks whose modes field lists that mode.

Each task has:
explicit ordering  
name  
human-readable description

Task set and metadata live in src/pyntara/values/tasks.py. Unknown task names show the resilience notice and are ignored.

## Force task selection

The force task list is fixed by the PYNTARA_FORCE_TASKS environment variable: space-separated task names that must rerun even when the target state is already reached. The keyword all forces every task of the resolved run set. Task names and the keyword are case-insensitive. Invalid names (unknown or not part of the run set) show the resilience notice and are ignored.
