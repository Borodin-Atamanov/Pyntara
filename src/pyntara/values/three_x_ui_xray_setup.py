"""Values of the 3x-ui panel task and of the modules that drive the panel.

The task wraps the official 3x-ui installer and then drives the running panel
over its HTTP API: the deployment, the credentials, the TLS certificate, the
universal inbound, the local proxy of this machine and the routing policy of
that proxy. Every path of that API, every field name of its answers and every
word of the vocabulary the panel accepts are declared here, so a panel that
answers with another schema is met by a change of this module and not of the
code that talks to it.

The fields of the JSON bodies the panel exchanges stay booleans (CLIENT_ENABLED,
the sniffing switches and the observatory switch), because the panel
distinguishes true from 1 in JSON; the switches that only drive this task
(SSL_ENABLED, UPNP_ENABLED) answer 1 and 0, the project convention for a switch.
"""

from __future__ import annotations

from pathlib import Path

# 3x-ui Xray panel installation parameters (docs/spec/3x-ui.md).
# The task wraps the official 3x-ui installer: it compares the installed
# version with the newest release and runs the official install.sh in
# non-interactive mode only when the version differs, the service is not
# in the target state, or the task is forced.
# GitHub repository of 3x-ui, owner/name form used by the releases API.
GITHUB_REPO: str = "MHSanaei/3x-ui"

# Whether the client the task creates in the panel is enabled. A client
# exists to be used, so it ships enabled; a client created as a draft is
# a legitimate staging step, which is why the flag is a value.
CLIENT_ENABLED: bool = True

# URL of the official installer script the task downloads and runs.
INSTALL_SCRIPT_URL: str = "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh"

# Directory holding the x-ui binary, where the installed version is read.
INSTALL_DIR: Path = Path("/usr/local/x-ui")

# Identity of the installed panel: the file name of its binary inside
# install_dir, which the task runs for every version, setting and
# certificate call, and the process name the port helpers look for when
# they have to free a port.
BINARY_FILE_NAME: str = "x-ui"
SERVICE_PROCESS_NAME: str = "x-ui"

# Commands of the panel CLI. Every one carries the path of the binary as
# {binary}; a value the call site knows (the port, the credentials, the
# certificate paths) travels as its own placeholder, so the verbs and the
# flags of the tool are declared values like the binary itself.
PANEL_VERSION_COMMAND: tuple[str, ...] = ("{binary}", "-v",)
PANEL_SETTINGS_QUERY_COMMAND: tuple[str, ...] = ("{binary}", "setting", "-show", "true",)
PANEL_CERT_QUERY_COMMAND: tuple[str, ...] = ("{binary}", "setting", "-getCert", "true",)
PANEL_PORT_COMMAND: tuple[str, ...] = ("{binary}", "setting", "-port", "{port}",)
PANEL_CREDENTIALS_COMMAND: tuple[str, ...] = (
        "{binary}",
        "setting",
        "-username",
        "{username}",
        "-password",
        "{password}",
        "-webBasePath",
        "{web_base_path}",
    )
PANEL_CERTIFICATE_COMMAND: tuple[str, ...] = (
        "{binary}",
        "cert",
        "-webCert",
        "{fullchain}",
        "-webCertKey",
        "{privkey}",
    )

# Commands of the tools the task drives next to the panel CLI: the
# downloaded installer script, the acme.sh installer and its steps, the
# temporary HTTP listener of the port probe, the service restart and the
# two openssl calls that check and generate a certificate. A placeholder
# carries a value the call site knows; the number of days a certificate
# lives is part of the template, so it lives in this table as well.
INSTALLER_RUN_COMMAND: tuple[str, ...] = ("bash", "{script_path}",)
ACME_INSTALL_COMMAND: tuple[str, ...] = ("bash", "-c", "curl -s https://get.acme.sh | sh",)
ACME_DIR_RELATIVE_PATH: str = ".acme.sh"
ACME_FILE_NAME: str = "acme.sh"
ACME_PORT_LISTENER_COMMAND: tuple[str, ...] = ("python3", "-m", "http.server", "{port}", "--bind", "0.0.0.0",)
ACME_SET_DEFAULT_CA_COMMAND: tuple[str, ...] = ("{acme}", "--set-default-ca", "--server", "letsencrypt", "--force",)
ACME_ISSUE_COMMAND: tuple[str, ...] = (
        "{acme}",
        "--issue",
        "-d",
        "{domain}",
        "--standalone",
        "--server",
        "letsencrypt",
        "--certificate-profile",
        "shortlived",
        "--days",
        "6",
        "--httpport",
        "{http_port}",
        "--force",
    )
ACME_INSTALLCERT_COMMAND: tuple[str, ...] = (
        "{acme}",
        "--installcert",
        "--force",
        "-d",
        "{domain}",
        "--key-file",
        "{key_file}",
        "--fullchain-file",
        "{fullchain_file}",
        "--reloadcmd",
        "{reload_command}",
    )
ACME_UPGRADE_COMMAND: tuple[str, ...] = ("{acme}", "--upgrade", "--auto-upgrade",)
ACME_RELOAD_COMMAND: str = "systemctl restart {service_unit_name} 2>/dev/null || true"
OPENSSL_CHECK_COMMAND: tuple[str, ...] = ("openssl", "x509", "-in", "{fullchain}", "-noout", "-checkend", "0",)
OPENSSL_GENERATE_COMMAND: tuple[str, ...] = (
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "825",
        "-subj",
        "{subject}",
        "-keyout",
        "{key_file}",
        "-out",
        "{fullchain_file}",
    )
OPENSSL_SUBJECT_TEMPLATE: str = "/CN={subject}"
SERVICE_RESTART_COMMAND: tuple[str, ...] = ("systemctl", "restart", "{service_unit_name}",)

# Name of the systemd service unit the official installer creates.
SERVICE_UNIT_NAME: str = "x-ui.service"

# Timeout in seconds of one readiness probe: the curl call that checks
# the panel HTTP listener. A short value reports a working service as
# unreachable on a slow link, so the probe is generous.
PROBE_TIMEOUT_SECONDS: int = 60

