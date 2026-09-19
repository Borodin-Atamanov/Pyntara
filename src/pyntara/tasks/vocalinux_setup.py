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

import errno
import filecmp
import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import install_missing_packages
from pyntara.utils import (
    download_command,
    dpkg_architecture,
    release_asset_architecture,
    run_command,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import vocalinux_setup as values


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


def _as_user_command(command: list[str]) -> list[str]:
    """Prefix a command with the wrapper of the target user.

    The wrapper is a value of this section, so a machine whose desktop user
    is reached another way is a values change.
    """

    return [
        *substituted_command(
            values.RUNUSER_COMMAND,
            {"username": common_values.DESKTOP_USERNAME},
        ),
        *command,
    ]


def _home_env() -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": common_values.DESKTOP_HOME_DIR}


def _release_download_url(repo: str, version: str, asset_name: str) -> str:
    """The download url of a pinned release asset.

    The repository pair, the pinned version and the asset name are
    substituted into the declared template, so the host is a declared value
    and a mirror works without touching the code.
    """

    return engine_values.GITHUB_RELEASE_DOWNLOAD_URL.format(
        repo=repo, version=version, asset_name=asset_name
    )


def _asset_name(asset_arch: str) -> str:
    """The asset file name of a Vocalinux release for one arch."""

    return values.ASSET_NAME_TEMPLATE.format(
        version=values.VERSION, asset_arch=asset_arch
    )


def _appimage_install_path(asset_name: str) -> Path:
    """The install path of the pinned AppImage under the user home."""

    return (
        Path(common_values.DESKTOP_HOME_DIR)
        / values.APPIMAGE_DIR_RELATIVE_PATH
        / asset_name
    )


def _write_user_file(
    rel_path: str,
    content: str,
    *,
    file_mode: int,
    timeout: float,
    force: bool,
) -> tuple[bool, str | None]:
    """Write one user-owned file as the target user; (changed, error).

    The parent directory is created as the target user, the content is
    written by the root process and then chowned and chmodded to the target
    user, so the file keeps the user ownership a desktop config file needs.
    A file that already holds the content is skipped. file_mode is the
    configured mode, applied in its octal form. A step that fails is
    reported with the path of the file and skips that file alone, so the
    remaining files and the steps after them still run.
    """

    target = Path(common_values.DESKTOP_HOME_DIR) / rel_path
    if not force and target.is_file():
        try:
            if target.read_text(encoding="utf-8") == content:
                return False, None
        except OSError:
            pass
    try:
        run_command(
            _as_user_command(
                substituted_command(values.MKDIR_COMMAND, {"path": str(target.parent)}),
            ),
            extra_env=_home_env(),
            timeout=timeout,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run_command(
            substituted_command(
                values.CHOWN_COMMAND,
                {
                    "owner": (
                        f"{common_values.DESKTOP_USERNAME}:"
                        f"{common_values.DESKTOP_USERNAME}"
                    ),
                    "path": str(target),
                },
            ),
            timeout=timeout,
        )
        run_command(
            substituted_command(
                values.CHMOD_COMMAND,
                {"file_mode": f"{file_mode:o}", "path": str(target)},
            ),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot write {target}: {exc}"
    _log(f"wrote {target}")
    return True, None


def _kconfig_command(
    base_command: tuple[str, ...],
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the base, the groups and the key.

    The base call carries the shortcut file name and every selector is a value
    of this section, so another KConfig version or another tool is a values
    change. The reader and the writer share this builder, so the two calls can
    never drift apart.
    """

    command = substituted_command(
        base_command, {"file_name": common_values.SHORTCUTS_FILE_NAME}
    )
    for segment in group_segments:
        command.extend(
            substituted_command(values.CONFIG_GROUP_FLAG, {"group": segment})
        )
    command.extend(substituted_command(values.CONFIG_KEY_FLAG, {"key": key}))
    return command


def _kreadconfig(
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one KConfig key, or an empty string when unset."""

    command = _kconfig_command(values.KREADCONFIG_COMMAND, group_segments, key)
    result = run_command(
        _as_user_command(command),
        extra_env=_home_env(),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _kwriteconfig(
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
) -> None:
    """Write one KConfig key with the writer of the section as the user."""

    command = _kconfig_command(values.KWRITECONFIG_COMMAND, group_segments, key)
    command.append(value)
    run_command(
        _as_user_command(command),
        extra_env=_home_env(),
        timeout=timeout,
    )


def _sync_echo_shortcut(
    *,
    timeout: float,
    force: bool,
) -> tuple[bool, str | None]:
    """Write the Meta+S consuming shortcut; (changed, error).

    The empty .desktop action is registered under the KDE services
    component, so Plasma owns Meta+S and the S never reaches the focused
    field, while the Vocalinux app-level listener still sees the raw key
    and toggles. The write applies at the next login. A failure of either
    the read or the write is reported with the component and the action
    instead of stopping the task, because every other step is already
    done.
    """

    group = (values.SHORTCUT_GROUP_NAME, values.SHORTCUT_ENTRY_NAME)
    try:
        current = _kreadconfig(group, values.SHORTCUT_ACTION_NAME, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot read the {values.SHORTCUT_ACTION_NAME} shortcut: {exc}"
    if not force and current == values.SHORTCUT_KEY_SEQUENCE:
        return False, None
    try:
        _kwriteconfig(
            group,
            values.SHORTCUT_ACTION_NAME,
            values.SHORTCUT_KEY_SEQUENCE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot write the {values.SHORTCUT_ACTION_NAME} shortcut: {exc}"
    _log(
        f"set {common_values.SHORTCUTS_FILE_NAME} "
        f"{values.SHORTCUT_ACTION_NAME}: {values.SHORTCUT_KEY_SEQUENCE}"
    )
    return True, None


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
    *,
    timeout: float,
    force: bool,
) -> tuple[bool, str | None]:
    """Install the pinned AppImage under the user home; (changed, error).

    The asset arch follows the dpkg architecture of the machine. The
    release asset is trusted as is and its checksum is not verified: the
    source is the official GitHub release of the pinned version. The file
    is downloaded into the root DOWNLOAD_DIR cache once and copied into
    the user home, so a rerun with a present install file changes nothing
    and never downloads again. An installed file whose bytes already equal
    the cached release is left where it is, even in force mode, because a
    rewrite of the same bytes changes nothing while a running app refuses
    it with a busy error; the ownership and the mode are applied either
    way. A superseded install of another version is
    moved into the user trash, never deleted.
    """

    arch = dpkg_architecture(timeout)
    asset_arch = release_asset_architecture(
        engine_values.RELEASE_ASSET_ARCHITECTURES, arch
    )
    asset_name = _asset_name(asset_arch)
    url = _release_download_url(values.GITHUB_REPO, values.VERSION, asset_name)
    install_dir = (
        Path(common_values.DESKTOP_HOME_DIR) / values.APPIMAGE_DIR_RELATIVE_PATH
    )
    target = install_dir / asset_name
    cache = values.DOWNLOAD_DIR / asset_name
    if target.is_file() and not force:
        return False, None
    run_command(
        _as_user_command(
            substituted_command(values.MKDIR_COMMAND, {"path": str(install_dir)}),
        ),
        extra_env=_home_env(),
        timeout=timeout,
    )
    install_dir.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        partial = values.DOWNLOAD_DIR / (
            asset_name + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX
        )
        try:
            run_command(
                download_command(partial, url),
                timeout=timeout,
            )
            partial.replace(cache)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            partial.unlink(missing_ok=True)
            return False, f"cannot download {url}: {exc}"
    rewritten = not (target.is_file() and filecmp.cmp(cache, target, shallow=False))
    if not rewritten:
        _log(f"the installed image already matches {cache.name}")
    try:
        if rewritten:
            shutil.copyfile(cache, target)
    except OSError as exc:
        if exc.errno == errno.ETXTBSY:
            return False, (
                f"cannot replace {target} while Vocalinux runs: the installed "
                "image is left as it is, close the app and rerun the task"
            )
        return False, f"cannot install {target}: {exc}"
    run_command(
        substituted_command(
            values.CHOWN_COMMAND,
            {
                "owner": (
                    f"{common_values.DESKTOP_USERNAME}:{common_values.DESKTOP_USERNAME}"
                ),
                "path": str(target),
            },
        ),
        timeout=timeout,
    )
    run_command(
        substituted_command(
            values.CHMOD_COMMAND,
            {
                "file_mode": f"{common_values.EXECUTABLE_FILE_MODE:o}",
                "path": str(target),
            },
        ),
        timeout=timeout,
    )
    trash_dir = (
        Path(common_values.DESKTOP_HOME_DIR) / ".local" / "share" / "Trash" / "files"
    )
    for stale in install_dir.glob("Vocalinux-*.AppImage"):
        if stale.name == asset_name:
            continue
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            stale.replace(trash_dir / stale.name)
            _log(f"moved superseded {stale.name} into the trash")
        except OSError as exc:
            _log(f"cannot move superseded {stale.name} into the trash: {exc}")
        else:
            rewritten = True
    return rewritten, None


def _ensure_input_group(*, timeout: float) -> tuple[bool, str | None]:
    """Add the desktop user to the input group; (changed, error).

    The group owns /dev/input and /dev/uinput, so without it the app-level
    hotkey listener cannot read the keyboard devices. The membership takes
    effect at the next login.
    """

    username = common_values.DESKTOP_USERNAME
    result = run_command(
        substituted_command(values.GROUP_MEMBERS_COMMAND, {"username": username}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if values.INPUT_GROUP in result.stdout.split():
        return False, None
    try:
        run_command(
            substituted_command(
                values.GROUP_ADD_COMMAND,
                {"input_group": values.INPUT_GROUP, "username": username},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot add {username} to {values.INPUT_GROUP}: {exc}"
    return True, None


def _enable_user_service(*, timeout: float) -> tuple[bool, str | None]:
    """Enable and start the ydotool user unit; (changed, error).

    The unit runs as the desktop user through its user manager, so the
    input-injection daemon is available to the app without touching the
    greeter session (the app warns against a global enable). A desktop
    session that cannot be reached disables the start: the unit then runs
    at the next login.
    """

    username = common_values.DESKTOP_USERNAME
    active = run_command(
        substituted_command(
            values.SERVICE_ACTIVE_COMMAND,
            {
                "username": username,
                "service_unit_name": values.SERVICE_UNIT_NAME,
            },
        ),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if (
        active.returncode == 0
        and trim_whitespace(active.stdout) == values.SERVICE_ACTIVE_STATE
    ):
        return False, None
    try:
        run_command(
            substituted_command(
                values.SERVICE_ENABLE_COMMAND,
                {
                    "username": username,
                    "service_unit_name": values.SERVICE_UNIT_NAME,
                },
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot enable {values.SERVICE_UNIT_NAME}: {exc}"
    return True, None


def _install_packages(ctx: Context) -> tuple[bool, bool]:
    """Install the missing app packages; return (all_ok, any_installed).

    The shared install path refreshes the apt index once unless the run asked
    to skip it, then installs each missing package individually with retries.
    A package that still fails is an error: without the injection tools the
    app cannot type on Wayland.
    """

    _, installed, failures, _ = install_missing_packages(ctx, values.PACKAGES)
    return not failures, bool(installed)


def task(ctx: Context) -> TaskResult:
    """Install Vocalinux and its user and system pieces.

    The goal is reached when the pinned AppImage, the app config, the
    autostart entry, the Meta+S consuming shortcut, the input group
    membership and the ydotool user unit are all in place; the task then
    returns changed=False. Otherwise it installs the packages and the
    AppImage, adds the user to the input group, enables ydotool, writes the
    user files and reports what it did. A step that cannot run is a warning
    of a completed task and the missing mechanism skips that step alone: a
    failed package install leaves the AppImage and the user files, a failed
    AppImage install skips the autostart entry that would point at a
    missing file, and a missing template skips the file it renders. The
    user must log out and back in once for the input group and the Meta+S
    shortcut to take effect, which the message states.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
        return TaskResult(
            success=True,
            message="the vocalinux_setup values are not declared, nothing was changed",
            warnings=(
                "the vocalinux_setup values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    packages_ok, installed_any = _install_packages(ctx)
    if installed_any:
        changed = True
    if not packages_ok:
        # The AppImage is self-contained, so the deployment of the user
        # files still runs and the missing packages are reported.
        warnings.append(f"failed to install required packages for {values.PACKAGES}")
    if installed_any:
        messages.append("installed the required packages")

    arch = dpkg_architecture(timeout)
    asset_arch = release_asset_architecture(
        engine_values.RELEASE_ASSET_ARCHITECTURES, arch
    )
    asset_name = _asset_name(asset_arch)
    appimage_path = _appimage_install_path(asset_name)

    appimage_changed, appimage_error = _install_appimage(
        timeout=timeout,
        force=force,
    )
    if appimage_error:
        # Without the AppImage the autostart entry would point at a file
        # that does not exist, so that entry alone is skipped.
        warnings.append(appimage_error)
    if appimage_changed:
        changed = True
        messages.append(f"installed Vocalinux {values.VERSION} to {appimage_path}")
    elif appimage_error is None:
        messages.append(f"Vocalinux {values.VERSION} already installed")

    group_changed, group_error = _ensure_input_group(timeout=timeout)
    if group_error:
        warnings.append(group_error)
    if group_changed:
        changed = True
        messages.append(
            f"added {common_values.DESKTOP_USERNAME} to the {values.INPUT_GROUP} group"
        )

    service_changed, service_error = _enable_user_service(timeout=timeout)
    if service_error:
        warnings.append(service_error)
    if service_changed:
        changed = True
        messages.append(f"enabled the {values.SERVICE_UNIT_NAME} user unit")

    template_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    app_config_template, app_config_error = _read_task_template(
        template_dir, values.APP_CONFIG_TEMPLATE_FILE_NAME, "app config template"
    )
    if app_config_error is not None:
        warnings.append(app_config_error)
    autostart_template, autostart_error = _read_task_template(
        template_dir, values.AUTOSTART_TEMPLATE_FILE_NAME, "autostart template"
    )
    if autostart_error is not None:
        warnings.append(autostart_error)
    echo_desktop_template, echo_desktop_error = _read_task_template(
        template_dir,
        values.ECHO_DESKTOP_TEMPLATE_FILE_NAME,
        "empty-action desktop template",
    )
    if echo_desktop_error is not None:
        warnings.append(echo_desktop_error)
    home_dir = Path(common_values.DESKTOP_HOME_DIR)
    app_config_path = home_dir / values.APP_CONFIG_RELATIVE_PATH
    autostart_path = home_dir / values.AUTOSTART_RELATIVE_PATH

    if app_config_template is not None:
        config_changed, config_error = _write_user_file(
            values.APP_CONFIG_RELATIVE_PATH,
            app_config_template,
            file_mode=common_values.LAUNCHER_FILE_MODE,
            timeout=timeout,
            force=force,
        )
        if config_error:
            warnings.append(config_error)
        if config_changed:
            changed = True
            messages.append(f"wrote the app config to {app_config_path}")

    if autostart_template is not None and appimage_path.is_file():
        autostart_changed, autostart_error = _write_user_file(
            values.AUTOSTART_RELATIVE_PATH,
            _autostart_content(autostart_template, appimage_path),
            file_mode=common_values.LAUNCHER_FILE_MODE,
            timeout=timeout,
            force=force,
        )
        if autostart_error:
            warnings.append(autostart_error)
        if autostart_changed:
            changed = True
            messages.append(f"wrote the autostart entry to {autostart_path}")

    if echo_desktop_template is not None:
        echo_changed, echo_error = _write_user_file(
            values.ECHO_DESKTOP_RELATIVE_PATH,
            echo_desktop_template,
            file_mode=common_values.LAUNCHER_FILE_MODE,
            timeout=timeout,
            force=force,
        )
        if echo_error:
            warnings.append(echo_error)
        if echo_changed:
            changed = True
            messages.append("wrote the empty Meta+S action desktop file")

    shortcut_changed, shortcut_error = _sync_echo_shortcut(timeout=timeout, force=force)
    if shortcut_error:
        warnings.append(shortcut_error)
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
