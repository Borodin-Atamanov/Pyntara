"""Strict checks of the config documents, test side only.

The runtime reads the config and uses the values as they are: it never
checks, never stops and never invents a value. Every condition that used to
guard the run lives here instead, so a broken config fails the test suite
during development and never on the target machine (architecture contract,
Configuration). The parsers below are the checks that used to run inside the
package, with their logic unchanged, and the vocabularies they validate
against live here as well: the run reads no rule of the config.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pyntara.config import (
    MODES,
    AddExtraReposConfig,
    ChromeSetupConfig,
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
    PlaywrightSetupConfig,
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
    TelegramSetupConfig,
    ThreeXuiXraySetupConfig,
    TorSetupConfig,
    VaultEntry,
    VaultGroup,
    VaultGroupSeed,
    VaultStructureConfig,
    VocalinuxSetupConfig,
    YggdrasilMulticastInterfaceConfig,
    YggdrasilServiceSetupConfig,
    ZramServiceConfig,
    ZswapServiceConfig,
)
from pyntara.config.kde_settings import KCONFIG_TYPES
from pyntara.config.vault import GENERATED_PASSWORD_RE

# The vocabulary constants the checks validate against, and the error they
# raise. They live here, in the test suite, because the runtime reader never
# checks a value and never raises: a rule of the config needs no name in the
# shipped package. MODES is the exception, and comes from pyntara.config,
# because production reads it to accept or reject an install mode.


class ConfigError(RuntimeError):
    """Raised by a check when a config value is missing or invalid."""


SEND_ORDERS: tuple[str, ...] = ("oldest_first", "newest_first")

I2PD_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warn", "error", "none")

TOR_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warn", "err")

DNS_OVER_TLS_VALUES: tuple[str, ...] = ("yes", "opportunistic", "no")

YGGDRASIL_LISTEN_SCHEMES: tuple[str, ...] = ("tcp", "tls", "quic", "ws", "unix")

YGGDRASIL_PEER_SCHEMES: tuple[str, ...] = (
    "tcp",
    "tls",
    "quic",
    "ws",
    "wss",
    "socks",
    "sockstls",
    "unix",
)

NUMLOCK_STATES: tuple[str, ...] = ("on", "off", "unchanged")

CLICK_METHODS: tuple[str, ...] = ("clickfinger", "clickareas", "none")

SHARE_ADDR_STRATEGIES: tuple[str, ...] = ("node", "listen", "custom")

# The domain resolution strategies of the Xray routing block: AsIs keeps
# every name unresolved, IPIfNonMatch resolves a name only when no domain
# rule matched it, IPOnDemand resolves before matching at all.
DOMAIN_STRATEGIES: tuple[str, ...] = ("AsIs", "IPIfNonMatch", "IPOnDemand")


def _int_field(raw: object, name: str) -> int:
    """Validate an integer config value; bool is a subclass of int and must
    be excluded explicitly."""

    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(f"{name} must be an integer")
    return raw


def _float_field(raw: object, name: str) -> float:
    """Validate a numeric config value; bool is a subclass of int and must
    be excluded explicitly."""

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"{name} must be a number")
    value = float(raw)
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


def _octal_mode_field(raw: object, name: str) -> int:
    """Parse one octal file mode string like "0700" into an int.

    TOML has no octal literals, so the modes are configured as strings
    and converted here; a value that is not four octal digits is a
    config error.
    """

    if not isinstance(raw, str) or len(raw) != 4:
        raise ConfigError(f"{name} must be an octal string like '0700'")
    try:
        parsed = int(raw, 8)
    except ValueError:
        raise ConfigError(f"{name} must be an octal string like '0700'") from None
    return parsed


def _nonempty_string_field(raw: object, name: str) -> str:
    """Validate a non-empty string config value."""

    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{name} must be a non-empty string")
    return raw


def _string_list(raw: object, name: str) -> tuple[str, ...]:
    """Validate a non-empty array of non-empty strings."""

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name} must be a non-empty array of strings")
    if not all(isinstance(part, str) and part.strip() for part in raw):
        raise ConfigError(f"{name} must be non-empty strings")
    return tuple(part.strip() for part in raw)


def _bool_field(raw: object, name: str) -> bool:
    """Validate a boolean config value."""

    if not isinstance(raw, bool):
        raise ConfigError(f"{name} must be a boolean")
    return raw


def _string_map(raw: object, name: str) -> dict[str, str]:
    """Validate a table of non-empty strings keyed by non-empty strings.

    The keys and values are stripped of surrounding whitespace. An empty
    table is allowed (it means the feature the map configures is off).
    """

    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a table of strings")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{name} keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{name} values must be non-empty strings")
        result[key.strip()] = value.strip()
    return result


def _enum_field(raw: object, name: str, allowed: tuple[str, ...]) -> str:
    """Validate a config value restricted to an allowed vocabulary."""

    if not isinstance(raw, str) or raw not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(allowed)}")
    return raw


def _int_map(raw: object, name: str) -> dict[str, int]:
    """Validate a table of integers keyed by non-empty strings.

    A boolean is not an integer here: it is a flag of the file format, not
    a number of the file. An empty table is allowed.
    """

    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a table of integers")
    result: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{name} keys must be non-empty strings")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{name} values must be integers")
        result[key.strip()] = value
    return result





# from add_extra_repos.py


def _add_extra_repos_table(raw: object) -> AddExtraReposConfig:
    """Validate the [add_extra_repos] table and build AddExtraReposConfig.

    Components are non-empty strings without whitespace, deduplicated while
    preserving their configured order. An empty list is invalid: an empty
    component set would make the task trivially satisfied.
    keep_downloaded_debs is a required boolean, never silently defaulted.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[add_extra_repos] section is missing or not a table")
    components = raw.get("components")
    if not isinstance(components, list) or not components:
        raise ConfigError(
            "add_extra_repos.components must be a non-empty array of strings"
        )
    if not all(
        isinstance(component, str)
        and component
        and component == component.strip()
        and " " not in component
        for component in components
    ):
        raise ConfigError(
            "add_extra_repos.components must be non-empty strings without whitespace"
        )
    unique: list[str] = []
    seen: set[str] = set()
    for component in components:
        if component not in seen:
            seen.add(component)
            unique.append(component)
    ubuntu_hosts = raw.get("ubuntu_hosts")
    if not isinstance(ubuntu_hosts, list) or not ubuntu_hosts:
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be a non-empty array of strings"
        )
    if not all(
        isinstance(host, str) and host and host == host.strip()
        for host in ubuntu_hosts
    ):
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be non-empty strings"
        )
    keep_downloaded_debs = _bool_field(
        raw.get("keep_downloaded_debs"), "add_extra_repos.keep_downloaded_debs"
    )
    legacy_sources_file = Path(
        _nonempty_string_field(
            raw.get("legacy_sources_file"), "add_extra_repos.legacy_sources_file"
        )
    )
    sources_list_d = Path(
        _nonempty_string_field(
            raw.get("sources_list_d"), "add_extra_repos.sources_list_d"
        )
    )
    keep_debs_file = Path(
        _nonempty_string_field(
            raw.get("keep_debs_file"), "add_extra_repos.keep_debs_file"
        )
    )
    keep_debs_dropin_content = _nonempty_string_field(
        raw.get("keep_debs_dropin_content"),
        "add_extra_repos.keep_debs_dropin_content",
    )
    return AddExtraReposConfig(
        components=tuple(unique),
        ubuntu_hosts=tuple(ubuntu_hosts),
        keep_downloaded_debs=keep_downloaded_debs,
        legacy_sources_file=legacy_sources_file,
        sources_list_d=sources_list_d,
        keep_debs_file=keep_debs_file,
        keep_debs_dropin_content=keep_debs_dropin_content,
    )





# from chrome_setup.py


def _chrome_setup_table(raw: object) -> ChromeSetupConfig:
    """Validate the [chrome_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[chrome_setup] section is missing or not a table")
    cdp_port = _int_field(raw.get("cdp_port"), "chrome_setup.cdp_port")
    if cdp_port <= 0:
        raise ConfigError("chrome_setup.cdp_port must be positive")
    return ChromeSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "chrome_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "chrome_setup.home_dir"
        ),
        package_name=_nonempty_string_field(
            raw.get("package_name"), "chrome_setup.package_name"
        ),
        process_name=_nonempty_string_field(
            raw.get("process_name"), "chrome_setup.process_name"
        ),
        appletsrc_file_name=_nonempty_string_field(
            raw.get("appletsrc_file_name"), "chrome_setup.appletsrc_file_name"
        ),
        appletsrc_relative_path=_nonempty_string_field(
            raw.get("appletsrc_relative_path"),
            "chrome_setup.appletsrc_relative_path",
        ),
        appletsrc_launchers_key=_nonempty_string_field(
            raw.get("appletsrc_launchers_key"),
            "chrome_setup.appletsrc_launchers_key",
        ),
        kreadconfig_command=_placeholder_command_field(
            raw.get("kreadconfig_command"),
            "chrome_setup.kreadconfig_command",
            ("{file_name}",),
        ),
        kwriteconfig_command=_placeholder_command_field(
            raw.get("kwriteconfig_command"),
            "chrome_setup.kwriteconfig_command",
            ("{file_name}",),
        ),
        config_group_flag=_placeholder_command_field(
            raw.get("config_group_flag"),
            "chrome_setup.config_group_flag",
            ("{group}",),
        ),
        config_key_flag=_placeholder_command_field(
            raw.get("config_key_flag"),
            "chrome_setup.config_key_flag",
            ("{key}",),
        ),
        taskbar_plugin_names=_string_list(
            raw.get("taskbar_plugin_names"),
            "chrome_setup.taskbar_plugin_names",
        ),
        panel_launcher_id=_nonempty_string_field(
            raw.get("panel_launcher_id"), "chrome_setup.panel_launcher_id"
        ),
        panel_restart_command=_string_list(
            raw.get("panel_restart_command"),
            "chrome_setup.panel_restart_command",
        ),
        settings_repo_url=_nonempty_string_field(
            raw.get("settings_repo_url"), "chrome_setup.settings_repo_url"
        ),
        settings_repo_ref=_nonempty_string_field(
            raw.get("settings_repo_ref"), "chrome_setup.settings_repo_ref"
        ),
        settings_dir=Path(
            _nonempty_string_field(
                raw.get("settings_dir"), "chrome_setup.settings_dir"
            )
        ),
        settings_system_tree_relative_path=_nonempty_string_field(
            raw.get("settings_system_tree_relative_path"),
            "chrome_setup.settings_system_tree_relative_path",
        ),
        preferences_relative_path=_nonempty_string_field(
            raw.get("preferences_relative_path"),
            "chrome_setup.preferences_relative_path",
        ),
        profile_dir_relative_path=_nonempty_string_field(
            raw.get("profile_dir_relative_path"),
            "chrome_setup.profile_dir_relative_path",
        ),
        keyring_temp_dir_prefix=_nonempty_string_field(
            raw.get("keyring_temp_dir_prefix"),
            "chrome_setup.keyring_temp_dir_prefix",
        ),
        apt_source_template_file_name=_nonempty_string_field(
            raw.get("apt_source_template_file_name"),
            "chrome_setup.apt_source_template_file_name",
        ),
        launch_flags=_string_list(
            raw.get("launch_flags"), "chrome_setup.launch_flags"
        ),
        keyring_dearmor_command=_string_list(
            raw.get("keyring_dearmor_command"),
            "chrome_setup.keyring_dearmor_command",
        ),
        settings_clone_command=_string_list(
            raw.get("settings_clone_command"),
            "chrome_setup.settings_clone_command",
        ),
        settings_fetch_command=_string_list(
            raw.get("settings_fetch_command"),
            "chrome_setup.settings_fetch_command",
        ),
        settings_revision_command=_string_list(
            raw.get("settings_revision_command"),
            "chrome_setup.settings_revision_command",
        ),
        settings_reset_command=_string_list(
            raw.get("settings_reset_command"),
            "chrome_setup.settings_reset_command",
        ),
        process_check_command=_string_list(
            raw.get("process_check_command"),
            "chrome_setup.process_check_command",
        ),
        mount_check_command=_string_list(
            raw.get("mount_check_command"), "chrome_setup.mount_check_command"
        ),
        mount_reload_command=_string_list(
            raw.get("mount_reload_command"), "chrome_setup.mount_reload_command"
        ),
        mount_enable_command=_string_list(
            raw.get("mount_enable_command"), "chrome_setup.mount_enable_command"
        ),
        menu_refresh_command=_string_list(
            raw.get("menu_refresh_command"), "chrome_setup.menu_refresh_command"
        ),
        mount_unit_template_file_name=_nonempty_string_field(
            raw.get("mount_unit_template_file_name"),
            "chrome_setup.mount_unit_template_file_name",
        ),
        system_root=Path(
            _nonempty_string_field(
                raw.get("system_root"), "chrome_setup.system_root"
            )
        ),
        apt_source_path=Path(
            _nonempty_string_field(
                raw.get("apt_source_path"), "chrome_setup.apt_source_path"
            )
        ),
        keyring_path=Path(
            _nonempty_string_field(
                raw.get("keyring_path"), "chrome_setup.keyring_path"
            )
        ),
        google_key_url=_nonempty_string_field(
            raw.get("google_key_url"), "chrome_setup.google_key_url"
        ),
        desktop_source_path=Path(
            _nonempty_string_field(
                raw.get("desktop_source_path"),
                "chrome_setup.desktop_source_path",
            )
        ),
        desktop_override_path=Path(
            _nonempty_string_field(
                raw.get("desktop_override_path"),
                "chrome_setup.desktop_override_path",
            )
        ),
        profile_mirror_path=Path(
            _nonempty_string_field(
                raw.get("profile_mirror_path"),
                "chrome_setup.profile_mirror_path",
            )
        ),
        mount_service_unit_name=_nonempty_string_field(
            raw.get("mount_service_unit_name"),
            "chrome_setup.mount_service_unit_name",
        ),
        cdp_port=cdp_port,
        cdp_address=_nonempty_string_field(
            raw.get("cdp_address"), "chrome_setup.cdp_address"
        ),
        runuser_command=_placeholder_command_field(
            raw.get("runuser_command"),
            "chrome_setup.runuser_command",
            ("{username}",),
        ),
        file_mode=_octal_mode_field(
            raw.get("file_mode"), "chrome_setup.file_mode"
        ),
    )





# from cli_tools.py


def _cli_tools_table(raw: object) -> CliToolsConfig:
    """Validate the [cli_tools] table and build CliToolsConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[cli_tools] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("cli_tools.packages must be an array of strings")
    package_success_threshold_percent = _int_field(
        raw.get("package_success_threshold_percent"),
        "cli_tools.package_success_threshold_percent",
    )
    if not 0 <= package_success_threshold_percent <= 100:
        raise ConfigError(
            "cli_tools.package_success_threshold_percent must be between 0 and 100"
        )
    return CliToolsConfig(
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "cli_tools.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"), "cli_tools.package_install_retries"
        ),
        package_success_threshold_percent=package_success_threshold_percent,
    )





# from dnsproxy_setup.py


def _dnsproxy_string_list(raw: dict[str, object], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ConfigError(f"dnsproxy_setup.{name} must be a non-empty array of strings")
    return tuple(item.strip() for item in value)


def _dnsproxy_setup_table(raw: object) -> DnsproxySetupConfig:
    # Validate the dnsproxy_setup table.

    if not isinstance(raw, dict):
        raise ConfigError("[dnsproxy_setup] section is missing or not a table")
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "dnsproxy_setup.github_repo"
    )
    asset_name_template = _nonempty_string_field(
        raw.get("asset_name_template"),
        "dnsproxy_setup.asset_name_template",
    )
    asset_architecture_names = _string_map(
        raw.get("asset_architecture_names"),
        "dnsproxy_setup.asset_architecture_names",
    )
    binary_file_name = _nonempty_string_field(
        raw.get("binary_file_name"), "dnsproxy_setup.binary_file_name"
    )
    staged_binary_file_name = _nonempty_string_field(
        raw.get("staged_binary_file_name"),
        "dnsproxy_setup.staged_binary_file_name",
    )
    extract_dir_name = _nonempty_string_field(
        raw.get("extract_dir_name"), "dnsproxy_setup.extract_dir_name"
    )
    download_dir = Path(
        _nonempty_string_field(raw.get("download_dir"), "dnsproxy_setup.download_dir")
    )
    binary_path = Path(
        _nonempty_string_field(raw.get("binary_path"), "dnsproxy_setup.binary_path")
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "dnsproxy_setup.service_unit_name"
    )
    service_unit_path = Path(
        _nonempty_string_field(raw.get("service_unit_path"), "dnsproxy_setup.service_unit_path")
    )
    service_template_path = Path(
        _nonempty_string_field(
            raw.get("service_template_path"), "dnsproxy_setup.service_template_path"
        )
    )
    listen_addresses = _dnsproxy_string_list(raw, "listen_addresses")
    listen_port = _int_field(raw.get("listen_port"), "dnsproxy_setup.listen_port")
    if not 1 <= listen_port <= 65535:
        raise ConfigError("dnsproxy_setup.listen_port must be between 1 and 65535")
    endpoint_values = tuple(
        _nonempty_string_field(raw.get(name), f"dnsproxy_setup.{name}")
        for name in ("doh_url_format", "dot_host_format", "doq_host_format")
    )
    if any("{profile_id}" not in value for value in endpoint_values):
        raise ConfigError("dnsproxy_setup endpoint formats must contain {profile_id}")
    upstream_mode = _nonempty_string_field(
        raw.get("upstream_mode"), "dnsproxy_setup.upstream_mode"
    )
    if upstream_mode not in ("load_balance", "parallel", "fastest_addr"):
        raise ConfigError("dnsproxy_setup.upstream_mode has an unsupported value")
    cache_enabled = raw.get("cache_enabled")
    if not isinstance(cache_enabled, bool):
        raise ConfigError("dnsproxy_setup.cache_enabled must be a boolean")
    cache_size_bytes = _int_field(
        raw.get("cache_size_bytes"), "dnsproxy_setup.cache_size_bytes"
    )
    if cache_size_bytes <= 0:
        raise ConfigError("dnsproxy_setup.cache_size_bytes must be positive")
    timeout_seconds = _int_field(
        raw.get("timeout_seconds"), "dnsproxy_setup.timeout_seconds"
    )
    if timeout_seconds <= 0:
        raise ConfigError("dnsproxy_setup.timeout_seconds must be positive")
    log_rate_limit_interval_seconds = _int_field(
        raw.get("log_rate_limit_interval_seconds"),
        "dnsproxy_setup.log_rate_limit_interval_seconds",
    )
    if log_rate_limit_interval_seconds <= 0:
        raise ConfigError(
            "dnsproxy_setup.log_rate_limit_interval_seconds must be positive"
        )
    log_rate_limit_burst = _int_field(
        raw.get("log_rate_limit_burst"), "dnsproxy_setup.log_rate_limit_burst"
    )
    if log_rate_limit_burst <= 0:
        raise ConfigError("dnsproxy_setup.log_rate_limit_burst must be positive")
    bootstrap_resolvers = _dnsproxy_string_list(raw, "bootstrap_resolvers")
    append_provider_dns = raw.get("append_provider_dns")
    if not isinstance(append_provider_dns, bool):
        raise ConfigError("dnsproxy_setup.append_provider_dns must be a boolean")
    service_restart_seconds = _float_field(
        raw.get("service_restart_seconds"), "dnsproxy_setup.service_restart_seconds"
    )
    if service_restart_seconds < 0:
        raise ConfigError("dnsproxy_setup.service_restart_seconds must not be negative")
    install_retries = _int_field(
        raw.get("install_retries"), "dnsproxy_setup.install_retries"
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "dnsproxy_setup.start_check_attempts"
    )
    if install_retries < 1 or start_check_attempts < 1:
        raise ConfigError("dnsproxy_setup retry and readiness values must be positive")
    start_delay = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "dnsproxy_setup.start_check_retry_delay_seconds",
    )
    if start_delay <= 0:
        raise ConfigError(
            "dnsproxy_setup.start_check_retry_delay_seconds must be positive"
        )
    resolved_conf_dir = Path(
        _nonempty_string_field(
            raw.get("resolved_conf_dir"), "dnsproxy_setup.resolved_conf_dir"
        )
    )
    resolved_dropin_file_name = _nonempty_string_field(
        raw.get("resolved_dropin_file_name"), "dnsproxy_setup.resolved_dropin_file_name"
    )
    resolved_dropin_file_mode = _octal_mode_field(
        raw.get("resolved_dropin_file_mode"), "dnsproxy_setup.resolved_dropin_file_mode"
    )
    staged_binary_file_mode = _octal_mode_field(
        raw.get("staged_binary_file_mode"), "dnsproxy_setup.staged_binary_file_mode"
    )
    resolved_dropin_header = _nonempty_string_field(
        raw.get("resolved_dropin_header"), "dnsproxy_setup.resolved_dropin_header"
    )
    resolved_section = _nonempty_string_field(
        raw.get("resolved_section"), "dnsproxy_setup.resolved_section"
    )
    resolved_dns_directives = _dnsproxy_string_list(raw, "resolved_dns_directives")
    resolved_domains_directive = _nonempty_string_field(
        raw.get("resolved_domains_directive"),
        "dnsproxy_setup.resolved_domains_directive",
    )
    manage_networkmanager = raw.get("manage_networkmanager")
    if not isinstance(manage_networkmanager, bool):
        raise ConfigError("dnsproxy_setup.manage_networkmanager must be a boolean")
    commands = tuple(
        _dnsproxy_string_list(raw, name)
        for name in (
            "nmcli_check_command",
            "nmcli_device_status_command",
            "nmcli_active_list_command",
            "nmcli_dns_state_command",
            "nmcli_modify_command",
            "nmcli_reapply_command",
            "daemon_reload_command",
            "restart_resolved_command",
            "resolvectl_status_command",
            "resolvectl_dns_command",
            "nmcli_dns_command",
            "verification_command",
            "ss_tcp_listen_command",
            "ss_udp_listen_command",
            "kill_command",
            "service_log_command",
        )
    )
    verification_domain = _nonempty_string_field(
        raw.get("verification_domain"), "dnsproxy_setup.verification_domain"
    )
    verification_error_excerpt_length = _positive_int_field(
        raw.get("verification_error_excerpt_length"),
        "dnsproxy_setup.verification_error_excerpt_length",
    )
    service_log_excerpt_length = _positive_int_field(
        raw.get("service_log_excerpt_length"),
        "dnsproxy_setup.service_log_excerpt_length",
    )
    profile_id_file_path = Path(
        _nonempty_string_field(
            raw.get("profile_id_file_path"), "dnsproxy_setup.profile_id_file_path"
        )
    )
    profile_id_file_mode = _octal_mode_field(
        raw.get("profile_id_file_mode"), "dnsproxy_setup.profile_id_file_mode"
    )
    return DnsproxySetupConfig(
        github_repo=github_repo,
        asset_name_template=asset_name_template,
        asset_architecture_names=asset_architecture_names,
        binary_file_name=binary_file_name,
        staged_binary_file_name=staged_binary_file_name,
        extract_dir_name=extract_dir_name,
        download_dir=download_dir,
        binary_path=binary_path,
        service_unit_name=service_unit_name,
        service_unit_path=service_unit_path,
        service_template_path=service_template_path,
        listen_addresses=listen_addresses,
        listen_port=listen_port,
        doh_url_format=endpoint_values[0],
        dot_host_format=endpoint_values[1],
        doq_host_format=endpoint_values[2],
        upstream_mode=upstream_mode,
        cache_enabled=cache_enabled,
        cache_size_bytes=cache_size_bytes,
        timeout_seconds=timeout_seconds,
        log_rate_limit_interval_seconds=log_rate_limit_interval_seconds,
        log_rate_limit_burst=log_rate_limit_burst,
        bootstrap_resolvers=bootstrap_resolvers,
        append_provider_dns=append_provider_dns,
        service_restart_seconds=service_restart_seconds,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_delay,
        resolved_conf_dir=resolved_conf_dir,
        resolved_dropin_file_name=resolved_dropin_file_name,
        resolved_dropin_file_mode=resolved_dropin_file_mode,
        staged_binary_file_mode=staged_binary_file_mode,
        resolved_dropin_header=resolved_dropin_header,
        resolved_section=resolved_section,
        resolved_dns_directives=resolved_dns_directives,
        resolved_domains_directive=resolved_domains_directive,
        manage_networkmanager=manage_networkmanager,
        nmcli_check_command=commands[0],
        nmcli_device_status_command=commands[1],
        nmcli_active_list_command=commands[2],
        nmcli_dns_state_command=commands[3],
        nmcli_modify_command=commands[4],
        nmcli_reapply_command=commands[5],
        daemon_reload_command=commands[6],
        restart_resolved_command=commands[7],
        resolvectl_status_command=commands[8],
        resolvectl_dns_command=commands[9],
        nmcli_dns_command=commands[10],
        verification_command=commands[11],
        ss_tcp_listen_command=commands[12],
        ss_udp_listen_command=commands[13],
        kill_command=commands[14],
        service_log_command=commands[15],
        verification_domain=verification_domain,
        probe_address=_nonempty_string_field(
            raw.get("probe_address"), "dnsproxy_setup.probe_address"
        ),
        probe_ident_bytes=_positive_int_field(
            raw.get("probe_ident_bytes"), "dnsproxy_setup.probe_ident_bytes"
        ),
        tun_device_type=_nonempty_string_field(
            raw.get("tun_device_type"), "dnsproxy_setup.tun_device_type"
        ),
        loopback_connection_name=_nonempty_string_field(
            raw.get("loopback_connection_name"),
            "dnsproxy_setup.loopback_connection_name",
        ),
        nmcli_auto_dns_ignored_value=_nonempty_string_field(
            raw.get("nmcli_auto_dns_ignored_value"),
            "dnsproxy_setup.nmcli_auto_dns_ignored_value",
        ),
        nmcli_ignore_auto_dns_value=_nonempty_string_field(
            raw.get("nmcli_ignore_auto_dns_value"),
            "dnsproxy_setup.nmcli_ignore_auto_dns_value",
        ),
        nmcli_restore_auto_dns_value=_nonempty_string_field(
            raw.get("nmcli_restore_auto_dns_value"),
            "dnsproxy_setup.nmcli_restore_auto_dns_value",
        ),
        service_stop_command=_string_list(
            raw.get("service_stop_command"),
            "dnsproxy_setup.service_stop_command",
        ),
        service_enable_command=_string_list(
            raw.get("service_enable_command"),
            "dnsproxy_setup.service_enable_command",
        ),
        service_start_command=_string_list(
            raw.get("service_start_command"),
            "dnsproxy_setup.service_start_command",
        ),
        service_restart_command=_string_list(
            raw.get("service_restart_command"),
            "dnsproxy_setup.service_restart_command",
        ),
        verification_error_excerpt_length=verification_error_excerpt_length,
        service_log_excerpt_length=service_log_excerpt_length,
        profile_id_file_path=profile_id_file_path,
        profile_id_file_mode=profile_id_file_mode,
    )





