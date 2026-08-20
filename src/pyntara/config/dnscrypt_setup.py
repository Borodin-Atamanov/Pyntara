"""[dnscrypt_setup] table parser.

The section carries the parameters of the system-wide dnscrypt-proxy
task: the package and unit names, the socket drop-in that changes the
listen address, the fallback resolvers, the resolved.conf drop-in and
the commands the task runs. The syslog priority and the command timeout
are not configured here: they are engine-wide values read from the
[engine] table (engine.error_priority and engine.command_timeout_seconds),
so the task never duplicates them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field, _nonempty_string_field, _octal_mode_field


@dataclass(frozen=True)
class DnscryptSetupConfig:
    """System-wide dnscrypt-proxy parameters for the dnscrypt_setup task.

    package_name is the Ubuntu archive package; config_path is the main
    configuration file the package ships and the task edits in place.
    service_unit_name and socket_unit_name are the systemd units of the
    package; the task changes the socket listen address through a drop-in
    in socket_dropin_dir with the name socket_dropin_file_name and the
    mode socket_dropin_file_mode, whose section is socket_section and
    ownership comment socket_dropin_header. listen_address is the address
    the proxy listens on (all interfaces, so machines on the network can
    use it too). fallback_resolvers are the plain DNS servers the proxy
    uses when its encrypted servers are unreachable. The task points
    systemd-resolved at the proxy through a drop-in in resolved_conf_dir
    with the name dropin_file_name and the mode dropin_file_mode;
    resolve_section and dropin_header are the section header and the
    ownership comment, dns_directive the DNS line that names the local
    proxy address, domains_directive the Domains value that routes every
    query through the global resolver and directive_keys the directive
    keys the task owns. manage_networkmanager tells the task to clear
    per-link DNS in NetworkManager so the global proxy is actually used.
    nmcli_check_command, nmcli_list_command and nmcli_modify_command are
    the NetworkManager commands (the modify command carries the
    {connection} and {value} placeholders), daemon_reload_command reloads
    systemd so the socket drop-in takes effect, restart_resolved_command
    restarts the resolver, resolvectl_status_command queries the resolver
    state and verification_command queries a domain through the proxy.
    install_retries is the retry count
    of the package install; start_check_attempts and
    start_check_retry_delay_seconds bound the loop that waits for the
    service to become active.
    """

    package_name: str
    config_path: Path
    service_unit_name: str
    socket_unit_name: str
    socket_dropin_dir: Path
    socket_dropin_file_name: str
    socket_dropin_file_mode: int
    socket_section: str
    socket_dropin_header: str
    listen_address: str
    fallback_resolvers: tuple[str, ...]
    resolved_conf_dir: Path
    dropin_file_name: str
    dropin_file_mode: int
    resolve_section: str
    dropin_header: str
    dns_directive: str
    domains_directive: str
    directive_keys: tuple[str, ...]
    manage_networkmanager: bool
    nmcli_check_command: tuple[str, ...]
    nmcli_list_command: tuple[str, ...]
    nmcli_modify_command: tuple[str, ...]
    daemon_reload_command: tuple[str, ...]
    restart_resolved_command: tuple[str, ...]
    resolvectl_status_command: tuple[str, ...]
    verification_command: tuple[str, ...]
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float


def _dnscrypt_setup_table(raw: object) -> DnscryptSetupConfig:
    """Validate the [dnscrypt_setup] table and build the config.

    Every value is required and typed: the package name, the config path,
    the unit names, the socket drop-in directory, file name, section and
    header, the listen address, the resolved drop-in directory, file name,
    section and header, the DNS and Domains directives and the ownership
    comment are non-empty strings; the drop-in modes are octal strings;
    fallback_resolvers, directive_keys and every command array are
    non-empty arrays of non-empty strings; nmcli_modify_command must carry
    the {connection} and {value} placeholders; manage_networkmanager is a
    boolean;
    install_retries and start_check_attempts are positive integers and
    start_check_retry_delay_seconds is positive.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[dnscrypt_setup] section is missing or not a table")
    package_name = _nonempty_string_field(
        raw.get("package_name"), "dnscrypt_setup.package_name"
    )
    config_path = Path(
        _nonempty_string_field(raw.get("config_path"), "dnscrypt_setup.config_path")
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "dnscrypt_setup.service_unit_name"
    )
    socket_unit_name = _nonempty_string_field(
        raw.get("socket_unit_name"), "dnscrypt_setup.socket_unit_name"
    )
    socket_dropin_dir = Path(
        _nonempty_string_field(
            raw.get("socket_dropin_dir"), "dnscrypt_setup.socket_dropin_dir"
        )
    )
    socket_dropin_file_name = _nonempty_string_field(
        raw.get("socket_dropin_file_name"),
        "dnscrypt_setup.socket_dropin_file_name",
    )
    socket_dropin_file_mode = _octal_mode_field(
        raw.get("socket_dropin_file_mode"),
        "dnscrypt_setup.socket_dropin_file_mode",
    )
    socket_section = _nonempty_string_field(
        raw.get("socket_section"), "dnscrypt_setup.socket_section"
    )
    socket_dropin_header = _nonempty_string_field(
        raw.get("socket_dropin_header"), "dnscrypt_setup.socket_dropin_header"
    )
    listen_address = _nonempty_string_field(
        raw.get("listen_address"), "dnscrypt_setup.listen_address"
    )

    def _string_list_field(name: str) -> tuple[str, ...]:
        """Validate a non-empty array of non-empty strings."""

        raw_value = raw.get(name)
        if not isinstance(raw_value, list) or not raw_value:
            raise ConfigError(
                f"dnscrypt_setup.{name} must be a non-empty array of strings"
            )
        values: list[str] = []
        for item in raw_value:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    f"dnscrypt_setup.{name} must be non-empty strings"
                )
            values.append(item.strip())
        return tuple(values)

    fallback_resolvers = _string_list_field("fallback_resolvers")
    resolved_conf_dir = Path(
        _nonempty_string_field(
            raw.get("resolved_conf_dir"), "dnscrypt_setup.resolved_conf_dir"
        )
    )
    dropin_file_name = _nonempty_string_field(
        raw.get("dropin_file_name"), "dnscrypt_setup.dropin_file_name"
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "dnscrypt_setup.dropin_file_mode"
    )
    resolve_section = _nonempty_string_field(
        raw.get("resolve_section"), "dnscrypt_setup.resolve_section"
    )
    dropin_header = _nonempty_string_field(
        raw.get("dropin_header"), "dnscrypt_setup.dropin_header"
    )
    dns_directive = _nonempty_string_field(
        raw.get("dns_directive"), "dnscrypt_setup.dns_directive"
    )
    domains_directive = _nonempty_string_field(
        raw.get("domains_directive"), "dnscrypt_setup.domains_directive"
    )
    directive_keys = _string_list_field("directive_keys")
    manage_networkmanager = raw.get("manage_networkmanager")
    if not isinstance(manage_networkmanager, bool):
        raise ConfigError("dnscrypt_setup.manage_networkmanager must be a boolean")
    nmcli_check_command = _string_list_field("nmcli_check_command")
    nmcli_list_command = _string_list_field("nmcli_list_command")
    nmcli_modify_command = _string_list_field("nmcli_modify_command")
    if "{connection}" not in nmcli_modify_command or "{value}" not in nmcli_modify_command:
        raise ConfigError(
            "dnscrypt_setup.nmcli_modify_command must contain the "
            "{connection} and {value} placeholders"
        )
    daemon_reload_command = _string_list_field("daemon_reload_command")
    restart_resolved_command = _string_list_field("restart_resolved_command")
    resolvectl_status_command = _string_list_field("resolvectl_status_command")
    verification_command = _string_list_field("verification_command")
    install_retries = _int_field(
        raw.get("install_retries"), "dnscrypt_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("dnscrypt_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "dnscrypt_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError("dnscrypt_setup.start_check_attempts must be positive")
    start_check_retry_delay_seconds = raw.get("start_check_retry_delay_seconds")
    if (
        isinstance(start_check_retry_delay_seconds, bool)
        or not isinstance(start_check_retry_delay_seconds, (int, float))
        or start_check_retry_delay_seconds < 0
    ):
        raise ConfigError(
            "dnscrypt_setup.start_check_retry_delay_seconds must be a "
            "non-negative number"
        )
    return DnscryptSetupConfig(
        package_name=package_name,
        config_path=config_path,
        service_unit_name=service_unit_name,
        socket_unit_name=socket_unit_name,
        socket_dropin_dir=socket_dropin_dir,
        socket_dropin_file_name=socket_dropin_file_name,
        socket_dropin_file_mode=socket_dropin_file_mode,
        socket_section=socket_section,
        socket_dropin_header=socket_dropin_header,
        listen_address=listen_address,
        fallback_resolvers=fallback_resolvers,
        resolved_conf_dir=resolved_conf_dir,
        dropin_file_name=dropin_file_name,
        dropin_file_mode=dropin_file_mode,
        resolve_section=resolve_section,
        dropin_header=dropin_header,
        dns_directive=dns_directive,
        domains_directive=domains_directive,
        directive_keys=directive_keys,
        manage_networkmanager=manage_networkmanager,
        nmcli_check_command=nmcli_check_command,
        nmcli_list_command=nmcli_list_command,
        nmcli_modify_command=nmcli_modify_command,
        daemon_reload_command=daemon_reload_command,
        restart_resolved_command=restart_resolved_command,
        resolvectl_status_command=resolvectl_status_command,
        verification_command=verification_command,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=float(start_check_retry_delay_seconds),
    )
