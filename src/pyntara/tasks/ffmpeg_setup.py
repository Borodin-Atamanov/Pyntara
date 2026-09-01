"""Task ffmpeg_setup: install ffmpeg from the Ubuntu archive.

The target goal is a working ffmpeg on the command line. The archive on the
target platform carries a complete GPL ffmpeg build (the meta package
ffmpeg pulls ffmpeg, ffprobe, ffplay and the shared libraries), so apt is
the whole install path: no third-party repository, no source build and no
version chase. The task installs whatever the archive carries without
checking the version or the feature set; installed means success. It
installs the configured packages through the shared install_packages helper
(utils.py) and succeeds only when every configured package is installed, so
a package that still fails is an error TaskResult: the runner continues with
the remaining tasks and never stops here.
"""

from __future__ import annotations

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed


def task(ctx: Context) -> TaskResult:
    """Install ffmpeg from the archive; skip when already done.

    The goal is reached when every configured package is installed; the task
    then returns changed=False with message "already installed". Otherwise it
    installs the missing packages with the shared install_packages helper
    (apt index refreshed once unless skip_apt_update) and reports what it
    did. The version is not verified: the archive on the target platform
    carries the current ffmpeg and receives its updates through the regular
    apt upgrade.
    """

    cfg = ctx.config.ffmpeg_setup
    install_timeout = ctx.config.engine.command_timeout_seconds

    installed_packages: list[str] = []
    warnings: list[str] = []
    missing = [
        package
        for package in cfg.packages
        if not package_is_installed(package, cfg.package_status_timeout_seconds)
    ]
    if missing:
        _log(f"installing: {', '.join(missing)}")
        installed, failures, install_warnings = install_packages(
            missing,
            install_timeout=install_timeout,
            update_timeout=install_timeout,
            retries=cfg.package_install_retries,
            skip_update=ctx.skip_apt_update,
        )
        installed_packages = installed
        warnings.extend(install_warnings)
        if failures:
            failed_names = "; ".join(f"{name}: {reason}" for name, reason in failures)
            detail = f"failed to install: {failed_names}"
            if warnings:
                detail = f"{detail}; {'; '.join(warnings)}"
            return TaskResult(
                success=False, changed=bool(installed_packages), error=detail
            )
    changed = bool(installed_packages)
    messages: list[str] = []
    if installed_packages:
        messages.append(f"installed {', '.join(installed_packages)}")
    if not messages:
        messages.append("already installed")
    if warnings:
        messages.append(f"warnings: {'; '.join(warnings)}")
    return TaskResult(success=True, changed=changed, message="; ".join(messages))
