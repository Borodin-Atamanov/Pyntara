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
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
        "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
        '[imagemagick_setup]\npackages = ["imagemagick"]\n'
        'policy_path = "/etc/ImageMagick-7/policy.xml"\n'
        'policy_template_file_name = "policy.xml"\n'
        'policy_backup_file_suffix = ".bak"\n'
        "package_status_timeout_seconds = 30\npackage_install_retries = 3\n"
        '[ffmpeg_setup]\npackages = ["ffmpeg"]\n'
        'wayrecord_bin_path = "/usr/local/bin/pyntara-wayrecord"\n'
        'wayrecord_desktop_path = "/usr/share/applications/pyntara-wayrecord.desktop"\nwayrecord_file_mode = "0755"\n'
        'wayrecord_source_file_names = ["wayrecord.c", "zkde-screencast-client.c"]\n'
        'wayrecord_desktop_template_file_name = "pyntara-wayrecord.desktop"\n'
        'wayrecord_build_file_suffix = ".build"\n'
        'wayrecord_build_flags_command = ["pkg-config", "--cflags", "--libs", "wayland-client", "libpipewire-0.3"]\n'
        'wayrecord_compile_command = ["gcc", "-O2", "-o", "{output}"]\n'
        "package_status_timeout_seconds = 30\npackage_install_retries = 3\n"
        '[add_extra_repos]\ncomponents = ["universe"]\n'
        'ubuntu_hosts = ["archive.ubuntu.com"]\nkeep_downloaded_debs = true\n'
        'uris_field_name = "uris:"\ncomponents_field_name = "components:"\n'
        'legacy_sources_file = "/etc/apt/sources.list"\n'
        'sources_list_d = "/etc/apt/sources.list.d"\n'
        'legacy_source_suffix = ".list"\n'
        'legacy_source_type_keywords = ["deb ", "deb-src "]\n'
        'source_url_schemes = ["http://", "https://"]\n'
        'deb822_source_suffix = ".sources"\n'
        'keep_debs_file = "/etc/apt/apt.conf.d/99keep-debs.conf"\n'
        'keep_debs_dropin_content = "# Written by pyntara add_extra_repos\\n# Keep downloaded .deb files after install for offline reinstall.\\nAPT::Keep-Downloaded-Packages \\"true\\";\\nUnattended-Upgrade::Keep-Debs-After-Install \\"true\\";\\n"\n'
        '[hostname]\nhostname_file = "/etc/hostname"\n'
        "hostname_random_bytes = 4\n"
        'set_hostname_command = ["hostnamectl", "set-hostname"]\n'
        "[kde_keyboard_setup]\n"
        'packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus", "python3-pyqt6"]\n'
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
        "shortcut_modifier_bits = { Ctrl = 0x04000000, Alt = 0x08000000, Shift = 0x02000000, Meta = 0x10000000 }\n"
        'apply_hotkeys_script_file_name = "apply_hotkeys.py"\n'
        'runuser_command = ["runuser", "-u", "{username}", "--"]\n'
        'kreadconfig_command = ["kreadconfig6", "--file", "{file_name}"]\n'
        'kwriteconfig_command = ["kwriteconfig6", "--file", "{file_name}"]\n'
        'config_group_flag = ["--group", "{group}"]\n'
        'config_key_flag = ["--key", "{key}"]\n'
        'config_bool_type_flag = ["--type", "bool"]\n'
        'mkdir_command = ["mkdir", "-p", "{path}"]\n'
        'python_script_command = ["{python}", "-c"]\n'
        '[swapfile_service_install]\nswapfile_path = "/swapfile"\n'
        'meminfo_total_key = "MemTotal:"\n'
        "ram_multiplier = 2\nram_extra_mb = 4096\ndisk_fraction = 0.5\n"
        'swapfile_mode = "0600"\nsize_tolerance_mb = 1\n'
        'service_unit_name = "swapfile.service"\n'
        'unit_template_file_name = "swapfile.service"\n'
        'swap_show_command = ["swapon", "--show", "--noheadings"]\n'
        'swap_on_command = ["swapon", "{swapfile_path}"]\n'
        'swap_off_command = ["swapoff", "{swapfile_path}"]\n'
        'create_command = ["fallocate", "-l", "{size_mb}M", "{swapfile_path}"]\n'
        'chmod_command = ["chmod", "{file_mode}", "{swapfile_path}"]\n'
        'format_command = ["mkswap", "{swapfile_path}"]\n'
        'systemctl_daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'systemctl_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        '[zswap_service]\nenabled = true\ncompressor = "zstd"\n'
        "max_pool_percent = 50\naccept_threshold_percent = 100\n"
        "shrinker_enabled = true\n"
        'parameters_dir_path = "/sys/module/zswap/parameters"\n'
        'parameter_names = ["enabled", "compressor", "max_pool_percent", "accept_threshold_percent", "shrinker_enabled"]\n'
        'unit_template_file_name = "zswap.service"\n'
        "unit_exec_line_template = \"ExecStart=/bin/sh -c 'echo {value} > "
        "{path}'\"\n"
        'service_unit_name = "zswap.service"\n'
        'systemctl_daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'systemctl_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        '[zram_service]\ncompressor = "zstd"\nswap_priority = 1111\n'
        "memory_fraction_percent = 96\nfallback_cpu_count = 8\n"
        'meminfo_total_key = "MemTotal:"\n'
        'cpuinfo_processor_key = "processor"\n'
        "alignment_bytes = 4096\nreset_busy_attempts = 5\n"
        "reset_busy_retry_delay_seconds = 0.5\n"
        'hot_add_readable_mode_bit = "0400"\n'
        'service_unit_name = "zram.service"\n'
        'module_name = "zram"\n'
        'unit_template_file_name = "zram.service"\n'
        'swap_show_command = ["swapon", "--show", "--noheadings"]\n'
        'module_load_command = ["modprobe", "{module_name}"]\n'
        'swap_off_command = ["swapoff", "{device_path}"]\n'
        'format_command = ["mkswap", "{device_path}"]\n'
        'swap_on_command = ["swapon", "--priority", "{swap_priority}", "{device_path}"]\n'
        'systemctl_daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'systemctl_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        "unit_load_line = \"ExecStart=/bin/sh -c 'modprobe {module_name} || true'\"\n"
        'unit_add_read_line = "ExecStart=/bin/cat {hot_add_path}"\n'
        "unit_add_write_line = \"ExecStart=/bin/sh -c 'echo 1 > {hot_add_path}'\"\n"
        "unit_algorithm_line = \"ExecStart=/bin/sh -c 'echo {compressor} > {algorithm_attribute}'\"\n"
        "unit_disksize_line = \"ExecStart=/bin/sh -c 'echo {size_bytes} > {disksize_attribute}'\"\n"
        'unit_format_line = "ExecStart=/sbin/mkswap {device_path}"\n'
        'unit_swap_on_line = "ExecStart=/sbin/swapon --priority {swap_priority} {device_path}"\n'
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
        'report_channel_name = "yggdrasil"\n'
        'nm_unmanaged_conf_path = "/etc/NetworkManager/conf.d/yggdrasil-unmanaged.conf"\n'
        'nm_unmanaged_conf_file_mode = "0644"\n'
        'netplan_dir_path = "/etc/netplan"\n'
        'asset_name_template = "yggdrasil-{version}-{arch}.deb"\n'
        'release_tag_prefix = "v"\n'
        'installed_version_command = ["yggdrasil", "-version"]\n'
        'export_key_from_config_command = ["yggdrasil", "-useconffile", "{config_path}", "-exportkey"]\n'
        'generate_config_command = ["yggdrasil", "-genconf", "-json"]\n'
        'export_key_from_stdin_command = ["yggdrasil", "-useconf", "-exportkey"]\n'
        'peers_latency_command = ["yggdrasilctl", "-json", "getPeers"]\n'
        'self_address_command = ["yggdrasilctl", "-json", "getSelf"]\n'
        'journal_connected_query_command = ["journalctl", "-u", "{service_unit_name}", "--since", "-{probe_seconds}s", "--no-pager", "--output=short-iso"]\n'
        'service_start_command = ["systemctl", "start", "{service_unit_name}"]\n'
        'service_restart_command = ["systemctl", "restart", "{service_unit_name}"]\n'
        'service_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        'nmcli_reload_command = ["nmcli", "general", "reload"]\n'
        'nmcli_connection_show_command = ["nmcli", "connection", "show", "{connection_name}"]\n'
        'nmcli_connection_delete_command = ["nmcli", "connection", "delete", "{connection_name}"]\n'
        'ip_link_show_command = ["ip", "link", "show", "dev", "{interface_name}"]\n'
        'ip_link_delete_command = ["ip", "link", "del", "{interface_name}"]\n'
        'nm_unmanaged_conf_body = "[keyfile]\\nunmanaged-devices=interface-name:{interface_name}\\n"\n'
        "netplan_interface_marker = 'connection.interface-name: \"{interface_name}\"'\n"
        'netplan_file_suffix = ".yaml"\n'
        'netplan_backup_suffix = ".bak"\n'
        'peers_tarball_temp_prefix = "yggdrasil-peers-"\n'
        'peers_tarball_temp_suffix = ".tar.gz"\n'
        'peer_markdown_suffix = ".md"\n'
        'line_separator = "\\n"\n'
        "config_json_indent = 2\n"
        'config_document_keys = { private_key_path = "PrivateKeyPath", admin_listen = "AdminListen", if_name = "IfName", if_mtu = "IfMTU", listen = "Listen", multicast_interfaces = "MulticastInterfaces", multicast_regex = "Regex", multicast_beacon = "Beacon", multicast_listen = "Listen", peers = "Peers" }\n'
        'admin_output_keys = { address = "address", peers = "peers", remote = "remote", latency = "latency" }\n'
        "[[yggdrasil_service_setup.multicast_interfaces]]\n"
        'regex = ".*"\n'
        "beacon = true\n"
        "listen = true\n"
        "[three_x_ui_xray_setup]\n"
        'github_repo = "MHSanaei/3x-ui"\n'
        'install_script_url = "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh"\n'
        'install_dir = "/usr/local/x-ui"\n'
        'binary_file_name = "x-ui"\n'
        'service_process_name = "x-ui"\n'
        'panel_version_command = ["{binary}", "-v"]\n'
        'panel_settings_query_command = ["{binary}", "setting", "-show", "true"]\n'
        'panel_cert_query_command = ["{binary}", "setting", "-getCert", "true"]\n'
        'panel_port_command = ["{binary}", "setting", "-port", "{port}"]\n'
        'panel_credentials_command = ["{binary}", "setting", "-username", "{username}", "-password", "{password}", "-webBasePath", "{web_base_path}"]\n'
        'panel_certificate_command = ["{binary}", "cert", "-webCert", "{fullchain}", "-webCertKey", "{privkey}"]\n'
        'installer_run_command = ["bash", "{script_path}"]\n'
        'acme_install_command = ["bash", "-c", "curl -s https://get.acme.sh | sh"]\n'
        'acme_dir_relative_path = ".acme.sh"\n'
        'acme_file_name = "acme.sh"\n'
        'acme_port_listener_command = ["python3", "-m", "http.server", "{port}", "--bind", "0.0.0.0"]\n'
        'acme_set_default_ca_command = ["{acme}", "--set-default-ca", "--server", "letsencrypt", "--force"]\n'
        'acme_issue_command = ["{acme}", "--issue", "-d", "{domain}", "--standalone", "--server", "letsencrypt", "--certificate-profile", "shortlived", "--days", "6", "--httpport", "{http_port}", "--force"]\n'
        'acme_installcert_command = ["{acme}", "--installcert", "--force", "-d", "{domain}", "--key-file", "{key_file}", "--fullchain-file", "{fullchain_file}", "--reloadcmd", "{reload_command}"]\n'
        'acme_upgrade_command = ["{acme}", "--upgrade", "--auto-upgrade"]\n'
        'acme_reload_command = "systemctl restart {service_unit_name} 2>/dev/null || true"\n'
        'openssl_check_command = ["openssl", "x509", "-in", "{fullchain}", "-noout", "-checkend", "0"]\n'
        'openssl_generate_command = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-subj", "{subject}", "-keyout", "{key_file}", "-out", "{fullchain_file}"]\n'
        'openssl_subject_template = "/CN={subject}"\n'
        'service_restart_command = ["systemctl", "restart", "{service_unit_name}"]\n'
        'service_unit_name = "x-ui.service"\n'
        "readiness_check_delay_seconds = 1\n"
        "service_start_wait_seconds = 60\n"
        "panel_listener_wait_seconds = 60\n"
        "core_ready_wait_seconds = 120\n"
        'install_result_env_path = "/etc/x-ui/install-result.env"\n'
        "random_username_bytes = 4\n"
        "random_secret_bytes = 8\n"
        "random_sub_id_bytes = 6\n"
        'inbound_payload_template_file_name = "vless_reality_inbound.json"\n'
        "panel_port = 35353\n"
        "ssl_enabled = true\n"
        'panel_http_address = "127.0.0.1"\n'
        "panel_api_timeout_seconds = 120\n"
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
        'panel_outbound_subs_path = "/panel/api/xray/outbound-subs"\n'
        'panel_outbound_subs_item_path = "/panel/api/xray/outbound-subs/{subscription_id}"\n'
        'panel_outbound_subs_refresh_path = "/panel/api/xray/outbound-subs/{subscription_id}/refresh"\n'
        'panel_balancer_status_path = "/panel/api/xray/balancerStatus"\n'
        'panel_status_path = "/panel/api/server/status"\n'
        'panel_xray_result_path = "/panel/api/xray/getXrayResult"\n'
        'panel_status_keys = { xray = "xray", state = "state", error_msg = "errorMsg" }\n'
        'panel_inbound_protocol = "mixed"\n'
        'panel_blocked_rule_protocols = ["bittorrent"]\n'
        'panel_private_block_category = "geoip:private"\n'
        'panel_geodata_domain_kind = "domain"\n'
        'panel_geodata_ip_kind = "ip"\n'
        'inbound_sniffing_protocols = ["http", "tls"]\n'
        'panel_http_headers = { content_type = "Content-Type", csrf_token = "X-CSRF-Token", requested_with = "X-Requested-With", referer = "Referer", authorization = "Authorization" }\n'
        'panel_http_header_values = { json = "application/json", form = "application/x-www-form-urlencoded", xml_http_request = "XMLHttpRequest", bearer_prefix = "Bearer " }\n'
        'panel_http_methods = { post = "POST", get = "GET" }\n'
        'panel_url_schemes = { http = "http", https = "https" }\n'
        'panel_environment_keys = { username = "XUI_USERNAME", password = "XUI_PASSWORD", panel_port = "XUI_PANEL_PORT", web_base_path = "XUI_WEB_BASE_PATH", scheme = "XUI_SCHEME", api_token = "XUI_API_TOKEN", db_type = "XUI_DB_TYPE", access_url = "XUI_ACCESS_URL", noninteractive = "XUI_NONINTERACTIVE" }\n'
        'panel_answer_keys = { success = "success", payload = "obj", message = "msg", token = "token", reason = "reason" }\n'
        'panel_field_keys = { username = "username", password = "password", id = "id", email = "email", enable = "enable", sub_id = "subId", inbound_ids = "inboundIds", client = "client", tag = "tag", port = "port", protocol = "protocol", network = "network", inbound_tag = "inboundTag", outbound_tag = "outboundTag", matched = "matched", domain = "domain", ip = "ip", kind = "kind", tokens = "tokens", token = "token", private_key = "privateKey", public_key = "publicKey", sub_path = "subPath", sub_json_path = "subJsonPath", sub_clash_path = "subClashPath", xray_setting = "xraySetting", outbound_test_url = "outboundTestUrl", subscription_remark = "remark", subscription_url = "url", subscription_tag_prefix = "tagPrefix", subscription_enabled = "enabled", subscription_update_interval = "updateInterval", subscription_allow_private = "allowPrivate", subscription_allow_insecure = "allowInsecure", subscription_prepend = "prepend", subscription_outbound_count = "outboundCount", subscription_last_error = "lastError", balancer_status_query = "tags", balancer_running = "running", balancer_override = "override", balancer_selected = "selected" }\n'
        'xray_field_keys = { tag = "tag", protocol = "protocol", settings = "settings", stream_settings = "streamSettings", network = "network", security = "security", reality_settings = "realitySettings", server_name = "serverName", fingerprint = "fingerprint", public_key = "publicKey", short_id = "shortId", private_key = "privateKey", spider_x = "spiderX", vnext = "vnext", address = "address", port = "port", users = "users", id = "id", encryption = "encryption", flow = "flow", servers = "servers", remark = "remark", listen = "listen", enable = "enable", expiry_time = "expiryTime", total = "total", up = "up", down = "down", auth = "auth", udp = "udp", ip = "ip", sniffing = "sniffing", enabled = "enabled", dest_override = "destOverride", metadata_only = "metadataOnly", route_only = "routeOnly", type = "type", inbound_tag = "inboundTag", outbound_tag = "outboundTag", domain = "domain", outbounds = "outbounds", routing = "routing", rules = "rules", domain_strategy = "domainStrategy", final_rules = "finalRules", observatory = "observatory", balancers = "balancers", subject_selector = "subjectSelector", probe_url = "probeUrl", probe_interval = "probeInterval", enable_concurrency = "enableConcurrency", strategy = "strategy", selector = "selector", fallback_tag = "fallbackTag", balancer_tag = "balancerTag", share_addr = "shareAddr", share_addr_strategy = "shareAddrStrategy" }\n'
        'xray_values = { vless = "vless", reality = "reality", none = "none", tcp = "tcp", socks = "socks", http = "http", noauth = "noauth", field = "field", api_tag = "api", onion_domain = "domain:.onion", i2p_domain = "domain:.i2p", least_ping = "leastPing" }\n'
        'vless_link_query_keys = { security = "security", public_key = "pbk", fingerprint = "fp", short_id = "sid", server_name = "sni", spider_x = "spx", flow = "flow", network = "type" }\n'
        'vault_entry_title = "three_x_ui_credentials"\n'
        'connection_vault_entry_title = "xray_connection"\n'
        'share_addr_strategy = "custom"\n'
        "inbound_port = 443\n"
        "route_test_port = 443\n"
        'route_test_network = "tcp"\n'
        'route_test_protocol = "tls"\n'
        "remote_link_default_port = 443\n"
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
        'port_forward_probe_command = ["curl", "--silent", "--connect-timeout", "{timeout_seconds}", "--max-time", "{timeout_seconds}"]\n'
        'port_forward_probe_url_format = "http://{host}:{port}/"\n'
        'panel_probe_command = ["curl", "--silent", "--max-time", "{timeout_seconds}", "--insecure", "--output", "/dev/null", "--header", "X-Requested-With: XMLHttpRequest"]\n'
        'tunnel_probe_command = ["curl", "--silent", "--show-error", "--proxy", "{proxy_address}", "--connect-timeout", "{timeout_seconds}", "--max-time", "{timeout_seconds}", "--write-out", "{write_out}"]\n'
        'tunnel_probe_write_out = "\\n%{http_code}"\n'
        'tunnel_probe_no_answer_code = "000"\n'
        "upnp_enabled = true\n"
        'upnp_package = "miniupnpc"\n'
        'upnp_client_command = "upnpc"\n'
        'upnp_protocol = "TCP"\n'
        'upnp_mapping_description = "pyntara xray {hostname}"\n'
        'client_profile_entry_title = "xray_client_profile"\n'
        'local_proxy_tag = "pyntara-local-proxy"\n'
        'local_proxy_listen_address = "127.0.0.1"\n'
        'private_ipv4_networks = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]\n'
        "local_proxy_port = 10800\n"
        "local_proxy_udp = true\n"
        'local_proxy_sniffing_protocols = ["http", "tls", "quic"]\n'
        "client_enabled = true\n"
        "local_proxy_enabled = true\n"
        "local_proxy_sniffing_enabled = true\n"
        "local_proxy_sniffing_metadata_only = false\n"
        "local_proxy_sniffing_route_only = false\n"
        "local_proxy_traffic_limit_bytes = 0\n"
        "local_proxy_expiry_time = 0\n"
        'remote_outbound_tag = "pyntara-remote"\n'
        'tor_outbound_tag = "pyntara-tor"\n'
        'i2p_outbound_tag = "pyntara-i2p"\n'
        'pool_balancer_tag = "pyntara-fastest"\n'
        'pool_member_prefix = "sota-"\n'
        'pool_probe_url = "https://www.google.com/generate_204"\n'
        'pool_probe_interval = "30s"\n'
        "pool_enable_concurrency = true\n"
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
        'proxy_check_blocked_url = "https://api.openai.com/v1/models"\n'
        "proxy_check_timeout_seconds = 30\n"
        "proxy_check_attempts = 3\n"
        "proxy_check_command_timeout_seconds = 50\n"
        "[sotavpn_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'runuser_command = ["runuser", "-u", "{username}", "--", "env", "HOME={home_dir}"]\n'
        'archive_url = "https://codeload.github.com/Borodin-Atamanov/sotavpn-subscription-for-any-client/tar.gz/refs/heads/main"\n'
        'archive_temp_prefix = "sotavpn-bridge"\n'
        'archive_temp_suffix = ".tar.gz"\n'
        'installer_file_name = "install_sotavpn_bridge.py"\n'
        'installer_command = ["{python}", "{installer_path}", "install"]\n'
        'service_unit_name = "sotavpn-bridge.service"\n'
        'user_service_is_active_command = ["systemctl", "--machine", "{username}@.host", "--user", "is-active", "{unit}"]\n'
        'user_install_relative_path = ".local/share/sotavpn-bridge"\n'
        'settings_file_name = "settings.py"\n'
        'settings_http_port_key = "HTTP_PORT"\n'
        'key_entry_title = "sotavpn_uuid"\n'
        'subscription_url_template = "http://127.0.0.1:{port}/sub/{key}/raw"\n'
        'subscription_remark = "sota-bridge"\n'
        "subscription_update_interval_seconds = 300\n"
        "subscription_enabled = true\n"
        "subscription_allow_private = true\n"
        "subscription_allow_insecure = false\n"
        "subscription_prepend = false\n"
        "subscription_fetch_wait_seconds = 90\n"
        "bridge_ready_wait_seconds = 60\n"
        "readiness_check_delay_seconds = 2\n"
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
        'dropin_header = "Managed by the Pyntara ssh_daemon_setup task."\n'
        'dropin_comment_sign = "#"\n'
        'include_directive = "Include"\n'
        'augeas_lens = "Sshd.lns"\n'
        'port_directive = "Port"\n'
        'private_key_file_name = "id_ed25519"\n'
        'public_key_file_name = "id_ed25519.pub"\n'
        'port_forwarding_private_key_file_name = "id_ed25519_pf"\n'
        'port_forwarding_public_key_file_name = "id_ed25519_pf.pub"\n'
        "port_forwarding_authorized_keys_options = 'restrict,port-forwarding,permitlisten=\"*\"'\n"
        'private_key_file_mode = "0600"\n'
        'public_key_file_mode = "0644"\n'
        'authorized_keys_file_mode = "0600"\n'
        'ssh_dir_mode = "0700"\n'
        'root_ssh_dir = "/root/.ssh"\n'
        'users = ["i", "j", "k"]\n'
        'effective_config_command = ["sshd", "-T"]\n'
        'listening_sockets_command = ["ss", "-tlnp"]\n'
        'socket_disable_command = ["systemctl", "disable", "--now", "{socket_unit_name}"]\n'
        'service_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        'service_start_command = ["systemctl", "start", "{service_unit_name}"]\n'
        'service_restart_command = ["systemctl", "restart", "{service_unit_name}"]\n'
        'service_reload_command = ["systemctl", "reload", "{service_unit_name}"]\n'
        "[[ssh_daemon_setup.directives]]\n"
        'name = "PubkeyAuthentication"\n'
        'value = "yes"\n'
        "[ssh_client_setup]\n"
        'ssh_config_path = "/etc/ssh/ssh_config"\n'
        'ssh_config_dropin_path = "/etc/ssh/ssh_config.d/pyntara.conf"\n'
        'dropin_file_mode = "0644"\n'
        'dropin_header = "Managed by the Pyntara ssh_client_setup task."\n'
        'dropin_comment_sign = "#"\n'
        'include_directive = "Include"\n'
        'effective_config_command = ["ssh", "-G", "example.com"]\n'
        'augeas_lens = "Ssh.lns"\n'
        'augeas_container = "Host"\n'
        'augeas_container_value = "*"\n'
        'augeas_tools_package_name = "augeas-tools"\n'
        "package_status_timeout_seconds = 30\n"
        "install_retries = 3\n"
        "[nextdns_setup_system_wide]\n"
        'vault_group_title = "NextDNS"\n'
        'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\n'
        'profile_id_file_mode = "0644"\n'
        "error_priority = 3\n"
        "[dnsproxy_setup]\n"
        'github_repo = "AdguardTeam/dnsproxy"\n'
        'asset_name_template = "dnsproxy-linux-{asset_arch}-{release_tag}.tar.gz"\n'
        'asset_architecture_names = { amd64 = "amd64", arm64 = "arm64", armhf = "arm7" }\n'
        'binary_file_name = "dnsproxy"\n'
        'staged_binary_file_name = "dnsproxy.staged"\n'
        'extract_dir_name = "extract"\n'
        'download_dir = "/tmp/dnsproxy"\n'
        'binary_path = "/usr/local/bin/dnsproxy"\n'
        'service_unit_name = "dnsproxy.service"\n'
        'service_unit_path = "/etc/systemd/system/dnsproxy.service"\n'
        'service_template_path = "task_data/dnsproxy_setup/dnsproxy.service"\n'
        'listen_addresses = ["0.0.0.0", "::"]\nlisten_port = 53053\n'
        'doh_url_format = "https://dns.nextdns.io/{profile_id}"\n'
        'dot_host_format = "tls://{profile_id}.dns.nextdns.io"\n'
        'doq_host_format = "quic://{profile_id}.dns.nextdns.io"\n'
        "bootstrap_form_templates = [\n"
        '    "{host}",\n'
        '    "tls://{host}:853",\n'
        '    "https://{host}:443/dns-query",\n'
        '    "quic://{host}:853",\n'
        "]\n"
        'upstream_mode = "load_balance"\ncache_enabled = true\n'
        "cache_size_bytes = 16777216\n"
        'bootstrap_resolvers = ["1.1.1.1", "2606:4700:4700::1111"]\n'
        "append_provider_dns = true\n"
        "timeout_seconds = 55\nlog_rate_limit_interval_seconds = 3777\nlog_rate_limit_burst = 7777\n"
        "service_restart_seconds = 2.0\ninstall_retries = 3\n"
        "start_check_attempts = 5\nstart_check_retry_delay_seconds = 1.0\n"
        'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
        'resolved_dropin_file_name = "pyntara-dnsproxy.conf"\nresolved_dropin_file_mode = "0644"\n'
        'staged_binary_file_mode = "0755"\n'
        'resolved_dropin_header = "# Managed by the Pyntara dnsproxy_setup task."\n'
        'resolved_status_global_marker = "Global"\n'
        'resolved_status_link_prefix = "Link "\n'
        'resolved_status_dns_server_labels = ["Current DNS Server", "DNS Servers"]\n'
        'resolved_status_dns_domain_label = "DNS Domain"\n'
        'resolved_stub_mode_line = "resolv.conf mode: stub"\n'
        'resolved_wildcard_domain = "~."\n'
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
        'probe_address = "127.0.0.1"\nprobe_ident_bytes = 2\n'
        'tun_device_type = "tun"\nloopback_connection_name = "lo"\n'
        'nmcli_auto_dns_ignored_value = "yes"\n'
        'nmcli_ignore_auto_dns_value = "true"\n'
        'nmcli_restore_auto_dns_value = "false"\n'
        'service_stop_command = ["systemctl", "stop", "{service_unit_name}"]\n'
        'installed_version_command = ["{binary}", "--version"]\n'
        'daemon_flag_templates = { port = "--port={value}", '
        'listen = "--listen={value}", upstream = "--upstream={value}", '
        'fallback = "--fallback={value}", '
        'upstream_mode = "--upstream-mode={value}", '
        'timeout = "--timeout={value}s", cache = "--cache", '
        'cache_size = "--cache-size={value}", '
        'bootstrap = "--bootstrap={value}" }\n'
        'service_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        'service_start_command = ["systemctl", "start", "{service_unit_name}"]\n'
        'service_restart_command = ["systemctl", "restart", "{service_unit_name}"]\n'
        "verification_error_excerpt_length = 200\n"
        'ss_tcp_listen_command = ["ss", "-lntp"]\n'
        'ss_udp_listen_command = ["ss", "-lunp"]\n'
        'kill_command = ["kill"]\n'
        'service_log_command = ["journalctl", "-u", "{unit}", "--no-pager", "-n", "20"]\n'
        "service_log_excerpt_length = 400\n"
        'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\nprofile_id_file_mode = "0644"\n'
        "[rustdesk_setup]\n"
        'github_repo = "rustdesk/rustdesk"\n'
        'asset_name_template = "rustdesk-{version}-{asset_arch}.deb"\n'
        'version_check_command = ["rustdesk", "--version"]\n'
        'machine_id_command = ["rustdesk", "--get-id"]\n'
        'get_option_command = ["rustdesk", "--option", "{key}"]\n'
        'set_option_command = ["rustdesk", "--option", "{key}", "{value}"]\n'
        'set_password_command = ["rustdesk", "--password", "{password}"]\n'
        'service_stop_command = ["systemctl", "stop", "{service_unit_name}"]\n'
        'service_enable_command = ["systemctl", "enable", "{service_unit_name}"]\n'
        'service_start_command = ["systemctl", "start", "{service_unit_name}"]\n'
        'identity_file_name = "RustDesk.toml"\n'
        "readiness_probe_timeout_seconds = 5\n"
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
        "service_settle_delay_seconds = 3.0\n"
        "[[rustdesk_setup.options]]\n"
        'key = "stop-service"\nvalue = ""\n'
        "[[rustdesk_setup.options]]\n"
        'key = "enable-udp-punch"\nvalue = "Y"\n'
        "[scrcpy_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'github_repo = "Genymobile/scrcpy"\n'
        'archive_name_template = "scrcpy-linux-{asset_arch}-{release_tag}.tar.gz"\n'
        'checksum_file_name = "SHA256SUMS.txt"\n'
        'fallback_packages = ["scrcpy"]\n'
        'udev_rules_package_name = "android-udev-rules"\n'
        'apt_binary_path = "/usr/bin/scrcpy"\n'
        'theme_icon_name = "scrcpy"\n'
        'download_dir = "/var/cache/pyntara/scrcpy"\n'
        'install_dir_relative_path = ".local/share/scrcpy"\n'
        'command_relative_path = ".local/bin/scrcpy"\n'
        'launcher_relative_path = ".local/share/applications/scrcpy.desktop"\n'
        'console_launcher_relative_path = ".local/share/applications/scrcpy-console.desktop"\n'
        'launcher_template_file_name = "scrcpy.desktop"\n'
        'console_launcher_template_file_name = "scrcpy-console.desktop"\n'
        'binary_file_name = "scrcpy"\n'
        'server_file_name = "scrcpy-server"\n'
        'adb_file_name = "adb"\n'
        'icon_file_name = "scrcpy.png"\n'
        'extract_dir_prefix = "pyntara-scrcpy-"\n'
        'trash_dir_relative_path = ".local/share/Trash/files"\n'
        'version_command = ["{binary}", "--version"]\n'
        'checksum_command = ["sha256sum", "{file}"]\n'
        'archive_extract_command = ["tar", "--extract", "--gzip", "--file", '
        '"{archive}", "--directory", "{extract_dir}"]\n'
        'launcher_file_mode = "0644"\n'
        'executable_file_mode = "0755"\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
        "[telegram_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'download_dir = "/var/cache/pyntara/telegram"\n'
        'latest_url = "https://telegram.org/dl/desktop/linux"\n'
        'latest_url_command = ["curl", "--fail", "--head", "--write-out", "%{url_effective}"]\n'
        'reachability_probe_command = ["curl", "--head", "--connect-timeout", '
        '"{timeout_seconds}", "--max-time", "{timeout_seconds}"]\n'
        "reachability_probe_timeout_seconds = 15\n"
        'icon_url = "https://example.invalid/telegram/icon512.png"\n'
        'install_dir_relative_path = ".local/share/Telegram"\n'
        'launcher_relative_path = ".local/share/applications/telegramdesktop.desktop"\n'
        'icon_relative_path = ".local/share/icons/telegram-desktop.png"\n'
        'binary_file_name = "Telegram"\n'
        'updater_file_name = "Updater"\n'
        'archive_directory_name = "Telegram"\n'
        'extract_dir_prefix = "pyntara-telegram-"\n'
        'launcher_template_file_name = "telegramdesktop.desktop"\n'
        'tar_extract_command = ["tar", "--extract", "--file", "{archive}", '
        '"--directory", "{extract_dir}"]\n'
        'launcher_file_mode = "0644"\nicon_file_mode = "0644"\nexecutable_file_mode = "0755"\n'
        "[chrome_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'package_name = "google-chrome-stable"\n'
        'process_name = "chrome"\n'
        'appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"\n'
        'appletsrc_relative_path = ".config/plasma-org.kde.plasma.desktop-appletsrc"\n'
        'appletsrc_launchers_key = "launchers"\n'
        'appletsrc_launcher_group = ["Configuration", "General"]\n'
        'taskbar_plugin_names = ["org.kde.plasma.icontasks", "org.kde.plasma.taskmanager"]\n'
        'panel_launcher_id = "applications:google-chrome.desktop"\n'
        'panel_restart_command = ["systemctl", "--user", "--machine", "{username}@.host", "restart", "plasma-plasmashell.service"]\n'
        'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n'
        'settings_repo_ref = "main"\n'
        'settings_dir = "/var/cache/pyntara/chromium-settings"\n'
        'settings_system_tree_relative_path = "system"\n'
        'preferences_relative_path = "Default/Preferences"\n'
        'profile_dir_relative_path = ".config/google-chrome"\n'
        'keyring_temp_dir_prefix = "pyntara-chrome-"\n'
        'keyring_armored_file_name = "google-chrome-key.pub"\n'
        'apt_source_template_file_name = "google-chrome.sources"\n'
        'launch_flags = ["--proxy-server={proxy_server}", "--user-data-dir={user_data_dir}", "--remote-debugging-port={cdp_port}", "--remote-debugging-address={cdp_address}"]\n'
        'keyring_dearmor_command = ["gpg", "--dearmor", "--output", "{output}", "{armored}"]\n'
        'settings_clone_command = ["git", "clone", "--quiet", "--depth", "1", "--branch", "{ref}", "{url}", "{dir}"]\n'
        'settings_fetch_command = ["git", "-C", "{dir}", "fetch", "--quiet", "origin", "{ref}"]\n'
        'settings_revision_command = ["git", "-C", "{dir}", "rev-parse", "{revision}"]\n'
        'settings_reset_command = ["git", "-C", "{dir}", "reset", "--hard", "{revision}"]\n'
        'process_check_command = ["pgrep", "-x", "{process_name}"]\n'
        'mount_check_command = ["findmnt", "--noheadings", "--output", "TARGET,FSROOT", "--target", "{path}"]\n'
        'mount_reload_command = ["systemctl", "daemon-reload"]\n'
        'mount_enable_command = ["systemctl", "enable", "--now", "{unit_name}"]\n'
        'menu_refresh_command = ["runuser", "-u", "{username}", "--", "env", "HOME={home_dir}", "XDG_MENU_PREFIX=plasma-", "kbuildsycoca6", "--noincremental"]\n'
        'mount_unit_template_file_name = "mount_chrome_user_dir.service"\n'
        'system_root = "/"\n'
        'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n'
        'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n'
        'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n'
        'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n'
        'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n'
        'desktop_entry_exec_key = "Exec="\n'
        'profile_mirror_path = "/home/i/.config/google-chrome-cdp"\n'
        'mount_service_unit_name = "mount_chrome_user_dir.service"\n'
        "cdp_port = 19222\n"
        'cdp_address = "127.0.0.1"\nfile_mode = "0644"\n'
        'runuser_command = ["runuser", "-u", "{username}", "--"]\n'
        'kreadconfig_command = ["kreadconfig6", "--file", "{file_name}"]\n'
        'kwriteconfig_command = ["kwriteconfig6", "--file", "{file_name}"]\n'
        'config_group_flag = ["--group", "{group}"]\n'
        'config_key_flag = ["--key", "{key}"]\n'
        "[playwright_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'packages = ["nodejs", "npm"]\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
        'cli_package = "@playwright/cli"\n'
        'user_prefix_relative_path = ".local"\n'
        'cli_bin_relative_path = "bin/playwright-cli"\n'
        'runuser_command = ["runuser", "-u", "{username}", "--", "env", "HOME={home_dir}"]\n'
        'cli_version_command = ["{cli_bin}", "--version"]\n'
        'npm_install_command = ["npm", "install", "-g", "{cli_package}", "--prefix", "{prefix}"]\n'
        "npm_install_timeout_seconds = 900\n"
        "[vocalinux_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'download_dir = "/var/cache/pyntara/vocalinux"\n'
        'version = "0.16.2"\n'
        'github_repo = "VocaHQ/vocalinux"\n'
        'asset_name_template = "Vocalinux-{version}-{asset_arch}.AppImage"\n'
        'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n'
        'input_group = "input"\n'
        'appimage_dir_relative_path = ".local/share/vocalinux/appimage"\n'
        'app_config_relative_path = ".config/vocalinux/config.json"\n'
        'autostart_relative_path = ".config/autostart/vocalinux.desktop"\n'
        'echo_desktop_relative_path = ".local/share/applications/net.local.echo.desktop"\n'
        'app_config_template_file_name = "config.json"\n'
        'autostart_template_file_name = "vocalinux.desktop"\n'
        'echo_desktop_template_file_name = "net.local.echo.desktop"\n'
        'shortcuts_file_name = "kglobalshortcutsrc"\n'
        'shortcut_group_name = "services"\n'
        'shortcut_entry_name = "net.local.echo.desktop"\n'
        'shortcut_action_name = "_launch"\n'
        'shortcut_key_sequence = "Meta+S"\n'
        'service_unit_name = "ydotool.service"\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
        'user_file_mode = "0644"\nexecutable_file_mode = "0755"\n'
        'runuser_command = ["runuser", "-u", "{username}", "--"]\n'
        'kreadconfig_command = ["kreadconfig6", "--file", "{file_name}"]\n'
        'kwriteconfig_command = ["kwriteconfig6", "--file", "{file_name}"]\n'
        'config_group_flag = ["--group", "{group}"]\n'
        'config_key_flag = ["--key", "{key}"]\n'
        'mkdir_command = ["mkdir", "-p", "{path}"]\n'
        'chown_command = ["chown", "{owner}", "{path}"]\n'
        'chmod_command = ["chmod", "{file_mode}", "{path}"]\n'
        'group_members_command = ["id", "-nG", "{username}"]\n'
        'group_add_command = ["usermod", "-aG", "{input_group}", "{username}"]\n'
        'service_active_command = ["systemctl", "--user", "--machine", "{username}@.host", "is-active", "{service_unit_name}"]\n'
        'service_active_state = "active"\n'
        'service_enable_command = ["systemctl", "--user", "--machine", "{username}@.host", "enable", "--now", "{service_unit_name}"]\n'
        "[system_metrics_setup]\n"
        "backoff_base_seconds = 2\nbackoff_multiplier = 2\n"
        "backoff_max_seconds = 14400\n"
        'python_version = "3.14"\nerror_priority = 3\n'
        'venv_dir = "/usr/local/lib/pyntara/venv"\n'
        'venv_python_relative_path = "bin/python"\n'
        'system_config_path = "/etc/pyntara/config.toml"\n'
        'command_path = "/usr/local/bin/commit_system_metrics"\n'
        'commit_command = ["{command_path}", "{file}"]\n'
        'vault_backup_file_name = "{hostname}.kdbx"\n'
        'vault_backup_file_mode = "0600"\n'
        'system_metrics_dir = "/var/lib/pyntara/metrics"\n'
        'system_metrics_dir_mode = "0700"\nqueue_file_mode = "0600"\n'
        'max_queue_file_size_bytes = 104857600\nsend_order = "oldest_first"\n'
        "queue_file_suffix_length = 12\n"
        'queue_file_suffix_alphabet = "abcxyz0123456789"\n'
        'spool_dir = "/var/spool/system_metrics"\nspool_dir_mode = "1733"\n'
        'spool_dir_permission_mask = "7777"\n'
        'command_file_mode = "0755"\n'
        'command_permission_mask = "0777"\n'
        'service_unit_name = "system_metrics.service"\n'
        'ingest_service_unit_name = "system_metrics-ingest.service"\n'
        'ingest_path_unit_name = "system_metrics-ingest.path"\n'
        'unit_template_file_name = "system_metrics.service"\n'
        'ingest_unit_template_file_name = "system_metrics-ingest.service"\n'
        'ingest_path_template_file_name = "system_metrics-ingest.path"\n'
        'collector_unit_template_file_name = "system_metrics_collector.service"\n'
        'collector_timer_template_file_name = "system_metrics_collector.timer"\n'
        'commit_command_template_file_name = "commit_system_metrics.sh"\n'
        'systemctl_daemon_reload_command = ["systemctl", "daemon-reload"]\n'
        'systemctl_enable_command = ["systemctl", "enable", "{unit_name}"]\n'
        'systemctl_restart_command = ["systemctl", "restart", "{unit_name}"]\n'
        'systemctl_start_command = ["systemctl", "start", "{unit_name}"]\n'
        "send_service_command = "
        '["{python}", "-m", "pyntara.metrics", "{config_path}"]\n'
        "ingest_service_command = "
        '["{python}", "-m", "pyntara.metrics_ingest", "{config_path}"]\n'
        "collector_service_command = "
        '["{python}", "-m", "pyntara.metrics_collect", "{config_path}"]\n'
        "venv_version_command = "
        '["{python}", "-c", "import pyntara; print(pyntara.__version__)"]\n'
        "venv_create_command = "
        '["{uv}", "venv", "{venv_dir}", "--python", "{python_version}", '
        '"--no-managed-python"]\n'
        "venv_sync_command = "
        '["{uv}", "sync", "--project", "{repo_root}", "--active", "--locked", '
        '"--no-dev", "--no-editable"]\n'
        'venv_reinstall_flags = ["--reinstall-package", "pyntara"]\n'
        'service_journal_identifier = "system_metrics"\n'
        'commit_journal_identifier = "commit_system_metrics"\n'
        'main_outbox_dir = "main_outbox"\ntemp_dir = "temp"\n'
        'spool_temp_prefix = ".commit-"\ntemp_name_random_bytes = 8\nqueue_link_attempts = 5\n'
        'google_script_dir = "google_script"\nmain_sent_dir = "main_sent"\n'
        "google_script_timeout_seconds = 60\n"
        'google_script_upload_command = ["curl", "--location", "--max-time", "{timeout_seconds}", "--silent", "--show-error", "--data-urlencode", "filename={file_name}", "--data-urlencode", "pass={key}", "--data-urlencode", "data@-"]\n'
        'google_script_key_entry_title = "google_script_key"\n'
        "google_script_deployment_url_regex = '^https://script\\.google\\.com/macros/s/([A-Za-z0-9_-]+)/exec$'\n"
        'google_script_answer_ok_prefix = "OK "\n'
        "google_script_answer_excerpt_chars = 200\n"
        'telemetry_pdf_report_file_name = "network-{hostname}.pdf"\n'
        'telemetry_password_entry_title = "telemetry_password"\n'
        'telemetry_pdf_vault_entry_titles = ["three_x_ui_credentials", "xray_connection", "rustdesk_password"]\n'
        "[system_metrics_setup.collector]\n"
        "boot_delay_seconds = 30\n"
        'daily_send_times = ["12:00:00", "00:00:00"]\n'
        "threshold_percent = 50\n"
        "retry_base_seconds = 2\n"
        "retry_multiplier = 2\n"
        "retry_max_seconds = 600\n"
        "command_timeout_seconds = 15\n"
        'service_unit_name = "system_metrics_collector.service"\n'
        'timer_unit_name = "system_metrics_collector.timer"\n'
        'start_command = ["systemctl", "start", "--no-block", "{service_unit_name}"]\n'
        'journal_identifier = "system_metrics_collector"\n'
        'lock_file_path = "/run/pyntara/system_metrics_collector.lock"\n'
        'report_file_name = "network.json"\n'
        'report_file_mode = "0600"\n'
        'report_keys = { generated_at = "generated_at", ready_percent = '
        '"ready_percent", network = "network", system = "system", name = "name", '
        'status = "status", output = "output" }\n'
        'report_status_words = { ok = "ok", empty = "empty", error = "error" }\n'
        "[[system_metrics_setup.collector.network_modules]]\n"
        'name = "ipv4"\n'
        'command = ["ip", "-4", "addr", "show", "scope", "global"]\n'
        "[[system_metrics_setup.collector.network_modules]]\n"
        'name = "ipv6"\n'
        'command = ["ip", "-6", "addr", "show", "scope", "global"]\n'
        "[[system_metrics_setup.collector.system_modules]]\n"
        'name = "hostname"\n'
        'command = ["hostname"]\n'
        "[system_metrics_setup.telemetry_pdf]\n"
        'font = "Courier"\nfont_size = 12\nline_width_chars = 72\n'
        "margin = 36\n"
        'section_ssh = "SSH"\nsection_secrets = "SECRETS"\n'
        'section_json = "NETWORK.JSON"\n'
        'nextdns_module_name = "nextdns"\n'
        'field_order = ["username", "password", "url", "notes"]\n'
        '[vault_structure]\n[[vault_structure.entries]]\ntitle = "password_salt"\n'
        'notes = "Primary salt."\n[[vault_structure.entries]]\n'
        'title = "pyntara_local_vault_password"\nnotes = "Local vault password."\n'
        '[[vault_structure.entries]]\ntitle = "google_script_key"\n'
        'notes = "Google script credentials."\n'
        '[[vault_structure.entries]]\ntitle = "telemetry_password"\n'
        'generated_password = "proquint-4"\nnotes = "Telemetry PDF password."\n'
        '[[vault_structure.entries]]\ntitle = "three_x_ui_credentials"\n'
        'notes = "3x-ui panel credentials."\n'
        '[[vault_structure.entries]]\ntitle = "xray_connection"\n'
        'notes = "Xray server connection profile."\n'
        '[[vault_structure.entries]]\ntitle = "sotavpn_uuid"\n'
        'notes = "Sotavpn access key."\n'
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


def assert_config_error(tmp_path: Path, content: str, match: str | None = None) -> None:
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
