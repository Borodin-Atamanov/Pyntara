"""Pyntara command entry point and composition root.

This module is the only place that reads the environment and assembles
runtime state: it validates the install mode and the task selection, builds
the Context and launches the runner. Tasks never read the environment
themselves (docs/contracts/architecture.md).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara import task_catalog
from pyntara.config import (
    Config,
    load_config,
)
from pyntara.context import Context
from pyntara.logger import (
    configure_journal,
    log_event,
    log_result_line,
)
from pyntara.task_runner import run_tasks
from pyntara.utils import (
    export_session_environment,
    session_environment,
    substituted_command,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import tasks as tasks_values

app = typer.Typer(invoke_without_command=True)

# The engine configuration lives in the repository root. inst.sh launches
# pyntara from the clone root, so the config/ directory is always found
# there. The directory is mandatory: a missing or invalid config stops the
# run (architecture contract, Configuration).
CONFIG_PATH = Path("config")

# Root of the clone this code runs from: the package lives in src/pyntara/, so
# the root is two directories above this file. The composition root is the only
# place that computes it; it goes into the Context, and a task reads it from
# there, because the clone a task must read is the one the run started from.
# A wrong depth here breaks every task that renders a template, because their
# templates live under task_data/ of the root, and the whole suite still passes.
REPO_ROOT = Path(__file__).resolve().parents[2]


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Launch the provisioning engine when no subcommand is given.

    Bootstrap contract, Python environment: inst.sh runs `uv run pyntara` with no
    arguments, so a bare invocation must start the engine instead of failing
    with "Missing command".
    """

    if ctx.invoked_subcommand is None:
        run()


def vault_password_is_correct(vault_path: str, password: str) -> bool:
    """Return True when the given password decrypts the KeePass database.

    This is the only place in the installer that touches KeePass decryption:
    the shell must not decrypt vaults (bootstrap contract, Secrets files), so
    inst.sh delegates the verification to the check-vault command. Opening
    the database IS the verification: PyKeePass raises CredentialsError for
    a wrong password, so no separate password comparison is needed.
    """

    try:
        PyKeePass(vault_path, password=password)
    except CredentialsError:
        # A wrong password makes the database header checksum fail.
        return False
    return True


@app.command(hidden=True)
def check_vault(
    vault: Annotated[str, typer.Option(help="Path to the KeePass vault to verify.")],
) -> None:
    """Verify a vault password read from stdin. Hidden helper for inst.sh."""

    # The password arrives on stdin, never as an argument, so it cannot leak
    # into the process list or the install log (project rules forbid storing
    # secrets in logs).
    password = sys.stdin.read().rstrip("\n")
    if vault_password_is_correct(vault, password):
        raise typer.Exit(0)
    raise typer.Exit(1)


def _env(name: str) -> str | None:
    """Read one environment variable; None when unset or empty."""

    value = os.environ.get(name)
    if not value:
        return None
    return value


def _env_flag(name: str) -> bool:
    """Read a boolean environment variable; True for a configured answer.

    The accepted answers are the declared ENVIRONMENT_FLAG_TRUE_VALUES,
    compared without case and without surrounding spaces. Any other value,
    including an unset or empty variable, is False. The explicit value list
    prevents a stray "0" from silently enabling a flag.
    """

    value = os.environ.get(name)
    if not value:
        return False
    answer = value.strip().casefold()
    return answer in {
        word.casefold() for word in engine_values.ENVIRONMENT_FLAG_TRUE_VALUES
    }


def _load_config() -> Config:
    """Read config.toml and return it, whatever it holds.

    The read never fails: a value that is not in the document reaches the
    run as an absent value, the task that needed it reports what it could
    not do, and the run continues. A broken config never stops the run
    (architecture contract, Configuration).
    """

    return load_config(CONFIG_PATH)


def _export_desktop_session() -> None:
    """Hand the live desktop session environment to the whole run.

    One export puts the session variables of the desktop user of the machine
    into the environment of this process, so every task and every child
    process inherits them: a run started over a remote console then
    configures the desktop exactly like a run started inside the session.
    The step runs before the mode detection, which reads a session variable
    as direct evidence of a desktop session and falls back to the declared
    process names when there is none. Nothing is exported when the session
    manager of that user does not answer, or when the session reports no bus
    or display variable; the desktop tasks then write their values and
    report that they apply at the next login.
    """

    username = common_values.DESKTOP_USERNAME
    session = session_environment(
        username,
        command_template=engine_values.SESSION_ENVIRONMENT_COMMAND,
        keys=engine_values.SESSION_ENVIRONMENT_KEYS,
        bus_key=engine_values.SESSION_BUS_KEY,
        display_keys=engine_values.SESSION_DISPLAY_KEYS,
        timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS,
    )
    if not session:
        log_event(
            f"No live desktop session for {username}, "
            "desktop settings apply at the next login"
        )
        return
    exported = export_session_environment(session)
    log_event(
        f"Desktop session of {username} exported: "
        + " ".join(f"{name}={session[name]}" for name in exported)
    )