# from engine.py


def _checked_parallel_source_marker(
    write_out_text: str, source_marker: str
) -> str:
    """The source marker of the parallel query, checked against its text.

    The marker is what split_url_answers looks for in the merged output,
    so a marker that does not appear in curl_parallel_write_out would
    leave every answer unattributed; the two values live in one table and
    this check keeps them in step.
    """

    if source_marker not in write_out_text:
        raise ConfigError(
            "engine.curl_parallel_source_marker must appear in "
            "engine.curl_parallel_write_out"
        )
    return source_marker


def _engine_table(raw: object) -> EngineConfig:
    """Validate the [engine] table and build EngineConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[engine] section is missing or not a table")
    task_data_root = raw.get("task_data_root")
    if not isinstance(task_data_root, str):
        raise ConfigError("engine.task_data_root must be a string")
    systemd_unit_dir = raw.get("systemd_unit_dir")
    if not isinstance(systemd_unit_dir, str):
        raise ConfigError("engine.systemd_unit_dir must be a string")
    desktop_detect_processes = raw.get("desktop_detect_processes")
    if not isinstance(desktop_detect_processes, list) or not desktop_detect_processes:
        raise ConfigError(
            "engine.desktop_detect_processes must be a non-empty array of strings"
        )
    if not all(
        isinstance(process, str) and process and process == process.strip()
        for process in desktop_detect_processes
    ):
        raise ConfigError(
            "engine.desktop_detect_processes must be non-empty strings"
        )
    desktop_username = raw.get("desktop_username", "")
    if not isinstance(desktop_username, str):
        raise ConfigError("engine.desktop_username must be a string")
    session_environment_command = raw.get("session_environment_command", [])
    if not isinstance(session_environment_command, list):
        raise ConfigError(
            "engine.session_environment_command must be an array of strings"
        )
    if not all(
        isinstance(part, str) and part.strip() for part in session_environment_command
    ):
        raise ConfigError(
            "engine.session_environment_command must be non-empty strings"
        )
    session_environment_keys = raw.get("session_environment_keys", [])
    if not isinstance(session_environment_keys, list):
        raise ConfigError(
            "engine.session_environment_keys must be an array of strings"
        )
    if not all(
        isinstance(key, str) and key.strip() for key in session_environment_keys
    ):
        raise ConfigError("engine.session_environment_keys must be non-empty strings")
    session_bus_key = raw.get("session_bus_key")
    if session_bus_key is None:
        session_bus_key = ""
    elif not isinstance(session_bus_key, str) or not session_bus_key:
        raise ConfigError("engine.session_bus_key must be a non-empty string")
    session_display_keys = raw.get("session_display_keys", [])
    if not isinstance(session_display_keys, list):
        raise ConfigError("engine.session_display_keys must be an array of strings")
    if not all(
        isinstance(key, str) and key.strip() for key in session_display_keys
    ):
        raise ConfigError("engine.session_display_keys must be non-empty strings")
    error_priority = _int_field(raw.get("error_priority"), "engine.error_priority")
    if not 0 <= error_priority <= 7:
        raise ConfigError("engine.error_priority must be between 0 and 7")
    progress_priority = _int_field(
        raw.get("progress_priority"), "engine.progress_priority"
    )
    if not 0 <= progress_priority <= 7:
        raise ConfigError("engine.progress_priority must be between 0 and 7")
    curl_timeout_seconds = _int_field(
        raw.get("curl_timeout_seconds"), "engine.curl_timeout_seconds"
    )
    if curl_timeout_seconds <= 0:
        raise ConfigError("engine.curl_timeout_seconds must be positive")
    curl_download_timeout_seconds = _int_field(
        raw.get("curl_download_timeout_seconds"),
        "engine.curl_download_timeout_seconds",
    )
    if curl_download_timeout_seconds <= 0:
        raise ConfigError("engine.curl_download_timeout_seconds must be positive")
    curl_retries = _int_field(raw.get("curl_retries"), "engine.curl_retries")
    if curl_retries < 0:
        raise ConfigError("engine.curl_retries must not be negative")
    curl_retry_delay_seconds = _int_field(
        raw.get("curl_retry_delay_seconds"), "engine.curl_retry_delay_seconds"
    )
    if curl_retry_delay_seconds < 1:
        raise ConfigError("engine.curl_retry_delay_seconds must be positive")
    curl_connect_timeout_seconds = _int_field(
        raw.get("curl_connect_timeout_seconds"),
        "engine.curl_connect_timeout_seconds",
    )
    if curl_connect_timeout_seconds <= 0:
        raise ConfigError("engine.curl_connect_timeout_seconds must be positive")
    curl_retry_max_time_seconds = _int_field(
        raw.get("curl_retry_max_time_seconds"),
        "engine.curl_retry_max_time_seconds",
    )
    if curl_retry_max_time_seconds <= 0:
        raise ConfigError("engine.curl_retry_max_time_seconds must be positive")
    curl_download_command = _string_list(
        raw.get("curl_download_command"), "engine.curl_download_command"
    )
    if not curl_download_command:
        raise ConfigError("engine.curl_download_command must not be empty")
    if "{output_path}" not in " ".join(curl_download_command):
        raise ConfigError(
            "engine.curl_download_command must carry the {output_path} placeholder"
        )
    if "{write_out}" not in " ".join(curl_download_command):
        raise ConfigError(
            "engine.curl_download_command must carry the {write_out} placeholder"
        )
    curl_query_command = _string_list(
        raw.get("curl_query_command"), "engine.curl_query_command"
    )
    if not curl_query_command:
        raise ConfigError("engine.curl_query_command must not be empty")
    curl_parallel_command = _string_list(
        raw.get("curl_parallel_command"), "engine.curl_parallel_command"
    )
    if not curl_parallel_command:
        raise ConfigError("engine.curl_parallel_command must not be empty")
    for placeholder in ("{parallel_max}", "{timeout_seconds}", "{write_out}"):
        if placeholder not in " ".join(curl_parallel_command):
            raise ConfigError(
                "engine.curl_parallel_command must carry the "
                f"{placeholder} placeholder"
            )
    curl_parallel_write_out = _nonempty_string_field(
        raw.get("curl_parallel_write_out"),
        "engine.curl_parallel_write_out",
    )
    curl_parallel_source_marker = _nonempty_string_field(
        raw.get("curl_parallel_source_marker"),
        "engine.curl_parallel_source_marker",
    )
    os_release_family_keys = _string_list(
        raw.get("os_release_family_keys"), "engine.os_release_family_keys"
    )
    if not os_release_family_keys:
        raise ConfigError("engine.os_release_family_keys must not be empty")
    os_release_debian_family_names = _string_list(
        raw.get("os_release_debian_family_names"),
        "engine.os_release_debian_family_names",
    )
    if not os_release_debian_family_names:
        raise ConfigError("engine.os_release_debian_family_names must not be empty")
    curl_download_write_out = _nonempty_string_field(
        raw.get("curl_download_write_out"), "engine.curl_download_write_out"
    )
    report_json_indent = _int_field(
        raw.get("report_json_indent"), "engine.report_json_indent"
    )
    if report_json_indent < 0:
        raise ConfigError("engine.report_json_indent must not be negative")
    ssh_report_command_format = _nonempty_string_field(
        raw.get("ssh_report_command_format"),
        "engine.ssh_report_command_format",
    )
    for placeholder in ("{port}", "{address}", "{proxy_option}"):
        if placeholder not in ssh_report_command_format:
            raise ConfigError(
                "engine.ssh_report_command_format must carry the "
                f"{placeholder} placeholder"
            )
    ssh_report_proxy_option_format = _nonempty_string_field(
        raw.get("ssh_report_proxy_option_format"),
        "engine.ssh_report_proxy_option_format",
    )
    if "{proxy_command}" not in ssh_report_proxy_option_format:
        raise ConfigError(
            "engine.ssh_report_proxy_option_format must carry the "
            "{proxy_command} placeholder"
        )
    ssh_report_socks_command_format = _nonempty_string_field(
        raw.get("ssh_report_socks_command_format"),
        "engine.ssh_report_socks_command_format",
    )
    if "{proxy}" not in ssh_report_socks_command_format:
        raise ConfigError(
            "engine.ssh_report_socks_command_format must carry the "
            "{proxy} placeholder"
        )
    ssh_report_proxy_host = _nonempty_string_field(
        raw.get("ssh_report_proxy_host"), "engine.ssh_report_proxy_host"
    )
    report_record_keys = _string_map(
        raw.get("report_record_keys"), "engine.report_record_keys"
    )
    for field in (
        "channel",
        "address",
        "port",
        "ssh",
        "note",
        "server",
        "local_port",
        "remote_port",
    ):
        if not report_record_keys.get(field):
            raise ConfigError(
                f"engine.report_record_keys must name the {field} field"
            )
    bytes_per_kib = _positive_int_field(
        raw.get("bytes_per_kib"), "engine.bytes_per_kib"
    )
    bytes_per_mib = _positive_int_field(
        raw.get("bytes_per_mib"), "engine.bytes_per_mib"
    )
    if bytes_per_mib != bytes_per_kib * bytes_per_kib:
        raise ConfigError(
            "engine.bytes_per_mib must be engine.bytes_per_kib squared"
        )
    return EngineConfig(
        task_data_root=Path(task_data_root),
        systemd_unit_dir=Path(systemd_unit_dir),
        notice_timeout=_int_field(raw.get("notice_timeout"), "engine.notice_timeout"),
        command_timeout_seconds=_int_field(
            raw.get("command_timeout_seconds"), "engine.command_timeout_seconds"
        ),
        curl_timeout_seconds=curl_timeout_seconds,
        curl_download_timeout_seconds=curl_download_timeout_seconds,
        curl_retries=curl_retries,
        curl_retry_delay_seconds=curl_retry_delay_seconds,
        curl_connect_timeout_seconds=curl_connect_timeout_seconds,
        curl_retry_max_time_seconds=curl_retry_max_time_seconds,
        curl_download_command=curl_download_command,
        curl_query_command=curl_query_command,
        curl_parallel_command=curl_parallel_command,
        curl_parallel_write_out=curl_parallel_write_out,
        curl_parallel_source_marker=_checked_parallel_source_marker(
            curl_parallel_write_out, curl_parallel_source_marker
        ),
        report_json_indent=report_json_indent,
        ssh_report_command_format=ssh_report_command_format,
        ssh_report_proxy_option_format=ssh_report_proxy_option_format,
        ssh_report_socks_command_format=ssh_report_socks_command_format,
        ssh_report_proxy_host=ssh_report_proxy_host,
        report_record_keys=report_record_keys,
        curl_download_write_out=curl_download_write_out,
        os_release_family_keys=os_release_family_keys,
        os_release_debian_family_names=os_release_debian_family_names,
        github_latest_release_url=_nonempty_string_field(
            raw.get("github_latest_release_url"),
            "engine.github_latest_release_url",
        ),
        github_release_download_url=_nonempty_string_field(
            raw.get("github_release_download_url"),
            "engine.github_release_download_url",
        ),
        release_asset_architectures=_string_map(
            raw.get("release_asset_architectures"),
            "engine.release_asset_architectures",
        ),
        partial_download_file_suffix=_nonempty_string_field(
            raw.get("partial_download_file_suffix"),
            "engine.partial_download_file_suffix",
        ),
        system_python=_nonempty_string_field(
            raw.get("system_python"), "engine.system_python"
        ),
        journal_identifier=_nonempty_string_field(
            raw.get("journal_identifier"), "engine.journal_identifier"
        ),
        root_owner_uid=_int_field(raw.get("root_owner_uid"), "engine.root_owner_uid"),
        root_owner_gid=_int_field(raw.get("root_owner_gid"), "engine.root_owner_gid"),
        percent_scale=_positive_int_field(
            raw.get("percent_scale"), "engine.percent_scale"
        ),
        bytes_per_kib=bytes_per_kib,
        bytes_per_mib=bytes_per_mib,
        error_priority=error_priority,
        progress_priority=progress_priority,
        process_check_timeout_seconds=_int_field(
            raw.get("process_check_timeout_seconds"),
            "engine.process_check_timeout_seconds",
        ),
        task_start_delay_seconds=_float_field(
            raw.get("task_start_delay_seconds"), "engine.task_start_delay_seconds"
        ),
        desktop_detect_processes=tuple(desktop_detect_processes),
        desktop_username=desktop_username,
        session_environment_command=tuple(session_environment_command),
        session_environment_keys=tuple(session_environment_keys),
        session_bus_key=session_bus_key,
        session_display_keys=tuple(session_display_keys),
        upnpc_status_command=_placeholder_command_field(
            raw.get("upnpc_status_command"),
            "engine.upnpc_status_command",
            ("{command}",),
        ),
        upnpc_mapping_list_command=_placeholder_command_field(
            raw.get("upnpc_mapping_list_command"),
            "engine.upnpc_mapping_list_command",
            ("{command}",),
        ),
        upnpc_mapping_add_command=_placeholder_command_field(
            raw.get("upnpc_mapping_add_command"),
            "engine.upnpc_mapping_add_command",
            (
                "{command}",
                "{description}",
                "{internal_address}",
                "{port}",
                "{protocol}",
            ),
        ),
        upnpc_external_address_key=_nonempty_string_field(
            raw.get("upnpc_external_address_key"),
            "engine.upnpc_external_address_key",
        ),
        upnpc_protocol_names=_string_list(
            raw.get("upnpc_protocol_names"), "engine.upnpc_protocol_names"
        ),
        upnpc_mapping_arrow=_nonempty_string_field(
            raw.get("upnpc_mapping_arrow"), "engine.upnpc_mapping_arrow"
        ),
        local_addresses_command=_placeholder_command_field(
            raw.get("local_addresses_command"),
            "engine.local_addresses_command",
            (),
        ),
        directly_connected_networks_command=_placeholder_command_field(
            raw.get("directly_connected_networks_command"),
            "engine.directly_connected_networks_command",
            ("{family}",),
        ),
        default_route_command=_placeholder_command_field(
            raw.get("default_route_command"),
            "engine.default_route_command",
            (),
        ),
    )





# from ffmpeg_setup.py


def _ffmpeg_setup_table(raw: object) -> FfmpegSetupConfig:
    """Validate the [ffmpeg_setup] table and build FfmpegSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[ffmpeg_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("ffmpeg_setup.packages must be an array of strings")
    wayrecord_bin_path = raw.get("wayrecord_bin_path")
    if not isinstance(wayrecord_bin_path, str):
        raise ConfigError("ffmpeg_setup.wayrecord_bin_path must be a string")
    wayrecord_desktop_path = raw.get("wayrecord_desktop_path")
    if not isinstance(wayrecord_desktop_path, str):
        raise ConfigError("ffmpeg_setup.wayrecord_desktop_path must be a string")
    wayrecord_source_file_names = raw.get("wayrecord_source_file_names")
    if not isinstance(wayrecord_source_file_names, list) or not all(
        isinstance(name, str) and name for name in wayrecord_source_file_names
    ):
        raise ConfigError(
            "ffmpeg_setup.wayrecord_source_file_names must be an array of "
            "non-empty strings"
        )
    wayrecord_desktop_template_file_name = _nonempty_string_field(
        raw.get("wayrecord_desktop_template_file_name"),
        "ffmpeg_setup.wayrecord_desktop_template_file_name",
    )
    wayrecord_build_file_suffix = _nonempty_string_field(
        raw.get("wayrecord_build_file_suffix"),
        "ffmpeg_setup.wayrecord_build_file_suffix",
    )
    wayrecord_build_flags_command = _string_list(
        raw.get("wayrecord_build_flags_command"),
        "ffmpeg_setup.wayrecord_build_flags_command",
    )
    wayrecord_compile_command = _string_list(
        raw.get("wayrecord_compile_command"),
        "ffmpeg_setup.wayrecord_compile_command",
    )
    return FfmpegSetupConfig(
        packages=tuple(packages),
        wayrecord_bin_path=Path(wayrecord_bin_path),
        wayrecord_desktop_path=Path(wayrecord_desktop_path),
        wayrecord_file_mode=_octal_mode_field(
            raw.get("wayrecord_file_mode"), "ffmpeg_setup.wayrecord_file_mode"
        ),
        wayrecord_source_file_names=tuple(wayrecord_source_file_names),
        wayrecord_desktop_template_file_name=wayrecord_desktop_template_file_name,
        wayrecord_build_file_suffix=wayrecord_build_file_suffix,
        wayrecord_build_flags_command=wayrecord_build_flags_command,
        wayrecord_compile_command=wayrecord_compile_command,
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "ffmpeg_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "ffmpeg_setup.package_install_retries",
        ),
    )





# from hostname.py


def _hostname_table(raw: object) -> HostnameConfig:
    """Validate the [hostname] table and build HostnameConfig.

    hostname_file is a non-empty string; hostname_random_bytes is a
    positive integer; set_hostname_command is a non-empty array of
    non-empty strings.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[hostname] section is missing or not a table")
    hostname_file = _nonempty_string_field(
        raw.get("hostname_file"), "hostname.hostname_file"
    )
    hostname_random_bytes = _positive_int_field(
        raw.get("hostname_random_bytes"), "hostname.hostname_random_bytes"
    )
    command = raw.get("set_hostname_command")
    if not isinstance(command, list) or not command:
        raise ConfigError(
            "hostname.set_hostname_command must be a non-empty array of strings"
        )
    if not all(isinstance(part, str) and part.strip() for part in command):
        raise ConfigError(
            "hostname.set_hostname_command must be non-empty strings"
        )
    return HostnameConfig(
        hostname_file=hostname_file,
        hostname_random_bytes=hostname_random_bytes,
        set_hostname_command=tuple(part.strip() for part in command),
    )





# from i2pd_service_setup.py


def _i2pd_service_setup_table(raw: object) -> I2pdServiceSetupConfig:
    """Validate the [i2pd_service_setup] table and build the config.

    github_repo, download_dir, service_unit_name and config_path are
    non-empty strings; log_level is one of the I2PD_LOG_LEVELS values;
    bandwidth is a positive integer in kilobytes per second and share is
    an integer percentage between 0 and 100; http_enabled and
    socks_proxy_enabled are strict booleans; socks_proxy_port is a TCP
    port, because the telemetry builds the ssh command over I2P through
    it; install_retries and
    start_check_attempts are positive integers;
    start_check_retry_delay_seconds is positive, so the readiness loop
    always waits between attempts. tunnels_config_path and
    tunnel_keys_path are non-empty strings; tunnel_name and tunnel_host
    are non-empty strings; address_file_path is a non-empty string and
    address_file_mode is an octal mode string.
    codename_asset_name_template and generic_asset_name_template are
    non-empty strings, os_release_codename_key names the os-release field
    the codename comes from, and config_true_value and
    config_false_value are non-empty strings; version_command and the
    three service commands are string lists, and
    config_template_file_name and tunnels_template_file_name are
    non-empty strings. address_check_attempts is a positive integer and
    address_check_retry_delay_seconds is positive, so the identity wait
    always pauses between two decodes.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[i2pd_service_setup] section is missing or not a table")
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "i2pd_service_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(
            raw.get("download_dir"), "i2pd_service_setup.download_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "i2pd_service_setup.service_unit_name"
    )
    config_path = Path(
        _nonempty_string_field(
            raw.get("config_path"), "i2pd_service_setup.config_path"
        )
    )
    log_level = raw.get("log_level")
    if log_level not in I2PD_LOG_LEVELS:
        raise ConfigError(
            "i2pd_service_setup.log_level must be one of "
            + ", ".join(I2PD_LOG_LEVELS)
        )
    bandwidth = _int_field(
        raw.get("bandwidth"), "i2pd_service_setup.bandwidth"
    )
    if bandwidth < 1:
        raise ConfigError("i2pd_service_setup.bandwidth must be positive")
    share = _int_field(raw.get("share"), "i2pd_service_setup.share")
    if share < 0 or share > 100:
        raise ConfigError(
            "i2pd_service_setup.share must be between 0 and 100"
        )
    http_enabled = raw.get("http_enabled")
    if not isinstance(http_enabled, bool):
        raise ConfigError("i2pd_service_setup.http_enabled must be a boolean")
    socks_proxy_enabled = raw.get("socks_proxy_enabled")
    if not isinstance(socks_proxy_enabled, bool):
        raise ConfigError(
            "i2pd_service_setup.socks_proxy_enabled must be a boolean"
        )
    socks_proxy_port = _int_field(
        raw.get("socks_proxy_port"), "i2pd_service_setup.socks_proxy_port"
    )
    if not 1 <= socks_proxy_port <= 65535:
        raise ConfigError(
            "i2pd_service_setup.socks_proxy_port must be a TCP port"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "i2pd_service_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("i2pd_service_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "i2pd_service_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "i2pd_service_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "i2pd_service_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "i2pd_service_setup.start_check_retry_delay_seconds must be positive"
        )
    tunnels_config_path = Path(
        _nonempty_string_field(
            raw.get("tunnels_config_path"), "i2pd_service_setup.tunnels_config_path"
        )
    )
    tunnel_name = _nonempty_string_field(
        raw.get("tunnel_name"), "i2pd_service_setup.tunnel_name"
    )
    tunnel_host = _nonempty_string_field(
        raw.get("tunnel_host"), "i2pd_service_setup.tunnel_host"
    )
    tunnel_keys_path = Path(
        _nonempty_string_field(
            raw.get("tunnel_keys_path"), "i2pd_service_setup.tunnel_keys_path"
        )
    )
    address_file_path = Path(
        _nonempty_string_field(
            raw.get("address_file_path"), "i2pd_service_setup.address_file_path"
        )
    )
    address_file_mode = _octal_mode_field(
        raw.get("address_file_mode"), "i2pd_service_setup.address_file_mode"
    )
    os_release_file_path = Path(
        _nonempty_string_field(
            raw.get("os_release_file_path"),
            "i2pd_service_setup.os_release_file_path",
        )
    )
    codename_asset_name_template = _nonempty_string_field(
        raw.get("codename_asset_name_template"),
        "i2pd_service_setup.codename_asset_name_template",
    )
    generic_asset_name_template = _nonempty_string_field(
        raw.get("generic_asset_name_template"),
        "i2pd_service_setup.generic_asset_name_template",
    )
    os_release_codename_key = _nonempty_string_field(
        raw.get("os_release_codename_key"),
        "i2pd_service_setup.os_release_codename_key",
    )
    config_template_file_name = _nonempty_string_field(
        raw.get("config_template_file_name"),
        "i2pd_service_setup.config_template_file_name",
    )
    tunnels_template_file_name = _nonempty_string_field(
        raw.get("tunnels_template_file_name"),
        "i2pd_service_setup.tunnels_template_file_name",
    )
    config_true_value = _nonempty_string_field(
        raw.get("config_true_value"),
        "i2pd_service_setup.config_true_value",
    )
    config_false_value = _nonempty_string_field(
        raw.get("config_false_value"),
        "i2pd_service_setup.config_false_value",
    )
    address_check_attempts = _int_field(
        raw.get("address_check_attempts"),
        "i2pd_service_setup.address_check_attempts",
    )
    if address_check_attempts < 1:
        raise ConfigError(
            "i2pd_service_setup.address_check_attempts must be positive"
        )
    address_check_retry_delay_seconds = _float_field(
        raw.get("address_check_retry_delay_seconds"),
        "i2pd_service_setup.address_check_retry_delay_seconds",
    )
    if address_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "i2pd_service_setup.address_check_retry_delay_seconds must be positive"
        )
    return I2pdServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        os_release_file_path=os_release_file_path,
        config_path=config_path,
        log_level=log_level,
        bandwidth=bandwidth,
        share=share,
        http_enabled=http_enabled,
        socks_proxy_enabled=socks_proxy_enabled,
        socks_proxy_port=socks_proxy_port,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        tunnels_config_path=tunnels_config_path,
        tunnel_name=tunnel_name,
        tunnel_host=tunnel_host,
        tunnel_keys_path=tunnel_keys_path,
        address_file_path=address_file_path,
        address_file_mode=address_file_mode,
        codename_asset_name_template=codename_asset_name_template,
        generic_asset_name_template=generic_asset_name_template,
        os_release_codename_key=os_release_codename_key,
        version_command=_string_list(
            raw.get("version_command"), "i2pd_service_setup.version_command"
        ),
        service_enable_command=_string_list(
            raw.get("service_enable_command"),
            "i2pd_service_setup.service_enable_command",
        ),
        service_start_command=_string_list(
            raw.get("service_start_command"),
            "i2pd_service_setup.service_start_command",
        ),
        service_restart_command=_string_list(
            raw.get("service_restart_command"),
            "i2pd_service_setup.service_restart_command",
        ),
        config_template_file_name=config_template_file_name,
        tunnels_template_file_name=tunnels_template_file_name,
        config_true_value=config_true_value,
        config_false_value=config_false_value,
        address_check_attempts=address_check_attempts,
        address_check_retry_delay_seconds=address_check_retry_delay_seconds,
        report_channel_name=_nonempty_string_field(
            raw.get("report_channel_name"),
            "i2pd_service_setup.report_channel_name",
        ),
    )





