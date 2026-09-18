"""The 3x-ui panel as a service: install it and keep it reachable.

This module owns the panel itself, not the Xray objects it carries: the
version gate and the official installer, the proquint credentials the
installer receives, the fixed panel port, the install-result.env file
the official installer writes, the vault entry that stores the panel
credentials (stage 2) and the subscription paths of the panel settings.

The Xray side of the machine lives next to it: the certificate stage in
pyntara.xray_certificate, the inbound and the connection profile in
pyntara.xray_inbound, the local proxy and its routing policy in
pyntara.xray_local_proxy (docs/spec/3x-ui.md).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pyntara import metrics
from pyntara import xui as xui_client
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    download_command,
    ensure_port_free,
    proquint_encode,
    run_command,
    service_is_active,
    substituted_command,
    version_from_output,
)
from pyntara.values import local_vault_setup as local_vault_values
from pyntara.values import three_x_ui_xray_setup as panel_values


def _panel_binary() -> Path:
    """The installed panel binary, from its configured file name.

    Every call the task makes to the panel binary is built from this one
    path, so the file name of the binary is a single declared value and a
    future release that renames it needs no code change.
    """

    return panel_values.INSTALL_DIR / panel_values.BINARY_FILE_NAME


def _panel_command(
    template: tuple[str, ...], **values: str
) -> list[str]:
    """The argv of one panel CLI call, with its placeholders filled in.

    The path of the binary enters every call as {binary} and the values
    the call site knows travel as their own placeholders, so the verbs
    and the flags of the tool are declared next to the binary
    itself.
    """

    return substituted_command(template, {**values, "binary": str(_panel_binary())})


def _installed_version(timeout: float) -> str | None:
    """The installed x-ui version from the binary -v output, or None.

    A missing binary, a nonzero exit or a hang means 3x-ui is not
    installed: the task treats the version as absent and runs the
    installer. The missing executable raises FileNotFoundError (an
    OSError), which subprocess raises regardless of check; the version
    triple is searched in stdout and stderr, because the exact output
    format may change.
    """

    try:
        result = run_command(
            _panel_command(panel_values.PANEL_VERSION_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    return version_from_output(result.stdout + "\n" + result.stderr)


def _download_installer(
    timeout: float,
) -> Path:
    """Download the official installer into a temporary file.

    Returns the path of the downloaded script. The command is the declared
    download call. Raises RuntimeError when curl fails, so the caller
    reports the reason.
    """

    _fd, name = tempfile.mkstemp(prefix="x-ui-install-", suffix=".sh")
    script_path = Path(name)
    try:
        run_command(
            download_command(script_path, panel_values.INSTALL_SCRIPT_URL),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        try:
            script_path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"cannot download installer {panel_values.INSTALL_SCRIPT_URL}: {exc}"
        ) from None
    return script_path


def _credential_env() -> dict[str, str]:
    """The XUI_ credential and port env vars for the installer.

    The panel port is fixed to panel_values.PANEL_PORT; the username, password
    and webBasePath are proquint encodings of fresh random bytes, whose
    length is a declared value (docs/spec/3x-ui.md, Credentials
    boundary). The installer applies
    these values only when the panel is in the default state (first
    deployment); on an existing panel with custom credentials it
    preserves the current values, so a rerun never rotates them. The
    applied values land in /etc/x-ui/install-result.env for stage 2.
    """

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    return {
        keys["username"]: proquint_encode(os.urandom(panel_values.RANDOM_USERNAME_BYTES), ""),
        keys["password"]: proquint_encode(os.urandom(panel_values.RANDOM_SECRET_BYTES), ""),
        keys["web_base_path"]: proquint_encode(
            os.urandom(panel_values.RANDOM_SECRET_BYTES), "-"
        ),
        keys["panel_port"]: str(panel_values.PANEL_PORT),
    }


def _installer_environment(
    extra_env: dict[str, str]
) -> dict[str, str]:
    """The environment the official installer runs in.

    The installer is a third-party shell script that resolves python3 from
    PATH for its own steps. The engine runs inside the project venv, whose
    bin directory would win that lookup with an interpreter that carries
    none of the installer's dependencies, so the venv leaves PATH and
    VIRTUAL_ENV is cleared: the installer then uses the system python3,
    where its packages live. The XUI_* values of extra_env stay untouched.
    """

    environment = dict(os.environ)
    venv_root = environment.get("VIRTUAL_ENV") or sys.prefix
    environment["PATH"] = os.pathsep.join(
        entry
        for entry in environment.get("PATH", "").split(os.pathsep)
        if entry
        and not (entry == venv_root or entry.startswith(f"{venv_root}{os.sep}"))
    )
    environment["VIRTUAL_ENV"] = ""
    environment[panel_values.PANEL_ENVIRONMENT_KEYS["noninteractive"]] = "1"
    environment.update(extra_env)
    return environment


def _run_installer(
    script_path: Path,
    timeout: float,
    extra_env: dict[str, str],
) -> None:
    """Run the downloaded official installer in non-interactive mode.

    XUI_NONINTERACTIVE=1 makes the installer replace every interactive
    prompt with an environment-variable value or a sane default. The
    extra env carries the proquint credentials and the fixed panel port;
    the installer applies them on first deployment and preserves the
    current values on an existing panel with custom credentials. Raises
    CalledProcessError or TimeoutExpired, so the caller reports the
    reason.
    """

    try:
        run_command(
            substituted_command(
                panel_values.INSTALLER_RUN_COMMAND, {"script_path": str(script_path)}
            ),
            extra_env=_installer_environment(extra_env),
            timeout=timeout,
        )
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def _wait_active(
    service_name: str,
    wait_seconds: int,
    check_delay_seconds: int,
    timeout: float,
) -> bool:
    """True when the service reports active within the readiness budget.

    The service may report activating for a moment after start, so the
    check is repeated with a pause until the budget runs out. The budget
    is a number of seconds and not a count of checks, because a count with
    a fixed pause is a hidden fixed sleep that reports a slow machine as a
    service that never became active.
    """

    started = time.monotonic()
    while True:
        if service_is_active(service_name, timeout):
            return True
        if time.monotonic() - started >= wait_seconds:
            return False
        time.sleep(check_delay_seconds)


def _build_notes(env: dict[str, str]) -> str:
    """Build the notes field for the vault entry from the env dict.

    The notes carry the additional values that do not fit into the
    standard KeePass fields: the panel port, the web base path, the API
    token and the database type, under the names the panel environment
    keys of the config give them. Each is written as key=value on its
    own line.
    """

    lines: list[str] = []
    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    for key in (
        keys["panel_port"],
        keys["web_base_path"],
        keys["api_token"],
        keys["db_type"],
    ):
        value = env.get(key)
        if value:
            lines.append(f"{key}={value}")
    return "\n".join(lines)


def _panel_environment_or_warning(
    timeout: float
) -> tuple[dict[str, str] | None, TaskResult | None]:
    """The panel environment, or the warning that says why it is missing.

    Reading install-result.env is the first step of every stage that talks
    to the panel, and a file the panel has not written yet and a file that
    lacks a required key are both ordinary states of a machine whose panel
    has not started: the caller reports the reason as a warning of a
    completed task and never as a failure. Exactly one of the two answers
    is set.
    """

    try:
        return xui_client.panel_environment(timeout), None
    except FileNotFoundError:
        return None, TaskResult(
            success=True,
            changed=False,
            warnings=("install-result.env not found: panel may not have started yet",),
        )
    except RuntimeError as exc:
        return None, TaskResult(
            success=True,
            changed=False,
            warnings=(str(exc),),
        )


def _stage2(
    timeout: float,
) -> TaskResult | None:
    """Run stage 2: read credentials, verify session, store in vault.

    Returns None on success (the vault entry was created or is already
    current). Returns a TaskResult when a non-fatal problem occurs
    (missing install-result.env, unreachable panel, vault unavailable),
    so the caller returns it as a done-with-warnings result.
    """

    # Read the credentials the panel generated on first start.
    env, warning = _panel_environment_or_warning(timeout)
    if env is None:
        return warning
    _log("stage 2: read credentials from install-result.env")

    # Verify the session through the panel REST API.
    if not xui_client.login_and_verify(env, timeout):
        _log("stage 2: panel login failed, credentials may be stale")
        return TaskResult(
            success=True,
            changed=False,
            warnings=(
                "panel login failed: panel may be unreachable or credentials invalid",
            ),
        )
    _log("stage 2: panel login successful")

    # Open the runtime vault.
    kp = metrics.open_runtime_vault()
    if kp is None:
        return TaskResult(
            success=True,
            changed=False,
            warnings=("runtime vault unavailable: credentials not stored",),
        )
    _log("stage 2: runtime vault opened")

    # Build the entry values.
    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    base_url = xui_client.build_panel_url(
        panel_values.PANEL_HTTP_ADDRESS,
        env.get(keys["panel_port"], ""),
        env.get(keys["web_base_path"]),
        scheme=env.get(keys["scheme"], panel_values.PANEL_URL_SCHEMES["http"]),
    )
    username = env.get(keys["username"], "")
    password = env.get(keys["password"], "")
    notes = _build_notes(env)

    # Find or create the entry.
    entry = kp.find_entries(
        title=panel_values.VAULT_ENTRY_TITLE,
        group=kp.root_group,
        recursive=False,
        first=True,
    )
    if entry is not None:
        # Entry exists: update if values differ.
        if (
            entry.username == username
            and entry.password == password
            and (entry.url or "") == base_url
            and (entry.notes or "") == notes
        ):
            _log("stage 2: vault entry already current")
            return None
        entry.username = username
        entry.password = password
        entry.url = base_url
        entry.notes = notes
        _log("stage 2: updating existing vault entry")
    else:
        kp.add_entry(
            kp.root_group,
            panel_values.VAULT_ENTRY_TITLE,
            username,
            password,
            url=base_url,
            notes=notes,
        )
        _log("stage 2: creating new vault entry")

    kp.save(filename=str(local_vault_values.LOCAL_VAULT_PATH))
    _log("stage 2: vault entry saved")
    return None


def _actual_panel_port(timeout: float) -> str | None:
    """The panel port from `x-ui setting -show true`, or None.

    The setting output prints "port: N" among other values; the first
    matching line wins. None when the panel cannot be queried or the
    line is absent.
    """

    try:
        result = run_command(
            _panel_command(panel_values.PANEL_SETTINGS_QUERY_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired, OSError:
        return None
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = re.search(r"^\s*port:\s*(\d+)\s*$", line)
        if match:
            return match.group(1)
    return None


def _converge_panel_port(
    timeout: float
) -> tuple[bool, str | None]:
    """Bring the panel to the configured port; returns (changed, message).

    Reads the actual panel port from `x-ui setting -show true`. When it
    differs from panel_values.PANEL_PORT the target port is freed, the new port
    is set through `x-ui setting -port` and the panel restarts so the
    change takes effect. Raises RuntimeError when the port stays
    occupied or the panel cannot be reconfigured. An unreadable panel
    port is reported as a message with changed=False, so a transient
    read failure does not fail the task.
    """

    actual = _actual_panel_port(timeout)
    if actual is None:
        return False, "cannot read panel port"
    if actual == str(panel_values.PANEL_PORT):
        return False, None
    _log(f"moving the panel from port {actual} to {panel_values.PANEL_PORT}")
    try:
        ensure_port_free(
            panel_values.PANEL_PORT,
            panel_values.SERVICE_UNIT_NAME,
            timeout,
            service_process_name=panel_values.SERVICE_PROCESS_NAME,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"panel port {panel_values.PANEL_PORT} still occupied: {exc}"
        ) from None
    try:
        run_command(
            _panel_command(
                panel_values.PANEL_PORT_COMMAND,
                port=str(panel_values.PANEL_PORT),
            ),
            timeout=timeout,
        )
        run_command(
            substituted_command(
                panel_values.SERVICE_RESTART_COMMAND,
                {"service_unit_name": panel_values.SERVICE_UNIT_NAME},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"cannot move panel to port {panel_values.PANEL_PORT}: {exc}"
        ) from None
    return True, f"panel port moved to {panel_values.PANEL_PORT}"


def _wait_panel_http(
    timeout: float,
) -> bool:
    """True when the panel answers HTTP on the configured port.

    Polls the panel's csrf-token endpoint until it answers. A port
    migration restarts the panel and the HTTP listener can trail the
    systemd active state by a moment; stage 2 would otherwise report a
    false login failure. The scheme follows the configured certificate
    and TLS is not verified, mirroring the API client. An unreadable
    install-result.env leaves the web base path empty, which still
    detects the listener. The budget in seconds, the pause between two
    checks and the probe timeout are declared values: a slow link must
    not be reported as an unreachable panel.
    """

    budget = panel_values.PANEL_LISTENER_WAIT_SECONDS
    delay = panel_values.READINESS_CHECK_DELAY_SECONDS
    _log(
        f"waiting for the panel HTTP listener on port {panel_values.PANEL_PORT} "
        f"(up to {budget} s)"
    )
    web_path = ""
    try:
        env = xui_client.parse_install_result_env(
            Path(panel_values.INSTALL_RESULT_ENV_PATH),
            xui_client.panel_required_environment_keys(),
        )
        web_path = env.get(panel_values.PANEL_ENVIRONMENT_KEYS["web_base_path"], "")
    except FileNotFoundError, RuntimeError, OSError:
        pass
    try:
        scheme = xui_client.panel_scheme(timeout)
    except subprocess.TimeoutExpired, OSError:
        scheme = "http"
    base_url = xui_client.build_panel_url(
        panel_values.PANEL_HTTP_ADDRESS, str(panel_values.PANEL_PORT), web_path, scheme=scheme
    )
    started = time.monotonic()
    while True:
        try:
            result = run_command(
                substituted_command(
                    panel_values.PANEL_PROBE_COMMAND,
                    {"timeout_seconds": str(panel_values.PROBE_TIMEOUT_SECONDS)},
                )
                + [f"{base_url}{panel_values.PANEL_CSRF_TOKEN_PATH}"],
                check=False,
                capture=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired, OSError:
            return False
        if result.returncode == 0:
            return True
        if time.monotonic() - started >= budget:
            return False
        time.sleep(delay)


def _env_values(path: Path) -> tuple[dict[str, str], list[str]]:
    """The key=value pairs of an env file and the order of its keys.

    A file that cannot be read answers an empty mapping and an empty
    order, so a caller reports that there is nothing to write instead of
    raising on the machine that owns the file. The shared parse keeps the
    shape of the file in one place, so the sync of install-result.env and
    the credential takeover read it the same way.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, []
    values: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
            order.append(key.strip())
    return values, order


