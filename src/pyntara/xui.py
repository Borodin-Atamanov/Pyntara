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

import http.cookiejar
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
    )

    # Create an opener with a cookie jar so the session cookie persists.
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

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
    )
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )
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
