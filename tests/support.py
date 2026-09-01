"""Shared test factories and fakes for the engine test suite.

The Context and Config shapes repeat in every test module. Defining them
once here keeps a change to Config (a new field, a renamed sub-config) a
single edit instead of six. FakeProc replaces the identical subprocess
stub classes that were copied per file. Domain-specific fakes (sysfs
mirrors, disk usage) stay in their own test modules.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from pyntara.config import (
    AddExtraReposConfig,
    CliToolsConfig,
    CollectorModuleConfig,
    Config,
    DnsproxySetupConfig,
    EngineConfig,
    FfmpegSetupConfig,
    HostnameConfig,
    I2pdServiceSetupConfig,
    ImagemagickSetupConfig,
    KConfigRecord,
    KdeKeyboardSetupConfig,
    KdeSettingsConfig,
    LocalVaultSetupConfig,
    NextdnsSetupSystemWideConfig,
    PortForwardingSetupConfig,
    RustdeskOptionConfig,
    RustdeskSetupConfig,
    SshClientSetupConfig,
    SshDaemonSetupConfig,
    SshDirective,
    SwapfileServiceInstallConfig,
    SystemMetricsCollectorConfig,
    SystemMetricsSetupConfig,
    TaskConfig,
    ThreeXuiXraySetupConfig,
    TorSetupConfig,
    VaultEntry,
    VaultStructureConfig,
    YggdrasilMulticastInterfaceConfig,
    YggdrasilServiceSetupConfig,
    ZramServiceConfig,
    ZswapServiceConfig,
)
from pyntara.context import Context


class FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def augtool_fake_run(command: list[str], input_: str | None) -> FakeProc:
    """Simulate augtool --noautoload over the real drop-in file.

    The fake implements the subset of augeas the tasks use: a manual
    load entry, load, print, set, rm and save. The tree is keyed by
    augeas path without [index] suffixes; nested nodes (the Host block
    of ssh_config) keep their parent-child paths, and save writes
    indented lines for them. The lens is not needed, because the file
    layout is derived from the indentation.
    """

    script = input_ or ""
    incl: str | None = None
    tree: dict[str, str] = {}
    out_lines: list[str] = []
    base = ""
    last_top: str | None = None
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("set "):
            parts = line.split(" ", 2)
            path, value = parts[1], parts[2].strip('"')
            if path.startswith("/augeas/load/"):
                if path.endswith("/incl"):
                    incl = value
                    base = f"/files{incl}"
                continue
            path = path.replace("[last()]", "")
            if path.endswith("/#comment"):
                existing = sorted(
                    node for node in tree if node.startswith(base + "/#comment")
                )
                if existing:
                    tree[existing[0]] = value
                else:
                    tree[path] = value
            else:
                tree[path] = value
        elif line.startswith("rm "):
            path = line.split(" ", 1)[1]
            for node in [
                node
                for node in tree
                if node == path or node.startswith(path + "/")
            ]:
                del tree[node]
            out_lines.append(f"rm : {path}")
        elif line == "load":
            tree = {}
            last_top = None
            if incl:
                file_path = Path(incl)
                if file_path.is_file():
                    comment_count = 0
                    for text in file_path.read_text(encoding="utf-8").splitlines():
                        if not text.strip():
                            continue
                        indented = text != text.lstrip()
                        stripped = text.strip()
                        if stripped.startswith("#"):
                            comment_count += 1
                            node = (
                                f"{base}/#comment"
                                if comment_count == 1
                                else f"{base}/#comment[{comment_count}]"
                            )
                            tree[node] = stripped[1:].strip()
                            last_top = node
                        else:
                            key, sep, value = stripped.partition(" ")
                            value = value.strip() if sep else ""
                            if indented and last_top is not None:
                                node = f"{last_top}/{key}"
                            else:
                                node = f"{base}/{key}"
                                last_top = node
                            tree[node] = value
        elif line.startswith("print "):
            path = line.split(" ", 1)[1]
            out_lines.append(path)
            for node, value in tree.items():
                if node.startswith(path + "/"):
                    out_lines.append(f'{node} = "{value}"')
        elif line == "save":
            if incl:
                file_path = Path(incl)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                content: list[str] = []
                for node, value in tree.items():
                    suffix = node[len(base):].lstrip("/")
                    if "/" in suffix:
                        continue
                    label = suffix.split("[", 1)[0]
                    if label.startswith("#"):
                        content.append(f"# {value}")
                        continue
                    children = [
                        (child, child_value)
                        for child, child_value in tree.items()
                        if child.startswith(node + "/")
                    ]
                    if children:
                        content.append(f"{label} {value}")
                        for child, child_value in children:
                            child_label = child[len(node):].lstrip("/").split("[", 1)[0]
                            content.append(f"\t{child_label} {child_value}")
                    else:
                        content.append(f"{label} {value}")
                file_path.write_text("\n".join(content) + "\n", encoding="utf-8")
            out_lines.append("Saved 1 file(s)")
    return FakeProc(0, "\n".join(out_lines) + "\n")


def make_config(
    *,
    task_data_root: Path = Path("/tmp"),
    notice_timeout: int = 7,
    command_timeout_seconds: int = 1800,
    curl_timeout_seconds: int = 777,
    curl_retries: int = 13,
    error_priority: int = 3,
    progress_priority: int = 7,
    process_check_timeout_seconds: int = 5,
    task_start_delay_seconds: float = 0.5,
    engine_desktop_detect_processes: tuple[str, ...] = (
        "kwin_wayland",
        "kwin_x11",
        "plasmashell",
        "gnome-shell",
    ),
    cli_tools_packages: tuple[str, ...] = ("mc", "htop"),
    cli_tools_threshold: int = 70,
    cli_tools_retries: int = 3,
    cli_tools_status_timeout: int = 30,

    imagemagick_setup_packages: tuple[str, ...] = ("imagemagick",),
    imagemagick_setup_policy_path: Path = Path("/etc/ImageMagick-7/policy.xml"),
    imagemagick_setup_retries: int = 3,
    imagemagick_setup_status_timeout: int = 30,

    ffmpeg_setup_packages: tuple[str, ...] = ("ffmpeg",),
    ffmpeg_setup_retries: int = 3,
    ffmpeg_setup_status_timeout: int = 30,
    ffmpeg_setup_wayrecord_bin_path: Path = Path(
        "/usr/local/bin/pyntara-wayrecord"
    ),
    ffmpeg_setup_wayrecord_desktop_path: Path = Path(
        "/usr/share/applications/pyntara-wayrecord.desktop"
    ),

    dnsproxy_download_dir: Path = Path("/tmp/dnsproxy"),
    dnsproxy_binary_path: Path = Path("/usr/local/bin/dnsproxy"),
    dnsproxy_service_unit_name: str = "dnsproxy.service",
    dnsproxy_service_unit_path: Path = Path("/etc/systemd/system/dnsproxy.service"),
    dnsproxy_service_template_path: Path = Path("task_data/dnsproxy_setup/dnsproxy.service"),
    dnsproxy_listen_addresses: tuple[str, ...] = ("0.0.0.0", "::"),
    dnsproxy_listen_port: int = 53053,
    dnsproxy_cache_size_bytes: int = 16777216,
    dnsproxy_query_log_path: Path = Path("/var/log/pyntara/dnsproxy.log"),
    dnsproxy_profile_id_file_path: Path = Path("/var/lib/pyntara/nextdns_profile_id"),
    dnsproxy_resolved_conf_dir: Path = Path("/etc/systemd/resolved.conf.d"),
    add_extra_repos_components: tuple[str, ...] = (
        "universe",
        "restricted",
        "multiverse",
    ),
    add_extra_repos_ubuntu_hosts: tuple[str, ...] = (
        "archive.ubuntu.com",
        "security.ubuntu.com",
        "ports.ubuntu.com",
        "old-releases.ubuntu.com",
    ),
    hostname_file: Path = Path("/etc/hostname"),
    hostname_set_hostname_command: tuple[str, ...] = (
        "hostnamectl",
        "set-hostname",
    ),
    kde_keyboard_setup_packages: tuple[str, ...] = (
        "libkf6config-bin",
        "qdbus-qt6",
        "python3-dbus",
    ),
    kde_keyboard_setup_username: str = "i",
    kde_keyboard_setup_home_dir: str = "/home/i",
    kde_keyboard_setup_config_dir: str = "/home/i/.config",
    kde_keyboard_setup_kxkbrc_file_name: str = "kxkbrc",
    kde_keyboard_setup_appletsrc_file_name: str = (
        "plasma-org.kde.plasma.desktop-appletsrc"
    ),
    kde_keyboard_setup_applet_plugin: str = "org.kde.plasma.keyboardlayout",
    kde_keyboard_setup_layouts: tuple[str, ...] = ("us", "ru", "es"),
    kde_keyboard_setup_switch_option: str = "grp:caps_select",
    kde_keyboard_setup_reset_old_options: bool = True,
    kde_keyboard_setup_switch_mode: str = "WinClass",
    kde_keyboard_setup_use_layout_switching: bool = True,
    kde_keyboard_setup_indicator_display_style: str = "Flag",
    kde_keyboard_setup_kwin_reload_command: tuple[str, ...] = (
        "qdbus6",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin.reconfigure",
    ),
    kde_keyboard_setup_panel_restart_command: tuple[str, ...] = (
        "systemctl",
        "--user",
        "--machine",
        "i@.host",
        "restart",
        "plasma-plasmashell.service",
    ),
    kde_keyboard_setup_layout_switch_shortcuts: dict[str, str] | None = None,
    kde_settings_packages: tuple[str, ...] = (
        "plasma-workspace",
        "libkf6config-bin",
        "kubuntu-settings-desktop",
        "python3-dbus",
    ),
    kde_settings_username: str = "i",
    kde_settings_home_dir: str = "/home/i",
    kde_settings_user_dirs: dict[str, str] | None = None,
    kde_settings_color_scheme: str = "BreezeDark",
    kde_settings_look_and_feel: str = "org.kubuntudark.desktop",
    kde_settings_look_and_feel_light: str = "org.kubuntulight.desktop",
    kde_settings_automatic_look_and_feel: bool = False,
    kde_settings_cursor_theme: str = "Oxygen_Yellow",
    kde_settings_cursor_theme_light: str = "Oxygen_Blue",
    kde_settings_numlock_on_boot: str = "off",
    kde_settings_touchpad_click_method: str = "clickfinger",
    kde_settings_touchpad_disable_on_external_mouse: bool = False,
    kde_settings_virtual_keyboard_enabled: bool = True,
    kde_settings_virtual_keyboard_input_method: str = (
        "/usr/share/applications/org.kde.plasma.keyboard.desktop"
    ),
    kde_settings_virtual_keyboard_locales: tuple[str, ...] = (
        "en_US",
        "es_MX",
        "ru_RU",
    ),
    kde_settings_kwin_reload_command: tuple[str, ...] = (
        "qdbus6",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin.reconfigure",
    ),
    kde_settings_sddm_autologin_user: str = "i",
    kde_settings_sddm_autologin_session: str = "plasma",
    kde_settings_sddm_theme: str = "kubuntu",
    kde_settings_sddm_theme_cursor_size: str = "30",
    kde_settings_sddm_theme_cursor_theme: str = "breeze_cursors",
    kde_settings_sddm_theme_font: str = "Noto Sans,20",
    kde_settings_kconfig: tuple[KConfigRecord, ...] = (),
    kde_settings_places_hidden: tuple[str, ...] = (),
    swapfile_path: Path = Path("/swapfile"),
    swapfile_ram_multiplier: float = 2.0,
    swapfile_ram_extra_mb: int = 4096,
    swapfile_disk_fraction: float = 0.5,
    swapfile_mode: int = 0o600,
    swapfile_size_tolerance_mb: int = 1,
    swapfile_service_unit_name: str = "swapfile.service",
    zswap_enabled: bool = True,
    zswap_compressor: str = "zstd",
    zswap_max_pool_percent: int = 50,
    zswap_accept_threshold_percent: int = 100,
    zswap_shrinker_enabled: bool = True,
    zswap_service_unit_name: str = "zswap.service",
    zram_compressor: str = "zstd",
    zram_swap_priority: int = 1111,
    zram_memory_fraction_percent: int = 96,
    zram_fallback_cpu_count: int = 8,
    zram_alignment_bytes: int = 4096,
    zram_service_unit_name: str = "zram.service",
    zram_reset_busy_attempts: int = 5,
    zram_reset_busy_retry_delay_seconds: float = 0.5,
    i2pd_github_repo: str = "PurpleI2P/i2pd",
    i2pd_download_dir: Path = Path("/var/lib/pyntara/i2pd-download"),
    i2pd_service_unit_name: str = "i2pd.service",
    i2pd_config_path: Path = Path("/etc/i2pd/i2pd.conf"),
    i2pd_log_level: str = "warn",
    i2pd_bandwidth: int = 12500,
    i2pd_share: int = 1,
    i2pd_http_enabled: bool = False,
    i2pd_socks_proxy_enabled: bool = True,
    i2pd_install_retries: int = 3,
    i2pd_start_check_attempts: int = 5,
    i2pd_start_check_retry_delay_seconds: float = 0.0,
    i2pd_tunnels_config_path: Path = Path("/etc/i2pd/tunnels.conf"),
    i2pd_tunnel_name: str = "ssh",
    i2pd_tunnel_host: str = "127.0.0.1",
    i2pd_tunnel_keys_path: Path = Path("/var/lib/i2pd/ssh.dat"),
    i2pd_address_file_path: Path = Path("/var/lib/pyntara/i2pd_ssh_address"),
    i2pd_address_file_mode: int = 0o644,
    three_x_ui_github_repo: str = "MHSanaei/3x-ui",
    three_x_ui_install_script_url: str = (
        "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh"
    ),
    three_x_ui_install_dir: Path = Path("/usr/local/x-ui"),
    three_x_ui_service_unit_name: str = "x-ui.service",
    three_x_ui_start_check_attempts: int = 10,
    three_x_ui_start_check_retry_delay_seconds: int = 1,
    three_x_ui_install_result_env_path: Path = Path("/etc/x-ui/install-result.env"),
    three_x_ui_panel_port: int = 35353,
    three_x_ui_ssl_enabled: bool = True,
    three_x_ui_panel_http_address: str = "127.0.0.1",
    three_x_ui_vault_entry_title: str = "three_x_ui_credentials",
    three_x_ui_inbound_port: int = 443,
    three_x_ui_inbound_remark: str = "universal",
    three_x_ui_reality_dest: str = "www.google.com:443",
    three_x_ui_reality_server_names: tuple[str, ...] = ("www.google.com",),
    three_x_ui_reality_short_id: str = "6ba85179e30d4fc2",
    three_x_ui_acme_port: int = 80,
    three_x_ui_cert_dir: Path = Path("/root/cert/ip"),
    three_x_ui_self_signed_cert_dir: Path = Path("/root/cert/selfsigned"),
    three_x_ui_server_ip_services: tuple[str, ...] = (
        "https://api4.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://v4.api.ipinfo.io/ip",
        "https://ipv4.myexternalip.com/raw",
        "https://4.ident.me",
        "https://check-host.net/ip",
    ),
    rustdesk_github_repo: str = "rustdesk/rustdesk",
    rustdesk_download_dir: Path = Path("/var/cache/pyntara/rustdesk"),
    rustdesk_id_file_path: Path = Path("/var/lib/pyntara/rustdesk_id"),
    rustdesk_id_file_mode: int = 0o644,
    rustdesk_vault_entry_title: str = "rustdesk_password",
    rustdesk_service_unit_name: str = "rustdesk.service",
    rustdesk_password_words: int = 6,
    rustdesk_password_separator: str = " ",
    rustdesk_config_dir: Path = Path("/home/i/.config/rustdesk"),
    rustdesk_install_timeout_seconds: int = 600,
    rustdesk_apt_update_timeout_seconds: int = 600,
    rustdesk_install_retries: int = 2,
    rustdesk_start_check_attempts: int = 10,
    rustdesk_start_check_retry_delay_seconds: float = 1.0,
    rustdesk_options: tuple[RustdeskOptionConfig, ...] = (
        RustdeskOptionConfig(key="enable-udp-punch", value="Y"),
    ),
    yggdrasil_github_repo: str = "yggdrasil-network/yggdrasil-go",
    yggdrasil_download_dir: Path = Path("/var/lib/pyntara/yggdrasil-download"),
    yggdrasil_service_unit_name: str = "yggdrasil.service",
    yggdrasil_install_retries: int = 3,
    yggdrasil_config_path: Path = Path("/etc/yggdrasil/yggdrasil.conf"),
    yggdrasil_private_key_path: Path = Path("/etc/yggdrasil/private-key.pem"),
    yggdrasil_config_file_mode: int = 0o640,
    yggdrasil_private_key_file_mode: int = 0o600,
    yggdrasil_if_name: str = "ygg",
    yggdrasil_if_mtu: int = 65535,
    yggdrasil_admin_listen: str = "unix:///var/run/yggdrasil/yggdrasil.sock",
    yggdrasil_listen: tuple[str, ...] = (
        "tcp://[::]:0",
        "tls://[::]:0",
        "quic://[::]:0",
        "ws://[::]:0",
    ),
    yggdrasil_multicast_interfaces: tuple[
        YggdrasilMulticastInterfaceConfig, ...
    ] = (
        YggdrasilMulticastInterfaceConfig(regex=".*", beacon=True, listen=True),
    ),
    yggdrasil_peers_full_path: Path = Path("/etc/yggdrasil/peers-full.txt"),
    yggdrasil_peers_tarball_url: str = (
        "https://codeload.github.com/yggdrasil-network/public-peers/"
        "tar.gz/refs/heads/master"
    ),
    yggdrasil_peer_batch_size: int = 100,
    yggdrasil_peer_target_count: int = 6,
    yggdrasil_peer_probe_timeout_seconds: float = 0.0,
    yggdrasil_peer_max_batches: int = 0,
    yggdrasil_static_peers: tuple[str, ...] = (),
    yggdrasil_address_file_path: Path = Path("/var/lib/pyntara/yggdrasil_self_address"),
    yggdrasil_address_file_mode: int = 0o644,
    yggdrasil_address_save_retry_base_seconds: int = 1,
    yggdrasil_address_save_retry_multiplier: int = 2,
    yggdrasil_address_save_retry_max_seconds: int = 1,
    yggdrasil_connection_wait_base_seconds: int = 1,
    yggdrasil_connection_wait_multiplier: int = 2,
    yggdrasil_connection_wait_max_seconds: int = 1,
    tor_package_name: str = "tor",
    tor_service_unit_name: str = "tor@default.service",
    tor_torrc_path: Path = Path("/etc/tor/torrc"),
    tor_torrc_dropin_path: Path = Path("/etc/tor/pyntara.conf"),
    tor_torrc_include_path: str = "/etc/tor/pyntara.conf",
    tor_dropin_file_mode: int = 0o644,
    tor_hidden_service_dir: Path = Path("/var/lib/tor/ssh"),
    tor_hidden_service_dir_mode: int = 0o700,
    tor_user: str = "debian-tor",
    tor_socks_port: int = 9050,
    tor_onion_ssh_port: int = 22,
    tor_num_introduction_points: int = 6,
    tor_log_level: str = "notice",
    tor_install_retries: int = 3,
    tor_start_check_attempts: int = 5,
    tor_start_check_retry_delay_seconds: float = 0.0,
    tor_address_file_path: Path = Path("/var/lib/pyntara/tor_ssh_address"),
    tor_address_file_mode: int = 0o644,
    ssh_daemon_package_name: str = "openssh-server",
    ssh_daemon_package_status_timeout_seconds: int = 30,
    ssh_daemon_install_retries: int = 3,
    ssh_daemon_service_unit_name: str = "ssh.service",
    ssh_daemon_socket_unit_name: str = "ssh.socket",
    ssh_daemon_start_check_attempts: int = 5,
    ssh_daemon_start_check_retry_delay_seconds: float = 0.0,
    ssh_daemon_sshd_config_path: Path = Path("/etc/ssh/sshd_config"),
    ssh_daemon_sshd_config_dropin_path: Path = Path(
        "/etc/ssh/sshd_config.d/pyntara.conf"
    ),
    ssh_daemon_dropin_file_mode: int = 0o644,
    ssh_daemon_private_key_file_name: str = "id_ed25519",
    ssh_daemon_public_key_file_name: str = "id_ed25519.pub",
    ssh_daemon_port_forwarding_private_key_file_name: str = "id_ed25519_pf",
    ssh_daemon_port_forwarding_public_key_file_name: str = "id_ed25519_pf.pub",
    ssh_daemon_port_forwarding_authorized_keys_options: str = (
        'restrict,port-forwarding,permitlisten="*"'
    ),
    ssh_daemon_private_key_file_mode: int = 0o600,
    ssh_daemon_public_key_file_mode: int = 0o644,
    ssh_daemon_authorized_keys_file_mode: int = 0o600,
    ssh_daemon_ssh_dir_mode: int = 0o700,
    ssh_daemon_root_ssh_dir: Path = Path("/root/.ssh"),
    ssh_daemon_users: tuple[str, ...] = ("i", "j", "k"),
    ssh_daemon_directives: tuple[SshDirective, ...] = (
        SshDirective(name="Port", value="30222"),
        SshDirective(name="PubkeyAuthentication", value="yes"),
    ),
    ssh_client_ssh_config_path: Path = Path("/etc/ssh/ssh_config"),
    ssh_client_ssh_config_dropin_path: Path = Path(
        "/etc/ssh/ssh_config.d/pyntara.conf"
    ),
    ssh_client_dropin_file_mode: int = 0o644,
    ssh_client_directives: tuple[SshDirective, ...] = (SshDirective(
        name="AddressFamily", value="any"
    ),),
    system_metrics_backoff_base_seconds: int = 2,
    system_metrics_backoff_multiplier: int = 2,
    system_metrics_backoff_max_seconds: int = 14400,
    system_metrics_python_version: str = "3",
    system_metrics_error_priority: int = 3,
    system_metrics_venv_dir: Path = Path("/usr/local/lib/pyntara/venv"),
    system_metrics_system_config_path: Path = Path("/etc/pyntara/config.toml"),
    system_metrics_command_path: Path = Path("/usr/local/bin/commit_system_metrics"),
    system_metrics_vault_backup_file_name: str = "{hostname}.kdbx",
    system_metrics_dir: Path = Path("/var/lib/pyntara/metrics"),
    system_metrics_dir_mode: int = 0o700,
    system_metrics_queue_file_mode: int = 0o600,
    system_metrics_max_queue_file_size_bytes: int = 104857600,
    system_metrics_send_order: str = "oldest_first",
    system_metrics_queue_file_suffix_length: int = 12,
    system_metrics_spool_dir: Path = Path("/var/spool/system_metrics"),
    system_metrics_spool_dir_mode: int = 0o1733,
    system_metrics_command_file_mode: int = 0o755,
    system_metrics_service_unit_name: str = "system_metrics.service",
    system_metrics_ingest_service_unit_name: str = "system_metrics-ingest.service",
    system_metrics_ingest_path_unit_name: str = "system_metrics-ingest.path",
    system_metrics_service_journal_identifier: str = "system_metrics",
    system_metrics_commit_journal_identifier: str = "commit_system_metrics",
    system_metrics_main_outbox_dir: str = "main_outbox",
    system_metrics_temp_dir: str = "temp",
    system_metrics_spool_temp_prefix: str = ".commit-",
    system_metrics_queue_link_attempts: int = 5,
    system_metrics_google_script_dir: str = "google_script",
    system_metrics_main_sent_dir: str = "main_sent",
    system_metrics_google_script_timeout_seconds: int = 60,
    system_metrics_google_script_key_entry_title: str = "google_script_key",
    system_metrics_google_script_deployment_url_regex: str = (
        r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]+)/exec$"
    ),
    system_metrics_collector_boot_delay_seconds: int = 30,
    system_metrics_collector_daily_send_time: str = "12:00:00",
    system_metrics_collector_threshold_percent: int = 50,
    system_metrics_collector_retry_base_seconds: int = 2,
    system_metrics_collector_retry_multiplier: int = 2,
    system_metrics_collector_retry_max_seconds: int = 600,
    system_metrics_collector_command_timeout_seconds: int = 15,
    system_metrics_collector_service_unit_name: str = (
        "system_metrics_collector.service"
    ),
    system_metrics_collector_timer_unit_name: str = (
        "system_metrics_collector.timer"
    ),
    system_metrics_collector_journal_identifier: str = (
        "system_metrics_collector"
    ),
    system_metrics_collector_lock_file_path: Path = Path(
        "/run/pyntara/system_metrics_collector.lock"
    ),
    system_metrics_collector_report_file_name: str = "network-{hostname}.json",
    system_metrics_collector_network_modules: tuple[CollectorModuleConfig, ...] = (),
    system_metrics_collector_system_modules: tuple[CollectorModuleConfig, ...] = (),
    local_vault_source_production: Path = Path("secrets/production.vault"),
    local_vault_source_default: Path = Path("secrets/default.vault"),
    local_vault_path: Path = Path("/var/lib/pyntara/secrets/pyntara.vault"),
    local_vault_pass_file_path: Path = Path("/etc/pyntara/pass"),
    local_vault_entry_title: str = "pyntara_local_vault_password",
    local_vault_secrets_dir_mode: int = 0o700,
    local_vault_file_mode: int = 0o640,
    local_vault_pass_dir_mode: int = 0o700,
    local_vault_pass_file_mode: int = 0o400,
    local_vault_error_priority: int = 3,
    nextdns_vault_group_title: str = "NextDNS",
    nextdns_profile_id_file_path: Path = Path("/var/lib/pyntara/nextdns_profile_id"),
    nextdns_profile_id_file_mode: int = 0o644,
    nextdns_error_priority: int = 3,
    port_forwarding_vault_group_title: str = "port_forwarding_servers",
    port_forwarding_passphrase_entry_title: str = "ssh_passphase_for_port_forwarding",
    port_forwarding_remote_ssh_user: str = "i",
    port_forwarding_desired_port_min: int = 32768,
    port_forwarding_desired_port_max: int = 60999,
    port_forwarding_server_alive_interval_seconds: int = 61,
    port_forwarding_server_alive_count_max: int = 3,
    port_forwarding_connect_timeout_seconds: int = 31,
    port_forwarding_backoff_base_seconds: int = 2,
    port_forwarding_backoff_multiplier: int = 2,
    port_forwarding_backoff_max_seconds: int = 1024,
    port_forwarding_state_file_path: Path = Path(
        "/var/lib/pyntara/port_forwarding_state.json"
    ),
    port_forwarding_service_unit_name: str = "auto_port_forwarding.service",
    port_forwarding_service_restart_seconds: int = 30,
    port_forwarding_journal_identifier: str = "auto_port_forwarding",
    port_forwarding_error_priority: int = 3,
    vault_entries: tuple[tuple[str, str], ...] = (
        ("password_salt", "Salt for deterministic password derivation."),
        ("pyntara_local_vault_password", "Password for the runtime secret vault."),
        ("telegram_bot_token", "Telegram bot token for System Metrics."),
        ("google_script_key", "Google Drive web app credentials for System Metrics."),
    ),
    tasks: tuple[TaskConfig, ...] = (),
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return Config(
        engine=EngineConfig(
            task_data_root=task_data_root,
            notice_timeout=notice_timeout,
            command_timeout_seconds=command_timeout_seconds,
            curl_timeout_seconds=curl_timeout_seconds,
            curl_retries=curl_retries,
            error_priority=error_priority,
            progress_priority=progress_priority,
            process_check_timeout_seconds=process_check_timeout_seconds,
            task_start_delay_seconds=task_start_delay_seconds,
            desktop_detect_processes=engine_desktop_detect_processes,
        ),
        cli_tools=CliToolsConfig(
            packages=cli_tools_packages,
            package_status_timeout_seconds=cli_tools_status_timeout,
            package_install_retries=cli_tools_retries,
            package_success_threshold_percent=cli_tools_threshold,
        ),
        imagemagick_setup=ImagemagickSetupConfig(
            packages=imagemagick_setup_packages,
            policy_path=imagemagick_setup_policy_path,
            package_status_timeout_seconds=imagemagick_setup_status_timeout,
            package_install_retries=imagemagick_setup_retries,
        ),

        ffmpeg_setup=FfmpegSetupConfig(
            packages=ffmpeg_setup_packages,
            wayrecord_bin_path=ffmpeg_setup_wayrecord_bin_path,
            wayrecord_desktop_path=ffmpeg_setup_wayrecord_desktop_path,
            package_status_timeout_seconds=ffmpeg_setup_status_timeout,
            package_install_retries=ffmpeg_setup_retries,
        ),

        dnsproxy_setup=DnsproxySetupConfig(
            github_repo="AdguardTeam/dnsproxy",
            download_dir=dnsproxy_download_dir,
            binary_path=dnsproxy_binary_path,
            service_unit_name=dnsproxy_service_unit_name,
            service_unit_path=dnsproxy_service_unit_path,
            service_template_path=dnsproxy_service_template_path,
            listen_addresses=dnsproxy_listen_addresses,
            listen_port=dnsproxy_listen_port,
            doh_url_format="https://dns.nextdns.io/{profile_id}",
            dot_host_format="tls://{profile_id}.dns.nextdns.io",
            doq_host_format="quic://{profile_id}.dns.nextdns.io",
            upstream_mode="load_balance",
            cache_enabled=True,
            cache_size_bytes=dnsproxy_cache_size_bytes,
            bootstrap_resolvers=("1.1.1.1", "2606:4700:4700::1111"),
            append_provider_dns=True,
            query_log_path=dnsproxy_query_log_path,
            query_log_mode=0o600,
            service_restart_seconds=2.0,
            install_retries=3,
            start_check_attempts=5,
            start_check_retry_delay_seconds=1.0,
            resolved_conf_dir=dnsproxy_resolved_conf_dir,
            resolved_dropin_file_name="pyntara-dnsproxy.conf",
            resolved_dropin_file_mode=0o644,
            resolved_dropin_header="# Managed by the Pyntara dnsproxy_setup task.",
            resolved_section="[Resolve]",
            resolved_dns_directives=("DNS=127.0.0.1:53053", "DNS=[::1]:53053"),
            resolved_domains_directive="Domains=~.",
            manage_networkmanager=True,
            nmcli_check_command=("nmcli", "--version"),
            nmcli_active_list_command=(
                "nmcli",
                "-t",
                "-f",
                "NAME,UUID,DEVICE",
                "connection",
                "show",
                "--active",
            ),
            nmcli_dns_state_command=(
                "nmcli",
                "-t",
                "-f",
                "ipv4.ignore-auto-dns,ipv6.ignore-auto-dns",
                "connection",
                "show",
                "{connection}",
            ),
            nmcli_modify_command=("nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"),
            nmcli_reapply_command=("nmcli", "device", "reapply", "{device}"),
            daemon_reload_command=("systemctl", "daemon-reload"),
            restart_resolved_command=("systemctl", "restart", "systemd-resolved"),
            resolvectl_status_command=("resolvectl", "status"),
            resolvectl_dns_command=("resolvectl", "dns"),
            nmcli_dns_command=("nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"),
            verification_domain="example.com",
            verification_command=("resolvectl", "query", "--cache=no", "{domain}"),
            ss_tcp_listen_command=("ss", "-lntp"),
            ss_udp_listen_command=("ss", "-lunp"),
            kill_command=("kill",),
            service_log_command=("journalctl", "-u", "{unit}", "--no-pager", "-n", "20"),
            profile_id_file_path=dnsproxy_profile_id_file_path,
            profile_id_file_mode=0o644,
        ),
        add_extra_repos=AddExtraReposConfig(
            components=add_extra_repos_components,
            ubuntu_hosts=add_extra_repos_ubuntu_hosts,
        ),
        hostname=HostnameConfig(
            hostname_file=str(hostname_file),
            set_hostname_command=hostname_set_hostname_command,
        ),
        kde_keyboard_setup=KdeKeyboardSetupConfig(
            packages=kde_keyboard_setup_packages,
            username=kde_keyboard_setup_username,
            home_dir=kde_keyboard_setup_home_dir,
            config_dir=kde_keyboard_setup_config_dir,
            kxkbrc_file_name=kde_keyboard_setup_kxkbrc_file_name,
            appletsrc_file_name=kde_keyboard_setup_appletsrc_file_name,
            applet_plugin=kde_keyboard_setup_applet_plugin,
            layouts=kde_keyboard_setup_layouts,
            switch_option=kde_keyboard_setup_switch_option,
            reset_old_options=kde_keyboard_setup_reset_old_options,
            switch_mode=kde_keyboard_setup_switch_mode,
            use_layout_switching=kde_keyboard_setup_use_layout_switching,
            indicator_display_style=kde_keyboard_setup_indicator_display_style,
            kwin_reload_command=kde_keyboard_setup_kwin_reload_command,
            panel_restart_command=kde_keyboard_setup_panel_restart_command,
            layout_switch_shortcuts=(
                kde_keyboard_setup_layout_switch_shortcuts
                if kde_keyboard_setup_layout_switch_shortcuts is not None
                else {}
            ),
        ),
        kde_settings=KdeSettingsConfig(
            packages=kde_settings_packages,
            username=kde_settings_username,
            home_dir=kde_settings_home_dir,
            user_dirs=(
                kde_settings_user_dirs
                if kde_settings_user_dirs is not None
                else {
                    "XDG_DOCUMENTS_DIR": "$HOME/Downloads",
                    "XDG_MUSIC_DIR": "$HOME/Downloads",
                    "XDG_PICTURES_DIR": "$HOME/Downloads",
                    "XDG_PUBLICSHARE_DIR": "$HOME/Downloads",
                    "XDG_TEMPLATES_DIR": "$HOME/Downloads",
                    "XDG_VIDEOS_DIR": "$HOME/Downloads",
                }
            ),
            color_scheme=kde_settings_color_scheme,
            look_and_feel=kde_settings_look_and_feel,
            look_and_feel_light=kde_settings_look_and_feel_light,
            automatic_look_and_feel=kde_settings_automatic_look_and_feel,
            cursor_theme=kde_settings_cursor_theme,
            cursor_theme_light=kde_settings_cursor_theme_light,
            numlock_on_boot=kde_settings_numlock_on_boot,
            touchpad_click_method=kde_settings_touchpad_click_method,
            touchpad_disable_on_external_mouse=kde_settings_touchpad_disable_on_external_mouse,
            virtual_keyboard_enabled=kde_settings_virtual_keyboard_enabled,
            virtual_keyboard_input_method=kde_settings_virtual_keyboard_input_method,
            virtual_keyboard_locales=kde_settings_virtual_keyboard_locales,
            kwin_reload_command=kde_settings_kwin_reload_command,
            sddm_autologin_user=kde_settings_sddm_autologin_user,
            sddm_autologin_session=kde_settings_sddm_autologin_session,
            sddm_theme=kde_settings_sddm_theme,
            sddm_theme_cursor_size=kde_settings_sddm_theme_cursor_size,
            sddm_theme_cursor_theme=kde_settings_sddm_theme_cursor_theme,
            sddm_theme_font=kde_settings_sddm_theme_font,
            places_hidden=kde_settings_places_hidden,
            kconfig=kde_settings_kconfig,
        ),
        swapfile_service_install=SwapfileServiceInstallConfig(
            swapfile_path=swapfile_path,
            ram_multiplier=swapfile_ram_multiplier,
            ram_extra_mb=swapfile_ram_extra_mb,
            disk_fraction=swapfile_disk_fraction,
            swapfile_mode=swapfile_mode,
            size_tolerance_mb=swapfile_size_tolerance_mb,
            service_unit_name=swapfile_service_unit_name,
        ),
        zswap_service=ZswapServiceConfig(
            enabled=zswap_enabled,
            compressor=zswap_compressor,
            max_pool_percent=zswap_max_pool_percent,
            accept_threshold_percent=zswap_accept_threshold_percent,
            shrinker_enabled=zswap_shrinker_enabled,
            service_unit_name=zswap_service_unit_name,
        ),
        zram_service=ZramServiceConfig(
            compressor=zram_compressor,
            swap_priority=zram_swap_priority,
            memory_fraction_percent=zram_memory_fraction_percent,
            fallback_cpu_count=zram_fallback_cpu_count,
            alignment_bytes=zram_alignment_bytes,
            service_unit_name=zram_service_unit_name,
            reset_busy_attempts=zram_reset_busy_attempts,
            reset_busy_retry_delay_seconds=zram_reset_busy_retry_delay_seconds,
        ),
        i2pd_service_setup=I2pdServiceSetupConfig(
            github_repo=i2pd_github_repo,
            download_dir=i2pd_download_dir,
            service_unit_name=i2pd_service_unit_name,
            config_path=i2pd_config_path,
            log_level=i2pd_log_level,
            bandwidth=i2pd_bandwidth,
            share=i2pd_share,
            http_enabled=i2pd_http_enabled,
            socks_proxy_enabled=i2pd_socks_proxy_enabled,
            install_retries=i2pd_install_retries,
            start_check_attempts=i2pd_start_check_attempts,
            start_check_retry_delay_seconds=i2pd_start_check_retry_delay_seconds,
            tunnels_config_path=i2pd_tunnels_config_path,
            tunnel_name=i2pd_tunnel_name,
            tunnel_host=i2pd_tunnel_host,
            tunnel_keys_path=i2pd_tunnel_keys_path,
            address_file_path=i2pd_address_file_path,
            address_file_mode=i2pd_address_file_mode,
        ),
        three_x_ui_xray_setup=ThreeXuiXraySetupConfig(
            github_repo=three_x_ui_github_repo,
            install_script_url=three_x_ui_install_script_url,
            install_dir=three_x_ui_install_dir,
            service_unit_name=three_x_ui_service_unit_name,
            start_check_attempts=three_x_ui_start_check_attempts,
            start_check_retry_delay_seconds=three_x_ui_start_check_retry_delay_seconds,
            install_result_env_path=three_x_ui_install_result_env_path,
            panel_port=three_x_ui_panel_port,
            ssl_enabled=three_x_ui_ssl_enabled,
            panel_http_address=three_x_ui_panel_http_address,
            vault_entry_title=three_x_ui_vault_entry_title,
            inbound_port=three_x_ui_inbound_port,
            inbound_remark=three_x_ui_inbound_remark,
            reality_dest=three_x_ui_reality_dest,
            reality_server_names=three_x_ui_reality_server_names,
            reality_short_id=three_x_ui_reality_short_id,
            acme_port=three_x_ui_acme_port,
            cert_dir=three_x_ui_cert_dir,
            cert_fullchain=three_x_ui_cert_dir / "fullchain.pem",
            cert_privkey=three_x_ui_cert_dir / "privkey.pem",
            self_signed_cert_dir=three_x_ui_self_signed_cert_dir,
            self_signed_cert_fullchain=(
                three_x_ui_self_signed_cert_dir / "fullchain.pem"
            ),
            self_signed_cert_privkey=(
                three_x_ui_self_signed_cert_dir / "privkey.pem"
            ),
            server_ip_services=three_x_ui_server_ip_services,
        ),
        yggdrasil_service_setup=YggdrasilServiceSetupConfig(
            github_repo=yggdrasil_github_repo,
            download_dir=yggdrasil_download_dir,
            service_unit_name=yggdrasil_service_unit_name,
            install_retries=yggdrasil_install_retries,
            config_path=yggdrasil_config_path,
            private_key_path=yggdrasil_private_key_path,
            config_file_mode=yggdrasil_config_file_mode,
            private_key_file_mode=yggdrasil_private_key_file_mode,
            if_name=yggdrasil_if_name,
            if_mtu=yggdrasil_if_mtu,
            admin_listen=yggdrasil_admin_listen,
            listen=yggdrasil_listen,
            multicast_interfaces=yggdrasil_multicast_interfaces,
            peers_full_path=yggdrasil_peers_full_path,
            peers_tarball_url=yggdrasil_peers_tarball_url,
            peer_batch_size=yggdrasil_peer_batch_size,
            peer_target_count=yggdrasil_peer_target_count,
            peer_probe_timeout_seconds=yggdrasil_peer_probe_timeout_seconds,
            peer_max_batches=yggdrasil_peer_max_batches,
            static_peers=yggdrasil_static_peers,
            address_file_path=yggdrasil_address_file_path,
            address_file_mode=yggdrasil_address_file_mode,
            address_save_retry_base_seconds=yggdrasil_address_save_retry_base_seconds,
            address_save_retry_multiplier=yggdrasil_address_save_retry_multiplier,
            address_save_retry_max_seconds=yggdrasil_address_save_retry_max_seconds,
            connection_wait_base_seconds=yggdrasil_connection_wait_base_seconds,
            connection_wait_multiplier=yggdrasil_connection_wait_multiplier,
            connection_wait_max_seconds=yggdrasil_connection_wait_max_seconds,
        ),
        tor_setup=TorSetupConfig(
            package_name=tor_package_name,
            service_unit_name=tor_service_unit_name,
            torrc_path=tor_torrc_path,
            torrc_dropin_path=tor_torrc_dropin_path,
            torrc_include_path=tor_torrc_include_path,
            dropin_file_mode=tor_dropin_file_mode,
            hidden_service_dir=tor_hidden_service_dir,
            hidden_service_dir_mode=tor_hidden_service_dir_mode,
            tor_user=tor_user,
            socks_port=tor_socks_port,
            onion_ssh_port=tor_onion_ssh_port,
            num_introduction_points=tor_num_introduction_points,
            log_level=tor_log_level,
            install_retries=tor_install_retries,
            start_check_attempts=tor_start_check_attempts,
            start_check_retry_delay_seconds=tor_start_check_retry_delay_seconds,
            address_file_path=tor_address_file_path,
            address_file_mode=tor_address_file_mode,
        ),
        ssh_daemon_setup=SshDaemonSetupConfig(
            package_name=ssh_daemon_package_name,
            package_status_timeout_seconds=ssh_daemon_package_status_timeout_seconds,
            install_retries=ssh_daemon_install_retries,
            service_unit_name=ssh_daemon_service_unit_name,
            socket_unit_name=ssh_daemon_socket_unit_name,
            start_check_attempts=ssh_daemon_start_check_attempts,
            start_check_retry_delay_seconds=ssh_daemon_start_check_retry_delay_seconds,
            sshd_config_path=ssh_daemon_sshd_config_path,
            sshd_config_dropin_path=ssh_daemon_sshd_config_dropin_path,
            dropin_file_mode=ssh_daemon_dropin_file_mode,
            private_key_file_name=ssh_daemon_private_key_file_name,
            public_key_file_name=ssh_daemon_public_key_file_name,
            port_forwarding_private_key_file_name=(
                ssh_daemon_port_forwarding_private_key_file_name
            ),
            port_forwarding_public_key_file_name=(
                ssh_daemon_port_forwarding_public_key_file_name
            ),
            port_forwarding_authorized_keys_options=(
                ssh_daemon_port_forwarding_authorized_keys_options
            ),
            private_key_file_mode=ssh_daemon_private_key_file_mode,
            public_key_file_mode=ssh_daemon_public_key_file_mode,
            authorized_keys_file_mode=ssh_daemon_authorized_keys_file_mode,
            ssh_dir_mode=ssh_daemon_ssh_dir_mode,
            root_ssh_dir=ssh_daemon_root_ssh_dir,
            users=ssh_daemon_users,
            directives=ssh_daemon_directives,
        ),
        ssh_client_setup=SshClientSetupConfig(
            ssh_config_path=ssh_client_ssh_config_path,
            ssh_config_dropin_path=ssh_client_ssh_config_dropin_path,
            dropin_file_mode=ssh_client_dropin_file_mode,
            directives=ssh_client_directives,
        ),
        system_metrics_setup=SystemMetricsSetupConfig(
            backoff_base_seconds=system_metrics_backoff_base_seconds,
            backoff_multiplier=system_metrics_backoff_multiplier,
            backoff_max_seconds=system_metrics_backoff_max_seconds,
            python_version=system_metrics_python_version,
            error_priority=system_metrics_error_priority,
            venv_dir=system_metrics_venv_dir,
            system_config_path=system_metrics_system_config_path,
            command_path=system_metrics_command_path,
            vault_backup_file_name=system_metrics_vault_backup_file_name,
            system_metrics_dir=system_metrics_dir,
            system_metrics_dir_mode=system_metrics_dir_mode,
            queue_file_mode=system_metrics_queue_file_mode,
            max_queue_file_size_bytes=system_metrics_max_queue_file_size_bytes,
            send_order=system_metrics_send_order,
            queue_file_suffix_length=system_metrics_queue_file_suffix_length,
            spool_dir=system_metrics_spool_dir,
            spool_dir_mode=system_metrics_spool_dir_mode,
            command_file_mode=system_metrics_command_file_mode,
            service_unit_name=system_metrics_service_unit_name,
            ingest_service_unit_name=system_metrics_ingest_service_unit_name,
            ingest_path_unit_name=system_metrics_ingest_path_unit_name,
            service_journal_identifier=system_metrics_service_journal_identifier,
            commit_journal_identifier=system_metrics_commit_journal_identifier,
            main_outbox_dir=system_metrics_main_outbox_dir,
            temp_dir=system_metrics_temp_dir,
            spool_temp_prefix=system_metrics_spool_temp_prefix,
            queue_link_attempts=system_metrics_queue_link_attempts,
            google_script_dir=system_metrics_google_script_dir,
            main_sent_dir=system_metrics_main_sent_dir,
            google_script_timeout_seconds=system_metrics_google_script_timeout_seconds,
            google_script_key_entry_title=system_metrics_google_script_key_entry_title,
            google_script_deployment_url_regex=(
                system_metrics_google_script_deployment_url_regex
            ),
            collector=SystemMetricsCollectorConfig(
                boot_delay_seconds=system_metrics_collector_boot_delay_seconds,
                daily_send_time=system_metrics_collector_daily_send_time,
                threshold_percent=system_metrics_collector_threshold_percent,
                retry_base_seconds=system_metrics_collector_retry_base_seconds,
                retry_multiplier=system_metrics_collector_retry_multiplier,
                retry_max_seconds=system_metrics_collector_retry_max_seconds,
                command_timeout_seconds=system_metrics_collector_command_timeout_seconds,
                service_unit_name=system_metrics_collector_service_unit_name,
                timer_unit_name=system_metrics_collector_timer_unit_name,
                journal_identifier=system_metrics_collector_journal_identifier,
                lock_file_path=system_metrics_collector_lock_file_path,
                report_file_name=system_metrics_collector_report_file_name,
                network_modules=system_metrics_collector_network_modules,
                system_modules=system_metrics_collector_system_modules,
            ),
        ),
        vault_structure=VaultStructureConfig(
            entries=tuple(
                VaultEntry(title=title, notes=notes)
                for title, notes in vault_entries
            )
        ),
        nextdns_setup_system_wide=NextdnsSetupSystemWideConfig(
            vault_group_title=nextdns_vault_group_title,
            profile_id_file_path=nextdns_profile_id_file_path,
            profile_id_file_mode=nextdns_profile_id_file_mode,
            error_priority=nextdns_error_priority,
        ),
        port_forwarding_setup=PortForwardingSetupConfig(
            vault_group_title=port_forwarding_vault_group_title,
            passphrase_entry_title=port_forwarding_passphrase_entry_title,
            remote_ssh_user=port_forwarding_remote_ssh_user,
            desired_port_min=port_forwarding_desired_port_min,
            desired_port_max=port_forwarding_desired_port_max,
            server_alive_interval_seconds=(
                port_forwarding_server_alive_interval_seconds
            ),
            server_alive_count_max=port_forwarding_server_alive_count_max,
            connect_timeout_seconds=port_forwarding_connect_timeout_seconds,
            backoff_base_seconds=port_forwarding_backoff_base_seconds,
            backoff_multiplier=port_forwarding_backoff_multiplier,
            backoff_max_seconds=port_forwarding_backoff_max_seconds,
            state_file_path=port_forwarding_state_file_path,
            service_unit_name=port_forwarding_service_unit_name,
            service_restart_seconds=port_forwarding_service_restart_seconds,
            journal_identifier=port_forwarding_journal_identifier,
            error_priority=port_forwarding_error_priority,
        ),
        rustdesk_setup=RustdeskSetupConfig(
            github_repo=rustdesk_github_repo,
            download_dir=rustdesk_download_dir,
            id_file_path=rustdesk_id_file_path,
            id_file_mode=rustdesk_id_file_mode,
            vault_entry_title=rustdesk_vault_entry_title,
            service_unit_name=rustdesk_service_unit_name,
            password_words=rustdesk_password_words,
            password_separator=rustdesk_password_separator,
            config_dir=rustdesk_config_dir,
            install_timeout_seconds=rustdesk_install_timeout_seconds,
            apt_update_timeout_seconds=rustdesk_apt_update_timeout_seconds,
            install_retries=rustdesk_install_retries,
            start_check_attempts=rustdesk_start_check_attempts,
            start_check_retry_delay_seconds=(
                rustdesk_start_check_retry_delay_seconds
            ),
            options=rustdesk_options,
        ),
        local_vault_setup=LocalVaultSetupConfig(
            source_vault_production=local_vault_source_production,
            source_vault_default=local_vault_source_default,
            local_vault_path=local_vault_path,
            pass_file_path=local_vault_pass_file_path,
            vault_password_entry_title=local_vault_entry_title,
            secrets_dir_mode=local_vault_secrets_dir_mode,
            local_vault_file_mode=local_vault_file_mode,
            pass_dir_mode=local_vault_pass_dir_mode,
            pass_file_mode=local_vault_pass_file_mode,
            error_priority=local_vault_error_priority,
        ),
        tasks=tasks,
    )


def make_context(
    *,
    install_mode: str = "minimal",
    vault_password: str | None = None,
    vault_source: str | None = None,
    force_tasks: frozenset[str] = frozenset(),
    task_data_root: Path = Path("/tmp"),
    skip_apt_update: bool = False,
    config: Config | None = None,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return Context(
        install_mode=install_mode,
        vault_password=vault_password,
        vault_source=vault_source,
        force_tasks=force_tasks,
        task_data_root=task_data_root,
        skip_apt_update=skip_apt_update,
        config=config if config is not None else make_config(task_data_root=task_data_root),
    )


# Fixture PrivateKeys record shared by the i2pd tests (the decoder, the
# address command and the task): the 387-byte IdentityEx (256-byte
# encryption key, 128-byte signing key, 3-byte certificate) with a KEY
# certificate carrying the 4-byte extended block of the signing and
# crypto key types, followed by private material. The expected address
# is the unpadded lowercase base32 of the SHA-256 of the IdentityEx,
# computed independently from the same parts.
I2PD_KEYS_IDENTITY_SIZE = 387
I2PD_KEYS_CERTIFICATE_TYPE_KEY = 5
I2PD_KEYS_EXTENDED_BYTES = b"\x00\x07\x00\x04"  # signing type 7, crypto type 4


def i2pd_keys_file_bytes() -> bytes:
    """The fixture PrivateKeys record for the i2pd tests."""

    identity = bytearray(I2PD_KEYS_IDENTITY_SIZE)
    identity[I2PD_KEYS_IDENTITY_SIZE - 3] = I2PD_KEYS_CERTIFICATE_TYPE_KEY
    identity[I2PD_KEYS_IDENTITY_SIZE - 2] = 0
    identity[I2PD_KEYS_IDENTITY_SIZE - 1] = len(I2PD_KEYS_EXTENDED_BYTES)
    return bytes(identity) + I2PD_KEYS_EXTENDED_BYTES + b"private material"


def i2pd_keys_b32_address() -> str:
    """The expected .b32.i2p address of the fixture keys record."""

    identity_len = I2PD_KEYS_IDENTITY_SIZE + len(I2PD_KEYS_EXTENDED_BYTES)
    digest = hashlib.sha256(i2pd_keys_file_bytes()[:identity_len]).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{encoded}.b32.i2p"
