# Yggdrasil service

There is a dedicated yggdrasil installation task: yggdrasil_service_setup.

The task installs the yggdrasil network router from the GitHub releases of the configured repository, configures it (interface, listeners, multicast, peers) and runs it as a system service. The distribution package is never used, so the installed version is always the newest release instead of the version packaged for the distribution.

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository: the endpoint https://api.github.com/repos/{github_repo}/releases/latest returns the latest non-prerelease release, and tag_name is the version. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is reported as a task error. The tag carries a leading v (v0.5.14), the release assets and the version output do not, so the tag is used with the leading v stripped.

The installed version comes from yggdrasil -version: the first dotted version triple in the combined stdout and stderr output. A missing binary, a nonzero exit or a hung query reports the version as not installed, so the task reinstalls. When the installed version differs from the newest release version, the task downloads and installs the new release; the rerun after a new upstream release therefore updates yggdrasil and restarts the service, which is the intended consequence of always running the newest version.

## Operating system and architecture

The architecture is read with dpkg --print-architecture through the shared helper in pyntara.utils. The architecture part of the asset name matches the dpkg architecture, so the asset yggdrasil-{version}-{arch}.deb is chosen directly by name from the release payload; a release without the asset for the architecture is reported as a task error. The distribution family is not checked: the deb package depends only on systemd, so the install fails loudly on a non-Debian system at the dpkg query itself, and the codename plays no role in the asset name.

## Download trust

The package is downloaded from the official GitHub release assets of the configured repository. No checksum verification is performed: the source is trusted, and an extra check would add a failure point without protecting the install, because the checksum file travels over the same channel as the package. The download uses curl --fail and a nonzero exit is reported as a task error, so a failed transfer is never mistaken for a successful one.

## Configuration ownership and node identity

The package postinst creates the group and the /etc/yggdrasil directory and generates /etc/yggdrasil/yggdrasil.conf with a fresh key pair when the file is absent. The task owns the configuration afterwards and rewrites it whenever the rendered content differs, so manual edits are reverted on the next run.

The node identity must survive config rewrites, so the task extracts the private key once into the separate PEM file at private_key_path and references it from the configuration through PrivateKeyPath:

1. When the PEM file exists, nothing happens: the identity is kept.
2. When the PEM is missing but the package-generated config exists, the key is extracted with yggdrasil -useconffile -exportkey and written to the PEM file with private_key_file_mode.
3. When neither exists, a fresh config is generated with yggdrasil -genconf -json, piped into yggdrasil -useconf -exportkey, and the key is saved the same way.

The configuration is rendered as JSON from the configured values: PrivateKeyPath, AdminListen, IfName, IfMTU, Listen, MulticastInterfaces and Peers. The log level is not part of the configuration: yggdrasil reads it from the -loglevel command line flag, which the package unit does not pass, so a log_level setting would have no effect and is therefore absent.

## Listeners and multicast

The listeners accept inbound peerings. The configured default listens on tcp, tls, quic and ws with the bind address [::], which covers IPv4 and IPv6, and port 0, which picks a random free port; wss is rejected by the yggdrasil listener code and socks and sockstls are outgoing-only, so those schemes are not accepted in the configuration for Listen. Multicast peer discovery runs on the configured interface blocks: one block with the regex .* and both beacon and listen enabled covers the whole local network. Multicast only works over IPv6 link-local, so IPv4 neighbours are found through the listener addresses and the peer list only.

## Peer selection

Yggdrasil has no concept of bootstrap nodes: every peering is a full network connection, and a node without peers never joins the network. The task therefore always provisions a peer list.

The full peer list comes from the official public-peers repository: the task downloads the configured tarball with curl, unpacks it into a temporary directory, parses every markdown file for backtick peer URIs and saves the deduplicated full list to peers_full_path next to the configuration for reference. Only the selected working peers ever enter the configuration. The markdown files also contain configuration templates with placeholder hosts such as [proxyhost]:[proxyport] and [username]:[password]@[proxyhost]; the task drops every URI whose host and port do not parse, because yggdrasil aborts on such a peer at startup and the whole node would never connect.

The selection probes the list in batches:

