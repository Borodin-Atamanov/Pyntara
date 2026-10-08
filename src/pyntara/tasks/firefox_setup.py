"""Task firefox_setup: install Firefox without the snap and apply the defaults.

The described goal is a Firefox installed from the official Mozilla apt
repository in place of the snap, carrying the browser defaults of the
firefox-default-settings repository, set as the default browser of the desktop
user and pinned to the Plasma taskbar. The task registers the Mozilla apt source
(an armored keyring, a deb822 source and the apt preferences file that keeps the
Ubuntu transitional package from winning), removes the snap version when it is
present, installs the package with --allow-downgrades when it is missing or in
force mode, clones or updates the defaults repository into the root cache,
deploys its system/ tree (the machine policy and the AutoConfig entry point with
its defaults file) under the configured system root, sets the packaged entry as
the default browser of the desktop user and pins the Firefox launcher to every
Plasma taskbar. The browser is not started through the local proxy, so the task
takes no dependency on three_x_ui_xray_setup.

The defaults repository is the single source of the browser defaults: the
machine policy sets the default search engine and installs the chosen
extensions, and the AutoConfig file sets interface defaults with defaultPref, so
every one of them stays changeable by the user (docs/spec/firefox-setup.md).

Force mode reinstalls the package and rewrites the deployed files regardless of
the current bytes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara import apt_repository, kconfig, plasma_panel, settings_repo
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_refreshing_index,
    process_is_running,
    run_command,
    substituted_command,
    task_data_dir,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import firefox_setup as values
from pyntara.values import missing_value_names


def _ensure_repository(
    apt_source_template_path: Path,
    apt_preferences_template_path: Path,
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, str | None]:
    """Register the Mozilla apt source, keyring and preferences; (changed, error).

    The armored key is downloaded when missing and written into the keyring path
    as downloaded, because it is already in the armored form apt accepts. The
    deb822 source is rendered from its template and the apt preferences file is
    copied from its template; both are root-owned and written only when their
    content differs.
    """

    changed = False
    try:
        if not (
            values.KEYRING_PATH.is_file() and values.KEYRING_PATH.stat().st_size > 0
        ):
            apt_repository.download_keyring(
                url=values.MOZILLA_KEY_URL,
                keyring_path=values.KEYRING_PATH,
                temp_dir_prefix=values.KEYRING_TEMP_DIR_PREFIX,
                armored_file_name=values.KEYRING_ARMORED_FILE_NAME,
                timeout=timeout,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            changed = True
        source_text = apt_repository.render_source_text(
            apt_source_template_path, values.KEYRING_PATH
        )
        preferences_text = apt_preferences_template_path.read_text(encoding="utf-8")
        for path, content in (
            (values.APT_SOURCE_PATH, source_text),
            (values.APT_PREFERENCES_PATH, preferences_text),
        ):
            changed |= apt_repository.write_root_file(
                path, content, owner_uid=owner_uid, owner_gid=owner_gid
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return changed, f"cannot register the Mozilla apt repository: {exc}"
    return changed, None


def _remove_snap_firefox(timeout: float) -> tuple[bool, str | None]:
    """Remove the snap version of the browser; (changed, warning).

    The presence of the snap is asked first, because snap remove answers the
    success code even for a snap that is not installed (measured on liveusb_test
    on 2026-10-08: the message `snap "firefox" is not installed` with exit code
    0), so that exit code alone cannot tell a removal from a no-op. A machine
    without the snap is left alone; a snap that is present and cannot be removed
    is a warning, and the deb install is unaffected.
    """

    present = run_command(
        substituted_command(values.SNAP_LIST_COMMAND, {"snap": values.SNAP_NAME}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if present.returncode != 0:
        return False, None
    result = run_command(
        substituted_command(values.SNAP_REMOVE_COMMAND, {"snap": values.SNAP_NAME}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        return True, None
    answer = f"{result.stdout}\n{result.stderr}".lower()
    if "not installed" in answer or "no matching snaps" in answer:
        return False, None
    return False, f"cannot remove the snap {values.SNAP_NAME}: {answer.strip()}"


def _ensure_firefox_installed(
    *, force: bool, skip_apt_update: bool, timeout: float
) -> tuple[bool, str | None]:
    """Install the browser when the real build is missing; (changed, note).

    The check is the binary of the Mozilla build and not the package alone,
    because the Ubuntu archive ships the transitional package firefox, whose
    presence installs the snap and provides no browser: a machine that carries
    the transitional package alone is installed over. The Mozilla package
    carries no epoch while the transitional one carries the epoch 1, so the
    install runs with --allow-downgrades. The note names a machine that still
    has no browser after the install, so the run never reports a browser it did
    not get.
    """

    if not force and values.BROWSER_BINARY_PATH.is_file():
        return False, None
    installed, note = install_package_refreshing_index(
        values.PACKAGE_NAME,
        install_command=values.APT_INSTALL_COMMAND,
        skip_apt_update=skip_apt_update,
        timeout=timeout,
    )
    if not installed:
        return False, note
    if not values.BROWSER_BINARY_PATH.is_file():
        return True, (
            f"{values.PACKAGE_NAME} was installed but "
            f"{values.BROWSER_BINARY_PATH} is missing; the machine has no "
            "working browser"
        )
    return True, None

def _browser_is_running(timeout: float) -> bool:
    """True when a Firefox main process is running."""

    return process_is_running(values.PROCESS_NAME, timeout)


def _set_default_browser(*, timeout: float) -> tuple[bool, str | None]:
    """Make the packaged Firefox entry the default browser; (changed, warning).

    The entries go into the mimeapps.list of the desktop user, which is the file
    the desktop reads, written with the KConfig writer of the section. The
    xdg-settings tool is unusable here: on Kubuntu 26.04 it takes a KDE branch
    that calls qtpaths, which is not installed (only qtpaths6 is), and fails
    (measured on liveusb_test on 2026-10-08). Every key is read first, so a
    machine that already points at the entry is left alone.
    """

    changed = False
    for key in values.DEFAULT_BROWSER_MIME_KEYS:
        try:
            current = kconfig.read_config_value(
                values.MIMEAPPS_FILE_NAME,
                values.DEFAULT_BROWSER_GROUP,
                key,
                timeout=timeout,
            )
            if current == values.DESKTOP_FILE_NAME:
                continue
            kconfig.write_config_value(
                values.MIMEAPPS_FILE_NAME,
                values.DEFAULT_BROWSER_GROUP,
                key,
                values.DESKTOP_FILE_NAME,
                timeout=timeout,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            return changed, f"cannot set Firefox as the default browser: {exc}"
        changed = True
    return changed, None


def task(ctx: Context) -> TaskResult:
    """Install Firefox in place of the snap and apply the browser defaults.

    The target state is reached when the Mozilla apt repository is registered,
    the snap is absent, the package is installed, the defaults repository is
    current, its system/ tree is deployed, Firefox is the default browser and the
    launcher sits in the taskbar launchers; the task then returns changed=False.
    Force mode reinstalls the package and rewrites the deployed files.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        return TaskResult(
            success=True,
            message="the firefox_setup values are not declared, nothing was changed",
            warnings=(
                "the firefox_setup values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []
    template_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    apt_source_template_path = template_dir / values.APT_SOURCE_TEMPLATE_FILE_NAME
    apt_preferences_template_path = (
        template_dir / values.APT_PREFERENCES_TEMPLATE_FILE_NAME
    )

    if apt_source_template_path.is_file() and apt_preferences_template_path.is_file():
        _log("registering the Mozilla apt repository")
        repo_changed, error = _ensure_repository(
            apt_source_template_path,
            apt_preferences_template_path,
            timeout,
            owner_uid,
            owner_gid,
        )
        if error:
            warnings.append(error)
        elif repo_changed:
            messages.append("registered the Mozilla apt repository")
            changed = True
    else:
        warnings.append(f"missing apt source template: {apt_source_template_path}")

    _log("checking the Firefox installation")
    install_changed, install_note = _ensure_firefox_installed(
        force=force,
        skip_apt_update=ctx.skip_apt_update,
        timeout=timeout,
    )
    if install_note:
        warnings.append(install_note)
    if install_changed:
        messages.append(f"installed {values.PACKAGE_NAME}")
        changed = True

    _log("removing the snap version of Firefox when present")
    snap_changed, snap_warning = _remove_snap_firefox(timeout)
    if snap_warning:
        warnings.append(snap_warning)
    elif snap_changed:
        messages.append("removed the snap version of Firefox")
        changed = True

    _log("updating the Firefox defaults repository")
    sync_changed, error = settings_repo.sync_repository(
        url=values.SETTINGS_REPO_URL,
        directory=values.SETTINGS_DIR,
        timeout=timeout,
    )
    if error:
        warnings.append(error)
    elif sync_changed:
        messages.append("updated the Firefox defaults repository")
        changed = True

    tree_changed, tree_warnings = settings_repo.deploy_system_tree(
        values.SETTINGS_DIR, force=force, owner_uid=owner_uid, owner_gid=owner_gid
    )
    warnings.extend(tree_warnings)
    if tree_changed:
        messages.append(
            f"deployed Firefox defaults to {common_values.SYSTEM_ROOT}"
        )
        changed = True

    _log("setting Firefox as the default browser")
    browser_changed, browser_warning = _set_default_browser(timeout=timeout)
    if browser_warning:
        warnings.append(browser_warning)
    elif browser_changed:
        messages.append("set Firefox as the default browser")
        changed = True

    _log("pinning the Firefox launcher to the Plasma taskbar")
    pin_changed, pin_note = plasma_panel.pin_launcher(
        values.PANEL_LAUNCHER_ID, timeout=timeout
    )
    if pin_note:
        warnings.append(pin_note)
    if pin_changed:
        messages.append("pinned the Firefox launcher to the Plasma taskbar")
        changed = True

    if not messages:
        messages.append("Firefox already set up")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
