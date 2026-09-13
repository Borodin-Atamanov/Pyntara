"""Task vocalinux_setup: install Vocalinux dictation for the desktop user.

The task installs the official Vocalinux AppImage (the release build with
the precompiled Vulkan pywhispercpp and the CPU fallback) of the pinned
version into the desktop user home, installs the Wayland injection tools
the app needs (wtype, ydotool and wl-clipboard), enables the ydotool user
unit, adds the user to the input group that owns /dev/input and
/dev/uinput, writes the app config (whisper_cpp engine with the small
model, ru language, toggle mode on super+s, autostart into the tray),
writes the autostart entry that launches the AppImage minimized, and
registers an empty KDE shortcut that consumes Meta+S so the app-level
listener toggles without typing the S letter into the focused field. The
speech model is not downloaded by the task: the config selects the small
model and Vocalinux downloads it itself on the first dictation through its
pinned and verified flow (docs/spec/vocalinux-setup.md).

The Meta+S trick and the input group membership take effect at the next
login, so the task reports that the user must log out and back in. A step
that a rerun can redo is reported as a recoverable warning; a failed
AppImage or package install is an error TaskResult.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara.config import EngineConfig, VocalinuxSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    download_command,
    dpkg_architecture,
    install_packages,
    package_is_installed,
    release_asset_architecture,
    run_command,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)


def _read_task_template(
    template_dir: Path, file_name: str, label: str
) -> tuple[str | None, str | None]:
    """Content of one configured template, or an error naming it.

    A missing template is an error and not a skipped step: the file it
    renders is part of the configured machine, and the template ships with
    the clone. The label names the file in the message, so the operator
    reads which template the run expected.
    """

    path = template_dir / file_name
    if not path.is_file():
        return None, f"missing {label}: {path}"
    return path.read_text(encoding="utf-8"), None


def _as_user_command(cfg: VocalinuxSetupConfig, command: list[str]) -> list[str]:
    """Prefix a command with the configured wrapper of the target user.

    The wrapper is a config value of the section, so a machine whose
    desktop user is reached another way is a config change.
    """

    return [
        *substituted_command(cfg.runuser_command, {"username": cfg.username}),
        *command,
    ]


def _home_env(cfg: VocalinuxSetupConfig) -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": cfg.home_dir}


def _release_download_url(
    engine: EngineConfig, repo: str, version: str, asset_name: str
) -> str:
    """The download url of a pinned release asset.

    The repository pair, the pinned version and the asset name are
    substituted into the engine-wide template, so the host is a config
    value and a mirror works without touching the code.
    """

    return engine.github_release_download_url.format(
        repo=repo, version=version, asset_name=asset_name
    )


def _asset_name(cfg: VocalinuxSetupConfig, asset_arch: str) -> str:
    """The asset file name of a Vocalinux release for one arch."""

    return cfg.asset_name_template.format(
        version=cfg.version, asset_arch=asset_arch
    )


def _appimage_install_path(
    cfg: VocalinuxSetupConfig, asset_name: str
) -> Path:
    """The install path of the pinned AppImage under the user home."""

    return Path(cfg.home_dir) / cfg.appimage_dir_relative_path / asset_name


def _write_user_file(
    cfg: VocalinuxSetupConfig,
    rel_path: str,
    content: str,
    *,
    file_mode: int,
    timeout: float,
    force: bool,
) -> bool:
    """Write one user-owned file as the target user; True when written.

    The parent directory is created as the target user, the content is
    written by the root process and then chowned and chmodded to the target
    user, so the file keeps the user ownership a desktop config file needs.
    A file that already holds the content is skipped. file_mode is the
    configured mode, applied in its octal form.
    """

    target = Path(cfg.home_dir) / rel_path
    if not force and target.is_file():
        try:
            if target.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    run_command(
        _as_user_command(cfg, ["mkdir", "-p", str(target.parent)]),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_command(["chown", f"{cfg.username}:{cfg.username}", str(target)], timeout=timeout)
    run_command(["chmod", f"{file_mode:o}", str(target)], timeout=timeout)
    _log(f"wrote {target}")
    return True


def _kconfig_command(
    cfg: VocalinuxSetupConfig,
    base_command: tuple[str, ...],
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the configured base, the groups and the key.

    The base call carries the shortcut file name and every selector is a
    config value, so another KConfig version or another tool is a config
    change. The reader and the writer share this builder, so the two calls
    can never drift apart.
    """

    command = substituted_command(
        base_command, {"file_name": cfg.shortcuts_file_name}
    )
    for segment in group_segments:
        command.extend(
            substituted_command(cfg.config_group_flag, {"group": segment})
        )
    command.extend(substituted_command(cfg.config_key_flag, {"key": key}))
    return command


