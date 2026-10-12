"""Download and install a deb package from a release asset.

Three tasks install a program from a GitHub release deb: i2pd_service_setup,
rustdesk_setup and yggdrasil_service_setup. Each one used to carry its own copy
of the download call and the apt install, so the curl flags, the apt index
refresh and the retry loop could drift apart; they live here once and the tasks
import them (architecture contract, Configuration). Which asset fits a machine
stays knowledge of the task, so the task selects the asset and passes its name
and url.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara.utils import (
    download_file,
    install_package_once,
    refresh_apt_index,
)


def download_asset(
    download_dir: Path,
    name: str,
    url: str,
    timeout: float,
) -> None:
    """Download the package into the download directory.

    The command is the shared download call, so the flags and the progress text
    are the same as in every other download of the run. Raises RuntimeError when
    curl fails, so the caller reports the reason.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        download_file(download_dir / name, url, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot download {url}: {exc}") from None


def install_deb(
    download_dir: Path,
    name: str,
    *,
    install_timeout: float,
    retries: int,
    update_timeout: float | None = None,
    skip_update: bool = False,
) -> tuple[bool, str]:
    """Install the downloaded deb; return (success, error_text).

    When update_timeout is given, the apt index is refreshed once before the
    install, so dependencies resolve from a fresh index; skip_update=True
    disables that refresh for test or offline runs. A caller that passes no
    update_timeout refreshes nothing on purpose: the yggdrasil package depends
    only on systemd, which is always present, so a refresh would add a failure
    point without resolving anything. Each attempt uses the shared
    noninteractive apt environment; total attempts are one initial plus retries.
    """

    if update_timeout is not None and not skip_update:
        try:
            refresh_apt_index(update_timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(retries + 1):
        ok, error = install_package_once(str(download_dir / name), install_timeout)
        if ok:
            break
    return ok, error
