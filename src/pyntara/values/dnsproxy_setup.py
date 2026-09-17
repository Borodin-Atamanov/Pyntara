"""Values of the dnsproxy_setup task.

The task installs the dnsproxy binary from its GitHub release and runs it as the
system-wide resolver of the machine: encrypted upstreams built from the NextDNS
profile ID that nextdns_setup_system_wide recorded, a systemd-resolved drop-in
that points the stub resolver at the local listener, and the NetworkManager
connections whose automatic DNS is switched off for the cutover
(docs/spec/dnsproxy-setup.md).

The path and the mode of the recorded profile ID file come from the shared
module, because two sections use them. The mode of the resolved drop-in and the
mode of the staged binary are values of this section and not the shared file
modes of the deployed desktop files: they carry the same numbers and a different
meaning, which is the reason the zram compressor word is not shared either.
"""

from __future__ import annotations

from pathlib import Path

# Repository and release asset of the resolver. The asset name template carries
# {asset_arch} and {release_tag}; the tag keeps its leading v, because the
# published asset name carries it. dnsproxy names its own architectures, so the
# table belongs to this section and not to the engine; an architecture the table
# does not name is an error and never a fallback to another one.
GITHUB_REPO: str = "AdguardTeam/dnsproxy"
ASSET_NAME_TEMPLATE: str = "dnsproxy-linux-{asset_arch}-{release_tag}.tar.gz"
ASSET_ARCHITECTURE_NAMES: dict[str, str] = {
    "amd64": "amd64",
    "arm64": "arm64",
    "armhf": "arm7",
}

# Name of the binary inside the archive and of the staged copy next to the
# archive; the registered name is BINARY_PATH.
BINARY_FILE_NAME: str = "dnsproxy"
STAGED_BINARY_FILE_NAME: str = "dnsproxy.staged"

# Name of the directory the archive is unpacked into inside DOWNLOAD_DIR.
EXTRACT_DIR_NAME: str = "extract"

# Root cache the release archive is downloaded into, and the path the binary is
# registered under.
DOWNLOAD_DIR: Path = Path("/var/lib/pyntara/dnsproxy-download")
BINARY_PATH: Path = Path("/usr/local/bin/dnsproxy")

# Name of the service unit, its path under the systemd unit directory and the
# template under task_data/ of the clone it is rendered from.
SERVICE_UNIT_NAME: str = "dnsproxy.service"
SERVICE_UNIT_PATH: Path = Path("/etc/systemd/system/dnsproxy.service")
SERVICE_TEMPLATE_PATH: str = "task_data/dnsproxy_setup/dnsproxy.service"

# Addresses and port the listener binds. The port is set to the permanent IANA
# DNS over TLS port, so the machine talks to the internet over DNS-over-TLS by
# default and dnsproxy carries every application over it.
LISTEN_ADDRESSES: tuple[str, ...] = ("0.0.0.0", "::")
LISTEN_PORT: int = 53053

# Address templates of the encrypted upstreams, whose {profile_id} is the ID of
# the NextDNS profile. The last one, DoH, is what strict networks accept, while
# DoT is the fastest of the three.
DOH_URL_FORMAT: str = "https://dns.nextdns.io/{profile_id}"
DOT_HOST_FORMAT: str = "tls://{profile_id}.dns.nextdns.io"
DOQ_HOST_FORMAT: str = "quic://{profile_id}.dns.nextdns.io"

# Protocol forms of every bootstrap and fallback address, one entry per form,
# with {host} replaced by the address (an IPv6 address in square brackets). The
# order is the order of the generated arguments.
BOOTSTRAP_FORM_TEMPLATES: tuple[str, ...] = (
    "{host}",
    "tls://{host}:853",
    "https://{host}:443/dns-query",
    "quic://{host}:853",
)

# Upstream selection of the daemon and its cache.
UPSTREAM_MODE: str = "load_balance"
CACHE_ENABLED: int = 1

# dnsproxy cache size in bytes, passed as --cache-size. 16 MiB.
CACHE_SIZE_BYTES: int = 16777216

# When 1, the task reads the current network provider DNS (resolvectl and nmcli)
# and appends every discovered address to the end of both the bootstrap and the
# fallback resolver groups in plain UDP port 53 form only. An empty discovery
# leaves the configured pool unchanged.
APPEND_PROVIDER_DNS: int = 1

