"""Task snap_remove: remove the snap subsystem and return the space it held.

The described goal is a machine without the snap subsystem: the snapd daemon,
its KDE Discover integration and every snap the daemon carries are gone, and
the directories that held the snap state are removed, so the space the snaps
occupied returns to the filesystem. The purge of the snapd package does the
work through the postrm of that package, which stops the snap units, unmounts
every snap and removes /snap, /var/snap, /var/cache/snapd and /var/lib/snapd.
The task removes no snap on its own, because a snap another snap builds on
cannot be removed alone, and the package resolves that ordering itself.

The task takes no dependency. The catalog orders it after firefox_setup, so on
a desktop the browser is the Mozilla deb before the snap subsystem goes away,
while a server or a minimal machine reaches the task on its own. A machine
without the snap packages is left alone, so a repeated run changes nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import apt_purge_packages_command, package_is_installed, run_command
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import snap_remove as values


def _snap_package_is_installed(timeout: float) -> bool:
    """True when any package whose purpose is snap is installed."""

    return any(package_is_installed(name, timeout) for name in values.PACKAGE_NAMES)


def _purge_snap_packages(timeout: float) -> str | None:
    """Purge the snap packages; an error text, or None when it worked.

    apt runs noninteractive through the declared environment. The purge of
    snapd runs the postrm of that package, which removes the snaps and the
    directories that hold them. The output is captured, so the noisy postrm
    does not drown the run log while a failure still carries its text.
    """

    try:
        result = run_command(
            apt_purge_packages_command(
                values.PACKAGE_NAMES, values.APT_PURGE_EXTRA_FLAGS
            ),
            check=False,
            capture=True,
            timeout=timeout,
            extra_env=dict(engine_values.APT_NONINTERACTIVE_ENVIRONMENT),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot remove the snap packages: {exc}"
    if result.returncode == 0:
        return None
    answer = f"{result.stdout}\n{result.stderr}".strip()
    return f"cannot remove the snap packages: {answer}"


def _remaining_snap_paths() -> tuple[Path, ...]:
    """The snap directories that are still present."""

    return tuple(path for path in values.SNAP_PATH_NAMES if path.exists())


def task(ctx: Context) -> TaskResult:
    """Remove the snap subsystem and return the space it held."""

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        return TaskResult(
            success=True,
            message="the snap_remove values are not declared, nothing was changed",
            warnings=(
                "the snap_remove values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS

    _log("checking the snap subsystem")
    if not _snap_package_is_installed(timeout):
        return TaskResult(
            success=True, message="the snap subsystem is not installed"
        )

    _log("removing the snap packages and every snap they carry")
    error = _purge_snap_packages(timeout)
    if error:
        return TaskResult(
            success=True,
            changed=False,
            message="the snap subsystem was not removed",
            warnings=(error,),
        )

    warnings: list[str] = []
    remaining = _remaining_snap_paths()
    if remaining:
        warnings.append(
            "the snap directories are still present: "
            + ", ".join(str(path) for path in remaining)
        )

    message = "removed the snap subsystem: " + ", ".join(values.PACKAGE_NAMES)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=True, message=message, warnings=tuple(warnings)
    )