def _rewrite_env(path: Path, updates: dict[str, str]) -> bool:
    """Apply key=value updates to an env file, preserving line order.

    Reads the file, keeps the order of the existing keys, appends keys
    that are not present yet, replaces the given pairs and writes back
    only when something changed. Returns True when the file was
    rewritten. Shared by the install-result.env sync and the credential
    takeover, so the file is written through one mechanism.
    """

    if not path.is_file():
        return False
    values, order = _env_values(path)
    changed = False
    for key, value in updates.items():
        if values.get(key) != value:
            values[key] = value
            changed = True
    if not changed:
        return False
    new_lines = []
    for key in order:
        new_lines.append(f"{key}={values[key]}")
    for key, value in values.items():
        if key not in order:
            new_lines.append(f"{key}={value}")
    try:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _sync_install_result_env(timeout: float) -> bool:
    """Sync install-result.env so its port, scheme and url match reality.

    The panel port after the convergence and the scheme after the HTTPS
    stage set the certificate are written through the shared env rewrite,
    so consumers never read a stale port or a scheme the panel does not
    serve.
    """

    env_path = Path(panel_values.INSTALL_RESULT_ENV_PATH)
    if not env_path.is_file():
        return False
    values, _ = _env_values(env_path)
    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    port = str(panel_values.PANEL_PORT)
    updates: dict[str, str] = {keys["panel_port"]: port}
    url = values.get(keys["access_url"])
    if url is not None:
        try:
            scheme = xui_client.panel_scheme(timeout)
        except subprocess.TimeoutExpired, OSError:
            scheme = None
        old_scheme = url.split("://", 1)[0] if "://" in url else "http"
        host = ""
        if "://" in url:
            host = url.split("://", 1)[1].split("/", 1)[0]
            if ":" in host:
                host = host.split(":", 1)[0]
        new_url = f"{scheme or old_scheme}://{host}:{port}"
        web_path = values.get(keys["web_base_path"], "").strip("/")
        if web_path:
            new_url += f"/{web_path}"
        if url != new_url:
            updates[keys["access_url"]] = new_url
    _log(f"syncing {env_path} with the real panel port and scheme")
    return _rewrite_env(env_path, updates)


