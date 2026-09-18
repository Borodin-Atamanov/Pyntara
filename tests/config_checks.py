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

from pathlib import Path
from typing import Any

from pyntara.config import (
    AddExtraReposConfig,
    ChromeSetupConfig,
    CliToolsConfig,
    Config,
    DnsproxySetupConfig,
    FfmpegSetupConfig,
    HostnameConfig,
    ImagemagickSetupConfig,
    KdeKeyboardSetupConfig,
    LocalVaultSetupConfig,
    NextdnsSetupSystemWideConfig,
    PlaywrightSetupConfig,
    RustdeskOptionConfig,
    RustdeskSetupConfig,
    ScrcpySetupConfig,
    SotavpnSetupConfig,
    SwapfileServiceInstallConfig,
    TelegramSetupConfig,
    VaultEntry,
    VaultGroup,
    VaultGroupSeed,
    VaultStructureConfig,
    VocalinuxSetupConfig,
    ZramServiceConfig,
    ZswapServiceConfig,
)
from pyntara.config.vault import GENERATED_PASSWORD_RE

# The vocabulary constants the checks validate against, and the error they
# raise. They live here, in the test suite, because the runtime reader never
# checks a value and never raises: a rule of the config needs no name in the
# shipped package. Two exceptions, which come from pyntara.config because
# production reads them: MODES, to accept or reject an install mode, and
# SEND_ORDERS, the order of the deployed sender.


class ConfigError(RuntimeError):
    """Raised by a check when a config value is missing or invalid."""


DNS_OVER_TLS_VALUES: tuple[str, ...] = ("yes", "opportunistic", "no")

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


def _complete_string_map(
    raw: object, name: str, required_keys: tuple[str, ...]
) -> dict[str, str]:
    """Validate a map and demand the meanings the code reads from it.

    A map the code indexes by a fixed meaning has to carry that meaning:
    a missing one would raise on the target machine, where nobody can add
    it, so the check refuses the config here instead.
    """

    result = _string_map(raw, name)
    missing = [key for key in required_keys if key not in result]
    if missing:
        raise ConfigError(f"{name} is missing the keys {', '.join(missing)}")
    return result


REPORT_KEY_MEANINGS = (
    "generated_at",
    "ready_percent",
    "network",
    "system",
    "name",
    "status",
    "output",
)

REPORT_STATUS_WORD_MEANINGS = (
    "ok",
    "empty",
    "error",
)

# The meanings the code of every address command indexes in the map of
# report record keys of the [engine] table. A map that misses one of them
# fails here instead of raising on the target machine, where nobody can add
# it.
REPORT_RECORD_KEY_MEANINGS = (
    "channel",
    "address",
    "port",
    "proxy",
    "ssh",
    "note",
    "server",
    "local_port",
    "remote_port",
    "family",
    "interface",
    "scope",
    "word",
    "in_country",
    "values",
    "answers",
    "source",
    "document",
    "reason",
)