# Bootstrap resolvers dnsproxy uses to resolve the hostnames of the encrypted
# upstream servers before contacting them. The list holds the documented anchor
# addresses (IPv4 and IPv6) of each provider, bare, without protocol forms. Each
# address is reachable over several protocols; the access form is chosen by
# scheme and port, not by address: plain DNS IP:53, DNS-over-TLS tls://IP on
# port 853, DNS-over-HTTPS https://IP/dns-query on port 443, DNS-over-QUIC
# quic://IP on port 853, DNSCrypt via its server stamp. dnsproxy tries all
# bootstrap entries in parallel and uses the first one that answers, so a
# blocked protocol or port does not stop resolution. The list is a curated
# subset of globally reachable anycast resolvers with broad protocol support,
# independent providers, and both address families.
BOOTSTRAP_RESOLVERS: tuple[str, ...] = (
    "1.1.1.1",
    "2606:4700:4700::1111",
    "8.8.8.8",
    "2001:4860:4860::8888",
    "9.9.9.9",
    "2620:fe::fe",
    "94.140.14.14",
    "2a10:50c0::ad1:ff",
    "45.90.28.0",
    "2a07:a8c0::",
    "208.67.222.222",
    "185.222.222.222",
    "2a09::",
    "185.228.168.9",
    "76.76.2.0",
    "194.242.2.2",
    "77.88.8.8",
    "2a02:6b8::feed:0ff",
)

# Outbound DNS query timeout in seconds, passed as --timeout.
TIMEOUT_SECONDS: int = 55

# Journal log rate limit of the service in messages per interval, rendered into
# the unit as LogRateLimitIntervalSec and LogRateLimitBurst.
LOG_RATE_LIMIT_INTERVAL_SECONDS: int = 3777
LOG_RATE_LIMIT_BURST: int = 7777

# Seconds the service is given to come back after a restart, the attempts of the
# readiness check and the pause between two attempts. The section used to carry
# an install_retries value that no code ever read; the migration drops it rather
# than freezing a dead machine setting.
SERVICE_RESTART_SECONDS: float = 7.0
START_CHECK_ATTEMPTS: int = 5
START_CHECK_RETRY_DELAY_SECONDS: float = 3.0

# The systemd-resolved drop-in that points the stub resolver at the local
# listener: its directory, file name, mode and header, the section the
# directives are written into, the DNS and routing directives themselves. The
# wildcard domain sends every name to the local listener.
RESOLVED_CONF_DIR: Path = Path("/etc/systemd/resolved.conf.d")
RESOLVED_DROPIN_FILE_NAME: str = "pyntara-dnsproxy.conf"
RESOLVED_DROPIN_FILE_MODE: int = 0o644
RESOLVED_DROPIN_HEADER: str = "# Managed by the Pyntara dnsproxy_setup task."
RESOLVED_SECTION: str = "[Resolve]"
RESOLVED_DNS_DIRECTIVES: tuple[str, ...] = (
    "DNS=127.0.0.1:53053",
    "DNS=[::1]:53053",
)
RESOLVED_DOMAINS_DIRECTIVE: str = "Domains=~."

# File mode of the downloaded binary before it is moved into place, octal; the
# staged copy must be executable for the version probe.
STAGED_BINARY_FILE_MODE: int = 0o755

# When 1, the task switches the automatic DNS of every active NetworkManager
# connection off for the cutover and puts it back when the verification fails.
MANAGE_NETWORKMANAGER: int = 1

# Vocabulary of the nmcli calls: the availability probe, the device status
# listing, the active connection listing, the state query of one connection, the
# modify call that ignores or accepts automatic DNS, the reapply call of one
# device, and the query of the DNS servers a device holds.
NMCLI_CHECK_COMMAND: tuple[str, ...] = ("nmcli", "--version")
NMCLI_DEVICE_STATUS_COMMAND: tuple[str, ...] = (
    "nmcli",
    "-t",
    "-f",
    "DEVICE,TYPE",
    "device",
    "status",
)
NMCLI_ACTIVE_LIST_COMMAND: tuple[str, ...] = (
    "nmcli",
    "-t",
    "-f",
    "NAME,UUID,DEVICE",
    "connection",
    "show",
    "--active",
)
NMCLI_DNS_STATE_COMMAND: tuple[str, ...] = (
    "nmcli",
    "-t",
    "-f",
    "ipv4.ignore-auto-dns,ipv6.ignore-auto-dns",
    "connection",
    "show",
    "{connection}",
)
NMCLI_MODIFY_COMMAND: tuple[str, ...] = (
    "nmcli",
    "connection",
    "modify",
    "{connection}",
    "ipv4.ignore-auto-dns",
    "{value}",
    "ipv6.ignore-auto-dns",
    "{value}",
)
NMCLI_REAPPLY_COMMAND: tuple[str, ...] = (
    "nmcli",
    "device",
    "reapply",
    "{device}",
)
NMCLI_DNS_COMMAND: tuple[str, ...] = (
    "nmcli",
    "-t",
    "-f",
    "IP4.DNS,IP6.DNS",
    "device",
    "show",
)

