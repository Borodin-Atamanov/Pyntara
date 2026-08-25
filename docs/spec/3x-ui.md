# 3x-ui Xray panel

There is a dedicated 3x-ui installation task: three_x_ui_xray_setup. Stage 1 installs the 3x-ui Xray panel as a system service by wrapping the official installer; it does not manage the panel credentials or create any inbound. Stage 2 reads the credentials the panel generated on first start, verifies the session through the REST API and stores them in the runtime vault. Stage 3 creates the universal server inbound. Stage 4 ensures the Let's Encrypt IP certificate when ssl_enabled.

## Installation mechanism

The task wraps the official install.sh of the configured repository instead of downloading and unpacking the release itself. The official installer is trusted to resolve the newest release, download and unpack the archive for the architecture into the install directory, create the systemd unit, enable and start the service and install its dependencies; the task does not duplicate those steps. The single source of truth for the installer location is install_script_url in the task config.

The official installer runs non-interactively: XUI_NONINTERACTIVE=1 replaces every interactive prompt with an environment-variable value or a sane default. Alone among the service-install tasks, 3x-ui does not own a rendered configuration file: the panel stores its own state in a generated SQLite database, so on stage 1 there is nothing for the task to write or diff.

Before the installer runs, the task frees the fixed panel port and, when ssl_enabled is set, the ACME port 80: when the x-ui service owns a port, the service is stopped via systemctl; when an unknown process owns it, the process is terminated with SIGTERM and then SIGKILL after a grace period. The installer is then run with the proquint credentials, the panel port and the SSL mode in its environment (XUI_USERNAME, XUI_PASSWORD, XUI_WEB_BASE_PATH, XUI_PANEL_PORT, XUI_SSL_MODE).

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository, the endpoint https://api.github.com/repos/{github_repo}/releases/latest. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is a task error. The tag carries a leading v; the version comparison strips it on both sides.

The installed version comes from the x-ui binary in the install directory run with -v: the first dotted version triple in the combined stdout and stderr output. A missing binary, a nonzero exit or a hung query reports the version as not installed, so the task runs the installer.

## Idempotency

The official installer always tears the panel down and rebuilds it: on every run it stops the service, removes the install directory and re-unpacks the archive, then enables and starts the service again. It has no version-equality gate of its own. The task therefore applies the gate itself and only invokes the installer when the target state is not reached.

The target state is reached when the installed version equals the newest release tag and the service is enabled and active. The task then returns a plain done result with changed=False and does not invoke the installer, so a working panel is never torn down on a rerun just to confirm the state.

When the version differs, the service is disabled or inactive, or the task is forced, the task downloads the official installer and runs it, then waits for the service to become active. A version mismatch is the normal-update path: the task updates the panel to the newest release without force mode, because updating a version does not destroy the configured system. Force mode reruns the installer even when the version matches and the service is enabled and active, which is the explicit permission to rebuild the panel.

## Credentials boundary

The task generates the panel credentials itself and passes them to the installer as env vars, so the task controls the credentials instead of accepting whatever the installer generates. The values are proquint encodings (draft-rayner-proquint, shared proquint_encode in utils) of fresh random bytes from os.urandom:

- username: 4 random bytes, no separator, 10 letters.
- password: 8 random bytes, no separator, 20 letters.
- webBasePath: 8 random bytes, dash separator, 23 characters (20 letters and 3 dashes).
- panel port: the fixed panel_port from the task config, default 35353.

The installer applies these env vars only when the panel is in the default state: a fresh panel starts with the default credentials and a short webBasePath, so the installer sets all four values and writes them into /etc/x-ui/install-result.env (mode 600, root) through write_install_result. On a rerun against a panel whose credentials are no longer the defaults, the installer preserves the current credentials, port and webBasePath, so a rerun never rotates them: the credentials are effectively applied only at the first deployment. This is the intended gate: the task does not re-check the panel state itself.

The API token is left to panel generation: the x-ui binary has no setter for it, so there is nothing the task could pass.

The install-result.env file is the handoff to stage 2, which logs in through the panel API and stores the credentials in the runtime vault.

## Service lifecycle

The systemd unit is created by the official installer; the task never renders or writes it. After the installer completes, the task waits for the unit to report active, repeating the is-active check up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the attempts. A unit that stays inactive after the loop is a task error.

## Stage 2: credential capture and vault storage

After the installer finishes (or when the target state is already reached on a rerun), the task runs stage 2: it reads the install-result.env file, builds the panel base URL from the address, port and webBasePath, performs a CSRF-protected login (GET /csrf-token, POST /login with X-CSRF-Token header and form-encoded username and password, then GET /panel/api/inbounds/list to verify the session), and stores the credentials in the runtime vault.

