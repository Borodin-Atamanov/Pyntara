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

import json
import subprocess
import tempfile
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    download_command,
    refresh_apt_index,
    run_command,
    session_environment,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import firefox_setup as values
from pyntara.values import kde_settings as shell_values
from pyntara.values import missing_value_names


def _source_text(template_path: Path, keyring_path: Path) -> str:
    """The deb822 apt source of the official Mozilla repository.

    The body of the source file lives in the template under task_data/ and only
    the keyring path is substituted, so the suite, the components and the
    archive address stay with the template.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(keyring_path=str(keyring_path))


def _ensure_repository(
    apt_source_template_path: Path,
    apt_preferences_template_path: Path,
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, str | None]:
    """Register the Mozilla apt source, keyring and preferences; (changed, error).

    The armored key is downloaded when missing and written to the configured
    keyring path as downloaded, because it is already in the armored form apt
    accepts. The deb822 source is rendered from its template and the apt
    preferences file is copied from its template; both are written only when
    their content differs, root-owned with the configured mode.
    """

    changed = False
    try:
        if not (
            values.KEYRING_PATH.is_file() and values.KEYRING_PATH.stat().st_size > 0
        ):
            values.KEYRING_PATH.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=values.KEYRING_TEMP_DIR_PREFIX
            ) as tmp:
                downloaded = Path(tmp) / "packages.mozilla.org.asc"
                run_command(
                    download_command(downloaded, values.MOZILLA_KEY_URL),
                    timeout=timeout,
                )
                values.KEYRING_PATH.write_bytes(downloaded.read_bytes())
            values.KEYRING_PATH.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(values.KEYRING_PATH, owner_uid, owner_gid)
            changed = True
        source_text = _source_text(apt_source_template_path, values.KEYRING_PATH)
        preferences_text = apt_preferences_template_path.read_text(encoding="utf-8")
        for path, content in (
            (values.APT_SOURCE_PATH, source_text),
            (values.APT_PREFERENCES_PATH, preferences_text),
        ):
            if path.is_file() and path.read_text(encoding="utf-8") == content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            path.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(path, owner_uid, owner_gid)
            changed = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return changed, f"cannot register the Mozilla apt repository: {exc}"
    return changed, None


def _remove_snap_firefox(timeout: float) -> tuple[bool, str | None]:
    """Remove the snap version of the browser; (changed, warning).

    A machine that never had the snap answers an error that names no installed
    snap, which is the normal state of a fresh machine and not a failure, so the
    error of an absent snap is swallowed. Any other failure is a warning: the
    deb install still works and the leftover snap is reported.
    """

    result = run_command(
        substituted_command(values.SNAP_REMOVE_COMMAND, {"snap": values.SNAP_NAME}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        return True, None
    answer = f"{result.stdout}\n{result.stderr}".lower()
    if "no matching snaps" in answer or "not installed" in answer:
        return False, None
    return False, f"cannot remove the snap {values.SNAP_NAME}: {answer.strip()}"


def _ensure_firefox_installed(
    *, force: bool, skip_apt_update: bool, timeout: float
) -> tuple[bool, str | None]:
    """Install the browser when the real build is missing; (changed, note).

    The check is the binary of the Mozilla build and not the package alone,
    because the Ubuntu archive ships the transitional package firefox, whose
    presence installs the snap and provides no browser: a machine that carries
    the transitional package alone is installed over. The note names a machine
    that still has no browser after the install, so the run never reports a
    browser it did not get.
    """

    if not force and values.BROWSER_BINARY_PATH.is_file():
        return False, None
    try:
        if not skip_apt_update:
            refresh_apt_index(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot refresh the apt index: {exc}"
    try:
        run_command(
            substituted_command(
                values.APT_INSTALL_COMMAND, {"package": values.PACKAGE_NAME}
            ),
            extra_env=dict(engine_values.APT_NONINTERACTIVE_ENVIRONMENT),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot install {values.PACKAGE_NAME}: {exc}"
    if not values.BROWSER_BINARY_PATH.is_file():
        return True, (
            f"{values.PACKAGE_NAME} was installed but "
            f"{values.BROWSER_BINARY_PATH} is missing; the machine has no "
            "working browser"
        )
    return True, None


def _sync_settings_repo(*, timeout: float) -> tuple[bool, str | None]:
    """Clone or update the defaults repository; (changed, error).

    The clone, the fetch and the two revision queries come from the values as
    command templates, so the flags of the version control tool are values and
    not code.
    """

    placeholders = {
        "url": values.SETTINGS_REPO_URL,
        "ref": values.SETTINGS_REPO_REF,
        "dir": str(values.SETTINGS_DIR),
    }
    try:
        if not (values.SETTINGS_DIR / ".git").is_dir():
            values.SETTINGS_DIR.parent.mkdir(parents=True, exist_ok=True)
            run_command(
                substituted_command(values.SETTINGS_CLONE_COMMAND, placeholders),
                timeout=timeout,
            )
            return True, None
        run_command(
            substituted_command(values.SETTINGS_FETCH_COMMAND, placeholders),
            timeout=timeout,
        )
        head = run_command(
            substituted_command(
                values.SETTINGS_REVISION_COMMAND,
                {**placeholders, "revision": "HEAD"},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        fetched = run_command(
            substituted_command(
                values.SETTINGS_REVISION_COMMAND,
                {**placeholders, "revision": "FETCH_HEAD"},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        if (
            head.returncode == 0
            and fetched.returncode == 0
            and head.stdout.strip() == fetched.stdout.strip()
        ):
            return False, None
        run_command(
            substituted_command(
                values.SETTINGS_RESET_COMMAND,
                {**placeholders, "revision": "FETCH_HEAD"},
            ),
            timeout=timeout,
        )
        return True, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot update the defaults repository: {exc}"


def _deploy_tree(
    source_root: Path,
    target_root: Path,
    *,
    force: bool,
    skip_relative_paths: tuple[str, ...] = (),
    owner_ids: tuple[int, int] | None = None,
) -> tuple[bool, list[str]]:
    """Copy one tree under another path; (changed, warnings).

    Every file lands under target_root with its relative path preserved,
    carrying the mode of every deployed file, and is written only when its bytes
    differ (or in force mode). A relative path that equals a skipped path or
    stands below it is left out. The owner pair is applied to every written file
    when it is given. A per-file failure is a warning, never a fatal error.
    """

    skipped = [Path(name) for name in skip_relative_paths]
    changed = False
    warnings: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if any(relative == name or name in relative.parents for name in skipped):
            continue
        target = target_root / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if (
                not force
                and target.is_file()
                and target.read_bytes() == path.read_bytes()
            ):
                continue
            target.write_bytes(path.read_bytes())
            target.chmod(common_values.LAUNCHER_FILE_MODE)
            if owner_ids is not None:
                apply_owner(target, owner_ids[0], owner_ids[1])
            changed = True
        except OSError as exc:
            warnings.append(f"cannot deploy {relative}: {exc}")
    return changed, warnings


def _deploy_system_tree(
    *,
    force: bool,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, list[str]]:
    """Deploy the repository system/ tree under SYSTEM_ROOT; (changed, warnings).

    The files are root-owned, copied only when the target differs (or in force
    mode); a repository without that tree is a note.
    """

    source_root = values.SETTINGS_DIR / values.SETTINGS_SYSTEM_TREE_RELATIVE_PATH
    if not source_root.is_dir():
        return False, ["the defaults repository carries no system/ tree"]
    return _deploy_tree(
        source_root,
        values.SYSTEM_ROOT,
        force=force,
        owner_ids=(owner_uid, owner_gid),
    )


def _browser_is_running(timeout: float) -> bool:
    """True when a Firefox main process is running."""

    result = run_command(
        substituted_command(
            values.PROCESS_CHECK_COMMAND, {"process_name": values.PROCESS_NAME}
        ),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _as_user_command(command: list[str]) -> list[str]:
    """Prefix a command with the wrapper of the desktop user."""

    return [
        *substituted_command(
            values.RUNUSER_COMMAND,
            {"username": common_values.DESKTOP_USERNAME},
        ),
        *command,
    ]


def _home_env() -> dict[str, str]:
    """Environment that points a command at the home of the desktop user."""

    return {"HOME": common_values.DESKTOP_HOME_DIR}


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
            current = _kreadconfig(
                values.MIMEAPPS_FILE_NAME,
                values.DEFAULT_BROWSER_GROUP,
                key,
                timeout=timeout,
            )
            if current == values.DESKTOP_FILE_NAME:
                continue
            _kwriteconfig(
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


def _kconfig_command(
    base_command: tuple[str, ...],
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the base, the file, the groups and the key."""

    command = substituted_command(base_command, {"file_name": file_name})
    for segment in group_segments:
        command.extend(
            substituted_command(values.CONFIG_GROUP_FLAG, {"group": segment})
        )
    command.extend(substituted_command(values.CONFIG_KEY_FLAG, {"key": key}))
    return command


