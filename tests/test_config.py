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
desktop_detect_processes = ["kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell"]

[cli_tools]
packages = ["mc", "htop"]
package_status_timeout_seconds = 30
package_install_retries = 3
package_success_threshold_percent = 70

[add_extra_repos]
components = ["universe", "restricted", "multiverse"]
ubuntu_hosts = ["archive.ubuntu.com", "security.ubuntu.com"]

[swapfile_service_install]
swapfile_path = "/swapfile"
ram_multiplier = 2
ram_extra_mb = 4096
disk_fraction = 0.5
swapfile_mode = "0600"
size_tolerance_mb = 1
service_unit_name = "swapfile.service"

[zswap_service]
enabled = true
compressor = "zstd"
max_pool_percent = 50
accept_threshold_percent = 100
shrinker_enabled = true
service_unit_name = "zswap.service"

[zram_service]
compressor = "zstd"
swap_priority = 1111
memory_fraction_percent = 96
fallback_cpu_count = 8
alignment_bytes = 4096
reset_busy_attempts = 5
reset_busy_retry_delay_seconds = 0.5
service_unit_name = "zram.service"

[system_metrics_setup]
backoff_base_seconds = 2
backoff_multiplier = 2
backoff_max_seconds = 14400
python_version = "3"
error_priority = 3
venv_dir = "/usr/local/lib/pyntara/venv"
system_config_path = "/etc/pyntara/config.toml"
command_path = "/usr/local/bin/commit_system_metrics"
system_metrics_dir = "/var/lib/pyntara/metrics"
system_metrics_dir_mode = "0700"
queue_file_mode = "0600"
max_queue_file_size_bytes = 104857600
send_order = "oldest_first"
queue_file_suffix_length = 12
spool_dir = "/var/spool/system_metrics"
spool_dir_mode = "1733"
command_file_mode = "0755"
service_unit_name = "system_metrics.service"
ingest_service_unit_name = "system_metrics-ingest.service"
ingest_path_unit_name = "system_metrics-ingest.path"
service_journal_identifier = "system_metrics"
commit_journal_identifier = "commit_system_metrics"
main_outbox_dir = "main_outbox"
temp_dir = "temp"
spool_temp_prefix = ".commit-"
queue_link_attempts = 5
google_script_dir = "google_script"
main_sent_dir = "main_sent"
google_script_timeout_seconds = 60
google_script_key_entry_title = "google_script_key"
google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'

[system_metrics_setup.collector]
boot_delay_seconds = 30
daily_send_time = "12:00:00"
threshold_percent = 50
retry_base_seconds = 2
retry_multiplier = 2
retry_max_seconds = 600
command_timeout_seconds = 15
service_unit_name = "system_metrics_collector.service"
timer_unit_name = "system_metrics_collector.timer"
journal_identifier = "system_metrics_collector"
lock_file_path = "/run/pyntara/system_metrics_collector.lock"
report_file_name = "network.json"

[[system_metrics_setup.collector.network_modules]]
name = "ipv4"
command = ["ip", "-4", "addr", "show", "scope", "global"]

[[system_metrics_setup.collector.network_modules]]
name = "ipv6"
command = ["ip", "-6", "addr", "show", "scope", "global"]

[[system_metrics_setup.collector.system_modules]]
name = "hostname"
command = ["hostname"]

[vault_structure]

[[vault_structure.entries]]
title = "password_salt"
notes = "Primary salt for password derivation."

[[vault_structure.entries]]
title = "pyntara_local_vault_password"
notes = "Password for the runtime secret vault."

[[vault_structure.entries]]
title = "telegram_bot_token"
notes = "Telegram bot token for System Metrics."

[[vault_structure.entries]]
title = "google_script_key"
notes = "Google Drive web app credentials for System Metrics."

