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
from config_helpers import base_config

from pyntara.config import (
    ConfigError,
    RustdeskOptionConfig,
    SshDirective,
    YggdrasilMulticastInterfaceConfig,
    load_config,
)

VALID_TOML = """\
[engine]
task_data_root = "/var/lib/pyntara/task-data"
notice_timeout = 7
command_timeout_seconds = 1800
curl_timeout_seconds = 777
curl_retries = 13
curl_connect_timeout_seconds = 30
curl_retry_max_time_seconds = 1500
error_priority = 3
progress_priority = 7
process_check_timeout_seconds = 5
task_start_delay_seconds = 0.5
desktop_detect_processes = ["kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell"]

[cli_tools]
packages = ["mc", "htop"]
package_status_timeout_seconds = 30
package_install_retries = 3
package_success_threshold_percent = 70

[imagemagick_setup]
packages = ["imagemagick"]
policy_path = "/etc/ImageMagick-7/policy.xml"
package_status_timeout_seconds = 30
package_install_retries = 3

[ffmpeg_setup]
packages = ["ffmpeg"]
wayrecord_bin_path = "/usr/local/bin/pyntara-wayrecord"
wayrecord_desktop_path = "/usr/share/applications/pyntara-wayrecord.desktop"
package_status_timeout_seconds = 30
package_install_retries = 3

[add_extra_repos]
components = ["universe", "restricted", "multiverse"]
ubuntu_hosts = ["archive.ubuntu.com", "security.ubuntu.com"]
keep_downloaded_debs = true

[hostname]
hostname_file = "/etc/hostname"
set_hostname_command = ["hostnamectl", "set-hostname"]

[kde_keyboard_setup]
packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]
username = "i"
home_dir = "/home/i"
config_dir = "/home/i/.config"
kxkbrc_file_name = "kxkbrc"
appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"
applet_plugin = "org.kde.plasma.keyboardlayout"
layouts = ["us", "ru", "es"]
switch_option = "grp:caps_select"
reset_old_options = true
switch_mode = "WinClass"
use_layout_switching = true
indicator_display_style = "Flag"
kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]
panel_restart_command = ["systemctl", "--user", "--machine", "i@.host", "restart", "plasma-plasmashell.service"]
layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }

[kde_settings]
packages = ["plasma-workspace", "libkf6config-bin"]
username = "i"
home_dir = "/home/i"
user_dirs = { "XDG_DOCUMENTS_DIR" = "$HOME/Downloads" }
color_scheme = "BreezeDark"
look_and_feel = "org.kubuntudark.desktop"
look_and_feel_light = "org.kubuntulight.desktop"
automatic_look_and_feel = true
cursor_theme = "Oxygen_Yellow"
cursor_theme_light = "Oxygen_Blue"
numlock_on_boot = "off"
touchpad_click_method = "clickfinger"
touchpad_disable_on_external_mouse = false
virtual_keyboard_enabled = true
virtual_keyboard_input_method = "/usr/share/applications/org.kde.plasma.keyboard.desktop"
virtual_keyboard_locales = ["en_US", "es_MX", "ru_RU"]
kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]
sddm_autologin_user = "i"
sddm_autologin_session = "plasma"
sddm_theme = "kubuntu"
sddm_theme_cursor_size = "30"
sddm_theme_cursor_theme = "breeze_cursors"
sddm_theme_font = "Noto Sans,20"

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

[i2pd_service_setup]
github_repo = "PurpleI2P/i2pd"
download_dir = "/var/lib/pyntara/i2pd-download"
service_unit_name = "i2pd.service"
config_path = "/etc/i2pd/i2pd.conf"
log_level = "warn"
bandwidth = 12500
share = 1
http_enabled = false
socks_proxy_enabled = true
install_retries = 3
start_check_attempts = 5
start_check_retry_delay_seconds = 1
tunnels_config_path = "/etc/i2pd/tunnels.conf"
tunnel_name = "ssh"
tunnel_host = "127.0.0.1"
tunnel_keys_path = "/var/lib/i2pd/ssh.dat"
address_file_path = "/var/lib/pyntara/i2pd_ssh_address"
address_file_mode = "0644"

[yggdrasil_service_setup]
github_repo = "yggdrasil-network/yggdrasil-go"
download_dir = "/var/lib/pyntara/yggdrasil-download"
service_unit_name = "yggdrasil.service"
install_retries = 3
config_path = "/etc/yggdrasil/yggdrasil.conf"
private_key_path = "/etc/yggdrasil/private-key.pem"
config_file_mode = "0640"
private_key_file_mode = "0600"
if_name = "ygg"
if_mtu = 65535
admin_listen = "unix:///var/run/yggdrasil/yggdrasil.sock"
listen = ["tcp://[::]:0", "tls://[::]:0", "quic://[::]:0", "ws://[::]:0"]
peers_full_path = "/etc/yggdrasil/peers-full.txt"
peers_tarball_url = "https://codeload.github.com/yggdrasil-network/public-peers/tar.gz/refs/heads/master"
peer_batch_size = 100
peer_target_count = 11
peer_probe_timeout_seconds = 30
peer_max_batches = 0
static_peers = []
address_file_path = "/var/lib/pyntara/yggdrasil_self_address"
address_file_mode = "0644"
address_save_retry_base_seconds = 1
address_save_retry_multiplier = 2
address_save_retry_max_seconds = 67
connection_wait_base_seconds = 1
connection_wait_multiplier = 2
connection_wait_max_seconds = 30

[[yggdrasil_service_setup.multicast_interfaces]]
regex = ".*"
beacon = true
listen = true

[three_x_ui_xray_setup]
github_repo = "MHSanaei/3x-ui"
install_script_url = "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh"
install_dir = "/usr/local/x-ui"
service_unit_name = "x-ui.service"
start_check_attempts = 10
start_check_retry_delay_seconds = 1
install_result_env_path = "/etc/x-ui/install-result.env"
panel_port = 35353
ssl_enabled = true
panel_http_address = "127.0.0.1"
vault_entry_title = "three_x_ui_credentials"
inbound_port = 443
inbound_remark = "universal"
reality_dest = "www.google.com:443"
reality_server_names = ["www.google.com"]
reality_short_id = "6ba85179e30d4fc2"
acme_port = 80
cert_dir = "/root/cert/ip"
self_signed_cert_dir = "/root/cert/selfsigned"
server_ip_services = ["https://api4.ipify.org", "https://ipv4.icanhazip.com", "https://v4.api.ipinfo.io/ip", "https://ipv4.myexternalip.com/raw", "https://4.ident.me", "https://check-host.net/ip"]

[tor_setup]
package_name = "tor"
service_unit_name = "tor@default.service"
torrc_path = "/etc/tor/torrc"
torrc_dropin_path = "/etc/tor/pyntara.conf"
torrc_include_path = "/etc/tor/pyntara.conf"
dropin_file_mode = "0644"
hidden_service_dir = "/var/lib/tor/ssh"
hidden_service_dir_mode = "0700"
tor_user = "debian-tor"
socks_port = 9050
onion_ssh_port = 22
num_introduction_points = 6
log_level = "notice"
install_retries = 3
start_check_attempts = 5
start_check_retry_delay_seconds = 1
address_file_path = "/var/lib/pyntara/tor_ssh_address"
address_file_mode = "0644"

[ssh_daemon_setup]
package_name = "openssh-server"
augeas_tools_package_name = "augeas-tools"
package_status_timeout_seconds = 30
install_retries = 3
service_unit_name = "ssh.service"
socket_unit_name = "ssh.socket"
start_check_attempts = 5
start_check_retry_delay_seconds = 1
sshd_config_path = "/etc/ssh/sshd_config"
sshd_config_dropin_path = "/etc/ssh/sshd_config.d/pyntara.conf"
dropin_file_mode = "0644"
private_key_file_name = "id_ed25519"
public_key_file_name = "id_ed25519.pub"
port_forwarding_private_key_file_name = "id_ed25519_pf"
port_forwarding_public_key_file_name = "id_ed25519_pf.pub"
port_forwarding_authorized_keys_options = 'restrict,port-forwarding,permitlisten="*"'
private_key_file_mode = "0600"
public_key_file_mode = "0644"
authorized_keys_file_mode = "0600"
ssh_dir_mode = "0700"
root_ssh_dir = "/root/.ssh"
users = ["i", "j", "k"]

[[ssh_daemon_setup.directives]]
name = "PubkeyAuthentication"
value = "yes"

[[ssh_daemon_setup.directives]]
name = "PermitRootLogin"
value = "prohibit-password"

[ssh_client_setup]
ssh_config_path = "/etc/ssh/ssh_config"
ssh_config_dropin_path = "/etc/ssh/ssh_config.d/pyntara.conf"
dropin_file_mode = "0644"
augeas_tools_package_name = "augeas-tools"
package_status_timeout_seconds = 30
install_retries = 3

[[ssh_client_setup.directives]]
name = "AddressFamily"
value = "any"

[[ssh_client_setup.directives]]
name = "CheckHostIP"
value = "no"

[nextdns_setup_system_wide]
vault_group_title = "NextDNS"
profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"
profile_id_file_mode = "0644"
error_priority = 3

[port_forwarding_setup]
vault_group_title = "port_forwarding_servers"
passphrase_entry_title = "ssh_passphase_for_port_forwarding"
remote_ssh_user = "i"
desired_port_min = 32768
desired_port_max = 60999
server_alive_interval_seconds = 61
server_alive_count_max = 3
connect_timeout_seconds = 31
backoff_base_seconds = 2
backoff_multiplier = 2
backoff_max_seconds = 1024
state_file_path = "/var/lib/pyntara/port_forwarding_state.json"
service_unit_name = "auto_port_forwarding.service"
service_restart_seconds = 30
journal_identifier = "auto_port_forwarding"
error_priority = 3

[rustdesk_setup]
github_repo = "rustdesk/rustdesk"
download_dir = "/var/cache/pyntara/rustdesk"
id_file_path = "/var/lib/pyntara/rustdesk_id"
id_file_mode = "0644"
vault_entry_title = "rustdesk_password"
service_unit_name = "rustdesk.service"
password_words = 6
password_separator = " "
config_dir = "/home/i/.config/rustdesk"
install_timeout_seconds = 600
apt_update_timeout_seconds = 600
install_retries = 2
start_check_attempts = 10
start_check_retry_delay_seconds = 1.0

[[rustdesk_setup.options]]
key = "enable-udp-punch"
value = "Y"

[[rustdesk_setup.options]]
key = "direct-server"
value = "Y"

[system_metrics_setup]
backoff_base_seconds = 2
backoff_multiplier = 2
backoff_max_seconds = 14400
python_version = "3"
error_priority = 3
venv_dir = "/usr/local/lib/pyntara/venv"
system_config_path = "/etc/pyntara/config.toml"
command_path = "/usr/local/bin/commit_system_metrics"
vault_backup_file_name = "{hostname}.kdbx"
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

[[system_metrics_setup.collector.network_modules]]
name = "ipv4_link"
command = ["ip", "-4", "addr", "show", "scope", "link"]

[[system_metrics_setup.collector.network_modules]]
name = "ipv6_link"
command = ["ip", "-6", "addr", "show", "scope", "link"]

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

[[vault_structure.entries]]
title = "three_x_ui_credentials"
notes = "3x-ui panel credentials on this machine."

[[vault_structure.entries]]
title = "ssh_passphase_for_port_forwarding"
generated_password = "proquint-7"
notes = "Passphrase of the port-forwarding key."

[[vault_structure.entries]]
title = "rustdesk_password"
notes = "RustDesk access password of this machine."

[dnsproxy_setup]
github_repo = "AdguardTeam/dnsproxy"
download_dir = "/tmp/dnsproxy"
binary_path = "/usr/local/bin/dnsproxy"
service_unit_name = "dnsproxy.service"
service_unit_path = "/etc/systemd/system/dnsproxy.service"
service_template_path = "task_data/dnsproxy_setup/dnsproxy.service"
listen_addresses = ["0.0.0.0", "::"]
listen_port = 53053
doh_url_format = "https://dns.nextdns.io/{profile_id}"
dot_host_format = "tls://{profile_id}.dns.nextdns.io"
doq_host_format = "quic://{profile_id}.dns.nextdns.io"
upstream_mode = "load_balance"
cache_enabled = true
cache_size_bytes = 16777216
bootstrap_resolvers = ["1.1.1.1", "2606:4700:4700::1111"]
append_provider_dns = true
timeout_seconds = 55
log_rate_limit_interval_seconds = 3777
log_rate_limit_burst = 7777
service_restart_seconds = 2.0
install_retries = 3
start_check_attempts = 5
start_check_retry_delay_seconds = 1.0
resolved_conf_dir = "/etc/systemd/resolved.conf.d"
resolved_dropin_file_name = "pyntara-dnsproxy.conf"
resolved_dropin_file_mode = "0644"
resolved_dropin_header = "# Managed by the Pyntara dnsproxy_setup task."
resolved_section = "[Resolve]"
resolved_dns_directives = ["DNS=127.0.0.1:53053", "DNS=[::1]:53053"]
resolved_domains_directive = "Domains=~."
manage_networkmanager = true
nmcli_check_command = ["nmcli", "--version"]
nmcli_active_list_command = ["nmcli", "-t", "-f", "NAME,UUID,DEVICE", "connection", "show", "--active"]
nmcli_dns_state_command = ["nmcli", "-t", "-f", "ipv4.ignore-auto-dns,ipv6.ignore-auto-dns", "connection", "show", "{connection}"]
nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]
nmcli_reapply_command = ["nmcli", "device", "reapply", "{device}"]
daemon_reload_command = ["systemctl", "daemon-reload"]
restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]
resolvectl_status_command = ["resolvectl", "status"]
resolvectl_dns_command = ["resolvectl", "dns"]
nmcli_dns_command = ["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"]
verification_domain = "example.com"
verification_command = ["resolvectl", "query", "--cache=no", "{domain}"]
ss_tcp_listen_command = ["ss", "-lntp"]
ss_udp_listen_command = ["ss", "-lunp"]
kill_command = ["kill"]
service_log_command = ["journalctl", "-u", "{unit}", "--no-pager", "-n", "20"]
profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"
profile_id_file_mode = "0644"

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
    assert config.engine.curl_timeout_seconds == 777
    assert config.engine.curl_retries == 13
    assert config.engine.curl_connect_timeout_seconds == 30
    assert config.engine.curl_retry_max_time_seconds == 1500
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
    assert config.imagemagick_setup.packages == ("imagemagick",)
    assert config.imagemagick_setup.policy_path == Path("/etc/ImageMagick-7/policy.xml")
    assert config.imagemagick_setup.package_status_timeout_seconds == 30
    assert config.imagemagick_setup.package_install_retries == 3
    assert config.ffmpeg_setup.packages == ("ffmpeg",)
    assert config.ffmpeg_setup.wayrecord_bin_path == Path(
        "/usr/local/bin/pyntara-wayrecord"
    )
    assert config.ffmpeg_setup.wayrecord_desktop_path == Path(
        "/usr/share/applications/pyntara-wayrecord.desktop"
    )
    assert config.ffmpeg_setup.package_status_timeout_seconds == 30
    assert config.ffmpeg_setup.package_install_retries == 3
    assert config.rustdesk_setup.github_repo == "rustdesk/rustdesk"
    assert config.rustdesk_setup.download_dir == Path("/var/cache/pyntara/rustdesk")
    assert config.rustdesk_setup.id_file_path == Path("/var/lib/pyntara/rustdesk_id")
    assert config.rustdesk_setup.id_file_mode == 0o644
    assert config.rustdesk_setup.vault_entry_title == "rustdesk_password"
    assert config.rustdesk_setup.password_words == 6
    assert config.rustdesk_setup.password_separator == " "
    assert config.rustdesk_setup.config_dir == Path("/home/i/.config/rustdesk")
    assert config.rustdesk_setup.start_check_attempts == 10
    assert config.rustdesk_setup.options == (
        RustdeskOptionConfig(key="enable-udp-punch", value="Y"),
        RustdeskOptionConfig(key="direct-server", value="Y"),
    )
    assert config.add_extra_repos.components == ("universe", "restricted", "multiverse")
    assert config.add_extra_repos.ubuntu_hosts == (
        "archive.ubuntu.com",
        "security.ubuntu.com",
    )
    assert config.dnsproxy_setup.append_provider_dns is True
    assert config.dnsproxy_setup.cache_size_bytes == 16777216
    assert config.dnsproxy_setup.timeout_seconds == 55
    assert config.dnsproxy_setup.log_rate_limit_interval_seconds == 3777
    assert config.dnsproxy_setup.log_rate_limit_burst == 7777
    assert config.dnsproxy_setup.verification_domain == "example.com"
    assert config.dnsproxy_setup.nmcli_active_list_command == (
        "nmcli",
        "-t",
        "-f",
        "NAME,UUID,DEVICE",
        "connection",
        "show",
        "--active",
    )
    assert config.dnsproxy_setup.nmcli_reapply_command == (
        "nmcli",
        "device",
        "reapply",
        "{device}",
    )
    assert config.dnsproxy_setup.kill_command == ("kill",)
    assert config.dnsproxy_setup.service_log_command == (
        "journalctl",
        "-u",
        "{unit}",
        "--no-pager",
        "-n",
        "20",
    )
    assert config.hostname.hostname_file == "/etc/hostname"
    assert config.hostname.set_hostname_command == (
        "hostnamectl",
        "set-hostname",
    )
    assert config.kde_keyboard_setup.packages == (
        "libkf6config-bin",
        "qdbus-qt6",
        "python3-dbus",
    )
    assert config.kde_keyboard_setup.username == "i"
    assert config.kde_keyboard_setup.home_dir == "/home/i"
    assert config.kde_keyboard_setup.layouts == ("us", "ru", "es")
    assert config.kde_keyboard_setup.switch_option == "grp:caps_select"
    assert config.kde_keyboard_setup.use_layout_switching is True
    assert config.kde_keyboard_setup.indicator_display_style == "Flag"
    assert config.kde_keyboard_setup.layout_switch_shortcuts == {
        "Switch keyboard layout to Spanish": "Meta+Q"
    }
    assert config.kde_keyboard_setup.kwin_reload_command == (
        "qdbus6",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin.reconfigure",
    )
    assert config.kde_settings.packages == ("plasma-workspace", "libkf6config-bin")
    assert config.kde_settings.username == "i"
    assert config.kde_settings.home_dir == "/home/i"
    assert config.kde_settings.user_dirs == {"XDG_DOCUMENTS_DIR": "$HOME/Downloads"}
    assert config.kde_settings.color_scheme == "BreezeDark"
    assert config.kde_settings.look_and_feel == "org.kubuntudark.desktop"
    assert config.kde_settings.automatic_look_and_feel is True
    assert config.kde_settings.cursor_theme == "Oxygen_Yellow"
    assert config.kde_settings.look_and_feel_light == "org.kubuntulight.desktop"
    assert config.kde_settings.cursor_theme_light == "Oxygen_Blue"
    assert config.kde_settings.numlock_on_boot == "off"
    assert config.kde_settings.touchpad_click_method == "clickfinger"
    assert config.kde_settings.touchpad_disable_on_external_mouse is False
    assert config.kde_settings.virtual_keyboard_enabled is True
    assert config.kde_settings.virtual_keyboard_locales == ("en_US", "es_MX", "ru_RU")
    assert config.kde_settings.sddm_autologin_user == "i"
    assert config.kde_settings.sddm_autologin_session == "plasma"
    assert config.kde_settings.sddm_theme == "kubuntu"
    assert config.kde_settings.sddm_theme_cursor_size == "30"
    assert config.kde_settings.sddm_theme_cursor_theme == "breeze_cursors"
    assert config.kde_settings.sddm_theme_font == "Noto Sans,20"
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
    assert config.i2pd_service_setup.github_repo == "PurpleI2P/i2pd"
    assert config.i2pd_service_setup.download_dir == Path(
        "/var/lib/pyntara/i2pd-download"
    )
    assert config.i2pd_service_setup.service_unit_name == "i2pd.service"
    assert config.i2pd_service_setup.config_path == Path("/etc/i2pd/i2pd.conf")
    assert config.i2pd_service_setup.log_level == "warn"
    assert config.i2pd_service_setup.bandwidth == 12500
    assert config.i2pd_service_setup.share == 1
    assert config.i2pd_service_setup.http_enabled is False
    assert config.i2pd_service_setup.socks_proxy_enabled is True
    assert config.i2pd_service_setup.install_retries == 3
    assert config.i2pd_service_setup.start_check_attempts == 5
    assert config.i2pd_service_setup.start_check_retry_delay_seconds == 1
    assert config.i2pd_service_setup.tunnels_config_path == Path(
        "/etc/i2pd/tunnels.conf"
    )
    assert config.i2pd_service_setup.tunnel_name == "ssh"
    assert config.i2pd_service_setup.tunnel_host == "127.0.0.1"
    assert config.i2pd_service_setup.tunnel_keys_path == Path(
        "/var/lib/i2pd/ssh.dat"
    )
    assert config.i2pd_service_setup.address_file_path == Path(
        "/var/lib/pyntara/i2pd_ssh_address"
    )
    assert config.i2pd_service_setup.address_file_mode == 0o644
    assert config.three_x_ui_xray_setup.panel_port == 35353
    assert config.three_x_ui_xray_setup.ssl_enabled is True
    assert config.three_x_ui_xray_setup.install_result_env_path == Path(
        "/etc/x-ui/install-result.env"
    )
    assert config.three_x_ui_xray_setup.acme_port == 80
    assert config.three_x_ui_xray_setup.cert_dir == Path("/root/cert/ip")
    assert config.three_x_ui_xray_setup.cert_fullchain == Path(
        "/root/cert/ip/fullchain.pem"
    )
    assert config.three_x_ui_xray_setup.cert_privkey == Path(
        "/root/cert/ip/privkey.pem"
    )
    assert config.three_x_ui_xray_setup.self_signed_cert_dir == Path(
        "/root/cert/selfsigned"
    )
    assert config.three_x_ui_xray_setup.self_signed_cert_fullchain == Path(
        "/root/cert/selfsigned/fullchain.pem"
    )
    assert config.three_x_ui_xray_setup.server_ip_services == (
        "https://api4.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://v4.api.ipinfo.io/ip",
        "https://ipv4.myexternalip.com/raw",
        "https://4.ident.me",
        "https://check-host.net/ip",
    )
    assert (
        config.yggdrasil_service_setup.github_repo
        == "yggdrasil-network/yggdrasil-go"
    )
    assert config.yggdrasil_service_setup.download_dir == Path(
        "/var/lib/pyntara/yggdrasil-download"
    )
    assert config.yggdrasil_service_setup.service_unit_name == "yggdrasil.service"
    assert config.yggdrasil_service_setup.install_retries == 3
    assert config.yggdrasil_service_setup.config_path == Path(
        "/etc/yggdrasil/yggdrasil.conf"
    )
    assert config.yggdrasil_service_setup.private_key_path == Path(
        "/etc/yggdrasil/private-key.pem"
    )
    assert config.yggdrasil_service_setup.config_file_mode == 0o640
    assert config.yggdrasil_service_setup.private_key_file_mode == 0o600
    assert config.yggdrasil_service_setup.if_name == "ygg"
    assert config.yggdrasil_service_setup.if_mtu == 65535
    assert config.yggdrasil_service_setup.admin_listen == (
        "unix:///var/run/yggdrasil/yggdrasil.sock"
    )
    assert config.yggdrasil_service_setup.listen == (
        "tcp://[::]:0",
        "tls://[::]:0",
        "quic://[::]:0",
        "ws://[::]:0",
    )
    assert config.yggdrasil_service_setup.multicast_interfaces == (
        YggdrasilMulticastInterfaceConfig(regex=".*", beacon=True, listen=True),
    )
    assert config.yggdrasil_service_setup.peers_full_path == Path(
        "/etc/yggdrasil/peers-full.txt"
    )
    assert config.yggdrasil_service_setup.peer_batch_size == 100
    assert config.yggdrasil_service_setup.peer_target_count == 11
    assert config.yggdrasil_service_setup.peer_probe_timeout_seconds == 30
    assert config.yggdrasil_service_setup.peer_max_batches == 0
    assert config.yggdrasil_service_setup.static_peers == ()
    assert config.yggdrasil_service_setup.address_file_path == Path(
        "/var/lib/pyntara/yggdrasil_self_address"
    )
    assert config.yggdrasil_service_setup.address_file_mode == 0o644
    assert config.yggdrasil_service_setup.address_save_retry_base_seconds == 1
    assert config.yggdrasil_service_setup.address_save_retry_multiplier == 2
    assert config.yggdrasil_service_setup.address_save_retry_max_seconds == 67
    assert config.yggdrasil_service_setup.connection_wait_base_seconds == 1
    assert config.yggdrasil_service_setup.connection_wait_multiplier == 2
    assert config.yggdrasil_service_setup.connection_wait_max_seconds == 30
    assert config.tor_setup.package_name == "tor"
    assert config.tor_setup.service_unit_name == "tor@default.service"
    assert config.tor_setup.torrc_path == Path("/etc/tor/torrc")
    assert config.tor_setup.torrc_dropin_path == Path(
        "/etc/tor/pyntara.conf"
    )
    assert config.tor_setup.torrc_include_path == "/etc/tor/pyntara.conf"
    assert config.tor_setup.dropin_file_mode == 0o644
    assert config.tor_setup.hidden_service_dir == Path("/var/lib/tor/ssh")
    assert config.tor_setup.hidden_service_dir_mode == 0o700
    assert config.tor_setup.tor_user == "debian-tor"
    assert config.tor_setup.socks_port == 9050
    assert config.tor_setup.onion_ssh_port == 22
    assert config.tor_setup.num_introduction_points == 6
    assert config.tor_setup.log_level == "notice"
    assert config.tor_setup.install_retries == 3
    assert config.tor_setup.start_check_attempts == 5
    assert config.tor_setup.start_check_retry_delay_seconds == 1
    assert config.tor_setup.address_file_path == Path(
        "/var/lib/pyntara/tor_ssh_address"
    )
    assert config.tor_setup.address_file_mode == 0o644
    assert config.ssh_daemon_setup.package_name == "openssh-server"
    assert config.ssh_daemon_setup.augeas_tools_package_name == "augeas-tools"
    assert config.ssh_daemon_setup.package_status_timeout_seconds == 30
    assert config.ssh_daemon_setup.install_retries == 3
    assert config.ssh_daemon_setup.service_unit_name == "ssh.service"
    assert config.ssh_daemon_setup.socket_unit_name == "ssh.socket"
    assert config.ssh_daemon_setup.start_check_attempts == 5
    assert config.ssh_daemon_setup.start_check_retry_delay_seconds == 1
    assert config.ssh_daemon_setup.sshd_config_path == Path("/etc/ssh/sshd_config")
    assert config.ssh_daemon_setup.sshd_config_dropin_path == Path(
        "/etc/ssh/sshd_config.d/pyntara.conf"
    )
    assert config.ssh_daemon_setup.dropin_file_mode == 0o644
    assert config.ssh_daemon_setup.private_key_file_name == "id_ed25519"
    assert config.ssh_daemon_setup.public_key_file_name == "id_ed25519.pub"
    assert (
        config.ssh_daemon_setup.port_forwarding_private_key_file_name
        == "id_ed25519_pf"
    )
    assert (
        config.ssh_daemon_setup.port_forwarding_authorized_keys_options
        == 'restrict,port-forwarding,permitlisten="*"'
    )
    assert config.ssh_daemon_setup.private_key_file_mode == 0o600
    assert config.ssh_daemon_setup.public_key_file_mode == 0o644
    assert config.ssh_daemon_setup.authorized_keys_file_mode == 0o600
    assert config.ssh_daemon_setup.ssh_dir_mode == 0o700
    assert config.ssh_daemon_setup.root_ssh_dir == Path("/root/.ssh")
    assert config.ssh_daemon_setup.users == ("i", "j", "k")
    assert config.ssh_daemon_setup.directives == (
        SshDirective(name="PubkeyAuthentication", value="yes"),
        SshDirective(name="PermitRootLogin", value="prohibit-password"),
    )
    assert config.ssh_client_setup.ssh_config_path == Path("/etc/ssh/ssh_config")
    assert config.ssh_client_setup.ssh_config_dropin_path == Path(
        "/etc/ssh/ssh_config.d/pyntara.conf"
    )
    assert config.ssh_client_setup.dropin_file_mode == 0o644
    assert config.ssh_client_setup.augeas_tools_package_name == "augeas-tools"
    assert config.ssh_client_setup.package_status_timeout_seconds == 30
    assert config.ssh_client_setup.install_retries == 3
    assert config.ssh_client_setup.directives == (
        SshDirective(name="AddressFamily", value="any"),
        SshDirective(name="CheckHostIP", value="no"),
    )
    assert config.nextdns_setup_system_wide.vault_group_title == "NextDNS"
    assert config.nextdns_setup_system_wide.profile_id_file_path == Path(
        "/var/lib/pyntara/nextdns_profile_id"
    )
    assert config.nextdns_setup_system_wide.profile_id_file_mode == 0o644
    assert config.nextdns_setup_system_wide.error_priority == 3
    assert config.system_metrics_setup.backoff_base_seconds == 2
    assert config.system_metrics_setup.backoff_multiplier == 2
    assert config.system_metrics_setup.backoff_max_seconds == 14400
    assert config.system_metrics_setup.python_version == "3"
    assert config.system_metrics_setup.error_priority == 3
    assert config.system_metrics_setup.venv_dir == Path("/usr/local/lib/pyntara/venv")
    assert config.system_metrics_setup.system_config_path == Path("/etc/pyntara/config.toml")
    assert config.system_metrics_setup.command_path == Path("/usr/local/bin/commit_system_metrics")
    assert config.system_metrics_setup.vault_backup_file_name == "{hostname}.kdbx"
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
    assert len(config.system_metrics_setup.collector.network_modules) == 4
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
    assert config.system_metrics_setup.collector.network_modules[1].command == (
        "ip",
        "-6",
        "addr",
        "show",
        "scope",
        "global",
    )
    assert (
        config.system_metrics_setup.collector.network_modules[2].name == "ipv4_link"
    )
    assert config.system_metrics_setup.collector.network_modules[2].command == (
        "ip",
        "-4",
        "addr",
        "show",
        "scope",
        "link",
    )
    assert (
        config.system_metrics_setup.collector.network_modules[3].name == "ipv6_link"
    )
    assert config.system_metrics_setup.collector.network_modules[3].command == (
        "ip",
        "-6",
        "addr",
        "show",
        "scope",
        "link",
    )
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
    assert config.vault_structure.entries[4].title == "three_x_ui_credentials"
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
    config = load_config(config_dir)
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
        load_config(config_dir)


def test_load_config_empty_directory_raises(tmp_path: Path) -> None:
    # An empty directory renders an empty document: no section exists, so
    # the first parser reports the missing table.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with pytest.raises(ConfigError):
        load_config(config_dir)