1. The full list is shuffled, so repeated force runs try different nodes.
2. The first peer_batch_size peers are written into the configuration, the service is restarted, and the task waits peer_probe_timeout_seconds.
3. The task reads the yggdrasil journal (journalctl) for Connected lines over that window, resolves each batch peer through DNS and keeps the peers whose address appears in the journal as working.
4. When a batch reaches peer_target_count working peers, the task reads the latencies from yggdrasilctl -json getPeers over the admin socket, keeps the peer_target_count working peers with the lowest ping and restarts the service with the final configuration.
5. When a batch has too few working peers, the next batch is tried, up to peer_max_batches (0 means the whole list). When no batch reaches the target, the last tried batch stays in the configuration and the task reports a warning, because the node at least keeps trying to connect.

The apt index is not refreshed before the install: the package depends only on systemd, which is always installed. When the peer list download fails, the configured static_peers are used; a run where the download fails and static_peers is empty is a task error, because the node would be useless without any peers.

## Determining the address on the target system

The node self address is available on the target system through a shared parser and a command. The JSON parsing of the admin socket output lives in the pyntara.yggdrasil module and is imported by the task and the command, never copied.

After the final restart the task saves the address reported by yggdrasilctl -json getSelf into the configured address_file_path with the mode address_file_mode. The -json flag is required: without it the admin socket prints the node state as an ASCII table, while the shared parser reads JSON, so both the task and the deployed command always request JSON. The address is not secret, so the mode is world-readable (0644 by default) and any user can read the file. The save is best-effort: a failed query or an unparsable output leaves the file untouched and never fails the task. The saved file is the fallback: the deployed command venv/bin/python -m pyntara.yggdrasil_address ADDRESS_FILE_PATH (the venv python from system_metrics_setup.venv_dir) asks the admin socket first (also with -json) and prints the self address on stdout, and when the live query fails reads the saved file instead. When the fallback is used, the reason goes to the following stdout line, so a System Metrics collector module that takes the stdout keeps the error instead of losing it. When neither source yields an address, the command exits nonzero with the reason and the raw yggdrasilctl output on stderr, which the collector joins into the module output. The command needs no config access.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The package postinst enables the unit; the task enables it when it is not enabled, then restarts it for every probe batch and after the final configuration. After the final restart the task checks once that the unit reports active, because the simple service either starts or fails immediately; a unit that stays inactive is a task error. The package also installs the yggdrasil-default-config.service unit; the task does not manage it.

## Idempotency

The target state is reached when the installed version equals the newest release version, the configuration exists with a non-empty Peers list, the key file exists, the saved address file exists and the service is enabled and active; the task then skips with changed=False. A missing address file keeps the task active, so the fallback of the deployed address command is guaranteed to exist after provisioning. Force mode reruns the whole peer selection (download, shuffle, batches) and rewrites the configuration, but never reinstalls a matching version. The download directory holds only the files of an interrupted install: the package is removed after a successful install, so the directory never accumulates old versions.

## Parameters

All parameters live in the config/ directory under [yggdrasil_service_setup]:

github_repo is the GitHub repository in owner/name form
download_dir is the directory for the downloaded package file
service_unit_name is the systemd unit installed by the package
install_retries is the retry count of the package install; total attempts are retries plus one
config_path is the owned configuration file, which must match the DefaultConfigFile embedded in the package
private_key_path is the PEM file with the node key, referenced by PrivateKeyPath
config_file_mode and private_key_file_mode are the file modes of the configuration and the key
if_name is the TUN interface name (ygg instead of auto)
if_mtu is the interface MTU, within the yggdrasil range 1280 to 65535
admin_listen is the admin socket URI used by yggdrasilctl
listen is the array of inbound listener URIs, schemes tcp, tls, quic, ws and unix
multicast_interfaces is the array of multicast blocks, each with regex, beacon and listen
peers_full_path is where the full downloaded peer list is saved for reference
peers_tarball_url is the public-peers repository tarball
peer_batch_size is the probe batch size
peer_target_count is the number of working peers to keep in the final configuration
peer_probe_timeout_seconds is the wait per batch before reading the journal
peer_max_batches is the batch cap; 0 means the whole list
static_peers is the fallback peer list used when the download fails
address_file_path is the saved self address file the task writes after the final restart; the deployed address command reads it as the fallback when the live admin socket query fails
address_file_mode is the file mode of the saved address file, as an octal string; the address is not secret, so the file is readable by every user (0644 by default)

The task belongs to the server and desktop modes and has no dependencies: it does not touch the apt index, so add_extra_repos is not required.
