"""Pyntara command entry point and composition root.

This module is the only place that reads the environment and assembles
runtime state: it validates the install mode and the task selection, builds
the Context and launches the runner. Tasks never read the environment
themselves (docs/contracts/architecture.md).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.task_runner import run_tasks

app = typer.Typer(invoke_without_command=True)

# Runtime task data root per FHS: /var/lib/pyntara holds runtime state.
DEFAULT_TASK_DATA_ROOT = Path("/var/lib/pyntara/task-data")


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


def _warn_and_pause(message: str) -> None:
    """Show an error notice on stderr and pause so the user can interrupt.

    The notice stays visible for PYNTARA_NOTICE_TIMEOUT seconds (default 7,
    per the simplified architecture: an unknown task name is not fatal). The
    user interrupts with Ctrl-C to fix the environment and rerun, or lets the
    run continue.
    """

    timeout = int(_env("PYNTARA_NOTICE_TIMEOUT") or "7")
    typer.echo(f"ERROR: {message}", err=True)
    if timeout > 0:
        time.sleep(timeout)


def _resolve_task_names(mode: str) -> list[str]:
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
        _warn_and_pause(
            f"unknown task names in PYNTARA_TASKS: {', '.join(unknown)}; continuing without them"
        )
    return task_catalog.resolve(names)


def _resolve_force_tasks(names: list[str]) -> frozenset[str]:
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
        _warn_and_pause(
            "invalid task names in PYNTARA_FORCE_TASKS: "
            + ", ".join(invalid)
            + "; continuing without them"
        )
    return frozenset(
        name
        for name in force_names
        if task_catalog.by_name(name) is not None and name in names
    )


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""

    mode = _env("PYNTARA_INSTALL_MODE")
    if mode is None:
        typer.echo("ERROR: PYNTARA_INSTALL_MODE is not set", err=True)
        raise typer.Exit(1)
    try:
        task_catalog.validate_mode(mode)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    names = _resolve_task_names(mode)
    force_tasks = _resolve_force_tasks(names)
    raw_task_data_root = _env("PYNTARA_TASK_DATA_DIR")
    ctx = Context(
        install_mode=mode,
        vault_password=_env("PYNTARA_VAULT_PASSWORD"),
        vault_source=_env("PYNTARA_VAULT_SOURCE"),
        force_tasks=force_tasks,
        task_data_root=Path(raw_task_data_root)
        if raw_task_data_root is not None
        else DEFAULT_TASK_DATA_ROOT,
    )
    typer.echo(f"Install mode: {mode}")
    typer.echo(f"Tasks: {' '.join(names)}")
    if force_tasks:
        typer.echo(f"Force: {' '.join(sorted(force_tasks))}")
    results = run_tasks(ctx, names)
    failed = [name for name, result in results if not result.success]
    for name, result in results:
        if result.success:
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
    typer.echo(f"All {len(results)} tasks finished")


def main() -> None:
    """Entry point registered in pyproject.toml as the pyntara script."""
    sys.exit(app())


if __name__ == "__main__":
    main()
