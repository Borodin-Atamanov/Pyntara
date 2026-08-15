# Tor onion service

There is a dedicated Tor installation task: tor_setup.

The task installs the Tor package from the Ubuntu archive and runs it as a system service. The machine becomes reachable over Tor without a single manual step: the task publishes an SSH onion service that forwards every incoming Tor connection to the local SSH daemon.

## Installation and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos), never from GitHub releases: Tor publishes no release assets on GitHub to chase, so the always-newest mechanic of the i2pd and yggdrasil tasks does not apply. The Ubuntu archive carries the current stable series and receives upstream security updates through the regular apt upgrade, so the running version stays patched without a dedicated update path. The apt index is not refreshed by the task: add_extra_repos refreshed it earlier in the same run, because the task depends on it.

## Configuration ownership

The task owns the main configuration file at the configured config_path. It renders the content from the configured values and rewrites the file whenever the rendered content differs, so manual edits are reverted on the next run. The render starts with an ownership comment, and the order of the lines is fixed, because the idempotency comparison is textual. The rendered options:

1. SocksPort 127.0.0.1:{socks_port} — the SOCKS proxy, bound to the loopback interface only. A client routes its SSH connection through this proxy to reach the onion address.
2. Log {log_level} syslog — the verbosity, written to syslog so the journal shows the Tor diagnostics.
3. HiddenServiceVersion 3 — the onion service protocol version.
4. NumIntroductionPoints {num_introduction_points} — how many introduction points the service maintains; more points keep the service reachable while some of them are under attack, at the cost of more keepalive traffic.
5. HiddenServiceDir {hidden_service_dir} — the directory of the service identity.
6. HiddenServicePort {onion_ssh_port} 127.0.0.1:{ssh_port} — the virtual port clients connect to, forwarding to the local SSH daemon.

The local ssh port is not a parameter anywhere: the task reads the sshd Port directive from the ssh_daemon_setup configuration through the shared reader in pyntara.ssh, the same helper the i2pd task uses. The service and the daemon therefore share one source of truth and can never diverge. The forward host is the loopback address, because the daemon runs on the same machine and the service connects locally. The virtual port is a separate entity owned by this task: it defines what a client connects to on the .onion address, not where the daemon listens, so Port 22 is not a duplication.

## Identity and the hidden service directory

The identity lives in the hidden service directory at the configured hidden_service_dir. The directory must live inside the Tor data directory (/var/lib/tor): the AppArmor profile of the package confines Tor to its data directory, the same lesson as the i2pd data directory. The task creates the directory when it is absent, sets the configured mode (0700 by default, because Tor refuses to serve an onion service from a world-readable directory) and hands the ownership to the configured tor_user (the system user the service runs as), so Tor can write the keys and the hostname file. The contents of the directory are never removed or overwritten: the identity is stable, so the onion address survives restarts and reconfigurations.

Tor generates the identity on the first start. A missing hostname file is not an error, it is the first-run state: the task reports that the address appears after the first start, and the next run reports it.

## Determining the address on the target system

The .onion address is available on the target system through a shared reader and a command. The reader lives in the pyntara.tor module and is imported by the task and the command, never copied. The address from the hostname file crosses an external boundary, so it passes through the shared trim_whitespace helper before it is stored or reported (project rules, the trim rule).

The task saves the address into the configured address_file_path with the mode address_file_mode once the hostname file exists, and rewrites the file whenever the address differs. The address is not secret, so the mode is world-readable (0644 by default) and any user can read the file. The saved file is the fallback: the deployed command venv/bin/python -m pyntara.tor_address HIDDEN_SERVICE_DIR ADDRESS_FILE_PATH (the venv python from system_metrics_setup.venv_dir) reads the live hostname file first and prints the address on stdout, and when the hostname file is missing or empty reads the saved file instead, because the identity may have been recreated between two provisioning runs. When the fallback is used, the reason goes to the following stdout line, so a System Metrics collector module that takes the stdout keeps the error instead of losing it. When neither source yields an address, the command exits nonzero with an explanation on stderr, which the collector joins into the module output. The command needs no config access.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled, then starts the unit when it is inactive or restarts it when it is active and the configuration changed. After a start or restart the task waits for the unit to report active, repeating the is-active check up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the attempts, because the forking service may report activating for a moment. A unit that stays inactive after the loop is a task error. A missing hostname file keeps the task active: it restarts the service so Tor regenerates the identity.

## Idempotency

The target state is reached when the package is installed, the configuration file matches the rendered content, the hidden service directory exists, the saved address file matches the current address and the service is enabled and active; the task then skips with changed=False. A missing or stale address file keeps the task active: it writes the file and never restarts a matching, active installation. Force mode rewrites the configuration, restarts the service and rewrites the address file, but never reinstalls the package.

## Parameters

All parameters live in the config/ directory under [tor_setup]:

package_name is the package that provides the Tor daemon
service_unit_name is the systemd unit installed by the package
config_path is the owned configuration file, rendered by the task
config_file_mode is the file mode of the configuration
hidden_service_dir is the directory of the onion service identity, inside the Tor data directory
hidden_service_dir_mode is the file mode of the directory; Tor refuses a world-readable directory
tor_user is the system user the service runs as, the owner of the hidden service directory
socks_port is the loopback port of the SOCKS proxy
onion_ssh_port is the virtual port of the onion service a client connects to; the local forward port is read from the ssh_daemon_setup Port directive
num_introduction_points is the number of introduction points of the service
log_level is the Tor verbosity: debug, info, notice, warn or err
install_retries is the retry count of the package install; total attempts are retries plus one
start_check_attempts and start_check_retry_delay_seconds bound the readiness loop after start or restart
address_file_path is the saved onion address file the task writes once the hostname file exists; the deployed address command reads it as the fallback when the hostname file cannot be read
address_file_mode is the file mode of the saved address file, as an octal string; the address is not secret, so the file is readable by every user (0644 by default)

## Connecting over Tor

An SSH client reaches the service through a Tor SOCKS proxy. The proxy of the target machine listens on 127.0.0.1:9050 by default, so the target machine itself is already a client; any other machine needs its own Tor with a SocksPort. The client routes the connection through the proxy with a ProxyCommand:

ssh -o ProxyCommand="nc -X 5 -x 127.0.0.1:9050 %h %p" <user>@<address>.onion

The virtual port is 22 by default, so -p is not needed. The same connection can be kept as a named host in the client configuration, so the invocation shortens to a single alias:

Host <alias>
HostName <address>.onion
User <user>
ProxyCommand nc -X 5 -x 127.0.0.1:9050 %h %p

The client must offer the deployed key; on the target machine the key is loaded once with ssh-add and the agent keeps it for the session. The connection is noticeably slower than the cleartext one, because the traffic crosses the Tor network in both directions, so the client timeouts from ssh_client_setup apply.

## Resilience and limitations

The resilience of the connection is provided by Tor itself: the client rebuilds circuits on failure, the guard and fallback directory nodes are compiled into the binary, and the onion service keeps several introduction points and republishes its descriptor hourly. The task does not strengthen the network, it only keeps the prerequisites in place: the service runs at boot, the identity never changes and the configuration stays minimal. Bridges are the manual remedy when the provider blocks Tor: the task does not configure them, because bridge selection needs per-network knowledge. The client-side AutomapHostsOnResolve option improves privacy for DNS-resolving clients and is left as a recommendation in the documentation rather than a rendered option, because nc does not resolve through Tor.

The task belongs to the server and desktop modes and depends on add_extra_repos, so the apt index has the components and the package resolves.
