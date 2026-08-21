"""[nextdns_setup_system_wide] table parser.

The section carries the parameters of the system-wide NextDNS task: where
the vault group lives, the dnscrypt-proxy configuration path, the
endpoint formats for every encrypted protocol (DoH, DoT, DoQ) and the
verification endpoint. The task writes [static] entries into the
dnscrypt-proxy configuration and sets server_names to use them, so the
proxy tries every protocol and keeps the fastest. The fallback servers
already configured in dnscrypt-proxy (by the dnscrypt_setup task) answer
whenever NextDNS itself is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    ConfigError,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


@dataclass(frozen=True)
class NextdnsSetupSystemWideConfig:
    """System-wide NextDNS parameters for the nextdns_setup_system_wide task.

    vault_group_title names the vault subgroup that carries the NextDNS
    profile accounts (the [vault_structure] groups). The task edits the
    dnscrypt-proxy configuration at dnscrypt_config_path in place: it
    writes a [forwarding] section that routes every query through the
    NextDNS DoH endpoint for the chosen profile. profile_id_file_path
    and profile_id_file_mode are the path and mode of the file that
    records the applied profile ID for the System Metrics collector.
    doh_url_format is the DoH URL pattern with the {profile_id}
    placeholder. verification_url is the NextDNS verification endpoint,
    restart_proxy_command restarts dnscrypt-proxy so the configuration
    takes effect, verification_command queries the endpoint (with the
    {url} and {timeout} placeholders). error_priority is the syslog
    priority of a serious failure, command_timeout_seconds the ceiling
    of a verification command.
    """

    vault_group_title: str
    dnscrypt_config_path: Path
    profile_id_file_path: Path
    profile_id_file_mode: int
    doh_url_format: str
    verification_url: str
    restart_proxy_command: tuple[str, ...]
    verification_command: tuple[str, ...]
    error_priority: int
    command_timeout_seconds: int


def _nextdns_setup_system_wide_table(
    raw: object,
) -> NextdnsSetupSystemWideConfig:
    """Validate the [nextdns_setup_system_wide] table and build the config.

    Every value is required and typed: the vault group title, the
    dnscrypt-proxy config path, the profile ID file path, the endpoint
    format strings and the static name prefix are non-empty strings;
    profile_id_file_mode is an octal string; doh_url_format,
    dot_stamp_host_format and doq_stamp_host_format must carry the
    {profile_id} placeholder; verification_command must carry the {url}
    and {timeout} placeholders; restart_proxy_command and
    verification_command are non-empty arrays of non-empty strings;
    error_priority is an integer 0-7 and command_timeout_seconds is
    positive.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[nextdns_setup_system_wide] section is missing or not a table"
        )
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), "nextdns_setup_system_wide.vault_group_title"
    )
    dnscrypt_config_path = _nonempty_string_field(
        raw.get("dnscrypt_config_path"),
        "nextdns_setup_system_wide.dnscrypt_config_path",
    )
    profile_id_file_path = _nonempty_string_field(
        raw.get("profile_id_file_path"),
        "nextdns_setup_system_wide.profile_id_file_path",
    )
    profile_id_file_mode = _octal_mode_field(
        raw.get("profile_id_file_mode"),
        "nextdns_setup_system_wide.profile_id_file_mode",
    )
    doh_url_format = _nonempty_string_field(
        raw.get("doh_url_format"), "nextdns_setup_system_wide.doh_url_format"
    )
    if "{profile_id}" not in doh_url_format:
        raise ConfigError(
            "nextdns_setup_system_wide.doh_url_format must contain "
            "the {profile_id} placeholder"
        )
    verification_url = _nonempty_string_field(
        raw.get("verification_url"), "nextdns_setup_system_wide.verification_url"
    )

    def _string_list_field(name: str) -> tuple[str, ...]:
        """Validate a non-empty array of non-empty strings."""

        raw_value = raw.get(name)
        if not isinstance(raw_value, list) or not raw_value:
            raise ConfigError(
                f"nextdns_setup_system_wide.{name} must be a non-empty "
                "array of strings"
            )
        values: list[str] = []
        for item in raw_value:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    f"nextdns_setup_system_wide.{name} must be non-empty strings"
                )
            values.append(item.strip())
        return tuple(values)

    restart_proxy_command = _string_list_field("restart_proxy_command")
    verification_command = _string_list_field("verification_command")
    if "{url}" not in verification_command or "{timeout}" not in verification_command:
        raise ConfigError(
            "nextdns_setup_system_wide.verification_command must contain "
            "the {url} and {timeout} placeholders"
        )
    error_priority = _int_field(
        raw.get("error_priority"), "nextdns_setup_system_wide.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "nextdns_setup_system_wide.error_priority must be between 0 and 7"
        )
    command_timeout_seconds = _int_field(
        raw.get("command_timeout_seconds"),
        "nextdns_setup_system_wide.command_timeout_seconds",
    )
    if command_timeout_seconds <= 0:
        raise ConfigError(
            "nextdns_setup_system_wide.command_timeout_seconds must be positive"
        )
    return NextdnsSetupSystemWideConfig(
        vault_group_title=vault_group_title,
        dnscrypt_config_path=Path(dnscrypt_config_path),
        profile_id_file_path=Path(profile_id_file_path),
        profile_id_file_mode=profile_id_file_mode,
        doh_url_format=doh_url_format,
        verification_url=verification_url,
        restart_proxy_command=restart_proxy_command,
        verification_command=verification_command,
        error_priority=error_priority,
        command_timeout_seconds=command_timeout_seconds,
    )
