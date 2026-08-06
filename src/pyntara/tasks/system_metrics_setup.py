"""Task system_metrics_setup: deploy the long-running System Metrics service.

The task makes the pyntara package and its dependencies available to the
services on the target machine: a dedicated virtual environment is created
at /usr/local/lib/pyntara/venv with uv and the package is installed into
it from the repository clone (REPO_ROOT), so deployed services import the
same code base the installer uses and never need the clone afterwards.
The single system config is copied to /etc/pyntara/config.toml, so the
deployed service reads its parameters with the same loader as the
installer (architecture contract section 3). The systemd unit
system_metrics.service starts the service at boot; the task enables it and
starts it immediately, so a broken deployment fails the task and shows in
the install log instead of surfacing at the first reboot. The task is
idempotent: it skips when the venv imports pyntara, the config and the
unit match their sources and the service is enabled; force mode
reinstalls the package and restarts the service.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command, service_is_active, service_is_enabled

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# The paths below are fixed machine contracts (architecture contract
# section 3): the venv, the system config and the unit directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "system_metrics.service"
)
SERVICE_NAME = "system_metrics.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
PYNTARA_LIB_DIR = Path("/usr/local/lib/pyntara")
VENV_DIR = PYNTARA_LIB_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
SYSTEM_CONFIG_DIR = Path("/etc/pyntara")
SYSTEM_CONFIG_PATH = SYSTEM_CONFIG_DIR / "config.toml"


def _venv_import_ok(timeout: float) -> bool:
    """True when the venv python exists and imports the pyntara package.

    The import check is the proof that the package is installed in the
    venv; it runs with capture so a broken import stays quiet and only the
    return code is inspected.
    """

    if not VENV_PYTHON.is_file():
        return False
    try:
        result = run_command(
            [str(VENV_PYTHON), "-c", "import pyntara"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _uv_path() -> str | None:
    """Path of the uv executable, or None when it is not on PATH."""

    return shutil.which("uv")


def _ensure_venv(uv: str, force: bool, timeout: float) -> tuple[bool, str | None]:
    """Ensure the venv exists with pyntara installed; (changed, error).

    Without force an existing working venv is left untouched. Otherwise the
    venv is created when missing and the package is installed from the
    repository clone; force adds --reinstall so the running code is
    refreshed even when the installed version did not change.
    """

    if _venv_import_ok(timeout) and not force:
        return False, None
    if not VENV_DIR.is_dir():
        _log(f"creating venv: uv venv {VENV_DIR}")
        try:
            run_command(
                [uv, "venv", str(VENV_DIR), "--python", "3"], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot create venv: {exc}"
        _log("venv created")
    install = [uv, "pip", "install"]
    if force:
        install.append("--reinstall")
    install += ["--python", str(VENV_PYTHON), str(REPO_ROOT)]
    _log(f"installing pyntara into the venv from {REPO_ROOT}")
    try:
        run_command(install, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot install pyntara into the venv: {exc}"
    _log("pyntara installed into the venv")
    return True, None


def _system_config_matches() -> bool:
    """True when the system config copy equals the repository config."""

    source = REPO_ROOT / "config.toml"
    try:
        if not SYSTEM_CONFIG_PATH.is_file():
            return False
        return (
            SYSTEM_CONFIG_PATH.read_text(encoding="utf-8")
            == source.read_text(encoding="utf-8")
        )
    except OSError:
        return False


def _write_system_config() -> None:
    """Copy the repository config to the fixed system path.

    The copy is the single config of the target system: deployed services
    read it through load_config, so they never need the repository.
    """

    source = REPO_ROOT / "config.toml"
    SYSTEM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEM_CONFIG_PATH.write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _render_unit() -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the metrics module and the fixed
    system config path as its only argument; the line is fully expanded
    here, so the template carries no shell variables of its own.
    """

    command = " ".join(
        [str(VENV_PYTHON), "-m", "pyntara.metrics", str(SYSTEM_CONFIG_PATH)]
    )
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={command}")


def _unit_matches() -> bool:
    """True when the deployed unit file equals the rendered one."""

    unit_path = SYSTEMD_UNIT_DIR / SERVICE_NAME
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == _render_unit()
    except OSError:
        return False


def _write_unit() -> None:
    """Write the rendered unit file into the systemd unit directory."""

    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_UNIT_DIR / SERVICE_NAME).write_text(
        _render_unit(), encoding="utf-8"
    )


def task(ctx: Context) -> TaskResult:
    """Deploy the System Metrics service; skip when the goal is reached.

    The goal is reached when the venv imports pyntara, the system config
    and the unit file match their sources and the service is enabled; the
    task then returns changed=False. Otherwise it creates the venv,
    installs the package, copies the config, writes the unit, reloads
    systemd, enables the service and starts it (restarts it when something
    changed while the service was running, and always in force mode), so a
    broken deployment fails the task and shows in the install log. A
    missing uv executable and a failed command are errors: the task
    returns success=False and the runner continues.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = "system_metrics_setup" in ctx.force_tasks

    venv_ok = _venv_import_ok(timeout)
    _log(f"checking venv {VENV_PYTHON}: {'ok' if venv_ok else 'missing or broken'}")
    config_ok = _system_config_matches()
    unit_ok = _unit_matches()
    enabled = service_is_enabled(SERVICE_NAME, timeout)
    _log(
        f"checking autorun service {SERVICE_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if not force and venv_ok and config_ok and unit_ok and enabled:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    uv = _uv_path()
    if uv is None:
        return TaskResult(success=False, error="uv executable not found on PATH")
    venv_changed, error = _ensure_venv(uv, force, timeout)
    if error is not None:
        return TaskResult(success=False, error=error)
    changed = changed or venv_changed

    if not config_ok or force:
        _log(f"writing system config {SYSTEM_CONFIG_PATH}")
        try:
            _write_system_config()
        except OSError as exc:
            return TaskResult(
                success=False, changed=changed, error=f"cannot write system config: {exc}"
            )
        _log("system config written")
        changed = True

    if not unit_ok or force:
        _log(f"rendering unit template from {TEMPLATE_PATH}")
        try:
            _write_unit()
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write unit file: {exc}",
            )
        _log("unit file written")
        changed = True

    try:
        _log("reloading systemd: systemctl daemon-reload")
        run_command(["systemctl", "daemon-reload"], timeout=timeout)
        _log("systemd reloaded")
        if not enabled or force:
            _log(f"enabling service: systemctl enable {SERVICE_NAME}")
            run_command(["systemctl", "enable", SERVICE_NAME], timeout=timeout)
            _log("service enabled")
        active = service_is_active(SERVICE_NAME, timeout)
        if force or (changed and active):
            _log(f"restarting service: systemctl restart {SERVICE_NAME}")
            run_command(["systemctl", "restart", SERVICE_NAME], timeout=timeout)
            _log("service restarted")
        else:
            _log(f"starting service: systemctl start {SERVICE_NAME}")
            run_command(["systemctl", "start", SERVICE_NAME], timeout=timeout)
            _log("service started")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False, changed=True, error=f"systemd setup failed: {exc}"
        )
    return TaskResult(
        success=True,
        changed=True,
        message=f"System Metrics service deployed, venv {VENV_DIR}",
    )
