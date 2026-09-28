"""Task swapfile_service_install: deploy the swap program and its boot service.

The swap file itself belongs to the program the section ships
(task_data/swapfile_service_install/configure_swapfile.py). That program reads
the installed memory and the free space, computes the size, creates the file,
formats it, activates it and refuses storage that keeps its data in memory; the
boot service runs the very same program at every start, so the run and the boot
apply one code and cannot drift apart. The task deploys the program, renders the
unit template with the command line it builds from the values module, enables
the service and runs the program once, so the work of this run is done by the
code the machine will run at its next boot.

The program prints one line per step and its last line carries the result as a
JSON object of changed, skipped_reason and error; that object is the only thing
this task parses, while the sentences go into the run log as they are. A storage
that keeps its data in memory is refused by the program and reported here as a
warning of a completed task: the machine stays usable without a disk swap file
instead of the run failing, and the service stays installed, because the storage
of the next boot may well be a disk.

The packages that carry the tools of the program are installed through the
shared package helper, so the section never assumes the machine already has
them. The path of the tool the unit stops the swap with is resolved at run time
and rendered into the unit, so the unit carries the absolute path this machine
really has and takes nothing from PATH at boot.

Every step is idempotent: the program is written only when its content differs
from the deployed one, the unit only when the rendered content differs, and the
service is enabled only when it is not enabled yet. Both files are written
through a temporary file that replaces the target in one step, because a machine
that loses power in the middle of an in-place write would be left with a
truncated program or unit and the next boot would fail on it. After the program
has run, the unit itself is started, so the artifact the next boot uses is
proved by this run instead of being trusted.

The section also makes hibernation possible, because a swap file that no resume
address names is a swap file the kernel can never resume from. The address has
two halves: the device that holds the swap file and the offset of the swap file
header inside that device. The device is read from the fstab line of the mount
that holds the swap file, so it is written the way this machine reaches that
filesystem, which is a UUID for a filesystem that is mounted directly and the
mapper path of an encrypted root. The offset is read by the program from the
machine itself, because it moves whenever the swap file is created again. Both
halves are written into the kernel command line of the boot menu as well, since
the kernel ignores the offset of the resume parameter alone, and the device is
written into the file the initramfs reads as well, so a machine whose command
line was edited by hand still carries it. The task reads both artifacts back and
reports what does not hold, because an address that is published halfway leaves
a machine whose hibernation fails without a word.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara import fstab
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import failure_detail, install_missing_packages
from pyntara.utils import (
    run_command,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import swapfile_service_install as values

# Suffix of the temporary file an atomic write goes through before it replaces
# the deployed program or the rendered unit.
TEMPORARY_FILE_SUFFIX: str = ".tmp"


def _number_text(value: float) -> str:
    """A size factor the way the command line carries it: 2 instead of 2.0."""

    return str(int(value)) if float(value).is_integer() else str(value)


def _command_path(command_name: str) -> str | None:
    """Absolute path of a command the rendered unit needs.

    The path is discovered at run time instead of being written down, so the
    unit carries the path this machine really has and the boot service takes
    nothing from PATH. None means the machine does not carry the command.
    """

    return shutil.which(command_name)


def _write_file_atomically(path: Path, content: bytes | str) -> None:
    """Write a file so that it is either the old content or the new one.

    The content goes into a temporary file next to the target and replaces it
    in one step, because a machine that loses power in the middle of an
    in-place write would be left with a truncated file.
    """

    temporary = path.with_name(path.name + TEMPORARY_FILE_SUFFIX)
    if isinstance(content, bytes):
        temporary.write_bytes(content)
    else:
        temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _grub_command_line_node() -> str:
    """Augeas node that carries the kernel command line of this machine.

    The node is the prefix of the values, the path of the settings file and the
    key of the line, so the program replaces one node of the tree augeas parsed
    instead of editing the syntax of a machine file itself.
    """

    return (
        f"{engine_values.AUGEAS_FILES_NODE_PREFIX}{values.GRUB_DEFAULT_FILE_PATH}/"
        f"{values.GRUB_COMMAND_LINE_KEY}"
    )


def _initramfs_resume_node() -> str:
    """Augeas node the initramfs reads the resume device from."""

    return (
        f"{engine_values.AUGEAS_FILES_NODE_PREFIX}"
        f"{values.INITRAMFS_RESUME_FILE_PATH}/{values.INITRAMFS_RESUME_KEY}"
    )


def _program_command(*, force: bool, resume_device: str) -> tuple[str, ...]:
    """The command line of the deployed program, built from the values.

    The option names are the interface of the program, which parses exactly
    these names and reads no other source; the end to end test of the section
    runs the program with this command line, so a name that drifts on one side
    fails the tests instead of failing on a target machine. The device of the
    resume address arrives with them, because the program writes that address
    into the boot menu and an empty device means there is nothing to write.
    """

    command = [
        str(values.PROGRAM_DEPLOY_PATH),
        "--swapfile",
        str(values.SWAPFILE_PATH),
        "--file-mode",
        f"{values.SWAPFILE_MODE:o}",
        "--ram-multiplier",
        _number_text(values.RAM_MULTIPLIER),
        "--ram-extra-mb",
        str(values.RAM_EXTRA_MB),
        "--disk-fraction",
        _number_text(values.DISK_FRACTION),
        "--size-tolerance-mb",
        str(values.SIZE_TOLERANCE_MB),
        "--probe-size-kb",
        str(values.PROBE_SIZE_KB),
        "--meminfo",
        str(values.MEMINFO_PATH),
        "--meminfo-total-key",
        common_values.MEMINFO_TOTAL_KEY,
        "--command-timeout-seconds",
        str(engine_values.COMMAND_TIMEOUT_SECONDS),
        "--resume-device",
        resume_device,
        "--grub-default-file",
        str(values.GRUB_DEFAULT_FILE_PATH),
        "--grub-command-line-node",
        _grub_command_line_node(),
        "--initramfs-resume-file",
        str(values.INITRAMFS_RESUME_FILE_PATH),
        "--initramfs-resume-node",
        _initramfs_resume_node(),
        "--augeas-lens",
        values.AUGEAS_SHELL_LENS,
        "--resume-device-parameter",
        values.RESUME_DEVICE_PARAMETER,
        "--resume-offset-parameter",
        values.RESUME_OFFSET_PARAMETER,
        "--power-resume-file",
        str(values.POWER_RESUME_FILE_PATH),
        "--power-resume-offset-file",
        str(values.POWER_RESUME_OFFSET_FILE_PATH),
        "--update-grub-timeout-seconds",
        str(values.UPDATE_GRUB_TIMEOUT_SECONDS),
    ]
    if force:
        command.append("--force")
    return tuple(command)


def _deploy_program(source: Path, target: Path) -> tuple[bool, str | None]:
    """Put the program at its deployed path with the declared mode.

    The file is written only when its content differs, so a rerun leaves the
    deployed program alone and reports no change of its own.
    """

    try:
        content = source.read_bytes()
    except OSError as exc:
        return False, f"cannot read the program {source}: {exc}"
    try:
        if target.is_file() and target.read_bytes() == content:
            _log(f"program already deployed: {target}")
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_file_atomically(target, content)
        target.chmod(values.PROGRAM_FILE_MODE)
    except OSError as exc:
        return False, f"cannot deploy the program to {target}: {exc}"
    _log(f"program deployed: {target}")
    return True, None


def _render_unit(
    template_path: Path,
    command: tuple[str, ...],
    swapfile_path: Path,
    swapoff_path: str,
) -> str:
    """Render the unit template with the command line and the paths.

    The swapoff path is resolved at run time by the caller, so the rendered unit
    carries the absolute path this machine really has.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={' '.join(command)}",
        swapfile_path=str(swapfile_path),
        swapoff_path=swapoff_path,
    )