# The address families the public address report writes into the family field
# of its records. The words are values of the shipped telemetry, so a map that
# misses one of them fails here instead of raising on the target machine.
REPORT_FAMILY_WORD_MEANINGS = (
    "ipv4",
    "ipv6",
)


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
        isinstance(host, str) and host and host == host.strip() for host in ubuntu_hosts
    ):
        raise ConfigError("add_extra_repos.ubuntu_hosts must be non-empty strings")
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
    legacy_source_suffix = _nonempty_string_field(
        raw.get("legacy_source_suffix"),
        "add_extra_repos.legacy_source_suffix",
    )
    legacy_source_type_keywords = _string_list(
        raw.get("legacy_source_type_keywords"),
        "add_extra_repos.legacy_source_type_keywords",
    )
    source_url_schemes = _string_list(
        raw.get("source_url_schemes"),
        "add_extra_repos.source_url_schemes",
    )
    deb822_source_suffix = _nonempty_string_field(
        raw.get("deb822_source_suffix"),
        "add_extra_repos.deb822_source_suffix",
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
    uris_field_name = _nonempty_string_field(
        raw.get("uris_field_name"), "add_extra_repos.uris_field_name"
    )
    components_field_name = _nonempty_string_field(
        raw.get("components_field_name"), "add_extra_repos.components_field_name"
    )
    return AddExtraReposConfig(
        components=tuple(unique),
        ubuntu_hosts=tuple(ubuntu_hosts),
        uris_field_name=uris_field_name,
        components_field_name=components_field_name,
        keep_downloaded_debs=keep_downloaded_debs,
        legacy_sources_file=legacy_sources_file,
        sources_list_d=sources_list_d,
        legacy_source_suffix=legacy_source_suffix,
        legacy_source_type_keywords=legacy_source_type_keywords,
        source_url_schemes=source_url_schemes,
        deb822_source_suffix=deb822_source_suffix,
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
        username=_nonempty_string_field(raw.get("username"), "chrome_setup.username"),
        desktop_entry_exec_key=_nonempty_string_field(
            raw.get("desktop_entry_exec_key"),
            "chrome_setup.desktop_entry_exec_key",
        ),
        home_dir=_nonempty_string_field(raw.get("home_dir"), "chrome_setup.home_dir"),
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
        appletsrc_launcher_group=_string_list(
            raw.get("appletsrc_launcher_group"),
            "chrome_setup.appletsrc_launcher_group",
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
            _nonempty_string_field(raw.get("settings_dir"), "chrome_setup.settings_dir")
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
        keyring_armored_file_name=_nonempty_string_field(
            raw.get("keyring_armored_file_name"),
            "chrome_setup.keyring_armored_file_name",
        ),
        apt_source_template_file_name=_nonempty_string_field(
            raw.get("apt_source_template_file_name"),
            "chrome_setup.apt_source_template_file_name",
        ),
        launch_flags=_string_list(raw.get("launch_flags"), "chrome_setup.launch_flags"),
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
            _nonempty_string_field(raw.get("system_root"), "chrome_setup.system_root")
        ),
        apt_source_path=Path(
            _nonempty_string_field(
                raw.get("apt_source_path"), "chrome_setup.apt_source_path"
            )
        ),
        keyring_path=Path(
            _nonempty_string_field(raw.get("keyring_path"), "chrome_setup.keyring_path")
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
        file_mode=_octal_mode_field(raw.get("file_mode"), "chrome_setup.file_mode"),
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
        _nonempty_string_field(
            raw.get("service_unit_path"), "dnsproxy_setup.service_unit_path"
        )
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
    bootstrap_form_templates = _string_list(
        raw.get("bootstrap_form_templates"),
        "dnsproxy_setup.bootstrap_form_templates",
    )
    if not bootstrap_form_templates:
        raise ConfigError("dnsproxy_setup.bootstrap_form_templates is empty")
    for template in bootstrap_form_templates:
        if "{host}" not in template:
            raise ConfigError(
                "dnsproxy_setup.bootstrap_form_templates entries must contain {host}"
            )
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
    resolved_status_global_marker = _nonempty_string_field(
        raw.get("resolved_status_global_marker"),
        "dnsproxy_setup.resolved_status_global_marker",
    )
    resolved_status_link_prefix = _nonempty_string_field(
        raw.get("resolved_status_link_prefix"),
        "dnsproxy_setup.resolved_status_link_prefix",
    )
    resolved_status_dns_server_labels = _string_list(
        raw.get("resolved_status_dns_server_labels"),
        "dnsproxy_setup.resolved_status_dns_server_labels",
    )
    if not resolved_status_dns_server_labels:
        raise ConfigError(
            "dnsproxy_setup.resolved_status_dns_server_labels must not be empty"
        )
    resolved_status_dns_domain_label = _nonempty_string_field(
        raw.get("resolved_status_dns_domain_label"),
        "dnsproxy_setup.resolved_status_dns_domain_label",
    )
    resolved_stub_mode_line = _nonempty_string_field(
        raw.get("resolved_stub_mode_line"),
        "dnsproxy_setup.resolved_stub_mode_line",
    )
    resolved_wildcard_domain = _nonempty_string_field(
        raw.get("resolved_wildcard_domain"),
        "dnsproxy_setup.resolved_wildcard_domain",
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
        bootstrap_form_templates=bootstrap_form_templates,
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
        resolved_status_global_marker=resolved_status_global_marker,
        resolved_status_link_prefix=resolved_status_link_prefix,
        resolved_status_dns_server_labels=resolved_status_dns_server_labels,
        resolved_status_dns_domain_label=resolved_status_dns_domain_label,
        resolved_stub_mode_line=resolved_stub_mode_line,
        resolved_wildcard_domain=resolved_wildcard_domain,
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
        installed_version_command=_placeholder_command_field(
            raw.get("installed_version_command"),
            "dnsproxy_setup.installed_version_command",
            ("{binary}",),
        ),
        daemon_flag_templates=_string_map(
            raw.get("daemon_flag_templates"),
            "dnsproxy_setup.daemon_flag_templates",
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
        raise ConfigError("hostname.set_hostname_command must be non-empty strings")
    return HostnameConfig(
        hostname_file=hostname_file,
        hostname_random_bytes=hostname_random_bytes,
        set_hostname_command=tuple(part.strip() for part in command),
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
        python_script_command=_placeholder_command_field(
            raw.get("python_script_command"),
            "kde_keyboard_setup.python_script_command",
            ("{python}",),
        ),
    )


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


# from rustdesk_setup.py


def _rustdesk_options(raw: object) -> tuple[RustdeskOptionConfig, ...]:
    """Validate the [rustdesk_setup.options] array of tables.

    Every option is a table with a non-empty key and a value; a missing
    array means no options. The value may be empty, which is the value
    that clears the option: RustDesk writes an option that carries
    nothing as the empty string, and the task applies such a value like
    any other. Duplicate keys are a config error, so the task never
    applies the same option twice with different values.
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
        if not isinstance(value, str):
            raise ConfigError("[rustdesk_setup] option value must be a string")
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
        raise ConfigError("rustdesk_setup.vault_entry_title must be a non-empty string")
    service_unit_name = raw.get("service_unit_name")
    if not isinstance(service_unit_name, str) or not service_unit_name:
        raise ConfigError("rustdesk_setup.service_unit_name must be a non-empty string")
    config_dir = raw.get("config_dir")
    if not isinstance(config_dir, str):
        raise ConfigError("rustdesk_setup.config_dir must be a string")
    password_separator = raw.get("password_separator")
    if not isinstance(password_separator, str) or not password_separator:
        raise ConfigError(
            "rustdesk_setup.password_separator must be a non-empty string"
        )
    service_settle_delay_seconds = _float_field(
        raw.get("service_settle_delay_seconds"),
        "rustdesk_setup.service_settle_delay_seconds",
    )
    if service_settle_delay_seconds <= 0:
        raise ConfigError(
            "rustdesk_setup.service_settle_delay_seconds must be positive"
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
        service_settle_delay_seconds=service_settle_delay_seconds,
        options=_rustdesk_options(raw.get("options")),
    )


# from sotavpn_setup.py


def _sotavpn_setup_table(raw: object) -> SotavpnSetupConfig:
    """Validate the [sotavpn_setup] table and build SotavpnSetupConfig.

    The account and the bridge installation are checked like every other
    section: names, paths and templates are non-empty strings, command
    arrays are non-empty arrays of strings, the refresh interval, the
    budget for the panel fetch and the readiness budget are positive, and
    the pause between two readiness probes is not negative. The entry
    title of the account is cross-checked against the [vault_structure]
    table in strict_config_from_document.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[sotavpn_setup] section is missing or not a table")
    subscription_update_interval_seconds = _positive_int_field(
        raw.get("subscription_update_interval_seconds"),
        "sotavpn_setup.subscription_update_interval_seconds",
    )
    subscription_fetch_wait_seconds = _positive_int_field(
        raw.get("subscription_fetch_wait_seconds"),
        "sotavpn_setup.subscription_fetch_wait_seconds",
    )
    bridge_ready_wait_seconds = _positive_int_field(
        raw.get("bridge_ready_wait_seconds"),
        "sotavpn_setup.bridge_ready_wait_seconds",
    )
    readiness_check_delay_seconds = _int_field(
        raw.get("readiness_check_delay_seconds"),
        "sotavpn_setup.readiness_check_delay_seconds",
    )
    if readiness_check_delay_seconds < 0:
        raise ConfigError(
            "sotavpn_setup.readiness_check_delay_seconds must not be negative"
        )
    return SotavpnSetupConfig(
        username=_nonempty_string_field(raw.get("username"), "sotavpn_setup.username"),
        home_dir=_nonempty_string_field(raw.get("home_dir"), "sotavpn_setup.home_dir"),
        runuser_command=_string_list(
            raw.get("runuser_command"), "sotavpn_setup.runuser_command"
        ),
        archive_url=_nonempty_string_field(
            raw.get("archive_url"), "sotavpn_setup.archive_url"
        ),
        archive_temp_prefix=_nonempty_string_field(
            raw.get("archive_temp_prefix"), "sotavpn_setup.archive_temp_prefix"
        ),
        archive_temp_suffix=_nonempty_string_field(
            raw.get("archive_temp_suffix"), "sotavpn_setup.archive_temp_suffix"
        ),
        installer_file_name=_nonempty_string_field(
            raw.get("installer_file_name"), "sotavpn_setup.installer_file_name"
        ),
        installer_command=_string_list(
            raw.get("installer_command"), "sotavpn_setup.installer_command"
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "sotavpn_setup.service_unit_name"
        ),
        user_service_is_active_command=_string_list(
            raw.get("user_service_is_active_command"),
            "sotavpn_setup.user_service_is_active_command",
        ),
        user_install_relative_path=_nonempty_string_field(
            raw.get("user_install_relative_path"),
            "sotavpn_setup.user_install_relative_path",
        ),
        settings_file_name=_nonempty_string_field(
            raw.get("settings_file_name"), "sotavpn_setup.settings_file_name"
        ),
        settings_http_port_key=_nonempty_string_field(
            raw.get("settings_http_port_key"),
            "sotavpn_setup.settings_http_port_key",
        ),
        key_entry_title=_nonempty_string_field(
            raw.get("key_entry_title"), "sotavpn_setup.key_entry_title"
        ),
        subscription_url_template=_nonempty_string_field(
            raw.get("subscription_url_template"),
            "sotavpn_setup.subscription_url_template",
        ),
        subscription_remark=_nonempty_string_field(
            raw.get("subscription_remark"), "sotavpn_setup.subscription_remark"
        ),
        subscription_update_interval_seconds=subscription_update_interval_seconds,
        subscription_enabled=_bool_field(
            raw.get("subscription_enabled"), "sotavpn_setup.subscription_enabled"
        ),
        subscription_allow_private=_bool_field(
            raw.get("subscription_allow_private"),
            "sotavpn_setup.subscription_allow_private",
        ),
        subscription_allow_insecure=_bool_field(
            raw.get("subscription_allow_insecure"),
            "sotavpn_setup.subscription_allow_insecure",
        ),
        subscription_prepend=_bool_field(
            raw.get("subscription_prepend"), "sotavpn_setup.subscription_prepend"
        ),
        subscription_fetch_wait_seconds=subscription_fetch_wait_seconds,
        bridge_ready_wait_seconds=bridge_ready_wait_seconds,
        readiness_check_delay_seconds=readiness_check_delay_seconds,
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
    """Validate a text the run fills and demand the placeholders it uses.

    A text the run fills placeholders into, be it a line of a file it
    writes or the description of a rule it creates, is a config value, so a
    mistyped placeholder would raise on the target machine; the checks
    refuse it here instead.
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
        raise ConfigError("swapfile_service_install.ram_extra_mb must not be negative")
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
        meminfo_total_key=_nonempty_string_field(
            raw.get("meminfo_total_key"),
            "swapfile_service_install.meminfo_total_key",
        ),
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


# from scrcpy_setup.py


def _scrcpy_setup_table(raw: object) -> ScrcpySetupConfig:
    """Validate the [scrcpy_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[scrcpy_setup] section is missing or not a table")
    return ScrcpySetupConfig(
        username=_nonempty_string_field(raw.get("username"), "scrcpy_setup.username"),
        home_dir=_nonempty_string_field(raw.get("home_dir"), "scrcpy_setup.home_dir"),
        github_repo=_nonempty_string_field(
            raw.get("github_repo"), "scrcpy_setup.github_repo"
        ),
        archive_name_template=_placeholder_text_field(
            raw.get("archive_name_template"),
            "scrcpy_setup.archive_name_template",
            ("{asset_arch}", "{release_tag}"),
        ),
        checksum_file_name=_nonempty_string_field(
            raw.get("checksum_file_name"), "scrcpy_setup.checksum_file_name"
        ),
        fallback_packages=_string_list(
            raw.get("fallback_packages"), "scrcpy_setup.fallback_packages"
        ),
        udev_rules_package_name=_nonempty_string_field(
            raw.get("udev_rules_package_name"),
            "scrcpy_setup.udev_rules_package_name",
        ),
        apt_binary_path=Path(
            _nonempty_string_field(
                raw.get("apt_binary_path"), "scrcpy_setup.apt_binary_path"
            )
        ),
        theme_icon_name=_nonempty_string_field(
            raw.get("theme_icon_name"), "scrcpy_setup.theme_icon_name"
        ),
        download_dir=Path(
            _nonempty_string_field(raw.get("download_dir"), "scrcpy_setup.download_dir")
        ),
        install_dir_relative_path=_nonempty_string_field(
            raw.get("install_dir_relative_path"),
            "scrcpy_setup.install_dir_relative_path",
        ),
        command_relative_path=_nonempty_string_field(
            raw.get("command_relative_path"),
            "scrcpy_setup.command_relative_path",
        ),
        launcher_relative_path=_nonempty_string_field(
            raw.get("launcher_relative_path"),
            "scrcpy_setup.launcher_relative_path",
        ),
        console_launcher_relative_path=_nonempty_string_field(
            raw.get("console_launcher_relative_path"),
            "scrcpy_setup.console_launcher_relative_path",
        ),
        launcher_template_file_name=_nonempty_string_field(
            raw.get("launcher_template_file_name"),
            "scrcpy_setup.launcher_template_file_name",
        ),
        console_launcher_template_file_name=_nonempty_string_field(
            raw.get("console_launcher_template_file_name"),
            "scrcpy_setup.console_launcher_template_file_name",
        ),
        binary_file_name=_nonempty_string_field(
            raw.get("binary_file_name"), "scrcpy_setup.binary_file_name"
        ),
        server_file_name=_nonempty_string_field(
            raw.get("server_file_name"), "scrcpy_setup.server_file_name"
        ),
        adb_file_name=_nonempty_string_field(
            raw.get("adb_file_name"), "scrcpy_setup.adb_file_name"
        ),
        icon_file_name=_nonempty_string_field(
            raw.get("icon_file_name"), "scrcpy_setup.icon_file_name"
        ),
        extract_dir_prefix=_nonempty_string_field(
            raw.get("extract_dir_prefix"), "scrcpy_setup.extract_dir_prefix"
        ),
        trash_dir_relative_path=_nonempty_string_field(
            raw.get("trash_dir_relative_path"),
            "scrcpy_setup.trash_dir_relative_path",
        ),
        version_command=_placeholder_command_field(
            raw.get("version_command"),
            "scrcpy_setup.version_command",
            ("{binary}",),
        ),
        checksum_command=_placeholder_command_field(
            raw.get("checksum_command"),
            "scrcpy_setup.checksum_command",
            ("{file}",),
        ),
        archive_extract_command=_placeholder_command_field(
            raw.get("archive_extract_command"),
            "scrcpy_setup.archive_extract_command",
            ("{archive}", "{extract_dir}"),
        ),
        launcher_file_mode=_octal_mode_field(
            raw.get("launcher_file_mode"), "scrcpy_setup.launcher_file_mode"
        ),
        executable_file_mode=_octal_mode_field(
            raw.get("executable_file_mode"),
            "scrcpy_setup.executable_file_mode",
        ),
        package_status_timeout_seconds=_positive_int_field(
            raw.get("package_status_timeout_seconds"),
            "scrcpy_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "scrcpy_setup.package_install_retries",
        ),
    )


# from telegram_setup.py


def _telegram_setup_table(raw: object) -> TelegramSetupConfig:
    """Validate the [telegram_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[telegram_setup] section is missing or not a table")
    return TelegramSetupConfig(
        username=_nonempty_string_field(raw.get("username"), "telegram_setup.username"),
        home_dir=_nonempty_string_field(raw.get("home_dir"), "telegram_setup.home_dir"),
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
        reachability_probe_command=_placeholder_command_field(
            raw.get("reachability_probe_command"),
            "telegram_setup.reachability_probe_command",
            ("{timeout_seconds}",),
        ),
        reachability_probe_timeout_seconds=_positive_int_field(
            raw.get("reachability_probe_timeout_seconds"),
            "telegram_setup.reachability_probe_timeout_seconds",
        ),
        icon_url=_nonempty_string_field(raw.get("icon_url"), "telegram_setup.icon_url"),
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
        tar_extract_command=_placeholder_command_field(
            raw.get("tar_extract_command"),
            "telegram_setup.tar_extract_command",
            ("{archive}", "{extract_dir}"),
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
            name
            for name in entry_raw
            if name not in ("title", "notes", "generated_password")
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
        if generated_password is not None and not isinstance(generated_password, str):
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
                raise ConfigError(f"[vault_structure] duplicate group title: {title}")
            seen_titles.add(title)
            notes = group_raw.get("notes")
            if not isinstance(notes, str) or not notes:
                raise ConfigError(
                    f"[vault_structure] group {title}: notes must be a non-empty string"
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
        seed_entries.append(VaultGroupSeed(title=seed_title, url=url, notes=seed_notes))
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
        raise ConfigError("local_vault_setup.pass_file_path must be a non-empty string")
    vault_password_entry_title = raw.get("vault_password_entry_title")
    if (
        not isinstance(vault_password_entry_title, str)
        or not vault_password_entry_title
    ):
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
        raise ConfigError("local_vault_setup.error_priority must be between 0 and 7")
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
        version=_nonempty_string_field(raw.get("version"), "vocalinux_setup.version"),
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
        service_active_state=_nonempty_string_field(
            raw.get("service_active_state"),
            "vocalinux_setup.service_active_state",
        ),
        service_enable_command=_placeholder_command_field(
            raw.get("service_enable_command"),
            "vocalinux_setup.service_enable_command",
            ("{username}", "{service_unit_name}"),
        ),
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
    swap_priority = _int_field(raw.get("swap_priority"), "zram_service.swap_priority")
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
        raise ConfigError("zram_service.reset_busy_attempts must be at least 1")
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
        meminfo_total_key=_nonempty_string_field(
            raw.get("meminfo_total_key"), "zram_service.meminfo_total_key"
        ),
        cpuinfo_processor_key=_nonempty_string_field(
            raw.get("cpuinfo_processor_key"),
            "zram_service.cpuinfo_processor_key",
        ),
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
        raise ConfigError("zswap_service.max_pool_percent must be between 1 and 100")
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
        unit_exec_line_template=_placeholder_text_field(
            raw.get("unit_exec_line_template"),
            "zswap_service.unit_exec_line_template",
            ("{value}", "{path}"),
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
    if not any(
        entry.title == local_vault_setup.vault_password_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must name an entry "
            "of the [vault_structure] table"
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
    sotavpn_setup = _sotavpn_setup_table(document.get("sotavpn_setup"))
    if not any(
        entry.title == sotavpn_setup.key_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "sotavpn_setup.key_entry_title must name an entry of the "
            "[vault_structure] table"
        )
    config = Config(
        cli_tools=_cli_tools_table(document.get("cli_tools")),
        chrome_setup=_chrome_setup_table(document.get("chrome_setup")),
        dnsproxy_setup=_dnsproxy_setup_table(document.get("dnsproxy_setup")),
        add_extra_repos=_add_extra_repos_table(document.get("add_extra_repos")),
        hostname=_hostname_table(document.get("hostname")),
        ffmpeg_setup=_ffmpeg_setup_table(document.get("ffmpeg_setup")),
        imagemagick_setup=_imagemagick_setup_table(document.get("imagemagick_setup")),
        kde_keyboard_setup=_kde_keyboard_setup_table(
            document.get("kde_keyboard_setup")
        ),
        swapfile_service_install=_swapfile_service_install_table(
            document.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(document.get("zswap_service")),
        zram_service=_zram_service_table(document.get("zram_service")),
        telegram_setup=_telegram_setup_table(document.get("telegram_setup")),
        vocalinux_setup=_vocalinux_setup_table(document.get("vocalinux_setup")),
        nextdns_setup_system_wide=_nextdns_setup_system_wide_table(
            document.get("nextdns_setup_system_wide")
        ),
        playwright_setup=_playwright_setup_table(document.get("playwright_setup")),
        rustdesk_setup=rustdesk_setup,
        scrcpy_setup=_scrcpy_setup_table(document.get("scrcpy_setup")),
        sotavpn_setup=sotavpn_setup,
        vault_structure=vault_structure,
        local_vault_setup=local_vault_setup,
    )
    return config
