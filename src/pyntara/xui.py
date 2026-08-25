"""Shared 3x-ui panel REST API client.

The module provides the HTTP communication with the 3x-ui Xray panel:
reading the install-result.env file the panel writes on first start,
building the panel base URL, performing a CSRF-protected login and
verifying the session. Stage 2 of the three_x_ui_xray_setup task uses
these functions to check that the panel is reachable and the credentials
are valid before storing them in the runtime vault; stage 3 will reuse
the same helpers for inbound management. The functions are stateless
and take the config and timeout as parameters, so they are testable
without a running panel.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pyntara.config import ThreeXuiXraySetupConfig


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
    address: str, port: str, web_base_path: str | None
) -> str:
    """The panel base URL from its address, port and optional webBasePath.

    The address is the configured panel_http_address (127.0.0.1 on the
    local machine). The port comes from XUI_PANEL_PORT. The webBasePath
    comes from XUI_WEB_BASE_PATH and is stripped of leading and trailing
    slashes; an empty or absent base path is omitted.
    """

    base = f"http://{address}:{port}"
    if web_base_path:
        cleaned = web_base_path.strip("/")
        if cleaned:
            base += f"/{cleaned}"
    return base


def _csrf_token(
    base_url: str, timeout: float
) -> str | None:
    """Fetch a CSRF token from the panel, or None on failure.

    GET /csrf-token returns {"success":true,"obj":"<token>"}. The
    request also sets a session cookie (3x-ui) that must be reused in
    the login call. Returns the token string or None when the panel
    does not answer or returns an unexpected payload.
    """

    url = f"{base_url}/csrf-token"
    try:
        req = urllib.request.Request(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    token = data.get("obj")
    return str(token) if isinstance(token, str) and token else None


def _login(
    base_url: str, username: str, password: str, csrf_token: str, timeout: float
) -> bool:
    """Authenticate with the panel and establish a session cookie.

    POST /login with form-encoded username and password, the CSRF token
    in the X-CSRF-Token header and the session cookie from the csrf-token
    call. Returns True when the panel responds with success=true.
    """

    data = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode("utf-8")
    url = f"{base_url}/login"
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{base_url}/",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    return bool(isinstance(data, dict) and data.get("success"))


def _verify_session(base_url: str, timeout: float) -> bool:
    """Check that the session cookie is valid by calling a protected API.

    GET /panel/api/inbounds/list with the session cookie from the login
    call. Returns True when the panel responds with success=true.
    """

    url = f"{base_url}/panel/api/inbounds/list"
    try:
        req = urllib.request.Request(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    return bool(isinstance(data, dict) and data.get("success"))


def _verify_bearer(base_url: str, api_token: str, timeout: float) -> bool:
    """Check that the Bearer API token is valid.

    GET /panel/api/inbounds/list with Authorization: Bearer <token>.
    Returns True when the panel responds with success=true.
    """

    url = f"{base_url}/panel/api/inbounds/list"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return False
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
    )
    token = _csrf_token(base_url, timeout)
    if token is None:
        return False
    if not _login(
        base_url,
        env.get("XUI_USERNAME", ""),
        env.get("XUI_PASSWORD", ""),
        token,
        timeout,
    ):
        return False
    return _verify_session(base_url, timeout)


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
    )
    return _verify_bearer(base_url, env.get("XUI_API_TOKEN", ""), timeout)
