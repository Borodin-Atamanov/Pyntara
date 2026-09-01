# RustDesk setup

The rustdesk_setup task installs the RustDesk remote desktop client on the target machine and configures it for unattended remote access. RustDesk is an open remote desktop tool: the controlled machine registers its ID with a rendezvous server, and any RustDesk client reaches it by typing the ID and the permanent password. The task uses the public RustDesk server (the client default), so no server address or key is configured on the machines or on the controlling clients: connecting is just ID plus password. The task belongs to the server and desktop install modes.

## Release install

The newest client release comes from the GitHub releases API of the configured repository. The release tag is normalized (a leading v is stripped) and compared with the installed version read from rustdesk --version; a normal run whose version equals the tag and whose service is enabled and active changes nothing. A missing version or a version mismatch downloads the matching deb asset and installs it with the shared noninteractive apt path, refreshing the apt index once unless the run skips it. The asset name is rustdesk-{version}-{arch}.deb with the upstream architecture spelling mapped from the dpkg architecture (amd64 to x86_64, arm64 to aarch64); any other architecture falls back to the dpkg spelling as is. The downloaded deb is removed after a successful install.

## Registration

RustDesk has no global ID registry: an ID is a short number that exists only in the registry of the rendezvous server the client registers with. The task keeps the client on the default public server (no custom-rendezvous-server, relay-server or key option is set), so the machine registers its ID on the public registry and is reachable by ID from any default-configured client. The machine ID is derived from the client key pair and is stable: it survives restarts and server changes and changes only when the identity is regenerated. The ID is read through rustdesk --get-id and written to the configured id_file_path with the configured mode, so the System Metrics collector includes it in the network report. A normal run rewrites the file only when it is missing or stale; force mode always rewrites.

## Permanent password

The permanent password is a per-machine secret. The task generates password_words random proquint words joined by password_separator through the shared proquint_encode helper, writes them into the runtime vault entry named by vault_entry_title and applies them through rustdesk --password. A normal run reuses the stored value, so the machine keeps its password; force mode (or a missing entry) generates a fresh value and saves it into the vault. The password is a secret and its command is never logged. A vault that cannot be opened leaves the password unchanged and reports a warning, so an existing access credential is never lost silently. The entry lives in the root group of the vault structure and is filled by the task, like the 3x-ui credentials entry.

## Identity and force mode

The client identity (the key pair and the derived machine ID) lives in the identity file RustDesk.toml inside the configured config_dir. A normal run keeps it, so the ID is stable and the network report reference stays valid. Force mode stops the service, removes the identity file and starts the service again, so a fresh identity and a fresh machine ID are generated, then the task rewrites the ID file. Force mode also regenerates the permanent password, so a forced rerun rotates both access credentials.

## Service lifecycle

The rustdesk package ships and enables the rustdesk.service unit; the task enables it when disabled and starts it when inactive, then waits through the configured readiness loop for the daemon to answer rustdesk --get-id, because the unit becomes active when its root --service process starts while the per-session --server process that owns the IPC appears a moment later.

## Client options

The client options come from the [rustdesk_setup.options] tables of the config and are applied through rustdesk --option; the task reads the current value and sets the option only when it differs, so the options are idempotent. The configured set covers UDP hole punching (enable-udp-punch), IPv6 P2P (enable-ipv6-punch), headless Linux capture (allow-linux-headless), direct access by IP (direct-server and direct-access-port, the listener that makes direct IP connections work), adaptive bitrate (enable-abr) and the access mode that accepts only permanent-password connections (access-mode). Peers discover each other through the LAN discovery that stays enabled, and the client keeps its automatic update check.

## Wayland and headless notes

Screen capture on a KDE Wayland session goes through the xdg-desktop-portal ScreenCast and works with the client's uinput keyboard and mouse devices; both are created by the root service, so no extra permissions are needed. The client cannot capture the login screen on Wayland. The allow-linux-headless option permits capture on a machine without a physical monitor, which is how a headless server machine stays controllable.

## Parameters

All parameters live in the [rustdesk_setup] table of the config/ directory. The release query and the package download run with the engine-wide curl_timeout_seconds and curl_retries from the [engine] table.
