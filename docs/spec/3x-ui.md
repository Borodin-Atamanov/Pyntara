# 3x-ui Xray panel

There is a dedicated 3x-ui installation task: three_x_ui_xray_setup. Stage 1 installs the 3x-ui Xray panel as a system service by wrapping the official installer; it does not manage the panel credentials or create any inbound. Stage 2 reads the credentials the panel generated on first start, verifies the session through the REST API and stores them in the runtime vault. Stage 3 creates the universal server inbound and makes sure it carries the REALITY key pair the panel issues. Stage 4 ensures the panel serves HTTPS when ssl_enabled: a trusted Let's Encrypt IP certificate when the challenge can reach the machine, a self-signed certificate otherwise. Stage 5 ensures the inbound carries exactly one client and stores the complete connection profile of that client in the runtime vault, so a finished task leaves the node usable as a server.

The task also moves the panel subscription paths off the well-known defaults through the panel settings API, because the panel warns about the default ones; a failure of that step is a warning, not an error, and the panel keeps working with its defaults.

## Installation mechanism

The task wraps the official install.sh of the configured repository instead of downloading and unpacking the release itself. The official installer is trusted to resolve the newest release, download and unpack the archive for the architecture into the install directory, create the systemd unit, enable and start the service and install its dependencies; the task does not duplicate those steps. The single source of truth for the installer location is install_script_url in the task config.

The official installer runs non-interactively: XUI_NONINTERACTIVE=1 replaces every interactive prompt with an environment-variable value or a sane default. Alone among the service-install tasks, 3x-ui does not own a rendered configuration file: the panel stores its own state in a generated SQLite database, so on stage 1 there is nothing for the task to write or diff.

Before the installer runs, the task frees the fixed panel port and, when the SSL challenge is reachable (ssl_enabled and port 80 open to the internet), the ACME port 80: when the x-ui service owns a port, the service is stopped via systemctl; when an unknown process owns it, the process is terminated with SIGTERM and then SIGKILL after a grace period. The installer is then run with the proquint credentials, the panel port and the SSL mode in its environment (XUI_USERNAME, XUI_PASSWORD, XUI_WEB_BASE_PATH, XUI_PANEL_PORT, XUI_SSL_MODE).

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository, the endpoint https://api.github.com/repos/{github_repo}/releases/latest. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is a task error. The release query and the installer download run with the engine-wide curl settings: curl_timeout_seconds bounds one metadata query, curl_download_timeout_seconds bounds one file download, and curl_retries, curl_retry_delay_seconds, curl_connect_timeout_seconds and curl_retry_max_time_seconds shape the retry behaviour. The tag carries a leading v; the version comparison strips it on both sides.

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

In force mode on an existing panel the task rotates the credentials and webBasePath: it applies the fresh proquint values it generated for the installer directly through `x-ui setting -username -password -webBasePath`, rewrites install-result.env and restarts the panel, so stage 2 stores the new values in the runtime vault. This is the explicit permission to regenerate the panel identity; normal runs never touch it. A fresh install already receives the values from the installer environment, so no takeover is needed there.

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

Before creating, the task calls `GET /panel/api/inbounds/list` and searches for an inbound whose `port` matches the configured `inbound_port`. When not found, it creates the inbound in one call and reports `changed=True`.

When the inbound is already there, the stage never creates a second one. It still makes sure the inbound carries the REALITY data a client needs: when the nested public key block is missing, the panel issues a fresh key pair and the stage writes it with a single `POST /panel/api/inbounds/update/{id}`; the stage reports `changed=True` only when that write happened, and `changed=False` when the inbound is already complete. The `clients` array is created empty and is filled by stage 5.

### Key storage

The pair is issued by the panel through `GET /panel/api/server/getNewX25519Cert`: the private key is written into `realitySettings.privateKey` and the public key, together with the configured fingerprint, into the nested `realitySettings.settings` block. The panel renders `pbk` in a share link only when the public key sits in that nested block, so a key stored anywhere else produces a link a client cannot use.

The live pair is read back from the inbound by stage 5 and stored in the connection profile entry of the runtime vault. The credentials entry of the panel carries panel data only; it never carries REALITY keys.

## Stage 4: HTTPS certificate

