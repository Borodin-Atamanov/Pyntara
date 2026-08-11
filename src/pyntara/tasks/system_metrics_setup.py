"""Task system_metrics_setup: deploy the long-running System Metrics service.

The task makes the pyntara package and its dependencies available to the
services on the target machine: a dedicated virtual environment is created
at the configured venv_dir with uv and the package is installed into it
from the repository clone (REPO_ROOT), so deployed services import the
same code base the installer uses and never need the clone afterwards.
The single system config is copied to the configured system_config_path,
so the deployed service reads its parameters with the same loader as the
installer (architecture contract section 3). The systemd unit
system_metrics.service starts the service at boot; the task enables it and
starts it immediately, so a broken deployment fails the task and shows in
the install log instead of surfacing at the first reboot. The task also
links the commit_system_metrics command script from the venv into the
configured command_path, so the queue commit utility is on PATH after the
install. The task is idempotent: it skips when the venv imports pyntara,
the config and the unit match their sources, the service is enabled and
the command symlink points at the venv script; force mode reinstalls the
package and restarts the service.
"""

from __future__ import annotations

import os
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
# Repository layout and the unit directory are fixed machine contracts
# (architecture contract section 3); the deployment paths of the venv and
# the system config live in config.toml through Context.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "system_metrics.service"
)
SERVICE_NAME = "system_metrics.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
# Name of the command script the package installer creates inside the venv
# from [project.scripts] in pyproject.toml. The task links command_path to
# this script. The name is a fixed package contract, not a deployment
# parameter, so it lives here as a documented exception (architecture
# contract section 3) instead of in config.toml.
COMMIT_COMMAND_NAME = "commit_system_metrics"


