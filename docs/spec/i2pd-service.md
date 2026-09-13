# i2pd service

There is a dedicated i2pd installation task: i2pd_service_setup.

The task installs the i2pd anonymous network router from the GitHub releases of the configured repository and runs it as a system service. The distribution package is never used, so the installed version is always the newest release instead of the version packaged for the distribution.

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository: the endpoint https://api.github.com/repos/{github_repo}/releases/latest returns the latest non-prerelease release, and tag_name is the version. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is a warning of a completed task, the version stays unknown and the remaining steps run.

The installed version comes from i2pd --version: the first dotted version triple in the combined stdout and stderr output. A missing binary, a nonzero exit or a hung query reports the version as not installed, so the task reinstalls. When the installed version differs from the newest release tag, the task downloads and installs the new release; the rerun after a new upstream release therefore updates i2pd and restarts the service, which is the intended consequence of always running the newest version.

## Operating system and architecture

The distribution is read from /etc/os-release through the shared helpers in pyntara.utils: read_os_release parses the shell-style variables, os_family_is_debian checks the fields of engine.os_release_family_keys for a value of engine.os_release_debian_family_names, and dpkg_architecture runs dpkg --print-architecture. The vocabulary of the file is a config value and not a constant, because a distribution that renames its family field or reports a new family name must not need a code change; the helpers stay shared with every future task that needs the same facts.

Only Debian-based distributions are supported: the release assets are deb packages, and a distribution outside the Debian family is a warning of a completed task. Such a machine never gets the package, so the task skips the release query and the install alone and still deploys the configuration files and the service state. The deb asset is chosen by the dpkg architecture and the VERSION_CODENAME of the os-release file:

the codename-specific asset i2pd_{tag}-1{codename}1_{arch}.deb wins, because it is built against this distribution  
the generic asset i2pd_{tag}-1_{arch}.deb is the fallback, so a release without a build for this codename still installs  
a release without either asset for the architecture is a warning of a completed task, which skips the install alone

The asset list comes from the release payload, so new codenames never need code changes: the exact name is looked up among the returned assets. The two candidate names are configured templates of the task: codename_asset_name_template carries {release_tag}, {codename} and {arch}, generic_asset_name_template carries {release_tag} and {arch}, and os_release_codename_key names the os-release field the codename is read from, so a rename of the upstream asset or of the distribution field is a config change and never a code change.

## Download trust

The package is downloaded from the official GitHub release assets of the configured repository. No checksum verification is performed: the source is trusted, and an extra check would add a failure point without protecting the install, because the checksum file travels over the same channel as the package. The download uses curl --fail and a nonzero exit is a warning of a completed task, so a failed transfer is never mistaken for a successful one and the remaining steps still run.

## Configuration ownership

The task owns the main configuration file at the configured config_path. It renders the template at task_data/i2pd_service_setup/i2pd.conf and rewrites the file whenever the rendered content differs, so manual edits are reverted on the next run. The template renders only the log level, the tunconf path to the owned tunnels file, the two proxy switches and the SOCKS proxy port. Every other option keeps the i2pd built-in default, so a package upgrade that gains new options never conflicts with this file, and dpkg never needs to resolve a conffile conflict during an update.

config_path must match the --conf path of the package unit, otherwise the rendered values are ignored. The deb package installs the unit with ExecStart i2pd --conf=/etc/i2pd/i2pd.conf, so the default config_path matches; the value stays configurable because the unit path is a package contract and may change.

The task owns a second file, the tunnels configuration with the SSH server tunnel, described below.

## SSH server tunnel

The machine becomes reachable over I2P without a single manual step after the run: the task publishes an SSH server tunnel. I2P cannot reach a TCP service directly; a server tunnel publishes a local destination on the network and forwards every incoming I2P connection to a local address. The tunnels file lives at the configured tunnels_config_path, and the main configuration names that file through tunconf, so i2pd reads exactly the owned file regardless of where the package default points.

The tunnel forwards to the SSH daemon, and its port is not a parameter anywhere: the task reads the sshd Port directive from the ssh_daemon_setup configuration. The tunnel and the daemon therefore share one source of truth and can never diverge. The forward host is the loopback address, because the daemon runs on the same machine and the tunnel connects locally.

