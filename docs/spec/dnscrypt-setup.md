# dnscrypt-proxy system-wide resolver

There is a dedicated dnscrypt-proxy installation task: dnscrypt_setup.

The task installs dnscrypt-proxy from the Ubuntu archive and runs it as a system service. The machine resolves every DNS query through the proxy: systemd-resolved is pointed at the local address of the proxy, and the proxy resolves through its encrypted servers with a large set of plain DNS fallback servers, so the machine never loses resolution.

## Installation and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos), never from GitHub releases: dnscrypt-proxy is packaged for Ubuntu and receives upstream security updates through the regular apt upgrade, so the running version stays patched without a dedicated update path. The apt index is not refreshed by the task: add_extra_repos refreshed it earlier in the same run, because the task depends on it.

## Listening socket

The Ubuntu package uses systemd socket activation: the socket unit owns the listening socket and passes it to the service, which runs as the unprivileged _dnscrypt-proxy user. The task keeps this mechanism and only changes the listen address through a systemd drop-in in the configured socket_dropin_dir (the file socket_dropin_file_name, the section socket_section and the ownership comment socket_dropin_header). The drop-in resets the package ListenStream and ListenDatagram (an empty value clears the list inherited from the main unit) and sets both to the configured listen_address, so the socket listens only on that address and never on the package default 127.0.2.1:53. The default listen_address is 0.0.0.0:53053: all interfaces, so machines on the network can use the proxy too, and a non-standard port, so no privileged binding is needed and the unprivileged service can own the socket. The drop-in is merged through the shared sync_directives_by_key helper (config_edit.py), which replaces the managed directives by their key and preserves every foreign line.

## Configuration ownership

The task never rewrites the package configuration file at the configured config_path wholesale: it only guarantees the fallback_resolvers line in the root table through the shared sync_toml_root_directive helper (config_edit.py). The helper replaces an existing fallback_resolvers line or inserts the line after the server_names anchor, so it stays in the root table and never lands inside a later [section] of the file; every other line and section of the package file survives. The resolvers are rendered as a single-line TOML array of quoted strings, the form dnscrypt-proxy parses. A missing configuration file is an error: the package ships it, and the task must not fabricate a proxy configuration from scratch.

The proxy resolves through the encrypted servers of its sources (the package ships the public-resolvers source) and falls back to fallback_resolvers, the configured plain DNS servers, whenever the encrypted servers are unreachable. The fallback set covers many independent providers with IPv4 and IPv6 anycast addresses, so a single provider outage never leaves the machine without resolution.

## Pointing the system at the proxy

The task points systemd-resolved at the local address of the proxy through a drop-in in the configured resolved_conf_dir (the file dropin_file_name, the section resolve_section and the ownership comment dropin_header). The DNS directive (dns_directive) names the local address of the proxy and the Domains directive (domains_directive, ~.) routes every query through the global resolver. The drop-in is merged through the shared sync_directives_by_key helper: the managed directives (the keys of directive_keys) are replaced by their key, every other line survives. When manage_networkmanager is set, the task tells NetworkManager to ignore DHCP-issued DNS on every connection (ipv4.ignore-auto-dns and ipv6.ignore-auto-dns), because per-link DNS would otherwise shadow the global proxy; the task checks that nmcli exists before touching NetworkManager.

## Service lifecycle

The service and socket units come from the package; the task never renders or writes them, only the socket drop-in. The task reloads systemd (daemon_reload_command) so the socket drop-in takes effect, then enables and starts the socket and the service when they are not already enabled and active. After a start the task waits for the service to report active, repeating the is-active check up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the attempts, because the socket-activated service may take a moment to come up. A service that stays inactive after the loop is a task error.

## Verification

The task verifies that the machine really resolves through the proxy: the service must be active and a real DNS query through the local resolver (verification_command, a resolvectl query) must succeed. On a failed verification the task reports the error and leaves the system as is: reverting to the previous resolver configuration would not restore working DNS, because the previous configuration is exactly what broke. The task is idempotent: it skips when the package is installed, the socket drop-in matches, the proxy configuration carries the fallback resolvers, the service is enabled and active, the resolved drop-in matches and the verification passes; force mode rewrites the drop-ins and restarts the service but never reinstalls a matching package.

## Parameters

All parameters live in the config/ directory under [dnscrypt_setup]:

package_name is the package that provides the proxy
config_path is the main configuration file the package ships, edited in place
service_unit_name and socket_unit_name are the systemd units of the package
socket_dropin_dir, socket_dropin_file_name and socket_dropin_file_mode are the directory, file name and mode of the systemd drop-in that changes the socket listen address
socket_section and socket_dropin_header are the section header and the ownership comment of the socket drop-in
listen_address is the address the proxy listens on (0.0.0.0:53053 by default)
fallback_resolvers are the plain DNS servers the proxy uses when its encrypted servers are unreachable
resolved_conf_dir, dropin_file_name and dropin_file_mode are the directory, file name and mode of the resolved drop-in
resolve_section and dropin_header are the section header and the ownership comment of the resolved drop-in
dns_directive is the DNS line that names the local proxy address
domains_directive is the Domains value that routes every query through the global resolver
directive_keys are the resolved drop-in directive keys the task owns
manage_networkmanager tells the task to clear per-link DNS in NetworkManager
nmcli_check_command, nmcli_list_command and nmcli_modify_command are the NetworkManager commands
daemon_reload_command reloads systemd so the socket drop-in takes effect
restart_resolved_command restarts systemd-resolved
resolvectl_status_command queries the resolver state
verification_command queries a domain through the proxy
install_retries is the retry count of the package install
start_check_attempts and start_check_retry_delay_seconds bound the loop that waits for the service to become active

The syslog priority of a serious failure and the command timeout are not configured here: they are engine-wide values read from the [engine] table (engine.error_priority and engine.command_timeout_seconds), so the task never duplicates them.