def _write_unit_file(
    unit_dir: Path, service_name: str, content: str
) -> tuple[bool, str | None]:
    """Write the unit file when it differs from the rendered content."""

    path = unit_dir / service_name
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            _log(f"unit file already current: {path}")
            return False, None
        unit_dir.mkdir(parents=True, exist_ok=True)
        _write_file_atomically(path, content)
    except OSError as exc:
        return False, f"cannot write the unit file {path}: {exc}"
    _log(f"unit file written: {path}")
    return True, None


def _parse_program_result(stdout: str) -> dict[str, object] | None:
    """The result object the program prints as its last line, or None.

    The program answers with one JSON object; anything else means the caller
    cannot know what happened, and the task then reports that instead of
    guessing from the sentences.
    """

    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            parsed: object = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
        return None
    return None


def _reported_offset_pages(outcome: dict[str, object]) -> int | None:
    """The resume offset the program reported, or None.

    The offset is a whole number of pages. A program that could not read it
    leaves the key empty, which the caller reports instead of publishing an
    address that names a place no image is written to.
    """

    reported = outcome.get("resume_offset")
    if isinstance(reported, bool) or not isinstance(reported, int):
        return None
    return reported


def _mount_point_of(path: Path, timeout: float) -> tuple[str | None, str | None]:
    """Mount point that holds a path, and the reason it was not read."""

    command = substituted_command(values.MOUNT_POINT_COMMAND, {"path": str(path)})
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"cannot read the mount point of {path}: {exc}"
    for line in result.stdout.splitlines():
        mount_point = line.strip()
        if mount_point:
            return mount_point, None
    return None, f"the machine reported no mount point for {path}"