[local_vault_setup]
source_vault_production = "secrets/production.vault"
source_vault_default = "secrets/default.vault"
local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"
pass_file_path = "/etc/pyntara/pass"
vault_password_entry_title = "pyntara_local_vault_password"
secrets_dir_mode = "0700"
local_vault_file_mode = "0640"
pass_dir_mode = "0700"
pass_file_mode = "0400"
error_priority = 3

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
    assert config.engine.desktop_detect_processes == (
        "kwin_wayland",
        "kwin_x11",
        "plasmashell",
        "gnome-shell",
    )
    assert config.cli_tools.packages == ("mc", "htop")
    assert config.cli_tools.package_status_timeout_seconds == 30
    assert config.cli_tools.package_install_retries == 3
    assert config.cli_tools.package_success_threshold_percent == 70
    assert config.add_extra_repos.components == ("universe", "restricted", "multiverse")
    assert config.add_extra_repos.ubuntu_hosts == (
        "archive.ubuntu.com",
        "security.ubuntu.com",
    )
    assert config.swapfile_service_install.swapfile_path == Path("/swapfile")
    assert config.swapfile_service_install.ram_multiplier == 2
    assert config.swapfile_service_install.ram_extra_mb == 4096
    assert config.swapfile_service_install.disk_fraction == 0.5
    assert config.swapfile_service_install.swapfile_mode == 0o600
    assert config.swapfile_service_install.size_tolerance_mb == 1
    assert config.swapfile_service_install.service_unit_name == "swapfile.service"
    assert config.zswap_service.enabled is True
    assert config.zswap_service.compressor == "zstd"
    assert config.zswap_service.max_pool_percent == 50
    assert config.zswap_service.accept_threshold_percent == 100
    assert config.zswap_service.shrinker_enabled is True
    assert config.zswap_service.service_unit_name == "zswap.service"
    assert config.zram_service.compressor == "zstd"
    assert config.zram_service.swap_priority == 1111
    assert config.zram_service.memory_fraction_percent == 96
    assert config.zram_service.fallback_cpu_count == 8
    assert config.zram_service.alignment_bytes == 4096
    assert config.zram_service.service_unit_name == "zram.service"
    assert config.zram_service.reset_busy_attempts == 5
    assert config.zram_service.reset_busy_retry_delay_seconds == 0.5
    assert config.system_metrics_setup.backoff_base_seconds == 2
    assert config.system_metrics_setup.backoff_multiplier == 2
    assert config.system_metrics_setup.backoff_max_seconds == 14400
    assert config.system_metrics_setup.python_version == "3"
    assert config.system_metrics_setup.error_priority == 3
    assert config.system_metrics_setup.venv_dir == Path("/usr/local/lib/pyntara/venv")
    assert config.system_metrics_setup.system_config_path == Path("/etc/pyntara/config.toml")
    assert config.system_metrics_setup.command_path == Path("/usr/local/bin/commit_system_metrics")
    assert config.system_metrics_setup.system_metrics_dir == Path("/var/lib/pyntara/metrics")
    assert config.system_metrics_setup.system_metrics_dir_mode == 0o700
    assert config.system_metrics_setup.queue_file_mode == 0o600
    assert config.system_metrics_setup.max_queue_file_size_bytes == 104857600
    assert config.system_metrics_setup.send_order == "oldest_first"
    assert config.system_metrics_setup.queue_file_suffix_length == 12
    assert config.system_metrics_setup.spool_dir == Path("/var/spool/system_metrics")
    assert config.system_metrics_setup.spool_dir_mode == 0o1733
    assert config.system_metrics_setup.command_file_mode == 0o755
    assert config.system_metrics_setup.service_unit_name == "system_metrics.service"
    assert (
        config.system_metrics_setup.ingest_service_unit_name
        == "system_metrics-ingest.service"
    )
    assert (
        config.system_metrics_setup.ingest_path_unit_name
        == "system_metrics-ingest.path"
    )
    assert (
        config.system_metrics_setup.service_journal_identifier == "system_metrics"
    )
    assert (
        config.system_metrics_setup.commit_journal_identifier
        == "commit_system_metrics"
    )
    assert config.system_metrics_setup.main_outbox_dir == "main_outbox"
    assert config.system_metrics_setup.temp_dir == "temp"
    assert config.system_metrics_setup.spool_temp_prefix == ".commit-"
    assert config.system_metrics_setup.queue_link_attempts == 5
    assert config.system_metrics_setup.google_script_dir == "google_script"
    assert config.system_metrics_setup.main_sent_dir == "main_sent"
    assert config.system_metrics_setup.google_script_timeout_seconds == 60
    assert (
        config.system_metrics_setup.google_script_key_entry_title
        == "google_script_key"
    )
    assert (
        config.system_metrics_setup.google_script_deployment_url_regex
        == r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]+)/exec$"
    )
    assert config.system_metrics_setup.collector.boot_delay_seconds == 30
    assert config.system_metrics_setup.collector.daily_send_time == "12:00:00"
    assert config.system_metrics_setup.collector.threshold_percent == 50
    assert config.system_metrics_setup.collector.retry_base_seconds == 2
    assert config.system_metrics_setup.collector.retry_multiplier == 2
    assert config.system_metrics_setup.collector.retry_max_seconds == 600
    assert config.system_metrics_setup.collector.command_timeout_seconds == 15
    assert (
        config.system_metrics_setup.collector.service_unit_name
        == "system_metrics_collector.service"
    )
    assert (
        config.system_metrics_setup.collector.timer_unit_name
        == "system_metrics_collector.timer"
    )
    assert (
        config.system_metrics_setup.collector.journal_identifier
        == "system_metrics_collector"
    )
    assert config.system_metrics_setup.collector.lock_file_path == Path(
        "/run/pyntara/system_metrics_collector.lock"
    )
    assert config.system_metrics_setup.collector.report_file_name == "network.json"
    assert len(config.system_metrics_setup.collector.network_modules) == 2
    assert config.system_metrics_setup.collector.network_modules[0].name == "ipv4"
    assert config.system_metrics_setup.collector.network_modules[0].command == (
        "ip",
        "-4",
        "addr",
        "show",
        "scope",
        "global",
    )
    assert config.system_metrics_setup.collector.network_modules[1].name == "ipv6"
    assert config.system_metrics_setup.collector.system_modules[0].name == "hostname"
    assert config.system_metrics_setup.collector.system_modules[0].command == (
        "hostname",
    )
    assert config.local_vault_setup.source_vault_production == Path("secrets/production.vault")
    assert config.local_vault_setup.source_vault_default == Path("secrets/default.vault")
    assert config.local_vault_setup.local_vault_path == Path("/var/lib/pyntara/secrets/pyntara.vault")
    assert config.local_vault_setup.pass_file_path == Path("/etc/pyntara/pass")
    assert config.local_vault_setup.vault_password_entry_title == "pyntara_local_vault_password"
    assert config.local_vault_setup.secrets_dir_mode == 0o700
    assert config.local_vault_setup.local_vault_file_mode == 0o640
    assert config.local_vault_setup.pass_dir_mode == 0o700
    assert config.local_vault_setup.pass_file_mode == 0o400
    assert config.local_vault_setup.error_priority == 3
    assert config.vault_structure.entries[0].title == "password_salt"
    assert config.vault_structure.entries[0].notes == "Primary salt for password derivation."
    assert config.vault_structure.entries[1].title == "pyntara_local_vault_password"
    assert config.vault_structure.entries[2].title == "telegram_bot_token"
    assert config.vault_structure.entries[3].title == "google_script_key"
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
    'desktop_detect_processes = ["kwin_wayland", "plasmashell"]\n'
    '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
    "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
    '[add_extra_repos]\ncomponents = ["universe"]\n'
    'ubuntu_hosts = ["archive.ubuntu.com"]\n'
    '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
    "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n"
    'swapfile_mode = "0600"\nsize_tolerance_mb = 1\n'
    'service_unit_name = "swapfile.service"\n'
    '[zswap_service]\nenabled = true\ncompressor = "zstd"\n'
    "max_pool_percent = 50\naccept_threshold_percent = 100\n"
    'shrinker_enabled = true\nservice_unit_name = "zswap.service"\n'
    '[zram_service]\ncompressor = "zstd"\nswap_priority = 1111\n'
    "memory_fraction_percent = 96\nfallback_cpu_count = 8\n"
    'alignment_bytes = 4096\nreset_busy_attempts = 5\n'
    "reset_busy_retry_delay_seconds = 0.5\n"
    'service_unit_name = "zram.service"\n'
    "[system_metrics_setup]\n"
    "backoff_base_seconds = 2\nbackoff_multiplier = 2\n"
    "backoff_max_seconds = 14400\n"
    'python_version = "3"\nerror_priority = 3\n'
    'venv_dir = "/usr/local/lib/pyntara/venv"\n'
    'system_config_path = "/etc/pyntara/config.toml"\n'
    'command_path = "/usr/local/bin/commit_system_metrics"\n'
    'system_metrics_dir = "/var/lib/pyntara/metrics"\n'
    'system_metrics_dir_mode = "0700"\nqueue_file_mode = "0600"\n'
    'max_queue_file_size_bytes = 104857600\nsend_order = "oldest_first"\n'
    'queue_file_suffix_length = 12\n'
    'spool_dir = "/var/spool/system_metrics"\nspool_dir_mode = "1733"\n'
    'command_file_mode = "0755"\n'
    'service_unit_name = "system_metrics.service"\n'
    'ingest_service_unit_name = "system_metrics-ingest.service"\n'
    'ingest_path_unit_name = "system_metrics-ingest.path"\n'
    'service_journal_identifier = "system_metrics"\n'
    'commit_journal_identifier = "commit_system_metrics"\n'
    'main_outbox_dir = "main_outbox"\ntemp_dir = "temp"\n'
    'spool_temp_prefix = ".commit-"\nqueue_link_attempts = 5\n'
    'google_script_dir = "google_script"\nmain_sent_dir = "main_sent"\n'
    "google_script_timeout_seconds = 60\n"
    'google_script_key_entry_title = "google_script_key"\n'
    "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'\n"
    '[system_metrics_setup.collector]\n'
    "boot_delay_seconds = 30\n"
    'daily_send_time = "12:00:00"\n'
    "threshold_percent = 50\n"
    "retry_base_seconds = 2\n"
    "retry_multiplier = 2\n"
    "retry_max_seconds = 600\n"
    "command_timeout_seconds = 15\n"
    'service_unit_name = "system_metrics_collector.service"\n'
    'timer_unit_name = "system_metrics_collector.timer"\n'
    'journal_identifier = "system_metrics_collector"\n'
    'lock_file_path = "/run/pyntara/system_metrics_collector.lock"\n'
    'report_file_name = "network.json"\n'
    '[[system_metrics_setup.collector.network_modules]]\n'
    'name = "ipv4"\n'
    'command = ["ip", "-4", "addr", "show", "scope", "global"]\n'
    '[[system_metrics_setup.collector.network_modules]]\n'
    'name = "ipv6"\n'
    'command = ["ip", "-6", "addr", "show", "scope", "global"]\n'
    '[[system_metrics_setup.collector.system_modules]]\n'
    'name = "hostname"\n'
    'command = ["hostname"]\n'
    '[vault_structure]\n[[vault_structure.entries]]\ntitle = "password_salt"\n'
    'notes = "Primary salt."\n[[vault_structure.entries]]\n'
    'title = "pyntara_local_vault_password"\nnotes = "Local vault password."\n'
    '[[vault_structure.entries]]\ntitle = "google_script_key"\n'
    'notes = "Google script credentials."\n'
    '[local_vault_setup]\nsource_vault_production = "secrets/production.vault"\n'
    'source_vault_default = "secrets/default.vault"\n'
    'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"\n'
    'pass_file_path = "/etc/pyntara/pass"\n'
    'vault_password_entry_title = "pyntara_local_vault_password"\n'
    'secrets_dir_mode = "0700"\nlocal_vault_file_mode = "0640"\n'
    'pass_dir_mode = "0700"\npass_file_mode = "0400"\nerror_priority = 3\n'
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
        # desktop_detect_processes is a string, not an array
        _BASE_CONFIG.replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            'desktop_detect_processes = "kwin_wayland"',
        ),
        # desktop_detect_processes is an empty array
        _BASE_CONFIG.replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            "desktop_detect_processes = []",
        ),
        # desktop_detect_processes contains a number, not strings
        _BASE_CONFIG.replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            "desktop_detect_processes = [1]",
        ),
        # desktop_detect_processes contains an empty string
        _BASE_CONFIG.replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            'desktop_detect_processes = [""]',
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
        # swapfile_mode is not a four-digit octal string
        _BASE_CONFIG.replace('swapfile_mode = "0600"', 'swapfile_mode = "600"'),
        # swapfile_mode is not octal
        _BASE_CONFIG.replace('swapfile_mode = "0600"', 'swapfile_mode = "zzzz"'),
        # swapfile_mode is a number, not a string
        _BASE_CONFIG.replace('swapfile_mode = "0600"', "swapfile_mode = 600"),
        # size_tolerance_mb is a string, not an integer
        _BASE_CONFIG.replace("size_tolerance_mb = 1", 'size_tolerance_mb = "1"'),
        # size_tolerance_mb is negative
        _BASE_CONFIG.replace("size_tolerance_mb = 1", "size_tolerance_mb = -1"),
        # zswap enabled is a string, not a boolean
        _BASE_CONFIG.replace("enabled = true", 'enabled = "true"'),
        # zswap compressor is a number, not a string
        _BASE_CONFIG.replace('compressor = "zstd"', "compressor = 1"),
        # zswap compressor is an empty string
        _BASE_CONFIG.replace('compressor = "zstd"', 'compressor = ""'),
        # zswap max_pool_percent is a string, not an integer
        _BASE_CONFIG.replace("max_pool_percent = 50", 'max_pool_percent = "50"'),
        # zswap max_pool_percent is zero
        _BASE_CONFIG.replace("max_pool_percent = 50", "max_pool_percent = 0"),
        # zswap max_pool_percent is above 100
        _BASE_CONFIG.replace("max_pool_percent = 50", "max_pool_percent = 101"),
        # zswap accept_threshold_percent is below one
        _BASE_CONFIG.replace(
            "accept_threshold_percent = 100", "accept_threshold_percent = 0"
        ),
        # zswap accept_threshold_percent is above 100
        _BASE_CONFIG.replace(
            "accept_threshold_percent = 100", "accept_threshold_percent = 101"
        ),
        # zswap shrinker_enabled is an integer, not a boolean
        _BASE_CONFIG.replace("shrinker_enabled = true", "shrinker_enabled = 1"),
        # swapfile service_unit_name is a number, not a string
        _BASE_CONFIG.replace(
            'service_unit_name = "swapfile.service"', "service_unit_name = 1"
        ),
        # swapfile service_unit_name is an empty string
        _BASE_CONFIG.replace(
            'service_unit_name = "swapfile.service"', 'service_unit_name = ""'
        ),
        # zswap service_unit_name is a number, not a string
        _BASE_CONFIG.replace(
            'service_unit_name = "zswap.service"', "service_unit_name = 1"
        ),
        # zram service_unit_name is an empty string
        _BASE_CONFIG.replace(
            'service_unit_name = "zram.service"', 'service_unit_name = ""'
        ),
        # zram reset_busy_attempts is zero
        _BASE_CONFIG.replace(
            "reset_busy_attempts = 5", "reset_busy_attempts = 0"
        ),
        # zram reset_busy_retry_delay_seconds is a string, not a number
        _BASE_CONFIG.replace(
            "reset_busy_retry_delay_seconds = 0.5",
            'reset_busy_retry_delay_seconds = "0.5"',
        ),
        # zram reset_busy_retry_delay_seconds is zero
        _BASE_CONFIG.replace(
            "reset_busy_retry_delay_seconds = 0.5",
            "reset_busy_retry_delay_seconds = 0",
        ),
        # system_metrics_setup backoff_base_seconds is a string
        _BASE_CONFIG.replace(
            "backoff_base_seconds = 2", 'backoff_base_seconds = "2"'
        ),
        # system_metrics_setup backoff_base_seconds is zero
        _BASE_CONFIG.replace(
            "backoff_base_seconds = 2", "backoff_base_seconds = 0"
        ),
        # system_metrics_setup backoff_base_seconds is negative
        _BASE_CONFIG.replace(
            "backoff_base_seconds = 2", "backoff_base_seconds = -5"
        ),
        # system_metrics_setup backoff_multiplier is a string
        _BASE_CONFIG.replace(
            "backoff_multiplier = 2", 'backoff_multiplier = "2"'
        ),
        # system_metrics_setup backoff_multiplier is one: no growth
        _BASE_CONFIG.replace(
            "backoff_multiplier = 2", "backoff_multiplier = 1"
        ),
        # system_metrics_setup backoff_multiplier is zero
        _BASE_CONFIG.replace(
            "backoff_multiplier = 2", "backoff_multiplier = 0"
        ),
        # system_metrics_setup backoff_max_seconds is a string
        _BASE_CONFIG.replace(
            "backoff_max_seconds = 14400", 'backoff_max_seconds = "14400"'
        ),
        # system_metrics_setup backoff_max_seconds is below the base
        _BASE_CONFIG.replace(
            "backoff_max_seconds = 14400", "backoff_max_seconds = 1"
        ),
        # system_metrics_setup backoff_max_seconds is negative
        _BASE_CONFIG.replace(
            "backoff_max_seconds = 14400", "backoff_max_seconds = -5"
        ),
        # system_metrics_setup python_version is a number, not a string
        _BASE_CONFIG.replace('python_version = "3"', "python_version = 3"),
        # system_metrics_setup python_version is an empty string
        _BASE_CONFIG.replace('python_version = "3"', 'python_version = ""'),
        # system_metrics_setup error_priority is a string, not an integer
        _BASE_CONFIG.replace(
            'python_version = "3"\nerror_priority = 3\n',
            'python_version = "3"\nerror_priority = "3"\n',
        ),
        # system_metrics_setup error_priority is above 7
        _BASE_CONFIG.replace(
            'python_version = "3"\nerror_priority = 3\n',
            'python_version = "3"\nerror_priority = 8\n',
        ),
        # system_metrics_setup venv_dir is a number, not a string
        _BASE_CONFIG.replace(
            'venv_dir = "/usr/local/lib/pyntara/venv"', "venv_dir = 1"
        ),
        # system_metrics_setup venv_dir is an empty string
        _BASE_CONFIG.replace(
            'venv_dir = "/usr/local/lib/pyntara/venv"', 'venv_dir = ""'
        ),
        # system_metrics_setup system_config_path is a number, not a string
        _BASE_CONFIG.replace(
            'system_config_path = "/etc/pyntara/config.toml"',
            "system_config_path = 1",
        ),
        # system_metrics_setup system_config_path is an empty string
        _BASE_CONFIG.replace(
            'system_config_path = "/etc/pyntara/config.toml"',
            'system_config_path = ""',
        ),
        # system_metrics_setup command_path is a number, not a string
        _BASE_CONFIG.replace(
            'command_path = "/usr/local/bin/commit_system_metrics"',
            "command_path = 1",
        ),
        # system_metrics_setup command_path is an empty string
        _BASE_CONFIG.replace(
            'command_path = "/usr/local/bin/commit_system_metrics"',
            'command_path = ""',
        ),
        # system_metrics_setup system_metrics_dir is a number, not a string
        _BASE_CONFIG.replace(
            'system_metrics_dir = "/var/lib/pyntara/metrics"',
            "system_metrics_dir = 1",
        ),
        # system_metrics_setup system_metrics_dir is an empty string
        _BASE_CONFIG.replace(
            'system_metrics_dir = "/var/lib/pyntara/metrics"',
            'system_metrics_dir = ""',
        ),
        # system_metrics_setup system_metrics_dir_mode is not a four-digit octal string
        _BASE_CONFIG.replace(
            'system_metrics_dir_mode = "0700"', 'system_metrics_dir_mode = "700"'
        ),
        # system_metrics_setup queue_file_mode is not a four-digit octal string
        _BASE_CONFIG.replace(
            'queue_file_mode = "0600"', 'queue_file_mode = "060"'
        ),
        # system_metrics_setup max_queue_file_size_bytes is a string, not an integer
        _BASE_CONFIG.replace(
            "max_queue_file_size_bytes = 104857600",
            'max_queue_file_size_bytes = "104857600"',
        ),
        # system_metrics_setup max_queue_file_size_bytes is zero
        _BASE_CONFIG.replace(
            "max_queue_file_size_bytes = 104857600",
            "max_queue_file_size_bytes = 0",
        ),
        # system_metrics_setup max_queue_file_size_bytes is negative
        _BASE_CONFIG.replace(
            "max_queue_file_size_bytes = 104857600",
            "max_queue_file_size_bytes = -1",
        ),
        # system_metrics_setup send_order is not a known order
        _BASE_CONFIG.replace(
            'send_order = "oldest_first"', 'send_order = "middle_first"'
        ),
        # system_metrics_setup queue_file_suffix_length is a string, not an integer
        _BASE_CONFIG.replace(
            "queue_file_suffix_length = 12", 'queue_file_suffix_length = "12"'
        ),
        # system_metrics_setup queue_file_suffix_length is zero
        _BASE_CONFIG.replace(
            "queue_file_suffix_length = 12", "queue_file_suffix_length = 0"
        ),
        # system_metrics_setup spool_dir is a number, not a string
        _BASE_CONFIG.replace(
            'spool_dir = "/var/spool/system_metrics"', "spool_dir = 1"
        ),
        # system_metrics_setup spool_dir is an empty string
        _BASE_CONFIG.replace(
            'spool_dir = "/var/spool/system_metrics"', 'spool_dir = ""'
        ),
        # system_metrics_setup spool_dir_mode is not a four-digit octal string
        _BASE_CONFIG.replace('spool_dir_mode = "1733"', 'spool_dir_mode = "173"'),
        # system_metrics_setup command_file_mode is not a four-digit octal string
        _BASE_CONFIG.replace('command_file_mode = "0755"', 'command_file_mode = "755"'),
        # system_metrics_setup service_unit_name is an empty string
        _BASE_CONFIG.replace(
            'service_unit_name = "system_metrics.service"', 'service_unit_name = ""'
        ),
        # system_metrics_setup ingest_service_unit_name is a number, not a string
        _BASE_CONFIG.replace(
            'ingest_service_unit_name = "system_metrics-ingest.service"',
            "ingest_service_unit_name = 1",
        ),
        # system_metrics_setup ingest_path_unit_name is an empty string
        _BASE_CONFIG.replace(
            'ingest_path_unit_name = "system_metrics-ingest.path"',
            'ingest_path_unit_name = ""',
        ),
        # system_metrics_setup service_journal_identifier is a number, not a string
        _BASE_CONFIG.replace(
            'service_journal_identifier = "system_metrics"',
            "service_journal_identifier = 1",
        ),
        # system_metrics_setup commit_journal_identifier is an empty string
        _BASE_CONFIG.replace(
            'commit_journal_identifier = "commit_system_metrics"',
            'commit_journal_identifier = ""',
        ),
        # system_metrics_setup main_outbox_dir is a number, not a string
        _BASE_CONFIG.replace(
            'main_outbox_dir = "main_outbox"', "main_outbox_dir = 1"
        ),
        # system_metrics_setup temp_dir is an empty string
        _BASE_CONFIG.replace('temp_dir = "temp"', 'temp_dir = ""'),
        # system_metrics_setup spool_temp_prefix is a number, not a string
        _BASE_CONFIG.replace(
            'spool_temp_prefix = ".commit-"', "spool_temp_prefix = 1"
        ),
        # system_metrics_setup queue_link_attempts is a string, not an integer
        _BASE_CONFIG.replace(
            "queue_link_attempts = 5", 'queue_link_attempts = "5"'
        ),
        # system_metrics_setup queue_link_attempts is zero
        _BASE_CONFIG.replace(
            "queue_link_attempts = 5", "queue_link_attempts = 0"
        ),
        # system_metrics_setup google_script_dir is a number, not a string
        _BASE_CONFIG.replace(
            'google_script_dir = "google_script"', "google_script_dir = 1"
        ),
        # system_metrics_setup google_script_dir is an empty string
        _BASE_CONFIG.replace(
            'google_script_dir = "google_script"', 'google_script_dir = ""'
        ),
        # system_metrics_setup main_sent_dir is a number, not a string
        _BASE_CONFIG.replace(
            'main_sent_dir = "main_sent"', "main_sent_dir = 1"
        ),
        # system_metrics_setup main_sent_dir is an empty string
        _BASE_CONFIG.replace(
            'main_sent_dir = "main_sent"', 'main_sent_dir = ""'
        ),
        # system_metrics_setup google_script_timeout_seconds is a string
        _BASE_CONFIG.replace(
            "google_script_timeout_seconds = 60",
            'google_script_timeout_seconds = "60"',
        ),
        # system_metrics_setup google_script_timeout_seconds is zero
        _BASE_CONFIG.replace(
            "google_script_timeout_seconds = 60",
            "google_script_timeout_seconds = 0",
        ),
        # system_metrics_setup google_script_key_entry_title is a number
        _BASE_CONFIG.replace(
            'google_script_key_entry_title = "google_script_key"',
            "google_script_key_entry_title = 1",
        ),
        # system_metrics_setup google_script_key_entry_title is an empty string
        _BASE_CONFIG.replace(
            'google_script_key_entry_title = "google_script_key"',
            'google_script_key_entry_title = ""',
        ),
        # system_metrics_setup google_script_deployment_url_regex is a number
        _BASE_CONFIG.replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = 1",
        ),
        # system_metrics_setup google_script_deployment_url_regex is empty
        _BASE_CONFIG.replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = ''",
        ),
        # system_metrics_setup google_script_deployment_url_regex is invalid
        _BASE_CONFIG.replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = 'a['",
        ),
        # system_metrics_setup google_script_deployment_url_regex has no group
        _BASE_CONFIG.replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/[A-Za-z0-9_-]+/exec$'",
        ),
        # local_vault_setup source_vault_production is a number, not a string
        _BASE_CONFIG.replace(
            'source_vault_production = "secrets/production.vault"',
            "source_vault_production = 1",
        ),
        # local_vault_setup source_vault_default is an empty string
        _BASE_CONFIG.replace(
            'source_vault_default = "secrets/default.vault"', 'source_vault_default = ""'
        ),
        # local_vault_setup local_vault_path is a number, not a string
        _BASE_CONFIG.replace(
            'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"',
            "local_vault_path = 1",
        ),
        # local_vault_setup pass_file_path is an empty string
        _BASE_CONFIG.replace('pass_file_path = "/etc/pyntara/pass"', 'pass_file_path = ""'),
        # local_vault_setup vault_password_entry_title is a number, not a string
        _BASE_CONFIG.replace(
            'vault_password_entry_title = "pyntara_local_vault_password"',
            "vault_password_entry_title = 1",
        ),
        # vault_structure entries is a string, not an array
        _BASE_CONFIG.replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            '[vault_structure]\nentries = "salt"',
        ),
        # vault_structure entries is an empty array
        _BASE_CONFIG.replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\nentries = []",
        ),
        # vault_structure entries are missing entirely: the section is gone
        _BASE_CONFIG.replace(
            "[vault_structure]\n[[vault_structure.entries]]\n"
            'title = "password_salt"\nnotes = "Primary salt."\n'
            "[[vault_structure.entries]]\n"
            'title = "pyntara_local_vault_password"\n'
            'notes = "Local vault password."\n',
            "",
        ),
        # vault_structure entry title is a number, not a string
        _BASE_CONFIG.replace('title = "password_salt"', "title = 1"),
        # vault_structure entry title is an empty string
        _BASE_CONFIG.replace('title = "password_salt"', 'title = ""'),
        # vault_structure entry notes is missing
        _BASE_CONFIG.replace('notes = "Primary salt."', ""),
        # vault_structure entry notes is an empty string
        _BASE_CONFIG.replace('notes = "Primary salt."', 'notes = ""'),
        # vault_structure entry titles are duplicated
        _BASE_CONFIG.replace(
            'title = "pyntara_local_vault_password"', 'title = "password_salt"'
        ),
        # vault_structure entry names an unknown field; url lives in the vault
        _BASE_CONFIG.replace(
            'notes = "Primary salt."',
            'notes = "Primary salt."\nurl = "https://example.com/exec"',
        ),
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
        # collector section is missing entirely
        _BASE_CONFIG.replace(
            "[system_metrics_setup.collector]\n", ""
        ),
        # collector boot_delay_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "boot_delay_seconds = 30", 'boot_delay_seconds = "30"'
        ),
        # collector boot_delay_seconds is negative
        _BASE_CONFIG.replace(
            "boot_delay_seconds = 30", "boot_delay_seconds = -1"
        ),
        # collector daily_send_time is a number, not a string
        _BASE_CONFIG.replace(
            'daily_send_time = "12:00:00"', "daily_send_time = 1200"
        ),
        # collector daily_send_time is not a time of day
        _BASE_CONFIG.replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "25:00:00"'
        ),
        # collector daily_send_time misses the minutes
        _BASE_CONFIG.replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "12"'
        ),
        # collector daily_send_time has four parts
        _BASE_CONFIG.replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "12:00:00:00"'
        ),
        # collector threshold_percent is a string, not an integer
        _BASE_CONFIG.replace(
            "threshold_percent = 50", 'threshold_percent = "50"'
        ),
        # collector threshold_percent is negative
        _BASE_CONFIG.replace(
            "threshold_percent = 50", "threshold_percent = -1"
        ),
        # collector threshold_percent is above 100
        _BASE_CONFIG.replace(
            "threshold_percent = 50", "threshold_percent = 101"
        ),
        # collector retry_base_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "retry_base_seconds = 2", 'retry_base_seconds = "2"'
        ),
        # collector retry_base_seconds is zero
        _BASE_CONFIG.replace("retry_base_seconds = 2", "retry_base_seconds = 0"),
        # collector retry_multiplier is one: no growth
        _BASE_CONFIG.replace(
            "retry_multiplier = 2", "retry_multiplier = 1"
        ),
        # collector retry_max_seconds is below the base
        _BASE_CONFIG.replace(
            "retry_max_seconds = 600", "retry_max_seconds = 1"
        ),
        # collector command_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "command_timeout_seconds = 15", 'command_timeout_seconds = "15"'
        ),
        # collector command_timeout_seconds is zero
        _BASE_CONFIG.replace(
            "command_timeout_seconds = 15", "command_timeout_seconds = 0"
        ),
        # collector service_unit_name is an empty string
        _BASE_CONFIG.replace(
            'service_unit_name = "system_metrics_collector.service"',
            'service_unit_name = ""',
        ),
        # collector timer_unit_name is a number, not a string
        _BASE_CONFIG.replace(
            'timer_unit_name = "system_metrics_collector.timer"',
            "timer_unit_name = 1",
        ),
        # collector journal_identifier is an empty string
        _BASE_CONFIG.replace(
            'journal_identifier = "system_metrics_collector"',
            'journal_identifier = ""',
        ),
        # collector lock_file_path is a number, not a string
        _BASE_CONFIG.replace(
            'lock_file_path = "/run/pyntara/system_metrics_collector.lock"',
            "lock_file_path = 1",
        ),
        # collector report_file_name is an empty string
        _BASE_CONFIG.replace(
            'report_file_name = "network.json"', 'report_file_name = ""'
        ),
        # collector network_modules is a string, not an array
        _BASE_CONFIG.replace(
            "[[system_metrics_setup.collector.network_modules]]",
            'network_modules = "ip"',
        ),
        # collector network_modules module is not a table
        _BASE_CONFIG.replace(
            "[[system_metrics_setup.collector.network_modules]]",
            "network_modules = [1]",
        ),
        # collector network_modules module name is an empty string
        _BASE_CONFIG.replace(
            'name = "ipv4"', 'name = ""'
        ),
        # collector network_modules module names are duplicated
        _BASE_CONFIG.replace(
            'name = "ipv6"', 'name = "ipv4"'
        ),
        # collector network_modules module command is an empty array
        _BASE_CONFIG.replace(
            'command = ["ip", "-4", "addr", "show", "scope", "global"]',
            "command = []",
        ),
        # collector network_modules module command contains an empty string
        _BASE_CONFIG.replace(
            'command = ["ip", "-4", "addr", "show", "scope", "global"]',
            'command = ["ip", ""]',
        ),
        # collector system_modules module command is missing
        _BASE_CONFIG.replace(
            'command = ["hostname"]', ""
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


def test_load_config_entry_title_must_exist_in_vault_structure(tmp_path: Path) -> None:
    # The local vault password entry must be part of the vault structure: a
    # typo in the title is caught at config load, not on the target machine.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _BASE_CONFIG.replace(
            'vault_password_entry_title = "pyntara_local_vault_password"',
            'vault_password_entry_title = "no_such_entry"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must name an entry"):
        load_config(config_path)


def test_load_config_google_script_entry_title_must_exist_in_vault_structure(
    tmp_path: Path,
) -> None:
    # The Google script entry title must be part of the vault structure:
    # a typo is caught at config load, not on the target machine.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _BASE_CONFIG.replace(
            'google_script_key_entry_title = "google_script_key"',
            'google_script_key_entry_title = "no_such_entry"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must name an entry"):
        load_config(config_path)


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
        'desktop_detect_processes = ["kwin_wayland", "plasmashell"]\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        'ubuntu_hosts = ["archive.ubuntu.com"]\n'
        '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
        "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n"
        'swapfile_mode = "0600"\nsize_tolerance_mb = 1\n'
        'service_unit_name = "swapfile.service"\n'
        '[zswap_service]\nenabled = true\ncompressor = "zstd"\n'
        "max_pool_percent = 50\naccept_threshold_percent = 100\n"
        'shrinker_enabled = true\nservice_unit_name = "zswap.service"\n'
        '[zram_service]\ncompressor = "zstd"\nswap_priority = 1111\n'
        "memory_fraction_percent = 96\nfallback_cpu_count = 8\n"
        'alignment_bytes = 4096\nreset_busy_attempts = 5\n'
        "reset_busy_retry_delay_seconds = 0.5\n"
        'service_unit_name = "zram.service"\n'
        "[system_metrics_setup]\n"
        "backoff_base_seconds = 2\nbackoff_multiplier = 2\n"
        "backoff_max_seconds = 14400\n"
        'python_version = "3"\nerror_priority = 3\n'
        'venv_dir = "/usr/local/lib/pyntara/venv"\n'
        'system_config_path = "/etc/pyntara/config.toml"\n'
        'command_path = "/usr/local/bin/commit_system_metrics"\n'
        'system_metrics_dir = "/var/lib/pyntara/metrics"\n'
        'system_metrics_dir_mode = "0700"\nqueue_file_mode = "0600"\n'
        'max_queue_file_size_bytes = 104857600\nsend_order = "oldest_first"\n'
        'queue_file_suffix_length = 12\n'
        'spool_dir = "/var/spool/system_metrics"\nspool_dir_mode = "1733"\n'
        'command_file_mode = "0755"\n'
        'service_unit_name = "system_metrics.service"\n'
        'ingest_service_unit_name = "system_metrics-ingest.service"\n'
        'ingest_path_unit_name = "system_metrics-ingest.path"\n'
        'service_journal_identifier = "system_metrics"\n'
        'commit_journal_identifier = "commit_system_metrics"\n'
        'main_outbox_dir = "main_outbox"\ntemp_dir = "temp"\n'
        'spool_temp_prefix = ".commit-"\nqueue_link_attempts = 5\n'
        'google_script_dir = "google_script"\nmain_sent_dir = "main_sent"\n'
        "google_script_timeout_seconds = 60\n"
        'google_script_key_entry_title = "google_script_key"\n'
        "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'\n"
        '[system_metrics_setup.collector]\n'
        "boot_delay_seconds = 30\n"
        'daily_send_time = "12:00:00"\n'
        "threshold_percent = 50\n"
        "retry_base_seconds = 2\n"
        "retry_multiplier = 2\n"
        "retry_max_seconds = 600\n"
        "command_timeout_seconds = 15\n"
        'service_unit_name = "system_metrics_collector.service"\n'
        'timer_unit_name = "system_metrics_collector.timer"\n'
        'journal_identifier = "system_metrics_collector"\n'
        'lock_file_path = "/run/pyntara/system_metrics_collector.lock"\n'
        'report_file_name = "network.json"\n'
        '[vault_structure]\n[[vault_structure.entries]]\ntitle = "password_salt"\n'
        'notes = "Primary salt."\n[[vault_structure.entries]]\n'
        'title = "pyntara_local_vault_password"\nnotes = "Local vault password."\n'
        '[[vault_structure.entries]]\ntitle = "google_script_key"\n'
        'notes = "Google script credentials."\n'
        '[local_vault_setup]\nsource_vault_production = "secrets/production.vault"\n'
        'source_vault_default = "secrets/default.vault"\n'
        'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"\n'
        'pass_file_path = "/etc/pyntara/pass"\n'
        'vault_password_entry_title = "pyntara_local_vault_password"\n'
        'secrets_dir_mode = "0700"\nlocal_vault_file_mode = "0640"\n'
        'pass_dir_mode = "0700"\npass_file_mode = "0400"\nerror_priority = 3\n',
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
        'desktop_detect_processes = ["kwin_wayland", "plasmashell"]\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        'ubuntu_hosts = ["archive.ubuntu.com"]\n'
        '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
        "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n"
        'swapfile_mode = "0600"\nsize_tolerance_mb = 1\n'
        'service_unit_name = "swapfile.service"\n'
        '[zswap_service]\nenabled = true\ncompressor = "zstd"\n'
        "max_pool_percent = 50\naccept_threshold_percent = 100\n"
        'shrinker_enabled = true\nservice_unit_name = "zswap.service"\n'
        '[zram_service]\ncompressor = "zstd"\nswap_priority = 1111\n'
        "memory_fraction_percent = 96\nfallback_cpu_count = 8\n"
        'alignment_bytes = 4096\nreset_busy_attempts = 5\n'
        "reset_busy_retry_delay_seconds = 0.5\n"
        'service_unit_name = "zram.service"\n'
        "[system_metrics_setup]\n"
        "backoff_base_seconds = 2\nbackoff_multiplier = 2\n"
        "backoff_max_seconds = 14400\n"
        'python_version = "3"\nerror_priority = 3\n'
        'venv_dir = "/usr/local/lib/pyntara/venv"\n'
        'system_config_path = "/etc/pyntara/config.toml"\n'
        'command_path = "/usr/local/bin/commit_system_metrics"\n'
        'system_metrics_dir = "/var/lib/pyntara/metrics"\n'
        'system_metrics_dir_mode = "0700"\nqueue_file_mode = "0600"\n'
        'max_queue_file_size_bytes = 104857600\nsend_order = "oldest_first"\n'
        'queue_file_suffix_length = 12\n'
        'spool_dir = "/var/spool/system_metrics"\nspool_dir_mode = "1733"\n'
        'command_file_mode = "0755"\n'
        'service_unit_name = "system_metrics.service"\n'
        'ingest_service_unit_name = "system_metrics-ingest.service"\n'
        'ingest_path_unit_name = "system_metrics-ingest.path"\n'
        'service_journal_identifier = "system_metrics"\n'
        'commit_journal_identifier = "commit_system_metrics"\n'
        'main_outbox_dir = "main_outbox"\ntemp_dir = "temp"\n'
        'spool_temp_prefix = ".commit-"\nqueue_link_attempts = 5\n'
        'google_script_dir = "google_script"\nmain_sent_dir = "main_sent"\n'
        "google_script_timeout_seconds = 60\n"
        'google_script_key_entry_title = "google_script_key"\n'
        "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'\n"
        '[system_metrics_setup.collector]\n'
        "boot_delay_seconds = 30\n"
        'daily_send_time = "12:00:00"\n'
        "threshold_percent = 50\n"
        "retry_base_seconds = 2\n"
        "retry_multiplier = 2\n"
        "retry_max_seconds = 600\n"
        "command_timeout_seconds = 15\n"
        'service_unit_name = "system_metrics_collector.service"\n'
        'timer_unit_name = "system_metrics_collector.timer"\n'
        'journal_identifier = "system_metrics_collector"\n'
        'lock_file_path = "/run/pyntara/system_metrics_collector.lock"\n'
        'report_file_name = "network.json"\n'
        '[vault_structure]\n[[vault_structure.entries]]\ntitle = "password_salt"\n'
        'notes = "Primary salt."\n[[vault_structure.entries]]\n'
        'title = "pyntara_local_vault_password"\nnotes = "Local vault password."\n'
        '[[vault_structure.entries]]\ntitle = "google_script_key"\n'
        'notes = "Google script credentials."\n'
        '[local_vault_setup]\nsource_vault_production = "secrets/production.vault"\n'
        'source_vault_default = "secrets/default.vault"\n'
        'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"\n'
        'pass_file_path = "/etc/pyntara/pass"\n'
        'vault_password_entry_title = "pyntara_local_vault_password"\n'
        'secrets_dir_mode = "0700"\nlocal_vault_file_mode = "0640"\n'
        'pass_dir_mode = "0700"\npass_file_mode = "0400"\nerror_priority = 3\n',
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
