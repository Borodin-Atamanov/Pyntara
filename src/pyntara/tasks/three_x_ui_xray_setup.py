"""Task three_x_ui_xray_setup: wrap the official 3x-ui installer.

The task deploys the 3x-ui Xray panel as a system service by wrapping
the official install.sh of the configured repository. The newest release
tag comes from the GitHub releases API
(https://api.github.com/repos/{repo}/releases/latest); the task compares
it with the installed version read from the x-ui binary and with the
enabled and active state of the service. When the installed version
equals the newest release tag and the service is already enabled and
active, the task returns a plain done result with changed=False: the
official installer always tears the panel down and rebuilds it, so it
must not be run on a working panel just to confirm the state. Otherwise
the task downloads the official install.sh and runs it in non-interactive
mode (XUI_NONINTERACTIVE=1).

Stage 2 reads the credentials the panel generated on first start from
/etc/x-ui/install-result.env, logs in through the panel REST API to
verify the session, and stores the credentials in the runtime vault
(/var/lib/pyntara/secrets/pyntara.vault) in a single KeePass entry
named by vault_entry_title. The username and password fields carry the
panel admin credentials; the url field carries the panel base URL; the
notes field carries the additional values (XUI_PANEL_PORT,
XUI_WEB_BASE_PATH, XUI_API_TOKEN, XUI_DB_TYPE) as key=value lines.
Stage 2 runs after every install and on every rerun where the target
state is already reached, so the vault entry is always up to date.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from pyntara import metrics
from pyntara import xui as xui_client
from pyntara.config import Config, ThreeXuiXraySetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command, service_is_active, service_is_enabled

# The x-ui binary prints its version as a bare dotted triple, e.g. 3.7.0.
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")

# The release tag carries a leading v, the version output does not; the
# comparison normalizes the prefix away on the tag side.
TAG_VERSION_PATTERN = re.compile(r"^v?")


def _normalized_version(value: str) -> str:
    """The version with an optional leading v stripped."""

    return TAG_VERSION_PATTERN.sub("", value)


def _release_tag(release: dict[str, object]) -> str:
    """The tag_name of a release payload; raises RuntimeError when absent."""

    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("release payload has no tag_name")
    return tag


def _fetch_release_json(repo: str, timeout: float) -> dict[str, object]:
    """The latest release payload from the GitHub releases API.

    Raises RuntimeError when the request fails or the payload is not
    usable JSON, so the caller reports the reason instead of a raw
    exception.
    """

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    result = run_command(
        ["curl", "--fail", "--silent", "--show-error", url],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch {url}: exit {result.returncode}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cannot parse release JSON from {url}: {exc}") from None
    if not isinstance(data, dict):
        raise TypeError(f"unexpected release payload from {url}")
    return data


def _installed_version(
    cfg: ThreeXuiXraySetupConfig, timeout: float
) -> str | None:
    """The installed x-ui version from the binary -v output, or None.

    A missing binary, a nonzero exit or a hang means 3x-ui is not
    installed: the task treats the version as absent and runs the
    installer. The missing executable raises FileNotFoundError (an
    OSError), which subprocess raises regardless of check; the version
    triple is searched in stdout and stderr, because the exact output
    format may change.
    """

    binary = cfg.install_dir / "x-ui"
    try:
        result = run_command(
            [str(binary), "-v"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    match = VERSION_PATTERN.search(result.stdout + "\n" + result.stderr)
    return match.group(1) if match else None


def _download_installer(
    cfg: ThreeXuiXraySetupConfig, timeout: float
) -> Path:
    """Download the official installer into a temporary file.

    Returns the path of the downloaded script. Raises RuntimeError when
    curl fails, so the caller reports the reason.
    """

    _fd, name = tempfile.mkstemp(prefix="x-ui-install-", suffix=".sh")
    script_path = Path(name)
    try:
        run_command(
            [
                "curl",
                "--fail",
                "--location",
                "--retry",
                "15",
                "--retry-delay",
                "3",
                "--retry-all-errors",
                "--retry-connrefused",
                "--silent",
                "--show-error",
                "--output",
                str(script_path),
                cfg.install_script_url,
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        try:
            script_path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"cannot download installer {cfg.install_script_url}: {exc}"
        ) from None
    return script_path


def _run_installer(script_path: Path, timeout: float) -> None:
    """Run the downloaded official installer in non-interactive mode.

    XUI_NONINTERACTIVE=1 makes the installer replace every interactive
    prompt with an environment-variable value or a sane default. The
    stage-1 task sets no username, password or port, so the panel
    generates them itself and saves them to /etc/x-ui/install-result.env
    for stage 2 to read. Raises CalledProcessError or TimeoutExpired,
    so the caller reports the reason.
    """

    try:
        run_command(
            ["bash", str(script_path)],
            extra_env={"XUI_NONINTERACTIVE": "1"},
            timeout=timeout,
        )
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def _wait_active(
    service_name: str,
    attempts: int,
    retry_delay_seconds: int,
    timeout: float,
) -> bool:
    """True when the service reports active within the readiness loop.

    The service may report activating for a moment after start, so the
    check is repeated with a pause until attempts run out.
    """

    for _ in range(attempts):
        time.sleep(retry_delay_seconds)
        if service_is_active(service_name, timeout):
            return True
    return False


def _build_notes(env: dict[str, str]) -> str:
    """Build the notes field for the vault entry from the env dict.

    The notes carry the additional values that do not fit into the
    standard KeePass fields: XUI_PANEL_PORT, XUI_WEB_BASE_PATH,
    XUI_API_TOKEN, XUI_DB_TYPE. Each is written as key=value on its
    own line.
    """

    lines: list[str] = []
    for key in ("XUI_PANEL_PORT", "XUI_WEB_BASE_PATH", "XUI_API_TOKEN", "XUI_DB_TYPE"):
        value = env.get(key)
        if value:
            lines.append(f"{key}={value}")
    return "\n".join(lines)


def _stage2(
    cfg: ThreeXuiXraySetupConfig,
    full_config: Config,
    timeout: float,
) -> TaskResult | None:
    """Run stage 2: read credentials, verify session, store in vault.

    Returns None on success (the vault entry was created or is already
    current). Returns a TaskResult when a non-fatal problem occurs
    (missing install-result.env, unreachable panel, vault unavailable),
    so the caller returns it as a done-with-warnings result.
    """

    # Read the credentials the panel generated on first start.
    try:
        env = xui_client.parse_install_result_env(cfg.install_result_env_path)
    except FileNotFoundError:
        return TaskResult(
            success=True,
            changed=False,
            warnings=("install-result.env not found: panel may not have started yet",),
        )
    except RuntimeError as exc:
        return TaskResult(
            success=True,
            changed=False,
            warnings=(str(exc),),
        )
    _log("stage 2: read credentials from install-result.env")

    # Verify the session through the panel REST API.
    if not xui_client.login_and_verify(cfg, env, timeout):
        _log("stage 2: panel login failed, credentials may be stale")
        return TaskResult(
            success=True,
            changed=False,
            warnings=("panel login failed: panel may be unreachable or credentials invalid",),
        )
    _log("stage 2: panel login successful")

    # Open the runtime vault.
    kp = metrics.open_runtime_vault(full_config)
    if kp is None:
        return TaskResult(
            success=True,
            changed=False,
            warnings=("runtime vault unavailable: credentials not stored",),
        )
    _log("stage 2: runtime vault opened")

    # Build the entry values.
    base_url = xui_client.build_panel_url(
        cfg.panel_http_address,
        env.get("XUI_PANEL_PORT", ""),
        env.get("XUI_WEB_BASE_PATH"),
    )
    username = env.get("XUI_USERNAME", "")
    password = env.get("XUI_PASSWORD", "")
    notes = _build_notes(env)

    # Find or create the entry.
    entry = kp.find_entries(
        title=cfg.vault_entry_title,
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
            cfg.vault_entry_title,
            username,
            password,
            url=base_url,
            notes=notes,
        )
        _log("stage 2: creating new vault entry")

    kp.save(filename=str(full_config.local_vault_setup.local_vault_path))
    _log("stage 2: vault entry saved")
    return None


def task(ctx: Context) -> TaskResult:
    """Wrap the official 3x-ui installer; done when the same version runs.

    The goal is reached when the installed version equals the newest
    release tag and the service is enabled and active; the task then
    returns changed=False without invoking the installer, because the
    official installer always tears the panel down and rebuilds it. A
    missing version, a version mismatch, a disabled or inactive service,
    or force mode runs the official install.sh non-interactively and
    waits for the service to become active. After the installer finishes
    (or when the target state is already reached), stage 2 reads the
    panel credentials, verifies the session through the REST API and
    stores them in the runtime vault. Every step is reported to stdout:
    measurements and decisions as single lines that include their result,
    long-running commands as a line before and a line after. Any failure
    is returned as an error TaskResult: the runner continues with the
    remaining tasks and never stops here.
    """

    cfg = ctx.config.three_x_ui_xray_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "three_x_ui_xray_setup" in ctx.force_tasks

    try:
        release = _fetch_release_json(cfg.github_repo, timeout)
        tag = _release_tag(release)
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    _log(f"checking latest release: {tag}")

    installed_version = _installed_version(cfg, timeout)
    _log(
        f"checking installed version: {installed_version or 'not installed'}"
    )

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    if (
        not force
        and installed_version == _normalized_version(tag)
        and enabled
        and active
    ):
        _log("target state already reached")
        result = TaskResult(success=True, changed=False, message="already configured")
    else:
        _log(f"downloading installer {cfg.install_script_url}")
        try:
            script_path = _download_installer(cfg, timeout)
        except RuntimeError as exc:
            return TaskResult(success=False, error=str(exc))
        _log("installer downloaded")

        _log("running official 3x-ui installer")
        try:
            _run_installer(script_path, timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(success=False, error=f"installer failed: {exc}")
        _log("installer finished")

        _log(
            f"waiting for service to become active (up to "
            f"{cfg.start_check_attempts} checks)"
        )
        if not _wait_active(
            cfg.service_unit_name,
            cfg.start_check_attempts,
            cfg.start_check_retry_delay_seconds,
            timeout,
        ):
            return TaskResult(
                success=False,
                changed=True,
                error=(
                    f"service {cfg.service_unit_name} did not become active "
                    f"after the installer"
                ),
            )
        _log(f"checking installed version: {_installed_version(cfg, timeout)}")
        result = TaskResult(
            success=True, changed=True, message=f"installed 3x-ui {tag}"
        )

    # Stage 2: read credentials, verify session, store in vault.
    stage2_result = _stage2(cfg, ctx.config, timeout)
    if stage2_result is not None:
        # Merge warnings from stage 2 into the main result.
        return TaskResult(
            success=stage2_result.success,
            changed=result.changed,
            message=result.message,
            warnings=stage2_result.warnings,
        )
    return result