def _warn_and_continue(message: str, notice_timeout: int | None) -> None:
    """Show an error notice with a visible countdown, then continue.

    General resilience rule: an invalid environment value must never stop the
    run. The notice names the problem and the applied fallback, waits a
    visible countdown (plain numbers, no unit letters) so the user can
    interrupt with Ctrl-C and fix the environment, then returns and the run
    continues. An absent notice timeout means no countdown at all.
    """

    log_event(f"Error! {message}", to_stderr=True)
    for remaining in range(notice_timeout or 0, 0, -1):
        print(f"\r{remaining} ", end="", flush=True, file=sys.stderr)
        time.sleep(1)
    # The final carriage return ends the countdown line cleanly.
    print("\r", end="", flush=True, file=sys.stderr)


def _process_running(name: str, timeout: float) -> bool:
    """True when a process with the exact name is running.

    The query is the declared PROCESS_CHECK_COMMAND with its {process_name}
    replaced by the name, and the exit status alone answers: zero means
    running. A missing tool, a timeout and a failed query all mean "not
    running", because the check only picks a default install mode and must
    never stop the run.
    """

    command = substituted_command(
        engine_values.PROCESS_CHECK_COMMAND, {"process_name": name}
    )
    executable = shutil.which(command[0])
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def detect_default_mode() -> str:
    """Pick the default install mode without asking: desktop when a desktop
    session is present, otherwise server. Mirrors inst.sh detection.

    The desktop processes are the declared DESKTOP_DETECT_PROCESSES, the
    list of process names whose presence marks a desktop session, and the
    check runs only when no session variable is set.
    """

    if os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION"):
        return "desktop"
    for process in engine_values.DESKTOP_DETECT_PROCESSES:
        if _process_running(process, engine_values.PROCESS_CHECK_TIMEOUT_SECONDS):
            return "desktop"
    return "server"


def _resolve_mode() -> str:
    """Resolve the install mode from PYNTARA_INSTALL_MODE or auto-detection.

    A missing variable is not an error: the mode is auto-detected and
    reported. A value not in the configuration shows the resilience notice
    and falls back to the auto-detected mode: the run continues whenever it
    can (general resilience rule).
    """

    mode = _env("PYNTARA_INSTALL_MODE")
    if mode is None:
        detected = detect_default_mode()
        log_event(f"Install mode not set, using detected default: {detected}")
        return detected
    if mode in tasks_values.MODES:
        return mode
    detected = detect_default_mode()
    _warn_and_continue(
        f"Install mode '{mode}' was set through environment variables but not "
        f"found in the configuration, applied mode '{detected}'. If this does "
        "not suit you, interrupt the program and redefine the mode through "
        "environment variables. Execution continues in",
        engine_values.NOTICE_TIMEOUT,
    )
    return detected


def _resolve_task_names(
    mode: str, notice_timeout: int | None, tasks: tuple[tasks_values.TaskSpec, ...]
) -> list[str]:
    """Task set from PYNTARA_TASKS, or the resolved mode defaults.

    PYNTARA_TASKS is a space-separated list of task names; dependencies are
    resolved transitively. Unknown names are not fatal: an error notice is
    shown, the run pauses so the user can interrupt, then the run continues
    without the unknown names. The mode defaults are resolved the same way:
    a task that belongs to the mode pulls its catalog dependencies into the
    run set even when those dependencies belong to no mode themselves.
    """

    selection = _env("PYNTARA_TASKS")
    if selection is None:
        defaults = task_catalog.default_tasks(mode, tasks)
        return task_catalog.resolve(defaults, tasks)
    names = selection.split()
    unknown = task_catalog.unknown_tasks(names, tasks)
    if unknown:
        _warn_and_continue(
            f"unknown task names in PYNTARA_TASKS: {', '.join(unknown)}; continuing without them",
            notice_timeout,
        )
    return task_catalog.resolve(names, tasks)