The tunnel identity lives in the keys file at the configured tunnel_keys_path and is created by i2pd on the first start. The file must live in the i2pd data directory, and only there: the AppArmor profile of the package grants the router write access to its data directory and read-only access to the configuration directory, and i2pd resolves every keys path from the tunnels file against the data directory anyway, so an absolute path in the tunnels file would point into a directory that does not exist. The tunnels file therefore carries only the file name, and the task reads the full configured path. A missing keys file is the first-run state, not an error: i2pd creates the identity only after the router is up, so once the service is started the task waits for the file, repeating the decode with a pause of address_check_retry_delay_seconds between two attempts until address_check_attempts run out. A normal first start writes the identity well inside that window, so one run both saves the address and reports it; a machine where the file never appears still ends the wait and says the address is not available yet, so the run never hangs. The identity is stable, so the address survives restarts and reconfigurations.

The keys file is the binary PrivateKeys record i2pd writes: the first 387 bytes are the IdentityEx (256-byte encryption key, 128-byte signing key and a 3-byte certificate), and the address is the lowercase unpadded base32 of the SHA-256 hash of that IdentityEx with the .b32.i2p suffix. The certificate starts with the type byte; the KEY type means the signing and crypto key types follow in an extended block whose length is the big-endian uint16 at certificate offset 1, and the hash covers the identity plus that block. The task parses the certificate, computes the address and carries it in its message; a record without the KEY certificate yields no address and the message says the address is not available yet.

## Connecting over I2P

An SSH client reaches the tunnel through the local SOCKS proxy of i2pd, which the task enables. The proxy listens on the loopback address at socks_proxy_port of the [i2pd_service_setup] table, which the task renders into the [socksproxy] section of i2pd.conf, so the port has one home and no caller guesses the i2pd default; the client routes the connection through it with a ProxyCommand:

```bash
ssh -v -p <ssh_port> -o ProxyCommand="nc -X 5 -x 127.0.0.1:<socks_proxy_port> %h %p" <user>@<base32>.b32.i2p
```

The placeholders are the configured sshd Port directive, the configured SOCKS port of the [i2pd_service_setup] table, the user whose authorized_keys holds the deployed key, and the tunnel address from the task message. The network telemetry carries the same invocation for the tunnel, built from the same config values and without a user: the operator chooses the user, and the deployed key is offered by the ssh agent. The same connection can be kept as a named host in the client configuration, so the invocation shortens to a single alias:

```text
Host <alias>
HostName <base32>.b32.i2p
User <user>
Port <ssh_port>
ProxyCommand nc -X 5 -x 127.0.0.1:<socks_proxy_port> %h %p
```

The client must offer the deployed key; on the target machine the key is loaded once with ssh-add and the agent keeps it for the session. The connection is noticeably slower than the cleartext one, because the traffic crosses the I2P network in both directions, so the client timeouts from ssh_client_setup apply.

## Determining the address on the target system

The .b32.i2p address is available on the target system through a shared decoder and a command, so the address can be reported without repeating the binary parsing logic. The decoder lives in the pyntara.i2pd module and is imported by the task, never copied.

