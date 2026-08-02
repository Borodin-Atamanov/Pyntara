from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from .config_loader import load_runtime_configuration
from .context import create_run_context
from .logging_setup import configure_logging
from .mode_selector import select_install_mode
from .models import InstallModesConfig
from .secrets_store import VaultSecretsStore
from .task_registry import TaskRegistry
from .task_runner import TaskRunner
from .task_selector import select_force_mode, select_force_tasks, select_tasks

app = typer.Typer(help="Pyntara automation CLI.")


@app.command()
def run(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("config.yaml"),
    tasks_config: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("tasks.yaml"),
    install_modes: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "install_modes.yaml"
    ),
    mode: Annotated[
        str | None, typer.Option(help="Installation mode: minimal/server/desktop.")
    ] = None,
    task: Annotated[
        list[str] | None, typer.Option("--task", help="Explicit task names to execute.")
    ] = None,
    force: Annotated[
        bool, typer.Option(help="Force execution even if task is already completed.")
    ] = False,
    use_production_secrets: Annotated[
        bool,
        typer.Option(
            "--use-production-secrets",
            help="Load secrets/production.vault instead of secrets/default.vault.",
        ),
    ] = False,
    command_timeout_sec: Annotated[int | None, typer.Option()] = None,
    task_timeout_sec: Annotated[int | None, typer.Option()] = None,
    log_level: Annotated[str, typer.Option()] = "INFO",
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

    _stage("before_mode_selector")
    selected_mode = mode or select_install_mode(
        install_modes=loaded.install_modes, env=dict(os.environ)
    )
    _stage(f"after_mode_selector mode={selected_mode}")

    mode_tasks = task or _select_mode_tasks(
        mode=selected_mode, install_modes_path=loaded.install_modes
    )
    selected_tasks = mode_tasks
    force_task_names: set[str] = set()

    if task is None:
        _stage("before_task_selector")
        selected_tasks = select_tasks(
            task_catalog=loaded.task_catalog,
            mode_task_names=mode_tasks,
            pre_interaction_timeout_sec=loaded.app_config.ui.task_pre_interaction_timeout_sec,
        )
        _stage(f"after_task_selector selected={','.join(selected_tasks)}")

        _stage("before_force_mode")
        use_force_mode = select_force_mode(
            timeout_sec=loaded.install_modes.auto_select_timeout_sec,
        )
        _stage(f"after_force_mode use_force={int(use_force_mode)}")

        if use_force_mode:
            _stage("before_force_tasks")
            force_task_names = select_force_tasks(
                selected_task_names=selected_tasks,
                task_catalog=loaded.task_catalog,
                pre_interaction_timeout_sec=loaded.app_config.ui.task_pre_interaction_timeout_sec,
            )
            _stage(f"after_force_tasks selected={','.join(sorted(force_task_names))}")

    secrets_dir = loaded.app_config.paths.secrets_dir
    secrets_store = VaultSecretsStore(
        default_vault=secrets_dir / "default.vault",
        production_vault=secrets_dir / "production.vault",
        use_production=use_production_secrets,
    )
    _stage("before_secrets_load")
    secrets_started_at = time.monotonic()
    secrets_store.load()
    _stage(f"after_secrets_load elapsed={time.monotonic() - secrets_started_at:.3f}s")

    run_context = create_run_context(
        config=loaded.app_config,
        install_modes=loaded.install_modes,
        task_catalog=loaded.task_catalog,
        secrets_store=secrets_store,
        logger=logger,
    )

    registry = TaskRegistry(task_catalog=run_context.task_catalog)
    runner = TaskRunner(registry=registry)
    report = runner.run(
        ctx=run_context,
        task_names=selected_tasks,
        force=force,
        force_task_names=force_task_names,
    )

    for execution in report.executions:
        typer.echo(f"{execution.task_name}: {execution.status}")
    if not report.success:
        raise typer.Exit(code=1)


def _select_mode_tasks(*, mode: str, install_modes_path: InstallModesConfig) -> list[str]:
    if mode == "minimal":
        return list(install_modes_path.minimal)
    if mode == "server":
        return list(install_modes_path.server)
    if mode == "desktop":
        return list(install_modes_path.desktop)
    raise ValueError(f"Unknown mode '{mode}'.")


def _stage(message: str) -> None:
    print(f"CLI_STAGE {message}", file=sys.stderr, flush=True)


def main() -> None:
    # Backward compatibility: old bootstrap scripts invoke `pyntara run`.
    # The current CLI is a single-command app, so we normalize that legacy prefix.
    argv = list(sys.argv[1:])
    if argv[:1] == ["run"]:
        argv = argv[1:]
    app(args=argv)


if __name__ == "__main__":
    main()
