"""Integration tests for config.toml loading.

The section-specific wrong-type tests live in test_config_engine.py,
test_config_memory.py, test_config_network.py, test_config_system_metrics.py,
test_config_vault.py and test_config_tasks.py; each uses the shared
base_config() from config_helpers.py. This module keeps the end-to-end
cases: a full valid document, typed value round-trip and the whole-file
failure modes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from config_checks import ConfigError
from config_helpers import base_config, load_checked_config, write_config


def test_load_config_returns_typed_values(tmp_path: Path) -> None:
    # The whole shared test document parses into typed values: a path becomes
    # a Path, an array becomes a tuple, numbers keep their type and a nested
    # table becomes its dataclass. The per-section tests assert the business
    # values of their section; this case proves that every section of one
    # full document survives the read with the shape its field declares.
    config = load_checked_config(write_config(tmp_path, base_config()))

    assert isinstance(config.engine.task_data_root, Path)
    assert isinstance(config.engine.notice_timeout, int)
    assert isinstance(config.engine.task_start_delay_seconds, float)
    assert isinstance(config.engine.desktop_detect_processes, tuple)
    assert isinstance(config.cli_tools.packages, tuple)
    assert isinstance(config.hostname.set_hostname_command, tuple)
    assert isinstance(config.ssh_daemon_setup.directives, tuple)
    assert isinstance(config.vault_structure.entries, tuple)
    assert isinstance(config.vault_structure.entries[0].title, str)
    assert isinstance(
        config.system_metrics_setup.collector.network_modules, tuple
    )
    assert isinstance(config.rustdesk_setup.options, tuple)
    assert isinstance(config.tasks, tuple)
    assert config.tasks[0].name == "users"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_checked_config(tmp_path / "missing.toml")


def test_load_config_invalid_toml_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_checked_config(config_path)


def test_load_config_missing_section_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine]\nnotice_timeout = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_checked_config(config_path)


def test_load_config_directory_joins_files(tmp_path: Path) -> None:
    # The repository config is a directory: the loader joins the *.toml
    # files in sorted order into one document and parses it. The base
    # document is split across two files to prove the join.
    text = base_config()
    split_at = text.index("[cli_tools]")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "engine.toml").write_text(
        text[:split_at], encoding="utf-8"
    )
    (config_dir / "rest.toml").write_text(
        text[split_at:], encoding="utf-8"
    )
    config = load_checked_config(config_dir)
    assert config.engine.notice_timeout == 7
    assert config.cli_tools.package_install_retries == 3
    assert config.tasks[0].name == "users"


def test_load_config_directory_duplicate_table_raises(tmp_path: Path) -> None:
    # The same table in two files is a duplicate table error: the join is
    # one document, so tomllib rejects it exactly like a duplicated table
    # in a single file.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "engine.toml").write_text(
        '[engine]\ntask_data_root = "/tmp"\n', encoding="utf-8"
    )
    (config_dir / "duplicate.toml").write_text(
        '[engine]\nnotice_timeout = 7\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="cannot read"):
        load_checked_config(config_dir)


def test_load_config_empty_directory_raises(tmp_path: Path) -> None:
    # An empty directory renders an empty document: no section exists, so
    # the first check reports the missing table.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with pytest.raises(ConfigError):
        load_checked_config(config_dir)