# from imagemagick_setup.py


def _imagemagick_setup_table(raw: object) -> ImagemagickSetupConfig:
    """Validate the [imagemagick_setup] table and build ImagemagickSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[imagemagick_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("imagemagick_setup.packages must be an array of strings")
    policy_path = raw.get("policy_path")
    if not isinstance(policy_path, str):
        raise ConfigError("imagemagick_setup.policy_path must be a string")
    policy_template_file_name = _nonempty_string_field(
        raw.get("policy_template_file_name"),
        "imagemagick_setup.policy_template_file_name",
    )
    policy_backup_file_suffix = _nonempty_string_field(
        raw.get("policy_backup_file_suffix"),
        "imagemagick_setup.policy_backup_file_suffix",
    )
    return ImagemagickSetupConfig(
        packages=tuple(packages),
        policy_path=Path(policy_path),
        policy_template_file_name=policy_template_file_name,
        policy_backup_file_suffix=policy_backup_file_suffix,
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "imagemagick_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "imagemagick_setup.package_install_retries",
        ),
    )





# from kde_keyboard_setup.py


def _kde_keyboard_setup_table(raw: object) -> KdeKeyboardSetupConfig:
    """Validate the [kde_keyboard_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[kde_keyboard_setup] section is missing or not a table")
    return KdeKeyboardSetupConfig(
        packages=_string_list(raw.get("packages"), "kde_keyboard_setup.packages"),
        username=_nonempty_string_field(
            raw.get("username"), "kde_keyboard_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "kde_keyboard_setup.home_dir"
        ),
        config_dir=_nonempty_string_field(
            raw.get("config_dir"), "kde_keyboard_setup.config_dir"
        ),
        kxkbrc_file_name=_nonempty_string_field(
            raw.get("kxkbrc_file_name"), "kde_keyboard_setup.kxkbrc_file_name"
        ),
        appletsrc_file_name=_nonempty_string_field(
            raw.get("appletsrc_file_name"),
            "kde_keyboard_setup.appletsrc_file_name",
        ),
        applet_plugin=_nonempty_string_field(
            raw.get("applet_plugin"), "kde_keyboard_setup.applet_plugin"
        ),
        layouts=_string_list(raw.get("layouts"), "kde_keyboard_setup.layouts"),
        switch_option=_nonempty_string_field(
            raw.get("switch_option"), "kde_keyboard_setup.switch_option"
        ),
        reset_old_options=_bool_field(
            raw.get("reset_old_options"), "kde_keyboard_setup.reset_old_options"
        ),
        switch_mode=_nonempty_string_field(
            raw.get("switch_mode"), "kde_keyboard_setup.switch_mode"
        ),
        use_layout_switching=_bool_field(
            raw.get("use_layout_switching"),
            "kde_keyboard_setup.use_layout_switching",
        ),
        indicator_display_style=_nonempty_string_field(
            raw.get("indicator_display_style"),
            "kde_keyboard_setup.indicator_display_style",
        ),
        kwin_reload_command=_string_list(
            raw.get("kwin_reload_command"), "kde_keyboard_setup.kwin_reload_command"
        ),
        panel_restart_command=_string_list(
            raw.get("panel_restart_command"),
            "kde_keyboard_setup.panel_restart_command",
        ),
        kxkbrc_group=_string_list(
            raw.get("kxkbrc_group"), "kde_keyboard_setup.kxkbrc_group"
        ),
        applet_configuration_group=_string_list(
            raw.get("applet_configuration_group"),
            "kde_keyboard_setup.applet_configuration_group",
        ),
        shortcuts_file_name=_nonempty_string_field(
            raw.get("shortcuts_file_name"),
            "kde_keyboard_setup.shortcuts_file_name",
        ),
        kxkbrc_key_layout_list=_nonempty_string_field(
            raw.get("kxkbrc_key_layout_list"),
            "kde_keyboard_setup.kxkbrc_key_layout_list",
        ),
        kxkbrc_key_display_names=_nonempty_string_field(
            raw.get("kxkbrc_key_display_names"),
            "kde_keyboard_setup.kxkbrc_key_display_names",
        ),
        kxkbrc_key_variant_list=_nonempty_string_field(
            raw.get("kxkbrc_key_variant_list"),
            "kde_keyboard_setup.kxkbrc_key_variant_list",
        ),
        kxkbrc_key_options=_nonempty_string_field(
            raw.get("kxkbrc_key_options"),
            "kde_keyboard_setup.kxkbrc_key_options",
        ),
        kxkbrc_key_reset_old_options=_nonempty_string_field(
            raw.get("kxkbrc_key_reset_old_options"),
            "kde_keyboard_setup.kxkbrc_key_reset_old_options",
        ),
        kxkbrc_key_switch_mode=_nonempty_string_field(
            raw.get("kxkbrc_key_switch_mode"),
            "kde_keyboard_setup.kxkbrc_key_switch_mode",
        ),
        kxkbrc_key_use=_nonempty_string_field(
            raw.get("kxkbrc_key_use"), "kde_keyboard_setup.kxkbrc_key_use"
        ),
        display_style_key=_nonempty_string_field(
            raw.get("display_style_key"),
            "kde_keyboard_setup.display_style_key",
        ),
        kconfig_true_value=_nonempty_string_field(
            raw.get("kconfig_true_value"),
            "kde_keyboard_setup.kconfig_true_value",
        ),
        kconfig_false_value=_nonempty_string_field(
            raw.get("kconfig_false_value"),
            "kde_keyboard_setup.kconfig_false_value",
        ),
        layout_switcher_component_unique=_nonempty_string_field(
            raw.get("layout_switcher_component_unique"),
            "kde_keyboard_setup.layout_switcher_component_unique",
        ),
        layout_switcher_component_friendly=_nonempty_string_field(
            raw.get("layout_switcher_component_friendly"),
            "kde_keyboard_setup.layout_switcher_component_friendly",
        ),
        shortcut_modifier_bits=_int_map(
            raw.get("shortcut_modifier_bits"),
            "kde_keyboard_setup.shortcut_modifier_bits",
        ),
        layout_switch_shortcuts=_string_map(
            raw.get("layout_switch_shortcuts", {}),
            "kde_keyboard_setup.layout_switch_shortcuts",
        ),
        apply_hotkeys_script_file_name=_nonempty_string_field(
            raw.get("apply_hotkeys_script_file_name"),
            "kde_keyboard_setup.apply_hotkeys_script_file_name",
        ),
        runuser_command=_placeholder_command_field(
            raw.get("runuser_command"),
            "kde_keyboard_setup.runuser_command",
            ("{username}",),
        ),
        kreadconfig_command=_placeholder_command_field(
            raw.get("kreadconfig_command"),
            "kde_keyboard_setup.kreadconfig_command",
            ("{file_name}",),
        ),
        kwriteconfig_command=_placeholder_command_field(
            raw.get("kwriteconfig_command"),
            "kde_keyboard_setup.kwriteconfig_command",
            ("{file_name}",),
        ),
        config_group_flag=_placeholder_command_field(
            raw.get("config_group_flag"),
            "kde_keyboard_setup.config_group_flag",
            ("{group}",),
        ),
        config_key_flag=_placeholder_command_field(
            raw.get("config_key_flag"),
            "kde_keyboard_setup.config_key_flag",
            ("{key}",),
        ),
        config_bool_type_flag=_string_list(
            raw.get("config_bool_type_flag"),
            "kde_keyboard_setup.config_bool_type_flag",
        ),
        mkdir_command=_placeholder_command_field(
            raw.get("mkdir_command"),
            "kde_keyboard_setup.mkdir_command",
            ("{path}",),
        ),
    )





# from kde_settings.py


def _kde_settings_table(raw: object) -> KdeSettingsConfig:
    """Validate the [kde_settings] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[kde_settings] section is missing or not a table")
    return KdeSettingsConfig(
        packages=_string_list(raw.get("packages"), "kde_settings.packages"),
        username=_nonempty_string_field(
            raw.get("username"), "kde_settings.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "kde_settings.home_dir"
        ),
        user_dirs=_string_map(raw.get("user_dirs"), "kde_settings.user_dirs"),
        places_hidden=(
            _string_list(raw["places_hidden"], "kde_settings.places_hidden")
            if "places_hidden" in raw
            else ()
        ),
        places_namespaces=_string_map(
            raw.get("places_namespaces"), "kde_settings.places_namespaces"
        ),
        places_metadata_owner=_nonempty_string_field(
            raw.get("places_metadata_owner"),
            "kde_settings.places_metadata_owner",
        ),
        kdeglobals_file_name=_nonempty_string_field(
            raw.get("kdeglobals_file_name"),
            "kde_settings.kdeglobals_file_name",
        ),
        kcminputrc_file_name=_nonempty_string_field(
            raw.get("kcminputrc_file_name"),
            "kde_settings.kcminputrc_file_name",
        ),
        kwinrc_file_name=_nonempty_string_field(
            raw.get("kwinrc_file_name"), "kde_settings.kwinrc_file_name"
        ),
        plasma_keyboard_file_name=_nonempty_string_field(
            raw.get("plasma_keyboard_file_name"),
            "kde_settings.plasma_keyboard_file_name",
        ),
        global_shortcuts_file_name=_nonempty_string_field(
            raw.get("global_shortcuts_file_name"),
            "kde_settings.global_shortcuts_file_name",
        ),
        general_group=_string_list(
            raw.get("general_group"), "kde_settings.general_group"
        ),
        kde_group=_string_list(raw.get("kde_group"), "kde_settings.kde_group"),
        mouse_group=_string_list(
            raw.get("mouse_group"), "kde_settings.mouse_group"
        ),
        keyboard_group=_string_list(
            raw.get("keyboard_group"), "kde_settings.keyboard_group"
        ),
        wayland_group=_string_list(
            raw.get("wayland_group"), "kde_settings.wayland_group"
        ),
        virtual_keyboard_group=_string_list(
            raw.get("virtual_keyboard_group"),
            "kde_settings.virtual_keyboard_group",
        ),
        plugins_group=_string_list(
            raw.get("plugins_group"), "kde_settings.plugins_group"
        ),
        desktops_group=_string_list(
            raw.get("desktops_group"), "kde_settings.desktops_group"
        ),
        look_and_feel_package_key=_nonempty_string_field(
            raw.get("look_and_feel_package_key"),
            "kde_settings.look_and_feel_package_key",
        ),
        color_scheme_key=_nonempty_string_field(
            raw.get("color_scheme_key"), "kde_settings.color_scheme_key"
        ),
        automatic_look_and_feel_key=_nonempty_string_field(
            raw.get("automatic_look_and_feel_key"),
            "kde_settings.automatic_look_and_feel_key",
        ),
        automatic_look_and_feel_idle_interval_key=_nonempty_string_field(
            raw.get("automatic_look_and_feel_idle_interval_key"),
            "kde_settings.automatic_look_and_feel_idle_interval_key",
        ),
        numlock_key=_nonempty_string_field(
            raw.get("numlock_key"), "kde_settings.numlock_key"
        ),
        input_method_key=_nonempty_string_field(
            raw.get("input_method_key"), "kde_settings.input_method_key"
        ),
        input_method_locales_key=_nonempty_string_field(
            raw.get("input_method_locales_key"),
            "kde_settings.input_method_locales_key",
        ),
        cursor_theme_key=_nonempty_string_field(
            raw.get("cursor_theme_key"), "kde_settings.cursor_theme_key"
        ),
        click_method_key=_nonempty_string_field(
            raw.get("click_method_key"), "kde_settings.click_method_key"
        ),
        touchpad_disable_external_mouse_key=_nonempty_string_field(
            raw.get("touchpad_disable_external_mouse_key"),
            "kde_settings.touchpad_disable_external_mouse_key",
        ),
        desktop_count_key=_nonempty_string_field(
            raw.get("desktop_count_key"), "kde_settings.desktop_count_key"
        ),
        kconfig_true_value=_nonempty_string_field(
            raw.get("kconfig_true_value"), "kde_settings.kconfig_true_value"
        ),
        kconfig_false_value=_nonempty_string_field(
            raw.get("kconfig_false_value"),
            "kde_settings.kconfig_false_value",
        ),
        numlock_values=_string_map(
            raw.get("numlock_values"), "kde_settings.numlock_values"
        ),
        click_method_values=_string_map(
            raw.get("click_method_values"),
            "kde_settings.click_method_values",
        ),
        automatic_theme_switch_idle_interval=_nonempty_string_field(
            raw.get("automatic_theme_switch_idle_interval"),
            "kde_settings.automatic_theme_switch_idle_interval",
        ),
        places_root_tag=_nonempty_string_field(
            raw.get("places_root_tag"), "kde_settings.places_root_tag"
        ),
        places_bookmark_tag=_nonempty_string_field(
            raw.get("places_bookmark_tag"),
            "kde_settings.places_bookmark_tag",
        ),
        places_title_tag=_nonempty_string_field(
            raw.get("places_title_tag"), "kde_settings.places_title_tag"
        ),
        places_metadata_path=_nonempty_string_field(
            raw.get("places_metadata_path"),
            "kde_settings.places_metadata_path",
        ),
        places_metadata_owner_attribute=_nonempty_string_field(
            raw.get("places_metadata_owner_attribute"),
            "kde_settings.places_metadata_owner_attribute",
        ),
        places_hidden_element=_nonempty_string_field(
            raw.get("places_hidden_element"),
            "kde_settings.places_hidden_element",
        ),
        places_hidden_value=_nonempty_string_field(
            raw.get("places_hidden_value"),
            "kde_settings.places_hidden_value",
        ),
        kwin_scripts=_string_list(
            raw.get("kwin_scripts"), "kde_settings.kwin_scripts"
        ),
        kwin_script_files=_string_list(
            raw.get("kwin_script_files"), "kde_settings.kwin_script_files"
        ),
        kwin_script_hotkeys=_string_list(
            raw.get("kwin_script_hotkeys"),
            "kde_settings.kwin_script_hotkeys",
        ),
        kwin_script_actions=_string_list(
            raw.get("kwin_script_actions"), "kde_settings.kwin_script_actions"
        ),
        desktop_ids_script_file_name=_nonempty_string_field(
            raw.get("desktop_ids_script_file_name"),
            "kde_settings.desktop_ids_script_file_name",
        ),
        kwin_scripts_dir_name=_nonempty_string_field(
            raw.get("kwin_scripts_dir_name"),
            "kde_settings.kwin_scripts_dir_name",
        ),
        konsole_profile_file_name=_nonempty_string_field(
            raw.get("konsole_profile_file_name"),
            "kde_settings.konsole_profile_file_name",
        ),
        runuser_command=_placeholder_command_field(
            raw.get("runuser_command"),
            "kde_settings.runuser_command",
            ("{username}",),
        ),
        kreadconfig_command=_placeholder_command_field(
            raw.get("kreadconfig_command"),
            "kde_settings.kreadconfig_command",
            ("{file_name}",),
        ),
        kwriteconfig_command=_placeholder_command_field(
            raw.get("kwriteconfig_command"),
            "kde_settings.kwriteconfig_command",
            ("{file_name}",),
        ),
        config_group_flag=_placeholder_command_field(
            raw.get("config_group_flag"),
            "kde_settings.config_group_flag",
            ("{group}",),
        ),
        config_key_flag=_placeholder_command_field(
            raw.get("config_key_flag"),
            "kde_settings.config_key_flag",
            ("{key}",),
        ),
        config_bool_type_flag=_string_list(
            raw.get("config_bool_type_flag"),
            "kde_settings.config_bool_type_flag",
        ),
        config_notify_flag=_string_list(
            raw.get("config_notify_flag"),
            "kde_settings.config_notify_flag",
        ),
        config_delete_flag=_string_list(
            raw.get("config_delete_flag"),
            "kde_settings.config_delete_flag",
        ),
        apply_look_and_feel_command=_placeholder_command_field(
            raw.get("apply_look_and_feel_command"),
            "kde_settings.apply_look_and_feel_command",
            ("{look_and_feel}",),
        ),
        apply_color_scheme_command=_placeholder_command_field(
            raw.get("apply_color_scheme_command"),
            "kde_settings.apply_color_scheme_command",
            ("{color_scheme}",),
        ),
        apply_cursor_theme_command=_placeholder_command_field(
            raw.get("apply_cursor_theme_command"),
            "kde_settings.apply_cursor_theme_command",
            ("{cursor_theme}",),
        ),
        mkdir_command=_placeholder_command_field(
            raw.get("mkdir_command"),
            "kde_settings.mkdir_command",
            ("{path}",),
        ),
        chown_command=_placeholder_command_field(
            raw.get("chown_command"),
            "kde_settings.chown_command",
            ("{owner}", "{path}"),
        ),
        chown_recursive_command=_placeholder_command_field(
            raw.get("chown_recursive_command"),
            "kde_settings.chown_recursive_command",
            ("{owner}", "{path}"),
        ),
        chmod_command=_placeholder_command_field(
            raw.get("chmod_command"),
            "kde_settings.chmod_command",
            ("{file_mode}", "{path}"),
        ),
        color_scheme=_nonempty_string_field(
            raw.get("color_scheme"), "kde_settings.color_scheme"
        ),
        look_and_feel=_nonempty_string_field(
            raw.get("look_and_feel"), "kde_settings.look_and_feel"
        ),
        look_and_feel_light=_nonempty_string_field(
            raw.get("look_and_feel_light"), "kde_settings.look_and_feel_light"
        ),
        automatic_look_and_feel=_bool_field(
            raw.get("automatic_look_and_feel"),
            "kde_settings.automatic_look_and_feel",
        ),
        cursor_theme=_nonempty_string_field(
            raw.get("cursor_theme"), "kde_settings.cursor_theme"
        ),
        cursor_theme_light=_nonempty_string_field(
            raw.get("cursor_theme_light"), "kde_settings.cursor_theme_light"
        ),
        numlock_on_boot=_enum_field(
            raw.get("numlock_on_boot"),
            "kde_settings.numlock_on_boot",
            NUMLOCK_STATES,
        ),
        touchpad_click_method=_enum_field(
            raw.get("touchpad_click_method"),
            "kde_settings.touchpad_click_method",
            CLICK_METHODS,
        ),
        touchpad_disable_on_external_mouse=_bool_field(
            raw.get("touchpad_disable_on_external_mouse"),
            "kde_settings.touchpad_disable_on_external_mouse",
        ),
        virtual_keyboard_enabled=_bool_field(
            raw.get("virtual_keyboard_enabled"),
            "kde_settings.virtual_keyboard_enabled",
        ),
        virtual_keyboard_input_method=_nonempty_string_field(
            raw.get("virtual_keyboard_input_method"),
            "kde_settings.virtual_keyboard_input_method",
        ),
        virtual_keyboard_locales=_string_list(
            raw.get("virtual_keyboard_locales"),
            "kde_settings.virtual_keyboard_locales",
        ),
        kwin_reload_command=_string_list(
            raw.get("kwin_reload_command"), "kde_settings.kwin_reload_command"
        ),
        sddm_conf_file=Path(
            _nonempty_string_field(
                raw.get("sddm_conf_file"), "kde_settings.sddm_conf_file"
            )
        ),
        sddm_theme_conf_file=Path(
            _nonempty_string_field(
                raw.get("sddm_theme_conf_file"), "kde_settings.sddm_theme_conf_file"
            )
        ),
        user_config_dir=_nonempty_string_field(
            raw.get("user_config_dir"), "kde_settings.user_config_dir"
        ),
        user_kwin_scripts_dir=Path(
            _nonempty_string_field(
                raw.get("user_kwin_scripts_dir"), "kde_settings.user_kwin_scripts_dir"
            )
        ),
        user_look_and_feel_dir=Path(
            _nonempty_string_field(
                raw.get("user_look_and_feel_dir"),
                "kde_settings.user_look_and_feel_dir",
            )
        ),
        user_places_file=Path(
            _nonempty_string_field(
                raw.get("user_places_file"), "kde_settings.user_places_file"
            )
        ),
        user_dirs_file=_nonempty_string_field(
            raw.get("user_dirs_file"), "kde_settings.user_dirs_file"
        ),
        konsole_profile_path=Path(
            _nonempty_string_field(
                raw.get("konsole_profile_path"), "kde_settings.konsole_profile_path"
            )
        ),
        script_file_mode=_octal_mode_field(
            raw.get("script_file_mode"), "kde_settings.script_file_mode"
        ),
        default_file_mode=_octal_mode_field(
            raw.get("default_file_mode"), "kde_settings.default_file_mode"
        ),
        system_look_and_feel_dir=Path(
            _nonempty_string_field(
                raw.get("system_look_and_feel_dir"),
                "kde_settings.system_look_and_feel_dir",
            )
        ),
        theme_defaults_dir=Path(
            _nonempty_string_field(
                raw.get("theme_defaults_dir"), "kde_settings.theme_defaults_dir"
            )
        ),
        sddm_autologin_user=_nonempty_string_field(
            raw.get("sddm_autologin_user"), "kde_settings.sddm_autologin_user"
        ),
        sddm_autologin_session=_nonempty_string_field(
            raw.get("sddm_autologin_session"),
            "kde_settings.sddm_autologin_session",
        ),
        sddm_theme=_nonempty_string_field(
            raw.get("sddm_theme"), "kde_settings.sddm_theme"
        ),
        sddm_theme_cursor_size=_nonempty_string_field(
            raw.get("sddm_theme_cursor_size"),
            "kde_settings.sddm_theme_cursor_size",
        ),
        sddm_theme_cursor_theme=_nonempty_string_field(
            raw.get("sddm_theme_cursor_theme"),
            "kde_settings.sddm_theme_cursor_theme",
        ),
        sddm_theme_font=_nonempty_string_field(
            raw.get("sddm_theme_font"), "kde_settings.sddm_theme_font"
        ),
        kconfig=_kconfig_records(raw.get("kconfig")),
    )


def _kconfig_record(raw: object, index: int) -> KConfigRecord:
    """Validate one [[kde_settings.kconfig]] record."""

    name = f"kde_settings.kconfig[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a table")
    file_name = _nonempty_string_field(raw.get("file"), f"{name}.file")
    group = _string_list(raw.get("group"), f"{name}.group")
    key = _nonempty_string_field(raw.get("key"), f"{name}.key")
    delete = _bool_field(raw.get("delete", False), f"{name}.delete")
    if delete:
        if raw.get("value") is not None:
            raise ConfigError(f"{name} must not have a value when delete is true")
        value = ""
    else:
        value = _nonempty_string_field(raw.get("value"), f"{name}.value")
    value_type = _enum_field(
        raw.get("type", "string"), f"{name}.type", KCONFIG_TYPES
    )
    return KConfigRecord(
        file=file_name,
        group=group,
        key=key,
        value=value,
        type=value_type,
        delete=delete,
    )


def _kconfig_records(raw: object) -> tuple[KConfigRecord, ...]:
    """Validate the [[kde_settings.kconfig]] records; empty when absent."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[kde_settings] kconfig must be an array of tables")
    return tuple(_kconfig_record(record, index) for index, record in enumerate(raw))





# from nextdns_setup_system_wide.py