def _resume_device_spec(
    swapfile_directory: Path, timeout: float
) -> tuple[str | None, list[str]]:
    """Device the resume address names, in the spelling of this machine.

    The fstab line of the mount that holds the swap file carries the device the
    way this machine reaches that filesystem: a UUID for a filesystem that is
    mounted directly, and the mapper path of an encrypted root, which is the
    name the initramfs can use once it has unlocked that root. A machine whose
    fstab carries no line for that mount is told about instead of being given an
    address that names nothing.
    """

    mount_point, error = _mount_point_of(swapfile_directory, timeout)
    if mount_point is None:
        return None, [error or "the mount point of the swap file was not read"]
    try:
        text = values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"cannot read {values.FSTAB_PATH}: {exc}"]
    device_spec = fstab.spec_of_mount_point(text, mount_point)
    if device_spec is None:
        return None, [f"{values.FSTAB_PATH} carries no line for {mount_point}"]
    _log(f"the resume address names {device_spec} for {mount_point}")
    return device_spec, []


def _initramfs_resume_file_text() -> str | None:
    """Content of the file the initramfs reads, or None when it is not there.

    The task reads the file around the run of the program, because the program
    is what writes it: a file that changed means the initial ramdisk carries an
    older device and has to be rebuilt.
    """

    try:
        return values.INITRAMFS_RESUME_FILE_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def _rebuild_initramfs() -> list[str]:
    """Rebuild the initial ramdisk so that it carries the resume file."""

    try:
        run_command(
            list(values.UPDATE_INITRAMFS_COMMAND),
            timeout=values.UPDATE_INITRAMFS_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return [f"the initial ramdisk was not rebuilt: {exc}"]
    _log("initial ramdisk rebuilt")
    return []


def _verify_resume_artifacts(offset_pages: int, warnings: list[str]) -> None:
    """Read back what was published and report what does not hold.

    The boot menu is what the next boot reads and the initial ramdisk is what
    carries the resume file the initramfs reads before it mounts anything. Both
    are read here instead of being trusted, because an address that is published
    halfway leaves a machine whose hibernation fails without a word.
    """

    wanted_parameter = f"{values.RESUME_OFFSET_PARAMETER}={offset_pages}"
    try:
        menu_text = values.GRUB_CONFIG_FILE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {values.GRUB_CONFIG_FILE_PATH}: {exc}")
    else:
        if wanted_parameter in menu_text:
            _log(f"the boot menu carries {wanted_parameter}")
        else:
            warnings.append(
                f"{values.GRUB_CONFIG_FILE_PATH} carries no {wanted_parameter}"
            )
    wanted_file = (
        f"{values.INITRAMFS_CONF_DIRECTORY_IN_IMAGE}/"
        f"{values.INITRAMFS_RESUME_FILE_PATH.name}"
    )
    command = substituted_command(
        values.INITRAMFS_IMAGE_LIST_COMMAND,
        {"kernel_release": platform.release()},
    )
    try:
        result = run_command(
            command,
            check=False,
            capture=True,
            timeout=values.UPDATE_INITRAMFS_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"cannot list the initial ramdisk: {exc}")
        return
    if wanted_file in result.stdout:
        _log(f"the initial ramdisk carries {wanted_file}")
    else:
        warnings.append(
            f"the initial ramdisk of {platform.release()} carries no {wanted_file}"
        )


def _write_polkit_rule(data_dir: Path) -> tuple[bool, list[str]]:
    """Write the rule that lets the desktop user hibernate; report a change.

    The rule is rendered from the template of the section with the desktop user
    of this machine and the two actions the session menu asks for. A machine
    without a desktop account has no session menu to offer hibernation in, so
    nothing is written there.
    """

    username = common_values.DESKTOP_USERNAME
    if not username:
        return False, []
    path = values.POLKIT_RULE_FILE_PATH
    try:
        template = Template(
            (data_dir / values.POLKIT_RULE_TEMPLATE_FILE_NAME).read_text(
                encoding="utf-8"
            )
        )
        content = template.substitute(
            desktop_user=username,
            hibernate_action=values.POLKIT_HIBERNATE_ACTION,
            hibernate_multiple_sessions_action=(
                values.POLKIT_HIBERNATE_MULTIPLE_SESSIONS_ACTION
            ),
        )
    except (OSError, KeyError, ValueError) as exc:
        return False, [f"cannot render the hibernation permission rule: {exc}"]
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            _log(f"the hibernation permission rule is current: {path}")
            return False, []
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_file_atomically(path, content)
        path.chmod(values.POLKIT_RULE_FILE_MODE)
    except OSError as exc:
        return False, [f"cannot write {path}: {exc}"]
    _log(f"hibernation permission rule written: {path}")
    return True, []


def _hibernate_answer_for_desktop_user(
    timeout: float,
) -> tuple[bool | None, str | None]:
    """Whether the machine offers hibernation to the desktop user.

    The question is asked as that user, because the answer is calculated for the
    account that asks and a machine that refuses the user answers root with yes.
    None means the answer could not be read, and the reason is returned with it.
    """

    command = [
        *substituted_command(
            values.RUNUSER_COMMAND, {"username": common_values.DESKTOP_USERNAME}
        ),
        *values.LOGIND_HIBERNATE_QUERY_COMMAND,
    ]
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"cannot ask the machine about hibernation: {exc}"
    answer = result.stdout.strip()
    if values.HIBERNATE_AVAILABLE_ANSWER in answer:
        return True, None
    return False, answer or f"exit code {result.returncode}"


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Deploy the swap program and its boot service; the program does the work.

    The goal is reached when the program is deployed, the unit carries the
    rendered command line, the service is enabled and the program reports the
    target state of the swap file as reached; the task then returns
    changed=False. Otherwise it deploys what differs, enables the service,
    runs the program once and reports what the program did. The resume address
    is then written and read back, because the machine offers hibernation only
    while both halves of that address name the swap file that is really there.
    A step that cannot run is reported as a warning of a completed task, so the
    runner continues with the remaining tasks and never stops here.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on. The
        # guard stands above every read.
        return TaskResult(
            success=True,
            message=(
                "the swapfile_service_install values are not declared, "
                "nothing was changed"
            ),
            warnings=(
                "the swapfile_service_install values are not declared: "
                + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    service_name = values.SERVICE_UNIT_NAME
    data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    warnings: list[str] = []
    changed = False

    _, installed, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    warnings.extend(package_warnings)
    if installed:
        _log(f"installed for the swap tools: {', '.join(installed)}")
    if failures:
        warnings.append(f"packages of the swap tools: {failure_detail(failures)}")
    swapoff_path = _command_path(values.SWAPOFF_COMMAND_NAME)
    if swapoff_path is None:
        # The unit stops the swap through this tool and the program runs it as
        # well, so a machine without it can neither write a unit that works nor
        # create a swap file. The tool is named and the rest is left alone.
        warnings.append(
            f"{values.SWAPOFF_COMMAND_NAME} is not installed on this machine"
        )
        return _result(
            changed=False,
            message="the swap tool is not installed",
            warnings=warnings,
        )

    resume_device, device_warnings = _resume_device_spec(
        values.SWAPFILE_PATH.parent, timeout
    )
    warnings.extend(device_warnings)
    command = _program_command(force=force, resume_device=resume_device or "")

    _log(f"deploying the swap program to {values.PROGRAM_DEPLOY_PATH}")
    program_changed, program_error = _deploy_program(
        data_dir / values.PROGRAM_FILE_NAME, values.PROGRAM_DEPLOY_PATH
    )
    if program_error is not None:
        # Without the program neither this run nor a boot can set the swap up,
        # so the task reports the reason and leaves the rest of the machine
        # alone instead of installing a service that cannot start.
        warnings.append(program_error)
        return _result(
            changed=False,
            message="the swap program could not be deployed",
            warnings=warnings,
        )
    changed = changed or program_changed

    template_path = data_dir / values.UNIT_TEMPLATE_FILE_NAME
    _log(f"rendering the unit template {template_path}")
    content: str | None = None
    try:
        content = _render_unit(
            template_path, command, values.SWAPFILE_PATH, swapoff_path
        )
    except OSError as exc:
        warnings.append(f"cannot read the unit template: {exc}")
    unit_ready = False
    if content is not None:
        unit_changed, unit_error = _write_unit_file(
            engine_values.SYSTEMD_UNIT_DIR, service_name, content
        )
        if unit_error is not None:
            warnings.append(unit_error)
        else:
            unit_ready = True
            if unit_changed:
                changed = True
                try:
                    run_command(
                        list(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND), timeout=timeout
                    )
                    _log("systemd reloaded")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    warnings.append(f"systemd reload failed: {exc}")
        enabled = service_is_enabled(service_name, timeout)
        _log(
            f"checking autorun service {service_name}: "
            f"{'enabled' if enabled else 'disabled'}"
        )
        if not enabled:
            try:
                run_command(
                    substituted_command(
                        values.SYSTEMCTL_ENABLE_COMMAND,
                        {"service_unit_name": service_name},
                    ),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"systemd enable failed: {exc}")
            else:
                _log("service enabled")
                changed = True

    offset_pages: int | None = None
    resume_file_before = _initramfs_resume_file_text()
    try:
        result = run_command(list(command), check=False, capture=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"the swap program could not run: {exc}")
    else:
        for line in result.stdout.splitlines():
            _log(line)
        outcome = _parse_program_result(result.stdout)
        if outcome is None:
            warnings.append(
                "the swap program reported nothing readable, "
                f"exit code {result.returncode}"
            )
        else:
            error_text = outcome.get("error")
            skipped_reason = outcome.get("skipped_reason")
            if isinstance(error_text, str) and error_text:
                warnings.append(f"the swap program failed: {error_text}")
            elif isinstance(skipped_reason, str) and skipped_reason:
                warnings.append(f"no swap file was created: {skipped_reason}")
            if outcome.get("changed") is True:
                changed = True
            offset_pages = _reported_offset_pages(outcome)

    if offset_pages is None or resume_device is None:
        warnings.append(
            "the resume address was not published: "
            + (
                "the device that holds the swap file was not read"
                if resume_device is None
                else "the program reported no resume offset"
            )
        )
    else:
        if _initramfs_resume_file_text() != resume_file_before:
            changed = True
            warnings.extend(_rebuild_initramfs())
        _verify_resume_artifacts(offset_pages, warnings)
        rule_changed, rule_warnings = _write_polkit_rule(data_dir)
        warnings.extend(rule_warnings)
        if rule_changed:
            changed = True
        if common_values.DESKTOP_USERNAME:
            available, description = _hibernate_answer_for_desktop_user(timeout)
            if available is True:
                _log(
                    "hibernation is offered to "
                    f"{common_values.DESKTOP_USERNAME}"
                )
            elif available is False:
                warnings.append(
                    "the machine does not offer hibernation to "
                    f"{common_values.DESKTOP_USERNAME}: {description}"
                )

    if unit_ready:
        # The unit is the artifact the next boot runs, so it is started here as
        # well: a unit that cannot start is a finding of this run instead of a
        # surprise of the next boot. The program already did the work above, so
        # this start changes nothing.
        try:
            run_command(
                substituted_command(
                    values.SYSTEMCTL_START_COMMAND,
                    {"service_unit_name": service_name},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"the boot service could not be started: {exc}")

    if changed:
        message = (
            f"swapfile {values.SWAPFILE_PATH} and service {service_name} configured"
        )
    else:
        message = "already configured"
    if offset_pages is not None and resume_device is not None:
        message += (
            f"; hibernation resumes from {resume_device} at offset {offset_pages}"
        )
        message += f"; {values.SESSION_RELOAD_MESSAGE}"
    return _result(changed=changed, message=message, warnings=warnings)