# Timeout in seconds of the self-test that asks whether external port 80
# reaches this machine. The probe talks to the machine's own public
# address, so a dropped packet means no port forward and waiting longer
# only wastes time: this probe is deliberately shorter than a request to
# a real server.
PROBE_PORT_80_TIMEOUT_SECONDS: int = 10

# Seconds to wait after starting the temporary listener of the port 80
# self-test, so the listener is bound before the probe call runs.
PROBE_LISTENER_START_SECONDS: int = 1

# The probe calls of the task, each a curl command whose {timeout_seconds}
# placeholder is filled with the timeout of that probe:
# port_forward_probe_command asks the machine's own public address through
# the port forward under test, panel_probe_command only asks whether the
# panel listener answers, and tunnel_probe_command reports the HTTP status
# of the checked URL through the local proxy. The arguments of a probe are
# ours to choose, so they live here with the timeouts above.
PORT_FORWARD_PROBE_COMMAND: tuple[str, ...] = (
        "curl",
        "--silent",
        "--connect-timeout",
        "{timeout_seconds}",
        "--max-time",
        "{timeout_seconds}",
    )

# URL format of the port forward self-test: {host} is the public address
# the probe reaches and {port} the port the forward must deliver.
PORT_FORWARD_PROBE_URL_FORMAT: str = "http://{host}:{port}/"
PANEL_PROBE_COMMAND: tuple[str, ...] = (
        "curl",
        "--silent",
        "--max-time",
        "{timeout_seconds}",
        "--insecure",
        "--output",
        "/dev/null",
        "--header",
        "X-Requested-With: XMLHttpRequest",
    )
TUNNEL_PROBE_COMMAND: tuple[str, ...] = (
        "curl",
        "--silent",
        "--show-error",
        "--proxy",
        "{proxy_address}",
        "--connect-timeout",
        "{timeout_seconds}",
        "--max-time",
        "{timeout_seconds}",
        "--write-out",
        "{write_out}",
    )

# Write-out text of the tunnel probe: the HTTP status code of the answer.
# It is a value of its own and not part of the command above, because curl
# reads its fields as %{name} and the command template is a format string.
TUNNEL_PROBE_WRITE_OUT: str = "\n%{http_code}"

# Code curl reports through that write-out when no answer arrived at all. It
# is a word of the tool and not a status of the peer, so a check compares
# against it instead of a number written in the code.
TUNNEL_PROBE_NO_ANSWER_CODE: str = "000"

# Pause in seconds between two readiness checks of this task: the service
# after the installer, the panel HTTP listener after the panel restarted,
# and the panel core after a template write. A check is one cheap call, so
# a short pause keeps a machine that answers at once responsive.
READINESS_CHECK_DELAY_SECONDS: int = 1

# Seconds the service unit may take to report active after the installer
# ran. The unit can report activating for a moment, and a slow machine
# needs a longer budget: the wait is seconds and not a count of checks,
# because a count of checks with a fixed pause is a hidden fixed sleep.
SERVICE_START_WAIT_SECONDS: int = 60

# Seconds the panel HTTP listener may take to answer after the panel
# restarted, which a port change, a certificate or an install causes. The
# listener trails the systemd active state, and a slow machine takes
# longer to bind it.
PANEL_LISTENER_WAIT_SECONDS: int = 60

# Seconds the panel core may take to answer a routing question after a
# template write. Writing the template reconciles the running core: the
# panel applies the change through the core API when the change allows it
# and restarts the core otherwise, and it answers the write before a
# restart has finished. The core then loads its geodata files and binds
# its listeners, which took nine seconds on an eight-core machine, so a
# slow machine raises this value; the wait is what keeps a booting core
# from being read as a wrong routing policy.
CORE_READY_WAIT_SECONDS: int = 120

# Path to the install-result.env file the panel writes on first start.
# Stage 2 reads the generated credentials from this file.
INSTALL_RESULT_ENV_PATH: Path = Path("/etc/x-ui/install-result.env")

# Name of the JSON template of the VLESS+REALITY inbound payload, read from
# task_data/three_x_ui_xray_setup/ of the clone. The template carries the
# fields and the protocol words of the panel API, so a panel version that
# renames a field is answered in the template; every $placeholder is filled
# with a JSON value by the task.
INBOUND_PAYLOAD_TEMPLATE_FILE_NAME: str = "vless_reality_inbound.json"

# Length in bytes of the random part of every generated panel credential,
# which proquint encodes into the value: the username and the client email
# take random_username_bytes, the password, the web base path and the
# client id take random_secret_bytes, the subscription id takes
# random_sub_id_bytes.
RANDOM_USERNAME_BYTES: int = 4
RANDOM_SECRET_BYTES: int = 8
RANDOM_SUB_ID_BYTES: int = 6

# Fixed panel port passed to the installer via XUI_PANEL_PORT. The
# installer applies it on first deployment; on an existing panel with
# custom credentials it preserves the current port.
PANEL_PORT: int = 35353

# Enable Let's Encrypt IP certificate setup (installer option 2). When
# true the task passes XUI_SSL_MODE=ip to the installer, frees the ACME
# port before SSL setup, and on a rerun issues the certificate through
# acme.sh when the panel has none. When false the panel stays HTTP.
SSL_ENABLED: int = 1

# HTTP address of the panel for REST API calls. 127.0.0.1 is the local
# machine; the panel listens on localhost and on the public interface.
PANEL_HTTP_ADDRESS: str = "127.0.0.1"

# Seconds one panel REST API call may take before it is reported as a
# failure of that call. The budget of a step is the engine command budget,
# which is hours, and a panel that accepts the connection and then never
# answers must not spend it: the longest legitimate answer of the panel is
# its own reconciliation of a template write, which took nine seconds on
# an eight-core machine, so a slow machine raises this value.
PANEL_API_TIMEOUT_SECONDS: int = 120