def _nextdns_setup_system_wide_table(
    raw: object,
) -> NextdnsSetupSystemWideConfig:
    """Validate the [nextdns_setup_system_wide] table and build the config.

    Every value is required and typed: the vault group title and the
    profile ID file path are non-empty strings; profile_id_file_mode is
    an octal string and error_priority is an integer 0-7.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[nextdns_setup_system_wide] section is missing or not a table"
        )
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), "nextdns_setup_system_wide.vault_group_title"
    )
    profile_id_file_path = _nonempty_string_field(
        raw.get("profile_id_file_path"),
        "nextdns_setup_system_wide.profile_id_file_path",
    )
    profile_id_file_mode = _octal_mode_field(
        raw.get("profile_id_file_mode"),
        "nextdns_setup_system_wide.profile_id_file_mode",
    )
    error_priority = _int_field(
        raw.get("error_priority"), "nextdns_setup_system_wide.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "nextdns_setup_system_wide.error_priority must be between 0 and 7"
        )
    return NextdnsSetupSystemWideConfig(
        vault_group_title=vault_group_title,
        profile_id_file_path=Path(profile_id_file_path),
        profile_id_file_mode=profile_id_file_mode,
        error_priority=error_priority,
    )





# from playwright_setup.py


def _playwright_setup_table(raw: object) -> PlaywrightSetupConfig:
    """Validate the [playwright_setup] table and build PlaywrightSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[playwright_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("playwright_setup.packages must be an array of strings")
    return PlaywrightSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "playwright_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "playwright_setup.home_dir"
        ),
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "playwright_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "playwright_setup.package_install_retries",
        ),
        cli_package=_nonempty_string_field(
            raw.get("cli_package"), "playwright_setup.cli_package"
        ),
        user_prefix_relative_path=_nonempty_string_field(
            raw.get("user_prefix_relative_path"),
            "playwright_setup.user_prefix_relative_path",
        ),
        cli_bin_relative_path=_nonempty_string_field(
            raw.get("cli_bin_relative_path"),
            "playwright_setup.cli_bin_relative_path",
        ),
        runuser_command=_string_list(
            raw.get("runuser_command"), "playwright_setup.runuser_command"
        ),
        cli_version_command=_string_list(
            raw.get("cli_version_command"),
            "playwright_setup.cli_version_command",
        ),
        npm_install_command=_string_list(
            raw.get("npm_install_command"),
            "playwright_setup.npm_install_command",
        ),
        npm_install_timeout_seconds=_int_field(
            raw.get("npm_install_timeout_seconds"),
            "playwright_setup.npm_install_timeout_seconds",
        ),
    )





# from port_forwarding_setup.py


def _positive_int_field(raw: object, name: str) -> int:
    """Validate a positive integer config value."""

    value = _int_field(raw, name)
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _port_field(raw: object, name: str) -> int:
    """Validate a TCP port config value from 1 to 65535."""

    value = _int_field(raw, name)
    if not 1 <= value <= 65535:
        raise ConfigError(f"{name} must be a TCP port from 1 to 65535")
    return value


def _port_forwarding_setup_table(raw: object) -> PortForwardingSetupConfig:
    """Validate the [port_forwarding_setup] table and build the config.

    Every value is required and typed. The desired port range must be
    ordered and inside the TCP port space; the backoff values are
    positive integers, the reconnect pause grows geometrically by the
    multiplier until the ceiling; error_priority is an integer 0-7.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[port_forwarding_setup] section is missing or not a table"
        )
    section = "port_forwarding_setup."
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), section + "vault_group_title"
    )
    passphrase_entry_title = _nonempty_string_field(
        raw.get("passphrase_entry_title"), section + "passphrase_entry_title"
    )
    remote_ssh_user = _nonempty_string_field(
        raw.get("remote_ssh_user"), section + "remote_ssh_user"
    )
    desired_port_min = _port_field(
        raw.get("desired_port_min"), section + "desired_port_min"
    )
    desired_port_max = _port_field(
        raw.get("desired_port_max"), section + "desired_port_max"
    )
    if desired_port_min > desired_port_max:
        raise ConfigError(
            "port_forwarding_setup.desired_port_min must not exceed "
            "desired_port_max"
        )
    server_alive_interval_seconds = _positive_int_field(
        raw.get("server_alive_interval_seconds"),
        section + "server_alive_interval_seconds",
    )
    server_alive_count_max = _positive_int_field(
        raw.get("server_alive_count_max"), section + "server_alive_count_max"
    )
    connect_timeout_seconds = _positive_int_field(
        raw.get("connect_timeout_seconds"), section + "connect_timeout_seconds"
    )
    own_addresses_timeout_seconds = _positive_int_field(
        raw.get("own_addresses_timeout_seconds"),
        section + "own_addresses_timeout_seconds",
    )
    agent_start_timeout_seconds = _positive_int_field(
        raw.get("agent_start_timeout_seconds"),
        section + "agent_start_timeout_seconds",
    )
    key_unlock_timeout_seconds = _positive_int_field(
        raw.get("key_unlock_timeout_seconds"),
        section + "key_unlock_timeout_seconds",
    )
    askpass_helper_file_mode = _octal_mode_field(
        raw.get("askpass_helper_file_mode"), section + "askpass_helper_file_mode"
    )
    askpass_display = _nonempty_string_field(
        raw.get("askpass_display"), section + "askpass_display"
    )
    state_file_mode = _octal_mode_field(
        raw.get("state_file_mode"), section + "state_file_mode"
    )
    backoff_base_seconds = _positive_int_field(
        raw.get("backoff_base_seconds"), section + "backoff_base_seconds"
    )
    backoff_multiplier = _positive_int_field(
        raw.get("backoff_multiplier"), section + "backoff_multiplier"
    )
    backoff_max_seconds = _positive_int_field(
        raw.get("backoff_max_seconds"), section + "backoff_max_seconds"
    )
    state_file_path = Path(
        _nonempty_string_field(raw.get("state_file_path"), section + "state_file_path")
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), section + "service_unit_name"
    )
    service_restart_seconds = _positive_int_field(
        raw.get("service_restart_seconds"), section + "service_restart_seconds"
    )
    journal_identifier = _nonempty_string_field(
        raw.get("journal_identifier"), section + "journal_identifier"
    )
    service_template_file_name = _nonempty_string_field(
        raw.get("service_template_file_name"),
        section + "service_template_file_name",
    )
    service_module_name = _nonempty_string_field(
        raw.get("service_module_name"), section + "service_module_name"
    )
    command_checks: dict[str, tuple[str, ...]] = {}
    for key in (
        "systemctl_daemon_reload_command",
        "systemctl_enable_command",
        "systemctl_restart_command",
        "systemctl_is_failed_command",
    ):
        command = _string_list(raw.get(key), section + key)
        if not command:
            raise ConfigError(f"port_forwarding_setup.{key} must not be empty")
        command_checks[key] = command
    for key in (
        "systemctl_enable_command",
        "systemctl_restart_command",
        "systemctl_is_failed_command",
    ):
        if "{service_unit_name}" not in " ".join(command_checks[key]):
            raise ConfigError(
                f"port_forwarding_setup.{key} must carry the "
                "{service_unit_name} placeholder"
            )
    start_check_attempts = _positive_int_field(
        raw.get("start_check_attempts"), section + "start_check_attempts"
    )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        section + "start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "port_forwarding_setup.start_check_retry_delay_seconds "
            "must be positive"
        )
    service_commands: dict[str, tuple[str, ...]] = {}
    for key, required_placeholders in (
        ("own_addresses_command", ()),
        ("agent_start_command", ()),
        ("key_add_command", ("{key_path}",)),
        ("collector_trigger_command", ("{service_unit_name}",)),
        (
            "ssh_forward_command",
            (
                "{ssh_port}",
                "{key_path}",
                "{remote_port}",
                "{local_port}",
                "{user}",
                "{host}",
                "{remote_bind_address}",
                "{server_alive_interval_seconds}",
                "{server_alive_count_max}",
                "{connect_timeout_seconds}",
            ),
        ),
    ):
        command = _string_list(raw.get(key), section + key)
        if not command:
            raise ConfigError(f"port_forwarding_setup.{key} must not be empty")
        joined = " ".join(command)
        for placeholder in required_placeholders:
            if placeholder not in joined:
                raise ConfigError(
                    f"port_forwarding_setup.{key} must carry the "
                    f"{placeholder} placeholder"
                )
        service_commands[key] = command
    collector_trigger_timeout_seconds = _positive_int_field(
        raw.get("collector_trigger_timeout_seconds"),
        section + "collector_trigger_timeout_seconds",
    )
    remote_bind_address = _nonempty_string_field(
        raw.get("remote_bind_address"), section + "remote_bind_address"
    )
    agent_socket_env_key = _nonempty_string_field(
        raw.get("agent_socket_env_key"), section + "agent_socket_env_key"
    )
    agent_pid_env_key = _nonempty_string_field(
        raw.get("agent_pid_env_key"), section + "agent_pid_env_key"
    )
    display_env_key = _nonempty_string_field(
        raw.get("display_env_key"), section + "display_env_key"
    )
    passphrase_env_key = _nonempty_string_field(
        raw.get("passphrase_env_key"), section + "passphrase_env_key"
    )
    askpass_env = _string_map(raw.get("askpass_env"), section + "askpass_env")
    if not askpass_env:
        raise ConfigError("port_forwarding_setup.askpass_env must not be empty")
    if "{helper_path}" not in " ".join(askpass_env.values()):
        raise ConfigError(
            "port_forwarding_setup.askpass_env must carry the "
            "{helper_path} placeholder"
        )
    askpass_helper_dir_prefix = _nonempty_string_field(
        raw.get("askpass_helper_dir_prefix"),
        section + "askpass_helper_dir_prefix",
    )
    askpass_helper_file_name = _nonempty_string_field(
        raw.get("askpass_helper_file_name"), section + "askpass_helper_file_name"
    )
    askpass_helper_content = _nonempty_string_field(
        raw.get("askpass_helper_content"), section + "askpass_helper_content"
    )
    if passphrase_env_key not in askpass_helper_content:
        raise ConfigError(
            "port_forwarding_setup.askpass_helper_content must print "
            "port_forwarding_setup.passphrase_env_key"
        )
    forward_outcome_poll_seconds = _float_field(
        raw.get("forward_outcome_poll_seconds"),
        section + "forward_outcome_poll_seconds",
    )
    if forward_outcome_poll_seconds <= 0:
        raise ConfigError(
            "port_forwarding_setup.forward_outcome_poll_seconds must be positive"
        )
    state_temp_file_suffix = _nonempty_string_field(
        raw.get("state_temp_file_suffix"), section + "state_temp_file_suffix"
    )
    state_json_indent = _int_field(
        raw.get("state_json_indent"), section + "state_json_indent"
    )
    if state_json_indent < 0:
        raise ConfigError(
            "port_forwarding_setup.state_json_indent must not be negative"
        )
    error_priority = _int_field(raw.get("error_priority"), section + "error_priority")
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "port_forwarding_setup.error_priority must be between 0 and 7"
        )
    return PortForwardingSetupConfig(
        vault_group_title=vault_group_title,
        passphrase_entry_title=passphrase_entry_title,
        remote_ssh_user=remote_ssh_user,
        desired_port_min=desired_port_min,
        desired_port_max=desired_port_max,
        server_alive_interval_seconds=server_alive_interval_seconds,
        server_alive_count_max=server_alive_count_max,
        connect_timeout_seconds=connect_timeout_seconds,
        own_addresses_timeout_seconds=own_addresses_timeout_seconds,
        agent_start_timeout_seconds=agent_start_timeout_seconds,
        key_unlock_timeout_seconds=key_unlock_timeout_seconds,
        askpass_helper_file_mode=askpass_helper_file_mode,
        askpass_display=askpass_display,
        state_file_mode=state_file_mode,
        backoff_base_seconds=backoff_base_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_max_seconds=backoff_max_seconds,
        state_file_path=state_file_path,
        service_unit_name=service_unit_name,
        service_restart_seconds=service_restart_seconds,
        journal_identifier=journal_identifier,
        service_template_file_name=service_template_file_name,
        service_module_name=service_module_name,
        systemctl_daemon_reload_command=command_checks[
            "systemctl_daemon_reload_command"
        ],
        systemctl_enable_command=command_checks["systemctl_enable_command"],
        systemctl_restart_command=command_checks["systemctl_restart_command"],
        systemctl_is_failed_command=command_checks[
            "systemctl_is_failed_command"
        ],
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        own_addresses_command=service_commands["own_addresses_command"],
        agent_start_command=service_commands["agent_start_command"],
        key_add_command=service_commands["key_add_command"],
        collector_trigger_command=service_commands["collector_trigger_command"],
        collector_trigger_timeout_seconds=collector_trigger_timeout_seconds,
        ssh_forward_command=service_commands["ssh_forward_command"],
        remote_bind_address=remote_bind_address,
        agent_socket_env_key=agent_socket_env_key,
        agent_pid_env_key=agent_pid_env_key,
        display_env_key=display_env_key,
        passphrase_env_key=passphrase_env_key,
        askpass_env=askpass_env,
        askpass_helper_dir_prefix=askpass_helper_dir_prefix,
        askpass_helper_file_name=askpass_helper_file_name,
        askpass_helper_content=askpass_helper_content,
        forward_outcome_poll_seconds=forward_outcome_poll_seconds,
        state_temp_file_suffix=state_temp_file_suffix,
        state_json_indent=state_json_indent,
        report_channel_name=_nonempty_string_field(
            raw.get("report_channel_name"),
            "port_forwarding_setup.report_channel_name",
        ),
        error_priority=error_priority,
    )





# from rustdesk_setup.py


def _rustdesk_options(raw: object) -> tuple[RustdeskOptionConfig, ...]:
    """Validate the [rustdesk_setup.options] array of tables.

    Every option is a table with a non-empty key and a non-empty value; a
    missing array means no options. Duplicate keys are a config error, so
    the task never applies the same option twice with different values.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[rustdesk_setup] options must be an array of tables")
    result: list[RustdeskOptionConfig] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[rustdesk_setup] option entries must be tables")
        key = entry.get("key")
        value = entry.get("value")
        if not isinstance(key, str) or not key:
            raise ConfigError("[rustdesk_setup] option key must be a non-empty string")
        if not isinstance(value, str) or not value:
            raise ConfigError("[rustdesk_setup] option value must be a non-empty string")
        if key in seen:
            raise ConfigError(f"[rustdesk_setup] duplicate option key: {key}")
        seen.add(key)
        result.append(RustdeskOptionConfig(key=key, value=value))
    return tuple(result)


def _rustdesk_setup_table(raw: object) -> RustdeskSetupConfig:
    """Validate the [rustdesk_setup] table and build RustdeskSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[rustdesk_setup] section is missing or not a table")
    github_repo = raw.get("github_repo")
    if not isinstance(github_repo, str) or not github_repo:
        raise ConfigError("rustdesk_setup.github_repo must be a non-empty string")
    asset_name_template = _nonempty_string_field(
        raw.get("asset_name_template"),
        "rustdesk_setup.asset_name_template",
    )
    version_check_command = _string_list(
        raw.get("version_check_command"),
        "rustdesk_setup.version_check_command",
    )
    machine_id_command = _string_list(
        raw.get("machine_id_command"),
        "rustdesk_setup.machine_id_command",
    )
    get_option_command = _string_list(
        raw.get("get_option_command"),
        "rustdesk_setup.get_option_command",
    )
    set_option_command = _string_list(
        raw.get("set_option_command"),
        "rustdesk_setup.set_option_command",
    )
    set_password_command = _string_list(
        raw.get("set_password_command"),
        "rustdesk_setup.set_password_command",
    )
    service_stop_command = _string_list(
        raw.get("service_stop_command"),
        "rustdesk_setup.service_stop_command",
    )
    service_enable_command = _string_list(
        raw.get("service_enable_command"),
        "rustdesk_setup.service_enable_command",
    )
    service_start_command = _string_list(
        raw.get("service_start_command"),
        "rustdesk_setup.service_start_command",
    )
    identity_file_name = _nonempty_string_field(
        raw.get("identity_file_name"),
        "rustdesk_setup.identity_file_name",
    )
    readiness_probe_timeout_seconds = _positive_int_field(
        raw.get("readiness_probe_timeout_seconds"),
        "rustdesk_setup.readiness_probe_timeout_seconds",
    )
    download_dir = raw.get("download_dir")
    if not isinstance(download_dir, str):
        raise ConfigError("rustdesk_setup.download_dir must be a string")
    id_file_path = raw.get("id_file_path")
    if not isinstance(id_file_path, str):
        raise ConfigError("rustdesk_setup.id_file_path must be a string")
    vault_entry_title = raw.get("vault_entry_title")
    if not isinstance(vault_entry_title, str) or not vault_entry_title:
        raise ConfigError(
            "rustdesk_setup.vault_entry_title must be a non-empty string"
        )
    service_unit_name = raw.get("service_unit_name")
    if not isinstance(service_unit_name, str) or not service_unit_name:
        raise ConfigError(
            "rustdesk_setup.service_unit_name must be a non-empty string"
        )
    config_dir = raw.get("config_dir")
    if not isinstance(config_dir, str):
        raise ConfigError("rustdesk_setup.config_dir must be a string")
    password_separator = raw.get("password_separator")
    if not isinstance(password_separator, str) or not password_separator:
        raise ConfigError(
            "rustdesk_setup.password_separator must be a non-empty string"
        )
    return RustdeskSetupConfig(
        github_repo=github_repo,
        asset_name_template=asset_name_template,
        version_check_command=version_check_command,
        machine_id_command=machine_id_command,
        get_option_command=get_option_command,
        set_option_command=set_option_command,
        set_password_command=set_password_command,
        service_stop_command=service_stop_command,
        service_enable_command=service_enable_command,
        service_start_command=service_start_command,
        identity_file_name=identity_file_name,
        readiness_probe_timeout_seconds=readiness_probe_timeout_seconds,
        download_dir=Path(download_dir),
        id_file_path=Path(id_file_path),
        id_file_mode=_octal_mode_field(
            raw.get("id_file_mode"), "rustdesk_setup.id_file_mode"
        ),
        vault_entry_title=vault_entry_title,
        service_unit_name=service_unit_name,
        password_words=_int_field(
            raw.get("password_words"), "rustdesk_setup.password_words"
        ),
        password_separator=password_separator,
        config_dir=Path(config_dir),
        install_timeout_seconds=_int_field(
            raw.get("install_timeout_seconds"),
            "rustdesk_setup.install_timeout_seconds",
        ),
        apt_update_timeout_seconds=_int_field(
            raw.get("apt_update_timeout_seconds"),
            "rustdesk_setup.apt_update_timeout_seconds",
        ),
        install_retries=_int_field(
            raw.get("install_retries"), "rustdesk_setup.install_retries"
        ),
        start_check_attempts=_int_field(
            raw.get("start_check_attempts"),
            "rustdesk_setup.start_check_attempts",
        ),
        start_check_retry_delay_seconds=_float_field(
            raw.get("start_check_retry_delay_seconds"),
            "rustdesk_setup.start_check_retry_delay_seconds",
        ),
        options=_rustdesk_options(raw.get("options")),
    )





# from ssh.py


def _ssh_directives_field(raw: object, name: str) -> tuple[SshDirective, ...]:
    """Validate one directive array of the ssh daemon table.

    A missing array means no directives: the drop-in is then removed
    instead of rendered. Every directive is a table with a unique
    non-empty keyword and a non-empty value string.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    directives: list[SshDirective] = []
    seen_names: set[str] = set()
    for index, directive_raw in enumerate(raw):
        if not isinstance(directive_raw, dict):
            raise ConfigError(f"{name} must be an array of tables")
        directive_name = directive_raw.get("name")
        if not isinstance(directive_name, str) or not directive_name:
            raise ConfigError(
                f"{name}[{index}] name must be a non-empty string"
            )
        if directive_name in seen_names:
            raise ConfigError(
                f"{name} directive names must be unique: {directive_name}"
            )
        seen_names.add(directive_name)
        value = directive_raw.get("value")
        if not isinstance(value, str) or not value:
            raise ConfigError(
                f"{name}[{index}] value must be a non-empty string"
            )
        directives.append(SshDirective(name=directive_name, value=value))
    return tuple(directives)


