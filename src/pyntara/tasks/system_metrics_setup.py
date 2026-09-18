"""Task system_metrics_setup: deploy the System Metrics service and spool ingest.

The task makes the pyntara package and its dependencies available to the
services on the target machine: a dedicated virtual environment is created
at the configured venv_dir with uv and the package is installed into it
from the repository lockfile of the clone (the context carries the root), so
deployed
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
time; all waiting happens inside the collector (docs/spec/system-values.MD,
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

from pyntara import __version__, deployment
from pyntara.config.loader import render_config_source
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# The unit, path and command templates of this task live under
# task_data/system_metrics_setup in the clone and are read from the context;
# the unit file names, the deployment paths of the venv and the system config
# live in config.toml through Context.


def _uv_path() -> str | None:
    """Path of the uv executable, or None when it is not on PATH."""

    return shutil.which("uv")


def _ensure_venv(
    repo_root: Path,
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
                substituted_command(
                    values.VENV_CREATE_COMMAND,
                    {
                        "uv": uv,
                        "venv_dir": str(venv_dir),
                        "python_version": python_version,
                    },
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot create venv: {exc}"
        _log("venv created")
        created = True
    sync = substituted_command(
        values.VENV_SYNC_COMMAND, {"uv": uv, "repo_root": str(repo_root)}
    )
    if force or (not venv_up_to_date and not created):
        sync += list(values.VENV_REINSTALL_FLAGS)
    _log(f"installing pyntara into the venv from the lockfile of {repo_root}")
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


def _system_config_matches(system_config_path: Path, config_source_dir: Path) -> bool:
    """True when the system config copy equals the repository config."""

    source = config_source_dir
    try:
        if not system_config_path.is_file():
            return False
        return system_config_path.read_text(encoding="utf-8") == render_config_source(
            source
        )
    except OSError:
        return False


def _write_system_config(system_config_path: Path, config_source_dir: Path) -> None:
    """Render the repository config to the configured system path.

    The copy is the single config of the target system: deployed services
    read it through load_config, so they never need the repository. The
    repository config/ directory is joined into one document, the same
    joined text load_config parses.
    """

    source = config_source_dir
    system_config_path.parent.mkdir(parents=True, exist_ok=True)
    system_config_path.write_text(render_config_source(source), encoding="utf-8")


def _render_service_unit(
    template_path: Path,
    venv_python: Path,
    system_config_path: Path,
    version: str,
) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the metrics module and the
    configured system config path as its only argument; the line is fully
    expanded here, so the template carries no shell variables of its own.
    The version line names the deployed code the unit belongs to, so a
    unit on the machine that names another version is written again and
    the service restarted.
    """

    command = " ".join(
        substituted_command(
            values.SEND_SERVICE_COMMAND,
            {"python": str(venv_python), "config_path": str(system_config_path)},
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={command}", version=version)


def _render_ingest_service_unit(
    template_path: Path,
    venv_python: Path,
    system_config_path: Path,
    version: str,
) -> str:
    """Render the ingest service unit with the ExecStart line substituted.

    The oneshot service runs the venv python with the metrics_ingest
    module and the configured system config path as its only argument, and
    it carries the same version line as the service beside it.
    """

    command = " ".join(
        substituted_command(
            values.INGEST_SERVICE_COMMAND,
            {"python": str(venv_python)},
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={command}", version=version)


def _render_ingest_path_unit(template_path: Path, spool_dir: Path, version: str) -> str:
    """Render the path unit that watches the spool directory.

    The unit itself starts the ingest service rather than running code, so
    the version line is the mark of the deployment that wrote it.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(spool_dir=spool_dir, version=version)


def _render_collector_service_unit(
    template_path: Path,
    venv_python: Path,
    system_config_path: Path,
    version: str,
) -> str:
    """Render the collector oneshot unit with the ExecStart line substituted.

    The service runs the venv python with the metrics_collect module and
    the configured system config path as its only argument; the line is
    fully expanded here, so the template carries no shell variables of
    its own. The version line is the mark of the deployed code it belongs
    to, like the one of the service and the ingest units.
    """

    command = " ".join(
        substituted_command(
            values.COLLECTOR_SERVICE_COMMAND,
            {"python": str(venv_python), "config_path": str(system_config_path)},
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={command}", version=version)


def _render_collector_timer_unit(
    template_path: Path,
    boot_delay_seconds: int,
    daily_send_times: tuple[str, ...],
    service_unit_name: str,
    version: str,
) -> str:
    """Render the timer unit that starts the collector after boot and daily.

    The collector does all waiting itself, so the timer only schedules
    the start: OnBootSec comes from the config, and every configured time
    of day becomes one OnCalendar line, because systemd reads one line
    per calendar event (docs/spec/system-values.MD, section Report
    collector). The version line is the mark of the deployment that wrote
    the timer.
    """

    calendar = "\n".join(
        f"OnCalendar=*-*-* {time_of_day}" for time_of_day in daily_send_times
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        boot_delay_seconds=boot_delay_seconds,
        daily_send_calendar=calendar,
        service_unit_name=service_unit_name,
        version=version,
    )


def _render_commit_command(
    template_path: Path, spool_dir: Path, journal_identifier: str, temp_prefix: str
) -> str:
    """Render the thin commit command with the configured values embedded.

    The command needs no config access at runtime: the spool path, the
    journal identifier and the temporary file prefix are substituted at
    generation time, so any user can run the command (architecture
    contract, Configuration).
    """

    template = template_path.read_text(encoding="utf-8")
    # The command template is a bash script with @PLACEHOLDER@ markers:
    # string.Template would clash with bash variables, so plain text
    # replacement is used instead.
    return (
        template.replace("@SPOOL_DIR@", str(spool_dir))
        .replace("@JOURNAL_IDENTIFIER@", journal_identifier)
        .replace("@TEMP_PREFIX@", temp_prefix)
    )


def _unit_matches(unit_dir: Path, name: str, expected: str) -> bool:
    """True when the deployed unit file equals the expected content."""

    unit_path = unit_dir / name
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def _write_unit(unit_dir: Path, name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / name).write_text(content, encoding="utf-8")


def _command_file_matches(
    command_path: Path, expected: str, mode: int, permission_mask: int
) -> bool:
    """True when the command file content and mode equal the expected.

    The command is a generated regular file: the mode matters as much as
    the content, because the file must stay executable for every user.
    Any OSError (missing path, unreadable file) is not ok. The comparison
    keeps the permission bits only, because the configured mask says which
    bits are the mode of the file.
    """

    try:
        if not command_path.is_file():
            return False
        return command_path.read_text(encoding="utf-8") == expected and (
            os.stat(command_path).st_mode & permission_mask == mode
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


def _spool_dir_ok(spool_dir: Path, mode: int, permission_mask: int) -> bool:
    """True when the spool directory exists with the configured mode.

    The mask is the configured one and covers the special bits, including
    the sticky bit of the 1733 mode, so the check does not silently drop
    it.
    """

    try:
        return spool_dir.is_dir() and (
            os.stat(spool_dir).st_mode & permission_mask == mode
        )
    except OSError:
        return False


def _ensure_spool_dir(
    spool_dir: Path, mode: int, owner_uid: int, owner_gid: int
) -> None:
    """Create the spool directory with the configured mode and root owner."""

    spool_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(spool_dir, mode)
    apply_owner(spool_dir, owner_uid, owner_gid)


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


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
    directory, so a broken step is visible in the install log. The systemd
    work runs only when the units themselves need it: a task that only has
    to write the missing command file leaves the running units untouched.
    A step that cannot run is a warning of a completed task: a missing uv
    executable skips the venv alone, an unwritable config, unit or command
    file and a failed systemd setup keep every earlier step, and the run
    never stops here.
    """

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    warnings: list[str] = []
    venv_dir = values.VENV_DIR
    venv_python = venv_dir / values.VENV_PYTHON_RELATIVE_PATH
    system_config_path = values.SYSTEM_CONFIG_PATH
    command_path = values.COMMAND_PATH
    service_name = values.SERVICE_UNIT_NAME
    ingest_service_name = values.INGEST_SERVICE_UNIT_NAME
    ingest_path_name = values.INGEST_PATH_UNIT_NAME
    collector_service_name = values.COLLECTOR.service_unit_name
    collector_timer_name = values.COLLECTOR.timer_unit_name
    spool_dir = values.SPOOL_DIR
    template_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    unit_version, version_warning = deployment.deployed_version(
        values.VENV_VERSION_COMMAND, venv_python, timeout, __version__
    )
    venv_ok = version_warning is None and unit_version == __version__
    _log(
        f"checking venv {venv_python}: "
        f"{'ok' if venv_ok else 'missing or stale'} "
        f"(venv {'none' if version_warning else unit_version}, "
        f"repository {__version__})"
    )
    if version_warning is not None:
        warnings.append(version_warning)

    service_unit = _render_service_unit(
        template_dir / values.UNIT_TEMPLATE_FILE_NAME,
        venv_python,
        system_config_path,
        unit_version,
    )
    ingest_service_unit = _render_ingest_service_unit(
        template_dir / values.INGEST_UNIT_TEMPLATE_FILE_NAME,
        venv_python,
        system_config_path,
        unit_version,
    )
    ingest_path_unit = _render_ingest_path_unit(
        template_dir / values.INGEST_PATH_TEMPLATE_FILE_NAME,
        spool_dir,
        unit_version,
    )
    collector_service_unit = _render_collector_service_unit(
        template_dir / values.COLLECTOR_UNIT_TEMPLATE_FILE_NAME,
        venv_python,
        system_config_path,
        unit_version,
    )
    collector_timer_unit = _render_collector_timer_unit(
        template_dir / values.COLLECTOR_TIMER_TEMPLATE_FILE_NAME,
        values.COLLECTOR.boot_delay_seconds,
        values.COLLECTOR.daily_send_times,
        collector_service_name,
        unit_version,
    )
    command_content = _render_commit_command(
        template_dir / values.COMMIT_COMMAND_TEMPLATE_FILE_NAME,
        spool_dir,
        values.COMMIT_JOURNAL_IDENTIFIER,
        values.SPOOL_TEMP_PREFIX,
    )

    config_ok = _system_config_matches(system_config_path, ctx.repo_root / "config")
    unit_dir = engine_values.SYSTEMD_UNIT_DIR
    service_unit_ok = _unit_matches(unit_dir, service_name, service_unit)
    ingest_service_unit_ok = _unit_matches(
        unit_dir, ingest_service_name, ingest_service_unit
    )
    ingest_path_unit_ok = _unit_matches(unit_dir, ingest_path_name, ingest_path_unit)
    collector_service_unit_ok = _unit_matches(
        unit_dir, collector_service_name, collector_service_unit
    )
    collector_timer_unit_ok = _unit_matches(
        unit_dir, collector_timer_name, collector_timer_unit
    )
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
        command_path,
        command_content,
        values.COMMAND_FILE_MODE,
        values.COMMAND_PERMISSION_MASK,
    )
    _log(
        f"checking command {command_path}: {'ok' if command_ok else 'missing or stale'}"
    )
    spool_ok = _spool_dir_ok(
        spool_dir, values.SPOOL_DIR_MODE, values.SPOOL_DIR_PERMISSION_MASK
    )
    _log(f"checking spool {spool_dir}: {'ok' if spool_ok else 'missing or wrong mode'}")

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
        return _result(changed=False, message="already configured", warnings=warnings)

    changed = False
    uv = _uv_path()
    if uv is None:
        # Without uv no virtual environment can be built, so that step is
        # skipped alone while the configuration, the units, the command
        # and the spool directory are still deployed.
        warnings.append("uv executable not found on PATH")
        venv_changed = False
    else:
        venv_changed, error = _ensure_venv(
            ctx.repo_root,
            uv,
            force,
            timeout,
            venv_dir,
            values.PYTHON_VERSION,
            venv_ok,
        )
        if error is not None:
            warnings.append(error)
            venv_changed = False
    changed = changed or venv_changed

    if not config_ok or force:
        _log(f"writing system config {system_config_path}")
        try:
            _write_system_config(system_config_path, ctx.repo_root / "config")
        except OSError as exc:
            warnings.append(f"cannot write system config: {exc}")
        else:
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
            if not force and _unit_matches(unit_dir, name, content):
                continue
            _log(f"writing unit {name}")
            try:
                _write_unit(unit_dir, name, content)
            except OSError as exc:
                warnings.append(f"cannot write unit file {name}: {exc}")
                continue
            _log(f"unit {name} written")
            changed = True

    if (
        force
        or venv_changed
        or not (
            config_ok
            and all(unit_states)
            and service_enabled
            and path_enabled
            and timer_enabled
        )
    ):
        try:
            _log("reloading systemd: systemctl daemon-reload")
            run_command(
                list(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND),
                timeout=timeout,
            )
            _log("systemd reloaded")
            for name in (service_name, ingest_path_name, collector_timer_name):
                if force or not service_is_enabled(name, timeout):
                    _log(f"enabling unit: systemctl enable {name}")
                    run_command(
                        substituted_command(
                            values.SYSTEMCTL_ENABLE_COMMAND,
                            {"unit_name": name},
                        ),
                        timeout=timeout,
                    )
                    _log(f"unit {name} enabled")
            active = service_is_active(service_name, timeout)
            if force or (changed and active):
                _log(f"restarting service: systemctl restart {service_name}")
                run_command(
                    substituted_command(
                        values.SYSTEMCTL_RESTART_COMMAND,
                        {"unit_name": service_name},
                    ),
                    timeout=timeout,
                )
                _log("service restarted")
            else:
                _log(f"starting service: systemctl start {service_name}")
                run_command(
                    substituted_command(
                        values.SYSTEMCTL_START_COMMAND,
                        {"unit_name": service_name},
                    ),
                    timeout=timeout,
                )
                _log("service started")
            path_active = service_is_active(ingest_path_name, timeout)
            if force or not ingest_path_unit_ok or not path_active:
                if path_active:
                    _log(f"restarting path unit: systemctl restart {ingest_path_name}")
                    run_command(
                        substituted_command(
                            values.SYSTEMCTL_RESTART_COMMAND,
                            {"unit_name": ingest_path_name},
                        ),
                        timeout=timeout,
                    )
                    _log("path unit restarted")
                else:
                    _log(f"starting path unit: systemctl start {ingest_path_name}")
                    run_command(
                        substituted_command(
                            values.SYSTEMCTL_START_COMMAND,
                            {"unit_name": ingest_path_name},
                        ),
                        timeout=timeout,
                    )
                    _log("path unit started")
            timer_active = service_is_active(collector_timer_name, timeout)
            if force or not collector_timer_unit_ok or not timer_active:
                if timer_active:
                    _log(
                        f"restarting collector timer: systemctl restart "
                        f"{collector_timer_name}"
                    )
                    run_command(
                        substituted_command(
                            values.SYSTEMCTL_RESTART_COMMAND,
                            {"unit_name": collector_timer_name},
                        ),
                        timeout=timeout,
                    )
                    _log("collector timer restarted")
                else:
                    _log(
                        f"starting collector timer: systemctl start "
                        f"{collector_timer_name}"
                    )
                    run_command(
                        substituted_command(
                            values.SYSTEMCTL_START_COMMAND,
                            {"unit_name": collector_timer_name},
                        ),
                        timeout=timeout,
                    )
                    _log("collector timer started")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemd setup failed: {exc}")
        else:
            # The block converged the enablement or the running state of
            # the units, which is a change of the machine.
            changed = True

    if not command_ok or force:
        _log(f"writing command {command_path}")
        try:
            _write_command_file(
                command_path, command_content, values.COMMAND_FILE_MODE
            )
            apply_owner(command_path, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot write command {command_path}: {exc}")
        else:
            _log("command written")
            changed = True

    if not spool_ok or force:
        _log(f"creating spool {spool_dir} with mode {values.SPOOL_DIR_MODE:04o}")
        try:
            _ensure_spool_dir(spool_dir, values.SPOOL_DIR_MODE, owner_uid, owner_gid)
        except OSError as exc:
            warnings.append(f"cannot create spool directory {spool_dir}: {exc}")
        else:
            _log("spool ready")
            changed = True

    message = f"System Metrics service deployed, venv {venv_dir}, spool {spool_dir}"
    return _result(changed=changed, message=message, warnings=warnings)