When `ssl_enabled` is set, the panel must serve HTTPS, never plain HTTP. A trusted Let's Encrypt IP certificate is issued when the HTTP-01 challenge can reach the machine (installer option 2, the shortlived profile); when it cannot, the task generates a self-signed certificate instead, so the admin traffic over the internet stays encrypted.

The task attempts a trusted certificate only when the HTTP-01 challenge can reach the machine. A machine with a public address on an interface (a VPS) can serve the challenge directly; a machine behind NAT can only when the router forwards external port 80 back to it, which a self-test confirms by binding a temporary listener on port 80 and connecting to the detected public address. The self-test has its own budget, `probe_port_80_timeout_seconds`, separate from the query budget: a router that does not answer must delay the run by seconds, not by a minute, so the probe is cut short when the port is closed. The ACME port is forwarded through UPnP before the stage runs (the same `pyntara.upnp` helper and the same `upnp_enabled` switch the connection stage uses), so a router with UPnP gives a trusted certificate without a manual rule; a router without UPnP is a normal situation, the attempt logs a progress line and the self-test decides as before. On the install path a reachable machine receives `XUI_SSL_MODE=ip` in the installer environment and the task frees port 80 before the installer runs; a machine behind NAT without a forward receives `XUI_SSL_MODE=none` and the task generates a self-signed certificate after the install.

The self-signed certificate is generated with openssl into `self_signed_cert_dir`; the openssl package is installed through the shared helper when absent, so the setup never depends on a preinstalled package. The panel is pointed at the files through `x-ui cert -webCert -webCertKey` and restarted. The certificate is valid for 825 days; a rerun regenerates it only when the files are missing or expired, and never touches a certificate the task does not own.

On a rerun where the target state is reached and the installer is skipped, the stage queries `x-ui setting -getCert true` and does nothing when the panel already carries a trusted or foreign certificate. When the panel has none and the challenge is reachable, the stage detects the public IPv4 address from the same echo services the installer uses, frees port 80 and issues the certificate through acme.sh exactly like the installer's setup_ip_certificate: `--issue --standalone --httpport 80` with the shortlived profile, `--installcert` into `cert_dir` with a reload command that restarts x-ui, then `x-ui cert -webCert -webCertKey`. When the panel serves the task's self-signed certificate and port 80 has become reachable, the stage replaces it with a trusted one. A trusted certificate that cannot be obtained is reported honestly: the panel serves HTTPS with the self-signed certificate and the message tells how to get a trusted one (forward port 80 and re-run); the failure never fails the task.

After the installer runs (and on a rerun) the task brings the panel to the configured `panel_port`: it reads the actual port from `x-ui setting -show true` and, when an earlier install left it on another port, frees the target port, sets the new one and restarts the panel. It then syncs `/etc/x-ui/install-result.env` so its `XUI_PANEL_PORT` and `XUI_ACCESS_URL` carry the real port and the real scheme (http or https), so consumers never read a stale port or a scheme the panel does not serve.

## Progress reporting

The task reports every action through `log_progress` before it runs, so the log reads as a description of what is being done, not as a wall of commands: querying the release, reading the installed version, checking the UPnP client package, checking the ports, downloading and running the installer, waiting for the service, waiting for the panel HTTP listener, probing the ACME port, generating or issuing a certificate, moving the panel port, syncing the env file, and each vault step name their target and their result. Command lines from `run_command` follow these messages, so a command always has a stated purpose above it.

The final result line states the installer outcome and names every stage that changed something: the certificate stage, the panel port, the subscription paths, the inbound security data and the connection profile each append their own message. A rerun that reached the target state but rewrote the REALITY keys or the profile therefore reads as `target state already reached; inbound share data updated; connection profile stored` instead of claiming that nothing happened.

## Stage 5: client and connection profile

After stage 3 the task runs stage 5, which turns the node into a ready server: the inbound receives exactly one client and the runtime vault receives the complete connection profile of that client. Every step of this stage reports a warning instead of an error, so a panel that is briefly unreachable never fails an otherwise finished setup, and the profile is written only from values the panel itself reports.

The share address comes first. The panel renders the host of every share link from the `shareAddr` field of the inbound, but it uses that field only when `shareAddrStrategy` is the configured `custom` value: with the default strategy the panel renders `localhost`, which is useless to a client. The stage therefore resolves the host and sets both fields on the inbound.