def _ssh_daemon_setup_table(raw: object) -> SshDaemonSetupConfig:
    """Validate the [ssh_daemon_setup] table and build the config.

    package_name, service_unit_name, the key file names and the paths
    are non-empty strings; the timeouts and retries are positive
    integers; start_check_retry_delay_seconds is positive; the file
    modes are octal strings; users is a non-empty array of unique
    non-empty names; directives are validated by
    _ssh_directives_field.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[ssh_daemon_setup] section is missing or not a table")
    package_name = _nonempty_string_field(
        raw.get("package_name"), "ssh_daemon_setup.package_name"
    )
    augeas_tools_package_name = _nonempty_string_field(
        raw.get("augeas_tools_package_name"),
        "ssh_daemon_setup.augeas_tools_package_name",
    )
    package_status_timeout_seconds = _int_field(
        raw.get("package_status_timeout_seconds"),
        "ssh_daemon_setup.package_status_timeout_seconds",
    )
    if package_status_timeout_seconds < 1:
        raise ConfigError(
            "ssh_daemon_setup.package_status_timeout_seconds must be positive"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "ssh_daemon_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("ssh_daemon_setup.install_retries must be positive")
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "ssh_daemon_setup.service_unit_name"
    )
    socket_unit_name = _nonempty_string_field(
        raw.get("socket_unit_name"), "ssh_daemon_setup.socket_unit_name"
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "ssh_daemon_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError("ssh_daemon_setup.start_check_attempts must be positive")
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "ssh_daemon_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "ssh_daemon_setup.start_check_retry_delay_seconds must be positive"
        )
    sshd_config_path = Path(
        _nonempty_string_field(
            raw.get("sshd_config_path"), "ssh_daemon_setup.sshd_config_path"
        )
    )
    sshd_config_dropin_path = Path(
        _nonempty_string_field(
            raw.get("sshd_config_dropin_path"),
            "ssh_daemon_setup.sshd_config_dropin_path",
        )
    )
    dropin_header = _nonempty_string_field(
        raw.get("dropin_header"), "ssh_daemon_setup.dropin_header"
    )
    augeas_lens = _nonempty_string_field(
        raw.get("augeas_lens"), "ssh_daemon_setup.augeas_lens"
    )
    port_directive = _nonempty_string_field(
        raw.get("port_directive"), "ssh_daemon_setup.port_directive"
    )
    private_key_file_name = _nonempty_string_field(
        raw.get("private_key_file_name"),
        "ssh_daemon_setup.private_key_file_name",
    )
    public_key_file_name = _nonempty_string_field(
        raw.get("public_key_file_name"), "ssh_daemon_setup.public_key_file_name"
    )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"ssh_daemon_setup.{name}")

    users_raw = raw.get("users")
    if not isinstance(users_raw, list) or not users_raw:
        raise ConfigError(
            "ssh_daemon_setup.users must be a non-empty array of strings"
        )
    if not all(isinstance(user, str) and user for user in users_raw):
        raise ConfigError("ssh_daemon_setup.users must be non-empty strings")
    if len(set(users_raw)) != len(users_raw):
        raise ConfigError("ssh_daemon_setup.users must not contain duplicates")
    return SshDaemonSetupConfig(
        package_name=package_name,
        augeas_tools_package_name=augeas_tools_package_name,
        package_status_timeout_seconds=package_status_timeout_seconds,
        install_retries=install_retries,
        service_unit_name=service_unit_name,
        socket_unit_name=socket_unit_name,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        sshd_config_path=Path(sshd_config_path),
        sshd_config_dropin_path=Path(sshd_config_dropin_path),
        dropin_file_mode=_file_mode_field("dropin_file_mode"),
        dropin_header=dropin_header,
        augeas_lens=augeas_lens,
        port_directive=port_directive,
        effective_config_command=_placeholder_command_field(
            raw.get("effective_config_command"),
            "ssh_daemon_setup.effective_config_command",
            (),
        ),
        listening_sockets_command=_placeholder_command_field(
            raw.get("listening_sockets_command"),
            "ssh_daemon_setup.listening_sockets_command",
            (),
        ),
        socket_disable_command=_placeholder_command_field(
            raw.get("socket_disable_command"),
            "ssh_daemon_setup.socket_disable_command",
            ("{socket_unit_name}",),
        ),
        service_enable_command=_placeholder_command_field(
            raw.get("service_enable_command"),
            "ssh_daemon_setup.service_enable_command",
            ("{service_unit_name}",),
        ),
        service_start_command=_placeholder_command_field(
            raw.get("service_start_command"),
            "ssh_daemon_setup.service_start_command",
            ("{service_unit_name}",),
        ),
        service_restart_command=_placeholder_command_field(
            raw.get("service_restart_command"),
            "ssh_daemon_setup.service_restart_command",
            ("{service_unit_name}",),
        ),
        service_reload_command=_placeholder_command_field(
            raw.get("service_reload_command"),
            "ssh_daemon_setup.service_reload_command",
            ("{service_unit_name}",),
        ),
        private_key_file_name=private_key_file_name,
        public_key_file_name=public_key_file_name,
        private_key_file_mode=_file_mode_field("private_key_file_mode"),
        public_key_file_mode=_file_mode_field("public_key_file_mode"),
        authorized_keys_file_mode=_file_mode_field("authorized_keys_file_mode"),
        ssh_dir_mode=_file_mode_field("ssh_dir_mode"),
        root_ssh_dir=Path(
            _nonempty_string_field(
                raw.get("root_ssh_dir"), "ssh_daemon_setup.root_ssh_dir"
            )
        ),
        users=tuple(users_raw),
        directives=_ssh_directives_field(
            raw.get("directives"), "ssh_daemon_setup.directives"
        ),
        port_forwarding_private_key_file_name=_nonempty_string_field(
            raw.get("port_forwarding_private_key_file_name"),
            "ssh_daemon_setup.port_forwarding_private_key_file_name",
        ),
        port_forwarding_public_key_file_name=_nonempty_string_field(
            raw.get("port_forwarding_public_key_file_name"),
            "ssh_daemon_setup.port_forwarding_public_key_file_name",
        ),
        port_forwarding_authorized_keys_options=_nonempty_string_field(
            raw.get("port_forwarding_authorized_keys_options"),
            "ssh_daemon_setup.port_forwarding_authorized_keys_options",
        ),
    )


def _ssh_client_setup_table(raw: object) -> SshClientSetupConfig:
    """Validate the [ssh_client_setup] table and build the config.

    ssh_config_path and ssh_config_dropin_path are non-empty strings;
    dropin_file_mode is an octal string; directives are validated by
    _ssh_directives_field.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[ssh_client_setup] section is missing or not a table"
        )
    ssh_config_path = Path(
        _nonempty_string_field(
            raw.get("ssh_config_path"), "ssh_client_setup.ssh_config_path"
        )
    )
    ssh_config_dropin_path = Path(
        _nonempty_string_field(
            raw.get("ssh_config_dropin_path"),
            "ssh_client_setup.ssh_config_dropin_path",
        )
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "ssh_client_setup.dropin_file_mode"
    )
    dropin_header = _nonempty_string_field(
        raw.get("dropin_header"), "ssh_client_setup.dropin_header"
    )
    augeas_lens = _nonempty_string_field(
        raw.get("augeas_lens"), "ssh_client_setup.augeas_lens"
    )
    augeas_container = _nonempty_string_field(
        raw.get("augeas_container"), "ssh_client_setup.augeas_container"
    )
    augeas_container_value = _nonempty_string_field(
        raw.get("augeas_container_value"),
        "ssh_client_setup.augeas_container_value",
    )
    augeas_tools_package_name = _nonempty_string_field(
        raw.get("augeas_tools_package_name"),
        "ssh_client_setup.augeas_tools_package_name",
    )
    package_status_timeout_seconds = _int_field(
        raw.get("package_status_timeout_seconds"),
        "ssh_client_setup.package_status_timeout_seconds",
    )
    if package_status_timeout_seconds < 1:
        raise ConfigError(
            "ssh_client_setup.package_status_timeout_seconds must be positive"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "ssh_client_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("ssh_client_setup.install_retries must be positive")
    directives = _ssh_directives_field(
        raw.get("directives"), "ssh_client_setup.directives"
    )
    return SshClientSetupConfig(
        ssh_config_path=ssh_config_path,
        ssh_config_dropin_path=ssh_config_dropin_path,
        dropin_file_mode=dropin_file_mode,
        dropin_header=dropin_header,
        augeas_lens=augeas_lens,
        augeas_container=augeas_container,
        augeas_container_value=augeas_container_value,
        augeas_tools_package_name=augeas_tools_package_name,
        package_status_timeout_seconds=package_status_timeout_seconds,
        install_retries=install_retries,
        effective_config_command=_string_list(
            raw.get("effective_config_command"),
            "ssh_client_setup.effective_config_command",
        ),
        directives=directives,
    )





# from swapfile_service_install.py


def _placeholder_command_field(
    raw: object, name: str, placeholders: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate a command array and demand the placeholders its call fills.

    A command whose arguments are ours to choose lives in the config with
    {placeholders}; the task fills them in, so a mistyped placeholder would
    raise on the target machine. The checks refuse it here instead.
    """

    command = _string_list(raw, name)
    for placeholder in placeholders:
        if not any(placeholder in part for part in command):
            raise ConfigError(f"{name} must carry the {placeholder} placeholder")
    return command


def _placeholder_text_field(
    raw: object, name: str, placeholders: tuple[str, ...]
) -> str:
    """Validate a line template and demand the placeholders it is filled with.

    A line of a file the run writes is a config value with placeholders,
    so a mistyped one would raise on the target machine; the checks refuse
    it here instead.
    """

    text = _nonempty_string_field(raw, name)
    for placeholder in placeholders:
        if placeholder not in text:
            raise ConfigError(f"{name} must carry the {placeholder} placeholder")
    return text


def _swapfile_service_install_table(raw: object) -> SwapfileServiceInstallConfig:
    """Validate the [swapfile_service_install] table and build the config.

    swapfile_path is a non-empty string; ram_multiplier is a non-negative
    number; ram_extra_mb is a non-negative integer; disk_fraction must be
    greater than zero and at most one, so the swap size always stays finite
    and positive when RAM and disk are present. swapfile_mode is an octal
    string like "0600"; size_tolerance_mb is a non-negative integer.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[swapfile_service_install] section is missing or not a table"
        )
    swapfile_path = raw.get("swapfile_path")
    if not isinstance(swapfile_path, str) or not swapfile_path:
        raise ConfigError(
            "swapfile_service_install.swapfile_path must be a non-empty string"
        )
    ram_multiplier = _float_field(
        raw.get("ram_multiplier"), "swapfile_service_install.ram_multiplier"
    )
    ram_extra_mb = _int_field(
        raw.get("ram_extra_mb"), "swapfile_service_install.ram_extra_mb"
    )
    if ram_extra_mb < 0:
        raise ConfigError(
            "swapfile_service_install.ram_extra_mb must not be negative"
        )
    disk_fraction = _float_field(
        raw.get("disk_fraction"), "swapfile_service_install.disk_fraction"
    )
    if not 0 < disk_fraction <= 1:
        raise ConfigError(
            "swapfile_service_install.disk_fraction must be between 0 (exclusive) and 1"
        )
    size_tolerance_mb = _int_field(
        raw.get("size_tolerance_mb"), "swapfile_service_install.size_tolerance_mb"
    )
    if size_tolerance_mb < 0:
        raise ConfigError(
            "swapfile_service_install.size_tolerance_mb must not be negative"
        )
    return SwapfileServiceInstallConfig(
        swapfile_path=Path(swapfile_path),
        ram_multiplier=ram_multiplier,
        ram_extra_mb=ram_extra_mb,
        disk_fraction=disk_fraction,
        swapfile_mode=_octal_mode_field(
            raw.get("swapfile_mode"), "swapfile_service_install.swapfile_mode"
        ),
        size_tolerance_mb=size_tolerance_mb,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "swapfile_service_install.service_unit_name",
        ),
        unit_template_file_name=_nonempty_string_field(
            raw.get("unit_template_file_name"),
            "swapfile_service_install.unit_template_file_name",
        ),
        swap_show_command=_string_list(
            raw.get("swap_show_command"),
            "swapfile_service_install.swap_show_command",
        ),
        swap_on_command=_placeholder_command_field(
            raw.get("swap_on_command"),
            "swapfile_service_install.swap_on_command",
            ("{swapfile_path}",),
        ),
        swap_off_command=_placeholder_command_field(
            raw.get("swap_off_command"),
            "swapfile_service_install.swap_off_command",
            ("{swapfile_path}",),
        ),
        create_command=_placeholder_command_field(
            raw.get("create_command"),
            "swapfile_service_install.create_command",
            ("{size_mb}", "{swapfile_path}"),
        ),
        chmod_command=_placeholder_command_field(
            raw.get("chmod_command"),
            "swapfile_service_install.chmod_command",
            ("{file_mode}", "{swapfile_path}"),
        ),
        format_command=_placeholder_command_field(
            raw.get("format_command"),
            "swapfile_service_install.format_command",
            ("{swapfile_path}",),
        ),
        systemctl_daemon_reload_command=_string_list(
            raw.get("systemctl_daemon_reload_command"),
            "swapfile_service_install.systemctl_daemon_reload_command",
        ),
        systemctl_enable_command=_placeholder_command_field(
            raw.get("systemctl_enable_command"),
            "swapfile_service_install.systemctl_enable_command",
            ("{service_unit_name}",),
        ),
    )





# from system_metrics_setup.py


def _daily_time_field(raw: object, name: str) -> str:
    """Validate a time of day "HH:MM" or "HH:MM:SS"; return "HH:MM:SS".

    The normalized form feeds the OnCalendar directive of the collector
    timer directly, so the config may use the short form and the renderer
    never has to guess the seconds.
    """

    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'")
    try:
        values = [int(part) for part in parts]
    except ValueError:
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'") from None
    hour, minute, second = (
        values[0],
        values[1],
        values[2] if len(values) == 3 else 0,
    )
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ConfigError(f"{name} must be a valid time of day")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _collector_modules_field(
    raw: object, name: str
) -> tuple[CollectorModuleConfig, ...]:
    """Validate one module array of the collector table.

    A missing array means no modules of that kind: an empty network
    module list is valid, because the readiness percentage is then 100
    by construction and only the system modules are collected. Every
    module is a table with a unique non-empty name and a non-empty
    command array of non-empty strings; the command is never a shell
    line.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    modules: list[CollectorModuleConfig] = []
    seen_names: set[str] = set()
    for index, module_raw in enumerate(raw):
        if not isinstance(module_raw, dict):
            raise ConfigError(f"{name} must be an array of tables")
        module_name = module_raw.get("name")
        if not isinstance(module_name, str) or not module_name:
            raise ConfigError(f"{name}[{index}] name must be a non-empty string")
        if module_name in seen_names:
            raise ConfigError(f"{name} module names must be unique: {module_name}")
        seen_names.add(module_name)
        command = module_raw.get("command")
        if not isinstance(command, list) or not command:
            raise ConfigError(
                f"{name}[{index}] command must be a non-empty array of strings"
            )
        if not all(isinstance(part, str) and part for part in command):
            raise ConfigError(
                f"{name}[{index}] command must be non-empty strings"
            )
        modules.append(CollectorModuleConfig(name=module_name, command=tuple(command)))
    return tuple(modules)


def _system_metrics_collector_table(raw: object) -> SystemMetricsCollectorConfig:
    """Validate the [system_metrics_setup.collector] table and build the
    config.

    The section is mandatory. boot_delay_seconds is a non-negative
    integer; daily_send_time is a time of day "HH:MM" or "HH:MM:SS"
    normalized to "HH:MM:SS"; threshold_percent is an integer between 0
    and 100; retry_base_seconds is positive, retry_multiplier is at
    least 2 and retry_max_seconds is not below retry_base_seconds;
    command_timeout_seconds is positive; the unit names, the journal
    identifier, the report file name are non-empty strings and
    lock_file_path is a non-empty string. The module arrays are
    optional; every module is a table with a unique non-empty name and a
    non-empty command array of non-empty strings.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[system_metrics_setup.collector] section is missing or not a table"
        )
    boot_delay_seconds = _int_field(
        raw.get("boot_delay_seconds"),
        "system_metrics_setup.collector.boot_delay_seconds",
    )
    if boot_delay_seconds < 0:
        raise ConfigError(
            "system_metrics_setup.collector.boot_delay_seconds must not be negative"
        )
    threshold_percent = _int_field(
        raw.get("threshold_percent"),
        "system_metrics_setup.collector.threshold_percent",
    )
    if not 0 <= threshold_percent <= 100:
        raise ConfigError(
            "system_metrics_setup.collector.threshold_percent must be between 0 and 100"
        )
    retry_base_seconds = _int_field(
        raw.get("retry_base_seconds"),
        "system_metrics_setup.collector.retry_base_seconds",
    )
    if retry_base_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.collector.retry_base_seconds must be positive"
        )
    retry_multiplier = _int_field(
        raw.get("retry_multiplier"),
        "system_metrics_setup.collector.retry_multiplier",
    )
    if retry_multiplier < 2:
        raise ConfigError(
            "system_metrics_setup.collector.retry_multiplier must be at least 2"
        )
    retry_max_seconds = _int_field(
        raw.get("retry_max_seconds"),
        "system_metrics_setup.collector.retry_max_seconds",
    )
    if retry_max_seconds < retry_base_seconds:
        raise ConfigError(
            "system_metrics_setup.collector.retry_max_seconds must be at least "
            "retry_base_seconds"
        )
    command_timeout_seconds = _int_field(
        raw.get("command_timeout_seconds"),
        "system_metrics_setup.collector.command_timeout_seconds",
    )
    if command_timeout_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.collector.command_timeout_seconds must be positive"
        )
    return SystemMetricsCollectorConfig(
        boot_delay_seconds=boot_delay_seconds,
        daily_send_time=_daily_time_field(
            raw.get("daily_send_time"),
            "system_metrics_setup.collector.daily_send_time",
        ),
        threshold_percent=threshold_percent,
        retry_base_seconds=retry_base_seconds,
        retry_multiplier=retry_multiplier,
        retry_max_seconds=retry_max_seconds,
        command_timeout_seconds=command_timeout_seconds,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "system_metrics_setup.collector.service_unit_name",
        ),
        timer_unit_name=_nonempty_string_field(
            raw.get("timer_unit_name"),
            "system_metrics_setup.collector.timer_unit_name",
        ),
        start_command=_string_list(
            raw.get("start_command"),
            "system_metrics_setup.collector.start_command",
        ),
        journal_identifier=_nonempty_string_field(
            raw.get("journal_identifier"),
            "system_metrics_setup.collector.journal_identifier",
        ),
        lock_file_path=Path(
            _nonempty_string_field(
                raw.get("lock_file_path"),
                "system_metrics_setup.collector.lock_file_path",
            )
        ),
        report_file_name=_nonempty_string_field(
            raw.get("report_file_name"),
            "system_metrics_setup.collector.report_file_name",
        ),
        report_file_mode=_octal_mode_field(
            raw.get("report_file_mode"),
            "system_metrics_setup.collector.report_file_mode",
        ),
        network_modules=_collector_modules_field(
            raw.get("network_modules"),
            "system_metrics_setup.collector.network_modules",
        ),
        system_modules=_collector_modules_field(
            raw.get("system_modules"),
            "system_metrics_setup.collector.system_modules",
        ),
    )


def _system_metrics_setup_table(raw: object) -> SystemMetricsSetupConfig:
    """Validate the [system_metrics_setup] table and build the config.

    backoff_base_seconds and backoff_max_seconds are positive integers
    and backoff_max_seconds is not below backoff_base_seconds;
    backoff_multiplier is an integer of at least 2, so the pause always
    grows. python_version is a non-empty string; error_priority is a
    syslog level between 0 and 7; venv_dir, system_config_path,
    command_path, vault_backup_file_name,
    system_metrics_dir, spool_dir and every unit name, journal
    identifier, queue directory name and spool temp prefix are non-empty
    strings; system_metrics_dir_mode, queue_file_mode, spool_dir_mode
    and command_file_mode are octal strings; max_queue_file_size_bytes,
    queue_file_suffix_length, queue_link_attempts and
    google_script_timeout_seconds are positive integers; send_order is
    one of the SEND_ORDERS values; google_script_dir, main_sent_dir and
    google_script_key_entry_title are non-empty strings;
    google_script_deployment_url_regex is a non-empty string that
    compiles as a regular expression with exactly one capture group.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[system_metrics_setup] section is missing or not a table"
        )
    backoff_base_seconds = _int_field(
        raw.get("backoff_base_seconds"),
        "system_metrics_setup.backoff_base_seconds",
    )
    if backoff_base_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.backoff_base_seconds must be positive"
        )
    backoff_multiplier = _int_field(
        raw.get("backoff_multiplier"),
        "system_metrics_setup.backoff_multiplier",
    )
    if backoff_multiplier < 2:
        raise ConfigError(
            "system_metrics_setup.backoff_multiplier must be at least 2"
        )
    backoff_max_seconds = _int_field(
        raw.get("backoff_max_seconds"),
        "system_metrics_setup.backoff_max_seconds",
    )
    if backoff_max_seconds < backoff_base_seconds:
        raise ConfigError(
            "system_metrics_setup.backoff_max_seconds must be at least "
            "backoff_base_seconds"
        )
    python_version = raw.get("python_version")
    if not isinstance(python_version, str) or not python_version:
        raise ConfigError(
            "system_metrics_setup.python_version must be a non-empty string"
        )
    error_priority = _int_field(
        raw.get("error_priority"), "system_metrics_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "system_metrics_setup.error_priority must be between 0 and 7"
        )
    venv_dir = raw.get("venv_dir")
    if not isinstance(venv_dir, str) or not venv_dir:
        raise ConfigError(
            "system_metrics_setup.venv_dir must be a non-empty string"
        )
    venv_python_relative_path = _nonempty_string_field(
        raw.get("venv_python_relative_path"),
        "system_metrics_setup.venv_python_relative_path",
    )
    system_config_path = raw.get("system_config_path")
    if not isinstance(system_config_path, str) or not system_config_path:
        raise ConfigError(
            "system_metrics_setup.system_config_path must be a non-empty string"
        )
    command_path = raw.get("command_path")
    if not isinstance(command_path, str) or not command_path:
        raise ConfigError(
            "system_metrics_setup.command_path must be a non-empty string"
        )
    commit_command = _string_list(
        raw.get("commit_command"),
        "system_metrics_setup.commit_command",
    )
    vault_backup_file_name = _nonempty_string_field(
        raw.get("vault_backup_file_name"),
        "system_metrics_setup.vault_backup_file_name",
    )
    vault_backup_file_mode = _octal_mode_field(
        raw.get("vault_backup_file_mode"),
        "system_metrics_setup.vault_backup_file_mode",
    )
    system_metrics_dir = raw.get("system_metrics_dir")
    if not isinstance(system_metrics_dir, str) or not system_metrics_dir:
        raise ConfigError(
            "system_metrics_setup.system_metrics_dir must be a non-empty string"
        )
    max_queue_file_size_bytes = _int_field(
        raw.get("max_queue_file_size_bytes"),
        "system_metrics_setup.max_queue_file_size_bytes",
    )
    if max_queue_file_size_bytes < 1:
        raise ConfigError(
            "system_metrics_setup.max_queue_file_size_bytes must be positive"
        )
    send_order = raw.get("send_order")
    if send_order not in SEND_ORDERS:
        raise ConfigError(
            "system_metrics_setup.send_order must be one of "
            + ", ".join(SEND_ORDERS)
        )
    queue_file_suffix_length = _int_field(
        raw.get("queue_file_suffix_length"),
        "system_metrics_setup.queue_file_suffix_length",
    )
    if queue_file_suffix_length < 1:
        raise ConfigError(
            "system_metrics_setup.queue_file_suffix_length must be positive"
        )
    queue_link_attempts = _int_field(
        raw.get("queue_link_attempts"),
        "system_metrics_setup.queue_link_attempts",
    )
    if queue_link_attempts < 1:
        raise ConfigError(
            "system_metrics_setup.queue_link_attempts must be positive"
        )
    google_script_dir = _nonempty_string_field(
        raw.get("google_script_dir"),
        "system_metrics_setup.google_script_dir",
    )
    main_sent_dir = _nonempty_string_field(
        raw.get("main_sent_dir"),
        "system_metrics_setup.main_sent_dir",
    )
    google_script_timeout_seconds = _int_field(
        raw.get("google_script_timeout_seconds"),
        "system_metrics_setup.google_script_timeout_seconds",
    )
    if google_script_timeout_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.google_script_timeout_seconds must be positive"
        )
    google_script_upload_command = _string_list(
        raw.get("google_script_upload_command"),
        "system_metrics_setup.google_script_upload_command",
    )
    if not google_script_upload_command:
        raise ConfigError(
            "system_metrics_setup.google_script_upload_command must not be empty"
        )
    for placeholder in ("{timeout_seconds}", "{file_name}", "{key}"):
        if placeholder not in " ".join(google_script_upload_command):
            raise ConfigError(
                "system_metrics_setup.google_script_upload_command must carry "
                f"the {placeholder} placeholder"
            )
    google_script_key_entry_title = _nonempty_string_field(
        raw.get("google_script_key_entry_title"),
        "system_metrics_setup.google_script_key_entry_title",
    )
    google_script_deployment_url_regex = raw.get(
        "google_script_deployment_url_regex"
    )
    if (
        not isinstance(google_script_deployment_url_regex, str)
        or not google_script_deployment_url_regex
    ):
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "be a non-empty string"
        )
    try:
        compiled_url_regex = re.compile(google_script_deployment_url_regex)
    except re.error as exc:
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex is not "
            f"a valid regular expression: {exc}"
        ) from None
    if compiled_url_regex.groups != 1:
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "contain exactly one capture group"
        )
    return SystemMetricsSetupConfig(
        backoff_base_seconds=backoff_base_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_max_seconds=backoff_max_seconds,
        python_version=python_version,
        error_priority=error_priority,
        venv_dir=Path(venv_dir),
        venv_python_relative_path=venv_python_relative_path,
        system_config_path=Path(system_config_path),
        command_path=Path(command_path),
        commit_command=commit_command,
        vault_backup_file_name=vault_backup_file_name,
        vault_backup_file_mode=vault_backup_file_mode,
        system_metrics_dir=Path(system_metrics_dir),
        system_metrics_dir_mode=_octal_mode_field(
            raw.get("system_metrics_dir_mode"),
            "system_metrics_setup.system_metrics_dir_mode",
        ),
        queue_file_mode=_octal_mode_field(
            raw.get("queue_file_mode"), "system_metrics_setup.queue_file_mode"
        ),
        max_queue_file_size_bytes=max_queue_file_size_bytes,
        send_order=send_order,
        queue_file_suffix_length=queue_file_suffix_length,
        spool_dir=Path(
            _nonempty_string_field(
                raw.get("spool_dir"), "system_metrics_setup.spool_dir"
            )
        ),
        spool_dir_mode=_octal_mode_field(
            raw.get("spool_dir_mode"), "system_metrics_setup.spool_dir_mode"
        ),
        spool_dir_permission_mask=_octal_mode_field(
            raw.get("spool_dir_permission_mask"),
            "system_metrics_setup.spool_dir_permission_mask",
        ),
        command_file_mode=_octal_mode_field(
            raw.get("command_file_mode"),
            "system_metrics_setup.command_file_mode",
        ),
        command_permission_mask=_octal_mode_field(
            raw.get("command_permission_mask"),
            "system_metrics_setup.command_permission_mask",
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "system_metrics_setup.service_unit_name",
        ),
        ingest_service_unit_name=_nonempty_string_field(
            raw.get("ingest_service_unit_name"),
            "system_metrics_setup.ingest_service_unit_name",
        ),
        ingest_path_unit_name=_nonempty_string_field(
            raw.get("ingest_path_unit_name"),
            "system_metrics_setup.ingest_path_unit_name",
        ),
        systemctl_daemon_reload_command=_placeholder_command_field(
            raw.get("systemctl_daemon_reload_command"),
            "system_metrics_setup.systemctl_daemon_reload_command",
            (),
        ),
        systemctl_enable_command=_placeholder_command_field(
            raw.get("systemctl_enable_command"),
            "system_metrics_setup.systemctl_enable_command",
            ("{unit_name}",),
        ),
        systemctl_restart_command=_placeholder_command_field(
            raw.get("systemctl_restart_command"),
            "system_metrics_setup.systemctl_restart_command",
            ("{unit_name}",),
        ),
        systemctl_start_command=_placeholder_command_field(
            raw.get("systemctl_start_command"),
            "system_metrics_setup.systemctl_start_command",
            ("{unit_name}",),
        ),
        service_journal_identifier=_nonempty_string_field(
            raw.get("service_journal_identifier"),
            "system_metrics_setup.service_journal_identifier",
        ),
        commit_journal_identifier=_nonempty_string_field(
            raw.get("commit_journal_identifier"),
            "system_metrics_setup.commit_journal_identifier",
        ),
        main_outbox_dir=_nonempty_string_field(
            raw.get("main_outbox_dir"),
            "system_metrics_setup.main_outbox_dir",
        ),
        temp_dir=_nonempty_string_field(
            raw.get("temp_dir"), "system_metrics_setup.temp_dir"
        ),
        spool_temp_prefix=_nonempty_string_field(
            raw.get("spool_temp_prefix"),
            "system_metrics_setup.spool_temp_prefix",
        ),
        queue_link_attempts=queue_link_attempts,
        google_script_dir=google_script_dir,
        main_sent_dir=main_sent_dir,
        google_script_timeout_seconds=google_script_timeout_seconds,
        google_script_upload_command=google_script_upload_command,
        google_script_key_entry_title=google_script_key_entry_title,
        google_script_deployment_url_regex=google_script_deployment_url_regex,
        collector=_system_metrics_collector_table(raw.get("collector")),
    )





# from tasks.py


def _tasks_table(raw: object) -> tuple[TaskConfig, ...]:
    """Validate the [[tasks]] section and build the task catalog.

    The catalog is non-empty; names are unique Python identifiers; every
    dependency names a task listed earlier in the file, which also rules out
    dependency cycles and keeps default task sets ordered; modes are known
    install modes without duplicates. An empty modes list is allowed: the
    task stays in the catalog but belongs to no install mode, so it never
    runs in a default task set and only runs when selected explicitly.
    """

    if not isinstance(raw, list):
        raise ConfigError("[tasks] section is missing or not an array of tables")
    result: list[TaskConfig] = []
    seen_names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[tasks] entries must be tables")
        name = entry.get("name")
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ConfigError("[tasks] task name must be a non-empty identifier")
        if name in seen_names:
            raise ConfigError(f"[tasks] duplicate task name: {name}")
        seen_names.add(name)
        description = entry.get("description")
        if not isinstance(description, str):
            raise ConfigError(f"[tasks] task {name}: description must be a string")
        depends_raw = entry.get("depends", [])
        if not isinstance(depends_raw, list) or not all(
            isinstance(dep, str) for dep in depends_raw
        ):
            raise ConfigError(
                f"[tasks] task {name}: depends must be an array of strings"
            )
        known_names = {task.name for task in result}
        for dep in depends_raw:
            if dep not in known_names:
                raise ConfigError(
                    f"[tasks] task {name}: dependency {dep!r} must be listed earlier"
                )
        modes_raw = entry.get("modes")
        if not isinstance(modes_raw, list):
            raise ConfigError(f"[tasks] task {name}: modes must be an array")
        if not all(isinstance(mode, str) for mode in modes_raw):
            raise ConfigError(f"[tasks] task {name}: modes must be strings")
        for mode in modes_raw:
            if mode not in MODES:
                raise ConfigError(
                    f"[tasks] task {name}: unknown install mode {mode!r}"
                )
        if len(set(modes_raw)) != len(modes_raw):
            raise ConfigError(f"[tasks] task {name}: duplicate mode entries")
        result.append(
            TaskConfig(
                name=name,
                description=description,
                depends=tuple(depends_raw),
                modes=tuple(modes_raw),
            )
        )
    if not result:
        raise ConfigError("[tasks] section must contain at least one task")
    return tuple(result)





# from telegram_setup.py


def _telegram_setup_table(raw: object) -> TelegramSetupConfig:
    """Validate the [telegram_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[telegram_setup] section is missing or not a table")
    return TelegramSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "telegram_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "telegram_setup.home_dir"
        ),
        download_dir=Path(
            _nonempty_string_field(
                raw.get("download_dir"), "telegram_setup.download_dir"
            )
        ),
        latest_url=_nonempty_string_field(
            raw.get("latest_url"), "telegram_setup.latest_url"
        ),
        latest_url_command=_string_list(
            raw.get("latest_url_command"),
            "telegram_setup.latest_url_command",
        ),
        icon_url=_nonempty_string_field(
            raw.get("icon_url"), "telegram_setup.icon_url"
        ),
        install_dir_relative_path=_nonempty_string_field(
            raw.get("install_dir_relative_path"),
            "telegram_setup.install_dir_relative_path",
        ),
        launcher_relative_path=_nonempty_string_field(
            raw.get("launcher_relative_path"),
            "telegram_setup.launcher_relative_path",
        ),
        icon_relative_path=_nonempty_string_field(
            raw.get("icon_relative_path"),
            "telegram_setup.icon_relative_path",
        ),
        binary_file_name=_nonempty_string_field(
            raw.get("binary_file_name"), "telegram_setup.binary_file_name"
        ),
        updater_file_name=_nonempty_string_field(
            raw.get("updater_file_name"), "telegram_setup.updater_file_name"
        ),
        archive_directory_name=_nonempty_string_field(
            raw.get("archive_directory_name"),
            "telegram_setup.archive_directory_name",
        ),
        extract_dir_prefix=_nonempty_string_field(
            raw.get("extract_dir_prefix"),
            "telegram_setup.extract_dir_prefix",
        ),
        launcher_template_file_name=_nonempty_string_field(
            raw.get("launcher_template_file_name"),
            "telegram_setup.launcher_template_file_name",
        ),
        launcher_file_mode=_octal_mode_field(
            raw.get("launcher_file_mode"), "telegram_setup.launcher_file_mode"
        ),
        icon_file_mode=_octal_mode_field(
            raw.get("icon_file_mode"), "telegram_setup.icon_file_mode"
        ),
        executable_file_mode=_octal_mode_field(
            raw.get("executable_file_mode"),
            "telegram_setup.executable_file_mode",
        ),
    )





