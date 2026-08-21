# dnsproxy system-wide resolver
There is a dedicated `dnsproxy_setup` task. It installs the latest Linux release of AdGuard dnsproxy from the configured GitHub repository and runs it as a root-owned systemd service.

## Upstreams
The task selects the NextDNS profile with the existing profile selection logic used by `nextdns_setup_system_wide`: the configured vault group is read, profile IDs are sorted and the hostname selects one deterministically. The selected profile is rendered into three equal primary upstreams:

```text
https://dns.nextdns.io/{profile_id}
tls://{profile_id}.dns.nextdns.io
quic://{profile_id}.dns.nextdns.io
```

The task uses dnsproxy load balancing. No order is required between DoH, DoT and DoQ. The configured fallback resolver list is passed separately to dnsproxy. Fallback is used when the primary upstream group is unavailable.

## Cache and logging
Caching is enabled by default and is an explicit configuration value. Every request is written to the configured single query log file through dnsproxy verbose output. The file is root-owned and uses the configured mode. Log rotation is outside this task.

## Installation
The task reads the latest release from the configured GitHub API repository, maps the Debian architecture to the official Linux tar archive, downloads it into the configured directory, extracts and validates the binary, and atomically replaces the installed binary only after the staged binary reports the expected version. A failed update does not remove a working installed binary.

## Service
The task renders a systemd unit from `task_data/dnsproxy_setup/dnsproxy.service`. All command-line values are supplied by the task configuration. The service runs as root, restarts on failure and is enabled for multi-user boot.

## System resolver
After the local dnsproxy listener is active and answers a verification query, the task owns a systemd-resolved drop-in that routes the global DNS domain to the local listener. NetworkManager DHCP DNS can be disabled through configured commands. Foreign resolver configuration lines are preserved by the shared directive merge helper.

## Existing DNS tasks
`dnscrypt_setup` remains in the repository and retains its implementation, but it has no default install mode membership. `nextdns_setup_system_wide` retains profile selection and profile state recording but no longer edits dnscrypt-proxy, restarts it or verifies through it. Both tasks are therefore not concurrent owners of the default system-wide resolver. The `dnsproxy_setup` task owns that resolver in all install modes.

## Idempotency
A matching installed binary, service unit, query log setup, resolver drop-in, enabled and active service and successful verification produce a skipped task result. Force mode restarts and reapplies the owned state. All task-owned paths and values come from `[dnsproxy_setup]` configuration.

## Limitations
Unit tests mock external processes, filesystem paths and network behavior. They do not prove external DNS availability in every network, fallback reachability for every configured server or systemd behavior on every distribution. The target system must be checked after installation.