def _venv_import_ok(venv_python: Path, timeout: float) -> bool:
    """True when the venv python exists and imports the pyntara package.

    The import check is the proof that the package is installed in the
    venv; it runs with capture so a broken import stays quiet and only the
    return code is inspected.
    """

    if not venv_python.is_file():
        return False
    try:
        result = run_command(
            [str(venv_python), "-c", "import pyntara"],
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


def _ensure_venv(
    uv: str,
    force: bool,
    timeout: float,
    venv_dir: Path,
    python_version: str,
) -> tuple[bool, str | None]:
    """Ensure the venv exists with pyntara installed; (changed, error).

    Without force an existing working venv is left untouched. Otherwise the
    venv is created when missing with the configured python version and
    the package is installed from the repository clone; force adds
    --reinstall so the running code is refreshed even when the installed
    version did not change.
    """

    venv_python = venv_dir / "bin" / "python"
    if _venv_import_ok(venv_python, timeout) and not force:
        return False, None
    if not venv_dir.is_dir():
        _log(f"creating venv: uv venv {venv_dir}")
        try:
            run_command(
                [uv, "venv", str(venv_dir), "--python", python_version],
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot create venv: {exc}"
        _log("venv created")
    install = [uv, "pip", "install"]
    if force:
        install.append("--reinstall")
    install += ["--python", str(venv_python), str(REPO_ROOT)]
    _log(f"installing pyntara into the venv from {REPO_ROOT}")
    try:
        run_command(install, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot install pyntara into the venv: {exc}"
    _log("pyntara installed into the venv")
    return True, None


def _system_config_matches(system_config_path: Path) -> bool:
    """True when the system config copy equals the repository config."""

    source = REPO_ROOT / "config.toml"
    try:
        if not system_config_path.is_file():
            return False
        return (
            system_config_path.read_text(encoding="utf-8")
            == source.read_text(encoding="utf-8")
        )
    except OSError:
        return False


def _write_system_config(system_config_path: Path) -> None:
    """Copy the repository config to the configured system path.

    The copy is the single config of the target system: deployed services
    read it through load_config, so they never need the repository.
    """

    source = REPO_ROOT / "config.toml"
    system_config_path.parent.mkdir(parents=True, exist_ok=True)
    system_config_path.write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _render_unit(venv_python: Path, system_config_path: Path) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the metrics module and the
    configured system config path as its only argument; the line is fully
    expanded here, so the template carries no shell variables of its own.
    """

    command = " ".join(
        [str(venv_python), "-m", "pyntara.metrics", str(system_config_path)]
    )
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={command}")


def _unit_matches(venv_python: Path, system_config_path: Path) -> bool:
    """True when the deployed unit file equals the rendered one."""

    unit_path = SYSTEMD_UNIT_DIR / SERVICE_NAME
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == _render_unit(
            venv_python, system_config_path
        )
    except OSError:
        return False


def _write_unit(venv_python: Path, system_config_path: Path) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_UNIT_DIR / SERVICE_NAME).write_text(
        _render_unit(venv_python, system_config_path), encoding="utf-8"
    )


def _command_symlink_ok(venv_script: Path, command_path: Path) -> bool:
    """True when the command path is a symlink to the existing venv script.

    The target is compared as a literal string, so a relative or
    differently spelled link is wrong even when it resolves to the same
    file; our own links always carry the absolute path. A missing venv
    script would leave any link dangling, so the goal is not reached
    then. Any OSError (missing path, unreadable link) is not ok.
    """

    if not venv_script.is_file():
        return False
    try:
        return os.readlink(command_path) == str(venv_script)
    except OSError:
        return False


def _link_command(
    venv_script: Path, command_path: Path, *, recreate: bool = False
) -> str | None:
    """Ensure the command symlink points at the venv script.

    The parent directory is created when missing. A symlink that already
    points at the venv script is left untouched unless recreate is set
    (force mode): then it is recreated so the link is refreshed. Any
    other owner of the path (a wrong symlink or a regular file) is
    replaced, because command_path is explicitly configured; the returned
    string describes the replacement for the final task message. A
    directory on command_path is never removed recursively and raises
    OSError with a clear message.
    """

    replaced = None
    if command_path.is_symlink():
        if os.readlink(command_path) == str(venv_script) and not recreate:
            return None
        command_path.unlink()
        replaced = f"replaced command symlink {command_path}"
    elif command_path.is_dir():
        raise OSError(
            f"command path {command_path} is a directory; refusing to remove it"
        )
    elif command_path.exists():
        command_path.unlink()
        replaced = f"replaced command file {command_path}"
    command_path.parent.mkdir(parents=True, exist_ok=True)
    command_path.symlink_to(venv_script)
    return replaced


def task(ctx: Context) -> TaskResult:
    """Deploy the System Metrics service; skip when the goal is reached.

    The goal is reached when the venv imports pyntara, the system config
    and the unit file match their sources, the service is enabled and the
    command symlink points at the venv script; the task then returns
    changed=False. Otherwise it creates the venv, installs the package,
    copies the config, writes the unit, reloads systemd, enables the
    service and starts it (restarts it when something changed while the
    service was running, and always in force mode), so a broken deployment
    fails the task and shows in the install log. The systemd work runs
    only when the service itself needs it: a task that only has to add
    the missing command symlink leaves a running service untouched. A
    missing uv executable and a failed command are errors: the task
    returns success=False and the runner continues.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = "system_metrics_setup" in ctx.force_tasks
    venv_dir = ctx.config.system_metrics_setup.venv_dir
    venv_python = venv_dir / "bin" / "python"
    venv_script = venv_dir / "bin" / COMMIT_COMMAND_NAME
    system_config_path = ctx.config.system_metrics_setup.system_config_path
    command_path = ctx.config.system_metrics_setup.command_path

    venv_ok = _venv_import_ok(venv_python, timeout)
    _log(f"checking venv {venv_python}: {'ok' if venv_ok else 'missing or broken'}")
    config_ok = _system_config_matches(system_config_path)
    unit_ok = _unit_matches(venv_python, system_config_path)
    enabled = service_is_enabled(SERVICE_NAME, timeout)
    _log(
        f"checking autorun service {SERVICE_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    command_ok = _command_symlink_ok(venv_script, command_path)
    _log(
        f"checking command {command_path}: "
        f"{'ok' if command_ok else 'missing or wrong'}"
    )

    if not force and venv_ok and config_ok and unit_ok and enabled and command_ok:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    uv = _uv_path()
    if uv is None:
        return TaskResult(success=False, error="uv executable not found on PATH")
    venv_changed, error = _ensure_venv(
        uv, force, timeout, venv_dir, ctx.config.system_metrics_setup.python_version
    )
    if error is not None:
        return TaskResult(success=False, error=error)
    changed = changed or venv_changed

    if not config_ok or force:
        _log(f"writing system config {system_config_path}")
        try:
            _write_system_config(system_config_path)
        except OSError as exc:
            return TaskResult(
                success=False, changed=changed, error=f"cannot write system config: {exc}"
            )
        _log("system config written")
        changed = True

    if not unit_ok or force:
        _log(f"rendering unit template from {TEMPLATE_PATH}")
        try:
            _write_unit(venv_python, system_config_path)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write unit file: {exc}",
            )
        _log("unit file written")
        changed = True

    if force or not (config_ok and unit_ok and enabled):
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

    replacement = None
    if not command_ok or force:
        _log(f"linking command {command_path} to {venv_script}")
        try:
            replacement = _link_command(venv_script, command_path, recreate=force)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot link command {command_path}: {exc}",
            )
        _log("command linked")
        changed = True

    message = f"System Metrics service deployed, venv {venv_dir}"
    if replacement is not None:
        message += f"; {replacement}"
    return TaskResult(success=True, changed=True, message=message)
