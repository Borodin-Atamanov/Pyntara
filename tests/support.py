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
import tempfile
from dataclasses import replace
from pathlib import Path

from config_helpers import base_config

from pyntara.config import (
    CollectorModuleConfig,
    Config,
    KConfigRecord,
    RustdeskOptionConfig,
    SshDirective,
    TaskConfig,
    load_config,
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


_BASE_CONFIG: Config | None = None


def _base_config() -> Config:
    """Return the Config parsed from the shared test document.

    The document is the single form of the test configuration and goes
    through the same loader as the deployed config, so its cross-checks
    hold here exactly as they do on the target machine. A section field
    added to the real config therefore reaches the tests without an edit
    in this module. The parse runs once per test session.
    """

    global _BASE_CONFIG
    if _BASE_CONFIG is None:
        with tempfile.TemporaryDirectory() as directory:
            document_path = Path(directory) / "config.toml"
            document_path.write_text(base_config(), encoding="utf-8")
            _BASE_CONFIG = load_config(document_path)
    return _BASE_CONFIG


def make_config(
    *,
    task_data_root: Path = Path("/tmp"),
    systemd_unit_dir: Path = Path("/etc/systemd/system"),
    notice_timeout: int = 7,
    command_timeout_seconds: int = 8000,
    curl_timeout_seconds: int = 777,
    curl_download_timeout_seconds: int = 7777,
    curl_retries: int = 17,
    curl_retry_delay_seconds: int = 3,
    curl_connect_timeout_seconds: int = 60,
    curl_retry_max_time_seconds: int = 7777,
    github_latest_release_url: str = (
        "https://api.github.com/repos/{repo}/releases/latest"
    ),
    journal_identifier: str = "pyntara-engine",
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

    imagemagick_setup_packages: tuple[str, ...] = ("imagemagick",),
    imagemagick_setup_policy_path: Path = Path("/etc/ImageMagick-7/policy.xml"),

    ffmpeg_setup_packages: tuple[str, ...] = ("ffmpeg",),
    ffmpeg_setup_wayrecord_bin_path: Path = Path(
        "/usr/local/bin/pyntara-wayrecord"
    ),
    ffmpeg_setup_wayrecord_desktop_path: Path = Path(
        "/usr/share/applications/pyntara-wayrecord.desktop"
    ),

    playwright_setup_home_dir: str = "/home/i",

    dnsproxy_download_dir: Path = Path("/tmp/dnsproxy"),
    dnsproxy_binary_path: Path = Path("/usr/local/bin/dnsproxy"),
    dnsproxy_service_unit_path: Path = Path("/etc/systemd/system/dnsproxy.service"),
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
    add_extra_repos_keep_downloaded_debs: bool = True,
    add_extra_repos_legacy_sources_file: Path = Path("/etc/apt/sources.list"),
    add_extra_repos_sources_list_d: Path = Path("/etc/apt/sources.list.d"),
    add_extra_repos_keep_debs_file: Path = Path(
        "/etc/apt/apt.conf.d/99keep-debs.conf"
    ),
    hostname_file: Path = Path("/etc/hostname"),
    hostname_set_hostname_command: tuple[str, ...] = (
        "hostnamectl",
        "set-hostname",
    ),
    kde_keyboard_setup_username: str = "i",
    kde_keyboard_setup_home_dir: str = "/home/i",
    kde_keyboard_setup_config_dir: str = "/home/i/.config",
    kde_keyboard_setup_layout_switch_shortcuts: dict[str, str] | None = None,
    kde_settings_packages: tuple[str, ...] = (
        "plasma-workspace",
        "libkf6config-bin",
        "kubuntu-settings-desktop",
        "python3-dbus",
    ),
    kde_settings_home_dir: str = "/home/i",
    kde_settings_system_look_and_feel_dir: Path = Path(
        "/usr/share/plasma/look-and-feel"
    ),
    kde_settings_automatic_look_and_feel: bool = False,
    kde_settings_touchpad_click_method: str = "clickfinger",
    kde_settings_virtual_keyboard_enabled: bool = True,
    kde_settings_kconfig: tuple[KConfigRecord, ...] = (),
    kde_settings_places_hidden: tuple[str, ...] = (),
    swapfile_path: Path = Path("/swapfile"),
    swapfile_ram_multiplier: float = 2.0,
    swapfile_mode: int = 0o600,
    zram_reset_busy_attempts: int = 5,
    zram_reset_busy_retry_delay_seconds: float = 0.5,
    i2pd_download_dir: Path = Path("/var/lib/pyntara/i2pd-download"),
    i2pd_os_release_file_path: Path = Path("/etc/os-release"),
    i2pd_config_path: Path = Path("/etc/i2pd/i2pd.conf"),
    i2pd_install_retries: int = 3,
    i2pd_start_check_attempts: int = 5,
    i2pd_start_check_retry_delay_seconds: float = 0.0,
    i2pd_tunnels_config_path: Path = Path("/etc/i2pd/tunnels.conf"),
    i2pd_tunnel_keys_path: Path = Path("/var/lib/i2pd/ssh.dat"),
    i2pd_address_file_path: Path = Path("/var/lib/pyntara/i2pd_ssh_address"),
    three_x_ui_install_dir: Path = Path("/usr/local/x-ui"),
    three_x_ui_start_check_attempts: int = 10,
    three_x_ui_start_check_retry_delay_seconds: int = 1,
    three_x_ui_install_result_env_path: Path = Path("/etc/x-ui/install-result.env"),
    three_x_ui_ssl_enabled: bool = True,
    three_x_ui_cert_dir: Path = Path("/root/cert/ip"),
    three_x_ui_self_signed_cert_dir: Path = Path("/root/cert/selfsigned"),
    three_x_ui_probe_timeout_seconds: int = 60,
    three_x_ui_probe_port_80_timeout_seconds: int = 10,
    three_x_ui_probe_listener_start_seconds: int = 1,
    three_x_ui_upnp_enabled: bool = True,
    three_x_ui_upnp_package: str = "miniupnpc",
    rustdesk_download_dir: Path = Path("/var/cache/pyntara/rustdesk"),
    rustdesk_id_file_path: Path = Path("/var/lib/pyntara/rustdesk_id"),
    rustdesk_config_dir: Path = Path("/home/i/.config/rustdesk"),
    rustdesk_options: tuple[RustdeskOptionConfig, ...] = (
        RustdeskOptionConfig(key="enable-udp-punch", value="Y"),
    ),
    telegram_home_dir: str = "/home/i",
    telegram_download_dir: Path = Path("/var/cache/pyntara/telegram"),
    chrome_home_dir: str = "/home/i",
    chrome_settings_dir: Path = Path("/var/cache/pyntara/chromium-settings"),
    chrome_system_root: Path = Path("/"),
    chrome_apt_source_path: Path = Path(
        "/etc/apt/sources.list.d/google-chrome.sources"
    ),
    chrome_keyring_path: Path = Path("/usr/share/keyrings/google-chrome.gpg"),
    chrome_desktop_source_path: Path = Path(
        "/usr/share/applications/google-chrome.desktop"
    ),
    chrome_desktop_override_path: Path = Path(
        "/usr/local/share/applications/google-chrome.desktop"
    ),
    chrome_profile_mirror_path: Path = Path("/home/i/.config/google-chrome-cdp"),
    vocalinux_home_dir: str = "/home/i",
    vocalinux_download_dir: Path = Path("/var/cache/pyntara/vocalinux"),
    yggdrasil_download_dir: Path = Path("/var/lib/pyntara/yggdrasil-download"),
    yggdrasil_install_retries: int = 3,
    yggdrasil_config_path: Path = Path("/etc/yggdrasil/yggdrasil.conf"),
    yggdrasil_private_key_path: Path = Path("/etc/yggdrasil/private-key.pem"),
    yggdrasil_listen: tuple[str, ...] = (
        "tcp://[::]:0",
        "tls://[::]:0",
        "quic://[::]:0",
        "ws://[::]:0",
    ),
    yggdrasil_peers_full_path: Path = Path("/etc/yggdrasil/peers-full.txt"),
    yggdrasil_peer_batch_size: int = 100,
    yggdrasil_peer_target_count: int = 6,
    yggdrasil_peer_probe_timeout_seconds: float = 0.0,
    yggdrasil_static_peers: tuple[str, ...] = (),
    yggdrasil_address_file_path: Path = Path("/var/lib/pyntara/yggdrasil_self_address"),
    yggdrasil_address_save_retry_base_seconds: int = 1,
    yggdrasil_address_save_retry_multiplier: int = 2,
    yggdrasil_address_save_retry_max_seconds: int = 1,
    yggdrasil_connection_wait_base_seconds: int = 1,
    yggdrasil_connection_wait_multiplier: int = 2,
    yggdrasil_connection_wait_max_seconds: int = 1,
    yggdrasil_nm_unmanaged_conf_path: Path = Path(
        "/etc/NetworkManager/conf.d/yggdrasil-unmanaged.conf"
    ),
    yggdrasil_netplan_dir_path: Path = Path("/etc/netplan"),
    tor_torrc_path: Path = Path("/etc/tor/torrc"),
    tor_torrc_dropin_path: Path = Path("/etc/tor/pyntara.conf"),
    tor_torrc_include_path: str = "/etc/tor/pyntara.conf",
    tor_hidden_service_dir: Path = Path("/var/lib/tor/ssh"),
    tor_user: str = "debian-tor",
    tor_install_retries: int = 3,
    tor_start_check_attempts: int = 5,
    tor_start_check_retry_delay_seconds: float = 0.0,
    tor_address_file_path: Path = Path("/var/lib/pyntara/tor_ssh_address"),
    ssh_daemon_start_check_retry_delay_seconds: float = 0.0,
    ssh_daemon_sshd_config_path: Path = Path("/etc/ssh/sshd_config"),
    ssh_daemon_sshd_config_dropin_path: Path = Path(
        "/etc/ssh/sshd_config.d/pyntara.conf"
    ),
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
    ssh_client_directives: tuple[SshDirective, ...] = (SshDirective(
        name="AddressFamily", value="any"
    ),),
    system_metrics_backoff_base_seconds: int = 2,
    system_metrics_backoff_multiplier: int = 2,
    system_metrics_backoff_max_seconds: int = 14400,
    system_metrics_venv_dir: Path = Path("/usr/local/lib/pyntara/venv"),
    system_metrics_system_config_path: Path = Path("/etc/pyntara/config.toml"),
    system_metrics_command_path: Path = Path("/usr/local/bin/commit_system_metrics"),
    system_metrics_dir: Path = Path("/var/lib/pyntara/metrics"),
    system_metrics_dir_mode: int = 0o700,
    system_metrics_max_queue_file_size_bytes: int = 104857600,
    system_metrics_send_order: str = "oldest_first",
    system_metrics_spool_dir: Path = Path("/var/spool/system_metrics"),
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
    local_vault_file_mode: int = 0o640,
    nextdns_vault_group_title: str = "NextDNS",
    nextdns_profile_id_file_path: Path = Path("/var/lib/pyntara/nextdns_profile_id"),
    nextdns_profile_id_file_mode: int = 0o644,
    nextdns_error_priority: int = 3,
    port_forwarding_connect_timeout_seconds: int = 31,
    port_forwarding_state_file_path: Path = Path(
        "/var/lib/pyntara/port_forwarding_state.json"
    ),
    tasks: tuple[TaskConfig, ...] = (),
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    base = _base_config()
    return replace(
        base,
        engine=replace(
            base.engine,
            task_data_root=task_data_root,
            systemd_unit_dir=systemd_unit_dir,
            notice_timeout=notice_timeout,
            command_timeout_seconds=command_timeout_seconds,
            curl_timeout_seconds=curl_timeout_seconds,
            curl_download_timeout_seconds=curl_download_timeout_seconds,
            curl_retries=curl_retries,
            curl_retry_delay_seconds=curl_retry_delay_seconds,
            curl_connect_timeout_seconds=curl_connect_timeout_seconds,
            curl_retry_max_time_seconds=curl_retry_max_time_seconds,
            github_latest_release_url=github_latest_release_url,
            journal_identifier=journal_identifier,
            error_priority=error_priority,
            progress_priority=progress_priority,
            process_check_timeout_seconds=process_check_timeout_seconds,
            task_start_delay_seconds=task_start_delay_seconds,
            desktop_detect_processes=engine_desktop_detect_processes,
        ),
        cli_tools=replace(
            base.cli_tools,
            packages=cli_tools_packages,
            package_install_retries=cli_tools_retries,
            package_success_threshold_percent=cli_tools_threshold,
        ),
        imagemagick_setup=replace(
            base.imagemagick_setup,
            packages=imagemagick_setup_packages,
            policy_path=imagemagick_setup_policy_path,
        ),

        ffmpeg_setup=replace(
            base.ffmpeg_setup,
            packages=ffmpeg_setup_packages,
            wayrecord_bin_path=ffmpeg_setup_wayrecord_bin_path,
            wayrecord_desktop_path=ffmpeg_setup_wayrecord_desktop_path,
        ),

        dnsproxy_setup=replace(
            base.dnsproxy_setup,
            download_dir=dnsproxy_download_dir,
            binary_path=dnsproxy_binary_path,
            service_unit_path=dnsproxy_service_unit_path,
            resolved_conf_dir=dnsproxy_resolved_conf_dir,
            profile_id_file_path=dnsproxy_profile_id_file_path,
        ),
        add_extra_repos=replace(
            base.add_extra_repos,
            components=add_extra_repos_components,
            ubuntu_hosts=add_extra_repos_ubuntu_hosts,
            keep_downloaded_debs=add_extra_repos_keep_downloaded_debs,
            legacy_sources_file=add_extra_repos_legacy_sources_file,
            sources_list_d=add_extra_repos_sources_list_d,
            keep_debs_file=add_extra_repos_keep_debs_file,
        ),
        hostname=replace(
            base.hostname,
            hostname_file=str(hostname_file),
            set_hostname_command=hostname_set_hostname_command,
        ),
        kde_keyboard_setup=replace(
            base.kde_keyboard_setup,
            username=kde_keyboard_setup_username,
            home_dir=kde_keyboard_setup_home_dir,
            config_dir=kde_keyboard_setup_config_dir,
            layout_switch_shortcuts=(
                kde_keyboard_setup_layout_switch_shortcuts
                if kde_keyboard_setup_layout_switch_shortcuts is not None
                else {}
            ),
        ),
        kde_settings=replace(
            base.kde_settings,
            packages=kde_settings_packages,
            home_dir=kde_settings_home_dir,
            system_look_and_feel_dir=kde_settings_system_look_and_feel_dir,
            automatic_look_and_feel=kde_settings_automatic_look_and_feel,
            touchpad_click_method=kde_settings_touchpad_click_method,
            virtual_keyboard_enabled=kde_settings_virtual_keyboard_enabled,
            places_hidden=kde_settings_places_hidden,
            kconfig=kde_settings_kconfig,
        ),
        swapfile_service_install=replace(
            base.swapfile_service_install,
            swapfile_path=swapfile_path,
            ram_multiplier=swapfile_ram_multiplier,
            swapfile_mode=swapfile_mode,
        ),
        zram_service=replace(
            base.zram_service,
            reset_busy_attempts=zram_reset_busy_attempts,
            reset_busy_retry_delay_seconds=zram_reset_busy_retry_delay_seconds,
        ),
        i2pd_service_setup=replace(
            base.i2pd_service_setup,
            download_dir=i2pd_download_dir,
            os_release_file_path=i2pd_os_release_file_path,
            config_path=i2pd_config_path,
            install_retries=i2pd_install_retries,
            start_check_attempts=i2pd_start_check_attempts,
            start_check_retry_delay_seconds=i2pd_start_check_retry_delay_seconds,
            tunnels_config_path=i2pd_tunnels_config_path,
            tunnel_keys_path=i2pd_tunnel_keys_path,
            address_file_path=i2pd_address_file_path,
        ),
        three_x_ui_xray_setup=replace(
            base.three_x_ui_xray_setup,
            install_dir=three_x_ui_install_dir,
            start_check_attempts=three_x_ui_start_check_attempts,
            start_check_retry_delay_seconds=three_x_ui_start_check_retry_delay_seconds,
            install_result_env_path=three_x_ui_install_result_env_path,
            ssl_enabled=three_x_ui_ssl_enabled,
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
            probe_timeout_seconds=three_x_ui_probe_timeout_seconds,
            probe_port_80_timeout_seconds=three_x_ui_probe_port_80_timeout_seconds,
            probe_listener_start_seconds=three_x_ui_probe_listener_start_seconds,
            upnp_enabled=three_x_ui_upnp_enabled,
            upnp_package=three_x_ui_upnp_package,
        ),
        yggdrasil_service_setup=replace(
            base.yggdrasil_service_setup,
            download_dir=yggdrasil_download_dir,
            install_retries=yggdrasil_install_retries,
            config_path=yggdrasil_config_path,
            private_key_path=yggdrasil_private_key_path,
            listen=yggdrasil_listen,
            peers_full_path=yggdrasil_peers_full_path,
            peer_batch_size=yggdrasil_peer_batch_size,
            peer_target_count=yggdrasil_peer_target_count,
            peer_probe_timeout_seconds=yggdrasil_peer_probe_timeout_seconds,
            static_peers=yggdrasil_static_peers,
            address_file_path=yggdrasil_address_file_path,
            address_save_retry_base_seconds=yggdrasil_address_save_retry_base_seconds,
            address_save_retry_multiplier=yggdrasil_address_save_retry_multiplier,
            address_save_retry_max_seconds=yggdrasil_address_save_retry_max_seconds,
            connection_wait_base_seconds=yggdrasil_connection_wait_base_seconds,
            connection_wait_multiplier=yggdrasil_connection_wait_multiplier,
            connection_wait_max_seconds=yggdrasil_connection_wait_max_seconds,
            nm_unmanaged_conf_path=yggdrasil_nm_unmanaged_conf_path,
            netplan_dir_path=yggdrasil_netplan_dir_path,
        ),
        tor_setup=replace(
            base.tor_setup,
            torrc_path=tor_torrc_path,
            torrc_dropin_path=tor_torrc_dropin_path,
            torrc_include_path=tor_torrc_include_path,
            hidden_service_dir=tor_hidden_service_dir,
            tor_user=tor_user,
            install_retries=tor_install_retries,
            start_check_attempts=tor_start_check_attempts,
            start_check_retry_delay_seconds=tor_start_check_retry_delay_seconds,
            address_file_path=tor_address_file_path,
        ),
        ssh_daemon_setup=replace(
            base.ssh_daemon_setup,
            start_check_retry_delay_seconds=ssh_daemon_start_check_retry_delay_seconds,
            sshd_config_path=ssh_daemon_sshd_config_path,
            sshd_config_dropin_path=ssh_daemon_sshd_config_dropin_path,
            root_ssh_dir=ssh_daemon_root_ssh_dir,
            users=ssh_daemon_users,
            directives=ssh_daemon_directives,
        ),
        ssh_client_setup=replace(
            base.ssh_client_setup,
            ssh_config_path=ssh_client_ssh_config_path,
            ssh_config_dropin_path=ssh_client_ssh_config_dropin_path,
            directives=ssh_client_directives,
        ),
        system_metrics_setup=replace(
            base.system_metrics_setup,
            backoff_base_seconds=system_metrics_backoff_base_seconds,
            backoff_multiplier=system_metrics_backoff_multiplier,
            backoff_max_seconds=system_metrics_backoff_max_seconds,
            venv_dir=system_metrics_venv_dir,
            system_config_path=system_metrics_system_config_path,
            command_path=system_metrics_command_path,
            system_metrics_dir=system_metrics_dir,
            system_metrics_dir_mode=system_metrics_dir_mode,
            max_queue_file_size_bytes=system_metrics_max_queue_file_size_bytes,
            send_order=system_metrics_send_order,
            spool_dir=system_metrics_spool_dir,
            collector=replace(
                base.system_metrics_setup.collector,
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
        nextdns_setup_system_wide=replace(
            base.nextdns_setup_system_wide,
            vault_group_title=nextdns_vault_group_title,
            profile_id_file_path=nextdns_profile_id_file_path,
            profile_id_file_mode=nextdns_profile_id_file_mode,
            error_priority=nextdns_error_priority,
        ),
        port_forwarding_setup=replace(
            base.port_forwarding_setup,
            connect_timeout_seconds=port_forwarding_connect_timeout_seconds,
            state_file_path=port_forwarding_state_file_path,
        ),
        vocalinux_setup=replace(
            base.vocalinux_setup,
            home_dir=vocalinux_home_dir,
            download_dir=vocalinux_download_dir,
        ),
        rustdesk_setup=replace(
            base.rustdesk_setup,
            download_dir=rustdesk_download_dir,
            id_file_path=rustdesk_id_file_path,
            config_dir=rustdesk_config_dir,
            options=rustdesk_options,
        ),
        telegram_setup=replace(
            base.telegram_setup,
            home_dir=telegram_home_dir,
            download_dir=telegram_download_dir,
        ),
        playwright_setup=replace(
            base.playwright_setup,
            home_dir=playwright_setup_home_dir,
        ),
        chrome_setup=replace(
            base.chrome_setup,
            home_dir=chrome_home_dir,
            settings_dir=chrome_settings_dir,
            system_root=chrome_system_root,
            apt_source_path=chrome_apt_source_path,
            keyring_path=chrome_keyring_path,
            desktop_source_path=chrome_desktop_source_path,
            desktop_override_path=chrome_desktop_override_path,
            profile_mirror_path=chrome_profile_mirror_path,
        ),
        local_vault_setup=replace(
            base.local_vault_setup,
            source_vault_production=local_vault_source_production,
            source_vault_default=local_vault_source_default,
            local_vault_path=local_vault_path,
            pass_file_path=local_vault_pass_file_path,
            local_vault_file_mode=local_vault_file_mode,
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
