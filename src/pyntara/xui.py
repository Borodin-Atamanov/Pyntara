"""Shared 3x-ui panel REST API client.

The module provides the HTTP communication with the 3x-ui Xray panel:
reading the install-result.env file the panel writes on first start,
building the panel base URL, performing a CSRF-protected login and
verifying the session. Stage 2 of the three_x_ui_xray_setup task uses
these functions to check that the panel is reachable and the credentials
are valid before storing them in the runtime vault. Stage 3 uses the
Bearer-token API helpers (list_inbounds, find_inbound_by_port,
create_inbound, generate_reality_key, build_vless_reality_payload) for
inbound management. Stage 6 and 7 use the Xray template helpers
(read_xray_template, write_xray_template), the inbound upsert
(upsert_inbound, delete_inbound), the category check
(validate_geodata_tokens) and the routing check (route_test) to turn the
panel into the client of a remote server and to verify that the running
core routes what the policy intends. The functions are stateless and take
the config and timeout as parameters, so they are testable without a
running panel.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pyntara.config import ThreeXuiXraySetupConfig
from pyntara.utils import run_command, substituted_command


def _ssl_context() -> ssl.SSLContext:
    """An unverified TLS context for local panel HTTPS connections.

    The panel serves a certificate for its public IP address, while the
    API client connects to the configured local address (127.0.0.1), so
    hostname verification would reject every request. The TLS here only
    protects the panel's own admin traffic; the local client does not
    need to validate the certificate.
    """

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _https_opener(
    *handlers: urllib.request.BaseHandler,
) -> urllib.request.OpenerDirector:
    """Build an opener that accepts the local unverified TLS context.

    The HTTPSHandler with the unverified context is appended to the
    given handlers, so every panel call works whether the panel serves
    plain HTTP or TLS.
    """

    return urllib.request.build_opener(
        *handlers,
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )


def panel_cert_value(
    cfg: ThreeXuiXraySetupConfig, timeout: float
) -> str | None:
    """The panel certificate path from `x-ui setting -getCert`, or None.

    An empty cert value means no certificate is configured; a nonzero
    exit or a missing cert line is treated the same, so the caller can
    attempt setup. Shared by the SSL stage and the scheme detection.
    """

    result = run_command(
        substituted_command(
            cfg.panel_cert_query_command,
            {"binary": str(cfg.install_dir / cfg.binary_file_name)},
        ),
        check=False,
        capture=True,
        timeout=timeout,
    )
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if "cert:" in line:
            value = line.split("cert:", 1)[1].strip()
            return value or None
    return None


def panel_scheme(cfg: ThreeXuiXraySetupConfig, timeout: float) -> str:
    """The panel URL scheme: https when a certificate is configured.

    The panel serves TLS only when a certificate path is set. Any
    failure to read the state is treated as http, so the client stays
    reachable over plain HTTP.
    """

    return "https" if panel_cert_value(cfg, timeout) else "http"


def parse_install_result_env(path: Path) -> dict[str, str]:
    """Read the install-result.env file and return its key-value pairs.

    The file is written by the 3x-ui panel on first start (mode 600,
    root). Each line is KEY=VALUE; blank lines and lines without an
    equals sign are ignored. The returned dict has the XUI_ keys from
    the file: XUI_USERNAME, XUI_PASSWORD, XUI_PANEL_PORT,
    XUI_WEB_BASE_PATH, XUI_API_TOKEN, XUI_DB_TYPE, XUI_ACCESS_URL.
    Raises FileNotFoundError when the file is absent and RuntimeError
    when a required key is missing.
    """

    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    required = ("XUI_USERNAME", "XUI_PASSWORD", "XUI_PANEL_PORT")
    missing = [k for k in required if k not in result]
    if missing:
        raise RuntimeError(
            f"install-result.env missing required key(s): {', '.join(missing)}"
        )
    return result


def build_panel_url(
    address: str,
    port: str,
    web_base_path: str | None,
    scheme: str = "http",
) -> str:
    """The panel base URL from its scheme, address, port and webBasePath.

    The address is the configured panel_http_address (127.0.0.1 on the
    local machine). The scheme is http by default and https when the
    panel serves TLS. The port comes from XUI_PANEL_PORT. The
    webBasePath comes from XUI_WEB_BASE_PATH and is stripped of leading
    and trailing slashes; an empty or absent base path is omitted.
    """

    base = f"{scheme}://{address}:{port}"
    if web_base_path:
        cleaned = web_base_path.strip("/")
        if cleaned:
            base += f"/{cleaned}"
    return base


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: float,
) -> tuple[int, str]:
    """Send an HTTP request and return (status_code, body).

    Uses the provided opener (which carries a cookie jar) so session
    cookies persist across calls. Returns (0, '') on connection errors.
    """

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return (resp.status, body)
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.read().decode("utf-8") if exc.fp else "")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return (0, "")


def _json_success(body: str) -> bool:
    """True when the body is valid JSON with success=true."""

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    return bool(isinstance(data, dict) and data.get("success"))


def login_and_verify(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> bool:
    """Log in to the panel with the credentials from install-result.env
    and verify the session.

    The function performs the full CSRF login flow: fetch a CSRF token,
    authenticate with username and password, then verify the session by
    calling a protected API endpoint. Returns True when the panel is
    reachable and the credentials are valid. The session cookie is
    discarded after the call; stage 3 will re-login when it needs to
    create an inbound.
    """

    base_url = build_panel_url(
        cfg.panel_http_address,
        env.get("XUI_PANEL_PORT", ""),
        env.get("XUI_WEB_BASE_PATH"),
        scheme=env.get("XUI_SCHEME", "http"),
    )

    # Create an opener with a cookie jar so the session cookie persists.
    jar = http.cookiejar.CookieJar()
    opener = _https_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1: fetch CSRF token.
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_csrf_token_path}",
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=timeout,
    )
    if status != 200 or not _json_success(body):
        return False
    try:
        token = json.loads(body).get("obj", "")
    except (json.JSONDecodeError, AttributeError):
        return False
    if not isinstance(token, str) or not token:
        return False

    # Step 2: login with username and password.
    login_data = urllib.parse.urlencode(
        {"username": env.get("XUI_USERNAME", ""), "password": env.get("XUI_PASSWORD", "")}
    ).encode("utf-8")
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_login_path}",
        data=login_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRF-Token": token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{base_url}{cfg.panel_root_path}",
        },
        method="POST",
        timeout=timeout,
    )
    if status != 200 or not _json_success(body):
        return False

    # Step 3: verify the session by calling a protected API.
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_list_path}",
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=timeout,
    )
    return status == 200 and _json_success(body)


def verify_bearer(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> bool:
    """Verify the panel session using the Bearer API token.

    Calls a protected API endpoint with the API token from
    install-result.env. Returns True when the panel is reachable and
    the token is valid.
    """

    base_url = build_panel_url(
        cfg.panel_http_address,
        env.get("XUI_PANEL_PORT", ""),
        env.get("XUI_WEB_BASE_PATH"),
        scheme=env.get("XUI_SCHEME", "http"),
    )
    jar = http.cookiejar.CookieJar()
    opener = _https_opener(urllib.request.HTTPCookieProcessor(jar))
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_list_path}",
        headers={
            "Authorization": f"Bearer {env.get('XUI_API_TOKEN', '')}",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=timeout,
    )
    return status == 200 and _json_success(body)


def _bearer_opener(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
) -> tuple[str, urllib.request.OpenerDirector]:
    """Build the panel base URL and an opener with Bearer auth headers.

    Returns (base_url, opener) for use by stage 3 API calls. The opener
    carries no cookie jar because Bearer-token calls do not need session
    cookies.
    """

    base_url = build_panel_url(
        cfg.panel_http_address,
        env.get("XUI_PANEL_PORT", ""),
        env.get("XUI_WEB_BASE_PATH"),
        scheme=env.get("XUI_SCHEME", "http"),
    )
    opener = _https_opener()
    return base_url, opener


def _bearer_headers(env: dict[str, str]) -> dict[str, str]:
    """Common headers for Bearer-authenticated API calls."""

    return {
        "Authorization": f"Bearer {env.get('XUI_API_TOKEN', '')}",
        "X-Requested-With": "XMLHttpRequest",
    }


def list_inbounds(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> list[dict[str, object]]:
    """List every inbound owned by the authenticated user.

    Returns the obj array from the response. Returns an empty list on
    failure (unreachable panel, bad token, unexpected response shape).
    """

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_list_path}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    obj = data.get("obj")
    if not isinstance(obj, list):
        return []
    return obj


def find_inbound_by_port(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    port: int,
    timeout: float,
) -> dict[str, object] | None:
    """Find an inbound by its port number.

    Returns the first inbound dict whose port matches, or None when no
    inbound uses the given port.
    """

    inbounds = list_inbounds(cfg, env, timeout)
    for inbound in inbounds:
        if isinstance(inbound, dict) and inbound.get("port") == port:
            return inbound
    return None


def find_inbound_by_tag(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    tag: str,
    timeout: float,
) -> dict[str, object] | None:
    """Find an inbound by the tag its traffic carries.

    The panel keeps the routing tag in the tag field of an inbound and the
    human label in remark, and only the tag is what the routing rules
    match with inboundTag, so the search uses the tag. Returns the inbound
    dict or None when no inbound carries that tag.
    """

    inbounds = list_inbounds(cfg, env, timeout)
    for inbound in inbounds:
        if isinstance(inbound, dict) and inbound.get("tag") == tag:
            return inbound
    return None


def _message_result(status: int, body: str, ok_default: str) -> tuple[bool, str]:
    """Parse a panel write response into (success, message).

    Status 0 means the panel was unreachable and an unparsable body is an
    unexpected response; otherwise the panel's own msg field is reported
    and success decides the flag. Shared by every write helper, so the
    error wording stays identical across them.
    """

    if status == 0:
        return False, "panel unreachable"
    try:
        resp = json.loads(body)
    except json.JSONDecodeError:
        return False, f"unexpected response (HTTP {status})"
    if not isinstance(resp, dict):
        return False, f"unexpected response (HTTP {status})"
    msg = resp.get("msg", "")
    if resp.get("success"):
        return True, msg or ok_default
    return False, msg or "unknown error"


def create_inbound(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Create a new inbound through the panel API.

    Returns (success, message). On success the message is the response
    msg field. On failure the message describes the error (port conflict,
    unreachable panel, etc.).
    """

    base_url, opener = _bearer_opener(cfg, env)
    data = json.dumps(payload).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/json"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_add_path}",
        data=data,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "inbound created")


