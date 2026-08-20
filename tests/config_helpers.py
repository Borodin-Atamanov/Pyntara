"""Shared config test helpers.

The wrong-type tests of every config section mutate exactly one value of
a full valid config, so the parser fails on that value and not on a
missing section. base_config() provides that full valid document and
assert_config_error() writes and loads a variant in one step. The two
duplicated documents of the old single test_config.py were the reason the
helpers exist: the base text now lives once, and each section test file
mutates it with a targeted replace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.config import ConfigError, load_config


def base_config() -> str:
    """Return a full valid config.toml document.

    Every wrong-type test replaces exactly one value of this document, so
    the parser fails on the mutated value, never on a missing section.
    """

    return (
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        "command_timeout_seconds = 1800\nerror_priority = 3\n"
        "process_check_timeout_seconds = 5\n"
        "task_start_delay_seconds = 0.5\n"
        'desktop_detect_processes = ["kwin_wayland", "plasmashell"]\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[dnscrypt_setup]\npackage_name = "dnscrypt-proxy"\n'
        'config_path = "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"\n'
        'service_unit_name = "dnscrypt-proxy.service"\n'
        'socket_unit_name = "dnscrypt-proxy.socket"\n'
        'socket_dropin_dir = "/etc/systemd/system/dnscrypt-proxy.socket.d"\n'
        'socket_dropin_file_name = "pyntara.conf"\n'
        'socket_dropin_file_mode = "0644"\n'
        'socket_section = "[Socket]"\n'
        'socket_dropin_header = "# Managed by the Pyntara dnscrypt_setup task."\n'
        'listen_address = "0.0.0.0:53053"\n'
        'fallback_resolvers = ["1.1.1.1", "8.8.8.8"]\n'
        'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
        'dropin_file_name = "dnscrypt.conf"\n'
        'dropin_file_mode = "0644"\n'
        'resolve_section = "[Resolve]"\n'
        'dropin_header = "# Managed by the Pyntara dnscrypt_setup task."\n'
        'dns_directive = "DNS=127.0.0.1:53053"\n'
        'domains_directive = "~."\n'
        'directive_keys = ["DNS", "Domains"]\n'
        "manage_networkmanager = true\n"
        'nmcli_check_command = ["nmcli", "--version"]\n'
        'nmcli_list_command = ["nmcli", "-t", "-f", "NAME", "connection", "show"]\n'
        'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]\n'
        'daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]\n'
        'resolvectl_status_command = ["resolvectl", "status"]\n'
        'verification_command = ["resolvectl", "query", "--cache=no", "example.com"]\n'
        "install_retries = 3\n"
        "start_check_attempts = 5\n"
        "start_check_retry_delay_seconds = 1.0\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        'ubuntu_hosts = ["archive.ubuntu.com"]\n'
        '[hostname]\nhostname_file = "/etc/hostname"\n'
        'set_hostname_command = ["hostnamectl", "set-hostname"]\n'
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
        '[i2pd_service_setup]\n'
        'github_repo = "PurpleI2P/i2pd"\n'
        'download_dir = "/var/lib/pyntara/i2pd-download"\n'
        'service_unit_name = "i2pd.service"\n'
        'config_path = "/etc/i2pd/i2pd.conf"\n'
        'log_level = "warn"\n'
        "http_enabled = false\n"
        "socks_proxy_enabled = true\n"
        "install_retries = 3\n"
        "start_check_attempts = 5\n"
        "start_check_retry_delay_seconds = 1\n"
        'tunnels_config_path = "/etc/i2pd/tunnels.conf"\n'
        'tunnel_name = "ssh"\n'
        'tunnel_host = "127.0.0.1"\n'
        'tunnel_keys_path = "/var/lib/i2pd/ssh.dat"\n'
        'address_file_path = "/var/lib/pyntara/i2pd_ssh_address"\n'
        'address_file_mode = "0644"\n'
        "[yggdrasil_service_setup]\n"
        'github_repo = "yggdrasil-network/yggdrasil-go"\n'
        'download_dir = "/var/lib/pyntara/yggdrasil-download"\n'
        'service_unit_name = "yggdrasil.service"\n'
        "install_retries = 3\n"
        'config_path = "/etc/yggdrasil/yggdrasil.conf"\n'
        'private_key_path = "/etc/yggdrasil/private-key.pem"\n'
        'config_file_mode = "0640"\n'
        'private_key_file_mode = "0600"\n'
        'if_name = "ygg"\n'
        "if_mtu = 65535\n"
        'admin_listen = "unix:///var/run/yggdrasil/yggdrasil.sock"\n'
        'listen = ["tcp://[::]:0", "tls://[::]:0"]\n'
        'peers_full_path = "/etc/yggdrasil/peers-full.txt"\n'
        'peers_tarball_url = "https://codeload.github.com/yggdrasil-network/public-peers/tar.gz/refs/heads/master"\n'
        "peer_batch_size = 100\n"
        "peer_target_count = 6\n"
        "peer_probe_timeout_seconds = 30\n"
        "peer_max_batches = 0\n"
        "static_peers = []\n"
        'address_file_path = "/var/lib/pyntara/yggdrasil_self_address"\n'
        'address_file_mode = "0644"\n'
        "[[yggdrasil_service_setup.multicast_interfaces]]\n"
        'regex = ".*"\n'
        "beacon = true\n"
        "listen = true\n"
        "[tor_setup]\n"
        'package_name = "tor"\n'
        'service_unit_name = "tor@default.service"\n'
        'torrc_path = "/etc/tor/torrc"\n'
        'torrc_dropin_path = "/etc/tor/pyntara.conf"\n'
        'torrc_include_path = "/etc/tor/pyntara.conf"\n'
        'dropin_file_mode = "0644"\n'
        'hidden_service_dir = "/var/lib/tor/ssh"\n'
        'hidden_service_dir_mode = "0700"\n'
        'tor_user = "debian-tor"\n'
        "socks_port = 9050\n"
        "onion_ssh_port = 22\n"
        "num_introduction_points = 6\n"
        'log_level = "notice"\n'
        "install_retries = 3\n"
        "start_check_attempts = 5\n"
        "start_check_retry_delay_seconds = 1\n"
        'address_file_path = "/var/lib/pyntara/tor_ssh_address"\n'
        'address_file_mode = "0644"\n'
        "[ssh_daemon_setup]\n"
        'package_name = "openssh-server"\n'
        "package_status_timeout_seconds = 30\n"
        "install_retries = 3\n"
        'service_unit_name = "ssh.service"\n'
        'socket_unit_name = "ssh.socket"\n'
        "start_check_attempts = 5\n"
        "start_check_retry_delay_seconds = 1\n"
        'sshd_config_path = "/etc/ssh/sshd_config"\n'
        'sshd_config_dropin_path = "/etc/ssh/sshd_config.d/pyntara.conf"\n'
        'dropin_file_mode = "0644"\n'
        'private_key_file_name = "id_ed25519"\n'
        'public_key_file_name = "id_ed25519.pub"\n'
        'private_key_file_mode = "0600"\n'
        'public_key_file_mode = "0644"\n'
        'authorized_keys_file_mode = "0600"\n'
        'ssh_dir_mode = "0700"\n'
        'root_ssh_dir = "/root/.ssh"\n'
        'users = ["i", "j", "k"]\n'
        '[[ssh_daemon_setup.directives]]\n'
        'name = "PubkeyAuthentication"\n'
        'value = "yes"\n'
        "[ssh_client_setup]\n"
        'ssh_config_path = "/etc/ssh/ssh_config"\n'
        'ssh_config_dropin_path = "/etc/ssh/ssh_config.d/pyntara.conf"\n'
        'dropin_file_mode = "0644"\n'
        "[nextdns_setup_system_wide]\n"
        'vault_group_title = "NextDNS"\n'
        'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
        'dropin_file_name = "pyntara.conf"\n'
        'dropin_file_mode = "0644"\n'
        'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\n'
        'profile_id_file_mode = "0644"\n'
        'resolve_section = "[Resolve]"\n'
        'dropin_header = "# Managed by the Pyntara nextdns_setup_system_wide task."\n'
        'domains_directive = "~."\n'
        'ipv4_servers = ["45.90.28.0", "45.90.30.0"]\n'
        'ipv6_prefixes = ["2a07:a8c0", "2a07:a8c1"]\n'
        'dot_endpoint_format = "{profile_id}.dns.nextdns.io"\n'
        'verification_url = "https://test.nextdns.io/"\n'
        'dns_over_tls = "opportunistic"\n'
        "fallback_dns = [\n"
        '    "1.1.1.1",\n'
        '    "8.8.8.8",\n'
        '    "9.9.9.9",\n'
        "]\n"
        'directive_keys = ["DNS", "FallbackDNS", "DNSOverTLS", "Domains"]\n'
        'nmcli_check_command = ["nmcli", "--version"]\n'
        'nmcli_list_command = ["nmcli", "-t", "-f", "NAME", "connection", "show"]\n'
        'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]\n'
        'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]\n'
        'resolvectl_status_command = ["resolvectl", "status"]\n'
        'verification_command = ["curl", "--location", "--fail", "--silent", "--show-error", "--max-time", "{timeout}", "{url}"]\n'
        "manage_networkmanager = true\n"
        "error_priority = 3\n"
        "command_timeout_seconds = 60\n"
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


def write_config(tmp_path: Path, content: str) -> Path:
    """Write content as config.toml in tmp_path and return its path."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def assert_config_error(
    tmp_path: Path, content: str, match: str | None = None
) -> None:
    """Write content as config.toml and expect load_config to raise.

    match narrows the assertion to a ConfigError message fragment; without
    it any ConfigError passes.
    """

    if match is None:
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, content))
    else:
        with pytest.raises(ConfigError, match=match):
            load_config(write_config(tmp_path, content))
