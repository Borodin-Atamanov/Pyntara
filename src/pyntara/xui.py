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
core routes what the policy intends. The outbound subscription helpers
(list_outbound_subscriptions, upsert_outbound_subscription,
refresh_outbound_subscription) let a task feed the panel from a URL that
serves a live server list, and list_balancer_status asks the panel which
member a load balancer currently picks. The functions are stateless and take
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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

from pyntara.utils import run_command, substituted_command, trim_whitespace
from pyntara.values import three_x_ui_xray_setup as panel_values


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
    timeout: float
) -> str | None:
    """The panel certificate path from `x-ui setting -getCert`, or None.

    An empty cert value means no certificate is configured; a nonzero
    exit or a missing cert line is treated the same, so the caller can
    attempt setup. Shared by the SSL stage and the scheme detection.
    """

    result = run_command(
        substituted_command(
            panel_values.PANEL_CERT_QUERY_COMMAND,
            {"binary": str(panel_values.INSTALL_DIR / panel_values.BINARY_FILE_NAME)},
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


def panel_scheme(timeout: float) -> str:
    """The panel URL scheme: the TLS scheme when a certificate is set.

    The panel serves TLS only when a certificate path is set. Any
    failure to read the state is treated as the plain scheme, so the
    client stays reachable over plain HTTP. Both names are config values,
    because they are part of the vocabulary of the panel.
    """

    schemes = panel_values.PANEL_URL_SCHEMES
    if panel_cert_value(timeout):
        return schemes["https"]
    return schemes["http"]


def panel_environment(
    timeout: float
) -> dict[str, str]:
    """The install-result.env pairs plus the panel URL scheme.

    XUI_SCHEME carries https when the panel serves TLS and http
    otherwise; the base URL builder reads it, so a caller works on both a
    plain HTTP panel and one with a certificate. A missing or unreadable
    file raises the error of the reader, and the caller reports it as the
    reason its step could not run.
    """

    env = parse_install_result_env(
        panel_values.INSTALL_RESULT_ENV_PATH,
        panel_required_environment_keys(),
    )
    env[panel_values.PANEL_ENVIRONMENT_KEYS["scheme"]] = panel_scheme(timeout)
    return env


def parse_install_result_env(
    path: Path, required_keys: tuple[str, ...]
) -> dict[str, str]:
    """Read the install-result.env file and return its key-value pairs.

    The file is written by the 3x-ui panel on first start (mode 600,
    root). Each line is KEY=VALUE; blank lines and lines without an
    equals sign are ignored. required_keys are the names that must be
    present, and they come from the panel environment keys of the config,
    because the panel names them and a version that renames one is
    answered there. Raises FileNotFoundError when the file is absent and
    RuntimeError when a required key is missing.
    """

    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    missing = [key for key in required_keys if key not in result]
    if missing:
        raise RuntimeError(
            f"install-result.env missing required key(s): {', '.join(missing)}"
        )
    return result


def panel_required_environment_keys(
    ) -> tuple[str, ...]:
    """The install-result.env keys the client cannot work without."""

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    return (keys["username"], keys["password"], keys["panel_port"])


def build_panel_url(
    address: str,
    port: str,
    web_base_path: str | None,
    scheme: str,
) -> str:
    """The panel base URL from its scheme, address, port and webBasePath.

    The address is the configured panel_http_address (127.0.0.1 on the
    local machine). The scheme is a value of panel_url_schemes, the port
    comes from the panel port key of panel_environment_keys and the
    webBasePath from its web base path key, stripped of leading and
    trailing slashes; an empty or absent base path is omitted.
    """

    base = f"{scheme}://{address}:{port}"
    if web_base_path:
        cleaned = web_base_path.strip("/")
        if cleaned:
            base += f"/{cleaned}"
    return base


def _api_call(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: float,
) -> tuple[int, str]:
    """One panel API call, bounded by the API timeout of the section.

    The caller passes the budget of its whole step, which is the engine
    command budget in the thousands of seconds; the socket timeout of one
    call is the smaller of that budget and panel_api_timeout_seconds,
    because a panel that accepts the connection and never answers must be
    reported as a failure of that call instead of stopping the run for
    hours. The value is a budget of the panel and not of the step: the
    longest legitimate answer of the panel is its own reconciliation of a
    template write, which fits inside it.
    """

    return _request(
        opener,
        url,
        data=data,
        headers=headers,
        method=method,
        timeout=min(timeout, panel_values.PANEL_API_TIMEOUT_SECONDS),
    )


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: float,
) -> tuple[int, str]:
    """Send one HTTP request and return (status_code, body).

    Uses the provided opener (which carries a cookie jar) so session
    cookies persist across calls. Returns (0, '') on connection errors.
    Every panel call of this module goes through _api_call, which owns the
    socket timeout of one call, so the timeout given here is the one the
    caller decided on.
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


def _json_success(body: str, success_key: str) -> bool:
    """True when the body is valid JSON with the success field set."""

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    return bool(isinstance(data, dict) and data.get(success_key))


def login_and_verify(
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

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    headers = panel_values.PANEL_HTTP_HEADERS
    header_values = panel_values.PANEL_HTTP_HEADER_VALUES
    answers = panel_values.PANEL_ANSWER_KEYS
    base_url = build_panel_url(
        panel_values.PANEL_HTTP_ADDRESS,
        env.get(keys["panel_port"], ""),
        env.get(keys["web_base_path"]),
        scheme=env.get(keys["scheme"], panel_values.PANEL_URL_SCHEMES["http"]),
    )

    # Create an opener with a cookie jar so the session cookie persists.
    jar = http.cookiejar.CookieJar()
    opener = _https_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1: fetch CSRF token.
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_CSRF_TOKEN_PATH}",
        headers={headers["requested_with"]: header_values["xml_http_request"]},
        timeout=timeout,
    )
    if status != 200 or not _json_success(body, answers["success"]):
        return False
    try:
        token = json.loads(body).get(answers["payload"], "")
    except (json.JSONDecodeError, AttributeError):
        return False
    if not isinstance(token, str) or not token:
        return False

    # Step 2: login with username and password.
    fields = panel_values.PANEL_FIELD_KEYS
    login_data = _form_body(
        {
            fields["username"]: env.get(keys["username"], ""),
            fields["password"]: env.get(keys["password"], ""),
        }
    )
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_LOGIN_PATH}",
        data=login_data,
        headers={
            headers["content_type"]: header_values["form"],
            headers["csrf_token"]: token,
            headers["requested_with"]: header_values["xml_http_request"],
            headers["referer"]: f"{base_url}{panel_values.PANEL_ROOT_PATH}",
        },
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    if status != 200 or not _json_success(body, answers["success"]):
        return False

    # Step 3: verify the session by calling a protected API.
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_LIST_PATH}",
        headers={headers["requested_with"]: header_values["xml_http_request"]},
        timeout=timeout,
    )
    return status == 200 and _json_success(body, answers["success"])


def verify_bearer(
    env: dict[str, str],
    timeout: float,
) -> bool:
    """Verify the panel session using the Bearer API token.

    Calls a protected API endpoint with the API token from
    install-result.env. Returns True when the panel is reachable and
    the token is valid.
    """

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    headers = panel_values.PANEL_HTTP_HEADERS
    header_values = panel_values.PANEL_HTTP_HEADER_VALUES
    answers = panel_values.PANEL_ANSWER_KEYS
    base_url = build_panel_url(
        panel_values.PANEL_HTTP_ADDRESS,
        env.get(keys["panel_port"], ""),
        env.get(keys["web_base_path"]),
        scheme=env.get(keys["scheme"], panel_values.PANEL_URL_SCHEMES["http"]),
    )
    jar = http.cookiejar.CookieJar()
    opener = _https_opener(urllib.request.HTTPCookieProcessor(jar))
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_LIST_PATH}",
        headers={
            headers["authorization"]: (
                f"{header_values['bearer_prefix']}{env.get(keys['api_token'], '')}"
            ),
            headers["requested_with"]: header_values["xml_http_request"],
        },
        timeout=timeout,
    )
    return status == 200 and _json_success(body, answers["success"])


def _bearer_opener(
    env: dict[str, str],
) -> tuple[str, urllib.request.OpenerDirector]:
    """Build the panel base URL and an opener with Bearer auth headers.

    Returns (base_url, opener) for use by stage 3 API calls. The opener
    carries no cookie jar because Bearer-token calls do not need session
    cookies.
    """

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    base_url = build_panel_url(
        panel_values.PANEL_HTTP_ADDRESS,
        env.get(keys["panel_port"], ""),
        env.get(keys["web_base_path"]),
        scheme=env.get(keys["scheme"], panel_values.PANEL_URL_SCHEMES["http"]),
    )
    opener = _https_opener()
    return base_url, opener


def _bearer_headers(env: dict[str, str]) -> dict[str, str]:
    """Common headers for Bearer-authenticated API calls."""

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    headers = panel_values.PANEL_HTTP_HEADERS
    header_values = panel_values.PANEL_HTTP_HEADER_VALUES
    return {
        headers["authorization"]: (
            f"{header_values['bearer_prefix']}{env.get(keys['api_token'], '')}"
        ),
        headers["requested_with"]: header_values["xml_http_request"],
    }


def _bearer_form_headers(
    env: dict[str, str]
) -> dict[str, str]:
    """Bearer headers for the calls the panel reads its fields as a form.

    The routing check, the geodata check, the Xray template write, the
    balancer status and the outbound subscriptions read their fields with
    the form reader of the panel, so the content type and the body built
    by _form_body belong together.
    """

    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "form"
    ]
    return headers


def _form_body(values: Mapping[str, object]) -> bytes:
    """One request body in the form the panel reads its fields from.

    A boolean becomes the lower-case word "true" or "false", which is what
    the panel compares a flag against; urlencode alone would write the
    Python spelling and the panel would read the flag as absent. Every
    other value is written as text, and a value that is already text stays
    as it is.
    """

    fields: dict[str, str] = {}
    for name, value in values.items():
        if isinstance(value, bool):
            fields[name] = "true" if value else "false"
        else:
            fields[name] = str(value)
    return urllib.parse.urlencode(fields).encode("utf-8")


def _payload_of(
    status: int, body: str
) -> object | None:
    """The obj field of a successful panel answer, or None.

    A successful answer is status 200 with a JSON document whose success
    field is set. Shared by the list calls, so they parse an answer the
    same way and differ only in what they do with the payload.
    """

    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return None
    return data.get(answers["payload"])


def list_inbounds(
    env: dict[str, str],
    timeout: float,
) -> list[dict[str, object]]:
    """List every inbound owned by the authenticated user.

    Returns the obj array from the response. Returns an empty list on
    failure (unreachable panel, bad token, unexpected response shape).
    """

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_LIST_PATH}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    obj = _payload_of(status, body)
    if not isinstance(obj, list):
        return []
    return obj


def find_inbound_by_port(
    env: dict[str, str],
    port: int,
    timeout: float,
) -> dict[str, object] | None:
    """Find an inbound by its port number.

    Returns the first inbound dict whose port matches, or None when no
    inbound uses the given port.
    """

    inbounds = list_inbounds(env, timeout)
    port_key = panel_values.PANEL_FIELD_KEYS["port"]
    for inbound in inbounds:
        if isinstance(inbound, dict) and inbound.get(port_key) == port:
            return inbound
    return None


def find_inbound_by_tag(
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

    inbounds = list_inbounds(env, timeout)
    tag_key = panel_values.PANEL_FIELD_KEYS["tag"]
    for inbound in inbounds:
        if isinstance(inbound, dict) and inbound.get(tag_key) == tag:
            return inbound
    return None


def _message_result(
    status: int, body: str, ok_default: str
) -> tuple[bool, str]:
    """Parse a panel write response into (success, message).

    Status 0 means the panel was unreachable and an unparsable body is an
    unexpected response; otherwise the panel's own message field is
    reported and its success field decides the flag. Shared by every
    write helper, so the error wording stays identical across them. Both
    field names come from the panel answer keys of the config.
    """

    if status == 0:
        return False, "panel unreachable"
    try:
        resp = json.loads(body)
    except json.JSONDecodeError:
        return False, f"unexpected response (HTTP {status})"
    if not isinstance(resp, dict):
        return False, f"unexpected response (HTTP {status})"
    answers = panel_values.PANEL_ANSWER_KEYS
    msg = resp.get(answers["message"], "")
    if resp.get(answers["success"]):
        return True, msg or ok_default
    return False, msg or "unknown error"


def create_inbound(
    env: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Create a new inbound through the panel API.

    Returns (success, message). On success the message is the response
    msg field. On failure the message describes the error (port conflict,
    unreachable panel, etc.).
    """

    base_url, opener = _bearer_opener(env)
    data = json.dumps(payload).encode("utf-8")
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_ADD_PATH}",
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "inbound created")