# Commands of the resolver cutover: reload the unit files and restart the stub
# resolver, then read its state.
DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
RESTART_RESOLVED_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "systemd-resolved",
)
RESOLVECTL_STATUS_COMMAND: tuple[str, ...] = ("resolvectl", "status")
RESOLVECTL_DNS_COMMAND: tuple[str, ...] = ("resolvectl", "dns")

# Vocabulary of the resolvectl output the task parses: the marker that opens the
# global block, the prefix of a per-link line, the labels of the DNS server and
# routing domain lines, the line that proves the stub resolver mode, and the
# routing domain that covers every name. A resolved release that renames one of
# them is answered here.
RESOLVED_STATUS_GLOBAL_MARKER: str = "Global"
RESOLVED_STATUS_LINK_PREFIX: str = "Link "
RESOLVED_STATUS_DNS_SERVER_LABELS: tuple[str, ...] = (
    "Current DNS Server",
    "DNS Servers",
)
RESOLVED_STATUS_DNS_DOMAIN_LABEL: str = "DNS Domain"
RESOLVED_STUB_MODE_LINE: str = "resolv.conf mode: stub"
RESOLVED_WILDCARD_DOMAIN: str = "~."

# The verification query the task runs after the cutover, and the domain it asks
# for.
VERIFICATION_DOMAIN: str = "example.com"
VERIFICATION_COMMAND: tuple[str, ...] = (
    "resolvectl",
    "query",
    "--cache=no",
    "{domain}",
)

# Address the direct DNS probe sends its query to: the local listener over the
# loopback interface, before the resolver cutover, and the number of random
# bytes of the probe query identifier.
PROBE_ADDRESS: str = "127.0.0.1"
PROBE_IDENT_BYTES: int = 2

# Device type NetworkManager reports for a tunnel device in the device status
# listing; such devices carry no DHCP-provided DNS. The loopback connection is
# skipped by the automatic DNS sweep.
TUN_DEVICE_TYPE: str = "tun"
LOOPBACK_CONNECTION_NAME: str = "lo"

# Value the state query reports when a connection already ignores automatic DNS,
# and the values written to make it ignore or accept automatic DNS again.
NMCLI_AUTO_DNS_IGNORED_VALUE: str = "yes"
NMCLI_IGNORE_AUTO_DNS_VALUE: str = "true"
NMCLI_RESTORE_AUTO_DNS_VALUE: str = "false"

# Command that reads the version of an installed dnsproxy binary, with the path
# of the binary as its placeholder.
INSTALLED_VERSION_COMMAND: tuple[str, ...] = ("{binary}", "--version")

# Flags of the dnsproxy daemon, one template per flag: {value} carries the value
# a flag takes, and a flag that takes no value is written as itself. The task
# assembles the ExecStart line of the unit from them, so a daemon that renames a
# flag is answered here.
DAEMON_FLAG_TEMPLATES: dict[str, str] = {
    "port": "--port={value}",
    "listen": "--listen={value}",
    "upstream": "--upstream={value}",
    "fallback": "--fallback={value}",
    "upstream_mode": "--upstream-mode={value}",
    "timeout": "--timeout={value}s",
    "cache": "--cache",
    "cache_size": "--cache-size={value}",
    "bootstrap": "--bootstrap={value}",
}

# The four commands that stop, enable, start and restart the service unit;
# {service_unit_name} is the unit of this section.
SERVICE_STOP_COMMAND: tuple[str, ...] = (
    "systemctl",
    "stop",
    "{service_unit_name}",
)
SERVICE_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SERVICE_START_COMMAND: tuple[str, ...] = (
    "systemctl",
    "start",
    "{service_unit_name}",
)
SERVICE_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "{service_unit_name}",
)

# Characters of the failed verification output shown in the error text: a long
# resolver error is cut to this length, an empty output becomes the placeholder
# <no output>.
VERIFICATION_ERROR_EXCERPT_LENGTH: int = 200

