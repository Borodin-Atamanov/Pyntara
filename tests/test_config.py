"""Unit tests for config.toml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.config import ConfigError, load_config

VALID_TOML = """\
[engine]
task_data_root = "/var/lib/pyntara/task-data"
notice_timeout = 7
command_timeout_seconds = 1800
process_check_timeout_seconds = 5
task_start_delay_seconds = 0.5

[cli_tools]
packages = ["mc", "htop"]
package_status_timeout_seconds = 30
package_install_retries = 3
package_success_threshold_percent = 70

[add_extra_repos]
components = ["universe", "restricted", "multiverse"]

[swapfile_service_install]
swapfile_path = "/swapfile"
ram_multiplier = 2
ram_extra_mb = 4096
disk_fraction = 0.5

[[tasks]]
name = "add_extra_repos"
description = "Enable extra Ubuntu archive components."
depends = []
modes = ["minimal", "server", "desktop"]

[[tasks]]
name = "users"
description = "Create and configure i, j, k users."
depends = []
modes = ["minimal", "server", "desktop"]

[[tasks]]
name = "passwords"
description = "Derive root and user passwords."
depends = ["users", "add_extra_repos"]
modes = ["minimal", "server", "desktop"]
"""


def test_load_config_returns_typed_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    config = load_config(config_path)
    assert config.engine.task_data_root == Path("/var/lib/pyntara/task-data")
    assert config.engine.notice_timeout == 7
    assert config.engine.command_timeout_seconds == 1800
    assert config.engine.process_check_timeout_seconds == 5
    assert config.engine.task_start_delay_seconds == 0.5
    assert config.cli_tools.packages == ("mc", "htop")
    assert config.cli_tools.package_status_timeout_seconds == 30
    assert config.cli_tools.package_install_retries == 3
    assert config.cli_tools.package_success_threshold_percent == 70
    assert config.add_extra_repos.components == ("universe", "restricted", "multiverse")
    assert config.swapfile_service_install.swapfile_path == Path("/swapfile")
    assert config.swapfile_service_install.ram_multiplier == 2
    assert config.swapfile_service_install.ram_extra_mb == 4096
    assert config.swapfile_service_install.disk_fraction == 0.5
    assert config.tasks[0].name == "add_extra_repos"
    assert config.tasks[0].description == "Enable extra Ubuntu archive components."
    assert config.tasks[0].depends == ()
    assert config.tasks[0].modes == ("minimal", "server", "desktop")
    assert config.tasks[2].name == "passwords"
    assert config.tasks[2].depends == ("users", "add_extra_repos")


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


# Valid base config; each wrong-type case replaces exactly one value.
_BASE_CONFIG = (
    '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
    "command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n"
    "task_start_delay_seconds = 0.5\n"
    '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
    "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
    '[add_extra_repos]\ncomponents = ["universe"]\n'
    '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
    "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n"
    '[[tasks]]\nname = "users"\ndescription = "Create users."\n'
    "depends = []\nmodes = [\"minimal\"]\n"
)


@pytest.mark.parametrize(
    "content",
    [
        # notice_timeout is a string, not an integer
        _BASE_CONFIG.replace('notice_timeout = 7', 'notice_timeout = "7"'),
        # packages is a string, not an array
        _BASE_CONFIG.replace('packages = ["mc"]', 'packages = "mc"'),
        # packages contains a number, not strings
        _BASE_CONFIG.replace('packages = ["mc"]', "packages = [1, 2]"),
        # task_data_root is a number, not a string
        _BASE_CONFIG.replace('task_data_root = "/tmp"', "task_data_root = 42"),
        # command_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "command_timeout_seconds = 1800", 'command_timeout_seconds = "1800"'
        ),
        # process_check_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "process_check_timeout_seconds = 5", 'process_check_timeout_seconds = "5"'
        ),
        # task_start_delay_seconds is a string, not a number
        _BASE_CONFIG.replace(
            "task_start_delay_seconds = 0.5", 'task_start_delay_seconds = "0.5"'
        ),
        # package_status_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "package_status_timeout_seconds = 30", 'package_status_timeout_seconds = "30"'
        ),
        # package_install_retries is a string, not an integer
        _BASE_CONFIG.replace(
            "package_install_retries = 3", 'package_install_retries = "3"'
        ),
        # package_success_threshold_percent is a string, not an integer
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            'package_success_threshold_percent = "70"',
        ),
        # components is a string, not an array
        _BASE_CONFIG.replace('components = ["universe"]', 'components = "universe"'),
        # components contains a number, not strings
        _BASE_CONFIG.replace('components = ["universe"]', "components = [1]"),
        # components contains an empty string
        _BASE_CONFIG.replace('components = ["universe"]', 'components = [""]'),
        # components contains whitespace
        _BASE_CONFIG.replace('components = ["universe"]', 'components = ["universe "]'),
        # components is an empty array
        _BASE_CONFIG.replace('components = ["universe"]', "components = []"),
        # swapfile_path is a number, not a string
        _BASE_CONFIG.replace('swapfile_path = "/swapfile"', "swapfile_path = 1"),
        # ram_multiplier is a string, not a number
        _BASE_CONFIG.replace("ram_multiplier = 2", 'ram_multiplier = "2"'),
        # ram_extra_mb is a string, not an integer
        _BASE_CONFIG.replace("ram_extra_mb = 4096", 'ram_extra_mb = "4096"'),
        # disk_fraction is above one
        _BASE_CONFIG.replace("disk_fraction = 0.5", "disk_fraction = 1.5"),
        # disk_fraction is zero
        _BASE_CONFIG.replace("disk_fraction = 0.5", "disk_fraction = 0"),
        # task name is a number, not a string
        _BASE_CONFIG.replace('name = "users"', "name = 1"),
        # task name is an empty string
        _BASE_CONFIG.replace('name = "users"', 'name = ""'),
        # task name contains a space, not an identifier
        _BASE_CONFIG.replace('name = "users"', 'name = "my task"'),
        # task description is a number, not a string
        _BASE_CONFIG.replace('description = "Create users."', "description = 1"),
        # task depends is a string, not an array
        _BASE_CONFIG.replace("depends = []", 'depends = "users"'),
        # task depends contains a number, not strings
        _BASE_CONFIG.replace("depends = []", "depends = [1]"),
        # task depends names a task that is not listed earlier
        _BASE_CONFIG.replace("depends = []", 'depends = ["later"]'),
        # task modes is a string, not an array
        _BASE_CONFIG.replace('modes = ["minimal"]', 'modes = "minimal"'),
        # task modes is an empty array
        _BASE_CONFIG.replace('modes = ["minimal"]', "modes = []"),
        # task modes contains an unknown install mode
        _BASE_CONFIG.replace('modes = ["minimal"]', 'modes = ["fancy"]'),
        # task modes contains a duplicate
        _BASE_CONFIG.replace(
            'modes = ["minimal"]', 'modes = ["minimal", "minimal"]'
        ),
        # task modes contains a number, not strings
        _BASE_CONFIG.replace('modes = ["minimal"]', "modes = [1]"),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_deduplicates_components(tmp_path: Path) -> None:
    # Duplicate components are removed while the configured order is kept.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _BASE_CONFIG.replace(
            'components = ["universe"]', 'components = ["universe", "multiverse", "universe"]'
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.add_extra_repos.components == ("universe", "multiverse")


@pytest.mark.parametrize(
    "content",
    [
        # threshold above 100 is invalid
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = 101",
        ),
        # threshold below 0 is invalid
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = -1",
        ),
    ],
)
def test_load_config_threshold_out_of_range_raises(
    tmp_path: Path, content: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="between 0 and 100"):
        load_config(config_path)


def test_load_config_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    # TOML booleans parse as Python bool, which is a subclass of int and must
    # not be accepted as a countdown value.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = true\n'
        'command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = 3\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_bool_not_accepted_as_retries(tmp_path: Path) -> None:
    # A bool value for package_install_retries must be rejected too.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        'command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = true\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_missing_tasks_section_raises(tmp_path: Path) -> None:
    # The catalog is mandatory: without it the engine cannot compute the
    # task set (architecture contract section 3).
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        "command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n"
        "task_start_delay_seconds = 0.5\n"
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
        "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="\\[tasks\\]"):
        load_config(config_path)


def test_load_config_empty_tasks_raises(tmp_path: Path) -> None:
    # An empty catalog is invalid: nothing would be provisionable. The
    # tasks key must sit before any table header, or TOML would attach it
    # to the preceding table.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "tasks = []\n"
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        "command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n"
        "task_start_delay_seconds = 0.5\n"
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
        "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="at least one task"):
        load_config(config_path)


def test_load_config_rejects_duplicate_task_names(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _BASE_CONFIG + '[[tasks]]\nname = "users"\ndescription = "Again."\n'
        "depends = []\nmodes = [\"minimal\"]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate task name"):
        load_config(config_path)