# from three_x_ui_xray_setup.py


def _three_x_ui_xray_setup_table(raw: object) -> ThreeXuiXraySetupConfig:
    """Validate the [three_x_ui_xray_setup] table and build the config.

    github_repo, install_script_url, install_dir, service_unit_name,
    install_result_env_path, panel_http_address and vault_entry_title are
    non-empty strings; start_check_attempts is positive and
    start_check_retry_delay_seconds is non-negative.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[three_x_ui_xray_setup] section is missing or not a table"
        )
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "three_x_ui_xray_setup.github_repo"
    )
    install_script_url = _nonempty_string_field(
        raw.get("install_script_url"),
        "three_x_ui_xray_setup.install_script_url",
    )
    install_dir = Path(
        _nonempty_string_field(
            raw.get("install_dir"), "three_x_ui_xray_setup.install_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"),
        "three_x_ui_xray_setup.service_unit_name",
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"),
        "three_x_ui_xray_setup.start_check_attempts",
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _int_field(
        raw.get("start_check_retry_delay_seconds"),
        "three_x_ui_xray_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds < 0:
        raise ConfigError(
            "three_x_ui_xray_setup.start_check_retry_delay_seconds "
            "must not be negative"
        )
    install_result_env_path = Path(
        _nonempty_string_field(
            raw.get("install_result_env_path"),
            "three_x_ui_xray_setup.install_result_env_path",
        )
    )
    panel_port = _int_field(
        raw.get("panel_port"),
        "three_x_ui_xray_setup.panel_port",
    )
    if panel_port < 1 or panel_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.panel_port must be between 1 and 65535"
        )
    ssl_enabled = _bool_field(
        raw.get("ssl_enabled"),
        "three_x_ui_xray_setup.ssl_enabled",
    )
    panel_http_address = _nonempty_string_field(
        raw.get("panel_http_address"),
        "three_x_ui_xray_setup.panel_http_address",
    )
    vault_entry_title = _nonempty_string_field(
        raw.get("vault_entry_title"),
        "three_x_ui_xray_setup.vault_entry_title",
    )
    connection_vault_entry_title = _nonempty_string_field(
        raw.get("connection_vault_entry_title"),
        "three_x_ui_xray_setup.connection_vault_entry_title",
    )
    share_addr_strategy = _nonempty_string_field(
        raw.get("share_addr_strategy"),
        "three_x_ui_xray_setup.share_addr_strategy",
    )
    if share_addr_strategy not in SHARE_ADDR_STRATEGIES:
        raise ConfigError(
            "three_x_ui_xray_setup.share_addr_strategy must be one of "
            + ", ".join(SHARE_ADDR_STRATEGIES)
        )
    inbound_port = _int_field(
        raw.get("inbound_port"),
        "three_x_ui_xray_setup.inbound_port",
    )
    if inbound_port < 1 or inbound_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.inbound_port must be between 1 and 65535"
        )
    inbound_remark = _nonempty_string_field(
        raw.get("inbound_remark"),
        "three_x_ui_xray_setup.inbound_remark",
    )
    reality_dest = _nonempty_string_field(
        raw.get("reality_dest"),
        "three_x_ui_xray_setup.reality_dest",
    )
    reality_server_names = _string_list(
        raw.get("reality_server_names"),
        "three_x_ui_xray_setup.reality_server_names",
    )
    reality_short_id = _nonempty_string_field(
        raw.get("reality_short_id"),
        "three_x_ui_xray_setup.reality_short_id",
    )
    reality_fingerprint = _nonempty_string_field(
        raw.get("reality_fingerprint"),
        "three_x_ui_xray_setup.reality_fingerprint",
    )
    subscription_path = _subscription_path_field(
        raw.get("subscription_path"),
        "three_x_ui_xray_setup.subscription_path",
    )
    subscription_json_path = _subscription_path_field(
        raw.get("subscription_json_path"),
        "three_x_ui_xray_setup.subscription_json_path",
    )
    subscription_clash_path = _subscription_path_field(
        raw.get("subscription_clash_path"),
        "three_x_ui_xray_setup.subscription_clash_path",
    )
    acme_port = _int_field(
        raw.get("acme_port"),
        "three_x_ui_xray_setup.acme_port",
    )
    if acme_port < 1 or acme_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.acme_port must be between 1 and 65535"
        )
    cert_dir = Path(
        _nonempty_string_field(
            raw.get("cert_dir"), "three_x_ui_xray_setup.cert_dir"
        )
    )
    self_signed_cert_dir = Path(
        _nonempty_string_field(
            raw.get("self_signed_cert_dir"),
            "three_x_ui_xray_setup.self_signed_cert_dir",
        )
    )
    cert_privkey_file_mode = _octal_mode_field(
        raw.get("cert_privkey_file_mode"),
        "three_x_ui_xray_setup.cert_privkey_file_mode",
    )
    cert_fullchain_file_mode = _octal_mode_field(
        raw.get("cert_fullchain_file_mode"),
        "three_x_ui_xray_setup.cert_fullchain_file_mode",
    )
    server_ip_timeout_seconds = _int_field(
        raw.get("server_ip_timeout_seconds"),
        "three_x_ui_xray_setup.server_ip_timeout_seconds",
    )
    if server_ip_timeout_seconds < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.server_ip_timeout_seconds must be positive"
        )
    probe_timeout_seconds = _int_field(
        raw.get("probe_timeout_seconds"),
        "three_x_ui_xray_setup.probe_timeout_seconds",
    )
    if probe_timeout_seconds < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.probe_timeout_seconds must be positive"
        )
    probe_port_80_timeout_seconds = _int_field(
        raw.get("probe_port_80_timeout_seconds"),
        "three_x_ui_xray_setup.probe_port_80_timeout_seconds",
    )
    if probe_port_80_timeout_seconds < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.probe_port_80_timeout_seconds must be positive"
        )
    probe_listener_start_seconds = _int_field(
        raw.get("probe_listener_start_seconds"),
        "three_x_ui_xray_setup.probe_listener_start_seconds",
    )
    if probe_listener_start_seconds < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.probe_listener_start_seconds "
            "must be positive"
        )
    port_forward_probe_command = _string_list(
        raw.get("port_forward_probe_command"),
        "three_x_ui_xray_setup.port_forward_probe_command",
    )
    if not port_forward_probe_command:
        raise ConfigError(
            "three_x_ui_xray_setup.port_forward_probe_command must not be empty"
        )
    if "{timeout_seconds}" not in " ".join(port_forward_probe_command):
        raise ConfigError(
            "three_x_ui_xray_setup.port_forward_probe_command must carry "
            "the {timeout_seconds} placeholder"
        )
    port_forward_probe_url_format = _nonempty_string_field(
        raw.get("port_forward_probe_url_format"),
        "three_x_ui_xray_setup.port_forward_probe_url_format",
    )
    panel_probe_command = _string_list(
        raw.get("panel_probe_command"),
        "three_x_ui_xray_setup.panel_probe_command",
    )
    if not panel_probe_command:
        raise ConfigError(
            "three_x_ui_xray_setup.panel_probe_command must not be empty"
        )
    tunnel_probe_command = _string_list(
        raw.get("tunnel_probe_command"),
        "three_x_ui_xray_setup.tunnel_probe_command",
    )
    if not tunnel_probe_command:
        raise ConfigError(
            "three_x_ui_xray_setup.tunnel_probe_command must not be empty"
        )
    for placeholder in ("{proxy_address}", "{timeout_seconds}", "{write_out}"):
        if placeholder not in " ".join(tunnel_probe_command):
            raise ConfigError(
                "three_x_ui_xray_setup.tunnel_probe_command must carry the "
                f"{placeholder} placeholder"
            )
    tunnel_probe_write_out = _nonempty_string_field(
        raw.get("tunnel_probe_write_out"),
        "three_x_ui_xray_setup.tunnel_probe_write_out",
    )
    server_ip_services = _string_list(
        raw.get("server_ip_services"),
        "three_x_ui_xray_setup.server_ip_services",
    )
    upnp_enabled = _bool_field(
        raw.get("upnp_enabled"),
        "three_x_ui_xray_setup.upnp_enabled",
    )
    upnp_package = _nonempty_string_field(
        raw.get("upnp_package"),
        "three_x_ui_xray_setup.upnp_package",
    )
    upnp_client_command = _nonempty_string_field(
        raw.get("upnp_client_command"),
        "three_x_ui_xray_setup.upnp_client_command",
    )
    upnp_mapping_description = _nonempty_string_field(
        raw.get("upnp_mapping_description"),
        "three_x_ui_xray_setup.upnp_mapping_description",
    )
    client_profile_entry_title = _nonempty_string_field(
        raw.get("client_profile_entry_title"),
        "three_x_ui_xray_setup.client_profile_entry_title",
    )
    local_proxy_tag = _tag_field(
        raw.get("local_proxy_tag"),
        "three_x_ui_xray_setup.local_proxy_tag",
    )
    local_proxy_listen_address = _nonempty_string_field(
        raw.get("local_proxy_listen_address"),
        "three_x_ui_xray_setup.local_proxy_listen_address",
    )
    local_proxy_port = _port_field(
        raw.get("local_proxy_port"),
        "three_x_ui_xray_setup.local_proxy_port",
    )
    if local_proxy_port in (panel_port, inbound_port):
        raise ConfigError(
            "three_x_ui_xray_setup.local_proxy_port must differ from "
            "panel_port and inbound_port"
        )
    local_proxy_udp = _bool_field(
        raw.get("local_proxy_udp"),
        "three_x_ui_xray_setup.local_proxy_udp",
    )
    local_proxy_sniffing_protocols = _string_list(
        raw.get("local_proxy_sniffing_protocols"),
        "three_x_ui_xray_setup.local_proxy_sniffing_protocols",
    )
    outbound_tags = {
        name: _tag_field(
            raw.get(name),
            f"three_x_ui_xray_setup.{name}",
        )
        for name in (
            "remote_outbound_tag",
            "tor_outbound_tag",
            "i2p_outbound_tag",
            "direct_outbound_tag",
            "blocked_outbound_tag",
        )
    }
    if len(set(outbound_tags.values())) != len(outbound_tags):
        raise ConfigError(
            "three_x_ui_xray_setup outbound tags must differ from each other"
        )
    if local_proxy_tag in set(outbound_tags.values()):
        raise ConfigError(
            "three_x_ui_xray_setup.local_proxy_tag must differ from the "
            "outbound tags"
        )
    tor_proxy_address = _address_port_field(
        raw.get("tor_proxy_address"),
        "three_x_ui_xray_setup.tor_proxy_address",
    )
    i2p_proxy_address = _address_port_field(
        raw.get("i2p_proxy_address"),
        "three_x_ui_xray_setup.i2p_proxy_address",
    )
    ad_block_domain_categories = _string_list(
        raw.get("ad_block_domain_categories"),
        "three_x_ui_xray_setup.ad_block_domain_categories",
    )
    direct_domains = _string_list(
        raw.get("direct_domains"),
        "three_x_ui_xray_setup.direct_domains",
    )
    direct_ip_categories = _string_list(
        raw.get("direct_ip_categories"),
        "three_x_ui_xray_setup.direct_ip_categories",
    )
    direct_ip_networks = _string_list(
        raw.get("direct_ip_networks"),
        "three_x_ui_xray_setup.direct_ip_networks",
    )
    country_services = _string_list(
        raw.get("country_services"),
        "three_x_ui_xray_setup.country_services",
    )
    country_word = _nonempty_string_field(
        raw.get("country_word"),
        "three_x_ui_xray_setup.country_word",
    )
    if any(character.isdigit() for character in country_word):
        raise ConfigError(
            "three_x_ui_xray_setup.country_word must be a word, not a code"
        )
    country_query_timeout_seconds = _positive_int_field(
        raw.get("country_query_timeout_seconds"),
        "three_x_ui_xray_setup.country_query_timeout_seconds",
    )
    country_command_timeout_seconds = _positive_int_field(
        raw.get("country_command_timeout_seconds"),
        "three_x_ui_xray_setup.country_command_timeout_seconds",
    )
    if country_command_timeout_seconds < country_query_timeout_seconds:
        raise ConfigError(
            "three_x_ui_xray_setup.country_command_timeout_seconds must not be "
            "smaller than country_query_timeout_seconds"
        )
    russia_blocked_domain_categories = _string_list(
        raw.get("russia_blocked_domain_categories"),
        "three_x_ui_xray_setup.russia_blocked_domain_categories",
    )
    russia_blocked_ip_categories = _string_list(
        raw.get("russia_blocked_ip_categories"),
        "three_x_ui_xray_setup.russia_blocked_ip_categories",
    )
    russia_direct_domain_categories = _string_list(
        raw.get("russia_direct_domain_categories"),
        "three_x_ui_xray_setup.russia_direct_domain_categories",
    )
    russia_direct_ip_categories = _string_list(
        raw.get("russia_direct_ip_categories"),
        "three_x_ui_xray_setup.russia_direct_ip_categories",
    )
    geo_restricted_domain_categories = _string_list(
        raw.get("geo_restricted_domain_categories"),
        "three_x_ui_xray_setup.geo_restricted_domain_categories",
    )
    russia_domain_strategy = _enum_field(
        raw.get("russia_domain_strategy"),
        "three_x_ui_xray_setup.russia_domain_strategy",
        DOMAIN_STRATEGIES,
    )
    outside_russia_domain_strategy = _enum_field(
        raw.get("outside_russia_domain_strategy"),
        "three_x_ui_xray_setup.outside_russia_domain_strategy",
        DOMAIN_STRATEGIES,
    )
    route_check_ad_domain = _nonempty_string_field(
        raw.get("route_check_ad_domain"),
        "three_x_ui_xray_setup.route_check_ad_domain",
    )
    route_check_foreign_domain = _nonempty_string_field(
        raw.get("route_check_foreign_domain"),
        "three_x_ui_xray_setup.route_check_foreign_domain",
    )
    route_check_onion_domain = _nonempty_string_field(
        raw.get("route_check_onion_domain"),
        "three_x_ui_xray_setup.route_check_onion_domain",
    )
    route_check_i2p_domain = _nonempty_string_field(
        raw.get("route_check_i2p_domain"),
        "three_x_ui_xray_setup.route_check_i2p_domain",
    )
    route_check_direct_domain = _nonempty_string_field(
        raw.get("route_check_direct_domain"),
        "three_x_ui_xray_setup.route_check_direct_domain",
    )
    route_check_russia_blocked_domain = _nonempty_string_field(
        raw.get("route_check_russia_blocked_domain"),
        "three_x_ui_xray_setup.route_check_russia_blocked_domain",
    )
    proxy_check_url = _nonempty_string_field(
        raw.get("proxy_check_url"),
        "three_x_ui_xray_setup.proxy_check_url",
    )
    proxy_check_blocked_url = _nonempty_string_field(
        raw.get("proxy_check_blocked_url"),
        "three_x_ui_xray_setup.proxy_check_blocked_url",
    )
    proxy_check_timeout_seconds = _positive_int_field(
        raw.get("proxy_check_timeout_seconds"),
        "three_x_ui_xray_setup.proxy_check_timeout_seconds",
    )
    panel_root_path = _nonempty_string_field(
        raw.get("panel_root_path"), "three_x_ui_xray_setup.panel_root_path"
    )
    panel_login_path = _nonempty_string_field(
        raw.get("panel_login_path"), "three_x_ui_xray_setup.panel_login_path"
    )
    panel_csrf_token_path = _nonempty_string_field(
        raw.get("panel_csrf_token_path"),
        "three_x_ui_xray_setup.panel_csrf_token_path",
    )
    panel_inbounds_list_path = _nonempty_string_field(
        raw.get("panel_inbounds_list_path"),
        "three_x_ui_xray_setup.panel_inbounds_list_path",
    )
    panel_inbounds_add_path = _nonempty_string_field(
        raw.get("panel_inbounds_add_path"),
        "three_x_ui_xray_setup.panel_inbounds_add_path",
    )
    panel_inbounds_update_path = _nonempty_string_field(
        raw.get("panel_inbounds_update_path"),
        "three_x_ui_xray_setup.panel_inbounds_update_path",
    )
    panel_inbounds_delete_path = _nonempty_string_field(
        raw.get("panel_inbounds_delete_path"),
        "three_x_ui_xray_setup.panel_inbounds_delete_path",
    )
    panel_client_get_path = _nonempty_string_field(
        raw.get("panel_client_get_path"),
        "three_x_ui_xray_setup.panel_client_get_path",
    )
    panel_client_add_path = _nonempty_string_field(
        raw.get("panel_client_add_path"),
        "three_x_ui_xray_setup.panel_client_add_path",
    )
    panel_client_links_path = _nonempty_string_field(
        raw.get("panel_client_links_path"),
        "three_x_ui_xray_setup.panel_client_links_path",
    )
    panel_x25519_cert_path = _nonempty_string_field(
        raw.get("panel_x25519_cert_path"),
        "three_x_ui_xray_setup.panel_x25519_cert_path",
    )
    panel_setting_all_path = _nonempty_string_field(
        raw.get("panel_setting_all_path"),
        "three_x_ui_xray_setup.panel_setting_all_path",
    )
    panel_setting_update_path = _nonempty_string_field(
        raw.get("panel_setting_update_path"),
        "three_x_ui_xray_setup.panel_setting_update_path",
    )
    panel_xray_status_path = _nonempty_string_field(
        raw.get("panel_xray_status_path"),
        "three_x_ui_xray_setup.panel_xray_status_path",
    )
    panel_xray_update_path = _nonempty_string_field(
        raw.get("panel_xray_update_path"),
        "three_x_ui_xray_setup.panel_xray_update_path",
    )
    panel_xray_geodata_validate_path = _nonempty_string_field(
        raw.get("panel_xray_geodata_validate_path"),
        "three_x_ui_xray_setup.panel_xray_geodata_validate_path",
    )
    panel_xray_route_test_path = _nonempty_string_field(
        raw.get("panel_xray_route_test_path"),
        "three_x_ui_xray_setup.panel_xray_route_test_path",
    )
    return ThreeXuiXraySetupConfig(
        github_repo=github_repo,
        install_script_url=install_script_url,
        install_dir=install_dir,
        service_unit_name=service_unit_name,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        install_result_env_path=install_result_env_path,
        panel_port=panel_port,
        ssl_enabled=ssl_enabled,
        panel_http_address=panel_http_address,
        panel_root_path=panel_root_path,
        panel_login_path=panel_login_path,
        panel_csrf_token_path=panel_csrf_token_path,
        panel_inbounds_list_path=panel_inbounds_list_path,
        panel_inbounds_add_path=panel_inbounds_add_path,
        panel_inbounds_update_path=panel_inbounds_update_path,
        panel_inbounds_delete_path=panel_inbounds_delete_path,
        panel_client_get_path=panel_client_get_path,
        panel_client_add_path=panel_client_add_path,
        panel_client_links_path=panel_client_links_path,
        panel_x25519_cert_path=panel_x25519_cert_path,
        panel_setting_all_path=panel_setting_all_path,
        panel_setting_update_path=panel_setting_update_path,
        panel_xray_status_path=panel_xray_status_path,
        panel_xray_update_path=panel_xray_update_path,
        panel_xray_geodata_validate_path=panel_xray_geodata_validate_path,
        panel_xray_route_test_path=panel_xray_route_test_path,
        vault_entry_title=vault_entry_title,
        connection_vault_entry_title=connection_vault_entry_title,
        share_addr_strategy=share_addr_strategy,
        inbound_port=inbound_port,
        inbound_remark=inbound_remark,
        reality_dest=reality_dest,
        reality_server_names=reality_server_names,
        reality_short_id=reality_short_id,
        reality_fingerprint=reality_fingerprint,
        subscription_path=subscription_path,
        subscription_json_path=subscription_json_path,
        subscription_clash_path=subscription_clash_path,
        acme_port=acme_port,
        cert_dir=cert_dir,
        cert_fullchain=cert_dir / "fullchain.pem",
        cert_privkey=cert_dir / "privkey.pem",
        cert_privkey_file_mode=cert_privkey_file_mode,
        cert_fullchain_file_mode=cert_fullchain_file_mode,
        self_signed_cert_dir=self_signed_cert_dir,
        self_signed_cert_fullchain=self_signed_cert_dir / "fullchain.pem",
        self_signed_cert_privkey=self_signed_cert_dir / "privkey.pem",
        server_ip_timeout_seconds=server_ip_timeout_seconds,
        server_ip_services=server_ip_services,
        probe_timeout_seconds=probe_timeout_seconds,
        probe_port_80_timeout_seconds=probe_port_80_timeout_seconds,
        probe_listener_start_seconds=probe_listener_start_seconds,
        port_forward_probe_command=port_forward_probe_command,
        port_forward_probe_url_format=port_forward_probe_url_format,
        panel_probe_command=panel_probe_command,
        tunnel_probe_command=tunnel_probe_command,
        tunnel_probe_write_out=tunnel_probe_write_out,
        upnp_enabled=upnp_enabled,
        upnp_package=upnp_package,
        upnp_client_command=upnp_client_command,
        upnp_protocol=_nonempty_string_field(
            raw.get("upnp_protocol"), "three_x_ui_xray_setup.upnp_protocol"
        ),
        upnp_mapping_description=upnp_mapping_description,
        client_profile_entry_title=client_profile_entry_title,
        local_proxy_tag=local_proxy_tag,
        local_proxy_listen_address=local_proxy_listen_address,
        local_proxy_port=local_proxy_port,
        local_proxy_udp=local_proxy_udp,
        local_proxy_sniffing_protocols=local_proxy_sniffing_protocols,
        remote_outbound_tag=outbound_tags["remote_outbound_tag"],
        tor_outbound_tag=outbound_tags["tor_outbound_tag"],
        i2p_outbound_tag=outbound_tags["i2p_outbound_tag"],
        direct_outbound_tag=outbound_tags["direct_outbound_tag"],
        blocked_outbound_tag=outbound_tags["blocked_outbound_tag"],
        tor_proxy_address=tor_proxy_address,
        i2p_proxy_address=i2p_proxy_address,
        ad_block_domain_categories=ad_block_domain_categories,
        direct_domains=direct_domains,
        direct_ip_categories=direct_ip_categories,
        direct_ip_networks=direct_ip_networks,
        country_services=country_services,
        country_word=country_word,
        country_query_timeout_seconds=country_query_timeout_seconds,
        country_command_timeout_seconds=country_command_timeout_seconds,
        russia_blocked_domain_categories=russia_blocked_domain_categories,
        russia_blocked_ip_categories=russia_blocked_ip_categories,
        russia_direct_domain_categories=russia_direct_domain_categories,
        russia_direct_ip_categories=russia_direct_ip_categories,
        geo_restricted_domain_categories=geo_restricted_domain_categories,
        russia_domain_strategy=russia_domain_strategy,
        outside_russia_domain_strategy=outside_russia_domain_strategy,
        route_check_ad_domain=route_check_ad_domain,
        route_check_foreign_domain=route_check_foreign_domain,
        route_check_onion_domain=route_check_onion_domain,
        route_check_i2p_domain=route_check_i2p_domain,
        route_check_direct_domain=route_check_direct_domain,
        route_check_russia_blocked_domain=route_check_russia_blocked_domain,
        proxy_check_url=proxy_check_url,
        proxy_check_blocked_url=proxy_check_blocked_url,
        proxy_check_timeout_seconds=proxy_check_timeout_seconds,
        proxy_check_command_timeout_seconds=_positive_int_field(
            raw.get("proxy_check_command_timeout_seconds"),
            "three_x_ui_xray_setup.proxy_check_command_timeout_seconds",
        ),
    )


def _tag_field(value: object, name: str) -> str:
    """An Xray tag: a non-empty string without whitespace.

    A tag is what the routing rules match with inboundTag and what names an
    outbound, so a tag with a space in it would never match the token the
    task builds from it.
    """

    text = _nonempty_string_field(value, name)
    if any(character.isspace() for character in text):
        raise ConfigError(f"{name} must not contain whitespace")
    return text


def _address_port_field(value: object, name: str) -> str:
    """An address:port value with a valid port.

    The local tor and i2p proxies are named this way; the address part stays
    opaque because an IPv6 address carries colons of its own, so only the
    port after the last colon is checked.
    """

    text = _nonempty_string_field(value, name)
    address, separator, port = text.rpartition(":")
    if not address or not separator or not port.isdigit():
        raise ConfigError(f"{name} must be an address:port value")
    number = int(port)
    if number < 1 or number > 65535:
        raise ConfigError(f"{name} port must be between 1 and 65535")
    return text


def _subscription_path_field(value: object, name: str) -> str:
    """A panel subscription path: a non-empty string wrapped in slashes.

    The panel warns about its well-known defaults (/sub/, /json/,
    /clash/), so the task writes its own paths. A value without the
    leading and trailing slash would be normalized differently by the
    panel and is rejected here.
    """

    text = _nonempty_string_field(value, name)
    if len(text) < 3 or not text.startswith("/") or not text.endswith("/"):
        raise ConfigError(f"{name} must start and end with a slash")
    return text





# from tor_setup.py


def _tor_setup_table(raw: object) -> TorSetupConfig:
    """Validate the [tor_setup] table and build the config.

    package_name, service_unit_name, torrc_path, torrc_dropin_path,
    torrc_include_path, hidden_service_dir and tor_user are non-empty
    strings; dropin_file_mode, hidden_service_dir_mode and
    address_file_mode are octal strings; log_level is one of the
    TOR_LOG_LEVELS values; socks_port and onion_ssh_port are port numbers
    between 1 and 65535; num_introduction_points, install_retries and
    start_check_attempts are positive integers;
    start_check_retry_delay_seconds is positive, so the readiness loop
    always waits between attempts; address_file_path is a non-empty
    string.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[tor_setup] section is missing or not a table")
    package_name = _nonempty_string_field(
        raw.get("package_name"), "tor_setup.package_name"
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "tor_setup.service_unit_name"
    )
    torrc_path = Path(
        _nonempty_string_field(raw.get("torrc_path"), "tor_setup.torrc_path")
    )
    torrc_dropin_path = Path(
        _nonempty_string_field(
            raw.get("torrc_dropin_path"), "tor_setup.torrc_dropin_path"
        )
    )
    torrc_include_path = _nonempty_string_field(
        raw.get("torrc_include_path"), "tor_setup.torrc_include_path"
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "tor_setup.dropin_file_mode"
    )
    hidden_service_dir = Path(
        _nonempty_string_field(
            raw.get("hidden_service_dir"), "tor_setup.hidden_service_dir"
        )
    )
    hidden_service_dir_mode = _octal_mode_field(
        raw.get("hidden_service_dir_mode"),
        "tor_setup.hidden_service_dir_mode",
    )
    tor_user = _nonempty_string_field(
        raw.get("tor_user"), "tor_setup.tor_user"
    )
    socks_port = _int_field(raw.get("socks_port"), "tor_setup.socks_port")
    if not 1 <= socks_port <= 65535:
        raise ConfigError("tor_setup.socks_port must be between 1 and 65535")
    onion_ssh_port = _int_field(
        raw.get("onion_ssh_port"), "tor_setup.onion_ssh_port"
    )
    if not 1 <= onion_ssh_port <= 65535:
        raise ConfigError(
            "tor_setup.onion_ssh_port must be between 1 and 65535"
        )
    num_introduction_points = _int_field(
        raw.get("num_introduction_points"),
        "tor_setup.num_introduction_points",
    )
    if num_introduction_points < 1:
        raise ConfigError(
            "tor_setup.num_introduction_points must be positive"
        )
    log_level = _nonempty_string_field(
        raw.get("log_level"), "tor_setup.log_level"
    )
    if log_level not in TOR_LOG_LEVELS:
        raise ConfigError(
            f"tor_setup.log_level must be one of {', '.join(TOR_LOG_LEVELS)}"
        )
    dropin_template_file_name = _nonempty_string_field(
        raw.get("dropin_template_file_name"),
        "tor_setup.dropin_template_file_name",
    )
    include_directive = _nonempty_string_field(
        raw.get("include_directive"), "tor_setup.include_directive"
    )
    hostname_file_name = _nonempty_string_field(
        raw.get("hostname_file_name"), "tor_setup.hostname_file_name"
    )
    verify_config_command = _string_list(
        raw.get("verify_config_command"),
        "tor_setup.verify_config_command",
    )
    service_enable_command = _string_list(
        raw.get("service_enable_command"),
        "tor_setup.service_enable_command",
    )
    service_start_command = _string_list(
        raw.get("service_start_command"),
        "tor_setup.service_start_command",
    )
    service_restart_command = _string_list(
        raw.get("service_restart_command"),
        "tor_setup.service_restart_command",
    )
    install_retries = _int_field(
        raw.get("install_retries"), "tor_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("tor_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "tor_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "tor_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "tor_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "tor_setup.start_check_retry_delay_seconds must be positive"
        )
    address_file_path = Path(
        _nonempty_string_field(
            raw.get("address_file_path"), "tor_setup.address_file_path"
        )
    )
    address_file_mode = _octal_mode_field(
        raw.get("address_file_mode"), "tor_setup.address_file_mode"
    )
    return TorSetupConfig(
        package_name=package_name,
        service_unit_name=service_unit_name,
        torrc_path=torrc_path,
        torrc_dropin_path=torrc_dropin_path,
        torrc_include_path=torrc_include_path,
        dropin_file_mode=dropin_file_mode,
        hidden_service_dir=hidden_service_dir,
        hidden_service_dir_mode=hidden_service_dir_mode,
        tor_user=tor_user,
        socks_port=socks_port,
        onion_ssh_port=onion_ssh_port,
        num_introduction_points=num_introduction_points,
        log_level=log_level,
        dropin_template_file_name=dropin_template_file_name,
        include_directive=include_directive,
        hostname_file_name=hostname_file_name,
        verify_config_command=verify_config_command,
        service_enable_command=service_enable_command,
        service_start_command=service_start_command,
        service_restart_command=service_restart_command,
        report_channel_name=_nonempty_string_field(
            raw.get("report_channel_name"), "tor_setup.report_channel_name"
        ),
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        address_file_path=address_file_path,
        address_file_mode=address_file_mode,
    )





