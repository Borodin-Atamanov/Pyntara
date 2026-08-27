"""Task system_metrics_setup: deploy the System Metrics service and spool ingest.

The task makes the pyntara package and its dependencies available to the
services on the target machine: a dedicated virtual environment is created
at the configured venv_dir with uv and the package is installed into it
from the repository lockfile of the clone (REPO_ROOT), so deployed
services import the same code base the installer uses, run the same
dependency versions as the repository and never need the clone
afterwards.
The venv is refreshed whenever its installed pyntara version differs
from the repository version, so deployed services run the current code
after every installer run.
The single system config is copied to the configured system_config_path,
so the deployed service reads its parameters with the same loader as the
installer (architecture contract, Configuration). The long-running service
system_metrics.service drains the Google Drive channel queue; the ingest
service system_metrics-ingest.service
moves committed files from the spool into the queue and is started by the
path unit system_metrics-ingest.path whenever a file appears in the spool.
The report collector service system_metrics_collector.service gathers the
network and system report and is started by the timer
system_metrics_collector.timer after boot and at the configured daily
time; all waiting happens inside the collector (docs/spec/system-metrics.md,
section Report collector).
All unit names, journal identifiers and the spool path come from config
(architecture contract, Configuration). The task generates the thin
commit_system_metrics command file from the command template with the
configured spool path and journal identifier embedded, so the command
needs no config access and no root privileges; it also creates the spool
directory with the configured mode. The task enables and starts the
service and the path unit immediately, so a broken deployment fails the
task and shows in the install log instead of surfacing at the first
reboot. The task is idempotent: it skips when the venv imports pyntara,
the config, the unit files and the command file match their sources, the
service and the path unit are enabled and the spool directory is in
place; force mode reinstalls the package and restarts the units.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara import __version__
from pyntara.config.loader import render_config_source
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    ensure_root_owner,
    run_command,
    service_is_active,
    service_is_enabled,
    trim_whitespace,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# Repository layout and the systemd unit directory are fixed machine
# contracts (architecture contract, Configuration); the unit file names, the
# deployment paths of the venv and the system config live in config.toml
# through Context.
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "system_metrics.service"
)
INGEST_SERVICE_TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "system_metrics-ingest.service"
)
INGEST_PATH_TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "system_metrics-ingest.path"
)
COLLECTOR_SERVICE_TEMPLATE_PATH = (
    REPO_ROOT
    / "task_data"
    / "system_metrics_setup"
    / "system_metrics_collector.service"
)
COLLECTOR_TIMER_TEMPLATE_PATH = (
    REPO_ROOT
    / "task_data"
    / "system_metrics_setup"
    / "system_metrics_collector.timer"
)
COMMAND_TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "system_metrics_setup" / "commit_system_metrics.sh"
)
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")


def _venv_package_version(venv_python: Path, timeout: float) -> str | None:
    """The pyntara version installed in the venv, or None.

    The import is the proof that the package is installed in the venv;
    the version proves that the installed code matches the repository
    clone. The check runs with capture so a broken import stays quiet;
    the printed version ends with a newline, so the output is trimmed
    through the shared helper.
    """

    if not venv_python.is_file():
        return None
    try:
        result = run_command(
            [
                str(venv_python),
                "-c",
                "import pyntara; print(pyntara.__version__)",
            ],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return trim_whitespace(result.stdout) or None


def _uv_path() -> str | None:
    """Path of the uv executable, or None when it is not on PATH."""

    return shutil.which("uv")


def _ensure_venv(
    uv: str,
    force: bool,
    timeout: float,
    venv_dir: Path,
    python_version: str,
    venv_up_to_date: bool,
) -> tuple[bool, str | None]:
    """Ensure the venv runs the repository pyntara version; (changed, error).

    A venv is up to date when its python imports pyntara and reports the
    repository version. Without force an up-to-date venv is left
    untouched; a stale or broken venv is updated even without force,
    because the deployed services must run the current code. The venv is
    created when missing with the configured python version; the package
    and its dependencies are installed from the repository lockfile with
    uv sync, so the deployed venv runs the same versions as the
    repository. The pyntara package itself is reinstalled from the clone
    when the update refreshes an existing venv or force asks for it,
    because uv sync does not rebuild a local project whose lockfile entry
    carries no version.
    """

    if venv_up_to_date and not force:
        return False, None
    created = False
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
        created = True
    sync = [
        uv,
        "sync",
        "--project",
        str(REPO_ROOT),
        "--active",
        "--locked",
        "--no-dev",
        "--no-editable",
    ]
    if force or (not venv_up_to_date and not created):
        sync += ["--reinstall-package", "pyntara"]
    _log(f"installing pyntara into the venv from the lockfile of {REPO_ROOT}")
    try:
        run_command(
            sync,
            timeout=timeout,
            extra_env={"VIRTUAL_ENV": str(venv_dir)},
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot install pyntara into the venv: {exc}"
    _log("pyntara installed into the venv")
    return True, None


def _system_config_matches(system_config_path: Path) -> bool:
    """True when the system config copy equals the repository config."""

    source = REPO_ROOT / "config"
    try:
        if not system_config_path.is_file():
            return False
        return (
            system_config_path.read_text(encoding="utf-8")
            == render_config_source(source)
        )
    except OSError:
        return False


def _write_system_config(system_config_path: Path) -> None:
    """Render the repository config to the configured system path.

    The copy is the single config of the target system: deployed services
    read it through load_config, so they never need the repository. The
    repository config/ directory is joined into one document, the same
    joined text load_config parses.
    """

    source = REPO_ROOT / "config"
    system_config_path.parent.mkdir(parents=True, exist_ok=True)
    system_config_path.write_text(
        render_config_source(source), encoding="utf-8"
    )


def _render_service_unit(
    venv_python: Path, system_config_path: Path, journal_identifier: str
) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the metrics module and the
    configured system config path as its only argument; the line is fully
    expanded here, so the template carries no shell variables of its own.
    The journal identifier comes from the config.
    """

    command = " ".join(
        [str(venv_python), "-m", "pyntara.metrics", str(system_config_path)]
    )
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={command}", journal_identifier=journal_identifier
    )