The host is resolved in this order, and the first available source wins: a public address that really belongs to this machine, the router address when UPnP forwards the port, the yggdrasil node address, the local address of this machine, then the share address the panel already stores. The public addresses come from the shared `pyntara.public_address` helper: it queries all configured echo services in one parallel curl call, waits for every transfer, keeps the addresses that arrived and merges the repeats, so the same address reported by several services counts once and a slow service costs at most `server_ip_timeout_seconds` instead of hiding the other answers. The local addresses come from `ip -o addr show scope global`, and an address is treated as white only when it appears there: on a machine with a white address the server is reachable from the internet, and the code needs no router cooperation at all.

Every source of an address is queried once per run, before the stages that need it, and the result is passed to every stage as run facts. One parallel echo-service call, one local-address call and, when UPnP is enabled, one router query produce the facts: the public addresses, the local addresses, the router address and the client-usable address the port forwarding returned. A machine without UPnP therefore pays for exactly one failed router query per run instead of one per stage, which keeps a run on a router-less network short. A machine that owns a public address needs no forwarding at all: an address reported by an echo service belongs to the machine only when it also appears among the local addresses, and then the address is reachable directly, so the UPnP client package is not installed and the router is never asked.

On a machine behind NAT the provider address is not white, so the task asks the router through UPnP (`pyntara.upnp`, the external upnpc client). The task installs that client while collecting the facts, exactly like the openssl package it needs for the self-signed certificate; the helper module installs nothing and only runs the program it is given, so a machine without the client simply gets no forwarded port. The task forwards two ports through the router address already read for this run, so the router is never asked twice: the inbound port, so clients reach the node, and the ACME port when `ssl_enabled` is set, so a trusted certificate becomes possible. The inbound mapping is forwarded to the address of the default route and the returned address is used only when it matches the address the echo services see, because a different address means the provider runs another NAT above the router and the mapping forwards nothing. A router without UPnP or a refused mapping is a normal situation: the attempt is reported as a progress line, never as a warning, and the resolution continues. When nothing is reachable from the internet the yggdrasil node address comes next, because another node reaches it over the mesh, then the local address, which keeps the server usable inside the local network. IPv6 goes into `shareAddr` wrapped in square brackets, which is exactly the form the panel stores and renders, so a rerun compares equal and stays a no-op. When none of the sources answers, the panel is left as it is and the stage reports a warning.

The white address test and the UPnP attempt never decide whether the server works: the inbound, the client and the profile are configured either way, so a machine with a white address works out of the box and a machine behind NAT still serves its local network.

The client identity is reused from the vault entry when one is already there: `CLIENT_EMAIL`, `CLIENT_ID` and `SUB_ID` are read back, so a rerun never adds a second client to the inbound and the identity survives an update of the panel. On a fresh run the stage generates them: the email is a 2-word proquint, the client id a 4-word proquint with dashes (it becomes the client identity the panel stores) and the subId a 6-byte proquint without a separator. The client is created through `POST /panel/api/clients/add` together with the inbound id; the email is only a label in the panel, never the credential.

The profile is then built from the panel, never from local guesses: the canonical share link comes from `GET /panel/api/clients/links/{email}`, and the REALITY public key, the REALITY private key and the fingerprint come from the inbound that stage 3 produced. The link is written into the url field of the connection entry, the resolved address into the username field, and the parameters a client needs into the notes field as key=value lines: SERVER_ADDRESS, SERVER_PORT, DEST, SERVER_NAME, CLIENT_EMAIL, CLIENT_ID, SUB_ID, SHORT_ID, FINGERPRINT, REALITY_PUBLIC_KEY, REALITY_PRIVATE_KEY. The address is written without the brackets an IPv6 share address carries, because a client configuration takes it bare while the panel adds the brackets only when it renders a link.

The entry title comes from `connection_vault_entry_title` and must name an entry of the `[vault_structure]` table, which the config loader checks. When the entry already carries exactly that link and those notes, the stage reports done without writing, so a rerun is a no-op.

## Stage 6: local proxy inbound