def _resolve_force_tasks(
    names: list[str],
    notice_timeout: int | None,
    tasks: tuple[tasks_values.TaskSpec, ...],
) -> frozenset[str]:
    """Force task list from PYNTARA_FORCE_TASKS, filtered to the run set.

    The keyword FORCE_ALL_KEYWORD (case-insensitive) forces every task in the
    run set. Every other entry must be a known task that is part of the run
    set, matched case-insensitively; the canonical catalog names are
    returned. Invalid entries are not fatal: an error notice is shown, the run
    pauses so the user can interrupt, then the run continues with the valid
    entries.
    """

    selection = _env("PYNTARA_FORCE_TASKS")
    if selection is None:
        return frozenset()
    force_names = selection.split()
    known = {task.name.casefold() for task in tasks}
    names_folded = {name.casefold() for name in names}
    all_keyword = engine_values.FORCE_ALL_KEYWORD.casefold()
    invalid = [
        name
        for name in force_names
        if name.casefold() != all_keyword
        and (name.casefold() not in known or name.casefold() not in names_folded)
    ]
    if invalid:
        _warn_and_continue(
            "invalid task names in PYNTARA_FORCE_TASKS: "
            + ", ".join(invalid)
            + "; continuing without them",
            notice_timeout,
        )
    if any(name.casefold() == all_keyword for name in force_names):
        return frozenset(names)
    force_folded = {name.casefold() for name in force_names}
    return frozenset(name for name in names if name.casefold() in force_folded)


def _run_context(cfg: Config, mode: str, names: list[str]) -> Context:
    """The Context every task of a run receives.

    The clone root is the one computation of REPO_ROOT, which points at
    the repository the running code lives in; the tasks read the shipped
    templates under task_data/ through it, so a wrong depth would reach
    every task at once. tests/test_entry.py proves the root the Context
    carries is the clone root, which is the check the live run of
    2026-09-13 did not have when the depth was wrong.
    """

    force_tasks = _resolve_force_tasks(
        names, engine_values.NOTICE_TIMEOUT, tasks_values.CATALOG
    )
    return Context(
        install_mode=mode,
        vault_password=_env("PYNTARA_VAULT_PASSWORD"),
        vault_source=_env("PYNTARA_VAULT_SOURCE"),
        force_tasks=force_tasks,
        repo_root=REPO_ROOT,
        task_data_root=engine_values.TASK_DATA_ROOT,
        skip_apt_update=_env_flag("PYNTARA_SKIP_APT_UPDATE"),
        config=cfg,
    )


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""

    cfg = _load_config()
    configure_journal(engine_values.JOURNAL_IDENTIFIER)
    _export_desktop_session()
    if not tasks_values.CATALOG:
        # Without the catalog there is nothing to run, so the run reports the
        # state instead of finishing as if the machine were provisioned.
        log_event(
            "Error! the task catalog is empty, nothing to run",
            to_stderr=True,
        )
        raise typer.Exit(1)
    mode = _resolve_mode()
    names = _resolve_task_names(
        mode, engine_values.NOTICE_TIMEOUT, tasks_values.CATALOG
    )
    ctx = _run_context(cfg, mode, names)
    log_event(f"Install mode: {mode}")
    log_event(f"Tasks: {' '.join(names)}")
    if ctx.force_tasks:
        log_event(f"Force: {' '.join(sorted(ctx.force_tasks))}")
    results = run_tasks(ctx, names)
    failed = [
        name for name, result in results if not result.success and not result.skipped
    ]
    warned = [
        name
        for name, result in results
        if result.success and not result.skipped and result.warnings
    ]
    skipped = [name for name, result in results if result.skipped]
    for name, result in results:
        log_result_line(name, result, to_journal=False)
    if failed:
        log_event(f"Failed {len(failed)} of {len(results)} tasks: {' '.join(failed)}")
        raise typer.Exit(1)
    if warned:
        log_event(
            f"Finished {len(results) - len(skipped)} of {len(results)} tasks, "
            f"{len(warned)} with warnings: {' '.join(warned)}"
        )
        raise typer.Exit(1)
    if skipped:
        log_event(
            f"Finished {len(results) - len(skipped)} of {len(results)} tasks, "
            f"skipped {len(skipped)}"
        )
        return
    log_event(f"All {len(results)} tasks finished")


def main() -> None:
    """Entry point registered in pyproject.toml as the pyntara script."""
    sys.exit(app())


if __name__ == "__main__":
    main()
