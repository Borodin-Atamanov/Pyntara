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
    Config,
    RustdeskOptionConfig,
    load_config,
)
from pyntara.context import Context

# Root of the clone the tests run from: the tests directory sits one level
# under it. The suite names it itself, exactly as the composition root does.
REPO_ROOT = Path(__file__).resolve().parents[1]


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
                node for node in tree if node == path or node.startswith(path + "/")
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
                    suffix = node[len(base) :].lstrip("/")
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
                            child_label = (
                                child[len(node) :].lstrip("/").split("[", 1)[0]
                            )
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
    cli_tools_packages: tuple[str, ...] = ("mc", "htop"),
    cli_tools_threshold: int = 70,
    cli_tools_retries: int = 3,
    imagemagick_setup_packages: tuple[str, ...] = ("imagemagick",),
    imagemagick_setup_policy_path: Path = Path("/etc/ImageMagick-7/policy.xml"),
    imagemagick_setup_policy_template_file_name: str = "policy.xml",
    imagemagick_setup_policy_backup_file_suffix: str = ".bak",
    ffmpeg_setup_packages: tuple[str, ...] = ("ffmpeg",),
    ffmpeg_setup_wayrecord_bin_path: Path = Path("/usr/local/bin/pyntara-wayrecord"),
    ffmpeg_setup_wayrecord_desktop_path: Path = Path(
        "/usr/share/applications/pyntara-wayrecord.desktop"
    ),
    playwright_setup_home_dir: str = "/home/i",
    sotavpn_setup_username: str = "i",
    sotavpn_setup_home_dir: str = "/home/i",
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
    add_extra_repos_uris_field_name: str = "uris:",
    add_extra_repos_components_field_name: str = "components:",
    add_extra_repos_legacy_sources_file: Path = Path("/etc/apt/sources.list"),
    add_extra_repos_sources_list_d: Path = Path("/etc/apt/sources.list.d"),
    add_extra_repos_keep_debs_file: Path = Path("/etc/apt/apt.conf.d/99keep-debs.conf"),
    hostname_file: Path = Path("/etc/hostname"),
    hostname_random_bytes: int = 4,
    hostname_set_hostname_command: tuple[str, ...] = (
        "hostnamectl",
        "set-hostname",
    ),
    kde_keyboard_setup_username: str = "i",
    kde_keyboard_setup_home_dir: str = "/home/i",
    kde_keyboard_setup_config_dir: str = "/home/i/.config",
    kde_keyboard_setup_layout_switch_shortcuts: dict[str, str] | None = None,
    kde_keyboard_setup_mkdir_command: tuple[str, ...] | None = None,
    swapfile_path: Path = Path("/swapfile"),
    swapfile_meminfo_total_key: str = "MemTotal:",
    swapfile_ram_multiplier: float = 2.0,
    swapfile_mode: int = 0o600,
    swapfile_unit_template_file_name: str = "swapfile.service",
    swapfile_create_command: tuple[str, ...] = (
        "fallocate",
        "-l",
        "{size_mb}M",
        "{swapfile_path}",
    ),
    swapfile_chmod_command: tuple[str, ...] = (
        "chmod",
        "{file_mode}",
        "{swapfile_path}",
    ),
    swapfile_format_command: tuple[str, ...] = ("mkswap", "{swapfile_path}"),
    swapfile_systemctl_enable_command: tuple[str, ...] = (
        "systemctl",
        "enable",
        "{service_unit_name}",
    ),
    zswap_parameters_dir_path: Path = Path("/sys/module/zswap/parameters"),
    zswap_systemctl_enable_command: tuple[str, ...] = (
        "systemctl",
        "enable",
        "{service_unit_name}",
    ),
    zram_reset_busy_attempts: int = 5,
    zram_meminfo_total_key: str = "MemTotal:",
    zram_module_name: str = "zram",
    zram_cpuinfo_processor_key: str = "processor",
    zram_reset_busy_retry_delay_seconds: float = 0.5,
    zram_module_load_command: tuple[str, ...] = ("modprobe", "{module_name}"),
    zram_swap_on_command: tuple[str, ...] = (
        "swapon",
        "--priority",
        "{swap_priority}",
        "{device_path}",
    ),
    zram_unit_algorithm_line: str = (
        "ExecStart=/bin/sh -c 'echo {compressor} > {algorithm_attribute}'"
    ),
    zram_unit_template_file_name: str = "zram.service",
    three_x_ui_install_dir: Path = Path("/usr/local/x-ui"),
    three_x_ui_service_start_wait_seconds: int = 60,
    three_x_ui_panel_listener_wait_seconds: int = 60,
    three_x_ui_readiness_check_delay_seconds: int = 1,
    three_x_ui_core_ready_wait_seconds: int = 120,
    three_x_ui_install_result_env_path: Path = Path("/etc/x-ui/install-result.env"),
    three_x_ui_random_username_bytes: int = 4,
    three_x_ui_random_secret_bytes: int = 8,
    three_x_ui_random_sub_id_bytes: int = 6,
    three_x_ui_route_test_port: int = 443,
    three_x_ui_route_test_network: str = "tcp",
    three_x_ui_route_test_protocol: str = "tls",
    three_x_ui_remote_link_default_port: int = 443,
    three_x_ui_ssl_enabled: bool = True,
    three_x_ui_cert_dir: Path = Path("/root/cert/ip"),
    three_x_ui_self_signed_cert_dir: Path = Path("/root/cert/selfsigned"),
    three_x_ui_probe_timeout_seconds: int = 60,
    three_x_ui_panel_api_timeout_seconds: int = 120,
    three_x_ui_proxy_check_attempts: int = 3,
    three_x_ui_probe_port_80_timeout_seconds: int = 10,
    three_x_ui_probe_listener_start_seconds: int = 1,
    three_x_ui_upnp_enabled: bool = True,
    three_x_ui_upnp_package: str = "miniupnpc",
    rustdesk_asset_name_template: str = "rustdesk-{version}-{asset_arch}.deb",
    rustdesk_download_dir: Path = Path("/var/cache/pyntara/rustdesk"),
    rustdesk_id_file_path: Path = Path("/var/lib/pyntara/rustdesk_id"),
    rustdesk_config_dir: Path = Path("/home/i/.config/rustdesk"),
    rustdesk_service_settle_delay_seconds: float = 0.0,
    rustdesk_options: tuple[RustdeskOptionConfig, ...] = (
        RustdeskOptionConfig(key="enable-udp-punch", value="Y"),
    ),
    scrcpy_setup_home_dir: str = "/home/i",
    scrcpy_setup_download_dir: Path = Path("/var/cache/pyntara/scrcpy"),
    telegram_home_dir: str = "/home/i",
    telegram_download_dir: Path = Path("/var/cache/pyntara/telegram"),
    local_vault_source_production: Path = Path("secrets/production.vault"),
    local_vault_source_default: Path = Path("secrets/default.vault"),
    local_vault_path: Path = Path("/var/lib/pyntara/secrets/pyntara.vault"),
    local_vault_pass_file_path: Path = Path("/etc/pyntara/pass"),
    local_vault_file_mode: int = 0o640,
    nextdns_vault_group_title: str = "NextDNS",
    nextdns_profile_id_file_path: Path = Path("/var/lib/pyntara/nextdns_profile_id"),
    nextdns_profile_id_file_mode: int = 0o644,
    nextdns_error_priority: int = 3,
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    base = _base_config()
    return replace(
        base,
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
            policy_template_file_name=imagemagick_setup_policy_template_file_name,
            policy_backup_file_suffix=imagemagick_setup_policy_backup_file_suffix,
        ),
        ffmpeg_setup=replace(
            base.ffmpeg_setup,
            packages=ffmpeg_setup_packages,
            wayrecord_bin_path=ffmpeg_setup_wayrecord_bin_path,
            wayrecord_desktop_path=ffmpeg_setup_wayrecord_desktop_path,
        ),
        add_extra_repos=replace(
            base.add_extra_repos,
            components=add_extra_repos_components,
            uris_field_name=add_extra_repos_uris_field_name,
            components_field_name=add_extra_repos_components_field_name,
            ubuntu_hosts=add_extra_repos_ubuntu_hosts,
            keep_downloaded_debs=add_extra_repos_keep_downloaded_debs,
            legacy_sources_file=add_extra_repos_legacy_sources_file,
            sources_list_d=add_extra_repos_sources_list_d,
            keep_debs_file=add_extra_repos_keep_debs_file,
        ),
        hostname=replace(
            base.hostname,
            hostname_file=str(hostname_file),
            hostname_random_bytes=hostname_random_bytes,
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
            mkdir_command=(
                kde_keyboard_setup_mkdir_command
                if kde_keyboard_setup_mkdir_command is not None
                else base.kde_keyboard_setup.mkdir_command
            ),
        ),
        swapfile_service_install=replace(
            base.swapfile_service_install,
            swapfile_path=swapfile_path,
            meminfo_total_key=swapfile_meminfo_total_key,
            ram_multiplier=swapfile_ram_multiplier,
            swapfile_mode=swapfile_mode,
            unit_template_file_name=swapfile_unit_template_file_name,
            create_command=swapfile_create_command,
            chmod_command=swapfile_chmod_command,
            format_command=swapfile_format_command,
            systemctl_enable_command=swapfile_systemctl_enable_command,
        ),
        zswap_service=replace(
            base.zswap_service,
            parameters_dir_path=zswap_parameters_dir_path,
            systemctl_enable_command=zswap_systemctl_enable_command,
        ),
        zram_service=replace(
            base.zram_service,
            module_name=zram_module_name,
            meminfo_total_key=zram_meminfo_total_key,
            cpuinfo_processor_key=zram_cpuinfo_processor_key,
            reset_busy_attempts=zram_reset_busy_attempts,
            reset_busy_retry_delay_seconds=zram_reset_busy_retry_delay_seconds,
            module_load_command=zram_module_load_command,
            swap_on_command=zram_swap_on_command,
            unit_algorithm_line=zram_unit_algorithm_line,
            unit_template_file_name=zram_unit_template_file_name,
        ),
        three_x_ui_xray_setup=replace(
            base.three_x_ui_xray_setup,
            install_dir=three_x_ui_install_dir,
            service_start_wait_seconds=three_x_ui_service_start_wait_seconds,
            panel_listener_wait_seconds=three_x_ui_panel_listener_wait_seconds,
            readiness_check_delay_seconds=three_x_ui_readiness_check_delay_seconds,
            core_ready_wait_seconds=three_x_ui_core_ready_wait_seconds,
            install_result_env_path=three_x_ui_install_result_env_path,
            random_username_bytes=three_x_ui_random_username_bytes,
            random_secret_bytes=three_x_ui_random_secret_bytes,
            random_sub_id_bytes=three_x_ui_random_sub_id_bytes,
            route_test_port=three_x_ui_route_test_port,
            route_test_network=three_x_ui_route_test_network,
            route_test_protocol=three_x_ui_route_test_protocol,
            remote_link_default_port=three_x_ui_remote_link_default_port,
            ssl_enabled=three_x_ui_ssl_enabled,
            cert_dir=three_x_ui_cert_dir,
            cert_fullchain=three_x_ui_cert_dir / "fullchain.pem",
            cert_privkey=three_x_ui_cert_dir / "privkey.pem",
            self_signed_cert_dir=three_x_ui_self_signed_cert_dir,
            self_signed_cert_fullchain=(
                three_x_ui_self_signed_cert_dir / "fullchain.pem"
            ),
            self_signed_cert_privkey=(three_x_ui_self_signed_cert_dir / "privkey.pem"),
            probe_timeout_seconds=three_x_ui_probe_timeout_seconds,
            panel_api_timeout_seconds=three_x_ui_panel_api_timeout_seconds,
            proxy_check_attempts=three_x_ui_proxy_check_attempts,
            probe_port_80_timeout_seconds=three_x_ui_probe_port_80_timeout_seconds,
            probe_listener_start_seconds=three_x_ui_probe_listener_start_seconds,
            upnp_enabled=three_x_ui_upnp_enabled,
            upnp_package=three_x_ui_upnp_package,
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
        local_vault_setup=replace(
            base.local_vault_setup,
            source_vault_production=local_vault_source_production,
            source_vault_default=local_vault_source_default,
            local_vault_path=local_vault_path,
            pass_file_path=local_vault_pass_file_path,
            local_vault_file_mode=local_vault_file_mode,
        ),
        sotavpn_setup=replace(
            base.sotavpn_setup,
            username=sotavpn_setup_username,
            home_dir=sotavpn_setup_home_dir,
        ),
    )


def make_context(
    *,
    install_mode: str = "minimal",
    vault_password: str | None = None,
    vault_source: str | None = None,
    force_tasks: frozenset[str] = frozenset(),
    repo_root: Path = REPO_ROOT,
    task_data_root: Path = Path("/tmp"),
    skip_apt_update: bool = False,
    config: Config | None = None,
    task_name: str = "",
) -> Context:
    """Context with a small safe config; the real file is never touched.

    repo_root defaults to the clone the tests run from, so a test that
    reads a real template keeps working; a test whose task renders a
    fixture passes its own directory here instead of monkeypatching a
    module constant.

    task_name is the name of the task under test. The runner fills it in
    a real run, so a test that exercises force mode or a task-data
    template passes the catalog name of the module it calls.
    """

    return Context(
        install_mode=install_mode,
        vault_password=vault_password,
        vault_source=vault_source,
        force_tasks=force_tasks,
        repo_root=repo_root,
        task_data_root=task_data_root,
        skip_apt_update=skip_apt_update,
        config=config if config is not None else make_config(),
        task_name=task_name,
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