Stage 6 makes this machine a client of the remote server whose canonical vless link sits in the source vault, in the entry named by `client_profile_entry_title` (`xray_client_profile`). The production vault wins over the default vault, exactly like the runtime vault build: the link is read through the shared `open_source_vault` helper, so one password rule and one vault reader exist in the project. An absent entry, an empty url, a url that is not a usable vless link and a source vault that does not open all end in the same honest place: a warning that the local proxy is not configured, and no change to the panel. The link is the single source of truth for the profile, because it already carries the REALITY public key, the short id and the spider path that the connection profile of the server entry repeats only partly.

No second Xray process is installed. The panel already runs an Xray core on this machine, so the client is an inbound of that core and the policy lives in the same template the panel uses for its own inbounds; the panel applies a written template to the running core at once. The inbound is created through `POST /panel/api/inbounds/add` with the tag from `local_proxy_tag`, and replaced through `POST /panel/api/inbounds/update/{id}` when the tag is already taken, so a rerun with another port or another sniffing set converges instead of failing on a duplicate tag. The panel keeps the routing tag of an inbound in its `tag` field and the human label in `remark`, and the rules match the tag, so the stage finds its inbound by tag and never by label. Writing a template whose tag the panel already stores is not enough on its own: the panel can rewrite its stored rules while the running core keeps the previous set, which is why stage 7 verifies the core (below).

The inbound is a `mixed` listener, because the panel has no plain `socks` protocol and `mixed` serves SOCKS5 and HTTP on one port, which is exactly a local proxy. It listens on `local_proxy_listen_address` (`127.0.0.1`) so the proxy stays on the machine, carries UDP when `local_proxy_udp` is set, needs no password, and is created with `expiryTime` 0 and `total` 0, which the panel reads as "no limit" and "no expiry": a local proxy that stops working after a quota is worse than useless. Sniffing is enabled for the configured protocols, because sniffing is what lets the rules decide by the requested name instead of by the address, and the country rules depend on names.

A machine that IS the remote server skips stages 6 and 7: the address of the link is compared with the addresses of the machine's interfaces and with the addresses the echo services reported, and a match means the machine must not connect to itself. The skip is a progress line, not a warning, and the machine keeps the routing of a plain server.

## Stage 7: routing policy

Stage 7 decides, for every connection that enters the local proxy, which outbound takes it. The rules are built by `pyntara.routing_policy`, a pure module: it takes the policy values and the Xray template and returns the updated template plus whether anything really differs, so the panel is written only when the document changed and a rerun is a no-op. The module owns only its own outbounds and its own rules: every other outbound, rule and section of the template is kept as it is, so a panel that grows a setting or an operator who edits the template by hand never loses work. The two outbound tags the panel ships (`direct` and `blocked`) are jumped to, and the panel's own restrictions are removed only on a machine that applies this policy: the block of the private range and of the bittorrent protocol, and the internal block rules of the direct outbound, would make the local proxy less useful than it can be. Removing them is safe because every rule of the policy is scoped to the local proxy inbound.

The rule order is fixed, and every name rule comes before the first address rule:

1. the configured advertising categories go to the panel blackhole, on every machine, so advertising and tracking endpoints are dropped;
2. `.onion` goes to the local tor SOCKS proxy and `.i2p` to the local i2pd HTTP proxy, because those are the only ways the names resolve at all;
3. the configured local domains go directly, because a remote server can never resolve them;
4. a machine in Russia: the names that answer only inside Russia go directly, and the names that refuse to serve Russia and the names blocked in Russia go through the remote server;
5. the configured address categories, the configured overlay networks and the subnets the kernel reports as directly connected go directly, so a local network, a bridge and the yggdrasil mesh never leave the machine; a machine in Russia then adds the Russian ranges as direct and the ranges blocked in Russia as proxied;
6. everything else goes through the remote server, or directly on a machine in Russia.

The name-first order is a requirement rather than a style, and a measurement stands behind it. A machine in Russia uses the routing domain strategy `IPIfNonMatch`, and the core resolves a name as soon as it reaches the first rule that carries addresses, in order to match them: the official documentation states it as "actual resolution behavior will be delayed until the first IP rule is encountered" (xtls.github.io, Routing, `domainStrategy`). A name that a name list already carries must therefore be decided before that point. Otherwise the address decides instead, and the address of a site blocked in Russia can fall into a range that is routed directly, which sends a site that must use the remote server straight into the block. Measured on a machine in Russia with the address rules first: the domain `api.openai.com` was routed to the remote server while the address that machine resolves for it was routed directly, and the service then answered with a refusal instead of an answer.