def generate_reality_key(
    env: dict[str, str],
    timeout: float,
) -> tuple[str, str] | None:
    """Generate a new X25519 keypair for Reality through the panel API.

    Returns (private_key, public_key) on success, or None on failure.
    """

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_X25519_CERT_PATH}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return None
    obj = data.get(answers["payload"])
    if not isinstance(obj, dict):
        return None
    fields = panel_values.PANEL_FIELD_KEYS
    private_key = obj.get(fields["private_key"], "")
    public_key = obj.get(fields["public_key"], "")
    if not private_key or not public_key:
        return None
    return (private_key, public_key)


def build_vless_reality_payload(
    payload_template: str,
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

    The document itself is the configured template: it carries the field
    names and the protocol words of the panel API, so a panel version that
    renames a field or another protocol is answered there and not here.
    Every $placeholder of the template is filled with a JSON value, the
    filled document is parsed once and returned as the object the caller
    serialises, so a template that does not produce valid JSON fails at
    the parse instead of reaching the panel. The public key and the
    fingerprint go into the nested realitySettings.settings block of the
    template: the panel writes pbk into share links only when it finds the
    public key there, while the server itself needs only the private key.
    The payload is ready to be sent to /panel/api/inbounds/add.
    """

    template = Template(payload_template)
    rendered = template.substitute(
        remark=json.dumps(remark),
        port=json.dumps(port),
        dest=json.dumps(dest),
        server_names=json.dumps(list(server_names)),
        private_key=json.dumps(private_key),
        public_key=json.dumps(public_key),
        short_ids=json.dumps([short_id]),
        fingerprint=json.dumps(fingerprint),
        sniffing_protocols=json.dumps(list(sniffing_protocols)),
    )
    payload = json.loads(rendered)
    if not isinstance(payload, dict):
        raise TypeError("the inbound payload template must hold a JSON object")
    return payload


def panel_settings(
    env: dict[str, str],
    timeout: float,
) -> dict[str, object] | None:
    """Read the whole panel settings document, or None on failure.

    The panel serves its settings at POST /panel/api/setting/all; the
    response obj is the full settings document. None on an unreachable
    panel, a bad token or an unexpected response shape.
    """

    base_url, opener = _bearer_opener(env)
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_SETTING_ALL_PATH}",
        data=b"{}",
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return None
    obj = data.get(answers["payload"])
    if not isinstance(obj, dict):
        return None
    return obj


def update_panel_settings(
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

    base_url, opener = _bearer_opener(env)
    data = json.dumps(settings).encode("utf-8")
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_SETTING_UPDATE_PATH}",
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "settings updated")


def ensure_subscription_paths(
    env: dict[str, str],
    timeout: float,
) -> tuple[bool, str]:
    """Move the panel subscription paths off the well-known defaults.

    Reads the settings document, compares subPath, subJsonPath and
    subClashPath with the configured values and writes the document back
    only when one differs, so a rerun is a no-op. Returns (changed,
    message); a failure returns (False, error) with changed False.
    """

    settings = panel_settings(env, timeout)
    if settings is None:
        return False, "cannot read panel settings"
    fields = panel_values.PANEL_FIELD_KEYS
    wanted = {
        fields["sub_path"]: panel_values.SUBSCRIPTION_PATH,
        fields["sub_json_path"]: panel_values.SUBSCRIPTION_JSON_PATH,
        fields["sub_clash_path"]: panel_values.SUBSCRIPTION_CLASH_PATH,
    }
    if all(settings.get(key) == value for key, value in wanted.items()):
        return False, ""
    settings.update(wanted)
    ok, message = update_panel_settings(env, settings, timeout)
    if not ok:
        return False, message
    return True, (
        "subscription paths set to "
        f"{panel_values.SUBSCRIPTION_PATH}, {panel_values.SUBSCRIPTION_JSON_PATH}, "
        f"{panel_values.SUBSCRIPTION_CLASH_PATH}"
    )


def update_inbound(
    env: dict[str, str],
    inbound: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Replace an inbound through the Bearer API.

    The panel persists the whole inbound object, so the caller reads it
    with list_inbounds, changes the wanted keys and sends it back. The id
    is taken from the object itself.
    """

    base_url, opener = _bearer_opener(env)
    data = json.dumps(inbound).encode("utf-8")
    fields = panel_values.PANEL_FIELD_KEYS
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    inbound_id = inbound.get(fields["id"])
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_UPDATE_PATH.format(inbound_id=inbound_id)}",
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "inbound updated")