def generate_reality_key(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> tuple[str, str] | None:
    """Generate a new X25519 keypair for Reality through the panel API.

    Returns (private_key, public_key) on success, or None on failure.
    """

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_x25519_cert_path}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    obj = data.get("obj")
    if not isinstance(obj, dict):
        return None
    private_key = obj.get("privateKey", "")
    public_key = obj.get("publicKey", "")
    if not private_key or not public_key:
        return None
    return (private_key, public_key)


def build_vless_reality_payload(
    port: int,
    remark: str,
    dest: str,
    server_names: tuple[str, ...],
    private_key: str,
    public_key: str,
    short_id: str,
    fingerprint: str,
    sniffing_protocols: tuple[str, ...],
) -> dict[str, object]:
    """Build the JSON payload for creating a VLESS+REALITY inbound.

    settings, streamSettings and sniffing are returned as nested JSON
    objects (the preferred format for the panel API). The public key and
    the fingerprint go into the nested realitySettings.settings block:
    the panel writes pbk into share links only when it finds the public
    key there, while the server itself needs only the private key. The
    payload is ready to be serialised and sent to
    /panel/api/inbounds/add.
    """

    return {
        "remark": remark,
        "port": port,
        "protocol": "vless",
        "settings": {
            "clients": [],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "xver": 0,
                "dest": dest,
                "serverNames": list(server_names),
                "privateKey": private_key,
                "shortIds": [short_id],
                "settings": {
                    "publicKey": public_key,
                    "fingerprint": fingerprint,
                },
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": list(sniffing_protocols),
        },
        "enable": True,
    }