def _kreadconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    *,
    timeout: float,
) -> str:
    """Current value of one key of a KConfig file of the desktop user."""

    result = run_command(
        _as_user_command(
            _kconfig_command(values.KREADCONFIG_COMMAND, file_name, group_segments, key)
        ),
        extra_env=_home_env(),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _kwriteconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
) -> None:
    """Write one key of a KConfig file of the desktop user."""

    command = _kconfig_command(
        values.KWRITECONFIG_COMMAND, file_name, group_segments, key
    )
    command.append(value)
    run_command(_as_user_command(command), extra_env=_home_env(), timeout=timeout)


def _taskbar_launcher_groups(text: str) -> list[tuple[str, ...]]:
    """The group of every task manager applet that holds pinned launchers."""

    groups: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    plugin_key = "plugin="
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = tuple(part for part in line[1:-1].split("][") if part)
        elif (
            line.startswith(plugin_key)
            and line.removeprefix(plugin_key) in values.TASKBAR_PLUGIN_NAMES
        ):
            groups.append(current + values.APPLETSRC_LAUNCHER_GROUP)
    return groups


def _desktop_session_environment() -> dict[str, str] | None:
    """Environment that reaches the live Plasma session; None when none runs."""

    session = session_environment(
        common_values.DESKTOP_USERNAME,
        command_template=engine_values.SESSION_ENVIRONMENT_COMMAND,
        keys=engine_values.SESSION_ENVIRONMENT_KEYS,
        bus_key=engine_values.SESSION_BUS_KEY,
        display_keys=engine_values.SESSION_DISPLAY_KEYS,
        timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS,
    )
    if not session:
        return None
    env = _home_env()
    env.update(session)
    return env


