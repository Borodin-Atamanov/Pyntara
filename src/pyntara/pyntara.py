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
from pyntara.config import Config, ConfigError, EngineConfig, load_config
from pyntara.context import Context
from pyntara.task_runner import run_tasks

app = typer.Typer(invoke_without_command=True)

# The engine configuration lives in the repository root. inst.sh launches
# pyntara from the clone root, so the file is always found there. The file
# is mandatory: a missing or invalid config stops the run (architecture
# contract section 3).
CONFIG_PATH = Path("config.toml")


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Launch the provisioning engine when no subcommand is given.

    Bootstrap contract section 6: inst.sh runs `uv run pyntara` with no
    arguments, so a bare invocation must start the engine instead of failing
    with "Missing command".
    """

    if ctx.invoked_subcommand is None:
        run()


def vault_password_is_correct(vault_path: str, password: str) -> bool:
    """Return True when the given password decrypts the KeePass database.

    This is the only place in the installer that touches KeePass decryption:
    the shell must not decrypt vaults (bootstrap contract section 12), so
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


def _load_config_or_exit() -> Config:
    """Load config.toml; a missing or invalid file stops the run.

    The config is the single source of truth for the Python part, so a
    broken file has no safe fallback: without it the engine cannot know
    what to provision. The failure is reported and the program exits.
    """

    try:
        return load_config(CONFIG_PATH)
    except ConfigError as exc:
        typer.echo(f"Error! {exc}", err=True)
        raise typer.Exit(1) from exc


def _warn_and_continue(message: str, notice_timeout: int) -> None:
    """Show an error notice with a visible countdown, then continue.

    General resilience rule: an invalid environment value must never stop the
    run. The notice names the problem and the applied fallback, waits a
    visible countdown (plain numbers, no unit letters) so the user can
    interrupt with Ctrl-C and fix the environment, then returns and the run
    continues.
    """

    typer.echo(f"Error! {message}", err=True)
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


def detect_default_mode(process_check_timeout: float) -> str:
    """Pick the default install mode without asking: desktop when a desktop
    session is present, otherwise server. Mirrors inst.sh detection.
    """

    if os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION"):
        return "desktop"
    for process in ("kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell"):
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
        detected = detect_default_mode(cfg.process_check_timeout_seconds)
        typer.echo(f"Install mode not set, using detected default: {detected}")
        return detected
    if mode in task_catalog.MODES:
        return mode
    detected = detect_default_mode(cfg.process_check_timeout_seconds)
    _warn_and_continue(
        f"Install mode '{mode}' was set through environment variables but not "
        f"found in the configuration, applied mode '{detected}'. If this does "
        "not suit you, interrupt the program and redefine the mode through "
        "environment variables. Execution continues in",
        cfg.notice_timeout,
    )
    return detected


def _resolve_task_names(mode: str, notice_timeout: int) -> list[str]:
    """Task set from PYNTARA_TASKS, or the mode defaults.

    PYNTARA_TASKS is a space-separated list of task names; dependencies are
    resolved transitively. Unknown names are not fatal: an error notice is
    shown, the run pauses so the user can interrupt, then the run continues
    without the unknown names.
    """

    selection = _env("PYNTARA_TASKS")
    if selection is None:
        return task_catalog.default_tasks(mode)
    names = selection.split()
    unknown = task_catalog.unknown_tasks(names)
    if unknown:
        _warn_and_continue(
            f"unknown task names in PYNTARA_TASKS: {', '.join(unknown)}; continuing without them",
            notice_timeout,
        )
    return task_catalog.resolve(names)


def _resolve_force_tasks(names: list[str], notice_timeout: int) -> frozenset[str]:
    """Force task list from PYNTARA_FORCE_TASKS, filtered to the run set.

    Each forced task must be a known task that is part of the run set.
    Invalid entries are not fatal: an error notice is shown, the run pauses
    so the user can interrupt, then the run continues with the valid entries.
    """

    selection = _env("PYNTARA_FORCE_TASKS")
    if selection is None:
        return frozenset()
    force_names = selection.split()
    invalid = [
        name
        for name in force_names
        if task_catalog.by_name(name) is None or name not in names
    ]
    if invalid:
        _warn_and_continue(
            "invalid task names in PYNTARA_FORCE_TASKS: "
            + ", ".join(invalid)
            + "; continuing without them",
            notice_timeout,
        )
    return frozenset(
        name
        for name in force_names
        if task_catalog.by_name(name) is not None and name in names
    )


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""

    cfg = _load_config_or_exit()
    mode = _resolve_mode(cfg.engine)
    names = _resolve_task_names(mode, cfg.engine.notice_timeout)
    force_tasks = _resolve_force_tasks(names, cfg.engine.notice_timeout)
    ctx = Context(
        install_mode=mode,
        vault_password=_env("PYNTARA_VAULT_PASSWORD"),
        vault_source=_env("PYNTARA_VAULT_SOURCE"),
        force_tasks=force_tasks,
        task_data_root=cfg.engine.task_data_root,
        config=cfg,
    )
    typer.echo(f"Install mode: {mode}")
    typer.echo(f"Tasks: {' '.join(names)}")
    if force_tasks:
        typer.echo(f"Force: {' '.join(sorted(force_tasks))}")
    results = run_tasks(ctx, names)
    failed = [
        name
        for name, result in results
        if not result.success and not result.skipped
    ]
    skipped = [name for name, result in results if result.skipped]
    for name, result in results:
        if result.skipped:
            detail = result.message or "not implemented"
            typer.echo(f"[skip] {name}: {detail}")
        elif result.success:
            line = f"[done] {name}"
            if result.message:
                line = f"{line}: {result.message}"
            typer.echo(line)
        else:
            detail = result.error or "unknown error"
            typer.echo(f"[failed] {name}: {detail}")
    if failed:
        typer.echo(f"Failed {len(failed)} of {len(results)} tasks: {' '.join(failed)}")
        raise typer.Exit(1)
    if skipped:
        typer.echo(
            f"Finished {len(results) - len(skipped)} of {len(results)} tasks, "
            f"skipped {len(skipped)}"
        )
        return
    typer.echo(f"All {len(results)} tasks finished")


def main() -> None:
    """Entry point registered in pyproject.toml as the pyntara script."""
    sys.exit(app())


if __name__ == "__main__":
    main()