def _takeover_credentials(
    timeout: float,
    creds: dict[str, str],
) -> tuple[bool, str]:
    """Force-apply fresh proquint credentials and webBasePath to the panel.

    Runs in force mode on an existing panel: the installer preserves the
    current credentials and webBasePath on a non-default panel, so the
    task applies the fresh values it generated for the installer directly
    through `x-ui setting`, rewrites install-result.env through the shared
    helper and restarts the panel, so stage 2 stores the new values.
    Returns (changed, message); a failure returns (False, error).
    """

    keys = panel_values.PANEL_ENVIRONMENT_KEYS
    username = creds.get(keys["username"], "")
    password = creds.get(keys["password"], "")
    web_base_path = creds.get(keys["web_base_path"], "")
    try:
        run_command(
            _panel_command(
                panel_values.PANEL_CREDENTIALS_COMMAND,
                username=username,
                password=password,
                web_base_path=web_base_path,
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot set panel credentials: {exc}"
    _rewrite_env(
        Path(panel_values.INSTALL_RESULT_ENV_PATH),
        {
            keys["username"]: username,
            keys["password"]: password,
            keys["web_base_path"]: web_base_path,
        },
    )
    try:
        run_command(
            substituted_command(
                panel_values.SERVICE_RESTART_COMMAND,
                {"service_unit_name": panel_values.SERVICE_UNIT_NAME},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot restart {panel_values.SERVICE_UNIT_NAME}: {exc}"
    _wait_panel_http(timeout)
    return True, (
        f"panel credentials and webBasePath set to fresh proquint "
        f"values ({web_base_path})"
    )


def _stage_settings(
    timeout: float
) -> tuple[bool, str] | None:
    """Move the panel subscription paths off the well-known defaults.

    Runs after stage 2, when the panel credentials are readable. Returns
    None when the paths already match the config, and (True, message)
    after writing them. Raises RuntimeError with the reason when the
    panel is unreachable or the write fails, so the caller reports a
    warning and the rest of the task continues.
    """

    try:
        env = xui_client.panel_environment(timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        raise RuntimeError(str(exc)) from None
    changed, message = xui_client.ensure_subscription_paths(env, timeout)
    if not changed and not message:
        return None
    if not changed:
        raise RuntimeError(message)
    return True, message