# Commands that list the listening TCP and UDP sockets and that signal a
# process, used to free the port of a previous resolver.
SS_TCP_LISTEN_COMMAND: tuple[str, ...] = ("ss", "-lntp")
SS_UDP_LISTEN_COMMAND: tuple[str, ...] = ("ss", "-lunp")
KILL_COMMAND: tuple[str, ...] = ("kill",)

# Command that reads the journal of the unit and the length of the excerpt kept
# for the diagnosis of a failed start: the last lines carry the reason the unit
# stopped.
SERVICE_LOG_COMMAND: tuple[str, ...] = (
    "journalctl",
    "-u",
    "{unit}",
    "--no-pager",
    "-n",
    "20",
)
SERVICE_LOG_EXCERPT_LENGTH: int = 400

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "ASSET_NAME_TEMPLATE",
    "ASSET_ARCHITECTURE_NAMES",
    "BINARY_FILE_NAME",
    "STAGED_BINARY_FILE_NAME",
    "EXTRACT_DIR_NAME",
    "DOWNLOAD_DIR",
    "BINARY_PATH",
    "SERVICE_UNIT_NAME",
    "SERVICE_UNIT_PATH",
    "SERVICE_TEMPLATE_PATH",
    "LISTEN_ADDRESSES",
    "LISTEN_PORT",
    "DOH_URL_FORMAT",
    "DOT_HOST_FORMAT",
    "DOQ_HOST_FORMAT",
    "BOOTSTRAP_FORM_TEMPLATES",
    "UPSTREAM_MODE",
    "CACHE_ENABLED",
    "CACHE_SIZE_BYTES",
    "APPEND_PROVIDER_DNS",
    "BOOTSTRAP_RESOLVERS",
    "TIMEOUT_SECONDS",
    "LOG_RATE_LIMIT_INTERVAL_SECONDS",
    "LOG_RATE_LIMIT_BURST",
    "SERVICE_RESTART_SECONDS",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "RESOLVED_CONF_DIR",
    "RESOLVED_DROPIN_FILE_NAME",
    "RESOLVED_DROPIN_FILE_MODE",
    "RESOLVED_DROPIN_HEADER",
    "RESOLVED_SECTION",
    "RESOLVED_DNS_DIRECTIVES",
    "RESOLVED_DOMAINS_DIRECTIVE",
    "STAGED_BINARY_FILE_MODE",
    "MANAGE_NETWORKMANAGER",
    "NMCLI_CHECK_COMMAND",
    "NMCLI_DEVICE_STATUS_COMMAND",
    "NMCLI_ACTIVE_LIST_COMMAND",
    "NMCLI_DNS_STATE_COMMAND",
    "NMCLI_MODIFY_COMMAND",
    "NMCLI_REAPPLY_COMMAND",
    "NMCLI_DNS_COMMAND",
    "DAEMON_RELOAD_COMMAND",
    "RESTART_RESOLVED_COMMAND",
    "RESOLVECTL_STATUS_COMMAND",
    "RESOLVECTL_DNS_COMMAND",
    "RESOLVED_STATUS_GLOBAL_MARKER",
    "RESOLVED_STATUS_LINK_PREFIX",
    "RESOLVED_STATUS_DNS_SERVER_LABELS",
    "RESOLVED_STATUS_DNS_DOMAIN_LABEL",
    "RESOLVED_STUB_MODE_LINE",
    "RESOLVED_WILDCARD_DOMAIN",
    "VERIFICATION_DOMAIN",
    "VERIFICATION_COMMAND",
    "PROBE_ADDRESS",
    "PROBE_IDENT_BYTES",
    "TUN_DEVICE_TYPE",
    "LOOPBACK_CONNECTION_NAME",
    "NMCLI_AUTO_DNS_IGNORED_VALUE",
    "NMCLI_IGNORE_AUTO_DNS_VALUE",
    "NMCLI_RESTORE_AUTO_DNS_VALUE",
    "INSTALLED_VERSION_COMMAND",
    "DAEMON_FLAG_TEMPLATES",
    "SERVICE_STOP_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "SERVICE_START_COMMAND",
    "SERVICE_RESTART_COMMAND",
    "VERIFICATION_ERROR_EXCERPT_LENGTH",
    "SS_TCP_LISTEN_COMMAND",
    "SS_UDP_LISTEN_COMMAND",
    "KILL_COMMAND",
    "SERVICE_LOG_COMMAND",
    "SERVICE_LOG_EXCERPT_LENGTH",
)
