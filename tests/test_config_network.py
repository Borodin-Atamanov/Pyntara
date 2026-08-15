"""Config tests for [i2pd_service_setup], [yggdrasil_service_setup],
[ssh_daemon_setup] and [ssh_client_setup]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # i2pd github_repo is a number, not a string
        base_config().replace(
            'github_repo = "PurpleI2P/i2pd"', "github_repo = 1"
        ),
        # i2pd github_repo is an empty string
        base_config().replace(
            'github_repo = "PurpleI2P/i2pd"', 'github_repo = ""'
        ),
        # i2pd download_dir is a number, not a string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/i2pd-download"', "download_dir = 1"
        ),
        # i2pd download_dir is an empty string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/i2pd-download"', 'download_dir = ""'
        ),
        # i2pd service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "i2pd.service"', "service_unit_name = 1"
        ),
        # i2pd config_path is an empty string
        base_config().replace(
            'config_path = "/etc/i2pd/i2pd.conf"', 'config_path = ""'
        ),
        # i2pd log_level is not a known level
        base_config().replace('log_level = "warn"', 'log_level = "chatty"'),
        # i2pd log_level is a number, not a string
        base_config().replace('log_level = "warn"', "log_level = 1"),
        # i2pd http_enabled is an integer, not a boolean
        base_config().replace("http_enabled = false", "http_enabled = 0"),
        # i2pd socks_proxy_enabled is a string, not a boolean
        base_config().replace(
            "socks_proxy_enabled = true", 'socks_proxy_enabled = "true"'
        ),
        # i2pd install_retries is a string, not an integer
        base_config().replace("install_retries = 3", 'install_retries = "3"'),
        # i2pd install_retries is zero
        base_config().replace("install_retries = 3", "install_retries = 0"),
        # i2pd start_check_attempts is a string, not an integer
        base_config().replace(
            "start_check_attempts = 5", 'start_check_attempts = "5"'
        ),
        # i2pd start_check_attempts is zero
        base_config().replace("start_check_attempts = 5", "start_check_attempts = 0"),
        # i2pd start_check_retry_delay_seconds is a string, not a number
        base_config().replace(
            "start_check_retry_delay_seconds = 1",
            'start_check_retry_delay_seconds = "1"',
        ),
        # i2pd start_check_retry_delay_seconds is zero
        base_config().replace(
            "start_check_retry_delay_seconds = 1",
            "start_check_retry_delay_seconds = 0",
        ),
        # yggdrasil github_repo is a number, not a string
        base_config().replace(
            'github_repo = "yggdrasil-network/yggdrasil-go"', "github_repo = 1"
        ),
        # yggdrasil github_repo is an empty string
        base_config().replace(
            'github_repo = "yggdrasil-network/yggdrasil-go"', 'github_repo = ""'
        ),
        # yggdrasil download_dir is a number, not a string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/yggdrasil-download"',
            "download_dir = 1",
        ),
        # yggdrasil download_dir is an empty string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/yggdrasil-download"',
            'download_dir = ""',
        ),
        # yggdrasil service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "yggdrasil.service"', "service_unit_name = 1"
        ),
        # yggdrasil service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "yggdrasil.service"', 'service_unit_name = ""'
        ),
        # yggdrasil install_retries is a string, not an integer
        base_config().replace(
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 3',
            'service_unit_name = "yggdrasil.service"\ninstall_retries = "3"',
        ),
        # yggdrasil install_retries is zero
        base_config().replace(
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 3',
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 0',
        ),
        # yggdrasil config_path is a number, not a string
        base_config().replace(
            'config_path = "/etc/yggdrasil/yggdrasil.conf"', "config_path = 1"
        ),
        # yggdrasil private_key_path is an empty string
        base_config().replace(
            'private_key_path = "/etc/yggdrasil/private-key.pem"',
            'private_key_path = ""',
        ),
        # yggdrasil config_file_mode is not an octal string
        base_config().replace('config_file_mode = "0640"', 'config_file_mode = "640"'),
        # yggdrasil private_key_file_mode is not octal
        base_config().replace(
            'private_key_file_mode = "0600"', 'private_key_file_mode = "zzzz"'
        ),
        # yggdrasil if_name is a number, not a string
        base_config().replace('if_name = "ygg"', "if_name = 1"),
        # yggdrasil if_mtu is below the yggdrasil range
        base_config().replace("if_mtu = 65535", "if_mtu = 1000"),
        # yggdrasil if_mtu is above the yggdrasil range
        base_config().replace("if_mtu = 65535", "if_mtu = 70000"),
        # yggdrasil if_mtu is a string, not an integer
        base_config().replace("if_mtu = 65535", 'if_mtu = "65535"'),
        # yggdrasil admin_listen is an empty string
        base_config().replace(
            'admin_listen = "unix:///var/run/yggdrasil/yggdrasil.sock"',
            'admin_listen = ""',
        ),
        # yggdrasil listen is a string, not an array
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]', 'listen = "tcp://[::]:0"'
        ),
        # yggdrasil listen contains a wss scheme, which is not a listener
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]',
            'listen = ["wss://[::]:0"]',
        ),
        # yggdrasil listen contains a socks scheme, which is outgoing only
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]',
            'listen = ["socks://proxy:1080/1.2.3.4:1000"]',
        ),
        # yggdrasil multicast_interfaces is a string, not an array
        base_config().replace(
            '[[yggdrasil_service_setup.multicast_interfaces]]\nregex = ".*"\nbeacon = true\nlisten = true\n',
            'multicast_interfaces = ".*"\n',
        ),
        # yggdrasil multicast regex is an empty string
        base_config().replace('regex = ".*"', 'regex = ""'),
        # yggdrasil multicast beacon is a string, not a boolean
        base_config().replace("beacon = true", 'beacon = "true"'),
        # yggdrasil multicast listen is an integer, not a boolean
        base_config().replace("listen = true", "listen = 1"),
        # yggdrasil peer_batch_size is zero
        base_config().replace("peer_batch_size = 100", "peer_batch_size = 0"),
        # yggdrasil peer_target_count is a string, not an integer
        base_config().replace(
            "peer_target_count = 6", 'peer_target_count = "6"'
        ),
        # yggdrasil peer_probe_timeout_seconds is zero
        base_config().replace(
            "peer_probe_timeout_seconds = 30", "peer_probe_timeout_seconds = 0"
        ),
        # yggdrasil peer_max_batches is negative
        base_config().replace("peer_max_batches = 0", "peer_max_batches = -1"),
        # yggdrasil static_peers is a string, not an array
        base_config().replace("static_peers = []", 'static_peers = "tcp://1.2.3.4:1000"'),
        # yggdrasil static_peers contains an unknown scheme
        base_config().replace(
            "static_peers = []", 'static_peers = ["carrierpigeon://1.2.3.4:1000"]'
        ),
        # ssh_daemon_setup package_name is a number, not a string
        base_config().replace(
            'package_name = "openssh-server"', "package_name = 1"
        ),
        # ssh_daemon_setup package_name is an empty string
        base_config().replace(
            'package_name = "openssh-server"', 'package_name = ""'
        ),
        # ssh_daemon_setup package_status_timeout_seconds is zero
        base_config().replace(
            "package_status_timeout_seconds = 30",
            "package_status_timeout_seconds = 0",
        ),
        # ssh_daemon_setup service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "ssh.service"', "service_unit_name = 1"
        ),
        # ssh_daemon_setup service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "ssh.service"', 'service_unit_name = ""'
        ),
        # ssh_daemon_setup socket_unit_name is a number, not a string
        base_config().replace(
            'socket_unit_name = "ssh.socket"', "socket_unit_name = 1"
        ),
        # ssh_daemon_setup socket_unit_name is an empty string
        base_config().replace(
            'socket_unit_name = "ssh.socket"', 'socket_unit_name = ""'
        ),
        # ssh_daemon_setup sshd_config_path is a number, not a string
        base_config().replace(
            'sshd_config_path = "/etc/ssh/sshd_config"', "sshd_config_path = 1"
        ),
        # ssh_daemon_setup sshd_config_path is an empty string
        base_config().replace(
            'sshd_config_path = "/etc/ssh/sshd_config"', 'sshd_config_path = ""'
        ),
        # ssh_daemon_setup sshd_config_dropin_path is an empty string
        base_config().replace(
            'sshd_config_dropin_path = "/etc/ssh/sshd_config.d/pyntara.conf"',
            'sshd_config_dropin_path = ""',
        ),
        # ssh_daemon_setup dropin_file_mode is not octal
        base_config().replace('dropin_file_mode = "0644"', 'dropin_file_mode = "zzzz"'),
        # ssh_daemon_setup private_key_file_name is an empty string
        base_config().replace(
            'private_key_file_name = "id_ed25519"', 'private_key_file_name = ""'
        ),
        # ssh_daemon_setup public_key_file_name is a number, not a string
        base_config().replace(
            'public_key_file_name = "id_ed25519.pub"', "public_key_file_name = 1"
        ),
        # ssh_daemon_setup private_key_file_mode is not four digits
        base_config().replace('private_key_file_mode = "0600"', 'private_key_file_mode = "600"'),
        # ssh_daemon_setup public_key_file_mode is a number, not a string
        base_config().replace('public_key_file_mode = "0644"', "public_key_file_mode = 644"),
        # ssh_daemon_setup authorized_keys_file_mode is not octal
        base_config().replace(
            'authorized_keys_file_mode = "0600"', 'authorized_keys_file_mode = "nope"'
        ),
        # ssh_daemon_setup ssh_dir_mode is a number, not a string
        base_config().replace('ssh_dir_mode = "0700"', "ssh_dir_mode = 700"),
        # ssh_daemon_setup root_ssh_dir is an empty string
        base_config().replace('root_ssh_dir = "/root/.ssh"', 'root_ssh_dir = ""'),
        # ssh_daemon_setup users is a string, not an array
        base_config().replace('users = ["i", "j", "k"]', 'users = "i"'),
        # ssh_daemon_setup users is an empty array
        base_config().replace('users = ["i", "j", "k"]', "users = []"),
        # ssh_daemon_setup users contains a number, not strings
        base_config().replace('users = ["i", "j", "k"]', "users = [1]"),
        # ssh_daemon_setup users contains an empty string
        base_config().replace('users = ["i", "j", "k"]', 'users = [""]'),
        # ssh_daemon_setup users contains duplicates
        base_config().replace('users = ["i", "j", "k"]', 'users = ["i", "i"]'),
        # ssh_daemon_setup directives is a string, not an array of tables
        base_config().replace(
            '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = "yes"\n',
            'directives = "PubkeyAuthentication yes"\n',
        ),
        # ssh_daemon_setup directive name is an empty string
        base_config().replace(
            '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = "yes"\n',
            '[[ssh_daemon_setup.directives]]\nname = ""\nvalue = "yes"\n',
        ),
        # ssh_daemon_setup directive value is an empty string
        base_config().replace(
            '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = "yes"\n',
            '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = ""\n',
        ),
        # tor_setup package_name is a number, not a string
        base_config().replace('package_name = "tor"', "package_name = 1"),
        # tor_setup package_name is an empty string
        base_config().replace('package_name = "tor"', 'package_name = ""'),
        # tor_setup service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "tor@default.service"', "service_unit_name = 1"
        ),
        # tor_setup service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "tor@default.service"', 'service_unit_name = ""'
        ),
        # tor_setup torrc_path is a number, not a string
        base_config().replace('torrc_path = "/etc/tor/torrc"', "torrc_path = 1"),
        # tor_setup torrc_path is an empty string
        base_config().replace(
            'torrc_path = "/etc/tor/torrc"', 'torrc_path = ""'
        ),
        # tor_setup torrc_dropin_path is a number, not a string
        base_config().replace(
            'torrc_dropin_path = "/etc/tor/pyntara.conf"',
            "torrc_dropin_path = 1",
        ),
        # tor_setup torrc_dropin_path is an empty string
        base_config().replace(
            'torrc_dropin_path = "/etc/tor/pyntara.conf"',
            'torrc_dropin_path = ""',
        ),
        # tor_setup torrc_include_path is a number, not a string
        base_config().replace(
            'torrc_include_path = "/etc/tor/pyntara.conf"',
            "torrc_include_path = 1",
        ),
        # tor_setup torrc_include_path is an empty string
        base_config().replace(
            'torrc_include_path = "/etc/tor/pyntara.conf"',
            'torrc_include_path = ""',
        ),
        # tor_setup dropin_file_mode is not octal
        base_config().replace(
            'torrc_include_path = "/etc/tor/pyntara.conf"\ndropin_file_mode = "0644"',
            'torrc_include_path = "/etc/tor/pyntara.conf"\ndropin_file_mode = "640"',
        ),
        # tor_setup hidden_service_dir is an empty string
        base_config().replace(
            'hidden_service_dir = "/var/lib/tor/ssh"', 'hidden_service_dir = ""'
        ),
        # tor_setup hidden_service_dir_mode is not octal
        base_config().replace(
            'hidden_service_dir_mode = "0700"', 'hidden_service_dir_mode = "700"'
        ),
        # tor_setup tor_user is a number, not a string
        base_config().replace('tor_user = "debian-tor"', "tor_user = 1"),
        # tor_setup tor_user is an empty string
        base_config().replace('tor_user = "debian-tor"', 'tor_user = ""'),
        # tor_setup socks_port is a string, not an integer
        base_config().replace("socks_port = 9050", 'socks_port = "9050"'),
        # tor_setup socks_port is below the port range
        base_config().replace("socks_port = 9050", "socks_port = 0"),
        # tor_setup socks_port is above the port range
        base_config().replace("socks_port = 9050", "socks_port = 70000"),
        # tor_setup onion_ssh_port is zero
        base_config().replace("onion_ssh_port = 22", "onion_ssh_port = 0"),
        # tor_setup onion_ssh_port is a string, not an integer
        base_config().replace("onion_ssh_port = 22", 'onion_ssh_port = "22"'),
        # tor_setup num_introduction_points is zero
        base_config().replace(
            "num_introduction_points = 6", "num_introduction_points = 0"
        ),
        # tor_setup log_level is not a known level
        base_config().replace('log_level = "notice"', 'log_level = "chatty"'),
        # tor_setup log_level is a number, not a string
        base_config().replace('log_level = "notice"', "log_level = 1"),
        # tor_setup install_retries is a string, not an integer
        base_config().replace(
            'num_introduction_points = 6\nlog_level = "notice"\ninstall_retries = 3',
            'num_introduction_points = 6\nlog_level = "notice"\ninstall_retries = "3"',
        ),
        # tor_setup install_retries is zero
        base_config().replace(
            'num_introduction_points = 6\nlog_level = "notice"\ninstall_retries = 3',
            'num_introduction_points = 6\nlog_level = "notice"\ninstall_retries = 0',
        ),
        # tor_setup start_check_attempts is zero
        base_config().replace(
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 5',
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 0',
        ),
        # tor_setup start_check_retry_delay_seconds is zero
        base_config().replace(
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 5\nstart_check_retry_delay_seconds = 1',
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 5\nstart_check_retry_delay_seconds = 0',
        ),
        # tor_setup start_check_retry_delay_seconds is a string
        base_config().replace(
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 5\nstart_check_retry_delay_seconds = 1',
            'log_level = "notice"\ninstall_retries = 3\nstart_check_attempts = 5\nstart_check_retry_delay_seconds = "1"',
        ),
        # tor_setup address_file_path is an empty string
        base_config().replace(
            'address_file_path = "/var/lib/pyntara/tor_ssh_address"',
            'address_file_path = ""',
        ),
        # tor_setup address_file_mode is not octal
        base_config().replace(
            'address_file_path = "/var/lib/pyntara/tor_ssh_address"\naddress_file_mode = "0644"',
            'address_file_path = "/var/lib/pyntara/tor_ssh_address"\naddress_file_mode = 644',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_rejects_duplicate_ssh_directive_names(tmp_path: Path) -> None:
    # A directive keyword must be unique in the ssh_daemon_setup table:
    # a duplicated keyword would render two lines for the same setting.
    assert_config_error(
        tmp_path,
        base_config()
        + '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = "yes"\n',
        match="directive names must be unique",
    )


def test_load_config_accepts_empty_ssh_directives(tmp_path: Path) -> None:
    # An empty directives list is valid: the drop-in is then removed by
    # the task instead of rendered.
    config = load_config(
        write_config(
            tmp_path,
            base_config().replace(
                '[[ssh_daemon_setup.directives]]\nname = "PubkeyAuthentication"\nvalue = "yes"\n',
                "",
            ),
        )
    )
    assert config.ssh_daemon_setup.directives == ()
