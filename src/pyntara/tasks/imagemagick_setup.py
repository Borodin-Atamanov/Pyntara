"""Task imagemagick_setup: install ImageMagick from the Ubuntu archive.

The target goal is a working ImageMagick on the command line. On Kubuntu
26.04 and newer the archive already ships ImageMagick 7 (the meta package
imagemagick pulls imagemagick-7.q16), so apt is the whole install path: no
third-party repository, no AppImage, no source build and no version chase.
The task installs the configured packages through the shared install_packages
helper (utils.py) and succeeds only when every configured package is
installed, so a package that still fails is an error TaskResult: the runner
continues with the remaining tasks and never stops here.
"""

from __future__ import annotations

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed


def task(ctx: Context) -> TaskResult:
    """Install ImageMagick; skip when the goal is already reached.

    The goal is reached when every configured package is installed; the
    task then returns changed=False. Otherwise it installs the missing
    packages with the shared install_packages helper (apt index refreshed
    once unless skip_apt_update) and reports the installed packages. The
    version is not verified: the archive on the target platform carries
    the current ImageMagick and receives its updates through the regular
    apt upgrade.
    """

    cfg = ctx.config.imagemagick_setup
    install_timeout = ctx.config.engine.command_timeout_seconds

    missing = [
        package
        for package in cfg.packages
        if not package_is_installed(package, cfg.package_status_timeout_seconds)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    _log(f"installing: {', '.join(missing)}")
    installed, failures, warnings = install_packages(
        missing,
        install_timeout=install_timeout,
        update_timeout=install_timeout,
        retries=cfg.package_install_retries,
        skip_update=ctx.skip_apt_update,
    )
    changed = bool(installed)
    if failures:
        failed_names = "; ".join(f"{name}: {reason}" for name, reason in failures)
        detail = f"failed to install: {failed_names}"
        if warnings:
            detail = f"{detail}; {'; '.join(warnings)}"
        return TaskResult(success=False, changed=changed, error=detail)
    message = f"installed {', '.join(installed)}"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(success=True, changed=changed, message=message)
