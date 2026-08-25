"""Shared 3x-ui panel REST API client.

The module provides the HTTP communication with the 3x-ui Xray panel:
reading the install-result.env file the panel writes on first start,
building the panel base URL, performing a CSRF-protected login and
verifying the session. Stage 2 of the three_x_ui_xray_setup task uses
these functions to check that the panel is reachable and the credentials
are valid before storing them in the runtime vault. Stage 3 uses the
Bearer-token API helpers (list_inbounds, find_inbound_by_port,
create_inbound, generate_reality_key, build_vless_reality_payload) for
inbound management. The functions are stateless and take the config and
timeout as parameters, so they are testable without a running panel.
"""

from __future__ import annotations

import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pyntara.config import ThreeXuiXraySetupConfig
from pyntara.utils import run_command


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
        [str(cfg.install_dir / "x-ui"), "setting", "-getCert", "true"],
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
    except (urllib.error.URLError, OSError, ValueError):
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
        f"{base_url}/csrf-token",
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
        f"{base_url}/login",
        data=login_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRF-Token": token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{base_url}/",
        },
        method="POST",
        timeout=timeout,
    )
    if status != 200 or not _json_success(body):
        return False

    # Step 3: verify the session by calling a protected API.
    status, body = _request(
        opener,
        f"{base_url}/panel/api/inbounds/list",
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
        f"{base_url}/panel/api/inbounds/list",
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
        f"{base_url}/panel/api/inbounds/list",
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
        f"{base_url}/panel/api/inbounds/add",
        data=data,
        headers=headers,
        method="POST",
        timeout=timeout,
    )
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
        return True, msg or "inbound created"
    return False, msg or "unknown error"


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
        f"{base_url}/panel/api/server/getNewX25519Cert",
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
    short_id: str,
) -> dict[str, object]:
    """Build the JSON payload for creating a VLESS+REALITY inbound.

    settings, streamSettings and sniffing are returned as nested JSON
    objects (the preferred format for the panel API). The payload is
    ready to be serialised and sent to /panel/api/inbounds/add.
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
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls"],
        },
        "enable": True,
    }
