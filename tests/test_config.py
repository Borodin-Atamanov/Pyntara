"""Unit tests for config.toml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.config import ConfigError, load_config

VALID_TOML = """\
[engine]
task_data_root = "/var/lib/pyntara/task-data"
notice_timeout = 7

[cli_tools]
packages = ["mc", "htop"]
"""


def test_load_config_returns_typed_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    config = load_config(config_path)
    assert config.engine.task_data_root == Path("/var/lib/pyntara/task-data")
    assert config.engine.notice_timeout == 7
    assert config.cli_tools.packages == ("mc", "htop")


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_invalid_toml_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(config_path)


def test_load_config_missing_section_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine]\nnotice_timeout = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


@pytest.mark.parametrize(
    "content",
    [
        # notice_timeout is a string, not an integer
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = "7"\n[cli_tools]\npackages = ["mc"]\n',
        # packages is a string, not an array
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n[cli_tools]\npackages = "mc"\n',
        # packages contains a number, not strings
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n[cli_tools]\npackages = [1, 2]\n',
        # task_data_root is a number, not a string
        '[engine]\ntask_data_root = 42\nnotice_timeout = 7\n[cli_tools]\npackages = ["mc"]\n',
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    # TOML booleans parse as Python bool, which is a subclass of int and must
    # not be accepted as a countdown value.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = true\n[cli_tools]\npackages = ["mc"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)