def upsert_inbound(
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

    fields = panel_values.PANEL_FIELD_KEYS
    tag = payload.get(fields["tag"])
    if not isinstance(tag, str) or not tag:
        return False, "inbound payload has no tag to identify it by"
    existing = find_inbound_by_tag(env, tag, timeout)
    if existing is None:
        return create_inbound(env, payload, timeout)
    replacement = dict(payload)
    replacement[fields["id"]] = existing.get(fields["id"])
    ok, message = update_inbound(env, replacement, timeout)
    return ok, message if not ok else f"inbound {tag} updated: {message}"


def delete_inbound(
    env: dict[str, str],
    inbound_id: int,
    timeout: float,
) -> tuple[bool, str]:
    """Delete one inbound by its id through the Bearer API."""

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_INBOUNDS_DELETE_PATH.format(inbound_id=inbound_id)}",
        headers=_bearer_headers(env),
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "inbound deleted")


def list_outbound_subscriptions(
    env: dict[str, str],
    timeout: float,
) -> list[dict[str, object]]:
    """List the panel subscriptions that build outbounds from a URL.

    A subscription is an address the panel fetches on a schedule and turns
    into outbounds whose tags carry its tag prefix; the Sotavpn bridge
    serves such an address on the loopback interface. Returns the obj array
    from the response and an empty list on failure, the same contract as
    list_inbounds.
    """

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_OUTBOUND_SUBS_PATH}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    obj = _payload_of(status, body)
    if not isinstance(obj, list):
        return []
    return obj