def _launcher_script() -> str:
    """The Plasma script that gives the launcher to every taskbar of the panel."""

    spec = json.dumps(
        {
            "plugins": list(values.TASKBAR_PLUGIN_NAMES),
            "group": list(values.APPLETSRC_LAUNCHER_GROUP[1:]),
            "key": values.APPLETSRC_LAUNCHERS_KEY,
            "id": values.PANEL_LAUNCHER_ID,
        }
    )
    return (
        f"var spec = {spec};"
        "var reports = [];"
        "var ps = panels();"
        "for (var p = 0; p < ps.length; p++) {"
        " var ws = ps[p].widgets();"
        " for (var w = 0; w < ws.length; w++) {"
        "  var t = String(ws[w].type);"
        "  if (spec.plugins.indexOf(t) < 0) continue;"
        "  ws[w].currentConfigGroup = spec.group;"
        "  var held = String(ws[w].readConfig(spec.key, ''));"
        "  var entries = held.split(',').filter(function (e) { return e !== ''; });"
        "  var action = 'held';"
        "  if (entries.indexOf(spec.id) < 0) {"
        "   entries.push(spec.id);"
        "   ws[w].writeConfig(spec.key, entries);"
        "   action = 'pinned';"
        "  }"
        "  reports.push(t + '|' + action + '|' + entries.join(','));"
        " }"
        "}"
        "print(reports.join(' ;; '));"
    )


