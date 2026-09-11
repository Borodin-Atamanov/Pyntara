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

import tomllib
from pathlib import Path

import pytest
from config_checks import ConfigError, strict_config_from_document

from pyntara.config import Config
from pyntara.config.loader import render_config_source


def base_config() -> str:
    """Return a full valid config.toml document.

    Every wrong-type test replaces exactly one value of this document, so
    the parser fails on the mutated value, never on a missing section.
    """

    return (
        '[engine]\ntask_data_root = "/tmp"\nsystemd_unit_dir = "/etc/systemd/system"\nnotice_timeout = 7\n'
        "command_timeout_seconds = 8000\ncurl_timeout_seconds = 777\n"
        "curl_download_timeout_seconds = 7777\ncurl_retries = 17\n"
        "curl_retry_delay_seconds = 3\ncurl_connect_timeout_seconds = 60\n"
        "curl_retry_max_time_seconds = 7777\n"
        'github_latest_release_url = "https://api.github.com/repos/{repo}/releases/latest"\n'
        'system_python = "/usr/bin/python3"\n'
        'journal_identifier = "pyntara-engine"\n'
        "error_priority = 3\n"
        "progress_priority = 7\n"
        "process_check_timeout_seconds = 5\n"
        "task_start_delay_seconds = 0.5\n"
        'desktop_detect_processes = ["kwin_wayland", "plasmashell"]\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"

        '[imagemagick_setup]\npackages = ["imagemagick"]\n'
        'policy_path = "/etc/ImageMagick-7/policy.xml"\n'
        "package_status_timeout_seconds = 30\npackage_install_retries = 3\n"

        '[ffmpeg_setup]\npackages = ["ffmpeg"]\n'
        'wayrecord_bin_path = "/usr/local/bin/pyntara-wayrecord"\n'
        'wayrecord_desktop_path = "/usr/share/applications/pyntara-wayrecord.desktop"\nwayrecord_file_mode = "0755"\n'
        "package_status_timeout_seconds = 30\npackage_install_retries = 3\n"

        '[add_extra_repos]\ncomponents = ["universe"]\n'
        'ubuntu_hosts = ["archive.ubuntu.com"]\nkeep_downloaded_debs = true\n'
        'legacy_sources_file = "/etc/apt/sources.list"\n'
        'sources_list_d = "/etc/apt/sources.list.d"\n'
        'keep_debs_file = "/etc/apt/apt.conf.d/99keep-debs.conf"\n'
        '[hostname]\nhostname_file = "/etc/hostname"\n'
        'set_hostname_command = ["hostnamectl", "set-hostname"]\n'
        '[kde_keyboard_setup]\n'
        'packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]\n'
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'config_dir = "/home/i/.config"\n'
        'kxkbrc_file_name = "kxkbrc"\n'
        'appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"\n'
        'applet_plugin = "org.kde.plasma.keyboardlayout"\n'
        'layouts = ["us", "ru", "es"]\n'
        'switch_option = "grp:caps_select"\n'
        "reset_old_options = true\n"
        'switch_mode = "WinClass"\n'
        "use_layout_switching = true\n"
        'indicator_display_style = "Flag"\n'
        'kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]\n'
        'panel_restart_command = ["systemctl", "--user", "--machine", "i@.host", "restart", "plasma-plasmashell.service"]\n'
        'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }\n'
        'kxkbrc_group = ["Layout"]\n'
        'applet_configuration_group = ["Configuration", "General"]\n'
        'shortcuts_file_name = "kglobalshortcutsrc"\n'
        'kxkbrc_key_layout_list = "LayoutList"\n'
        'kxkbrc_key_display_names = "DisplayNames"\n'
        'kxkbrc_key_variant_list = "VariantList"\n'
        'kxkbrc_key_options = "Options"\n'
        'kxkbrc_key_reset_old_options = "ResetOldOptions"\n'
        'kxkbrc_key_switch_mode = "SwitchMode"\n'
        'kxkbrc_key_use = "Use"\n'
        'display_style_key = "displayStyle"\n'
        'kconfig_true_value = "true"\n'
        'kconfig_false_value = "false"\n'
        'layout_switcher_component_unique = "KDE Keyboard Layout Switcher"\n'
        'layout_switcher_component_friendly = "Keyboard Layout Switcher"\n'
        'shortcut_modifier_bits = { Ctrl = 0x04000000, Alt = 0x08000000, Shift = 0x02000000, Meta = 0x10000000 }\n'
        '[kde_settings]\n'
        'packages = ["plasma-workspace", "libkf6config-bin"]\n'
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'user_config_dir = ".config"\n'
        'user_kwin_scripts_dir = ".local/share/kwin/scripts"\n'
        'user_look_and_feel_dir = ".local/share/plasma/look-and-feel"\n'
        'user_places_file = ".local/share/user-places.xbel"\n'
        'script_file_mode = "0644"\n'
        'default_file_mode = "0600"\n'
        'user_dirs_file = "user-dirs.dirs"\n'
        'places_namespaces = { bookmark = "http://freedesktop.org/standards/desktop-bookmarks", kdepriv = "http://www.kde.org/kdepriv", mime = "http://freedesktop.org/standards/shared-mime-info" }\n'
        'places_metadata_owner = "http://www.kde.org"\n'
        'kdeglobals_file_name = "kdeglobals"\n'
        'kcminputrc_file_name = "kcminputrc"\n'
        'kwinrc_file_name = "kwinrc"\n'
        'plasma_keyboard_file_name = "plasmakeyboardrc"\n'
        'global_shortcuts_file_name = "kglobalshortcutsrc"\n'
        'general_group = ["General"]\n'
        'kde_group = ["KDE"]\n'
        'mouse_group = ["Mouse"]\n'
        'keyboard_group = ["Keyboard"]\n'
        'wayland_group = ["Wayland"]\n'
        'virtual_keyboard_group = ["General"]\n'
        'plugins_group = ["Plugins"]\n'
        'desktops_group = ["Desktops"]\n'
        'look_and_feel_package_key = "LookAndFeelPackage"\n'
        'color_scheme_key = "ColorScheme"\n'
        'automatic_look_and_feel_key = "AutomaticLookAndFeel"\n'
        'automatic_look_and_feel_idle_interval_key = "AutomaticLookAndFeelIdleInterval"\n'
        'numlock_key = "NumLock"\n'
        'input_method_key = "InputMethod"\n'
        'input_method_locales_key = "enabledLocales"\n'
        'cursor_theme_key = "cursorTheme"\n'
        'click_method_key = "ClickMethod"\n'
        'touchpad_disable_external_mouse_key = "DisableEventsOnExternalMouse"\n'
        'desktop_count_key = "Number"\n'
        'kconfig_true_value = "true"\n'
        'kconfig_false_value = "false"\n'
        'numlock_values = { on = "0", off = "1", unchanged = "2" }\n'
        'click_method_values = { clickfinger = "1", clickareas = "2", none = "0" }\n'
        'automatic_theme_switch_idle_interval = "99"\n'
        'places_root_tag = "xbel"\n'
        'places_bookmark_tag = "bookmark"\n'
        'places_title_tag = "title"\n'
        'places_metadata_path = "info/metadata"\n'
        'places_metadata_owner_attribute = "owner"\n'
        'places_hidden_element = "IsHidden"\n'
        'places_hidden_value = "true"\n'
        'kwin_scripts = ["window-grow-shrink", "window-restore-tracker"]\n'
        'kwin_script_files = ["metadata.json", "contents/code/main.js"]\n'
        'kwin_script_hotkeys = ["Meta+Ctrl+Up", "Meta+Ctrl+Down"]\n'
        'kwin_script_actions = ["Grow Window by 5px", "Shrink Window by 5px"]\n'
        'konsole_profile_path = ".local/share/konsole/Pyntara.profile"\n'
        'system_look_and_feel_dir = "/usr/share/plasma/look-and-feel"\n'
        'theme_defaults_dir = "contents/defaults"\n'
        'user_dirs = { "XDG_DOCUMENTS_DIR" = "$HOME/Downloads", "XDG_MUSIC_DIR" = "$HOME/Downloads", "XDG_PICTURES_DIR" = "$HOME/Downloads", "XDG_PUBLICSHARE_DIR" = "$HOME/Downloads", "XDG_TEMPLATES_DIR" = "$HOME/Downloads", "XDG_VIDEOS_DIR" = "$HOME/Downloads" }\n'
        'color_scheme = "BreezeDark"\n'
        'look_and_feel = "org.kubuntudark.desktop"\n'
        'look_and_feel_light = "org.kubuntulight.desktop"\n'
        "automatic_look_and_feel = true\n"
        'cursor_theme = "Oxygen_Yellow"\n'
        'cursor_theme_light = "Oxygen_Blue"\n'
        'numlock_on_boot = "off"\n'
        'touchpad_click_method = "clickfinger"\n'
        "touchpad_disable_on_external_mouse = false\n"
        "virtual_keyboard_enabled = true\n"
        'virtual_keyboard_input_method = "/usr/share/applications/org.kde.plasma.keyboard.desktop"\n'
        'virtual_keyboard_locales = ["en_US", "es_MX", "ru_RU"]\n'
        'sddm_conf_file = "/etc/sddm.conf"\n'
        'sddm_theme_conf_file = "/etc/sddm.conf.d/20-kubuntu.conf"\n'
        'kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]\n'
        'sddm_autologin_user = "i"\n'
        'sddm_autologin_session = "plasma"\n'
        'sddm_theme = "kubuntu"\n'
        "sddm_theme_cursor_size = \"30\"\n"
        'sddm_theme_cursor_theme = "breeze_cursors"\n'
        'sddm_theme_font = "Noto Sans,20"\n'
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
        'hot_add_readable_mode_bit = "0400"\n'
        'service_unit_name = "zram.service"\n'
        '[i2pd_service_setup]\n'
        'github_repo = "PurpleI2P/i2pd"\n'
        'download_dir = "/var/lib/pyntara/i2pd-download"\n'
        'os_release_file_path = "/etc/os-release"\n'
        'service_unit_name = "i2pd.service"\n'
        'config_path = "/etc/i2pd/i2pd.conf"\n'
        'log_level = "warn"\n'
        "bandwidth = 12500\n"
        "share = 1\n"
        "http_enabled = false\n"
        "socks_proxy_enabled = true\n"
        "socks_proxy_port = 4447\n"
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
        "address_save_retry_base_seconds = 1\n"
        "address_save_retry_multiplier = 2\n"
        "address_save_retry_max_seconds = 67\n"
        "connection_wait_base_seconds = 1\n"
        "connection_wait_multiplier = 2\n"
        "connection_wait_max_seconds = 30\n"
        'nm_unmanaged_conf_path = "/etc/NetworkManager/conf.d/yggdrasil-unmanaged.conf"\n'
        'nm_unmanaged_conf_file_mode = "0644"\n'
        'netplan_dir_path = "/etc/netplan"\n'
        "[[yggdrasil_service_setup.multicast_interfaces]]\n"
        'regex = ".*"\n'
        "beacon = true\n"
        "listen = true\n"
        "[three_x_ui_xray_setup]\n"
        'github_repo = "MHSanaei/3x-ui"\n'
        'install_script_url = "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh"\n'
        'install_dir = "/usr/local/x-ui"\n'
        'service_unit_name = "x-ui.service"\n'
        "start_check_attempts = 10\n"
        "start_check_retry_delay_seconds = 1\n"
        'install_result_env_path = "/etc/x-ui/install-result.env"\n'
        "panel_port = 35353\n"
        "ssl_enabled = true\n"
        'panel_http_address = "127.0.0.1"\n'
        'panel_root_path = "/"\n'
        'panel_login_path = "/login"\n'
        'panel_csrf_token_path = "/csrf-token"\n'
        'panel_inbounds_list_path = "/panel/api/inbounds/list"\n'
        'panel_inbounds_add_path = "/panel/api/inbounds/add"\n'
        'panel_inbounds_update_path = "/panel/api/inbounds/update/{inbound_id}"\n'
        'panel_inbounds_delete_path = "/panel/api/inbounds/del/{inbound_id}"\n'
        'panel_client_get_path = "/panel/api/clients/get/{email}"\n'
        'panel_client_add_path = "/panel/api/clients/add"\n'
        'panel_client_links_path = "/panel/api/clients/links/{email}"\n'
        'panel_x25519_cert_path = "/panel/api/server/getNewX25519Cert"\n'
        'panel_setting_all_path = "/panel/api/setting/all"\n'
        'panel_setting_update_path = "/panel/api/setting/update"\n'
        'panel_xray_status_path = "/panel/api/xray/"\n'
        'panel_xray_update_path = "/panel/api/xray/update"\n'
        'panel_xray_geodata_validate_path = "/panel/api/xray/geodata/validate"\n'
        'panel_xray_route_test_path = "/panel/api/xray/routeTest"\n'
        'vault_entry_title = "three_x_ui_credentials"\n'
        'connection_vault_entry_title = "xray_connection"\n'
        'share_addr_strategy = "custom"\n'
        'inbound_port = 443\n'
        'inbound_remark = "universal"\n'
        'reality_dest = "www.google.com:443"\n'
        'reality_server_names = ["www.google.com"]\n'
        'reality_short_id = "6ba85179e30d4fc2"\n'
        'reality_fingerprint = "chrome"\n'
        'subscription_path = "/s/"\n'
        'subscription_json_path = "/j/"\n'
        'subscription_clash_path = "/c/"\n'
        "acme_port = 80\n"
        'cert_dir = "/root/cert/ip"\n'
        'self_signed_cert_dir = "/root/cert/selfsigned"\n'
        'cert_privkey_file_mode = "0600"\n'
        'cert_fullchain_file_mode = "0644"\n'
        "server_ip_timeout_seconds = 60\n"
        'server_ip_services = ["https://api4.ipify.org", "https://ipv4.icanhazip.com", "https://v4.api.ipinfo.io/ip", "https://ipv4.myexternalip.com/raw", "https://4.ident.me", "https://check-host.net/ip"]\n'
        "probe_timeout_seconds = 60\n"
        "probe_port_80_timeout_seconds = 10\n"
        "probe_listener_start_seconds = 1\n"
        "upnp_enabled = true\n"
        'upnp_package = "miniupnpc"\n'
        'upnp_client_command = "upnpc"\n'
        'upnp_mapping_description = "pyntara xray"\n'
        'client_profile_entry_title = "xray_client_profile"\n'
        'local_proxy_tag = "pyntara-local-proxy"\n'
        'local_proxy_listen_address = "127.0.0.1"\n'
        "local_proxy_port = 10800\n"
        "local_proxy_udp = true\n"
        'local_proxy_sniffing_protocols = ["http", "tls", "quic"]\n'
        'remote_outbound_tag = "pyntara-remote"\n'
        'tor_outbound_tag = "pyntara-tor"\n'
        'i2p_outbound_tag = "pyntara-i2p"\n'
        'direct_outbound_tag = "direct"\n'
        'blocked_outbound_tag = "blocked"\n'
        'tor_proxy_address = "127.0.0.1:9050"\n'
        'i2p_proxy_address = "127.0.0.1:4444"\n'
        'ad_block_domain_categories = ["geosite:category-ads-all"]\n'
        'direct_domains = ["domain:localhost", "domain:.local", "domain:.home.arpa", "domain:.lan", "domain:.internal"]\n'
        'direct_ip_categories = ["geoip:private"]\n'
        'direct_ip_networks = ["200::/7", "300::/7"]\n'
        'country_services = ["https://ip2c.org/self", "https://ifconfig.co/json", "https://ipwho.is/"]\n'
        'country_word = "russia"\n'
        "country_query_timeout_seconds = 10\n"
        "country_command_timeout_seconds = 20\n"
        'russia_blocked_domain_categories = ["ext-site:geosite_RU.dat:ru-blocked-all"]\n'
        'russia_blocked_ip_categories = ["ext-ip:geoip_RU.dat:ru-blocked", "ext-ip:geoip_RU.dat:ru-blocked-community"]\n'
        'russia_direct_domain_categories = ["ext-site:geosite_RU.dat:ru-available-only-inside"]\n'
        'russia_direct_ip_categories = ["ext-ip:geoip_RU.dat:ru-whitelist"]\n'
        'geo_restricted_domain_categories = ["geosite:category-ai-!cn", "geosite:openai", "geosite:xai", "geosite:netflix", "geosite:spotify", "geosite:category-social-media-!cn"]\n'
        'russia_domain_strategy = "IPIfNonMatch"\n'
        'outside_russia_domain_strategy = "AsIs"\n'
        'route_check_ad_domain = "doubleclick.net"\n'
        'route_check_foreign_domain = "example.com"\n'
        'route_check_onion_domain = "pyntara-check.onion"\n'
        'route_check_i2p_domain = "pyntara-check.i2p"\n'
        'route_check_direct_domain = "localhost"\n'
        'route_check_russia_blocked_domain = "instagram.com"\n'
        'proxy_check_url = "https://api4.ipify.org"\n'
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
        'augeas_tools_package_name = "augeas-tools"\n'
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
        'port_forwarding_private_key_file_name = "id_ed25519_pf"\n'
        'port_forwarding_public_key_file_name = "id_ed25519_pf.pub"\n'
        'port_forwarding_authorized_keys_options = \'restrict,port-forwarding,permitlisten="*"\'\n'
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
        'augeas_tools_package_name = "augeas-tools"\n'
        "package_status_timeout_seconds = 30\n"
        "install_retries = 3\n"
        "[nextdns_setup_system_wide]\n"
        'vault_group_title = "NextDNS"\n'
        'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\n'
        'profile_id_file_mode = "0644"\n'
        "error_priority = 3\n"
        "[port_forwarding_setup]\n"
        'vault_group_title = "port_forwarding_servers"\n'
        'passphrase_entry_title = "ssh_passphase_for_port_forwarding"\n'
        'remote_ssh_user = "i"\n'
        "desired_port_min = 32768\n"
        "desired_port_max = 60999\n"
        "server_alive_interval_seconds = 61\n"
        "server_alive_count_max = 3\n"
        "connect_timeout_seconds = 31\n"
        "own_addresses_timeout_seconds = 15\n"
        "agent_start_timeout_seconds = 15\n"
        "key_unlock_timeout_seconds = 30\n"
        'askpass_helper_file_mode = "0700"\n'
        'state_file_mode = "0600"\n'
        "backoff_base_seconds = 2\n"
        "backoff_multiplier = 2\n"
        "backoff_max_seconds = 1024\n"
        'state_file_path = "/var/lib/pyntara/port_forwarding_state.json"\n'
        'service_unit_name = "auto_port_forwarding.service"\n'
        "service_restart_seconds = 30\n"
        'journal_identifier = "auto_port_forwarding"\n'
        "error_priority = 3\n"
        "[dnsproxy_setup]\n"
        'github_repo = "AdguardTeam/dnsproxy"\n'
        'download_dir = "/tmp/dnsproxy"\n'
        'binary_path = "/usr/local/bin/dnsproxy"\n'
        'service_unit_name = "dnsproxy.service"\n'
        'service_unit_path = "/etc/systemd/system/dnsproxy.service"\n'
        'service_template_path = "task_data/dnsproxy_setup/dnsproxy.service"\n'
        'listen_addresses = ["0.0.0.0", "::"]\nlisten_port = 53053\n'
        'doh_url_format = "https://dns.nextdns.io/{profile_id}"\n'
        'dot_host_format = "tls://{profile_id}.dns.nextdns.io"\n'
        'doq_host_format = "quic://{profile_id}.dns.nextdns.io"\n'
        'upstream_mode = "load_balance"\ncache_enabled = true\n'
        "cache_size_bytes = 16777216\n"
        'bootstrap_resolvers = ["1.1.1.1", "2606:4700:4700::1111"]\n'
        'append_provider_dns = true\n'
        "timeout_seconds = 55\nlog_rate_limit_interval_seconds = 3777\nlog_rate_limit_burst = 7777\n"
        'service_restart_seconds = 2.0\ninstall_retries = 3\n'
        'start_check_attempts = 5\nstart_check_retry_delay_seconds = 1.0\n'
        'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
        'resolved_dropin_file_name = "pyntara-dnsproxy.conf"\nresolved_dropin_file_mode = "0644"\n'
        'staged_binary_file_mode = "0755"\n'
        'resolved_dropin_header = "# Managed by the Pyntara dnsproxy_setup task."\n'
        'resolved_section = "[Resolve]"\nresolved_dns_directives = ["DNS=127.0.0.1:53053", "DNS=[::1]:53053"]\n'
        'resolved_domains_directive = "Domains=~."\nmanage_networkmanager = true\n'
        'nmcli_check_command = ["nmcli", "--version"]\n'
        'nmcli_device_status_command = ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]\n'
        'nmcli_active_list_command = ["nmcli", "-t", "-f", "NAME,UUID,DEVICE", "connection", "show", "--active"]\n'
        'nmcli_dns_state_command = ["nmcli", "-t", "-f", "ipv4.ignore-auto-dns,ipv6.ignore-auto-dns", "connection", "show", "{connection}"]\n'
        'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]\n'
        'nmcli_reapply_command = ["nmcli", "device", "reapply", "{device}"]\n'
        'daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]\n'
        'resolvectl_status_command = ["resolvectl", "status"]\n'
        'resolvectl_dns_command = ["resolvectl", "dns"]\n'
        'nmcli_dns_command = ["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"]\n'
        'verification_domain = "example.com"\n'
        'verification_command = ["resolvectl", "query", "--cache=no", "{domain}"]\n'
        'verification_error_excerpt_length = 200\n'
        'ss_tcp_listen_command = ["ss", "-lntp"]\n'
        'ss_udp_listen_command = ["ss", "-lunp"]\n'
        'kill_command = ["kill"]\n'
        'service_log_command = ["journalctl", "-u", "{unit}", "--no-pager", "-n", "20"]\n'
        'service_log_excerpt_length = 400\n'
        'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\nprofile_id_file_mode = "0644"\n'
        "[rustdesk_setup]\n"
        'github_repo = "rustdesk/rustdesk"\n'
        'download_dir = "/var/cache/pyntara/rustdesk"\n'
        'id_file_path = "/var/lib/pyntara/rustdesk_id"\n'
        'id_file_mode = "0644"\n'
        'vault_entry_title = "rustdesk_password"\n'
        'service_unit_name = "rustdesk.service"\n'
        "password_words = 6\n"
        'password_separator = " "\n'
        'config_dir = "/home/i/.config/rustdesk"\n'
        "install_timeout_seconds = 600\n"
        "apt_update_timeout_seconds = 600\n"
        "install_retries = 2\n"
        "start_check_attempts = 10\n"
        "start_check_retry_delay_seconds = 1.0\n"
        '[[rustdesk_setup.options]]\n'
        'key = "enable-udp-punch"\nvalue = "Y"\n'
        "[telegram_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'download_dir = "/var/cache/pyntara/telegram"\n'
        'latest_url = "https://telegram.org/dl/desktop/linux"\n'
        'icon_url = "https://example.invalid/telegram/icon512.png"\nlauncher_file_mode = "0644"\nicon_file_mode = "0644"\nexecutable_file_mode = "0755"\n'
        "[chrome_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n'
        'settings_repo_ref = "main"\n'
        'settings_dir = "/var/cache/pyntara/chromium-settings"\n'
        'system_root = "/"\n'
        'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n'
        'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n'
        'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n'
        'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n'
        'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n'
        'profile_mirror_path = "/home/i/.config/google-chrome-cdp"\n'
        'mount_service_unit_name = "mount_chrome_user_dir.service"\n'
        "cdp_port = 19222\n"
        'cdp_address = "127.0.0.1"\nfile_mode = "0644"\n'
        "[playwright_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'packages = ["nodejs", "npm"]\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
        'cli_package = "@playwright/cli"\n'
        "npm_install_timeout_seconds = 900\n"
        "[vocalinux_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'download_dir = "/var/cache/pyntara/vocalinux"\n'
        'version = "0.16.2"\n'
        'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n'
        'input_group = "input"\n'
        'service_unit_name = "ydotool.service"\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
        'user_file_mode = "0644"\nexecutable_file_mode = "0755"\n'
        "[system_metrics_setup]\n"
        "backoff_base_seconds = 2\nbackoff_multiplier = 2\n"
        "backoff_max_seconds = 14400\n"
        'python_version = "3"\nerror_priority = 3\n'
        'venv_dir = "/usr/local/lib/pyntara/venv"\n'
        'system_config_path = "/etc/pyntara/config.toml"\n'
        'command_path = "/usr/local/bin/commit_system_metrics"\n'
        'vault_backup_file_name = "{hostname}.kdbx"\n'
        'vault_backup_file_mode = "0600"\n'
        'system_metrics_dir = "/var/lib/pyntara/metrics"\n'
        'system_metrics_dir_mode = "0700"\nqueue_file_mode = "0600"\n'
        'max_queue_file_size_bytes = 104857600\nsend_order = "oldest_first"\n'
        'queue_file_suffix_length = 12\n'
        'spool_dir = "/var/spool/system_metrics"\nspool_dir_mode = "1733"\n'
        'spool_dir_permission_mask = "7777"\n'
        'command_file_mode = "0755"\n'
        'command_permission_mask = "0777"\n'
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
        'report_file_mode = "0600"\n'
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
        '[[vault_structure.entries]]\ntitle = "three_x_ui_credentials"\n'
        'notes = "3x-ui panel credentials."\n'
        '[[vault_structure.entries]]\ntitle = "xray_connection"\n'
        'notes = "Xray server connection profile."\n'
        '[[vault_structure.entries]]\ntitle = "ssh_passphase_for_port_forwarding"\n'
        'generated_password = "proquint-7"\nnotes = "Port forwarding key passphrase."\n'
        '[[vault_structure.entries]]\ntitle = "rustdesk_password"\n'
        'notes = "RustDesk access password."\n'
        '[local_vault_setup]\nsource_vault_production = "secrets/production.vault"\n'
        'source_vault_default = "secrets/default.vault"\n'
        'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"\n'
        'pass_file_path = "/etc/pyntara/pass"\n'
        'vault_password_entry_title = "pyntara_local_vault_password"\n'
        'secrets_dir_mode = "0700"\nlocal_vault_file_mode = "0640"\n'
        'pass_dir_mode = "0700"\npass_file_mode = "0400"\npass_file_writable_mode = "0600"\nerror_priority = 3\n'
        '[[tasks]]\nname = "users"\ndescription = "Create users."\n'
        "depends = []\nmodes = [\"minimal\"]\n"
    )


def write_config(tmp_path: Path, content: str) -> Path:
    """Write content as config.toml in tmp_path and return its path."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def load_checked_config(path: Path) -> Config:
    """Read the config through the strict checks of the test suite.

    The runtime reader never fails, so a test that asserts a config rule
    asks the checks instead: they are the same conditions that used to run
    inside the package (tests/config_checks.py).
    """

    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        document = tomllib.loads(render_config_source(path))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return strict_config_from_document(document)


def assert_config_error(
    tmp_path: Path, content: str, match: str | None = None
) -> None:
    """Write content as config.toml and expect load_config to raise.

    match narrows the assertion to a ConfigError message fragment; without
    it any ConfigError passes.
    """

    if match is None:
        with pytest.raises(ConfigError):
            load_checked_config(write_config(tmp_path, content))
    else:
        with pytest.raises(ConfigError, match=match):
            load_checked_config(write_config(tmp_path, content))