# from vault.py


def _vault_structure_table(raw: object) -> VaultStructureConfig:
    """Validate the [vault_structure] table and build VaultStructureConfig.

    The section is mandatory and non-empty; every entry is a table with a
    unique non-empty title and a non-empty notes field. The structure is
    flat by contract, so the parser reads entries directly from the table
    and rejects unknown field names: url and any other per-entry value
    live in the vault database, not in the config. The optional groups
    array describes the data subgroups (NextDNS accounts): a group is a
    table with a unique non-empty title and notes, validated the same way
    as an entry.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[vault_structure] section is missing or not a table")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ConfigError(
            "[vault_structure] entries must be a non-empty array of tables"
        )
    entries: list[VaultEntry] = []
    seen_titles: set[str] = set()
    for index, entry_raw in enumerate(entries_raw):
        if not isinstance(entry_raw, dict):
            raise ConfigError("[vault_structure] entries must be tables")
        unknown = sorted(
            name for name in entry_raw if name not in ("title", "notes", "generated_password")
        )
        if unknown:
            raise ConfigError(
                f"[vault_structure] entry {index + 1} names unknown field(s) "
                f"{', '.join(unknown)}; expected title, notes, generated_password"
            )
        title = entry_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ConfigError(
                "[vault_structure] entry title must be a non-empty string"
            )
        if title in seen_titles:
            raise ConfigError(f"[vault_structure] duplicate entry title: {title}")
        seen_titles.add(title)
        notes = entry_raw.get("notes")
        if not isinstance(notes, str) or not notes:
            raise ConfigError(
                f"[vault_structure] entry {title}: notes must be a non-empty string"
            )
        generated_password = entry_raw.get("generated_password")
        if generated_password is not None and not isinstance(
            generated_password, str
        ):
            raise ConfigError(
                f"[vault_structure] entry {title}: generated_password must be "
                "a string like 'proquint-7'"
            )
        if generated_password is not None and not GENERATED_PASSWORD_RE.match(
            generated_password
        ):
            raise ConfigError(
                f"[vault_structure] entry {title}: generated_password must "
                "match 'proquint-N' with a positive word count"
            )
        entries.append(
            VaultEntry(
                title=title,
                notes=notes,
                generated_password=generated_password,
            )
        )
    groups_raw = raw.get("groups")
    if groups_raw is None:
        groups: tuple[VaultGroup, ...] = ()
    else:
        if not isinstance(groups_raw, list):
            raise ConfigError("[vault_structure] groups must be an array of tables")
        groups_list: list[VaultGroup] = []
        for index, group_raw in enumerate(groups_raw):
            if not isinstance(group_raw, dict):
                raise ConfigError("[vault_structure] groups must be tables")
            unknown = sorted(
                name
                for name in group_raw
                if name not in ("title", "notes", "seed_entries")
            )
            if unknown:
                raise ConfigError(
                    f"[vault_structure] group {index + 1} names unknown field(s) "
                    f"{', '.join(unknown)}; expected title, notes, seed_entries"
                )
            title = group_raw.get("title")
            if not isinstance(title, str) or not title:
                raise ConfigError(
                    "[vault_structure] group title must be a non-empty string"
                )
            if title in seen_titles:
                raise ConfigError(
                    f"[vault_structure] duplicate group title: {title}"
                )
            seen_titles.add(title)
            notes = group_raw.get("notes")
            if not isinstance(notes, str) or not notes:
                raise ConfigError(
                    f"[vault_structure] group {title}: notes must be a "
                    "non-empty string"
                )
            seed_entries = _vault_group_seed_entries(group_raw, title)
            groups_list.append(
                VaultGroup(title=title, notes=notes, seed_entries=seed_entries)
            )
        groups = tuple(groups_list)
    return VaultStructureConfig(entries=tuple(entries), groups=groups)


def _vault_group_seed_entries(
    group_raw: dict[str, object], group_title: str
) -> tuple[VaultGroupSeed, ...]:
    """Validate the seed_entries array of a group and build the tuple.

    Seed entries are the default content of a data group, so a freshly
    created vault mirrors the structure. Every seed entry is a table with
    a unique non-empty title and optional string url and notes fields. url
    is a data value, allowed here because the seed carries it into the
    database, unlike the [vault_structure] entries whose url is rejected.
    """

    raw = group_raw.get("seed_entries")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(
            f"[vault_structure] group {group_title}: seed_entries must be "
            "an array of tables"
        )
    seed_entries: list[VaultGroupSeed] = []
    seen_titles: set[str] = set()
    for index, seed_raw in enumerate(raw):
        if not isinstance(seed_raw, dict):
            raise ConfigError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1} must be a table"
            )
        unknown = sorted(
            name for name in seed_raw if name not in ("title", "url", "notes")
        )
        if unknown:
            raise ConfigError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1} names unknown field(s) {', '.join(unknown)}; "
                "expected title, url, notes"
            )
        seed_title = seed_raw.get("title")
        if not isinstance(seed_title, str) or not seed_title:
            raise ConfigError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1}: title must be a non-empty string"
            )
        if seed_title in seen_titles:
            raise ConfigError(
                f"[vault_structure] group {group_title}: duplicate seed "
                f"entry title: {seed_title}"
            )
        seen_titles.add(seed_title)
        url = seed_raw.get("url")
        if url is not None and not isinstance(url, str):
            raise ConfigError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{seed_title}: url must be a string"
            )
        seed_notes = seed_raw.get("notes")
        if seed_notes is not None and not isinstance(seed_notes, str):
            raise ConfigError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{seed_title}: notes must be a string"
            )
        seed_entries.append(
            VaultGroupSeed(title=seed_title, url=url, notes=seed_notes)
        )
    return tuple(seed_entries)


def _local_vault_setup_table(raw: object) -> LocalVaultSetupConfig:
    """Validate the [local_vault_setup] table and build the config.

    Source vault paths and the entry title are non-empty strings; the
    source paths are repository-root relative, the target paths absolute
    (the fixed locations from docs/spec/secrets-model.md).
    """

    if not isinstance(raw, dict):
        raise ConfigError("[local_vault_setup] section is missing or not a table")
    source_vault_production = raw.get("source_vault_production")
    if not isinstance(source_vault_production, str) or not source_vault_production:
        raise ConfigError(
            "local_vault_setup.source_vault_production must be a non-empty string"
        )
    source_vault_default = raw.get("source_vault_default")
    if not isinstance(source_vault_default, str) or not source_vault_default:
        raise ConfigError(
            "local_vault_setup.source_vault_default must be a non-empty string"
        )
    local_vault_path = raw.get("local_vault_path")
    if not isinstance(local_vault_path, str) or not local_vault_path:
        raise ConfigError(
            "local_vault_setup.local_vault_path must be a non-empty string"
        )
    pass_file_path = raw.get("pass_file_path")
    if not isinstance(pass_file_path, str) or not pass_file_path:
        raise ConfigError(
            "local_vault_setup.pass_file_path must be a non-empty string"
        )
    vault_password_entry_title = raw.get("vault_password_entry_title")
    if not isinstance(vault_password_entry_title, str) or not vault_password_entry_title:
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must be a non-empty string"
        )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"local_vault_setup.{name}")

    error_priority = _int_field(
        raw.get("error_priority"), "local_vault_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "local_vault_setup.error_priority must be between 0 and 7"
        )
    return LocalVaultSetupConfig(
        source_vault_production=Path(source_vault_production),
        source_vault_default=Path(source_vault_default),
        local_vault_path=Path(local_vault_path),
        pass_file_path=Path(pass_file_path),
        vault_password_entry_title=vault_password_entry_title,
        secrets_dir_mode=_file_mode_field("secrets_dir_mode"),
        local_vault_file_mode=_file_mode_field("local_vault_file_mode"),
        pass_dir_mode=_file_mode_field("pass_dir_mode"),
        pass_file_mode=_file_mode_field("pass_file_mode"),
        pass_file_writable_mode=_file_mode_field("pass_file_writable_mode"),
        error_priority=error_priority,
    )





# from vocalinux_setup.py


def _vocalinux_setup_table(raw: object) -> VocalinuxSetupConfig:
    """Validate the [vocalinux_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[vocalinux_setup] section is missing or not a table")
    return VocalinuxSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "vocalinux_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "vocalinux_setup.home_dir"
        ),
        download_dir=Path(
            _nonempty_string_field(
                raw.get("download_dir"), "vocalinux_setup.download_dir"
            )
        ),
        version=_nonempty_string_field(
            raw.get("version"), "vocalinux_setup.version"
        ),
        github_repo=_nonempty_string_field(
            raw.get("github_repo"), "vocalinux_setup.github_repo"
        ),
        asset_name_template=_nonempty_string_field(
            raw.get("asset_name_template"),
            "vocalinux_setup.asset_name_template",
        ),
        packages=_string_list(raw.get("packages"), "vocalinux_setup.packages"),
        input_group=_nonempty_string_field(
            raw.get("input_group"), "vocalinux_setup.input_group"
        ),
        appimage_dir_relative_path=_nonempty_string_field(
            raw.get("appimage_dir_relative_path"),
            "vocalinux_setup.appimage_dir_relative_path",
        ),
        app_config_relative_path=_nonempty_string_field(
            raw.get("app_config_relative_path"),
            "vocalinux_setup.app_config_relative_path",
        ),
        autostart_relative_path=_nonempty_string_field(
            raw.get("autostart_relative_path"),
            "vocalinux_setup.autostart_relative_path",
        ),
        echo_desktop_relative_path=_nonempty_string_field(
            raw.get("echo_desktop_relative_path"),
            "vocalinux_setup.echo_desktop_relative_path",
        ),
        app_config_template_file_name=_nonempty_string_field(
            raw.get("app_config_template_file_name"),
            "vocalinux_setup.app_config_template_file_name",
        ),
        autostart_template_file_name=_nonempty_string_field(
            raw.get("autostart_template_file_name"),
            "vocalinux_setup.autostart_template_file_name",
        ),
        echo_desktop_template_file_name=_nonempty_string_field(
            raw.get("echo_desktop_template_file_name"),
            "vocalinux_setup.echo_desktop_template_file_name",
        ),
        shortcuts_file_name=_nonempty_string_field(
            raw.get("shortcuts_file_name"),
            "vocalinux_setup.shortcuts_file_name",
        ),
        shortcut_group_name=_nonempty_string_field(
            raw.get("shortcut_group_name"),
            "vocalinux_setup.shortcut_group_name",
        ),
        shortcut_entry_name=_nonempty_string_field(
            raw.get("shortcut_entry_name"),
            "vocalinux_setup.shortcut_entry_name",
        ),
        shortcut_action_name=_nonempty_string_field(
            raw.get("shortcut_action_name"),
            "vocalinux_setup.shortcut_action_name",
        ),
        shortcut_key_sequence=_nonempty_string_field(
            raw.get("shortcut_key_sequence"),
            "vocalinux_setup.shortcut_key_sequence",
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "vocalinux_setup.service_unit_name"
        ),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "vocalinux_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "vocalinux_setup.package_install_retries",
        ),
        user_file_mode=_octal_mode_field(
            raw.get("user_file_mode"), "vocalinux_setup.user_file_mode"
        ),
        executable_file_mode=_octal_mode_field(
            raw.get("executable_file_mode"),
            "vocalinux_setup.executable_file_mode",
        ),
        runuser_command=_placeholder_command_field(
            raw.get("runuser_command"),
            "vocalinux_setup.runuser_command",
            ("{username}",),
        ),
        kreadconfig_command=_placeholder_command_field(
            raw.get("kreadconfig_command"),
            "vocalinux_setup.kreadconfig_command",
            ("{file_name}",),
        ),
        kwriteconfig_command=_placeholder_command_field(
            raw.get("kwriteconfig_command"),
            "vocalinux_setup.kwriteconfig_command",
            ("{file_name}",),
        ),
        config_group_flag=_placeholder_command_field(
            raw.get("config_group_flag"),
            "vocalinux_setup.config_group_flag",
            ("{group}",),
        ),
        config_key_flag=_placeholder_command_field(
            raw.get("config_key_flag"),
            "vocalinux_setup.config_key_flag",
            ("{key}",),
        ),
        mkdir_command=_placeholder_command_field(
            raw.get("mkdir_command"),
            "vocalinux_setup.mkdir_command",
            ("{path}",),
        ),
        chown_command=_placeholder_command_field(
            raw.get("chown_command"),
            "vocalinux_setup.chown_command",
            ("{owner}", "{path}"),
        ),
        chmod_command=_placeholder_command_field(
            raw.get("chmod_command"),
            "vocalinux_setup.chmod_command",
            ("{file_mode}", "{path}"),
        ),
        group_members_command=_placeholder_command_field(
            raw.get("group_members_command"),
            "vocalinux_setup.group_members_command",
            ("{username}",),
        ),
        group_add_command=_placeholder_command_field(
            raw.get("group_add_command"),
            "vocalinux_setup.group_add_command",
            ("{input_group}", "{username}"),
        ),
        service_active_command=_placeholder_command_field(
            raw.get("service_active_command"),
            "vocalinux_setup.service_active_command",
            ("{username}", "{service_unit_name}"),
        ),
        service_enable_command=_placeholder_command_field(
            raw.get("service_enable_command"),
            "vocalinux_setup.service_enable_command",
            ("{username}", "{service_unit_name}"),
        ),
    )





# from yggdrasil_service_setup.py


def _yggdrasil_uri_list_field(
    raw: object, name: str, schemes: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate an array of yggdrasil URIs with allowed schemes.

    A missing array means an empty list. Every entry is a non-empty
    string whose scheme is in schemes; the scheme is everything before
    the first colon, so a malformed URI without a colon is rejected.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of strings")
    result: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"{name}[{index}] must be a non-empty string")
        scheme = entry.split(":", 1)[0].casefold()
        if scheme not in schemes:
            raise ConfigError(
                f"{name}[{index}] scheme {scheme} is not supported, "
                f"allowed: {', '.join(schemes)}"
            )
        result.append(entry)
    return tuple(result)


