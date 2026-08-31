# Port forwarding setup

There is a dedicated port-forwarding task: port_forwarding_setup.

The task deploys the Auto Port Forwarding service that keeps reverse ssh tunnels from the machine to every server of the vault group port_forwarding_servers, so the machine's SSH daemon becomes reachable from each server at a known remote port. The port-forwarding key pair itself is deployed by the ssh_daemon_setup task in parallel with the main key pair; this task only configures the service, so a machine is ready to forward as soon as its vault carries the server group and the passphrase.

## Key pair

The key pair lives in the repository under task_data/ssh_daemon_setup/: the private key id_ed25519_pf and the public key id_ed25519_pf.pub. Both files are committed to the repository. The private key is an OpenSSH private key encrypted with the passphrase of the ssh_passphase_for_port_forwarding vault entry, so committing it is safe: the passphrase lives only in the vault, never in the repository or in the config.

The ssh_daemon_setup task deploys the pair to root and to every configured user, in parallel with the main id_ed25519 pair, with the same file modes. The public key line in authorized_keys carries the configured restriction prefix, by default restrict,port-forwarding,permitlisten="*": restrict disables every capability, port-forwarding re-enables only port forwarding, permitlisten allows any listen port on the server side, so the key can only open reverse tunnels and nothing else. The line is appended without duplicates, like the main key line, so keys the user added by hand survive.

## Vault data

The server addresses come from the vault group port_forwarding_servers, one entry per server with the address in the url field. The address may be ipv4, ipv6 or a url; entries without an url are skipped. The group is data, like the NextDNS accounts: the regeneration tooling creates it but never fills or deletes its entries. The data exists only in the production vault, never in the default vault, so a runtime vault without the group makes the service connect to nothing.

The key passphrase comes from the vault entry ssh_passphase_for_port_forwarding. The entry exists only in the production vault; the regeneration tooling, when it creates the entry in a vault that lacks it, generates a fresh password of 7 proquint words joined by dashes (generated_password = "proquint-7" in the vault structure), so the production secret is never copied into another vault. A runtime vault without the entry makes the service connect to nothing.

## Service

The systemd unit auto_port_forwarding.service, deployed by the task, starts the service from the shared deployment venv of system_metrics_setup with the single system config as its only argument. The service:

1. Opens the runtime secret vault with the local password through the shared vault opener.
2. When the server group or the passphrase entry is absent, journals an informative message and exits cleanly; when the vault cannot open or the key is missing, it exits nonzero so systemd restarts it.
3. Reads the server addresses and the passphrase, starts a dedicated ssh-agent, unlocks the key in it through SSH_ASKPASS_REQUIRE=force and removes the askpass helper afterwards, so the passphrase never lingers on disk.
4. Starts one supervisor thread per server. Every thread keeps one ssh -N tunnel alive that forwards the local SSH daemon port, taken from the ssh_daemon_setup Port directive, to a remote port on the server, connecting as the configured user.

The desired remote port is a deterministic function of the machine hostname: sha256 of the hostname mapped into the configured range, so the same machine asks for the same port on every server and the operator can predict it. The configured range defaults to the Linux kernel ephemeral port range, 32768 to 60999, so a desired port that is free on the server falls in the zone the kernel assigns random ports from. When the requested port is taken on the server, the thread asks for a random port (remote port 0), reads the granted port from the ssh output line Allocated port N for remote forward, records it and keeps it stable across reconnects. The server may grant any port; the configured range does not constrain the granted one. The connection is confirmed by its own success line, read from the ssh output.

A dropped connection is re-established after the geometric backoff from the config: the first drop waits the base, every further consecutive drop multiplies the pause until the ceiling; the escalation resets after a connection that stayed up for at least the maximum backoff, so a single drop after a long uptime waits only the base pause.

## Telemetry

The assigned remote ports live in the root-only state file. The System Metrics collector carries a port_forwarding network module that reads the state file, so every network report shows the current ports per server; a machine without the state file reports an empty module instead of an error. On a granted-port change the service saves the state and triggers a fresh collection through systemctl start --no-block on the collector service, so the network report is regenerated with the new ports and sent through the existing pipeline. The collector's non-blocking flock skips the trigger when a collection is already running; the daily collection still carries the current ports. No separate telemetry file is produced.

## Update flow

The server list is read from the runtime vault once at service start and is never refreshed at runtime. To add or remove a server, edit the production vault group, refresh the runtime vault (a pyntara run with the local_vault_setup task forced), and restart the auto_port_forwarding service.

## Idempotency

The task is idempotent: it is done when the unit file matches its template and the service is enabled. It writes the unit, reloads systemd, enables and starts the service otherwise, and verifies that the started service is not in the failed state; an inactive service after a start is the intended no-op state of a machine without port-forwarding data, not a failure. Force mode rewrites the unit and restarts the service, but never touches the keys, which are owned by the ssh_daemon_setup task.

## Parameters

All parameters live in the [port_forwarding_setup] table of the config/ directory: the vault group and passphrase entry titles, the remote user, the desired port range (the Linux ephemeral range by default), the ssh keepalive and connect options, the reconnect backoff, the state file name, the service unit name, the restart pause and the journal identifier. The forwarded local port and the port-forwarding key file names come from the ssh_daemon_setup table, the single source of truth.