# Paths of the panel REST API, relative to panel_http_address: the login
# session, the inbound, client, server and settings endpoints, and the
# xray endpoints the task uses. A path is written as the panel serves it,
# with {placeholders} for the values of one call, which the call site
# fills in.
PANEL_ROOT_PATH: str = "/"
PANEL_LOGIN_PATH: str = "/login"
PANEL_CSRF_TOKEN_PATH: str = "/csrf-token"
PANEL_INBOUNDS_LIST_PATH: str = "/panel/api/inbounds/list"
PANEL_INBOUNDS_ADD_PATH: str = "/panel/api/inbounds/add"
PANEL_INBOUNDS_UPDATE_PATH: str = "/panel/api/inbounds/update/{inbound_id}"
PANEL_INBOUNDS_DELETE_PATH: str = "/panel/api/inbounds/del/{inbound_id}"
PANEL_CLIENT_GET_PATH: str = "/panel/api/clients/get/{email}"
PANEL_CLIENT_ADD_PATH: str = "/panel/api/clients/add"
PANEL_CLIENT_LINKS_PATH: str = "/panel/api/clients/links/{email}"
PANEL_X25519_CERT_PATH: str = "/panel/api/server/getNewX25519Cert"
PANEL_SETTING_ALL_PATH: str = "/panel/api/setting/all"
PANEL_SETTING_UPDATE_PATH: str = "/panel/api/setting/update"
PANEL_XRAY_STATUS_PATH: str = "/panel/api/xray/"
PANEL_XRAY_UPDATE_PATH: str = "/panel/api/xray/update"
PANEL_XRAY_GEODATA_VALIDATE_PATH: str = "/panel/api/xray/geodata/validate"
PANEL_XRAY_ROUTE_TEST_PATH: str = "/panel/api/xray/routeTest"

# Endpoints of the outbound subscriptions and of the load balancer: the
# subscription the sotavpn_setup task creates fetches the server list of
# the Sotavpn bridge, and the balancer status answers which member of the
# pool the running core currently picks. The item and refresh paths carry
# a {subscription_id} placeholder where the panel expects the id of one
# subscription.
PANEL_OUTBOUND_SUBS_PATH: str = "/panel/api/xray/outbound-subs"
PANEL_OUTBOUND_SUBS_ITEM_PATH: str = "/panel/api/xray/outbound-subs/{subscription_id}"
PANEL_OUTBOUND_SUBS_REFRESH_PATH: str = "/panel/api/xray/outbound-subs/{subscription_id}/refresh"
PANEL_BALANCER_STATUS_PATH: str = "/panel/api/xray/balancerStatus"

# Endpoints the task reads to name why the core of the panel does not
# answer: the live state of the core service and the last line the core
# process printed. A warning about a core that never answered quotes
# them, so an operator on the target machine reads the state of the panel
# and the words of the core instead of a guess.
PANEL_STATUS_PATH: str = "/panel/api/server/status"
PANEL_XRAY_RESULT_PATH: str = "/panel/api/xray/getXrayResult"

# Fields of the status answer that carry the core: the block the panel
# groups it in, the state it reports there and the error it carries
# beside that state.
PANEL_STATUS_KEYS: dict[str, str] = {"xray": "xray", "state": "state", "error_msg": "errorMsg"}

# Vocabulary of the panel objects the local proxy is built from: the
# protocol of the inbound the policy creates (the panel has no plain
# socks inbound, mixed serves SOCKS5 and HTTP on one port), the rule
# protocols that count as a panel restriction the policy removes, and
# the address category the panel ships to block every private
# destination.
PANEL_INBOUND_PROTOCOL: str = "mixed"
PANEL_BLOCKED_RULE_PROTOCOLS: tuple[str, ...] = ("bittorrent",)
PANEL_PRIVATE_BLOCK_CATEGORY: str = "geoip:private"

# Vocabulary of the panel geodata check the task sends before a routing
# policy is applied: the kind of a domain token and the kind of an address
# token, exactly as the panel expects them in the request form. The
# sniffing protocols are what the panel is asked to detect on the
# universal inbound, so the routing rules can decide by the requested
# name.
PANEL_GEODATA_DOMAIN_KIND: str = "domain"
PANEL_GEODATA_IP_KIND: str = "ip"
INBOUND_SNIFFING_PROTOCOLS: tuple[str, ...] = ("http", "tls",)

# Vocabulary of the panel HTTP interface, exactly as the panel serves and
# expects it: the header names, the values of those headers, the methods
# of the calls, the schemes of the panel URL, the keys of the
# install-result.env file the panel writes on first start, the keys of
# its JSON answers, and the field names of the request bodies the task
# sends. A panel version that renames a field is answered here and not in
# the code.
PANEL_HTTP_HEADERS: dict[str, str] = {
        "content_type": "Content-Type",
        "csrf_token": "X-CSRF-Token",
        "requested_with": "X-Requested-With",
        "referer": "Referer",
        "authorization": "Authorization",
    }
PANEL_HTTP_HEADER_VALUES: dict[str, str] = {
        "json": "application/json",
        "form": "application/x-www-form-urlencoded",
        "xml_http_request": "XMLHttpRequest",
        "bearer_prefix": "Bearer ",
    }
PANEL_HTTP_METHODS: dict[str, str] = {"post": "POST", "get": "GET"}
PANEL_URL_SCHEMES: dict[str, str] = {"http": "http", "https": "https"}
PANEL_ENVIRONMENT_KEYS: dict[str, str] = {
        "username": "XUI_USERNAME",
        "password": "XUI_PASSWORD",
        "panel_port": "XUI_PANEL_PORT",
        "web_base_path": "XUI_WEB_BASE_PATH",
        "scheme": "XUI_SCHEME",
        "api_token": "XUI_API_TOKEN",
        "db_type": "XUI_DB_TYPE",
        "access_url": "XUI_ACCESS_URL",
        "noninteractive": "XUI_NONINTERACTIVE",
    }
PANEL_ANSWER_KEYS: dict[str, str] = {
        "success": "success",
        "payload": "obj",
        "message": "msg",
        "token": "token",
        "reason": "reason",
    }
