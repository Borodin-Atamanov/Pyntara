# Network features, proxy, and access

## dnscrypt-proxy system-wide resolver

Task: dnscrypt_setup. The machine resolves every DNS query through a
local dnscrypt-proxy service, which listens on all interfaces and
resolves through its encrypted servers with a large set of plain DNS
fallback servers. The full design lives in docs/spec/dnscrypt-setup.md.

## NextDNS system-wide resolver

Task: nextdns_setup_system_wide. The machine resolves through one NextDNS
profile, chosen deterministically from the hostname, with DNS-over-TLS,
and falls back to independent public DNS when NextDNS is unreachable.

Profile selection: the vault subgroup named by
nextdns_setup_system_wide.vault_group_title carries one entry per profile;
the username field of every entry is the 6-hex profile ID. The IDs are
sorted and the profile is sha256(hostname) modulo the pool size, so the
same hostname always resolves through the same account and hostnames
spread evenly over the pool. The hostname is the machine hostname
reported by the kernel, set by the hostname task.

Endpoints (fixed by the NextDNS service, values in the
nextdns_setup_system_wide config table): the DNS-over-TLS endpoint is
<id>.dns.nextdns.io (dot_endpoint_format with the {profile_id}
placeholder); the IPv4 anycast addresses (ipv4_servers) carry the profile
only through the TLS server name, never through the address; the
id-specific IPv6 addresses are <prefix>::<b1>:<b2b3> under every
configured ipv6_prefixes entry, where b1 is the first byte of the profile
ID and b2b3 the two remaining bytes. The formulas live in pyntara.nextdns
as pure functions that take the configured values, the single
implementation imported by the task.

Configuration: the task writes a drop-in into
nextdns_setup_system_wide.resolved_conf_dir (the file dropin_file_name,
the section header resolve_section and the ownership comment
dropin_header) with the directives whose keys come from directive_keys:
the first lists the DoT servers (address#endpoint), the second the
FallbackDNS servers, the third the DNSOverTLS mode and the fourth the
Domains value (domains_directive). The drop-in is merged, never rewritten
wholesale: a line whose key equals one of directive_keys is replaced by
the merge, every other line in the file survives, so a profile change
swaps the old DNS= line instead of stacking a second one. DNSOverTLS is
opportunistic, so the machine keeps working on networks that block or
lack TLS DNS. When manage_networkmanager is set, the task tells
NetworkManager to ignore DHCP-issued DNS on every connection
(ipv4.ignore-auto-dns and ipv6.ignore-auto-dns), because per-link DNS
would otherwise shadow the global NextDNS servers; the task checks that
nmcli exists before touching NetworkManager. All commands (the
NetworkManager check, list and modify templates, the resolver restart,
the state query and the verification query) come from the
nextdns_setup_system_wide config table.

Verification: the task proves that the machine actually resolves through
the profile the way NextDNS recommends. resolvectl status must list the
configured servers and a query to nextdns_setup_system_wide.verification_url
must return a JSON body with status ok, which is the
NextDNS-recommended check. The endpoint answers with a redirect to a
per-query subdomain, so the verification command follows redirects. On a
failed verification the drop-in is removed, the NetworkManager flags are
disabled again and systemd-resolved is restarted, so the machine returns
to its previous resolver configuration.

Telemetry: after a successful verification the task records the applied
profile ID in the file nextdns_setup_system_wide.profile_id_file_path
(mode profile_id_file_mode). The file is removed on revert, so its
presence means the profile is applied and verified. The System Metrics
collector reads it into network.json through the nextdns module
(docs/spec/system-metrics.md, section Collected data).
answer when NextDNS itself is unreachable. The configured set covers
seven independent providers (Cloudflare, Google, Quad9, Cisco OpenDNS,
AdGuard, CleanBrowsing and Verisign) with IPv4 and the main IPv6 anycast
addresses, so a NextDNS outage never leaves the machine without
resolution and no single provider becomes a point of failure.

## Local proxy server

Dedicated task: run a local proxy server on the computer with authentication (password/port).
This proxy runs as a Kubuntu system service and is managed by standard system tools.

## Proxy tunnel

Dedicated task: local proxy tunnel to a remote proxy/VPN.
Remote proxy connection parameters are taken from secrets unlocked by admin password at first Pyntara installation.
A local proxy port must be created so any applications can connect to it.
