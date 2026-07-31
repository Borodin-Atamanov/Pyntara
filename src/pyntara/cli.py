from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer

from .config_loader import load_runtime_configuration
from .context import create_run_context
from .logging_setup import configure_logging
from .models import InstallModesConfig
from .secrets_store import VaultSecretsStore
from .task_registry import TaskRegistry
from .task_runner import TaskRunner

app = typer.Typer(help="Pyntara automation CLI.")


@app.command()
def run(
    config: Path = typer.Option(Path("config.yaml"), exists=True, dir_okay=False),
    tasks_config: Path = typer.Option(Path("tasks.yaml"), exists=True, dir_okay=False),
    install_modes: Path = typer.Option(Path("install_modes.yaml"), exists=True, dir_okay=False),
    mode: str | None = typer.Option(None, help="Installation mode: minimal/server/desktop."),
    task: list[str] = typer.Option([], "--task", help="Explicit task names to execute."),
    force: bool = typer.Option(False, help="Force execution even if task is already completed."),
    use_production_secrets: bool = typer.Option(
        False,
        "--use-production-secrets",
        help="Load secrets/production.vault instead of secrets/default.vault.",
    ),
    command_timeout_sec: int | None = typer.Option(None),
    task_timeout_sec: int | None = typer.Option(None),
    log_level: str = typer.Option("INFO"),
) -> None:
    logger = configure_logging(level=log_level)

    cli_overrides: dict[str, Any] = {}
    if command_timeout_sec is not None:
        cli_overrides.setdefault("timeouts", {})["command_sec"] = command_timeout_sec
    if task_timeout_sec is not None:
        cli_overrides.setdefault("timeouts", {})["task_sec"] = task_timeout_sec

    loaded = load_runtime_configuration(
        config_path=config,
        tasks_path=tasks_config,
        install_modes_path=install_modes,
        cli_overrides=cli_overrides,
        env=dict(os.environ),
    )

    secrets_dir = loaded.app_config.paths.secrets_dir
    secrets_store = VaultSecretsStore(
        default_vault=secrets_dir / "default.vault",
        production_vault=secrets_dir / "production.vault",
        use_production=use_production_secrets,
    )
    secrets_store.load()

    run_context = create_run_context(
        config=loaded.app_config,
        install_modes=loaded.install_modes,
        task_catalog=loaded.task_catalog,
        secrets_store=secrets_store,
        logger=logger,
    )

    selected_tasks = task or _select_mode_tasks(mode=mode, install_modes_path=loaded.install_modes)
    registry = TaskRegistry(task_catalog=run_context.task_catalog)
    runner = TaskRunner(registry=registry)
    report = runner.run(ctx=run_context, task_names=selected_tasks, force=force)

    for execution in report.executions:
        typer.echo(f"{execution.task_name}: {execution.status}")
    if not report.success:
        raise typer.Exit(code=1)


def _select_mode_tasks(*, mode: str | None, install_modes_path: InstallModesConfig) -> list[str]:
    chosen_mode = mode or _detect_default_mode(install_modes_path)
    if chosen_mode == "minimal":
        return list(install_modes_path.minimal)
    if chosen_mode == "server":
        return list(install_modes_path.server)
    if chosen_mode == "desktop":
        return list(install_modes_path.desktop)
    raise ValueError(f"Unknown mode '{chosen_mode}'.")


def _detect_default_mode(install_modes_path: InstallModesConfig) -> str:
    is_desktop = "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ
    if is_desktop:
        return str(install_modes_path.default_desktop_mode)
    return str(install_modes_path.default_server_mode)


if __name__ == "__main__":
    app()
