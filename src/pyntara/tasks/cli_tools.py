"""Task cli_tools: install the console utility set.

The package list is the single source of truth in this module. It uses the
comment-friendly format parsed by parse_commented_lines: one package per
line, # starts a comment. The task checks the real system state with
dpkg-query and installs only what is missing, so repeated runs change
nothing (docs/contracts/task-model.md).
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import parse_commented_lines, run_command

# Console utilities installed in every install mode. The list mirrors the
# classic apt package-list format: one package per line, # starts a comment.
# Packages owned by dedicated parts of the system stay out of this list:
# curl and git come from inst.sh, ssh tooling from the ssh task,
# zram-tools from the zram task, ffmpeg and imagemagick from the apps task.
PACKAGES = parse_commented_lines(
    """
    # file managers
    mc
    nnn

    # archives and compression
    unrar
    unrar-free
    unzip

    # system information
    inxi
    lsscsi
    ncdu
    htop
    nload
    nmon
    net-tools
    traceroute
    whois

    # arp is a command from net-tools, which is listed above
    # bind-utils is the RHEL package name; dnsutils covers dig on Ubuntu
    dnsutils
    bind9-utils

    # file and disk tools
    exfat-fuse
    fdupes
    lshw

    # media information
    mediainfo
    exiftool

    # network and misc utilities
    nmap
    tcpdump
    wget
    expect
    augeas-tools
    calc
    hollywood
    """
)

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