def _render_ingest_service_unit(
    venv_python: Path, system_config_path: Path, journal_identifier: str
) -> str:
    """Render the ingest service unit with the ExecStart line substituted.

    The oneshot service runs the venv python with the metrics_ingest
    module and the configured system config path as its only argument.
    """

    command = " ".join(
        [str(venv_python), "-m", "pyntara.metrics_ingest", str(system_config_path)]
    )
    template = Template(INGEST_SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={command}", journal_identifier=journal_identifier
    )


def _render_ingest_path_unit(spool_dir: Path) -> str:
    """Render the path unit that watches the spool directory."""

    template = Template(INGEST_PATH_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(spool_dir=spool_dir)


def _render_collector_service_unit(
    venv_python: Path, system_config_path: Path, journal_identifier: str
) -> str:
    """Render the collector oneshot unit with the ExecStart line substituted.

    The service runs the venv python with the metrics_collect module and
    the configured system config path as its only argument; the line is
    fully expanded here, so the template carries no shell variables of
    its own. The journal identifier comes from the config.
    """

    command = " ".join(
        [str(venv_python), "-m", "pyntara.metrics_collect", str(system_config_path)]
    )
    template = Template(
        COLLECTOR_SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    )
    return template.substitute(
        exec_lines=f"ExecStart={command}", journal_identifier=journal_identifier
    )


def _render_collector_timer_unit(
    boot_delay_seconds: int, daily_send_time: str, service_unit_name: str
) -> str:
    """Render the timer unit that starts the collector after boot and daily.

    The collector does all waiting itself, so the timer only schedules
    the start: OnBootSec comes from the config and OnCalendar from the
    normalized daily time of the config (docs/spec/system-metrics.md,
    section Report collector).
    """

    template = Template(COLLECTOR_TIMER_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        boot_delay_seconds=boot_delay_seconds,
        daily_send_time=daily_send_time,
        service_unit_name=service_unit_name,
    )


def _render_commit_command(
    spool_dir: Path, journal_identifier: str, temp_prefix: str
) -> str:
    """Render the thin commit command with the configured values embedded.

    The command needs no config access at runtime: the spool path, the
    journal identifier and the temporary file prefix are substituted at
    generation time, so any user can run the command (architecture
    contract, Configuration).
    """

    template = COMMAND_TEMPLATE_PATH.read_text(encoding="utf-8")
    # The command template is a bash script with @PLACEHOLDER@ markers:
    # string.Template would clash with bash variables, so plain text
    # replacement is used instead.
    return (
        template.replace("@SPOOL_DIR@", str(spool_dir))
        .replace("@JOURNAL_IDENTIFIER@", journal_identifier)
        .replace("@TEMP_PREFIX@", temp_prefix)
    )


def _unit_matches(name: str, expected: str) -> bool:
    """True when the deployed unit file equals the expected content."""

    unit_path = SYSTEMD_UNIT_DIR / name
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def _write_unit(name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_UNIT_DIR / name).write_text(content, encoding="utf-8")


def _command_file_matches(command_path: Path, expected: str, mode: int) -> bool:
    """True when the command file content and mode equal the expected.

    The command is a generated regular file: the mode matters as much as
    the content, because the file must stay executable for every user.
    Any OSError (missing path, unreadable file) is not ok.
    """

    try:
        if not command_path.is_file():
            return False
        return command_path.read_text(encoding="utf-8") == expected and (
            os.stat(command_path).st_mode & 0o777 == mode
        )
    except OSError:
        return False


def _write_command_file(command_path: Path, content: str, mode: int) -> None:
    """Write the generated commit command with the configured mode.

    The parent directory is created when missing. Any other owner of the
    path (a stale generated file or a foreign file) is replaced, because
    command_path is explicitly configured. A directory on command_path is
    never removed recursively and raises OSError with a clear message.
    """

    if command_path.is_dir():
        raise OSError(
            f"command path {command_path} is a directory; refusing to remove it"
        )
    command_path.parent.mkdir(parents=True, exist_ok=True)
    if command_path.is_symlink() or command_path.exists():
        command_path.unlink()
    command_path.write_text(content, encoding="utf-8")
    os.chmod(command_path, mode)


def _spool_dir_ok(spool_dir: Path, mode: int) -> bool:
    """True when the spool directory exists with the configured mode.

    The mask covers the sticky bit (0o1000) of the 1733 mode, so the
    check does not silently drop it.
    """

    try:
        return spool_dir.is_dir() and (os.stat(spool_dir).st_mode & 0o7777 == mode)
    except OSError:
        return False


def _ensure_spool_dir(spool_dir: Path, mode: int) -> None:
    """Create the spool directory with the configured mode and root owner."""

    spool_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(spool_dir, mode)
    ensure_root_owner(spool_dir)


def task(ctx: Context) -> TaskResult:
    """Deploy the System Metrics service; skip when the goal is reached.

    The goal is reached when the venv runs the repository pyntara version,
    the system config,
    the three unit files and the generated command file match their
    sources, the service and the path unit are enabled and the spool
    directory is in place; the task then returns changed=False. Otherwise
    it creates the venv, installs the package, copies the config, writes
    the units, reloads systemd, enables and starts the service and the
    path unit, generates the commit command and creates the spool
    directory, so a broken deployment fails the task and shows in the
    install log. The systemd work runs only when the units themselves
    need it: a task that only has to write the missing command file
    leaves the running units untouched. A missing uv executable and a
    failed command are errors: the task returns success=False and the
    runner continues.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = "system_metrics_setup" in ctx.force_tasks
    metrics = ctx.config.system_metrics_setup
    venv_dir = metrics.venv_dir
    venv_python = venv_dir / "bin" / "python"
    system_config_path = metrics.system_config_path
    command_path = metrics.command_path
    service_name = metrics.service_unit_name
    ingest_service_name = metrics.ingest_service_unit_name
    ingest_path_name = metrics.ingest_path_unit_name
    collector_service_name = metrics.collector.service_unit_name
    collector_timer_name = metrics.collector.timer_unit_name
    spool_dir = metrics.spool_dir
    journal_identifier = metrics.service_journal_identifier

    service_unit = _render_service_unit(
        venv_python, system_config_path, journal_identifier
    )
    ingest_service_unit = _render_ingest_service_unit(
        venv_python, system_config_path, journal_identifier
    )
    ingest_path_unit = _render_ingest_path_unit(spool_dir)
    collector_service_unit = _render_collector_service_unit(
        venv_python, system_config_path, metrics.collector.journal_identifier
    )
    collector_timer_unit = _render_collector_timer_unit(
        metrics.collector.boot_delay_seconds,
        metrics.collector.daily_send_time,
        collector_service_name,
    )
    command_content = _render_commit_command(
        spool_dir, metrics.commit_journal_identifier, metrics.spool_temp_prefix
    )

    venv_python = venv_dir / "bin" / "python"
    venv_version = _venv_package_version(venv_python, timeout)
    venv_ok = venv_version == __version__
    _log(
        f"checking venv {venv_python}: "
        f"{'ok' if venv_ok else 'missing or stale'} "
        f"(venv {venv_version or 'none'}, repository {__version__})"
    )
    config_ok = _system_config_matches(system_config_path)
    service_unit_ok = _unit_matches(service_name, service_unit)
    ingest_service_unit_ok = _unit_matches(ingest_service_name, ingest_service_unit)
    ingest_path_unit_ok = _unit_matches(ingest_path_name, ingest_path_unit)
    collector_service_unit_ok = _unit_matches(
        collector_service_name, collector_service_unit
    )
    collector_timer_unit_ok = _unit_matches(collector_timer_name, collector_timer_unit)
    service_enabled = service_is_enabled(service_name, timeout)
    _log(
        f"checking autorun service {service_name}: "
        f"{'enabled' if service_enabled else 'disabled'}"
    )
    path_enabled = service_is_enabled(ingest_path_name, timeout)
    _log(
        f"checking spool watcher {ingest_path_name}: "
        f"{'enabled' if path_enabled else 'disabled'}"
    )
    timer_enabled = service_is_enabled(collector_timer_name, timeout)
    _log(
        f"checking collector timer {collector_timer_name}: "
        f"{'enabled' if timer_enabled else 'disabled'}"
    )
    command_ok = _command_file_matches(
        command_path, command_content, metrics.command_file_mode
    )
    _log(
        f"checking command {command_path}: "
        f"{'ok' if command_ok else 'missing or stale'}"
    )
    spool_ok = _spool_dir_ok(spool_dir, metrics.spool_dir_mode)
    _log(
        f"checking spool {spool_dir}: "
        f"{'ok' if spool_ok else 'missing or wrong mode'}"
    )

    if (
        not force
        and venv_ok
        and config_ok
        and service_unit_ok
        and ingest_service_unit_ok
        and ingest_path_unit_ok
        and collector_service_unit_ok
        and collector_timer_unit_ok
        and service_enabled
        and path_enabled
        and timer_enabled
        and command_ok
        and spool_ok
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    uv = _uv_path()
    if uv is None:
        return TaskResult(success=False, error="uv executable not found on PATH")
    venv_changed, error = _ensure_venv(
        uv, force, timeout, venv_dir, metrics.python_version, venv_ok
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

    units = (
        (service_name, service_unit),
        (ingest_service_name, ingest_service_unit),
        (ingest_path_name, ingest_path_unit),
        (collector_service_name, collector_service_unit),
        (collector_timer_name, collector_timer_unit),
    )
    unit_states = (
        service_unit_ok,
        ingest_service_unit_ok,
        ingest_path_unit_ok,
        collector_service_unit_ok,
        collector_timer_unit_ok,
    )
    if not all(unit_states) or force:
        for name, content in units:
            if not force and _unit_matches(name, content):
                continue
            _log(f"writing unit {name}")
            try:
                _write_unit(name, content)
            except OSError as exc:
                return TaskResult(
                    success=False,
                    changed=changed,
                    error=f"cannot write unit file {name}: {exc}",
                )
            _log(f"unit {name} written")
            changed = True

    if force or venv_changed or not (
        config_ok
        and all(unit_states)
        and service_enabled
        and path_enabled
        and timer_enabled
    ):
        try:
            _log("reloading systemd: systemctl daemon-reload")
            run_command(["systemctl", "daemon-reload"], timeout=timeout)
            _log("systemd reloaded")
            for name in (service_name, ingest_path_name, collector_timer_name):
                if force or not service_is_enabled(name, timeout):
                    _log(f"enabling unit: systemctl enable {name}")
                    run_command(["systemctl", "enable", name], timeout=timeout)
                    _log(f"unit {name} enabled")
            active = service_is_active(service_name, timeout)
            if force or (changed and active):
                _log(f"restarting service: systemctl restart {service_name}")
                run_command(["systemctl", "restart", service_name], timeout=timeout)
                _log("service restarted")
            else:
                _log(f"starting service: systemctl start {service_name}")
                run_command(["systemctl", "start", service_name], timeout=timeout)
                _log("service started")
            path_active = service_is_active(ingest_path_name, timeout)
            if force or not ingest_path_unit_ok or not path_active:
                if path_active:
                    _log(f"restarting path unit: systemctl restart {ingest_path_name}")
                    run_command(["systemctl", "restart", ingest_path_name], timeout=timeout)
                    _log("path unit restarted")
                else:
                    _log(f"starting path unit: systemctl start {ingest_path_name}")
                    run_command(["systemctl", "start", ingest_path_name], timeout=timeout)
                    _log("path unit started")
            timer_active = service_is_active(collector_timer_name, timeout)
            if force or not collector_timer_unit_ok or not timer_active:
                if timer_active:
                    _log(
                        f"restarting collector timer: systemctl restart "
                        f"{collector_timer_name}"
                    )
                    run_command(
                        ["systemctl", "restart", collector_timer_name], timeout=timeout
                    )
                    _log("collector timer restarted")
                else:
                    _log(
                        f"starting collector timer: systemctl start "
                        f"{collector_timer_name}"
                    )
                    run_command(
                        ["systemctl", "start", collector_timer_name], timeout=timeout
                    )
                    _log("collector timer started")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False, changed=True, error=f"systemd setup failed: {exc}"
            )

    if not command_ok or force:
        _log(f"writing command {command_path}")
        try:
            _write_command_file(command_path, command_content, metrics.command_file_mode)
            ensure_root_owner(command_path)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot write command {command_path}: {exc}",
            )
        _log("command written")
        changed = True

    if not spool_ok or force:
        _log(f"creating spool {spool_dir} with mode {metrics.spool_dir_mode:04o}")
        try:
            _ensure_spool_dir(spool_dir, metrics.spool_dir_mode)
        except OSError as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot create spool directory {spool_dir}: {exc}",
            )
        _log("spool ready")
        changed = True

    message = (
        f"System Metrics service deployed, venv {venv_dir}, spool {spool_dir}"
    )
    return TaskResult(success=True, changed=True, message=message)