PANEL_FIELD_KEYS: dict[str, str] = {
        "username": "username",
        "password": "password",
        "id": "id",
        "email": "email",
        "enable": "enable",
        "sub_id": "subId",
        "inbound_ids": "inboundIds",
        "client": "client",
        "tag": "tag",
        "port": "port",
        "protocol": "protocol",
        "network": "network",
        "inbound_tag": "inboundTag",
        "outbound_tag": "outboundTag",
        "matched": "matched",
        "domain": "domain",
        "ip": "ip",
        "kind": "kind",
        "tokens": "tokens",
        "token": "token",
        "private_key": "privateKey",
        "public_key": "publicKey",
        "sub_path": "subPath",
        "sub_json_path": "subJsonPath",
        "sub_clash_path": "subClashPath",
        "xray_setting": "xraySetting",
        "outbound_test_url": "outboundTestUrl",
        "subscription_remark": "remark",
        "subscription_url": "url",
        "subscription_tag_prefix": "tagPrefix",
        "subscription_enabled": "enabled",
        "subscription_update_interval": "updateInterval",
        "subscription_allow_private": "allowPrivate",
        "subscription_allow_insecure": "allowInsecure",
        "subscription_prepend": "prepend",
        "subscription_outbound_count": "outboundCount",
        "subscription_last_error": "lastError",
        "balancer_status_query": "tags",
        "balancer_running": "running",
        "balancer_override": "override",
        "balancer_selected": "selected",
    }

# Vocabulary of the Xray document the panel stores: the field names and the
# values the policy writes into it. A core version that renames a field or
# a protocol word is answered here and not in the code.
XRAY_FIELD_KEYS: dict[str, str] = {
        "tag": "tag",
        "protocol": "protocol",
        "settings": "settings",
        "stream_settings": "streamSettings",
        "network": "network",
        "security": "security",
        "reality_settings": "realitySettings",
        "server_name": "serverName",
        "fingerprint": "fingerprint",
        "public_key": "publicKey",
        "short_id": "shortId",
        "private_key": "privateKey",
        "spider_x": "spiderX",
        "vnext": "vnext",
        "address": "address",
        "port": "port",
        "users": "users",
        "id": "id",
        "encryption": "encryption",
        "flow": "flow",
        "servers": "servers",
        "remark": "remark",
        "listen": "listen",
        "enable": "enable",
        "expiry_time": "expiryTime",
        "total": "total",
        "up": "up",
        "down": "down",
        "auth": "auth",
        "udp": "udp",
        "ip": "ip",
        "sniffing": "sniffing",
        "enabled": "enabled",
        "dest_override": "destOverride",
        "metadata_only": "metadataOnly",
        "route_only": "routeOnly",
        "type": "type",
        "inbound_tag": "inboundTag",
        "outbound_tag": "outboundTag",
        "domain": "domain",
        "outbounds": "outbounds",
        "routing": "routing",
        "rules": "rules",
        "domain_strategy": "domainStrategy",
        "final_rules": "finalRules",
        "observatory": "observatory",
        "balancers": "balancers",
        "subject_selector": "subjectSelector",
        "probe_url": "probeUrl",
        "probe_interval": "probeInterval",
        "enable_concurrency": "enableConcurrency",
        "strategy": "strategy",
        "selector": "selector",
        "fallback_tag": "fallbackTag",
        "balancer_tag": "balancerTag",
        "share_addr": "shareAddr",
        "share_addr_strategy": "shareAddrStrategy",
    }
XRAY_VALUES: dict[str, str] = {
        "vless": "vless",
        "reality": "reality",
        "none": "none",
        "tcp": "tcp",
        "socks": "socks",
        "http": "http",
        "noauth": "noauth",
        "field": "field",
        "api_tag": "api",
        "onion_domain": "domain:.onion",
        "i2p_domain": "domain:.i2p",
        "least_ping": "leastPing",
    }

# Names of the query parameters of a vless share link, as the panel writes
# them into the link it hands out.
VLESS_LINK_QUERY_KEYS: dict[str, str] = {
        "security": "security",
        "public_key": "pbk",
        "fingerprint": "fp",
        "short_id": "sid",
        "server_name": "sni",
        "spider_x": "spx",
        "flow": "flow",
        "network": "type",
    }

# Title of the runtime vault entry that stores the panel credentials.
# The entry must exist in the [vault_structure] table.
VAULT_ENTRY_TITLE: str = "three_x_ui_credentials"

# Title of the runtime vault entry that stores the server connection
# profile: the client identity, the REALITY keys and the share link.
# The entry must exist in the [vault_structure] table.
CONNECTION_VAULT_ENTRY_TITLE: str = "xray_connection"

# Share-address strategy the panel uses when it renders client links,
# one of node, listen or custom. Only custom makes the panel use the
# address the task writes into shareAddr; the default node renders the
# link host as localhost.
SHARE_ADDR_STRATEGY: str = "custom"

# Stage 3: universal server inbound parameters.
# Port for the VLESS+REALITY inbound. Required.
INBOUND_PORT: int = 443

# Port the routing check knocks on, for every destination class: a port
# nothing listens on, so the answer is the outbound the policy chose and
# never a real connection.
ROUTE_TEST_PORT: int = 443

# Transport and security the routing check declares for the destination it
# asks about. The core answers for that network and that protocol, so a
# check of a TLS destination asks with tls.
ROUTE_TEST_NETWORK: str = "tcp"
ROUTE_TEST_PROTOCOL: str = "tls"

# Port of a remote share link that carries none. Optional, default 443.
REMOTE_LINK_DEFAULT_PORT: int = 443

# Remark for the inbound. Optional, default "universal".
INBOUND_REMARK: str = "universal"

# REALITY destination address:port for TLS handshake mimicry.
# Optional, default "www.google.com:443".
REALITY_DEST: str = "www.google.com:443"

# REALITY serverNames list. Optional, default ["www.google.com"].
REALITY_SERVER_NAMES: tuple[str, ...] = ("www.google.com",)

# REALITY shortIds entry. Optional, default is a random 8-hex-char string.
REALITY_SHORT_ID: str = "6ba85179e30d4fc2"

# TLS fingerprint the REALITY client presents; the panel writes it into
# the share link as fp=<value>.
REALITY_FINGERPRINT: str = "chrome"

# Panel subscription path. The panel warns about the well-known default
# "/sub/", so the task writes its own short path. Must start and end
# with a slash.
SUBSCRIPTION_PATH: str = "/s/"

# JSON subscription path, changed from the well-known default "/json/"
# for the same reason. Must start and end with a slash.
SUBSCRIPTION_JSON_PATH: str = "/j/"

