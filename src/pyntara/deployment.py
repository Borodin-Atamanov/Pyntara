"""Which version of Pyntara a deployed unit belongs to.

The tasks that deploy our services on a target machine render systemd
units from templates under task_data/. A unit that runs the deployed
pyntara package has to say which version of that package it belongs to:
a task decides whether its deployment is already in place by comparing
the rendered unit with the unit on the machine byte for byte, so a line
that carries the version makes an update of the code visible to that
comparison, and the unit is then rewritten and the service restarted. A
task that skipped the comparison used to leave the machine running the
old code under an unchanged unit, which is exactly the failure this
module prevents (docs/spec/system-metrics.md,
docs/spec/port-forwarding-setup.md, docs/spec/upnp-forwarding-setup.md).

The version comes from the deployed interpreter and not from the running
installer, because the unit runs that interpreter: a deployment whose
venv could not be refreshed must not be passed off as up to date.
venv_package_version asks one interpreter for the version it imports and
deployed_version answers what a unit has to carry, reporting plainly when
the answer is only the caller's fallback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara.utils import run_command, substituted_command, trim_whitespace


def venv_package_version(
    version_command: tuple[str, ...], python_path: Path, timeout: float
) -> str | None:
    """The pyntara version one interpreter reports, or None.

    The import is the proof that the package is installed for that
    interpreter, and the version proves that the installed code is the
    code the unit will run. The command, its {python} placeholder and the
    bound of the call come from the caller. A missing interpreter, a
    failed import and a call that does not answer in time all mean the
    same to the caller: nothing could be read. The printed version ends
    with a newline, so the output is trimmed through the shared helper.
    """

    if not python_path.is_file():
        return None
    try:
        result = run_command(
            substituted_command(version_command, {"python": str(python_path)}),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return trim_whitespace(result.stdout) or None


def deployed_version(
    version_command: tuple[str, ...],
    python_path: Path,
    timeout: float,
    fallback: str,
) -> tuple[str, str | None]:
    """The version a unit must carry, and a warning when it is a fallback.

    The version of the deployed interpreter is the answer, because the
    unit runs that interpreter; it is the version the reader of the unit
    on the machine sees, and it is what the byte comparison of the
    deploying task notices. An interpreter that cannot be asked leaves
    the fallback as the only available answer, and the returned warning
    names that gap instead of hiding it, so the missing refresh stays
    visible in the install log.
    """

    version = venv_package_version(version_command, python_path, timeout)
    if version is None:
        return fallback, (
            f"cannot read the version of the deployed interpreter "
            f"{python_path}, the unit carries {fallback}"
        )
    return version, None