def find_outbound_subscription_by_remark(
    env: dict[str, str],
    remark: str,
    timeout: float,
) -> dict[str, object] | None:
    """Find one outbound subscription by the remark the panel shows.

    The remark is the human label of a subscription, and the task writes
    the configured remark, so the lookup stays stable across runs. Returns
    the subscription object or None when no subscription carries it.
    """

    key = panel_values.PANEL_FIELD_KEYS["subscription_remark"]
    for subscription in list_outbound_subscriptions(env, timeout):
        if isinstance(subscription, dict) and subscription.get(key) == remark:
            return subscription
    return None


def create_outbound_subscription(
    env: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Create an outbound subscription through the panel API.

    The fields travel as a form body, because the panel reads them with
    its form reader; a JSON body would leave the address empty and the
    panel would refuse the subscription.
    """

    base_url, opener = _bearer_opener(env)
    data = _form_body(payload)
    headers = _bearer_form_headers(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_OUTBOUND_SUBS_PATH}",
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "subscription created")


def update_outbound_subscription(
    env: dict[str, str],
    subscription: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Replace one outbound subscription, identified by its own id.

    The fields travel as a form body, the only body the panel reads them
    from, and the id travels in the address; the caller passes the payload
    it wants stored together with the id of the row it replaces.
    """

    base_url, opener = _bearer_opener(env)
    data = _form_body(subscription)
    fields = panel_values.PANEL_FIELD_KEYS
    headers = _bearer_form_headers(env)
    subscription_id = subscription.get(fields["id"])
    status, body = _api_call(
        opener,
        f"{base_url}"
        + panel_values.PANEL_OUTBOUND_SUBS_ITEM_PATH.format(
            subscription_id=subscription_id
        ),
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "subscription updated")


def upsert_outbound_subscription(
    env: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str]:
    """Create the subscription or replace the one with the same remark.

    A machine that already carries the subscription gets its definition
    replaced, so a rerun with a new address, a new tag prefix or a new
    update interval converges instead of failing on a duplicate; a machine
    without it gets the subscription created. The remark field of the
    payload is the identity, as the tag is for upsert_inbound.
    """

    fields = panel_values.PANEL_FIELD_KEYS
    remark = payload.get(fields["subscription_remark"])
    if not isinstance(remark, str) or not remark:
        return False, "subscription payload has no remark to identify it by"
    existing = find_outbound_subscription_by_remark(env, remark, timeout)
    if existing is None:
        return create_outbound_subscription(env, payload, timeout)
    replacement = dict(payload)
    replacement[fields["id"]] = existing.get(fields["id"])
    ok, message = update_outbound_subscription(env, replacement, timeout)
    return ok, message if not ok else f"subscription {remark} updated: {message}"


def refresh_outbound_subscription(
    env: dict[str, str],
    subscription_id: object,
    timeout: float,
) -> tuple[bool, str]:
    """Ask the panel to fetch the subscription and rebuild its outbounds.

    The panel also fetches on its own schedule; this call makes a fresh
    outbound list appear right away, which is what the routing checks of
    the same run need.
    """

    base_url, opener = _bearer_opener(env)
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    status, body = _api_call(
        opener,
        f"{base_url}"
        + panel_values.PANEL_OUTBOUND_SUBS_REFRESH_PATH.format(
            subscription_id=subscription_id
        ),
        data=b"{}",
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "subscription refreshed")


def list_balancer_status(
    env: dict[str, str],
    tags: tuple[str, ...],
    timeout: float,
) -> list[dict[str, object]]:
    """Ask the panel which member each load balancer currently picks.

    The panel answers this endpoint only to a form call, and it answers an
    object keyed by the balancer tag with one entry per requested tag: the
    member it selected, whether the operator pinned one by hand and
    whether the balancer is running. The caller can therefore prove that
    the pool has a live member. A list of entries is accepted as well,
    because a panel version may serve that shape; the query field name and
    the entry field names come from the config. An unreachable panel and
    an unexpected shape answer an empty list.
    """

    fields = panel_values.PANEL_FIELD_KEYS
    form = _form_body({fields["balancer_status_query"]: ",".join(tags)})
    base_url, opener = _bearer_opener(env)
    headers = _bearer_form_headers(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_BALANCER_STATUS_PATH}",
        data=form,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    obj = _payload_of(status, body)
    if isinstance(obj, dict):
        return [entry for entry in obj.values() if isinstance(entry, dict)]
    if isinstance(obj, list):
        return [entry for entry in obj if isinstance(entry, dict)]
    return []


def find_client(
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

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_CLIENT_GET_PATH.format(email=urllib.parse.quote(email))}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return None
    obj = data.get(answers["payload"])
    if not isinstance(obj, dict):
        return None
    client = obj.get(panel_values.PANEL_FIELD_KEYS["client"])
    return client if isinstance(client, dict) else None


def create_client(
    env: dict[str, str],
    inbound_id: int,
    client_id: str,
    email: str,
    sub_id: str,
    timeout: float,
) -> tuple[bool, str]:
    """Create one VLESS client attached to an inbound.

    The credential lives in the client.id field (stored as the client
    uuid); email is only the human label and must be unique. Whether the
    client is enabled is a config value, because a client created as a
    draft is a legitimate way to stage one. Returns (success, message)
    from the panel response.
    """

    base_url, opener = _bearer_opener(env)
    fields = panel_values.PANEL_FIELD_KEYS
    payload = {
        fields["client"]: {
            fields["id"]: client_id,
            fields["email"]: email,
            fields["enable"]: panel_values.CLIENT_ENABLED,
            fields["sub_id"]: sub_id,
        },
        fields["inbound_ids"]: [inbound_id],
    }
    data = json.dumps(payload).encode("utf-8")
    headers = _bearer_headers(env)
    headers[panel_values.PANEL_HTTP_HEADERS["content_type"]] = panel_values.PANEL_HTTP_HEADER_VALUES[
        "json"
    ]
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_CLIENT_ADD_PATH}",
        data=data,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "client created")