Outside Russia everything else goes through the remote server: the machine gets a complete tunnel with one rule, and the direct traffic is exactly the local traffic of rules 3 and 5. The routing domain strategy is `AsIs` there, so no name is resolved before the decision: resolving a name would hand it to the local resolver and cost a query, while the catch-all rule sends the connection to the remote server anyway.

In Russia the rest is split four ways: the names that answer only inside Russia go directly, because the remote server cannot serve them; the names of the resources blocked inside Russia go through the remote server, which sits outside the block; the names of the services that refuse to serve Russia on their own (the AI services, the streaming catalogues, the social networks that block Russian addresses) go through the remote server as well; and everything else goes directly, so ordinary traffic keeps the latency of the country of the machine. The address lists of the Russian datasets still apply, and the strategy `IPIfNonMatch` is what makes them apply: a name that no name list carries is resolved and then matched against the address lists, which is exactly the traffic those lists are meant to sort. A name that a name list carries never gets there.

### Country detection

The country comes from the configured services, all queried in one parallel curl call, so one slow service costs at most `country_query_timeout_seconds` and hides nothing. Every answer is standardized without losing anything: a JSON document is flattened with its paths, a `key=value` or `key: value` line becomes a named field, a `;`-separated record becomes one value per part, and anything else stays a plain value. The values of every answer are merged in arrival order with the repeats removed.

The machine is treated as being in Russia when the configured `country_word` appears in one of those values, compared after case folding and after every non-alphanumeric character has become a space: `Russia`, `russian federation` and `loc=Russia` all match, while `GRU`, `Peru` and `Belarus` do not. The search is a plain occurrence rather than a whole word, so `Prussia` matches as well; that is the accepted price of a rule that must also catch `Russian Federation`. A bare two-letter code is deliberately not the rule: the same shape appears as a colo code (`GRU` is Sao Paulo), so a code rule would place machines in Russia by accident. When no service answers or no answer carries the word, the machine is treated as outside Russia and the stage says so in the log: the catch-all rule then sends everything to the remote server, which is the useful default for a machine whose country nobody could confirm.

### Categories and verification

Every configured category token is checked against the geodata files the panel installed before it is applied (`POST /panel/api/xray/geodata/validate` with `kind=domain` for the site lists and `kind=ip` for the address lists). Those files are community maintained and a token the panel cannot resolve would make the whole Xray configuration invalid and take the panel down with it, so a rejected token is dropped from the policy and reported as a warning naming the token and the reason, and the rest of the policy is applied. Literal tokens (a `domain:` name, an address range) are accepted by the panel as they are and always survive. The task never updates the geodata files: the panel does that on demand, so a stale dataset gives stale decisions.

After the template is written, the stage asks the running core about one destination of every class (`POST /panel/api/xray/routeTest` with the domain or the address, the port, the network, the protocol and the inbound tag) and compares the answer with the outbound the policy intends: a hidden service must take the tor or the i2p outbound, an advertising domain must be dropped, a direct domain and an address of the machine's own networks must go directly, and the country decides the rest. The core answers from its own routing engine, so this is the only honest check that the policy reached the traffic. When a check disagrees, the stage writes the same template once more and asks again, which is what brings a core that kept an older rule set back; a disagreement that survives the second write is reported as a warning naming the destination, the expected outbound and the answer, and it never fails the task.

The path is then proven with real requests, and what counts as success follows from the policy. `proxy_check_url` is queried through the local proxy with the call configured as `tunnel_probe_command`, whose `{proxy_address}` and `{timeout_seconds}` the task fills and whose `--write-out` text is `tunnel_probe_write_out`, so the status code of the answer is read from the output: outside Russia the answer must be the address of the remote server, and on a machine in Russia it must be an address of this machine, because the policy sends an ordinary foreign name directly there. Each check is attempted twice before it is reported, because the path to the remote server can stall once and answer on the next attempt, which is a fact of the network rather than a reason to raise an alarm. On a machine in Russia a second request proves the tunnel itself: `proxy_check_blocked_url` is a destination of a class the policy sends through the remote server, and an HTTP answer is the evidence, because such a service does not report the address of its client. Every disagreement is reported as a warning that names the URL, what was expected and what came back, so a proxy that quietly falls back to a direct connection and a tunnel that carries nothing are both visible instead of trusted.

