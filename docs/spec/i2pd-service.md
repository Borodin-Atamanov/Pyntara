# i2pd service

There is a dedicated i2pd installation task: i2pd_service_setup.

The task installs the i2pd anonymous network router from the GitHub releases of the configured repository and runs it as a system service. The distribution package is never used, so the installed version is always the newest release instead of the version packaged for the distribution.

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository: the endpoint https://api.github.com/repos/{github_repo}/releases/latest returns the latest non-prerelease release, and tag_name is the version. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is reported as a task error.

The installed version comes from i2pd --version: the first dotted version triple in the combined stdout and stderr output. A missing binary, a nonzero exit or a hung query reports the version as not installed, so the task reinstalls. When the installed version differs from the newest release tag, the task downloads and installs the new release; the rerun after a new upstream release therefore updates i2pd and restarts the service, which is the intended consequence of always running the newest version.

## Operating system and architecture

The distribution is read from /etc/os-release through the shared helpers in pyntara.utils: read_os_release parses the shell-style variables, os_family_is_debian checks ID and ID_LIKE for the debian or ubuntu family, and dpkg_architecture runs dpkg --print-architecture. The helpers are shared with future tasks that need the same facts.

Only Debian-based distributions are supported: the release assets are deb packages, and a distribution outside the Debian family is reported as a task error before any download. The deb asset is chosen by the dpkg architecture and the VERSION_CODENAME of the os-release file:

the codename-specific asset i2pd_{tag}-1{codename}1_{arch}.deb wins, because it is built against this distribution
the generic asset i2pd_{tag}-1_{arch}.deb is the fallback, so a release without a build for this codename still installs
a release without either asset for the architecture is reported as a task error

The asset list comes from the release payload, so new codenames never need code changes: the exact name is looked up among the returned assets.

## Download trust

The package is downloaded from the official GitHub release assets of the configured repository. No checksum verification is performed: the source is trusted, and an extra check would add a failure point without protecting the install, because the checksum file travels over the same channel as the package. The download uses curl --fail and a nonzero exit is reported as a task error, so a failed transfer is never mistaken for a successful one.

## Configuration ownership

The task owns the main configuration file at the configured config_path. It renders the template at task_data/i2pd_service_setup/i2pd.conf and rewrites the file whenever the rendered content differs, so manual edits are reverted on the next run. The template renders only the log level, the tunconf path to the owned tunnels file and the two proxy switches. Every other option keeps the i2pd built-in default, so a package upgrade that gains new options never conflicts with this file, and dpkg never needs to resolve a conffile conflict during an update.

config_path must match the --conf path of the package unit, otherwise the rendered values are ignored. The deb package installs the unit with ExecStart i2pd --conf=/etc/i2pd/i2pd.conf, so the default config_path matches; the value stays configurable because the unit path is a package contract and may change.

The task owns a second file, the tunnels configuration with the SSH server tunnel, described below.

## SSH server tunnel

The machine becomes reachable over I2P without a single manual step after the run: the task publishes an SSH server tunnel. I2P cannot reach a TCP service directly; a server tunnel publishes a local destination on the network and forwards every incoming I2P connection to a local address. The tunnels file lives at the configured tunnels_config_path, and the main configuration names that file through tunconf, so i2pd reads exactly the owned file regardless of where the package default points.

The tunnel forwards to the SSH daemon, and its port is not a parameter anywhere: the task reads the sshd Port directive from the ssh_daemon_setup configuration. The tunnel and the daemon therefore share one source of truth and can never diverge. The forward host is the loopback address, because the daemon runs on the same machine and the tunnel connects locally.

The tunnel identity lives in the keys file at the configured tunnel_keys_path and is created by i2pd on the first start. The file must live in the i2pd data directory, and only there: the AppArmor profile of the package grants the router write access to its data directory and read-only access to the configuration directory, and i2pd resolves every keys path from the tunnels file against the data directory anyway, so an absolute path in the tunnels file would point into a directory that does not exist. The tunnels file therefore carries only the file name, and the task reads the full configured path. A missing keys file is not an error, it is the first-run state: the task reports that the address appears after the first start, and the next run reports it. The identity is stable, so the address survives restarts and reconfigurations.