def _kreadconfig(
    cfg: VocalinuxSetupConfig,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one KConfig key, or an empty string when unset."""

    command = _kconfig_command(
        cfg, cfg.kreadconfig_command, group_segments, key
    )
    result = run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _kwriteconfig(
    cfg: VocalinuxSetupConfig,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
) -> None:
    """Write one KConfig key with the configured writer as the target user."""

    command = _kconfig_command(
        cfg, cfg.kwriteconfig_command, group_segments, key
    )
    command.append(value)
    run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )


def _sync_echo_shortcut(
    cfg: VocalinuxSetupConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the Meta+S consuming shortcut; True when written.

    The empty .desktop action is registered under the KDE services
    component, so Plasma owns Meta+S and the S never reaches the focused
    field, while the Vocalinux app-level listener still sees the raw key
    and toggles. The write applies at the next login.
    """

    group = (cfg.shortcut_group_name, cfg.shortcut_entry_name)
    current = _kreadconfig(cfg, group, cfg.shortcut_action_name, timeout)
    if not force and current == cfg.shortcut_key_sequence:
        return False
    _kwriteconfig(
        cfg,
        group,
        cfg.shortcut_action_name,
        cfg.shortcut_key_sequence,
        timeout=timeout,
    )
    _log(
        f"set {cfg.shortcuts_file_name} {cfg.shortcut_action_name}: "
        f"{cfg.shortcut_key_sequence}"
    )
    return True


def _autostart_content(template: str, appimage_path: Path) -> str:
    """The autostart desktop entry that launches the AppImage minimized.

    Written by the task, not by the app: the app autostart manager would
    emit a broken Exec for an AppImage (the FUSE mount path or a venv
    wrapper), so the task pins the Exec to the stable install path. The
    body of the entry lives in the template and only the AppImage path is
    substituted, so the entry text stays with the template.
    """

    return Template(template).substitute(appimage=str(appimage_path))


def _install_appimage(
    cfg: VocalinuxSetupConfig,
    engine: EngineConfig,
    *,
    timeout: float,
    force: bool,
) -> tuple[bool, str | None]:
    """Install the pinned AppImage under the user home; (changed, error).

    The asset arch follows the dpkg architecture of the machine. The
    release asset is trusted as is and its checksum is not verified: the
    source is the official GitHub release of the pinned version. The file
    is downloaded into the root download_dir cache once and copied into
    the user home, so a rerun with a present install file changes nothing
    and never downloads again. A superseded install of another version is
    moved into the user trash, never deleted.
    """

    arch = dpkg_architecture(timeout)
    asset_arch = release_asset_architecture(
        engine.release_asset_architectures, arch
    )
    asset_name = _asset_name(cfg, asset_arch)
    url = _release_download_url(engine, cfg.github_repo, cfg.version, asset_name)
    install_dir = Path(cfg.home_dir) / cfg.appimage_dir_relative_path
    target = install_dir / asset_name
    cache = cfg.download_dir / asset_name
    if target.is_file() and not force:
        return False, None
    run_command(
        _as_user_command(cfg, ["mkdir", "-p", str(install_dir)]),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )
    install_dir.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        partial = cfg.download_dir / (
            asset_name + engine.partial_download_file_suffix
        )
        try:
            run_command(
                download_command(engine, partial, url),
                timeout=timeout,
            )
            partial.replace(cache)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            partial.unlink(missing_ok=True)
            return False, f"cannot download {url}: {exc}"
    try:
        shutil.copyfile(cache, target)
    except OSError as exc:
        return False, f"cannot install {target}: {exc}"
    run_command(["chown", f"{cfg.username}:{cfg.username}", str(target)], timeout=timeout)
    run_command(
        ["chmod", f"{cfg.executable_file_mode:o}", str(target)], timeout=timeout
    )
    trash_dir = Path(cfg.home_dir) / ".local" / "share" / "Trash" / "files"
    for stale in install_dir.glob("Vocalinux-*.AppImage"):
        if stale.name == asset_name:
            continue
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            stale.replace(trash_dir / stale.name)
            _log(f"moved superseded {stale.name} into the trash")
        except OSError as exc:
            _log(f"cannot move superseded {stale.name} into the trash: {exc}")
    return True, None


def _ensure_input_group(
    cfg: VocalinuxSetupConfig, *, timeout: float
) -> tuple[bool, str | None]:
    """Add the desktop user to the input group; (changed, error).

    The group owns /dev/input and /dev/uinput, so without it the app-level
    hotkey listener cannot read the keyboard devices. The membership takes
    effect at the next login.
    """

    result = run_command(
        ["id", "-nG", cfg.username],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if cfg.input_group in result.stdout.split():
        return False, None
    try:
        run_command(["usermod", "-aG", cfg.input_group, cfg.username], timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot add {cfg.username} to {cfg.input_group}: {exc}"
    return True, None


def _enable_user_service(
    cfg: VocalinuxSetupConfig, *, timeout: float
) -> tuple[bool, str | None]:
    """Enable and start the ydotool user unit; (changed, error).

    The unit runs as the desktop user through its user manager, so the
    input-injection daemon is available to the app without touching the
    greeter session (the app warns against a global enable). A desktop
    session that cannot be reached disables the start: the unit then runs
    at the next login.
    """

    prefix = ["systemctl", "--user", "--machine", f"{cfg.username}@.host"]
    active = run_command(
        [*prefix, "is-active", cfg.service_unit_name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if active.returncode == 0 and trim_whitespace(active.stdout) == "active":
        return False, None
    try:
        run_command(
            [*prefix, "enable", "--now", cfg.service_unit_name],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot enable {cfg.service_unit_name}: {exc}"
    return True, None


def _install_packages(ctx: Context) -> tuple[bool, bool]:
    """Install the missing app packages; return (all_ok, any_installed).

    The shared helper refreshes the apt index once unless the run asked to
    skip it, then installs each missing package individually with retries.
    A package that still fails is an error: without the injection tools the
    app cannot type on Wayland.
    """

    cfg = ctx.config.vocalinux_setup
    timeout = ctx.config.engine.command_timeout_seconds
    missing = [
        package
        for package in cfg.packages
        if not package_is_installed(package, cfg.package_status_timeout_seconds)
    ]
    if not missing:
        return True, False
    _log(f"installing: {', '.join(missing)}")
    installed, failures, _ = install_packages(
        missing,
        install_timeout=timeout,
        update_timeout=timeout,
        retries=cfg.package_install_retries,
        skip_update=ctx.skip_apt_update,
    )
    ok = not failures
    return ok, bool(installed)


def task(ctx: Context) -> TaskResult:
    """Install Vocalinux and its user and system pieces.

    The goal is reached when the pinned AppImage, the app config, the
    autostart entry, the Meta+S consuming shortcut, the input group
    membership and the ydotool user unit are all in place; the task then
    returns changed=False. Otherwise it installs the packages and the
    AppImage, adds the user to the input group, enables ydotool, writes the
    user files and reports what it did. A failed package or AppImage
    install is an error; a step a rerun can redo is a warning. The user
    must log out and back in once for the input group and the Meta+S
    shortcut to take effect, which the message states.
    """

    cfg = ctx.config.vocalinux_setup
    timeout = ctx.config.engine.command_timeout_seconds
    engine = ctx.config.engine
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    packages_ok, installed_any = _install_packages(ctx)
    if installed_any:
        changed = True
    if not packages_ok:
        return TaskResult(
            success=False,
            changed=changed,
            error=f"failed to install required packages for {cfg.packages}",
        )
    if installed_any:
        messages.append("installed the required packages")

    arch = dpkg_architecture(timeout)
    asset_arch = release_asset_architecture(
        engine.release_asset_architectures, arch
    )
    asset_name = _asset_name(cfg, asset_arch)
    appimage_path = _appimage_install_path(cfg, asset_name)

    appimage_changed, appimage_error = _install_appimage(
        cfg,
        engine,
        timeout=timeout,
        force=force,
    )
    if appimage_error:
        return TaskResult(
            success=False,
            changed=changed,
            error=appimage_error,
        )
    if appimage_changed:
        changed = True
        messages.append(f"installed Vocalinux {cfg.version} to {appimage_path}")
    else:
        messages.append(f"Vocalinux {cfg.version} already installed")

    group_changed, group_error = _ensure_input_group(cfg, timeout=timeout)
    if group_error:
        warnings.append(group_error)
    if group_changed:
        changed = True
        messages.append(f"added {cfg.username} to the {cfg.input_group} group")

    service_changed, service_error = _enable_user_service(cfg, timeout=timeout)
    if service_error:
        warnings.append(service_error)
    if service_changed:
        changed = True
        messages.append(f"enabled the {cfg.service_unit_name} user unit")

    template_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    app_config_template, app_config_error = _read_task_template(
        template_dir, cfg.app_config_template_file_name, "app config template"
    )
    if app_config_error is not None:
        return TaskResult(
            success=False, changed=changed, error=app_config_error
        )
    autostart_template, autostart_error = _read_task_template(
        template_dir, cfg.autostart_template_file_name, "autostart template"
    )
    if autostart_error is not None:
        return TaskResult(
            success=False, changed=changed, error=autostart_error
        )
    echo_desktop_template, echo_desktop_error = _read_task_template(
        template_dir,
        cfg.echo_desktop_template_file_name,
        "empty-action desktop template",
    )
    if echo_desktop_error is not None:
        return TaskResult(
            success=False, changed=changed, error=echo_desktop_error
        )
    assert app_config_template is not None
    assert autostart_template is not None
    assert echo_desktop_template is not None
    app_config_path = Path(cfg.home_dir) / cfg.app_config_relative_path
    autostart_path = Path(cfg.home_dir) / cfg.autostart_relative_path

    config_changed = _write_user_file(
        cfg,
        cfg.app_config_relative_path,
        app_config_template,
        file_mode=cfg.user_file_mode,
        timeout=timeout,
        force=force,
    )
    if config_changed:
        changed = True
        messages.append(f"wrote the app config to {app_config_path}")

    autostart_changed = _write_user_file(
        cfg,
        cfg.autostart_relative_path,
        _autostart_content(autostart_template, appimage_path),
        file_mode=cfg.user_file_mode,
        timeout=timeout,
        force=force,
    )
    if autostart_changed:
        changed = True
        messages.append(f"wrote the autostart entry to {autostart_path}")

    echo_changed = _write_user_file(
        cfg,
        cfg.echo_desktop_relative_path,
        echo_desktop_template,
        file_mode=cfg.user_file_mode,
        timeout=timeout,
        force=force,
    )
    if echo_changed:
        changed = True
        messages.append("wrote the empty Meta+S action desktop file")

    shortcut_changed = _sync_echo_shortcut(cfg, timeout=timeout, force=force)
    if shortcut_changed:
        changed = True
        messages.append("registered the Meta+S consuming KDE shortcut")

    if not messages:
        messages.append("Vocalinux already set up")
    messages.append(
        "log out and back in once so the input group and the Meta+S "
        "shortcut apply; the first dictation then offers to download the "
        "small model"
    )
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
