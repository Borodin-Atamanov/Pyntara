"""Pyntara command entry point and composition root.

The composition root is the only place allowed to assemble runtime state,
as specified by docs/contracts/architecture.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara.task_catalog import DEFAULT_CATALOG_PATH, TaskCatalog

app = typer.Typer(invoke_without_command=True)


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


@app.command(hidden=True)
def task_catalog(
    mode: Annotated[str, typer.Option(help="Install mode to select tasks for.")],
    timeout: Annotated[
        int, typer.Option(help="Seconds before dialog auto-accepts.")
    ] = 30,
    result_file: Annotated[
        str, typer.Option(help="File where dialog writes the checked tasks.")
    ] = "/tmp/pyntara-tasks",
    selected: Annotated[
        str | None, typer.Option(help="Space-separated selected tasks.")
    ] = None,
    catalog_path: Annotated[
        Path,
        typer.Option(help="Path to tasks.yaml."),
    ] = DEFAULT_CATALOG_PATH,
) -> None:
    """Print the task catalog for an install mode. Hidden helper for inst.sh.

    Without --selected prints two lines: the defaults for the mode and a fully
    quoted dialog --checklist command. With --selected prints the resolved
    task list including transitive dependencies.
    """

    catalog = TaskCatalog.from_yaml(catalog_path)
    if selected is None:
        typer.echo(catalog.defaults_line(mode))
        typer.echo(catalog.dialog_line(mode, timeout, result_file))
    else:
        typer.echo(catalog.tasks_line(selected.split()))


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""
    typer.echo("Pyntara provisioning engine is not implemented yet.")


def main() -> None:
    """Entry point registered in pyproject.toml as the pyntara script."""
    sys.exit(app())


if __name__ == "__main__":
    main()