# Clash subscription path, changed from the well-known default "/clash/"
# for the same reason. Must start and end with a slash.
SUBSCRIPTION_CLASH_PATH: str = "/c/"

# ACME HTTP-01 listener port and the certificate location mirror the
# official installer's setup_ip_certificate layout, so the task and the
# installer produce the same files.
ACME_PORT: int = 80
CERT_DIR: Path = Path("/root/cert/ip")

# Certificate pair of the panel. The panel and acme.sh take the two paths
# explicitly, and the names inside the directory are the ones acme.sh
# writes, so they stay here beside the directory.
CERT_FULLCHAIN_PATH: Path = CERT_DIR / "fullchain.pem"
CERT_PRIVKEY_PATH: Path = CERT_DIR / "privkey.pem"

# Self-signed fallback certificate location used when the Let's Encrypt
# challenge cannot reach the machine (behind NAT without a port-80
# forward), so the panel still serves HTTPS instead of plain HTTP.
SELF_SIGNED_CERT_DIR: Path = Path("/root/cert/selfsigned")
SELF_SIGNED_CERT_FULLCHAIN_PATH: Path = SELF_SIGNED_CERT_DIR / "fullchain.pem"
SELF_SIGNED_CERT_PRIVKEY_PATH: Path = SELF_SIGNED_CERT_DIR / "privkey.pem"

# File modes of the certificate pair the panel serves, in the octal form
# os.chmod takes: the private key stays readable to its owner only, the
# full chain is world readable. Both the Let's Encrypt pair and the
# self-signed pair use them.
CERT_PRIVKEY_FILE_MODE: int = 0o600
CERT_FULLCHAIN_FILE_MODE: int = 0o644

# Timeout in seconds of a single echo-service query used to detect the
# public IPv4 address (curl --max-time). A short value makes the
# detection fail on a slow link, so the timeout lets a slow answer
# arrive instead of discarding the address.
SERVER_IP_TIMEOUT_SECONDS: int = 60

# Echo services that report the public IPv4 address, in the order the
# official installer tries them.
SERVER_IP_SERVICES: tuple[str, ...] = (
        "https://api4.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://v4.api.ipinfo.io/ip",
        "https://ipv4.myexternalip.com/raw",
        "https://4.ident.me",
        "https://check-host.net/ip",
    )

# UPnP port forwarding through the miniupnpc client. When the machine
# sits behind a router with UPnP IGD, the task asks the router to forward
# the inbound port and then uses the router internet address for client
# links, so a machine behind NAT becomes reachable from the internet
# without a manual rule. A router without UPnP is a normal situation:
# the attempt is skipped quietly and the address falls back further.
UPNP_ENABLED: int = 1

# Package that provides the upnpc client.
UPNP_PACKAGE: str = "miniupnpc"

# Name of the upnpc binary the task runs.
UPNP_CLIENT_COMMAND: str = "upnpc"

# Protocol of the forwarding mappings the task asks the router for; the
# client sends it as the protocol of the rule.
UPNP_PROTOCOL: str = "TCP"

# Description of the created mapping, shown in the router interface so the
# user can tell which rule belongs to Pyntara, and read back from the router
# as the ownership mark of a rule. The {hostname} placeholder is replaced by
# the machine hostname, so the rules of two machines of this project on one
# router stay apart and a machine never takes the rule of its neighbour.
UPNP_MAPPING_DESCRIPTION: str = "pyntara xray {hostname}"

# Stage 6: make this machine a client of the remote server through its own
# panel. The panel already runs Xray on the machine, so the client is an
# inbound of that panel plus the outbounds and rules that tell its core
# where each connection goes; no second Xray process is installed.
# Title of the vault entry holding the canonical vless link of the remote
# server. The entry is read from the source vault, production first, and
# must exist in the [vault_structure] table. An absent entry or an empty
# url means no profile is configured: the stage reports a warning and
# leaves the panel alone.
CLIENT_PROFILE_ENTRY_TITLE: str = "xray_client_profile"

# Tag of the local proxy inbound, also used as its panel label. The
# routing rules are scoped to this tag, so it must differ from the tags of
# the other inbounds.
LOCAL_PROXY_TAG: str = "pyntara-local-proxy"

# Address the local proxy listens on. 127.0.0.1 keeps the proxy out of
# reach of the local network: the client is this machine alone.
LOCAL_PROXY_LISTEN_ADDRESS: str = "127.0.0.1"

# The IPv4 networks that count as private, in CIDR notation: an own address
# inside one of them means the machine sits behind NAT, so the Let's Encrypt
# HTTP-01 challenge on acme_port only succeeds when the router forwards it.
# The set is the RFC1918 private range and is a value like any other,
# because a machine behind carrier-grade NAT may need 100.64.0.0/10 added.
PRIVATE_IPV4_NETWORKS: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",)

# Port of the local proxy. The panel mixes SOCKS5 and HTTP on one port, so
# a program points at the same number whether it speaks SOCKS5 or HTTP.
LOCAL_PROXY_PORT: int = 10800

# Whether the local proxy carries UDP. QUIC and DNS through the proxy need
# it; a program that only speaks TCP is unaffected either way.
LOCAL_PROXY_UDP: bool = True

# Protocols the panel sniffs to learn the requested name. Sniffing is what
# lets the rules decide by name instead of by address, and a name that the
# remote server can resolve is what makes the country rules work.
LOCAL_PROXY_SNIFFING_PROTOCOLS: tuple[str, ...] = ("http", "tls", "quic",)

# Whether the local proxy inbound is enabled in the panel. A local proxy
# exists to be used, so it ships enabled.
LOCAL_PROXY_ENABLED: bool = True

# Whether the panel keeps the sniffed result and how it treats it: the
# override list is applied to the connection, metadata only keeps the
# result without touching the traffic, and route only uses it for the
# routing decision alone. The shipped combination routes by the sniffed
# name and changes nothing else in the stream.
LOCAL_PROXY_SNIFFING_ENABLED: bool = True
LOCAL_PROXY_SNIFFING_METADATA_ONLY: bool = False
LOCAL_PROXY_SNIFFING_ROUTE_ONLY: bool = False