The vault entry is named by vault_entry_title in the task config (three_x_ui_credentials by default). The username field carries the panel admin username, the password field carries the panel admin password, the url field carries the panel base URL (http://127.0.0.1:PORT/BASE_PATH). The notes field carries the additional values as key=value lines: XUI_PANEL_PORT, XUI_WEB_BASE_PATH, XUI_API_TOKEN, XUI_DB_TYPE.

When the vault entry already exists and its values match the current credentials, stage 2 does nothing (changed=False). When the values differ (a panel reinstall changed the credentials), the entry is updated. When the install-result.env file is absent or the panel is unreachable, stage 2 returns a warning but does not fail the task, so a rerun after the panel starts will capture the credentials.

The runtime vault must exist before stage 2 runs; the local_vault_setup task, which runs earlier in the default task set, creates it. When the runtime vault is unavailable, stage 2 returns a warning and the credentials are not stored.

## Stage 3: universal server inbound

After stage 2 completes, the task runs stage 3: it creates a VLESS inbound with REALITY on the port configured in `inbound_port` through the panel Bearer-token API. On a rerun it finds the existing inbound by port and returns done without creating a duplicate.

The REALITY keypair is generated through the panel's built-in endpoint `GET /panel/api/server/getNewX25519Cert`, which returns both the private and public X25519 keys. The private key is required for the inbound configuration; the public key is stored alongside it in the vault entry notes for client configuration.

### API authentication

Stage 3 uses the Bearer token from `install-result.env` (`XUI_API_TOKEN`) for all API calls. The Bearer token authenticates every `/panel/api/*` endpoint without needing a CSRF token or session cookie, as documented by the panel: "Bearer-token callers can skip this — the middleware short-circuits CSRF for authenticated API requests."

### Inbound payload

The inbound is created with nested JSON objects for `settings`, `streamSettings` and `sniffing` (the preferred format). The payload structure:

- `protocol`: `"vless"`
- `port`: from `inbound_port` config
- `remark`: from `inbound_remark` config
- `settings`: `{"clients": [], "decryption": "none"}`
- `streamSettings`: `{"network": "tcp", "security": "reality", "realitySettings": {"show": false, "xver": 0, "dest": "<reality_dest>", "serverNames": ["<reality_server_names>"], "privateKey": "<generated>", "shortIds": ["<reality_short_id>"]}}`
- `sniffing`: `{"enabled": true, "destOverride": ["http", "tls"]}`
- `enable`: `true`

### Idempotency

Before creating, the task calls `GET /panel/api/inbounds/list` and searches for an inbound whose `port` matches the configured `inbound_port`. When found, stage 3 returns immediately with `changed=False`. When not found, it generates a keypair, creates the inbound, and appends the REALITY keys to the vault entry notes.

### Key storage

The generated private and public keys are appended to the existing vault entry notes as `REALITY_PRIVATE_KEY=<value>` and `REALITY_PUBLIC_KEY=<value>` on separate lines. When the vault is unavailable, the inbound is still created but the keys are not persisted (a warning is returned).

## Stage 4: SSL certificate

When `ssl_enabled` is set, the panel gets a Let's Encrypt certificate for its public IP address (installer option 2, the shortlived profile). The certificate is valid for about six days and auto-renews through the acme.sh cron job.

On the install path the installer receives `XUI_SSL_MODE=ip` in its environment, so its own SSL step issues the certificate. The ACME HTTP-01 listener binds port 80, and the installer's SSL step fails in non-interactive mode when the port is busy, so the task frees port 80 before the installer runs.

On a rerun where the target state is reached and the installer is skipped, the task runs stage 4: it queries `x-ui setting -getCert true` and does nothing when the panel already has a certificate. When it has none, the stage detects the public IPv4 address from the same echo services the installer uses, frees port 80 and issues the certificate through acme.sh exactly like the installer's setup_ip_certificate: `--issue --standalone --httpport 80` with the shortlived profile, `--installcert` into `/root/cert/ip` with a reload command that restarts x-ui, then `x-ui cert -webCert -webCertKey`. A panel that cannot be reached by Let's Encrypt (no public address, port 80 not open) reports a warning and stays on HTTP; the failure never fails the task.

### Config reference

New fields in the `[three_x_ui_xray_setup]` table:

- `panel_port` (integer, optional, default `35353`): fixed panel port passed to the installer via XUI_PANEL_PORT. Must be between 1 and 65535. Applied on first deployment; preserved on an existing panel with custom credentials.
- `ssl_enabled` (boolean, optional, default `true`): enables the Let's Encrypt IP certificate setup. When false the installer runs without XUI_SSL_MODE, no port is freed for ACME and stage 4 is skipped; the panel stays HTTP.
- `inbound_port` (integer, required): TCP port for the VLESS+REALITY inbound. Must be between 1 and 65535.
- `inbound_remark` (string, optional, default `"universal"`): Display label for the inbound in the panel.
- `reality_dest` (string, optional, default `"www.google.com:443"`): Destination address and port for REALITY TLS handshake mimicry.
- `reality_server_names` (array of strings, optional, default `["www.google.com"]`): ServerNames the REALITY handshake presents.
- `reality_short_id` (string, optional, default `"6ba85179e30d4fc2"`): Short ID for REALITY. Must be a hex string.

### Limitations

Stage 3 does not add any clients to the inbound — the `clients` array is created empty. Client management is outside the scope of this stage. The inbound is created with `enable: true` and starts accepting connections immediately after the panel applies the configuration.
