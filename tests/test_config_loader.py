from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.config_loader import load_runtime_configuration


def test_cli_env_file_precedence(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "timeouts:",
                "  command_sec: 100",
            ]
        ),
        encoding="utf-8",
    )

    tasks_path = tmp_path / "tasks.yaml"
    tasks_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  - name: hostname",
                "    order: 10",
                "    description: host",
                "    module: pyntara.tasks.hostname:run",
                "    depends_on: []",
            ]
        ),
        encoding="utf-8",
    )

    install_modes_path = tmp_path / "install_modes.yaml"
    install_modes_path.write_text(
        "\n".join(
            [
                "minimal: [hostname]",
                "server: [hostname]",
                "desktop: [hostname]",
            ]
        ),
        encoding="utf-8",
    )

    env = {"PYNTARA_TIMEOUTS__COMMAND_SEC": "200"}
    loaded = load_runtime_configuration(
        config_path=config_path,
        tasks_path=tasks_path,
        install_modes_path=install_modes_path,
        cli_overrides={"timeouts": {"command_sec": 300}},
        env=env,
    )
    assert loaded.app_config.timeouts.command_sec == 300


def test_install_mode_must_reference_existing_tasks(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("target_platform: kubuntu-26.04\n", encoding="utf-8")

    tasks_path = tmp_path / "tasks.yaml"
    tasks_path.write_text(
        "\n".join(
            [
                "tasks:",
                "  - name: hostname",
                "    order: 10",
                "    description: host",
                "    module: pyntara.tasks.hostname:run",
                "    depends_on: []",
            ]
        ),
        encoding="utf-8",
    )

    install_modes_path = tmp_path / "install_modes.yaml"
    install_modes_path.write_text(
        "\n".join(
            [
                "minimal: [missing_task]",
                "server: [hostname]",
                "desktop: [hostname]",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown task 'missing_task'"):
        load_runtime_configuration(
            config_path=config_path,
            tasks_path=tasks_path,
            install_modes_path=install_modes_path,
            env={},
        )

