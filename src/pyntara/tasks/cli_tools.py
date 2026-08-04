"""Task cli_tools: install the console utility set.

The package list is the single source of truth in this module. The task
checks the real system state with dpkg-query and installs only what is
missing, so repeated runs change nothing (docs/contracts/task-model.md).
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# Console utilities installed in every install mode: mc file manager, htop
# process monitor, hollywood decorative terminal screens.
PACKAGES = ("mc", "htop", "hollywood")

# apt must never ask questions; all package operations run noninteractive.
APT_EXTRA_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def _is_installed(package: str) -> bool:
    """True when dpkg considers the package fully installed.

    The status query distinguishes "install ok installed" from leftovers
    like "deinstall ok config-files", so an uninstalled package is never
    treated as installed.
    """

    result = run_command(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        capture=True,
        timeout=30,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _install_packages(packages: list[str]) -> None:
    """Install packages with the optimistic apt strategy.

    First attempt without an index refresh; when the index is stale the
    first install fails, so refresh and retry once (bootstrap contract
    section 2 uses the same strategy).
    """

    try:
        run_command(
            ["apt-get", "install", "-y", *packages],
            extra_env=APT_EXTRA_ENV,
        )
        return
    except subprocess.CalledProcessError:
        pass
    run_command(["apt-get", "update"], extra_env=APT_EXTRA_ENV)
    run_command(
        ["apt-get", "install", "-y", *packages],
        extra_env=APT_EXTRA_ENV,
    )


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached."""

    missing = [package for package in PACKAGES if not _is_installed(package)]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    try:
        _install_packages(missing)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, changed=False, error=str(exc))
    return TaskResult(
        success=True,
        changed=True,
        message=f"installed: {', '.join(missing)}",
    )
