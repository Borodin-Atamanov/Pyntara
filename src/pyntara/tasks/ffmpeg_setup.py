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

After the packages are in place the task builds the wayrecord capture
engine: the C source task_data/ffmpeg_setup/wayrecord.c is compiled with
gcc against libwayland-client and libpipewire to the configured system path
(pyntara-wayrecord), and a desktop entry is written that lists
X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1, so KWin grants the
direct screencast interface to the engine. Recording through the portal is
not needed and no screen dialog is ever shown. The build and the desktop
deploy are idempotent: an engine or entry that already matches the built
artifact is left alone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed, run_command

# The templates live in the repository clone; REPO_ROOT is monkeypatched by
# the tests to point at a fixture (docs/guides/developer-guide.md).
REPO_ROOT = Path(__file__).resolve().parents[3]
WAYRECORD_MODE = 0o755


def _wayrecord_sources() -> list[Path]:
    """The C sources of the capture engine, in compile order."""

    base = REPO_ROOT / "task_data" / "ffmpeg_setup"
    return [base / "wayrecord.c", base / "zkde-screencast-client.c"]


def _build_wayrecord(binary_path: Path, timeout: float) -> tuple[bool, str | None]:
    """Compile the engine and install it; return (changed, error).

    The build flags come from pkg-config; the binary is compiled to a
    sibling .build file first, so an identical engine is detected by byte
    comparison and left alone (idempotent deploy). A missing source, a
    failed build or an install error is an error string.
    """

    sources = _wayrecord_sources()
    for source in sources:
        if not source.is_file():
            return False, f"missing wayrecord source: {source}"
    flags_result = run_command(
        ["pkg-config", "--cflags", "--libs", "wayland-client", "libpipewire-0.3"],
        capture=True,
        check=False,
        timeout=timeout,
    )
    if flags_result.returncode != 0:
        return False, (
            "cannot resolve build flags "
            "(pkg-config wayland-client libpipewire-0.3)"
        )
    flags = flags_result.stdout.strip().split()
    build_path = binary_path.parent / (binary_path.name + ".build")
    try:
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "gcc",
                "-O2",
                "-o",
                str(build_path),
                str(sources[0]),
                str(sources[1]),
                *flags,
            ],
            check=True,
            timeout=timeout,
        )
        built = build_path.read_bytes()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        build_path.unlink(missing_ok=True)
        return False, f"cannot build wayrecord engine: {exc}"
    try:
        if binary_path.is_file() and binary_path.read_bytes() == built:
            build_path.unlink(missing_ok=True)
            return False, None
        build_path.replace(binary_path)
        binary_path.chmod(WAYRECORD_MODE)
    except OSError as exc:
        build_path.unlink(missing_ok=True)
        return False, f"cannot install wayrecord engine: {exc}"
    return True, None


def _desktop_content(bin_path: Path) -> str:
    """The desktop entry that grants the screencast interface to the engine.

    KWin matches the running engine by its Exec path and grants the
    interfaces listed in X-KDE-Wayland-Interfaces, exactly like Spectacle.
    """

    return (
        "[Desktop Entry]\n"
        "Name=Pyntara Wayrecord\n"
        "Comment=Wayland screen capture source for ffmpeg\n"
        f"Exec={bin_path}\n"
        "Icon=camera-video\n"
        "Type=Application\n"
        "NoDisplay=true\n"
        "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1\n"
    )


def _deploy_desktop(ctx: Context) -> tuple[bool, str | None]:
    """Write the trusted-app desktop entry; return (changed, error)."""

    cfg = ctx.config.ffmpeg_setup
    target = cfg.wayrecord_desktop_path
    content = _desktop_content(cfg.wayrecord_bin_path)
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write wayrecord desktop entry: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install ffmpeg, build the wayrecord engine and register it.

    The goal is reached when every configured package is installed and the
    engine plus its desktop entry already match the built artifacts; the
    task then returns changed=False. Otherwise it installs the missing
    packages with the shared install_packages helper (apt index refreshed
    once unless skip_apt_update), builds the engine, writes the desktop
    entry and reports what it did. The version is not verified: the archive
    on the target platform carries the current ffmpeg and receives its
    updates through the regular apt upgrade.
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
    engine_changed, engine_error = _build_wayrecord(
        cfg.wayrecord_bin_path, install_timeout
    )
    if engine_error:
        return TaskResult(
            success=False,
            changed=bool(installed_packages),
            error=engine_error,
        )
    desktop_changed, desktop_error = _deploy_desktop(ctx)
    if desktop_error:
        return TaskResult(
            success=False,
            changed=bool(installed_packages) or engine_changed,
            error=desktop_error,
        )
    changed = bool(installed_packages) or engine_changed or desktop_changed
    messages: list[str] = []
    if installed_packages:
        messages.append(f"installed {', '.join(installed_packages)}")
    if engine_changed:
        messages.append(f"wayrecord engine built to {cfg.wayrecord_bin_path}")
    if desktop_changed:
        messages.append(f"wayrecord desktop entry written to {cfg.wayrecord_desktop_path}")
    if not messages:
        messages.append("already installed")
    if warnings:
        messages.append(f"warnings: {'; '.join(warnings)}")
    return TaskResult(success=True, changed=changed, message="; ".join(messages))