### Config reference

New fields in the `[three_x_ui_xray_setup]` table:  
`panel_port` (integer, optional, default `35353`): fixed panel port. Must be between 1 and 65535. Passed to the installer via XUI_PANEL_PORT and applied on first deployment; the task also brings an existing panel to this port when an earlier install left it on another port.  
`ssl_enabled` (boolean, optional, default `true`): enables the panel HTTPS setup. When false the installer runs without XUI_SSL_MODE and the panel stays HTTP; when true the panel serves HTTPS, a trusted Let's Encrypt IP certificate when port 80 is reachable and a self-signed certificate otherwise.  
`acme_port` (integer, optional, default `80`): the HTTP-01 challenge listener port, mirroring the installer's setup_ip_certificate layout.  
`cert_dir` (path, optional, default `/root/cert/ip`): directory of the trusted Let's Encrypt certificate; the config loader derives the fullchain and privkey paths from it.  
`self_signed_cert_dir` (path, optional, default `/root/cert/selfsigned`): directory of the self-signed fallback certificate.  
`server_ip_services` (array of strings, optional): echo services queried for the public addresses, all of them in one parallel call. A service that reports an IPv4 address, an IPv6 address or both is used as it answers; a service that stays silent costs at most the query timeout and hides nothing else.  
`server_ip_timeout_seconds` (integer, optional, default `60`): timeout of one echo-service query. Must be positive; the value must let a slow link answer, so a query is never cut off after a few seconds.  
`probe_port_80_timeout_seconds` (integer, optional, default `10`): timeout of the self-test that connects to the machine's own public address on the ACME port, used as both the connect timeout and the total time of that request. Must be positive; the probe has its own budget because a closed port must not delay the run for a minute. The listener stop keeps using `probe_timeout_seconds`.  
`upnp_enabled` (boolean, optional, default `true`): enables the UPnP port-forwarding attempt, so a machine behind a router with UPnP can be reached from the internet without a manual rule. A router without UPnP is a normal situation and is skipped quietly.  
`upnp_package` (string, optional, default `"miniupnpc"`): package that provides the upnpc client. The task checks it with the shared package check and installs it while collecting the run facts, before the stages that may forward a port, like its other packages; a machine where it cannot be installed keeps working without port forwarding.  
`upnp_client_command` (string, optional, default `"upnpc"`): name of the upnpc binary.  
`upnp_mapping_description` (string, optional, default `"pyntara xray"`): description of the created mapping, shown in the router interface.  
`panel_root_path`, `panel_login_path`, `panel_csrf_token_path`, `panel_inbounds_list_path`, `panel_inbounds_add_path`, `panel_inbounds_update_path`, `panel_inbounds_delete_path`, `panel_client_get_path`, `panel_client_add_path`, `panel_client_links_path`, `panel_x25519_cert_path`, `panel_setting_all_path`, `panel_setting_update_path`, `panel_xray_status_path`, `panel_xray_update_path`, `panel_xray_geodata_validate_path`, `panel_xray_route_test_path` (strings, required): paths of the panel REST API, relative to `panel_http_address`. The update, delete, client get and client links paths carry a `{placeholder}` for the value of one call, which the call site fills in. Every path is written as the panel serves it, so a panel version that renames an endpoint is answered in the config and not in the code, and a test that hands the client another path proves the client reads it.  
`inbound_port` (integer, required): TCP port for the VLESS+REALITY inbound. Must be between 1 and 65535.  
`inbound_remark` (string, optional, default `"universal"`): display label for the inbound in the panel.  
`reality_dest` (string, optional, default `"www.google.com:443"`): destination address and port for REALITY TLS handshake mimicry.  
`reality_server_names` (array of strings, optional, default `["www.google.com"]`): ServerNames the REALITY handshake presents.  
`reality_short_id` (string, optional, default `"6ba85179e30d4fc2"`): short ID for REALITY. Must be a hex string.  
`subscription_path` (string, optional, default `"/s/"`): panel subscription path, moved off the well-known default `"/sub/"` so the panel does not warn about it. Must start and end with a slash.  
`subscription_json_path` (string, optional, default `"/j/"`): JSON subscription path, moved off the well-known default `"/json/"` for the same reason. Must start and end with a slash.  
`subscription_clash_path` (string, optional, default `"/c/"`): Clash subscription path, moved off the well-known default `"/clash/"` for the same reason. Must start and end with a slash.  
`reality_fingerprint` (string, optional, default `"chrome"`): the uTLS fingerprint that the REALITY settings advertise to clients and that the panel renders in the share link. Must not be empty.  
`connection_vault_entry_title` (string, optional, default `"xray_connection"`): title of the runtime vault entry that carries the connection profile of the client. Must name an entry of the `[vault_structure]` table.  
`share_addr_strategy` (string, optional, default `"custom"`): how the panel picks the host of the share links; one of `node`, `listen`, `custom`, and only `custom` makes the panel use the address the task sets.  
`client_profile_entry_title` (string, optional, default `"xray_client_profile"`): title of the source vault entry whose url carries the canonical vless link of the remote server this machine connects to as a client. Must name an entry of the `[vault_structure]` table. An absent entry or an empty url means no profile is configured, and stages 6 and 7 then report a warning instead of changing the panel.  
`local_proxy_tag` (string, optional, default `"pyntara-local-proxy"`): tag of the local proxy inbound, used both as its routing tag and as its panel label. Must not contain whitespace and must differ from the outbound tags.  
`local_proxy_listen_address` (string, optional, default `"127.0.0.1"`): address the local proxy listens on. The loopback address keeps the proxy on this machine; any other address hands the remote server to whoever reaches that address.  
`local_proxy_port` (integer, optional, default `10800`): port of the local proxy, serving SOCKS5 and HTTP at once. Must be between 1 and 65535 and must differ from `panel_port` and `inbound_port`.  
`local_proxy_udp` (boolean, optional, default `true`): whether the local proxy carries UDP, which QUIC and DNS through the proxy need.  
`local_proxy_sniffing_protocols` (array of strings, optional, default `["http", "tls", "quic"]`): protocols the panel sniffs to learn the requested name.  
`remote_outbound_tag`, `tor_outbound_tag`, `i2p_outbound_tag` (strings, optional, defaults `"pyntara-remote"`, `"pyntara-tor"`, `"pyntara-i2p"`): tags of the outbounds the policy owns. They must all differ from each other and from `local_proxy_tag`, so a rerun can replace its own objects and no tag collides.  
`direct_outbound_tag`, `blocked_outbound_tag` (strings, optional, defaults `"direct"`, `"blocked"`): tags of the panel's own outbounds the policy jumps to, the freedom outbound and the blackhole.  
`tor_proxy_address`, `i2p_proxy_address` (strings, optional, defaults `"127.0.0.1:9050"`, `"127.0.0.1:4444"`): local SOCKS proxy of tor and local HTTP proxy of i2pd. Each must be an address:port value with a port between 1 and 65535.  
`ad_block_domain_categories` (array of strings, optional): domain categories of advertising and tracking endpoints, dropped on every machine.  
`direct_domains` (array of strings, optional): domain tokens that must go directly, the private name suffixes among them.  
`direct_ip_categories` (array of strings, optional): address categories that must go directly, the reserved and private range among them.  
`direct_ip_networks` (array of strings, optional): literal address ranges that must go directly, the overlay networks of this project among them. They are not categories, so they are applied as they are written.  
`country_services` (array of strings, optional): services queried in parallel for the country of this machine. A service that stays silent costs at most the query timeout and hides nothing else.  
`country_word` (string, optional, default `"russia"`): the word that decides that this machine is in Russia. Must be non-empty and must not contain digits, so a code never passes as a word.  
`country_query_timeout_seconds` (integer, optional, default `10`): timeout of one country service query. Must be positive.  
`country_command_timeout_seconds` (integer, optional, default `20`): timeout of the whole parallel country query. Must not be smaller than `country_query_timeout_seconds`.  
`russia_blocked_domain_categories`, `russia_blocked_ip_categories` (arrays of strings, optional): the resources blocked inside Russia, reached through the remote server from a machine in Russia.  
`russia_direct_domain_categories`, `russia_direct_ip_categories` (arrays of strings, optional): the resources that answer only inside Russia, reached directly from a machine in Russia.  
`geo_restricted_domain_categories` (array of strings, optional): the services that refuse to serve Russia on their own, reached through the remote server from a machine in Russia.  
`russia_domain_strategy`, `outside_russia_domain_strategy` (strings, optional, defaults `"IPIfNonMatch"`, `"AsIs"`): the routing domain strategy of the two country profiles; each must be one of `AsIs`, `IPIfNonMatch`, `IPOnDemand`.  
`route_check_ad_domain`, `route_check_foreign_domain`, `route_check_onion_domain`, `route_check_i2p_domain`, `route_check_direct_domain`, `route_check_russia_blocked_domain` (strings, optional): the destinations the task asks the running core about after applying the policy, one per class. The expectations follow from the configuration, so a disagreeing answer means the core did not take the policy.  
`proxy_check_url` (string, optional): the URL queried through the local proxy to prove where its traffic leaves; the expected answer is the remote server outside Russia and this machine in Russia, because there the policy routes an ordinary foreign name directly.  
`proxy_check_blocked_url` (string, optional, default `"https://api.openai.com/v1/models"`): the URL queried through the local proxy on a machine in Russia only, of a class the policy sends through the remote server, a resource blocked in Russia or a service that refuses to serve it. An HTTP answer is the evidence that the tunnel carries that class.  
`proxy_check_timeout_seconds` (integer, optional, default `20`): timeout of one request of the two checks above, used as both the connection timeout and the total time of the request, so a stalling tunnel is reported after a predictable wait. Must be positive.  
`proxy_check_command_timeout_seconds` (integer, default `50`): outer timeout of one tunnel check call, so curl reports its own give-up before the run kills the process.  
`port_forward_probe_command`, `port_forward_probe_url_format`, `panel_probe_command`, `tunnel_probe_command` and `tunnel_probe_write_out`: the three probe calls of the task and the pieces they are built from. The port 80 self-test asks the machine's own public address through the forward under test, with the URL composed from `port_forward_probe_url_format` and `{host}` plus `{port}`; the panel check only asks whether the listener answers, with `--insecure` because the certificate may be self-signed; the tunnel check reports the status code through `tunnel_probe_write_out`, which is a value of its own because curl reads its fields as `%{name}` and a command template is a format string.