def client_links(
    env: dict[str, str],
    email: str,
    timeout: float,
) -> list[str]:
    """The share links of one client, or an empty list.

    The panel renders the links through the same subscription engine the
    panel UI uses, so the returned strings carry every connection
    parameter including pbk and the share address.
    """

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_CLIENT_LINKS_PATH.format(email=urllib.parse.quote(email))}",
        headers=_bearer_headers(env),
        timeout=timeout,
    )
    if status != 200:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return []
    obj = data.get(answers["payload"])
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

    base_url, opener = _bearer_opener(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_XRAY_STATUS_PATH}",
        headers=_bearer_headers(env),
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return None
    fields = panel_values.PANEL_FIELD_KEYS
    blob = _decoded_json_object(data.get(answers["payload"]))
    if blob is None:
        return None
    settings_text = blob.get(fields["xray_setting"])
    settings = _decoded_json_object(settings_text)
    if settings is None:
        return None
    test_url = blob.get(fields["outbound_test_url"])
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


def _payload_text(body: str, answers: dict[str, str]) -> str:
    """The text a panel answer carries in its payload, or "".

    The status answer carries an object and the answer of the last core
    output carries a plain string; this reads the string form, so a caller
    that wants the text does not decode the envelope twice.
    """

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return ""
    payload = data.get(answers["payload"])
    return payload if isinstance(payload, str) else ""