The keys file is the binary PrivateKeys record i2pd writes: the first 387 bytes are the IdentityEx (256-byte encryption key, 128-byte signing key and a 3-byte certificate), and the address is the lowercase unpadded base32 of the SHA-256 hash of that IdentityEx with the .b32.i2p suffix. The certificate starts with the type byte; the KEY type means the signing and crypto key types follow in an extended block whose length is the big-endian uint16 at certificate offset 1, and the hash covers the identity plus that block. The task parses the certificate, computes the address and carries it in its message; a record without the KEY certificate yields no address and the message says the address is not available yet.

## Connecting over I2P

An SSH client reaches the tunnel through the local SOCKS proxy of i2pd, which the task enables. The proxy listens on 127.0.0.1:4447 by default, and the client routes the connection through it with a ProxyCommand:

ssh -p 30222 -o ProxyCommand="nc -X 5 -x 127.0.0.1:4447 %h %p" <user>@<base32>.b32.i2p

The placeholders are the configured sshd Port directive, the user whose authorized_keys holds the deployed key, and the tunnel address from the task message. The same connection can be kept as a named host in the client configuration, so the invocation shortens to a single alias:

Host <alias>
HostName <base32>.b32.i2p
User <user>
Port 30222
ProxyCommand nc -X 5 -x 127.0.0.1:4447 %h %p

The client must offer the deployed key; on the target machine the key is loaded once with ssh-add and the agent keeps it for the session. The connection is noticeably slower than the cleartext one, because the traffic crosses the I2P network in both directions, so the client timeouts from ssh_client_setup apply.

## Determining the address on the target system

The .b32.i2p address is available on the target system through a shared decoder and a command, so the address can be reported without repeating the binary parsing logic. The decoder lives in the pyntara.i2pd module and is imported by the task, never copied.

The task saves the computed address into the configured address_file_path with the mode address_file_mode once the identity exists, and rewrites the file whenever the address differs. The address is not secret, so the mode is world-readable (0644 by default) and any user can read the file. The deployed command venv/bin/python -m pyntara.i2pd_address KEYS_PATH ADDRESS_FILE_PATH (the venv python from system_metrics_setup.venv_dir) decodes the live keys file first and falls back to the saved file when the keys file is missing or broken, because the identity may have been recreated between two provisioning runs. The shared reporting convention is defined in [Address commands](docs/spec/system-metrics.md#address-commands).

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled, then starts the unit when it is inactive or restarts it when it is active and the package or the configuration changed. After a start or restart the task waits for the unit to report active, repeating the is-active check up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the attempts, because the forking service may report activating for a moment. A unit that stays inactive after the loop is a task error.

The package installs the unit with a dedicated system user and a data directory; the first start generates the router keys under the data directory. The package also installs an AppArmor profile that confines the router to its data and config directories, so the task never touches those locations.

## Idempotency

The target state is reached when the installed version equals the newest release tag, the configuration file matches the rendered template, the tunnels file matches its render, the tunnel keys file exists, the saved address file matches the current address and the service is enabled and active; the task then skips with changed=False. A missing keys file keeps the task active: it restarts the service so i2pd regenerates the identity, and never reinstalls a matching version. A missing or stale address file also keeps the task active: it writes the file and never reinstalls or restarts a matching, active installation. Force mode rewrites the configurations and restarts the service, but never reinstalls a matching version. The download directory holds only the files of an interrupted install: the package is removed after a successful install, so the directory never accumulates old versions.

## Parameters

All parameters live in the [i2pd_service_setup] table of the config/ directory.

The task belongs to the server and desktop modes and depends on add_extra_repos, so the apt index has the components and the package dependencies resolve.
