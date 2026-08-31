"""Config tests for [system_metrics_setup] and its [collector] sub-table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # system_metrics_setup backoff_base_seconds is a string
        base_config().replace(
            "backoff_base_seconds = 2", 'backoff_base_seconds = "2"'
        ),
        # system_metrics_setup backoff_base_seconds is zero
        base_config().replace(
            "backoff_base_seconds = 2", "backoff_base_seconds = 0"
        ),
        # system_metrics_setup backoff_base_seconds is negative
        base_config().replace(
            "backoff_base_seconds = 2", "backoff_base_seconds = -5"
        ),
        # system_metrics_setup backoff_multiplier is a string
        base_config().replace(
            "backoff_multiplier = 2", 'backoff_multiplier = "2"'
        ),
        # system_metrics_setup backoff_multiplier is one: no growth
        base_config().replace(
            "backoff_multiplier = 2", "backoff_multiplier = 1"
        ),
        # system_metrics_setup backoff_multiplier is zero
        base_config().replace(
            "backoff_multiplier = 2", "backoff_multiplier = 0"
        ),
        # system_metrics_setup backoff_max_seconds is a string
        base_config().replace(
            "backoff_max_seconds = 14400", 'backoff_max_seconds = "14400"'
        ),
        # system_metrics_setup backoff_max_seconds is below the base
        base_config().replace(
            "backoff_max_seconds = 14400", "backoff_max_seconds = 1"
        ),
        # system_metrics_setup backoff_max_seconds is negative
        base_config().replace(
            "backoff_max_seconds = 14400", "backoff_max_seconds = -5"
        ),
        # system_metrics_setup python_version is a number, not a string
        base_config().replace('python_version = "3"', "python_version = 3"),
        # system_metrics_setup python_version is an empty string
        base_config().replace('python_version = "3"', 'python_version = ""'),
        # system_metrics_setup error_priority is a string, not an integer
        base_config().replace(
            'python_version = "3"\nerror_priority = 3\n',
            'python_version = "3"\nerror_priority = "3"\n',
        ),
        # system_metrics_setup error_priority is above 7
        base_config().replace(
            'python_version = "3"\nerror_priority = 3\n',
            'python_version = "3"\nerror_priority = 8\n',
        ),
        # system_metrics_setup venv_dir is a number, not a string
        base_config().replace(
            'venv_dir = "/usr/local/lib/pyntara/venv"', "venv_dir = 1"
        ),
        # system_metrics_setup venv_dir is an empty string
        base_config().replace(
            'venv_dir = "/usr/local/lib/pyntara/venv"', 'venv_dir = ""'
        ),
        # system_metrics_setup system_config_path is a number, not a string
        base_config().replace(
            'system_config_path = "/etc/pyntara/config.toml"',
            "system_config_path = 1",
        ),
        # system_metrics_setup system_config_path is an empty string
        base_config().replace(
            'system_config_path = "/etc/pyntara/config.toml"',
            'system_config_path = ""',
        ),
        # system_metrics_setup command_path is a number, not a string
        base_config().replace(
            'command_path = "/usr/local/bin/commit_system_metrics"',
            "command_path = 1",
        ),
        # system_metrics_setup command_path is an empty string
        base_config().replace(
            'command_path = "/usr/local/bin/commit_system_metrics"',
            'command_path = ""',
        ),
        # system_metrics_setup vault_backup_file_name is a number, not a string
        base_config().replace(
            'vault_backup_file_name = "{hostname}.kdbx"',
            "vault_backup_file_name = 1",
        ),
        # system_metrics_setup vault_backup_file_name is an empty string
        base_config().replace(
            'vault_backup_file_name = "{hostname}.kdbx"',
            'vault_backup_file_name = ""',
        ),
        # system_metrics_setup system_metrics_dir is a number, not a string
        base_config().replace(
            'system_metrics_dir = "/var/lib/pyntara/metrics"',
            "system_metrics_dir = 1",
        ),
        # system_metrics_setup system_metrics_dir is an empty string
        base_config().replace(
            'system_metrics_dir = "/var/lib/pyntara/metrics"',
            'system_metrics_dir = ""',
        ),
        # system_metrics_setup system_metrics_dir_mode is not a four-digit octal string
        base_config().replace(
            'system_metrics_dir_mode = "0700"', 'system_metrics_dir_mode = "700"'
        ),
        # system_metrics_setup queue_file_mode is not a four-digit octal string
        base_config().replace(
            'queue_file_mode = "0600"', 'queue_file_mode = "060"'
        ),
        # system_metrics_setup max_queue_file_size_bytes is a string, not an integer
        base_config().replace(
            "max_queue_file_size_bytes = 104857600",
            'max_queue_file_size_bytes = "104857600"',
        ),
        # system_metrics_setup max_queue_file_size_bytes is zero
        base_config().replace(
            "max_queue_file_size_bytes = 104857600",
            "max_queue_file_size_bytes = 0",
        ),
        # system_metrics_setup max_queue_file_size_bytes is negative
        base_config().replace(
            "max_queue_file_size_bytes = 104857600",
            "max_queue_file_size_bytes = -1",
        ),
        # system_metrics_setup send_order is not a known order
        base_config().replace(
            'send_order = "oldest_first"', 'send_order = "middle_first"'
        ),
        # system_metrics_setup queue_file_suffix_length is a string, not an integer
        base_config().replace(
            "queue_file_suffix_length = 12", 'queue_file_suffix_length = "12"'
        ),
        # system_metrics_setup queue_file_suffix_length is zero
        base_config().replace(
            "queue_file_suffix_length = 12", "queue_file_suffix_length = 0"
        ),
        # system_metrics_setup spool_dir is a number, not a string
        base_config().replace(
            'spool_dir = "/var/spool/system_metrics"', "spool_dir = 1"
        ),
        # system_metrics_setup spool_dir is an empty string
        base_config().replace(
            'spool_dir = "/var/spool/system_metrics"', 'spool_dir = ""'
        ),
        # system_metrics_setup spool_dir_mode is not a four-digit octal string
        base_config().replace('spool_dir_mode = "1733"', 'spool_dir_mode = "173"'),
        # system_metrics_setup command_file_mode is not a four-digit octal string
        base_config().replace('command_file_mode = "0755"', 'command_file_mode = "755"'),
        # system_metrics_setup service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "system_metrics.service"', 'service_unit_name = ""'
        ),
        # system_metrics_setup ingest_service_unit_name is a number, not a string
        base_config().replace(
            'ingest_service_unit_name = "system_metrics-ingest.service"',
            "ingest_service_unit_name = 1",
        ),
        # system_metrics_setup ingest_path_unit_name is an empty string
        base_config().replace(
            'ingest_path_unit_name = "system_metrics-ingest.path"',
            'ingest_path_unit_name = ""',
        ),
        # system_metrics_setup service_journal_identifier is a number, not a string
        base_config().replace(
            'service_journal_identifier = "system_metrics"',
            "service_journal_identifier = 1",
        ),
        # system_metrics_setup commit_journal_identifier is an empty string
        base_config().replace(
            'commit_journal_identifier = "commit_system_metrics"',
            'commit_journal_identifier = ""',
        ),
        # system_metrics_setup main_outbox_dir is a number, not a string
        base_config().replace(
            'main_outbox_dir = "main_outbox"', "main_outbox_dir = 1"
        ),
        # system_metrics_setup temp_dir is an empty string
        base_config().replace('temp_dir = "temp"', 'temp_dir = ""'),
        # system_metrics_setup spool_temp_prefix is a number, not a string
        base_config().replace(
            'spool_temp_prefix = ".commit-"', "spool_temp_prefix = 1"
        ),
        # system_metrics_setup queue_link_attempts is a string, not an integer
        base_config().replace(
            "queue_link_attempts = 5", 'queue_link_attempts = "5"'
        ),
        # system_metrics_setup queue_link_attempts is zero
        base_config().replace(
            "queue_link_attempts = 5", "queue_link_attempts = 0"
        ),
        # system_metrics_setup google_script_dir is a number, not a string
        base_config().replace(
            'google_script_dir = "google_script"', "google_script_dir = 1"
        ),
        # system_metrics_setup google_script_dir is an empty string
        base_config().replace(
            'google_script_dir = "google_script"', 'google_script_dir = ""'
        ),
        # system_metrics_setup main_sent_dir is a number, not a string
        base_config().replace(
            'main_sent_dir = "main_sent"', "main_sent_dir = 1"
        ),
        # system_metrics_setup main_sent_dir is an empty string
        base_config().replace(
            'main_sent_dir = "main_sent"', 'main_sent_dir = ""'
        ),
        # system_metrics_setup google_script_timeout_seconds is a string
        base_config().replace(
            "google_script_timeout_seconds = 60",
            'google_script_timeout_seconds = "60"',
        ),
        # system_metrics_setup google_script_timeout_seconds is zero
        base_config().replace(
            "google_script_timeout_seconds = 60",
            "google_script_timeout_seconds = 0",
        ),
        # system_metrics_setup google_script_key_entry_title is a number
        base_config().replace(
            'google_script_key_entry_title = "google_script_key"',
            "google_script_key_entry_title = 1",
        ),
        # system_metrics_setup google_script_key_entry_title is an empty string
        base_config().replace(
            'google_script_key_entry_title = "google_script_key"',
            'google_script_key_entry_title = ""',
        ),
        # system_metrics_setup google_script_deployment_url_regex is a number
        base_config().replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = 1",
        ),
        # system_metrics_setup google_script_deployment_url_regex is empty
        base_config().replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = ''",
        ),
        # system_metrics_setup google_script_deployment_url_regex is invalid
        base_config().replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = 'a['",
        ),
        # system_metrics_setup google_script_deployment_url_regex has no group
        base_config().replace(
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'",
            "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/[A-Za-z0-9_-]+/exec$'",
        ),
        # collector section is missing entirely
        base_config().replace(
            "[system_metrics_setup.collector]\n", ""
        ),
        # collector boot_delay_seconds is a string, not an integer
        base_config().replace(
            "boot_delay_seconds = 30", 'boot_delay_seconds = "30"'
        ),
        # collector boot_delay_seconds is negative
        base_config().replace(
            "boot_delay_seconds = 30", "boot_delay_seconds = -1"
        ),
        # collector daily_send_time is a number, not a string
        base_config().replace(
            'daily_send_time = "12:00:00"', "daily_send_time = 1200"
        ),
        # collector daily_send_time is not a time of day
        base_config().replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "25:00:00"'
        ),
        # collector daily_send_time misses the minutes
        base_config().replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "12"'
        ),
        # collector daily_send_time has four parts
        base_config().replace(
            'daily_send_time = "12:00:00"', 'daily_send_time = "12:00:00:00"'
        ),
        # collector threshold_percent is a string, not an integer
        base_config().replace(
            "threshold_percent = 50", 'threshold_percent = "50"'
        ),
        # collector threshold_percent is negative
        base_config().replace(
            "threshold_percent = 50", "threshold_percent = -1"
        ),
        # collector threshold_percent is above 100
        base_config().replace(
            "threshold_percent = 50", "threshold_percent = 101"
        ),
        # collector retry_base_seconds is a string, not an integer
        base_config().replace(
            "retry_base_seconds = 2", 'retry_base_seconds = "2"'
        ),
        # collector retry_base_seconds is zero
        base_config().replace("retry_base_seconds = 2", "retry_base_seconds = 0"),
        # collector retry_multiplier is one: no growth
        base_config().replace(
            "retry_multiplier = 2", "retry_multiplier = 1"
        ),
        # collector retry_max_seconds is below the base
        base_config().replace(
            "retry_max_seconds = 600", "retry_max_seconds = 1"
        ),
        # collector command_timeout_seconds is a string, not an integer
        base_config().replace(
            "command_timeout_seconds = 15", 'command_timeout_seconds = "15"'
        ),
        # collector command_timeout_seconds is zero
        base_config().replace(
            "command_timeout_seconds = 15", "command_timeout_seconds = 0"
        ),
        # collector service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "system_metrics_collector.service"',
            'service_unit_name = ""',
        ),
        # collector timer_unit_name is a number, not a string
        base_config().replace(
            'timer_unit_name = "system_metrics_collector.timer"',
            "timer_unit_name = 1",
        ),
        # collector journal_identifier is an empty string
        base_config().replace(
            'journal_identifier = "system_metrics_collector"',
            'journal_identifier = ""',
        ),
        # collector lock_file_path is a number, not a string
        base_config().replace(
            'lock_file_path = "/run/pyntara/system_metrics_collector.lock"',
            "lock_file_path = 1",
        ),
        # collector report_file_name is an empty string
        base_config().replace(
            'report_file_name = "network.json"', 'report_file_name = ""'
        ),
        # collector network_modules is a string, not an array
        base_config().replace(
            "[[system_metrics_setup.collector.network_modules]]",
            'network_modules = "ip"',
        ),
        # collector network_modules module is not a table
        base_config().replace(
            "[[system_metrics_setup.collector.network_modules]]",
            "network_modules = [1]",
        ),
        # collector network_modules module name is an empty string
        base_config().replace(
            'name = "ipv4"', 'name = ""'
        ),
        # collector network_modules module names are duplicated
        base_config().replace(
            'name = "ipv6"', 'name = "ipv4"'
        ),
        # collector network_modules module command is an empty array
        base_config().replace(
            'command = ["ip", "-4", "addr", "show", "scope", "global"]',
            "command = []",
        ),
        # collector network_modules module command contains an empty string
        base_config().replace(
            'command = ["ip", "-4", "addr", "show", "scope", "global"]',
            'command = ["ip", ""]',
        ),
        # collector system_modules module command is missing
        base_config().replace(
            'command = ["hostname"]', ""
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_collector_parses_anonymous_network_modules(tmp_path: Path) -> None:
    # The i2pd, yggdrasil and tor_onion network modules run the address
    # commands of the dedicated venv; all parse as modules with their
    # argv commands.
    content = base_config().replace(
        "[[system_metrics_setup.collector.system_modules]]",
        '[[system_metrics_setup.collector.network_modules]]\n'
        'name = "i2pd"\n'
        'command = ["/usr/local/lib/pyntara/venv/bin/python", "-m", '
        '"pyntara.i2pd_address", "/var/lib/i2pd/ssh.dat", '
        '"/var/lib/pyntara/i2pd_ssh_address"]\n'
        '[[system_metrics_setup.collector.network_modules]]\n'
        'name = "yggdrasil"\n'
        'command = ["/usr/local/lib/pyntara/venv/bin/python", "-m", '
        '"pyntara.yggdrasil_address", "/var/lib/pyntara/yggdrasil_self_address"]\n'
        '[[system_metrics_setup.collector.network_modules]]\n'
        'name = "tor_onion"\n'
        'command = ["/usr/local/lib/pyntara/venv/bin/python", "-m", '
        '"pyntara.tor_address", "/var/lib/tor/ssh", '
        '"/var/lib/pyntara/tor_ssh_address"]\n'
        '[[system_metrics_setup.collector.network_modules]]\n'
        'name = "nextdns"\n'
        'command = ["cat", "/var/lib/pyntara/nextdns_profile_id"]\n'
        '[[system_metrics_setup.collector.network_modules]]\n'
        'name = "port_forwarding"\n'
        'command = ["/usr/local/lib/pyntara/venv/bin/python", "-m", '
        '"pyntara.port_forwarding_state", '
        '"/var/lib/pyntara/port_forwarding_state.json"]\n'
        "[[system_metrics_setup.collector.system_modules]]",
    )
    config = load_config(write_config(tmp_path, content))
    modules = config.system_metrics_setup.collector.network_modules
    assert [module.name for module in modules] == [
        "ipv4",
        "ipv6",
        "i2pd",
        "yggdrasil",
        "tor_onion",
        "nextdns",
        "port_forwarding",
    ]
    by_name = {module.name: module for module in modules}
    assert by_name["i2pd"].command == (
        "/usr/local/lib/pyntara/venv/bin/python",
        "-m",
        "pyntara.i2pd_address",
        "/var/lib/i2pd/ssh.dat",
        "/var/lib/pyntara/i2pd_ssh_address",
    )
    assert by_name["yggdrasil"].command == (
        "/usr/local/lib/pyntara/venv/bin/python",
        "-m",
        "pyntara.yggdrasil_address",
        "/var/lib/pyntara/yggdrasil_self_address",
    )
    assert by_name["tor_onion"].command == (
        "/usr/local/lib/pyntara/venv/bin/python",
        "-m",
        "pyntara.tor_address",
        "/var/lib/tor/ssh",
        "/var/lib/pyntara/tor_ssh_address",
    )
    assert by_name["nextdns"].command == (
        "cat",
        "/var/lib/pyntara/nextdns_profile_id",
    )
    assert by_name["port_forwarding"].command == (
        "/usr/local/lib/pyntara/venv/bin/python",
        "-m",
        "pyntara.port_forwarding_state",
        "/var/lib/pyntara/port_forwarding_state.json",
    )


def test_nextdns_module_path_matches_nextdns_config() -> None:
    # The nextdns collector module reads the profile ID file whose path
    # lives in the [nextdns_setup_system_wide] table. The two config
    # files must not drift apart: the module command path must equal the
    # configured profile_id_file_path, so a rename in one file is caught
    # here instead of silently breaking the telemetry.
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "config")
    modules = config.system_metrics_setup.collector.network_modules
    nextdns_module = next(
        module for module in modules if module.name == "nextdns"
    )
    assert nextdns_module.command == (
        "cat",
        str(config.nextdns_setup_system_wide.profile_id_file_path),
    )


def test_port_forwarding_module_path_matches_port_forwarding_config() -> None:
    # The port_forwarding collector module reads the state file whose
    # path lives in the [port_forwarding_setup] table. The two config
    # files must not drift apart: the module command path must equal the
    # configured state_file_path, so a rename in one file is caught here
    # instead of silently breaking the telemetry.
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "config")
    modules = config.system_metrics_setup.collector.network_modules
    port_forwarding_module = next(
        module for module in modules if module.name == "port_forwarding"
    )
    assert port_forwarding_module.command == (
        "/usr/local/lib/pyntara/venv/bin/python",
        "-m",
        "pyntara.port_forwarding_state",
        str(config.port_forwarding_setup.state_file_path),
    )
