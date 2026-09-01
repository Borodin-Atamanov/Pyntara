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

After the packages are in place the task deploys the wayrecord bridge: the
script task_data/ffmpeg_setup/wayrecord.py is copied to the configured
system path (pyntara-wayrecord) and made executable, so the desktop user can
record the Wayland screen into ffmpeg through the ScreenCast portal. The
deploy is idempotent: a target file that already matches the template is
left alone.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed

# The template lives in the repository clone; REPO_ROOT is monkeypatched by
# the tests to point at a fixture (docs/guides/developer-guide.md).
REPO_ROOT = Path(__file__).resolve().parents[3]
WAYRECORD_MODE = 0o755


def _deploy_wayrecord(ctx: Context) -> tuple[bool, str | None]:
    """Deploy the wayrecord script to its system path; return (changed, error).

    The script task_data/ffmpeg_setup/wayrecord.py is copied to
    cfg.wayrecord_bin_path and made executable. When the target already
    matches the template nothing is written.
    """

    cfg = ctx.config.ffmpeg_setup
    source = REPO_ROOT / "task_data" / "ffmpeg_setup" / "wayrecord.py"
    target = cfg.wayrecord_bin_path
    try:
        template = source.read_bytes()
    except OSError as exc:
        return False, f"cannot read wayrecord template: {exc}"
    try:
        if target.exists() and target.read_bytes() == template:
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(template)
        target.chmod(WAYRECORD_MODE)
    except OSError as exc:
        return False, f"cannot write wayrecord script: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install ffmpeg and deploy the wayrecord bridge; skip when done.

    The goal is reached when every configured package is installed and the
    wayrecord script already matches the template; the task then returns
    changed=False. Otherwise it installs the missing packages with the
    shared install_packages helper (apt index refreshed once unless
    skip_apt_update), deploys the script and reports what it did. The
    version is not verified: the archive on the target platform carries the
    current ffmpeg and receives its updates through the regular apt upgrade.
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
    wayrecord_changed, wayrecord_error = _deploy_wayrecord(ctx)
    if wayrecord_error:
        return TaskResult(
            success=False, changed=bool(installed_packages), error=wayrecord_error
        )
    changed = bool(installed_packages) or wayrecord_changed
    messages: list[str] = []
    if installed_packages:
        messages.append(f"installed {', '.join(installed_packages)}")
    if wayrecord_changed:
        messages.append(f"wayrecord deployed to {cfg.wayrecord_bin_path}")
    if not messages:
        messages.append("already installed")
    if warnings:
        messages.append(f"warnings: {'; '.join(warnings)}")
    return TaskResult(success=True, changed=changed, message="; ".join(messages))
