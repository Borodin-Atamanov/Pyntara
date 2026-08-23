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
    MODES,
    Config,
    ConfigError,
    EngineConfig,
    TaskConfig,
    load_config,
)
from pyntara.context import Context
from pyntara.logger import log_event, log_result_line
from pyntara.task_runner import run_tasks

app = typer.Typer(invoke_without_command=True)

# The engine configuration lives in the repository root. inst.sh launches
# pyntara from the clone root, so the config/ directory is always found
# there. The directory is mandatory: a missing or invalid config stops the
# run (architecture contract, Configuration).
CONFIG_PATH = Path("config")


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
    """Read a boolean environment variable; True for 1, true or yes.

    Any other value, including an unset or empty variable, is False. The
    explicit value list prevents a stray "0" from silently enabling a flag.
    """

    value = os.environ.get(name)
    if not value:
        return False
    return value.strip().lower() in ("1", "true", "yes")


def _load_config_or_exit() -> Config:
    """Load config.toml; a missing or invalid file stops the run.

    The config is the single source of truth for the Python part, so a
    broken file has no safe fallback: without it the engine cannot know
    what to provision. The failure is reported and the program exits.
    """

    try:
        return load_config(CONFIG_PATH)
    except ConfigError as exc:
        log_event(f"Error! {exc}", to_stderr=True)
        raise typer.Exit(1) from exc


def _warn_and_continue(message: str, notice_timeout: int) -> None:
    """Show an error notice with a visible countdown, then continue.

    General resilience rule: an invalid environment value must never stop the
    run. The notice names the problem and the applied fallback, waits a
    visible countdown (plain numbers, no unit letters) so the user can
    interrupt with Ctrl-C and fix the environment, then returns and the run
    continues.
    """

    log_event(f"Error! {message}", to_stderr=True)
    for remaining in range(notice_timeout, 0, -1):
        print(f"\r{remaining} ", end="", flush=True, file=sys.stderr)
        time.sleep(1)
    # The final carriage return ends the countdown line cleanly.
    print("\r", end="", flush=True, file=sys.stderr)


def _process_running(name: str, timeout: float) -> bool:
    """True when a process with the exact name is running (pgrep -x).

    The timeout comes from config.toml and bounds the pgrep query.
    """

    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return False
    try:
        result = subprocess.run(
            [pgrep, "-x", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def detect_default_mode(
    process_check_timeout: float, desktop_processes: tuple[str, ...]
) -> str:
    """Pick the default install mode without asking: desktop when a desktop
    session is present, otherwise server. Mirrors inst.sh detection.

    desktop_processes is the configured list of process names whose
    presence marks a desktop session; the check runs only when no session
    variable is set.
    """

    if os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION"):
        return "desktop"
    for process in desktop_processes:
        if _process_running(process, process_check_timeout):
            return "desktop"
    return "server"


def _resolve_mode(cfg: EngineConfig) -> str:
    """Resolve the install mode from PYNTARA_INSTALL_MODE or auto-detection.

    A missing variable is not an error: the mode is auto-detected and
    reported. A value not in the configuration shows the resilience notice
    and falls back to the auto-detected mode: the run continues whenever it
    can (general resilience rule).
    """

    mode = _env("PYNTARA_INSTALL_MODE")
    if mode is None:
        detected = detect_default_mode(
            cfg.process_check_timeout_seconds, cfg.desktop_detect_processes
        )
        log_event(f"Install mode not set, using detected default: {detected}")
        return detected
    if mode in MODES:
        return mode
    detected = detect_default_mode(
        cfg.process_check_timeout_seconds, cfg.desktop_detect_processes
    )
    _warn_and_continue(
        f"Install mode '{mode}' was set through environment variables but not "
        f"found in the configuration, applied mode '{detected}'. If this does "
        "not suit you, interrupt the program and redefine the mode through "
        "environment variables. Execution continues in",
        cfg.notice_timeout,
    )
    return detected


def _resolve_task_names(
    mode: str, notice_timeout: int, tasks: tuple[TaskConfig, ...]
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
    names: list[str], notice_timeout: int, tasks: tuple[TaskConfig, ...]
) -> frozenset[str]:
    """Force task list from PYNTARA_FORCE_TASKS, filtered to the run set.

    The keyword all (case-insensitive) forces every task in the run set.
    Every other entry must be a known task that is part of the run set,
    matched case-insensitively; the canonical catalog names are returned.
    Invalid entries are not fatal: an error notice is shown, the run pauses
    so the user can interrupt, then the run continues with the valid entries.
    """

    selection = _env("PYNTARA_FORCE_TASKS")
    if selection is None:
        return frozenset()
    force_names = selection.split()
    known = {task.name.casefold() for task in tasks}
    names_folded = {name.casefold() for name in names}
    invalid = [
        name
        for name in force_names
        if name.casefold() != "all"
        and (name.casefold() not in known or name.casefold() not in names_folded)
    ]
    if invalid:
        _warn_and_continue(
            "invalid task names in PYNTARA_FORCE_TASKS: "
            + ", ".join(invalid)
            + "; continuing without them",
            notice_timeout,
        )
    if any(name.casefold() == "all" for name in force_names):
        return frozenset(names)
    force_folded = {name.casefold() for name in force_names}
    return frozenset(name for name in names if name.casefold() in force_folded)


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""

    cfg = _load_config_or_exit()
    mode = _resolve_mode(cfg.engine)
    names = _resolve_task_names(mode, cfg.engine.notice_timeout, cfg.tasks)
    force_tasks = _resolve_force_tasks(names, cfg.engine.notice_timeout, cfg.tasks)
    ctx = Context(
        install_mode=mode,
        vault_password=_env("PYNTARA_VAULT_PASSWORD"),
        vault_source=_env("PYNTARA_VAULT_SOURCE"),
        force_tasks=force_tasks,
        task_data_root=cfg.engine.task_data_root,
        skip_apt_update=_env_flag("PYNTARA_SKIP_APT_UPDATE"),
        config=cfg,
    )
    log_event(f"Install mode: {mode}")
    log_event(f"Tasks: {' '.join(names)}")
    if force_tasks:
        log_event(f"Force: {' '.join(sorted(force_tasks))}")
    results = run_tasks(ctx, names)
    failed = [
        name
        for name, result in results
        if not result.success and not result.skipped
    ]
    skipped = [name for name, result in results if result.skipped]
    for name, result in results:
        log_result_line(name, result, to_journal=False)
    if failed:
        log_event(f"Failed {len(failed)} of {len(results)} tasks: {' '.join(failed)}")
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