# Traffic limit in bytes and expiry time of the local proxy inbound, both
# 0: a local proxy that stops working after a quota or a date is worse
# than useless, so the panel is asked for neither limit.
LOCAL_PROXY_TRAFFIC_LIMIT_BYTES: int = 0
LOCAL_PROXY_EXPIRY_TIME: int = 0

# Stage 7: routing policy of that proxy. The tags name the objects the
# policy owns: a rerun replaces its own outbounds and rules and leaves
# every foreign one as it is.
REMOTE_OUTBOUND_TAG: str = "pyntara-remote"
TOR_OUTBOUND_TAG: str = "pyntara-tor"
I2P_OUTBOUND_TAG: str = "pyntara-i2p"

# The pool the remote classes leave through: the balancer covers every
# outbound whose tag begins with pool_member_prefix (the outbounds the
# panel builds from its outbound subscriptions) and the remote outbound,
# so the fastest member of all of them carries the traffic. Its fallback
# is the remote outbound; on a machine without one, which is the remote
# server itself, the fallback is the direct outbound, so an empty or dead
# pool never blackholes the classes. The pool exists on every machine,
# and the subscription that fills it is the business of the sotavpn_setup
# task.
POOL_BALANCER_TAG: str = "pyntara-fastest"
POOL_MEMBER_PREFIX: str = "sota-"

# Observatory of the pool: the balanced strategy picks by its probe
# results, and an outbound it does not observe is excluded, so its
# subject selector equals the balancer selector. The probe URL is the
# tiny answer that carries no body and is never cached.
POOL_PROBE_URL: str = "https://www.google.com/generate_204"
POOL_PROBE_INTERVAL: str = "30s"
POOL_ENABLE_CONCURRENCY: bool = True

# Tags of the panel's own outbounds, used as jump targets: direct is the
# freedom outbound, blocked is the panel blackhole.
DIRECT_OUTBOUND_TAG: str = "direct"
BLOCKED_OUTBOUND_TAG: str = "blocked"

# Local proxies that already reach the anonymity networks: the tor SOCKS
# port and the i2pd HTTP proxy port. Onion hosts only answer through tor,
# i2p hosts only through i2pd, and both are local, so neither depends on
# the remote server.
TOR_PROXY_ADDRESS: str = "127.0.0.1:9050"
I2P_PROXY_ADDRESS: str = "127.0.0.1:4444"

# Domain categories whose connections are dropped: advertising and
# tracking endpoints. The categories come from the geodata files the panel
# installs; the task verifies every token against them before applying.
AD_BLOCK_DOMAIN_CATEGORIES: tuple[str, ...] = ("geosite:category-ads-all",)

# Domains that must never leave the machine, because a remote server
# cannot resolve them: local hostnames and the private name suffixes.
DIRECT_DOMAINS: tuple[str, ...] = (
        "domain:localhost",
        "domain:.local",
        "domain:.home.arpa",
        "domain:.lan",
        "domain:.internal",
    )

# Address ranges handled directly: the reserved and private set of the
# geoip data, plus the overlay networks of this project. Yggdrasil uses
# 200::/7 and the project reserves 300::/7 for the same purpose; the
# address of a yggdrasil peer is not reachable through the remote server
# and must not be sent to it.
DIRECT_IP_CATEGORIES: tuple[str, ...] = ("geoip:private",)
DIRECT_IP_NETWORKS: tuple[str, ...] = ("200::/7", "300::/7",)

# Services that report the country of this machine, in the order they are
# asked. All of them are queried at once and the answers are merged, so a
# silent service costs nothing but a shorter answer list. Only a service
# that names the country in words is used, because a bare two-letter code
# is also what a colo code looks like ("GRU" is Sao Paulo).
COUNTRY_SERVICES: tuple[str, ...] = (
        "https://ip2c.org/self",
        "https://ifconfig.co/json",
        "https://ipwho.is/",
    )

# The word that decides "this machine is in Russia": it is searched for in
# the answers, ignoring case and separators, so "russia", "Russia",
# "russian federation" and "loc=Russia" all match, while "GRU", "Peru" and
# "Belarus" do not. The search is a plain occurrence, so a word merely
# containing it would match too.
COUNTRY_WORD: str = "russia"

# Timeout in seconds of one country service query (curl --max-time) and of
# the whole parallel query. The first bounds a slow service, the second
# bounds the stage even when every service hangs.
COUNTRY_QUERY_TIMEOUT_SECONDS: int = 10
COUNTRY_COMMAND_TIMEOUT_SECONDS: int = 20

# Categories of the resources blocked inside Russia. A machine in Russia
# reaches them through the remote server, which is outside the block.
RUSSIA_BLOCKED_DOMAIN_CATEGORIES: tuple[str, ...] = ("ext-site:geosite_RU.dat:ru-blocked-all",)
RUSSIA_BLOCKED_IP_CATEGORIES: tuple[str, ...] = (
        "ext-ip:geoip_RU.dat:ru-blocked",
        "ext-ip:geoip_RU.dat:ru-blocked-community",
    )

# Categories of the resources that only answer inside Russia. A machine in
# Russia reaches them directly, because they are the ones the remote
# server cannot serve.
RUSSIA_DIRECT_DOMAIN_CATEGORIES: tuple[str, ...] = ("ext-site:geosite_RU.dat:ru-available-only-inside",)
RUSSIA_DIRECT_IP_CATEGORIES: tuple[str, ...] = ("ext-ip:geoip_RU.dat:ru-whitelist",)

# Services that refuse to serve Russia on their own: AI services, the
# streaming catalogues and the social networks that block Russian
# addresses. They are reached through the remote server from a machine in
# Russia; outside Russia the catch-all rule already sends them there.
GEO_RESTRICTED_DOMAIN_CATEGORIES: tuple[str, ...] = (
        "geosite:category-ai-!cn",
        "geosite:openai",
        "geosite:xai",
        "geosite:netflix",
        "geosite:spotify",
        "geosite:category-social-media-!cn",
    )