def _last_line(text: str) -> str:
    """The last non-empty line of a text, with its edges trimmed.

    A core process prints its history and ends with the line that says why
    it stopped, so the last line is the part a warning needs.
    """

    lines = [trim_whitespace(line) for line in text.splitlines()]
    return next((line for line in reversed(lines) if line), "")


def core_diagnostics(
    env: dict[str, str],
    timeout: float,
) -> str:
    """What the panel says about its core, for a warning.

    Reads the panel status and the last line the core printed, so a core
    that never answered is reported with the state the panel sees and with
    the text of the core itself instead of with a guess of ours. Every
    failure to read is reported as its own short reason and never raises:
    this runs while a warning of a completed task is composed.
    """

    base_url, opener = _bearer_opener(env)
    headers = _bearer_headers(env)
    method = panel_values.PANEL_HTTP_METHODS["get"]
    answers = panel_values.PANEL_ANSWER_KEYS
    keys = panel_values.PANEL_STATUS_KEYS
    parts: list[str] = []
    status_code, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_STATUS_PATH}",
        headers=headers,
        method=method,
        timeout=timeout,
    )
    if status_code == 0:
        parts.append("the panel did not answer")
    else:
        data = _decoded_json_object(body)
        obj = data.get(answers["payload"]) if data is not None else None
        xray = obj.get(keys["xray"]) if isinstance(obj, dict) else None
        if isinstance(xray, dict):
            state = xray.get(keys["state"])
            error = xray.get(keys["error_msg"])
            parts.append(
                f"the panel reports its core {state}"
                if isinstance(state, str) and state
                else "the panel reports no core state"
            )
            if isinstance(error, str) and error:
                parts.append(f"with the error {error}")
        else:
            parts.append("the panel reported no core state")
    result_code, result_body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_XRAY_RESULT_PATH}",
        headers=headers,
        method=method,
        timeout=timeout,
    )
    if result_code != 0:
        last_line = _last_line(_payload_text(result_body, answers))
        if last_line:
            parts.append(f"and the core printed {last_line!r}")
    return ", ".join(parts)


