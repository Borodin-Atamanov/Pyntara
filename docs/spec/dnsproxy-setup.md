# dnsproxy system-wide resolver
There is a dedicated `dnsproxy_setup` task. It installs the latest Linux release of AdGuard dnsproxy from the configured GitHub repository and runs it as a root-owned systemd service.

## Upstreams
The task reads the NextDNS profile id from the shared profile id file that `nextdns_setup_system_wide` writes; it never opens the vault itself, so both tasks always agree on the profile. The task depends on `nextdns_setup_system_wide` in the catalog, so the file exists before dnsproxy runs. The selected profile is rendered into three equal primary upstreams:

```text
https://dns.nextdns.io/{profile_id}
tls://{profile_id}.dns.nextdns.io
quic://{profile_id}.dns.nextdns.io
```

The task uses dnsproxy load balancing. No order is required between DoH, DoT and DoQ. The fallback resolver group is a copy of the bootstrap addresses in the same protocol forms, and is passed to dnsproxy as the fallback list. Fallback is used when the primary upstream group is unavailable. When the `append_provider_dns` flag is enabled, the addresses discovered from the current network are appended to the end of both the fallback and the bootstrap resolver groups in plain UDP port 53 form only.

## Cache and logging
Caching is enabled by default and is an explicit configuration value. The cache size in bytes is a configured value passed through the dnsproxy `--cache-size` flag. DNS queries are written to the configured single query log file through the dnsproxy `--output` flag. The production command does not pass `--verbose`, so the query log carries startup info and upstream errors, not one line per answered query. The file is root-owned and uses the configured mode. Log rotation is outside this task.

## Installation
The task reads the latest release from the configured GitHub API repository, maps the Debian architecture to the official Linux tar archive, downloads it into the configured directory, extracts and validates the binary, and atomically replaces the installed binary only after the staged binary reports the expected version. The release query and the binary download run with the engine-wide curl_timeout_seconds and curl_retries. A failed update does not remove a working installed binary.

## Service
The task renders a systemd unit from `task_data/dnsproxy_setup/dnsproxy.service`. All command-line values are supplied by the task configuration. The service runs as root, restarts on failure and is enabled for multi-user boot. It listens on both `0.0.0.0:53053` and `[::]:53053`, so IPv4 and IPv6 clients on the network can use it.

Before a fresh start, the task scans the TCP and UDP listen states of the resolver port and stops every process that owns it, telling the user what was stopped; a port that stays occupied is a loud failure. After the service becomes active the task sends a direct DNS query to the local listener through a standard-library probe, before any resolver change: a dnsproxy that is up but cannot resolve leaves the system resolver untouched.

## System resolver
After the local dnsproxy listeners are active and answer the direct probe, the task owns a systemd-resolved drop-in that routes the global DNS domain to the local loopback listeners (`127.0.0.1:53053` and `[::1]:53053`). NetworkManager auto DNS is disabled on the active connections by UUID, so a repeated profile name in the catalog cannot redirect the change to the wrong connection. Every changed connection is reapplied to its running device, because a profile-only change keeps the DHCP-provided DNS on the per-link scope until the connection is reapplied. Foreign resolver configuration lines are preserved by the shared directive merge helper.

After the cutover the task verifies the system in two ways: a query of the verification domain through systemd-resolved must succeed, and the routing check must prove that systemd-resolved forwards every query to the local dnsproxy. The routing check reads the Global block of resolvectl status: the resolv.conf mode is stub, so applications resolve through systemd-resolved, the global DNS points at the local loopback listener (`127.0.0.1:53053` or `[::1]:53053`), so systemd-resolved forwards to dnsproxy, and the wildcard routing domain `~.` is present, so no query can fall through to a default-route per-link server. A failed cutover step reverts: the drop-in written by the run is removed, the changed NetworkManager connections return to their previous auto DNS handling, systemd-resolved restarts and the service stops, so the machine never keeps a broken cutover. A genuine routing failure (stub mode, global DNS or the `~.` domain missing) does not revert: the resolver drop-in and the running dnsproxy service are kept so the system stays on dnsproxy, the failure is journaled at error_priority and reported as a task error with the remedy (check the systemd-resolved routing and rerun the task).

A surviving provider DNS server on a per-link scope is not an error by itself: without a routing domain on the per-link scope it does not compete with the global `~.` domain, so systemd-resolved still routes queries through dnsproxy. The leftover servers are reported as a task warning with the remedy (remove the static provider DNS from the active NetworkManager connection or reapply it), and the task succeeds. Address matching is per whole token, so a truncated address can never match as a substring of a longer one.

## Existing DNS tasks
`nextdns_setup_system_wide` selects and records the machine's NextDNS profile and writes the profile id file that `dnsproxy_setup` reads. The `dnsproxy_setup` task owns the default system-wide resolver in all install modes; it does not remove dnscrypt-proxy, because any process that owns the resolver port is stopped by the port scan before the service starts.

## Idempotency
A matching installed binary, service unit, query log setup, resolver drop-in, enabled and active service and successful verification produce a done result with changed=False. Force mode restarts and reapplies the owned state. All task-owned paths and values come from `[dnsproxy_setup]` configuration.

## Runtime DNS discovery
The task module exposes `discover_dns_servers(cfg, timeout)` for other tasks that need DNS addresses from current network state. It always invokes both `resolvectl dns` and `nmcli -t -f IP4.DNS,IP6.DNS device show`; it does not read files or change system state. Outputs from both commands are combined, duplicate and loopback addresses are removed, valid addresses are normalized and sorted into separate IPv4 and IPv6 tuples. A failure of one command does not discard results from the other; diagnostics are returned with the address groups. The function only discovers addresses and does not test DNS reachability.

The task itself calls discovery during installation when `append_provider_dns` is enabled. Every discovered IPv4 and IPv6 address is appended to the end of the fallback and the bootstrap resolver groups as a bare address for plain UDP on port 53; no encrypted protocol forms are generated for provider addresses. An empty discovery leaves the configured pool unchanged. When the flag is disabled, the discovery commands are not run at all. Because the discovered addresses are baked into the rendered unit, a changed provider DNS on a later run rewrites the unit and restarts the service.

## Limitations
Unit tests mock external processes, filesystem paths and network behavior. They do not prove external DNS availability in every network, fallback reachability for every configured server or systemd behavior on every distribution. The target system must be checked after installation.
