# NextDNS profile selection

This spec covers the nextdns_setup_system_wide task: the deterministic
per-machine choice of one NextDNS profile and the recording of its ID.
The system-wide resolver itself is owned by the dnsproxy_setup task
(docs/spec/dnsproxy-setup.md), which reads the recorded ID to render its
NextDNS upstreams.

## Profile selection

The vault subgroup named by nextdns_setup_system_wide.vault_group_title
carries one entry per profile; the username field of every entry is the
6-hex profile ID. The IDs are sorted and the profile is sha256(hostname)
modulo the pool size, so the same hostname always resolves through the
same account and hostnames spread evenly over the pool. The hostname is
the machine hostname reported by the kernel, set by the hostname task.
The derivation lives in pyntara.nextdns as pure functions, the single
implementation imported by the task.

## Profile ID file

After the selection the task records the applied profile ID in the file
at the configured profile_id_file_path with the configured
profile_id_file_mode. dnsproxy_setup reads it to render its NextDNS
upstreams (docs/spec/dnsproxy-setup.md), and the System Metrics
collector reads it into network.json through the nextdns module
(docs/spec/system-metrics.md, section Collected data). The file is
rewritten on a profile change and in force mode; the task is idempotent:
it skips when the file already carries the selected profile. A missing
profile group or an empty profile pool is a failure: the file is never
touched then.