def panel_settings(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> dict[str, object] | None:
    """Read the whole panel settings document, or None on failure.

    The panel serves its settings at POST /panel/api/setting/all; the
    response obj is the full settings document. None on an unreachable
    panel, a bad token or an unexpected response shape.
    """

    base_url, opener = _bearer_opener(cfg, env)
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/json"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_setting_all_path}",
        data=b"{}",
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    obj = data.get("obj")
    if not isinstance(obj, dict):
        return None
    return obj


def update_panel_settings(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    settings: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Persist the whole settings document through the Bearer API.

    The panel writes every setting at once, so the caller reads the
    document, changes the wanted keys and sends the whole object here.
    A blank secret field means "unchanged", so writing back a document
    read from /panel/api/setting/all never clears a secret. Returns
    (success, message).
    """

    base_url, opener = _bearer_opener(cfg, env)
    data = json.dumps(settings).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/json"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_setting_update_path}",
        data=data,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "settings updated")


def ensure_subscription_paths(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> tuple[bool, str]:
    """Move the panel subscription paths off the well-known defaults.

    Reads the settings document, compares subPath, subJsonPath and
    subClashPath with the configured values and writes the document back
    only when one differs, so a rerun is a no-op. Returns (changed,
    message); a failure returns (False, error) with changed False.
    """

    settings = panel_settings(cfg, env, timeout)
    if settings is None:
        return False, "cannot read panel settings"
    wanted = {
        "subPath": cfg.subscription_path,
        "subJsonPath": cfg.subscription_json_path,
        "subClashPath": cfg.subscription_clash_path,
    }
    if all(settings.get(key) == value for key, value in wanted.items()):
        return False, ""
    settings.update(wanted)
    ok, message = update_panel_settings(cfg, env, settings, timeout)
    if not ok:
        return False, message
    return True, (
        "subscription paths set to "
        f"{cfg.subscription_path}, {cfg.subscription_json_path}, "
        f"{cfg.subscription_clash_path}"
    )


def update_inbound(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    inbound: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Replace an inbound through the Bearer API.

    The panel persists the whole inbound object, so the caller reads it
    with list_inbounds, changes the wanted keys and sends it back. The id
    is taken from the object itself.
    """

    base_url, opener = _bearer_opener(cfg, env)
    data = json.dumps(inbound).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/json"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_update_path.format(inbound_id=inbound.get('id'))}",
        data=data,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "inbound updated")


def upsert_inbound(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Create the inbound or replace the one that carries the same tag.

    The tag comes from the payload tag field. A machine that already has
    the inbound gets its definition replaced, so a rerun with a new port
    or protocol converges instead of failing on a duplicate tag; a machine
    without it gets the inbound created. The messages tell the two apart
    for the log.
    """

    tag = payload.get("tag")
    if not isinstance(tag, str) or not tag:
        return False, "inbound payload has no tag to identify it by"
    existing = find_inbound_by_tag(cfg, env, tag, timeout)
    if existing is None:
        return create_inbound(cfg, env, payload, timeout)
    replacement = dict(payload)
    replacement["id"] = existing.get("id")
    ok, message = update_inbound(cfg, env, replacement, timeout)
    return ok, message if not ok else f"inbound {tag} updated: {message}"


def delete_inbound(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    inbound_id: int,
    timeout: float,
) -> tuple[bool, str]:
    """Delete one inbound by its id through the Bearer API."""

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_inbounds_delete_path.format(inbound_id=inbound_id)}",
        headers=_bearer_headers(env),
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "inbound deleted")


def find_client(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    email: str,
    timeout: float,
) -> dict[str, object] | None:
    """Find a client by its email label through the Bearer API, or None.

    The panel answers /panel/api/clients/get/{email} with a payload that
    wraps the client record; the record itself is returned here. A
    missing client, an unreachable panel and an unexpected shape are all
    None, so the caller can create the client.
    """

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_client_get_path.format(email=urllib.parse.quote(email))}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    obj = data.get("obj")
    if not isinstance(obj, dict):
        return None
    client = obj.get("client")
    return client if isinstance(client, dict) else None


def create_client(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    inbound_id: int,
    client_id: str,
    email: str,
    sub_id: str,
    timeout: float,
) -> tuple[bool, str]:
    """Create one VLESS client attached to an inbound.

    The credential lives in the client.id field (stored as the client
    uuid); email is only the human label and must be unique. Returns
    (success, message) from the panel response.
    """

    base_url, opener = _bearer_opener(cfg, env)
    payload = {
        "client": {
            "id": client_id,
            "email": email,
            "enable": True,
            "subId": sub_id,
        },
        "inboundIds": [inbound_id],
    }
    data = json.dumps(payload).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/json"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_client_add_path}",
        data=data,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "client created")


def client_links(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    email: str,
    timeout: float,
) -> list[str]:
    """The share links of one client, or an empty list.

    The panel renders the links through the same subscription engine the
    panel UI uses, so the returned strings carry every connection
    parameter including pbk and the share address.
    """

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_client_links_path.format(email=urllib.parse.quote(email))}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    obj = data.get("obj")
    if not isinstance(obj, list):
        return []
    return [item for item in obj if isinstance(item, str) and item]


@dataclass(frozen=True)
class XrayTemplate:
    """The Xray configuration the panel stores, and its outbound test URL.

    The panel keeps the whole Xray document plus a few values it needs for
    its own UI in one blob; the settings field is the document itself, the
    part a routing policy rewrites, and outbound_test_url is kept so a
    written document never loses the panel's own setting.
    """

    settings: dict[str, object]
    outbound_test_url: str


def read_xray_template(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> XrayTemplate | None:
    """Read the stored Xray template through the Bearer API.

    The panel answers only POST on its Xray template endpoint and returns
    the blob as a JSON string, whose xraySetting member holds the Xray
    document itself. Both the string and the already parsed form of that
    member are accepted, because the panel has shipped both. None means
    the panel was unreachable or answered something unreadable, and the
    caller must not write a template it never read.
    """

    base_url, opener = _bearer_opener(cfg, env)
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_xray_status_path}",
        headers=_bearer_headers(env),
        method="POST",
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    blob = _decoded_json_object(data.get("obj"))
    if blob is None:
        return None
    settings_text = blob.get("xraySetting")
    settings = _decoded_json_object(settings_text)
    if settings is None:
        return None
    test_url = blob.get("outboundTestUrl")
    return XrayTemplate(
        settings=settings,
        outbound_test_url=test_url if isinstance(test_url, str) else "",
    )


def _decoded_json_object(value: object) -> dict[str, object] | None:
    """The dict behind a value that is a dict or a JSON string of one."""

    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def write_xray_template(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    template: XrayTemplate,
    timeout: float,
) -> tuple[bool, str]:
    """Write the Xray template back through the Bearer API.

    The panel takes the document and its test URL as form fields and
    applies the result to the running core at once, by hot reload when the
    change allows it. Returns (success, message); the caller verifies the
    running core afterwards, because a stored template alone has already
    been observed to disagree with what the core routes.
    """

    base_url, opener = _bearer_opener(cfg, env)
    form = urllib.parse.urlencode(
        {
            "xraySetting": json.dumps(template.settings),
            "outboundTestUrl": template.outbound_test_url,
        }
    ).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_xray_update_path}",
        data=form,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    return _message_result(status, body, "xray template applied")


def validate_geodata_tokens(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    kind: str,
    tokens: list[str],
    timeout: float,
) -> dict[str, str]:
    """Check routing tokens against the geodata files the panel installed.

    kind is one of the two configured kinds, panel_geodata_domain_kind or
    panel_geodata_ip_kind: domain tokens are resolved against the geosite
    files, address tokens against the geoip files, and the panel receives
    the kind as its own word. Returns a mapping of the tokens the panel
    rejects to the reason it gives; an empty mapping means every token
    resolved. An unreachable panel returns every token mapped to "panel
    unreachable", which is what the caller needs to hear: it cannot treat
    an unverified token as a working one.
    """

    known_kinds = (cfg.panel_geodata_domain_kind, cfg.panel_geodata_ip_kind)
    if kind not in known_kinds:
        raise ValueError(
            f"unknown geodata kind {kind!r}, expected one of {known_kinds}"
        )
    if not tokens:
        return {}
    base_url, opener = _bearer_opener(cfg, env)
    form = urllib.parse.urlencode(
        {"kind": kind, "tokens": ",".join(tokens)}
    ).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_xray_geodata_validate_path}",
        data=form,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    if status != 200:
        return {token: "panel unreachable" for token in tokens}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {token: f"unexpected response (HTTP {status})" for token in tokens}
    if not isinstance(data, dict) or not data.get("success"):
        return {token: "the panel rejected the check" for token in tokens}
    obj = data.get("obj")
    if not isinstance(obj, list):
        return {token: "the panel answered no result" for token in tokens}
    rejected: dict[str, str] = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        if not isinstance(token, str) or not token:
            continue
        reason = item.get("reason")
        rejected[token] = reason if isinstance(reason, str) and reason else "rejected"
    return rejected


def route_test(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    *,
    inbound_tag: str,
    domain: str = "",
    address: str = "",
    port: int = 0,
    network: str = "tcp",
    protocol: str = "tls",
    timeout: float,
) -> tuple[bool, str]:
    """Ask the running core which outbound it picks for a destination.

    Exactly one of domain and address is given. The answer comes from the
    core's own routing engine, so it is the only honest check that a
    written policy reached the traffic; a stored template can disagree
    with the core. Returns (matched, answer): on success matched is True
    and answer is the tag of the chosen outbound, otherwise matched is
    False and answer says why (the panel was unreachable, the panel
    reported an error, or no rule matched the destination).
    """

    base_url, opener = _bearer_opener(cfg, env)
    fields: dict[str, str] = {
        "port": str(port),
        "network": network,
        "protocol": protocol,
        "inboundTag": inbound_tag,
    }
    if domain:
        fields["domain"] = domain
    elif address:
        fields["ip"] = address
    else:
        raise ValueError("route_test needs a domain or an address")
    form = urllib.parse.urlencode(fields).encode("utf-8")
    headers = _bearer_headers(env)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    status, body = _request(
        opener,
        f"{base_url}{cfg.panel_xray_route_test_path}",
        data=form,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
    if status == 0:
        return False, "panel unreachable"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, f"unexpected response (HTTP {status})"
    if not isinstance(data, dict):
        return False, f"unexpected response (HTTP {status})"
    if not data.get("success"):
        message = data.get("msg")
        return False, message if isinstance(message, str) and message else "route test failed"
    obj = data.get("obj")
    if not isinstance(obj, dict):
        return False, "the panel answered no routing decision"
    outbound_tag = obj.get("outboundTag")
    if not obj.get("matched") or not isinstance(outbound_tag, str) or not outbound_tag:
        return False, "no routing rule matched the destination"
    return True, outbound_tag