The task saves the computed address into the configured address_file_path with the mode address_file_mode once the identity exists, and rewrites the file whenever the address differs. The address is not secret, so the mode is world-readable (0644 by default) and any user can read the file. The run that creates the identity reports the address in its message, because it waits for the file after the start; a run that finds no identity within the wait says the address is not available yet. The deployed command venv/bin/python -m pyntara.i2pd_address CONFIG_PATH (the venv python from system_metrics_setup.venv_dir) decodes the live keys file first and falls back to the saved file when the keys file is missing or broken, because the identity may have been recreated between two provisioning runs; it prints one JSON record with the channel, the address, the sshd port, the SOCKS proxy and the ssh command that reaches the daemon through the tunnel, and a fallback reason travels inside the record as a note. The ports come from the single config named by the argument, which is the same source the task reads. The shared reporting convention is defined in [Address commands](system-metrics.md#address-commands).

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled, then starts the unit when it is inactive or restarts it when it is active and the package or the configuration changed. The enable, start and restart commands are config values of the task, each carrying the unit name as its {service_unit_name} placeholder, and the installed version comes from the configured version_command. After a start or restart the task waits for the unit to report active, repeating the is-active check up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the attempts, because the forking service may report activating for a moment. A unit that stays inactive after the loop is a warning of a completed task: the missing address is reported and the run continues. The two configuration templates are named by config_template_file_name and tunnels_template_file_name under task_data/i2pd_service_setup/ of the clone, and a missing template is a warning of a completed task that skips the step it serves alone while every other step still runs. The rendered configuration spells its booleans with config_true_value and config_false_value, so the rendered file, the idempotency comparison and the written file share one representation.

The package installs the unit with a dedicated system user and a data directory; the first start generates the router keys under the data directory. The package also installs an AppArmor profile that confines the router to its data and config directories, so the task never touches those locations.

## Idempotency

The target state is reached when the installed version equals the newest release tag, the configuration file matches the rendered template, the tunnels file matches its render, the tunnel keys file exists, the saved address file matches the current address and the service is enabled and active; the task then returns done with changed=False. A missing keys file keeps the task active: it restarts the service so i2pd regenerates the identity, waits for the file to appear within the bounded identity loop, saves the address and reports it. A missing or stale address file also keeps the task active: it writes the file and never reinstalls or restarts a matching, active installation. Force mode rewrites the configurations and restarts the service, but never reinstalls a matching version. The download directory holds only the files of an interrupted install: the package is removed after a successful install, so the directory never accumulates old versions.

## Traffic limit

The router traffic is limited by two parameters rendered into the main configuration file. bandwidth is the total bandwidth limit of the router in kilobytes per second; the configured value 12500 maps to a 100 Mbit/s link, because 100 Mbit/s is 12500000 bytes per second and one kilobyte is 1000 bytes. share is the percentage of that bandwidth used for transit traffic; the configured value 1 means one percent. The transit limit therefore is bandwidth times share divided by 100, so the configured values yield 12500 times 1 divided by 100, that is 125 kilobytes per second, which is the most the router relays for foreign tunnels while the other 99 percent stays for its own traffic.

## Parameters

All parameters live in the [i2pd_service_setup] table of the config/ directory. The release query and the package download run with the engine-wide curl settings from the [engine] table: curl_query_command is the query call and curl_download_command the download call, curl_download_write_out is the progress text the download prints, curl_timeout_seconds is the per-attempt budget of the metadata query, curl_download_timeout_seconds the one of the download, and curl_retries, curl_retry_delay_seconds, curl_connect_timeout_seconds and curl_retry_max_time_seconds are the retry bounds of both; one shared helper inserts those flags before the URL, so no task spells a curl flag itself.

github_repo is the owner/name pair the release query addresses; download_dir is the directory of the downloaded package; os_release_file_path is the distribution identity file the task reads, os_release_codename_key is the field of that file that carries the codename, and codename_asset_name_template with generic_asset_name_template are the two candidate asset names, formatted with the release tag, the codename and the dpkg architecture.  
service_unit_name, config_path, log_level, bandwidth, share, http_enabled, socks_proxy_enabled and socks_proxy_port are the unit and the values rendered into the main configuration file, with config_true_value and config_false_value as the boolean spelling of that file.  
tunnels_config_path, tunnel_name, tunnel_host and tunnel_keys_path describe the owned tunnels file and the identity whose address the task computes; the SSH port of the tunnel is not a parameter here, it comes from the ssh_daemon_setup Port directive.  
address_file_path and address_file_mode are the saved address file and its mode, address_check_attempts and address_check_retry_delay_seconds bound the loop that waits for the identity file after the first start, install_retries bounds the package install, and start_check_attempts with start_check_retry_delay_seconds bounds the loop that waits for the unit to become active.  
version_command prints the installed version; service_enable_command, service_start_command and service_restart_command drive the unit and carry {service_unit_name}; config_template_file_name and tunnels_template_file_name name the two templates under task_data/i2pd_service_setup/.  
The task belongs to the server and desktop modes and depends on add_extra_repos, so the apt index has the components and the package dependencies resolve.