def write_xray_template(
    env: dict[str, str],
    template: XrayTemplate,
    timeout: float,
) -> tuple[bool, str]:
    """Write the Xray template back through the Bearer API.

    The panel takes the document and its test URL as form fields and
    reconciles the running core with it: the change goes through the core
    API when the diff allows that, and the core is stopped and started
    again otherwise. The panel answers the write before a restart it
    triggered has finished, so the core is not ready the moment this
    returns. Returns (success, message); the caller waits for the core to
    answer and then verifies it, because a stored template alone has
    already been observed to disagree with what the core routes.
    """

    base_url, opener = _bearer_opener(env)
    fields = panel_values.PANEL_FIELD_KEYS
    form = _form_body(
        {
            fields["xray_setting"]: json.dumps(template.settings),
            fields["outbound_test_url"]: template.outbound_test_url,
        }
    )
    headers = _bearer_form_headers(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_XRAY_UPDATE_PATH}",
        data=form,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    return _message_result(status, body, "xray template applied")


def validate_geodata_tokens(
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

    known_kinds = (panel_values.PANEL_GEODATA_DOMAIN_KIND, panel_values.PANEL_GEODATA_IP_KIND)
    if kind not in known_kinds:
        raise ValueError(
            f"unknown geodata kind {kind!r}, expected one of {known_kinds}"
        )
    if not tokens:
        return {}
    base_url, opener = _bearer_opener(env)
    fields = panel_values.PANEL_FIELD_KEYS
    form = _form_body({fields["kind"]: kind, fields["tokens"]: ",".join(tokens)})
    headers = _bearer_form_headers(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_XRAY_GEODATA_VALIDATE_PATH}",
        data=form,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    if status != 200:
        return {token: "panel unreachable" for token in tokens}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {token: f"unexpected response (HTTP {status})" for token in tokens}
    answers = panel_values.PANEL_ANSWER_KEYS
    if not isinstance(data, dict) or not data.get(answers["success"]):
        return {token: "the panel rejected the check" for token in tokens}
    obj = data.get(answers["payload"])
    if not isinstance(obj, list):
        return {token: "the panel answered no result" for token in tokens}
    rejected: dict[str, str] = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        token = item.get(fields["token"])
        if not isinstance(token, str) or not token:
            continue
        reason = item.get(answers["reason"])
        rejected[token] = reason if isinstance(reason, str) and reason else "rejected"
    return rejected


def route_test(
    env: dict[str, str],
    *,
    inbound_tag: str,
    network: str,
    protocol: str,
    domain: str = "",
    address: str = "",
    port: int = 0,
    timeout: float,
) -> tuple[bool | None, str]:
    """Ask the running core which outbound it picks for a destination.

    Exactly one of domain and address is given. The network and the
    protocol of the question are arguments and not defaults, because they
    are the vocabulary of the core and a value of the config. The answer
    comes from the core's own routing engine, so it is the only honest
    check that a written policy reached the traffic; a stored template can
    disagree with the core. Returns (decision, answer) with three states,
    because the caller must never read a core that has not finished
    starting as a disclosure about its routing: True means the core
    answered and answer is the tag it chose, False means the core answered
    and no rule matched the destination, and None means no decision was
    obtained at all, with answer naming why (the panel was unreachable,
    the panel reported an error, or its answer carried no decision). The
    task writes a template by reconciling the running core, which the
    panel may do by restarting it, so None is the state a caller waits on.
    """

    base_url, opener = _bearer_opener(env)
    fields = panel_values.PANEL_FIELD_KEYS
    request: dict[str, str] = {
        fields["port"]: str(port),
        fields["network"]: network,
        fields["protocol"]: protocol,
        fields["inbound_tag"]: inbound_tag,
    }
    if domain:
        request[fields["domain"]] = domain
    elif address:
        request[fields["ip"]] = address
    else:
        raise ValueError("route_test needs a domain or an address")
    form = _form_body(request)
    headers = _bearer_form_headers(env)
    status, body = _api_call(
        opener,
        f"{base_url}{panel_values.PANEL_XRAY_ROUTE_TEST_PATH}",
        data=form,
        headers=headers,
        method=panel_values.PANEL_HTTP_METHODS["post"],
        timeout=timeout,
    )
    if status == 0:
        return None, "panel unreachable"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, f"unexpected response (HTTP {status})"
    if not isinstance(data, dict):
        return None, f"unexpected response (HTTP {status})"
    answers = panel_values.PANEL_ANSWER_KEYS
    if not data.get(answers["success"]):
        message = data.get(answers["message"])
        return None, message if isinstance(message, str) and message else "route test failed"
    obj = data.get(answers["payload"])
    if not isinstance(obj, dict):
        return None, "the panel answered no routing decision"
    outbound_tag = obj.get(fields["outbound_tag"])
    if (
        not obj.get(fields["matched"])
        or not isinstance(outbound_tag, str)
        or not outbound_tag
    ):
        return False, "no routing rule matched the destination"
    return True, outbound_tag