# Domain resolution strategy of the routing block. A machine in Russia
# uses IPIfNonMatch so an address can be matched against the lists of
# resources blocked inside Russia, which are address lists for the most
# part; outside Russia AsIs keeps every name local, because the catch-all
# rule sends the connection to the remote server anyway and resolving a
# name first would leak it and cost a query.
RUSSIA_DOMAIN_STRATEGY: str = "IPIfNonMatch"
OUTSIDE_RUSSIA_DOMAIN_STRATEGY: str = "AsIs"

# Destinations the task asks the running core about after applying the
# policy. Each one has an answer that follows from the configuration, so a
# disagreeing answer means the running core did not take the policy.
# A domain of the advertising category, blocked on every machine.
ROUTE_CHECK_AD_DOMAIN: str = "doubleclick.net"

# A plain foreign domain that no category list carries: through the remote
# server outside Russia, directly in Russia.
ROUTE_CHECK_FOREIGN_DOMAIN: str = "example.com"

# A hidden service of tor and one of i2p, both local.
ROUTE_CHECK_ONION_DOMAIN: str = "pyntara-check.onion"
ROUTE_CHECK_I2P_DOMAIN: str = "pyntara-check.i2p"

# A domain of the direct list.
ROUTE_CHECK_DIRECT_DOMAIN: str = "localhost"

# A domain of the resources blocked inside Russia, used on a machine in
# Russia only.
ROUTE_CHECK_RUSSIA_BLOCKED_DOMAIN: str = "instagram.com"

# URL queried once through the local proxy to prove the whole path works,
# not only the routing decision: the answer must be the address of the
# remote server outside Russia, and an address of this machine in Russia,
# because the policy sends an ordinary foreign name directly there.
PROXY_CHECK_URL: str = "https://api4.ipify.org"

# URL queried through the local proxy on a machine in Russia only, of a
# class the policy sends through the remote server: a resource blocked in
# Russia or a service that refuses to serve it. Such a service does not
# report the address of its client, so an HTTP answer is the evidence
# that the tunnel carries that class.
PROXY_CHECK_BLOCKED_URL: str = "https://api.openai.com/v1/models"

# Timeout in seconds of one request of the two path checks above. It
# bounds the connection phase and the whole transfer, so a stalling
# tunnel is reported after a predictable wait instead of holding the run.
# A slow link needs room for the whole chain of one request, the local
# proxy, the handshake with the remote server and the answer of the
# destination, so the value lets that chain finish.
PROXY_CHECK_TIMEOUT_SECONDS: int = 30

# How many times each path check repeats its request before it reports no
# answer. The path can stall once and answer on the next attempt, which
# is a fact of the network and not a broken tunnel, so more than one
# attempt keeps a slow link from being reported as a failure; every
# attempt costs at most proxy_check_timeout_seconds.
PROXY_CHECK_ATTEMPTS: int = 3

# Outer timeout in seconds of one tunnel check call: it stays above the
# probe timeout, so curl reports its own give-up with a clear message
# before the run kills the process.
PROXY_CHECK_COMMAND_TIMEOUT_SECONDS: int = 50