### Limitations

One node receives one client, not one client per person: every connection to the node shares a single identity, so the panel statistics do not separate users and revoking one person means rotating the identity for everyone. Managing a client per user, per-user limits and per-user subscription links are outside the scope of this task.

The node stores a connection profile in the runtime vault and the same task consumes the profile of the remote server: stages 6 and 7 read the vless link of the source vault entry named by `client_profile_entry_title` and configure this machine as a client of that server, so the server half and the client half are one task with one config. The machine that runs the server skips the client stages, because its link points at itself.

The routing decisions rest on community maintained data: the geoip and geosite files, and the country lists of the profile in use. The task verifies a token before it uses it and never updates a file, so a dataset the panel has not refreshed gives decisions of the day it was built, and a name the list does not carry yet is routed by the catch-all rule.

The country rule is a word search over what the services answer: a machine that reaches the internet through a VPN, a proxy or another country's exit is placed by the country of that exit, and the configured word is the only way to move the boundary.

The local proxy has no authentication because it listens on the loopback address, where only programs of this machine can reach it. Binding `local_proxy_listen_address` to another address hands the remote server to every machine that reaches that address.

The client outbound is a plain vless client: it carries no fallback, no balancer and no observatory, because the project configures exactly one remote server. A machine that must survive the failure of that server needs a second entry and a policy for it, which is not part of this task.

The inbound is created with `enable: true` and starts accepting connections immediately after the panel applies the configuration.

The SSL stage needs a public IPv4 address, because a Let's Encrypt IP certificate is issued for an IPv4 address: a machine whose public address is IPv6 only keeps its self-signed certificate, even though its client links can use the IPv6 address.