def _yggdrasil_multicast_field(
    raw: object, name: str
) -> tuple[YggdrasilMulticastInterfaceConfig, ...]:
    """Validate the multicast_interfaces array of the yggdrasil table.

    A missing array means no multicast discovery. Every entry is a table
    with a non-empty regex string and strict boolean beacon and listen
    switches.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    result: list[YggdrasilMulticastInterfaceConfig] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"{name} must be an array of tables")
        regex = entry.get("regex")
        if not isinstance(regex, str) or not regex:
            raise ConfigError(f"{name}[{index}] regex must be a non-empty string")
        beacon = entry.get("beacon")
        if not isinstance(beacon, bool):
            raise ConfigError(f"{name}[{index}] beacon must be a boolean")
        listen = entry.get("listen")
        if not isinstance(listen, bool):
            raise ConfigError(f"{name}[{index}] listen must be a boolean")
        result.append(
            YggdrasilMulticastInterfaceConfig(
                regex=regex, beacon=beacon, listen=listen
            )
        )
    return tuple(result)


def _yggdrasil_command_fields(raw: dict[str, object]) -> dict[str, tuple[str, ...]]:
    """Validate the command arrays of the [yggdrasil_service_setup] table.

    Every command is a non-empty array of non-empty strings, and every
    placeholder the task fills in must be present, so a mistyped
    placeholder is caught here instead of raising on the target machine.
    """

    required_placeholders: dict[str, tuple[str, ...]] = {
        "installed_version_command": (),
        "export_key_from_config_command": ("{config_path}",),
        "generate_config_command": (),
        "export_key_from_stdin_command": (),
        "peers_latency_command": (),
        "self_address_command": (),
        "journal_connected_query_command": ("{service_unit_name}", "{probe_seconds}"),
        "service_start_command": ("{service_unit_name}",),
        "service_restart_command": ("{service_unit_name}",),
        "service_enable_command": ("{service_unit_name}",),
        "nmcli_reload_command": (),
        "nmcli_connection_show_command": ("{connection_name}",),
        "nmcli_connection_delete_command": ("{connection_name}",),
        "ip_link_show_command": ("{interface_name}",),
        "ip_link_delete_command": ("{interface_name}",),
    }
    commands: dict[str, tuple[str, ...]] = {}
    for key, placeholders in required_placeholders.items():
        name = f"yggdrasil_service_setup.{key}"
        command = _string_list(raw.get(key), name)
        for placeholder in placeholders:
            if not any(placeholder in part for part in command):
                raise ConfigError(f"{name} must carry the {placeholder} placeholder")
        commands[key] = command
    return commands


def _yggdrasil_service_setup_table(raw: object) -> YggdrasilServiceSetupConfig:
    """Validate the [yggdrasil_service_setup] table and build the config.

    github_repo, download_dir, service_unit_name, config_path,
    private_key_path, if_name, admin_listen and peers_tarball_url are
    non-empty strings; install_retries, if_mtu, peer_batch_size and
    peer_target_count are positive integers; if_mtu stays within the
    yggdrasil range; config_file_mode, private_key_file_mode and
    address_file_mode are octal strings; listen and static_peers are URI
    arrays with the allowed schemes; multicast_interfaces is the
    multicast block array; peer_probe_timeout_seconds is positive and
    peer_max_batches is non-negative; address_file_path is a non-empty
    string; address_save_retry_base_seconds is positive,
    address_save_retry_multiplier is at least 2 and
    address_save_retry_max_seconds is not below the base.

    asset_name_template carries its version and architecture
    placeholders and release_tag_prefix is a non-empty string; every
    command array is non-empty and carries the placeholders the task
    fills in; nm_unmanaged_conf_body and netplan_interface_marker carry
    the interface placeholder; the suffixes, the temporary file name
    parts, line_separator and config_json_indent are values of their
    types; config_document_keys names every key of the rendered
    configuration and admin_output_keys every field the task reads from
    the admin socket output.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[yggdrasil_service_setup] section is missing or not a table"
        )
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "yggdrasil_service_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(
            raw.get("download_dir"), "yggdrasil_service_setup.download_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "yggdrasil_service_setup.service_unit_name"
    )
    install_retries = _int_field(
        raw.get("install_retries"), "yggdrasil_service_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError(
            "yggdrasil_service_setup.install_retries must be positive"
        )
    config_path = Path(
        _nonempty_string_field(
            raw.get("config_path"), "yggdrasil_service_setup.config_path"
        )
    )
    private_key_path = Path(
        _nonempty_string_field(
            raw.get("private_key_path"), "yggdrasil_service_setup.private_key_path"
        )
    )
    config_file_mode = _octal_mode_field(
        raw.get("config_file_mode"), "yggdrasil_service_setup.config_file_mode"
    )
    private_key_file_mode = _octal_mode_field(
        raw.get("private_key_file_mode"),
        "yggdrasil_service_setup.private_key_file_mode",
    )
    if_name = _nonempty_string_field(
        raw.get("if_name"), "yggdrasil_service_setup.if_name"
    )
    if_mtu = _int_field(raw.get("if_mtu"), "yggdrasil_service_setup.if_mtu")
    if not 1280 <= if_mtu <= 65535:
        raise ConfigError(
            "yggdrasil_service_setup.if_mtu must be between 1280 and 65535"
        )
    admin_listen = _nonempty_string_field(
        raw.get("admin_listen"), "yggdrasil_service_setup.admin_listen"
    )
    listen = _yggdrasil_uri_list_field(
        raw.get("listen"), "yggdrasil_service_setup.listen", YGGDRASIL_LISTEN_SCHEMES
    )
    multicast_interfaces = _yggdrasil_multicast_field(
        raw.get("multicast_interfaces"),
        "yggdrasil_service_setup.multicast_interfaces",
    )
    peers_full_path = Path(
        _nonempty_string_field(
            raw.get("peers_full_path"), "yggdrasil_service_setup.peers_full_path"
        )
    )
    peers_tarball_url = _nonempty_string_field(
        raw.get("peers_tarball_url"), "yggdrasil_service_setup.peers_tarball_url"
    )
    peer_batch_size = _int_field(
        raw.get("peer_batch_size"), "yggdrasil_service_setup.peer_batch_size"
    )
    if peer_batch_size < 1:
        raise ConfigError(
            "yggdrasil_service_setup.peer_batch_size must be positive"
        )
    peer_target_count = _int_field(
        raw.get("peer_target_count"), "yggdrasil_service_setup.peer_target_count"
    )
    if peer_target_count < 1:
        raise ConfigError(
            "yggdrasil_service_setup.peer_target_count must be positive"
        )
    peer_probe_timeout_seconds = _float_field(
        raw.get("peer_probe_timeout_seconds"),
        "yggdrasil_service_setup.peer_probe_timeout_seconds",
    )
    if peer_probe_timeout_seconds <= 0:
        raise ConfigError(
            "yggdrasil_service_setup.peer_probe_timeout_seconds must be positive"
        )
    peer_max_batches = _int_field(
        raw.get("peer_max_batches"), "yggdrasil_service_setup.peer_max_batches"
    )
    if peer_max_batches < 0:
        raise ConfigError(
            "yggdrasil_service_setup.peer_max_batches must not be negative"
        )
    static_peers = _yggdrasil_uri_list_field(
        raw.get("static_peers"),
        "yggdrasil_service_setup.static_peers",
        YGGDRASIL_PEER_SCHEMES,
    )
    address_file_path = Path(
        _nonempty_string_field(
            raw.get("address_file_path"), "yggdrasil_service_setup.address_file_path"
        )
    )
    address_file_mode = _octal_mode_field(
        raw.get("address_file_mode"), "yggdrasil_service_setup.address_file_mode"
    )
    address_save_retry_base_seconds = _int_field(
        raw.get("address_save_retry_base_seconds"),
        "yggdrasil_service_setup.address_save_retry_base_seconds",
    )
    if address_save_retry_base_seconds < 1:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_base_seconds must be positive"
        )
    address_save_retry_multiplier = _int_field(
        raw.get("address_save_retry_multiplier"),
        "yggdrasil_service_setup.address_save_retry_multiplier",
    )
    if address_save_retry_multiplier < 2:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_multiplier must be at least 2"
        )
    address_save_retry_max_seconds = _int_field(
        raw.get("address_save_retry_max_seconds"),
        "yggdrasil_service_setup.address_save_retry_max_seconds",
    )
    if address_save_retry_max_seconds < address_save_retry_base_seconds:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_max_seconds must be at "
            "least address_save_retry_base_seconds"
        )
    connection_wait_base_seconds = _int_field(
        raw.get("connection_wait_base_seconds"),
        "yggdrasil_service_setup.connection_wait_base_seconds",
    )
    if connection_wait_base_seconds < 1:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_base_seconds must be positive"
        )
    connection_wait_multiplier = _int_field(
        raw.get("connection_wait_multiplier"),
        "yggdrasil_service_setup.connection_wait_multiplier",
    )
    if connection_wait_multiplier < 2:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_multiplier must be at least 2"
        )
    connection_wait_max_seconds = _int_field(
        raw.get("connection_wait_max_seconds"),
        "yggdrasil_service_setup.connection_wait_max_seconds",
    )
    if connection_wait_max_seconds < connection_wait_base_seconds:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_max_seconds must be at "
            "least connection_wait_base_seconds"
        )
    nm_unmanaged_conf_path = Path(
        _nonempty_string_field(
            raw.get("nm_unmanaged_conf_path"),
            "yggdrasil_service_setup.nm_unmanaged_conf_path",
        )
    )
    nm_unmanaged_conf_file_mode = _octal_mode_field(
        raw.get("nm_unmanaged_conf_file_mode"),
        "yggdrasil_service_setup.nm_unmanaged_conf_file_mode",
    )
    netplan_dir_path = Path(
        _nonempty_string_field(
            raw.get("netplan_dir_path"),
            "yggdrasil_service_setup.netplan_dir_path",
        )
    )
    asset_name_template = _nonempty_string_field(
        raw.get("asset_name_template"),
        "yggdrasil_service_setup.asset_name_template",
    )
    for placeholder in ("{version}", "{arch}"):
        if placeholder not in asset_name_template:
            raise ConfigError(
                "yggdrasil_service_setup.asset_name_template must carry the "
                f"{placeholder} placeholder"
            )
    release_tag_prefix = _nonempty_string_field(
        raw.get("release_tag_prefix"),
        "yggdrasil_service_setup.release_tag_prefix",
    )
    commands = _yggdrasil_command_fields(raw)
    nm_unmanaged_conf_body = _nonempty_string_field(
        raw.get("nm_unmanaged_conf_body"),
        "yggdrasil_service_setup.nm_unmanaged_conf_body",
    )
    netplan_interface_marker = _nonempty_string_field(
        raw.get("netplan_interface_marker"),
        "yggdrasil_service_setup.netplan_interface_marker",
    )
    for key, text in (
        ("nm_unmanaged_conf_body", nm_unmanaged_conf_body),
        ("netplan_interface_marker", netplan_interface_marker),
    ):
        if "{interface_name}" not in text:
            raise ConfigError(
                f"yggdrasil_service_setup.{key} must carry the "
                "{interface_name} placeholder"
            )
    netplan_file_suffix = _nonempty_string_field(
        raw.get("netplan_file_suffix"),
        "yggdrasil_service_setup.netplan_file_suffix",
    )
    netplan_backup_suffix = _nonempty_string_field(
        raw.get("netplan_backup_suffix"),
        "yggdrasil_service_setup.netplan_backup_suffix",
    )
    peers_tarball_temp_prefix = _nonempty_string_field(
        raw.get("peers_tarball_temp_prefix"),
        "yggdrasil_service_setup.peers_tarball_temp_prefix",
    )
    peers_tarball_temp_suffix = _nonempty_string_field(
        raw.get("peers_tarball_temp_suffix"),
        "yggdrasil_service_setup.peers_tarball_temp_suffix",
    )
    peer_markdown_suffix = _nonempty_string_field(
        raw.get("peer_markdown_suffix"),
        "yggdrasil_service_setup.peer_markdown_suffix",
    )
    line_separator = _nonempty_string_field(
        raw.get("line_separator"),
        "yggdrasil_service_setup.line_separator",
    )
    config_json_indent = _int_field(
        raw.get("config_json_indent"),
        "yggdrasil_service_setup.config_json_indent",
    )
    if config_json_indent < 0:
        raise ConfigError(
            "yggdrasil_service_setup.config_json_indent must not be negative"
        )
    config_document_keys = _string_map(
        raw.get("config_document_keys"),
        "yggdrasil_service_setup.config_document_keys",
    )
    for key in (
        "private_key_path",
        "admin_listen",
        "if_name",
        "if_mtu",
        "listen",
        "multicast_interfaces",
        "multicast_regex",
        "multicast_beacon",
        "multicast_listen",
        "peers",
    ):
        if not config_document_keys.get(key):
            raise ConfigError(
                "yggdrasil_service_setup.config_document_keys must name the "
                f"{key} key of the configuration document"
            )
    admin_output_keys = _string_map(
        raw.get("admin_output_keys"),
        "yggdrasil_service_setup.admin_output_keys",
    )
    for key in ("address", "peers", "remote", "latency"):
        if not admin_output_keys.get(key):
            raise ConfigError(
                "yggdrasil_service_setup.admin_output_keys must name the "
                f"{key} field of the admin socket output"
            )
    return YggdrasilServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        install_retries=install_retries,
        config_path=config_path,
        private_key_path=private_key_path,
        config_file_mode=config_file_mode,
        private_key_file_mode=private_key_file_mode,
        if_name=if_name,
        if_mtu=if_mtu,
        admin_listen=admin_listen,
        listen=listen,
        multicast_interfaces=multicast_interfaces,
        peers_full_path=peers_full_path,
        peers_tarball_url=peers_tarball_url,
        peer_batch_size=peer_batch_size,
        peer_target_count=peer_target_count,
        peer_probe_timeout_seconds=peer_probe_timeout_seconds,
        peer_max_batches=peer_max_batches,
        static_peers=static_peers,
        address_file_path=address_file_path,
        address_file_mode=address_file_mode,
        address_save_retry_base_seconds=address_save_retry_base_seconds,
        address_save_retry_multiplier=address_save_retry_multiplier,
        address_save_retry_max_seconds=address_save_retry_max_seconds,
        connection_wait_base_seconds=connection_wait_base_seconds,
        connection_wait_multiplier=connection_wait_multiplier,
        connection_wait_max_seconds=connection_wait_max_seconds,
        report_channel_name=_nonempty_string_field(
            raw.get("report_channel_name"),
            "yggdrasil_service_setup.report_channel_name",
        ),
        nm_unmanaged_conf_path=nm_unmanaged_conf_path,
        nm_unmanaged_conf_file_mode=nm_unmanaged_conf_file_mode,
        netplan_dir_path=netplan_dir_path,
        asset_name_template=asset_name_template,
        release_tag_prefix=release_tag_prefix,
        installed_version_command=commands["installed_version_command"],
        export_key_from_config_command=commands["export_key_from_config_command"],
        generate_config_command=commands["generate_config_command"],
        export_key_from_stdin_command=commands["export_key_from_stdin_command"],
        peers_latency_command=commands["peers_latency_command"],
        self_address_command=commands["self_address_command"],
        journal_connected_query_command=commands[
            "journal_connected_query_command"
        ],
        service_start_command=commands["service_start_command"],
        service_restart_command=commands["service_restart_command"],
        service_enable_command=commands["service_enable_command"],
        nmcli_reload_command=commands["nmcli_reload_command"],
        nmcli_connection_show_command=commands["nmcli_connection_show_command"],
        nmcli_connection_delete_command=commands[
            "nmcli_connection_delete_command"
        ],
        ip_link_show_command=commands["ip_link_show_command"],
        ip_link_delete_command=commands["ip_link_delete_command"],
        nm_unmanaged_conf_body=nm_unmanaged_conf_body,
        netplan_interface_marker=netplan_interface_marker,
        netplan_file_suffix=netplan_file_suffix,
        netplan_backup_suffix=netplan_backup_suffix,
        peers_tarball_temp_prefix=peers_tarball_temp_prefix,
        peers_tarball_temp_suffix=peers_tarball_temp_suffix,
        peer_markdown_suffix=peer_markdown_suffix,
        line_separator=line_separator,
        config_json_indent=config_json_indent,
        config_document_keys=config_document_keys,
        admin_output_keys=admin_output_keys,
    )





# from zram_service.py


def _zram_service_table(raw: object) -> ZramServiceConfig:
    """Validate the [zram_service] table and build ZramServiceConfig.

    compressor is a non-empty string; swap_priority is a positive swap
    priority; memory_fraction_percent is a percentage between 1 and 100;
    fallback_cpu_count is at least 1; alignment_bytes is positive, because
    the zram driver rejects a non-positive or unaligned disksize;
    reset_busy_attempts is at least 1 and
    reset_busy_retry_delay_seconds is positive.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zram_service] section is missing or not a table")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zram_service.compressor must be a non-empty string")
    swap_priority = _int_field(
        raw.get("swap_priority"), "zram_service.swap_priority"
    )
    if swap_priority < 1:
        raise ConfigError("zram_service.swap_priority must be positive")
    memory_fraction_percent = _int_field(
        raw.get("memory_fraction_percent"),
        "zram_service.memory_fraction_percent",
    )
    if not 1 <= memory_fraction_percent <= 100:
        raise ConfigError(
            "zram_service.memory_fraction_percent must be between 1 and 100"
        )
    fallback_cpu_count = _int_field(
        raw.get("fallback_cpu_count"), "zram_service.fallback_cpu_count"
    )
    if fallback_cpu_count < 1:
        raise ConfigError("zram_service.fallback_cpu_count must be at least 1")
    alignment_bytes = _int_field(
        raw.get("alignment_bytes"), "zram_service.alignment_bytes"
    )
    if alignment_bytes < 1:
        raise ConfigError("zram_service.alignment_bytes must be positive")
    reset_busy_attempts = _int_field(
        raw.get("reset_busy_attempts"), "zram_service.reset_busy_attempts"
    )
    if reset_busy_attempts < 1:
        raise ConfigError(
            "zram_service.reset_busy_attempts must be at least 1"
        )
    reset_busy_retry_delay_seconds = _float_field(
        raw.get("reset_busy_retry_delay_seconds"),
        "zram_service.reset_busy_retry_delay_seconds",
    )
    if reset_busy_retry_delay_seconds <= 0:
        raise ConfigError(
            "zram_service.reset_busy_retry_delay_seconds must be positive"
        )
    return ZramServiceConfig(
        compressor=compressor,
        swap_priority=swap_priority,
        memory_fraction_percent=memory_fraction_percent,
        fallback_cpu_count=fallback_cpu_count,
        alignment_bytes=alignment_bytes,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zram_service.service_unit_name"
        ),
        reset_busy_attempts=reset_busy_attempts,
        reset_busy_retry_delay_seconds=reset_busy_retry_delay_seconds,
        hot_add_readable_mode_bit=_octal_mode_field(
            raw.get("hot_add_readable_mode_bit"),
            "zram_service.hot_add_readable_mode_bit",
        ),
        module_name=_nonempty_string_field(
            raw.get("module_name"), "zram_service.module_name"
        ),
        unit_template_file_name=_nonempty_string_field(
            raw.get("unit_template_file_name"),
            "zram_service.unit_template_file_name",
        ),
        swap_show_command=_string_list(
            raw.get("swap_show_command"), "zram_service.swap_show_command"
        ),
        module_load_command=_placeholder_command_field(
            raw.get("module_load_command"),
            "zram_service.module_load_command",
            ("{module_name}",),
        ),
        swap_off_command=_placeholder_command_field(
            raw.get("swap_off_command"),
            "zram_service.swap_off_command",
            ("{device_path}",),
        ),
        format_command=_placeholder_command_field(
            raw.get("format_command"),
            "zram_service.format_command",
            ("{device_path}",),
        ),
        swap_on_command=_placeholder_command_field(
            raw.get("swap_on_command"),
            "zram_service.swap_on_command",
            ("{swap_priority}", "{device_path}"),
        ),
        systemctl_daemon_reload_command=_string_list(
            raw.get("systemctl_daemon_reload_command"),
            "zram_service.systemctl_daemon_reload_command",
        ),
        systemctl_enable_command=_placeholder_command_field(
            raw.get("systemctl_enable_command"),
            "zram_service.systemctl_enable_command",
            ("{service_unit_name}",),
        ),
        unit_load_line=_placeholder_text_field(
            raw.get("unit_load_line"),
            "zram_service.unit_load_line",
            ("{module_name}",),
        ),
        unit_add_read_line=_placeholder_text_field(
            raw.get("unit_add_read_line"),
            "zram_service.unit_add_read_line",
            ("{hot_add_path}",),
        ),
        unit_add_write_line=_placeholder_text_field(
            raw.get("unit_add_write_line"),
            "zram_service.unit_add_write_line",
            ("{hot_add_path}",),
        ),
        unit_algorithm_line=_placeholder_text_field(
            raw.get("unit_algorithm_line"),
            "zram_service.unit_algorithm_line",
            ("{compressor}", "{algorithm_attribute}"),
        ),
        unit_disksize_line=_placeholder_text_field(
            raw.get("unit_disksize_line"),
            "zram_service.unit_disksize_line",
            ("{size_bytes}", "{disksize_attribute}"),
        ),
        unit_format_line=_placeholder_text_field(
            raw.get("unit_format_line"),
            "zram_service.unit_format_line",
            ("{device_path}",),
        ),
        unit_swap_on_line=_placeholder_text_field(
            raw.get("unit_swap_on_line"),
            "zram_service.unit_swap_on_line",
            ("{swap_priority}", "{device_path}"),
        ),
    )





# from zswap_service.py


def _zswap_service_table(raw: object) -> ZswapServiceConfig:
    """Validate the [zswap_service] table and build ZswapServiceConfig.

    enabled and shrinker_enabled are strict booleans; compressor is a
    non-empty string; max_pool_percent and accept_threshold_percent are
    integers between 1 and 100, the meaningful range for a percentage that
    the kernel accepts on the sysfs attributes. A pool ceiling of zero
    would disable zswap entirely, so it is rejected here.

    parameter_names, unit_template_file_name and the two systemctl command
    arrays are the names and the commands of the task; the enable command
    must carry its "{service_unit_name}" placeholder.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zswap_service] section is missing or not a table")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("zswap_service.enabled must be a boolean")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zswap_service.compressor must be a non-empty string")
    max_pool_percent = _int_field(
        raw.get("max_pool_percent"), "zswap_service.max_pool_percent"
    )
    if not 1 <= max_pool_percent <= 100:
        raise ConfigError(
            "zswap_service.max_pool_percent must be between 1 and 100"
        )
    accept_threshold_percent = _int_field(
        raw.get("accept_threshold_percent"),
        "zswap_service.accept_threshold_percent",
    )
    if not 1 <= accept_threshold_percent <= 100:
        raise ConfigError(
            "zswap_service.accept_threshold_percent must be between 1 and 100"
        )
    shrinker_enabled = raw.get("shrinker_enabled")
    if not isinstance(shrinker_enabled, bool):
        raise ConfigError("zswap_service.shrinker_enabled must be a boolean")
    return ZswapServiceConfig(
        enabled=enabled,
        compressor=compressor,
        max_pool_percent=max_pool_percent,
        accept_threshold_percent=accept_threshold_percent,
        shrinker_enabled=shrinker_enabled,
        parameters_dir_path=Path(
            _nonempty_string_field(
                raw.get("parameters_dir_path"),
                "zswap_service.parameters_dir_path",
            )
        ),
        parameter_names=_string_list(
            raw.get("parameter_names"), "zswap_service.parameter_names"
        ),
        unit_template_file_name=_nonempty_string_field(
            raw.get("unit_template_file_name"),
            "zswap_service.unit_template_file_name",
        ),
        systemctl_daemon_reload_command=_string_list(
            raw.get("systemctl_daemon_reload_command"),
            "zswap_service.systemctl_daemon_reload_command",
        ),
        systemctl_enable_command=_placeholder_command_field(
            raw.get("systemctl_enable_command"),
            "zswap_service.systemctl_enable_command",
            ("{service_unit_name}",),
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zswap_service.service_unit_name"
        ),
    )


def strict_config_from_document(document: dict[str, Any]) -> Config:
    """Build the Config with every check the package used to run.

    The test suite calls this instead of the runtime reader: a config that
    breaks a rule fails here, during development, and never on a machine.
    """

    vault_structure = _vault_structure_table(document.get("vault_structure"))
    local_vault_setup = _local_vault_setup_table(document.get("local_vault_setup"))
    system_metrics_setup = _system_metrics_setup_table(
        document.get("system_metrics_setup")
    )
    if not any(
        entry.title == local_vault_setup.vault_password_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must name an entry "
            "of the [vault_structure] table"
        )
    if not any(
        entry.title == system_metrics_setup.google_script_key_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "system_metrics_setup.google_script_key_entry_title must name an "
            "entry of the [vault_structure] table"
        )
    port_forwarding_setup = _port_forwarding_setup_table(
        document.get("port_forwarding_setup")
    )
    if not any(
        entry.title == port_forwarding_setup.passphrase_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "port_forwarding_setup.passphrase_entry_title must name an entry "
            "of the [vault_structure] table"
        )
    three_x_ui = document.get("three_x_ui_xray_setup")
    if isinstance(three_x_ui, dict):
        vault_entry_title = three_x_ui.get("vault_entry_title")
        if vault_entry_title is not None and not any(
            entry.title == vault_entry_title
            for entry in vault_structure.entries
        ):
            raise ConfigError(
                "three_x_ui_xray_setup.vault_entry_title must name an entry "
                "of the [vault_structure] table"
            )
        connection_title = three_x_ui.get("connection_vault_entry_title")
        if connection_title is not None and not any(
            entry.title == connection_title
            for entry in vault_structure.entries
        ):
            raise ConfigError(
                "three_x_ui_xray_setup.connection_vault_entry_title must name "
                "an entry of the [vault_structure] table"
            )
    rustdesk_setup = _rustdesk_setup_table(document.get("rustdesk_setup"))
    if not any(
        entry.title == rustdesk_setup.vault_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "rustdesk_setup.vault_entry_title must name an entry of the "
            "[vault_structure] table"
        )
    config = Config(
        engine=_engine_table(document.get("engine")),
        cli_tools=_cli_tools_table(document.get("cli_tools")),
        chrome_setup=_chrome_setup_table(document.get("chrome_setup")),
        dnsproxy_setup=_dnsproxy_setup_table(document.get("dnsproxy_setup")),
        add_extra_repos=_add_extra_repos_table(document.get("add_extra_repos")),
        hostname=_hostname_table(document.get("hostname")),
        ffmpeg_setup=_ffmpeg_setup_table(document.get("ffmpeg_setup")),
        imagemagick_setup=_imagemagick_setup_table(
            document.get("imagemagick_setup")
        ),
        kde_keyboard_setup=_kde_keyboard_setup_table(document.get("kde_keyboard_setup")),
        kde_settings=_kde_settings_table(document.get("kde_settings")),
        swapfile_service_install=_swapfile_service_install_table(
            document.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(document.get("zswap_service")),
        zram_service=_zram_service_table(document.get("zram_service")),
        telegram_setup=_telegram_setup_table(document.get("telegram_setup")),
        i2pd_service_setup=_i2pd_service_setup_table(
            document.get("i2pd_service_setup")
        ),
        yggdrasil_service_setup=_yggdrasil_service_setup_table(
            document.get("yggdrasil_service_setup")
        ),
        three_x_ui_xray_setup=_three_x_ui_xray_setup_table(
            document.get("three_x_ui_xray_setup")
        ),
        tor_setup=_tor_setup_table(document.get("tor_setup")),
        ssh_daemon_setup=_ssh_daemon_setup_table(document.get("ssh_daemon_setup")),
        ssh_client_setup=_ssh_client_setup_table(document.get("ssh_client_setup")),
        vocalinux_setup=_vocalinux_setup_table(document.get("vocalinux_setup")),
        nextdns_setup_system_wide=_nextdns_setup_system_wide_table(
            document.get("nextdns_setup_system_wide")
        ),
        playwright_setup=_playwright_setup_table(
            document.get("playwright_setup")
        ),
        port_forwarding_setup=port_forwarding_setup,
        rustdesk_setup=rustdesk_setup,
        system_metrics_setup=system_metrics_setup,
        vault_structure=vault_structure,
        local_vault_setup=local_vault_setup,
        tasks=_tasks_table(document.get("tasks")),
    )
    return config
