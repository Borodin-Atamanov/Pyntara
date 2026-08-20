# Network features, proxy, and access

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

Endpoints (fixed by the NextDNS service): the DNS-over-TLS endpoint is
<id>.dns.nextdns.io; the IPv4 anycast addresses 45.90.28.0 and 45.90.30.0
carry the profile only through the TLS server name, never through the
address; the id-specific IPv6 addresses are 2a07:a8c0::<b1>:<b2b3> and
2a07:a8c1::<b1>:<b2b3>, where b1 is the first byte of the profile ID and
b2b3 the two remaining bytes. The formulas live in pyntara.nextdns, the
single implementation imported by the task.

Configuration: the task writes a drop-in into
nextdns_setup_system_wide.resolved_conf_dir with the DNS= entries
(address#endpoint), FallbackDNS=, DNSOverTLS= and Domains=~. The drop-in
is edited line by line through the shared config_edit helper: only
missing or differing lines are written, everything else in the file
survives. DNSOverTLS is opportunistic, so the machine keeps working on
networks that block or lack TLS DNS. When manage_networkmanager is set,
the task tells NetworkManager to ignore DHCP-issued DNS on every
connection (ipv4.ignore-auto-dns and ipv6.ignore-auto-dns), because
per-link DNS would otherwise shadow the global NextDNS servers; the task
checks that nmcli exists before touching NetworkManager.

Verification: the task proves that the machine actually resolves through
the profile the way NextDNS recommends. resolvectl status must list the
configured servers and a query to test.nextdns.io must return a JSON body
with status ok, which is the NextDNS-recommended check. On a failed
verification the drop-in is removed, the NetworkManager flags are
disabled again and systemd-resolved is restarted, so the machine returns
to its previous resolver configuration.

Fallback DNS: the servers of nextdns_setup_system_wide.fallback_dns
answer when NextDNS itself is unreachable. The configured set covers two
independent providers with IPv4 and IPv6, so the machine never loses
resolution.

## Local proxy server

Dedicated task: run a local proxy server on the computer with authentication (password/port).
This proxy runs as a Kubuntu system service and is managed by standard system tools.

## Proxy tunnel

Dedicated task: local proxy tunnel to a remote proxy/VPN.
Remote proxy connection parameters are taken from secrets unlocked by admin password at first Pyntara installation.
A local proxy port must be created so any applications can connect to it.
