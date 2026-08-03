"""Pyntara command entry point and composition root.

The composition root is the only place allowed to assemble runtime state,
as specified by docs/contracts/architecture.md.
"""

from __future__ import annotations

import sys

import typer

app = typer.Typer()


@app.command()
def run() -> None:
    """Run the Pyntara provisioning engine."""
    typer.echo("Pyntara provisioning engine is not implemented yet.")


def main() -> None:
    """Entry point registered in pyproject.toml as the pyntara script."""
    sys.exit(app())


if __name__ == "__main__":
    main()