def _pin_launcher_in_the_running_panel(
    env: dict[str, str], *, timeout: float
) -> tuple[bool, str | None]:
    """Give the launcher to the running panel; (pinned, warning)."""

    command = _as_user_command(
        substituted_command(
            shell_values.PLASMA_SHELL_SCRIPT_COMMAND,
            {
                "plasma_shell_bus_name": shell_values.PLASMA_SHELL_BUS_NAME,
                "plasma_shell_object_path": shell_values.PLASMA_SHELL_OBJECT_PATH,
                "plasma_shell_script_interface_name": (
                    shell_values.PLASMA_SHELL_SCRIPT_INTERFACE_NAME
                ),
                "plasma_shell_script_method_name": (
                    shell_values.PLASMA_SHELL_SCRIPT_METHOD_NAME
                ),
                "script": _launcher_script(),
            },
        )
    )
    try:
        answer = run_command(command, extra_env=env, timeout=timeout, capture=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot give the launcher to the running panel: {exc}"
    reports = [item for item in trim_whitespace(answer.stdout).split(" ;; ") if item]
    if not reports:
        return False, (
            "the running panel reported no task manager applet; the launcher "
            "appears in the panel at the next login"
        )
    pinned = False
    for report in reports:
        widget_type, _, rest = report.partition("|")
        action, _, held = rest.partition("|")
        if action == "pinned":
            pinned = True
        if values.PANEL_LAUNCHER_ID not in [
            entry for entry in held.split(",") if entry
        ]:
            return pinned, (
                f"the running panel did not take the launcher of {widget_type}; "
                "it appears in the panel at the next login"
            )
    _log("gave the launcher to the running panel")
    return pinned, None


def _pin_launcher_in_the_appletsrc(
    groups: list[tuple[str, ...]], *, timeout: float
) -> tuple[bool, str | None]:
    """Append the launcher to the pinned list of every taskbar; (changed, note)."""

    changed = False
    for group in groups:
        try:
            current = _kreadconfig(
                values.APPLETSRC_FILE_NAME,
                group,
                values.APPLETSRC_LAUNCHERS_KEY,
                timeout=timeout,
            )
            entries = [entry for entry in current.split(",") if entry]
            if values.PANEL_LAUNCHER_ID in entries:
                continue
            _kwriteconfig(
                values.APPLETSRC_FILE_NAME,
                group,
                values.APPLETSRC_LAUNCHERS_KEY,
                ",".join([*entries, values.PANEL_LAUNCHER_ID]),
                timeout=timeout,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            return changed, f"cannot pin the Firefox launcher: {exc}"
        changed = True
    return changed, None


def _pin_firefox_launcher(*, timeout: float) -> tuple[bool, str | None]:
    """Pin the Firefox launcher to the Plasma taskbars; (changed, note).

    A running panel receives the launcher through the scripting interface of the
    shell; without a running session, or when the shell cannot be reached, the
    launcher is written into the group below every task manager applet, where
    the panel reads it at the next login. A missing appletsrc is a note and a pin
    that was asked for and did not arrive is a warning.
    """

    appletsrc_path = (
        Path(common_values.DESKTOP_HOME_DIR) / values.APPLETSRC_RELATIVE_PATH
    )
    try:
        groups = _taskbar_launcher_groups(appletsrc_path.read_text(encoding="utf-8"))
    except OSError:
        _log(
            "no Plasma panel config yet; the Firefox launcher pins after the "
            "first login"
        )
        return False, None
    if not groups:
        _log("no Plasma task manager applet found; the Firefox launcher is not pinned")
        return False, None
    env = _desktop_session_environment()
    if env is None:
        _log("no desktop session, the launcher pins at the next login")
        return _pin_launcher_in_the_appletsrc(groups, timeout=timeout)
    pinned, warning = _pin_launcher_in_the_running_panel(env, timeout=timeout)
    if warning is None:
        return pinned, None
    file_changed, file_note = _pin_launcher_in_the_appletsrc(groups, timeout=timeout)
    if file_note:
        return pinned or file_changed, f"{warning}; {file_note}"
    return pinned or file_changed, f"{warning}, it is written into the appletsrc"


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
    sync_changed, error = _sync_settings_repo(timeout=timeout)
    if error:
        warnings.append(error)
    elif sync_changed:
        messages.append("updated the Firefox defaults repository")
        changed = True

    tree_changed, tree_warnings = _deploy_system_tree(
        force=force, owner_uid=owner_uid, owner_gid=owner_gid
    )
    warnings.extend(tree_warnings)
    if tree_changed:
        messages.append(f"deployed Firefox defaults to {values.SYSTEM_ROOT}")
        changed = True

    _log("setting Firefox as the default browser")
    browser_changed, browser_warning = _set_default_browser(timeout=timeout)
    if browser_warning:
        warnings.append(browser_warning)
    elif browser_changed:
        messages.append("set Firefox as the default browser")
        changed = True

    _log("pinning the Firefox launcher to the Plasma taskbar")
    pin_changed, pin_note = _pin_firefox_launcher(timeout=timeout)
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
