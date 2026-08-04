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
# Each package is annotated with what it does. Packages owned by dedicated
# parts of the system stay out of this list: curl and git come from inst.sh,
# ssh tooling from the ssh task, zram-tools from the zram task, ffmpeg and
# imagemagick from the apps task.
PACKAGES = parse_commented_lines(
    """
    # file managers
    # mc: Midnight Commander, two-panel console file manager
    mc
    # nnn: lightweight terminal file browser
    nnn

    # archivers
    # unrar: extractor for the proprietary RAR archive format
    unrar
    # unrar-free: free replacement for unrar
    unrar-free
    # unzip: ZIP archive extractor
    unzip

    # system information
    # inxi: full system information report
    inxi
    # lsscsi: list SCSI and other storage devices
    lsscsi
    # lshw: detailed hardware information
    lshw

    # resource monitoring
    # htop: interactive process viewer
    htop
    # nmon: system performance monitor for CPU, memory, network, disk
    nmon
    # ncdu: disk usage analyzer with interactive curses interface
    ncdu
    # nload: console network traffic monitor
    nload

    # network tools
    # net-tools: classic networking tools, ifconfig, netstat, route, arp
    net-tools
    # nmap: network scanner
    nmap
    # tcpdump: command-line packet analyzer
    tcpdump
    # traceroute: trace the network path to a remote host
    traceroute
    # whois: client for the whois directory service
    whois
    # wget: non-interactive network downloader
    wget

    # arp is a command from net-tools, which is listed above
    # bind-utils is the RHEL package name; dnsutils covers dig on Ubuntu
    # DNS utilities
    # dnsutils: DNS utilities dig, nslookup and host
    dnsutils

    # disk and file tools
    # exfat-fuse: read and write exFAT filesystems through FUSE
    exfat-fuse
    # fdupes: find and remove duplicate files
    fdupes

    # media tools
    # mediainfo: technical details of media files, codecs and streams
    mediainfo
    # exiftool: read and write EXIF and IPTC metadata in media files
    exiftool

    # automation and configuration
    # expect: automate interactive terminal programs
    expect
    # augeas-tools: command-line tools for the Augeas configuration editor
    augeas-tools

    # small utilities
    # calc: arbitrary precision calculator
    calc
    # hollywood: decorative fake Hollywood-style terminal activity
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