# The names the task and the modules that drive the panel read. The list lives
# next to the values it names, so a module that stops declaring one of them
# is reported by name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "CLIENT_ENABLED",
    "INSTALL_SCRIPT_URL",
    "INSTALL_DIR",
    "BINARY_FILE_NAME",
    "SERVICE_PROCESS_NAME",
    "PANEL_VERSION_COMMAND",
    "PANEL_SETTINGS_QUERY_COMMAND",
    "PANEL_CERT_QUERY_COMMAND",
    "PANEL_PORT_COMMAND",
    "PANEL_CREDENTIALS_COMMAND",
    "PANEL_CERTIFICATE_COMMAND",
    "INSTALLER_RUN_COMMAND",
    "ACME_INSTALL_COMMAND",
    "ACME_DIR_RELATIVE_PATH",
    "ACME_FILE_NAME",
    "ACME_PORT_LISTENER_COMMAND",
    "ACME_SET_DEFAULT_CA_COMMAND",
    "ACME_ISSUE_COMMAND",
    "ACME_INSTALLCERT_COMMAND",
    "ACME_UPGRADE_COMMAND",
    "ACME_RELOAD_COMMAND",
    "OPENSSL_CHECK_COMMAND",
    "OPENSSL_GENERATE_COMMAND",
    "OPENSSL_SUBJECT_TEMPLATE",
    "SERVICE_RESTART_COMMAND",
    "SERVICE_UNIT_NAME",
    "PROBE_TIMEOUT_SECONDS",
    "PROBE_PORT_80_TIMEOUT_SECONDS",
    "PROBE_LISTENER_START_SECONDS",
    "PORT_FORWARD_PROBE_COMMAND",
    "PORT_FORWARD_PROBE_URL_FORMAT",
    "PANEL_PROBE_COMMAND",
    "TUNNEL_PROBE_COMMAND",
    "TUNNEL_PROBE_WRITE_OUT",
    "TUNNEL_PROBE_NO_ANSWER_CODE",
    "READINESS_CHECK_DELAY_SECONDS",
    "SERVICE_START_WAIT_SECONDS",
    "PANEL_LISTENER_WAIT_SECONDS",
    "CORE_READY_WAIT_SECONDS",
    "INSTALL_RESULT_ENV_PATH",
    "INBOUND_PAYLOAD_TEMPLATE_FILE_NAME",
    "RANDOM_USERNAME_BYTES",
    "RANDOM_SECRET_BYTES",
    "RANDOM_SUB_ID_BYTES",
    "PANEL_PORT",
    "SSL_ENABLED",
    "PANEL_HTTP_ADDRESS",
    "PANEL_API_TIMEOUT_SECONDS",
    "PANEL_ROOT_PATH",
    "PANEL_LOGIN_PATH",
    "PANEL_CSRF_TOKEN_PATH",
    "PANEL_INBOUNDS_LIST_PATH",
    "PANEL_INBOUNDS_ADD_PATH",
    "PANEL_INBOUNDS_UPDATE_PATH",
    "PANEL_INBOUNDS_DELETE_PATH",
    "PANEL_CLIENT_GET_PATH",
    "PANEL_CLIENT_ADD_PATH",
    "PANEL_CLIENT_LINKS_PATH",
    "PANEL_X25519_CERT_PATH",
    "PANEL_SETTING_ALL_PATH",
    "PANEL_SETTING_UPDATE_PATH",
    "PANEL_XRAY_STATUS_PATH",
    "PANEL_XRAY_UPDATE_PATH",
    "PANEL_XRAY_GEODATA_VALIDATE_PATH",
    "PANEL_XRAY_ROUTE_TEST_PATH",
    "PANEL_OUTBOUND_SUBS_PATH",
    "PANEL_OUTBOUND_SUBS_ITEM_PATH",
    "PANEL_OUTBOUND_SUBS_REFRESH_PATH",
    "PANEL_BALANCER_STATUS_PATH",
    "PANEL_STATUS_PATH",
    "PANEL_XRAY_RESULT_PATH",
    "PANEL_STATUS_KEYS",
    "PANEL_INBOUND_PROTOCOL",
    "PANEL_BLOCKED_RULE_PROTOCOLS",
    "PANEL_PRIVATE_BLOCK_CATEGORY",
    "PANEL_GEODATA_DOMAIN_KIND",
    "PANEL_GEODATA_IP_KIND",
    "INBOUND_SNIFFING_PROTOCOLS",
    "PANEL_HTTP_HEADERS",
    "PANEL_HTTP_HEADER_VALUES",
    "PANEL_HTTP_METHODS",
    "PANEL_URL_SCHEMES",
    "PANEL_ENVIRONMENT_KEYS",
    "PANEL_ANSWER_KEYS",
    "PANEL_FIELD_KEYS",
    "XRAY_FIELD_KEYS",
    "XRAY_VALUES",
    "VLESS_LINK_QUERY_KEYS",
    "VAULT_ENTRY_TITLE",
    "CONNECTION_VAULT_ENTRY_TITLE",
    "SHARE_ADDR_STRATEGY",
    "INBOUND_PORT",
    "ROUTE_TEST_PORT",
    "ROUTE_TEST_NETWORK",
    "ROUTE_TEST_PROTOCOL",
    "REMOTE_LINK_DEFAULT_PORT",
    "INBOUND_REMARK",
    "REALITY_DEST",
    "REALITY_SERVER_NAMES",
    "REALITY_SHORT_ID",
    "REALITY_FINGERPRINT",
    "SUBSCRIPTION_PATH",
    "SUBSCRIPTION_JSON_PATH",
    "SUBSCRIPTION_CLASH_PATH",
    "ACME_PORT",
    "CERT_DIR",
    "CERT_FULLCHAIN_PATH",
    "CERT_PRIVKEY_PATH",
    "SELF_SIGNED_CERT_DIR",
    "SELF_SIGNED_CERT_FULLCHAIN_PATH",
    "SELF_SIGNED_CERT_PRIVKEY_PATH",
    "CERT_PRIVKEY_FILE_MODE",
    "CERT_FULLCHAIN_FILE_MODE",
    "SERVER_IP_TIMEOUT_SECONDS",
    "SERVER_IP_SERVICES",
    "UPNP_ENABLED",
    "UPNP_PACKAGE",
    "UPNP_CLIENT_COMMAND",
    "UPNP_PROTOCOL",
    "UPNP_MAPPING_DESCRIPTION",
    "CLIENT_PROFILE_ENTRY_TITLE",
    "LOCAL_PROXY_TAG",
    "LOCAL_PROXY_LISTEN_ADDRESS",
    "PRIVATE_IPV4_NETWORKS",
    "LOCAL_PROXY_PORT",
    "LOCAL_PROXY_UDP",
    "LOCAL_PROXY_SNIFFING_PROTOCOLS",
    "LOCAL_PROXY_ENABLED",
    "LOCAL_PROXY_SNIFFING_ENABLED",
    "LOCAL_PROXY_SNIFFING_METADATA_ONLY",
    "LOCAL_PROXY_SNIFFING_ROUTE_ONLY",
    "LOCAL_PROXY_TRAFFIC_LIMIT_BYTES",
    "LOCAL_PROXY_EXPIRY_TIME",
    "REMOTE_OUTBOUND_TAG",
    "TOR_OUTBOUND_TAG",
    "I2P_OUTBOUND_TAG",
    "POOL_BALANCER_TAG",
    "POOL_MEMBER_PREFIX",
    "POOL_PROBE_URL",
    "POOL_PROBE_INTERVAL",
    "POOL_ENABLE_CONCURRENCY",
    "DIRECT_OUTBOUND_TAG",
    "BLOCKED_OUTBOUND_TAG",
    "TOR_PROXY_ADDRESS",
    "I2P_PROXY_ADDRESS",
    "AD_BLOCK_DOMAIN_CATEGORIES",
    "DIRECT_DOMAINS",
    "DIRECT_IP_CATEGORIES",
    "DIRECT_IP_NETWORKS",
    "COUNTRY_SERVICES",
    "COUNTRY_WORD",
    "COUNTRY_QUERY_TIMEOUT_SECONDS",
    "COUNTRY_COMMAND_TIMEOUT_SECONDS",
    "RUSSIA_BLOCKED_DOMAIN_CATEGORIES",
    "RUSSIA_BLOCKED_IP_CATEGORIES",
    "RUSSIA_DIRECT_DOMAIN_CATEGORIES",
    "RUSSIA_DIRECT_IP_CATEGORIES",
    "GEO_RESTRICTED_DOMAIN_CATEGORIES",
    "RUSSIA_DOMAIN_STRATEGY",
    "OUTSIDE_RUSSIA_DOMAIN_STRATEGY",
    "ROUTE_CHECK_AD_DOMAIN",
    "ROUTE_CHECK_FOREIGN_DOMAIN",
    "ROUTE_CHECK_ONION_DOMAIN",
    "ROUTE_CHECK_I2P_DOMAIN",
    "ROUTE_CHECK_DIRECT_DOMAIN",
    "ROUTE_CHECK_RUSSIA_BLOCKED_DOMAIN",
    "PROXY_CHECK_URL",
    "PROXY_CHECK_BLOCKED_URL",
    "PROXY_CHECK_TIMEOUT_SECONDS",
    "PROXY_CHECK_ATTEMPTS",
    "PROXY_CHECK_COMMAND_TIMEOUT_SECONDS",
)
