"""Task chrome_setup: install Google Chrome and apply the browser settings.

The described goal is a Google Chrome installed from the official Google
apt repository, carrying the browser settings of the chromium-default-
settings repository, and launched from the KDE menu with a Chrome DevTools
Protocol listener on the loopback address. The task registers the official
Google apt source (a deb822 file with the keyring downloaded from Google),
installs google-chrome-stable when missing, clones or updates the settings
repository into the root cache, deploys its system/ tree (the machine
policy and the external extension files) under the configured system root,
merges its Default/Preferences over the live profile of the desktop user,
and writes a desktop entry override that appends the launch flags to every
Exec line of the packaged entry: the local proxy of the
three_x_ui_xray_setup section when a listener answers on its port, the
profile mirror, and the CDP listener. The mirror is a bind mount of the
live profile under profile_mirror_path, restored at every boot by the
oneshot unit mount_service_unit_name, because branded Chrome refuses the
DevTools listener on the default data directory. The task then pins the
entry to the Plasma taskbar of the desktop user so the Chrome button sits
in the panel.

No piece of the launcher is assumed to be in place. The proxy flag enters
the entry only when the port of the local proxy answers, because a proxy
flag with no proxy behind it opens every request with a connection error,
and the absence is reported as a warning. The --user-data-dir flag enters
the entry only when the mirror mount is confirmed, because a Chrome start
on an empty directory would hide the live profile; a mirror that could not
be mounted is reported as a warning too.

The profile merge is identical in normal and force mode by design: the
repository preferences are laid over the current profile, so the
configured keys win on conflict and unrelated current settings are never
deleted. When the profile merge would produce the current file, nothing is
written. A running Chrome makes the profile merge wait: it warns and
applies on the next Chrome start, because a live Chrome would rewrite the
file from its own memory. The desktop override lives in
/usr/local/share/applications, ahead of the packaged entry in the XDG
search order, and is re-derived from the packaged entry on every run, so a
Chrome update that replaces the packaged file is followed on the next run
(docs/spec/chrome-setup.md).

Force mode re-runs the installation and rewrites the deployed files
regardless of the current bytes; it changes nothing about the merge, which
is identical in both modes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
    install_package_once,
    package_is_installed,
    port_listener_pid,
    refresh_apt_index,
    run_command,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import chrome_setup as values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import three_x_ui_xray_setup as panel_values

# The placeholders of a configured launch flag, e.g. {proxy_server}: a
# flag whose value is empty is left out of the Exec line.
FLAG_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")


def _source_text(template_path: Path, keyring_path: Path) -> str:
    """The deb822 apt source of the official Google Chrome repository.

    The body of the source file lives in the template under task_data/ and
    only the keyring path is substituted, so the suite, the components and
    the archive address stay with the template.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(keyring_path=str(keyring_path))


def _ensure_repository(
    apt_source_template_path: Path,
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, str | None]:
    """Register the Google apt source and its keyring; (changed, error).

    The keyring is downloaded from Google when missing, with the declared
    download command, and dearmored into the configured path; the deb822
    source file is rendered from its template and written when its content
    differs. Both files are root-owned with the configured mode.
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
                armored = Path(tmp) / values.KEYRING_ARMORED_FILE_NAME
                run_command(
                    download_command(armored, values.GOOGLE_KEY_URL),
                    timeout=timeout,
                )
                run_command(
                    substituted_command(
                        values.KEYRING_DEARMOR_COMMAND,
                        {
                            "output": str(values.KEYRING_PATH),
                            "armored": str(armored),
                        },
                    ),
                    timeout=timeout,
                )
            values.KEYRING_PATH.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(values.KEYRING_PATH, owner_uid, owner_gid)
            changed = True
        content = _source_text(apt_source_template_path, values.KEYRING_PATH)
        if not (
            values.APT_SOURCE_PATH.is_file()
            and values.APT_SOURCE_PATH.read_text(encoding="utf-8") == content
        ):
            values.APT_SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
            values.APT_SOURCE_PATH.write_text(content, encoding="utf-8")
            values.APT_SOURCE_PATH.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(values.APT_SOURCE_PATH, owner_uid, owner_gid)
            changed = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return changed, f"cannot register the Google Chrome apt repository: {exc}"
    return changed, None


def _ensure_chrome_installed(
    *,
    force: bool,
    skip_apt_update: bool,
    timeout: float,
) -> tuple[bool, str | None]:
    """Install the browser package when missing or forced; (changed, error)."""

    if not force and package_is_installed(values.PACKAGE_NAME, timeout):
        return False, None
    try:
        if not skip_apt_update:
            refresh_apt_index(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot refresh the apt index: {exc}"
    ok, error = install_package_once(values.PACKAGE_NAME, timeout)
    if not ok:
        return False, f"cannot install {values.PACKAGE_NAME}: {error}"
    return True, None


def _sync_settings_repo(*, timeout: float) -> tuple[bool, str | None]:
    """Clone or update the browser settings repository; (changed, error).

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
        return False, f"cannot update the browser settings repository: {exc}"


def _deploy_system_tree(
    *,
    force: bool,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, list[str]]:
    """Deploy the repository system/ tree under SYSTEM_ROOT; (changed, warnings).

    Each file is copied to the values.SYSTEM_ROOT with its relative path
    preserved, root-owned mode 0644, only when the target differs (or in
    force mode). A per-file failure is a warning, never a fatal error.
    """

    source_root = values.SETTINGS_DIR / values.SETTINGS_SYSTEM_TREE_RELATIVE_PATH
    if not source_root.is_dir():
        return False, ["the settings repository carries no system/ tree"]
    changed = False
    warnings: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root)
        target = values.SYSTEM_ROOT / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if (
                not force
                and target.is_file()
                and target.read_bytes() == path.read_bytes()
            ):
                continue
            shutil.copyfile(path, target)
            target.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(target, owner_uid, owner_gid)
            changed = True
        except OSError as exc:
            warnings.append(f"cannot deploy {rel}: {exc}")
    return changed, warnings


def _profile_dir() -> Path:
    """The live Chrome profile directory of the desktop user."""

    return Path(common_values.DESKTOP_HOME_DIR) / values.PROFILE_DIR_RELATIVE_PATH


def _profile_preferences_path() -> Path:
    """The live Chrome profile preferences of the desktop user."""

    return _profile_dir() / values.PREFERENCES_RELATIVE_PATH


def _merge_preferences(current: object, overlay: object) -> object:
    """Merge overlay over current; the overlay wins on conflicts.

    Dictionaries merge recursively, so unrelated current keys are kept and
    only the overlapping leaves take the overlay value. Lists and scalars
    in the overlay replace the current value entirely.
    """

    if isinstance(current, dict) and isinstance(overlay, dict):
        merged = dict(current)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _merge_preferences(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


def _chrome_is_running(timeout: float) -> bool:
    """True when a Google Chrome main process is running."""

    result = run_command(
        substituted_command(
            values.PROCESS_CHECK_COMMAND,
            {"process_name": values.PROCESS_NAME},
        ),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _own_to_user(username: str, path: Path) -> None:
    """Chown a file under the user home to the desktop user.

    The provisioning engine runs as root, so profile files must belong to
    the desktop user; a non-root test run and an unknown configured user
    leave the ownership untouched.
    """

    if os.geteuid() != 0:
        return
    try:
        import pwd

        entry = pwd.getpwnam(username)
    except KeyError:
        return
    os.chown(path, entry.pw_uid, entry.pw_gid)


def _apply_profile_preferences(*, timeout: float) -> tuple[bool, str | None]:
    """Merge the repository profile over the live profile; (changed, note).

    The merge is identical in normal and force mode: the repository
    preferences are laid over the current profile file, the configured
    keys win and unrelated current settings are never deleted. A merge
    that reproduces the current content writes nothing. A running Chrome
    and an unreadable profile file leave the file untouched and report a
    warning, because a live Chrome would overwrite the file from its own
    memory.
    """

    repo_prefs = values.SETTINGS_DIR / values.PREFERENCES_RELATIVE_PATH
    if not repo_prefs.is_file():
        return False, "the settings repository carries no preferences file"
    if _chrome_is_running(timeout):
        return (
            False,
            "Google Chrome is running; the profile settings apply on the next Chrome start",
        )
    target = _profile_preferences_path()
    try:
        try:
            current: object = (
                json.loads(target.read_text(encoding="utf-8"))
                if target.is_file()
                else {}
            )
        except json.JSONDecodeError, OSError:
            return (
                False,
                f"the profile preferences are unreadable; left untouched: {target}",
            )
        overlay: object = json.loads(repo_prefs.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"cannot read the repository preferences: {exc}"
    merged = _merge_preferences(current, overlay)
    if merged == current:
        return False, None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        target.chmod(common_values.LAUNCHER_FILE_MODE)
        _own_to_user(common_values.DESKTOP_USERNAME, target)
    except OSError as exc:
        return False, f"cannot write the profile preferences: {exc}"
    return True, None


def _mirror_is_mounted(profile_dir: Path, mirror_path: Path, timeout: float) -> bool:
    """True when the mirror path is a bind mount of the live profile.

    findmnt answers the mount that contains the target path: TARGET is that
    mount point and FSROOT is the directory inside the mounted filesystem the
    mount starts at. A bind mount of the profile shows the mirror path as the
    mount point and the profile directory as the filesystem root, while a
    plain directory reports the mount that encloses it. The source column is
    not used because findmnt renders a bind mount source as the device with
    the subdirectory in brackets.
    """

    result = run_command(
        substituted_command(values.MOUNT_CHECK_COMMAND, {"path": str(mirror_path)}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return False
    fields = trim_whitespace(result.stdout).split()
    return (
        len(fields) == 2
        and fields[0] == str(mirror_path)
        and fields[1] == str(profile_dir)
    )


def _render_mount_unit(
    template_path: Path, profile_dir: Path, mirror_path: Path, username: str
) -> str:
    """Render the bind mount unit with the profile and mirror paths."""

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        profile_dir=str(profile_dir),
        mirror_path=str(mirror_path),
        username=username,
    )


def _ensure_profile_mirror(
    unit_dir: Path,
    template_path: Path,
    *,
    force: bool,
    timeout: float,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, str | None]:
    """Mount the live profile on the mirror path and keep it across boots.

    Branded Google Chrome refuses the DevTools listener on the default data
    directory and asks for a non-default one, so the live profile is bind
    mounted to a second path and Chrome is started with --user-data-dir on
    that path: the same files under another directory name. The mount is
    restored at every boot by the oneshot unit named by
    MOUNT_SERVICE_UNIT_NAME, rendered from the template; the unit is written
    and enabled on every run, so a mirror that was mounted by hand is
    restored after a reboot too. Returns whether the
    mirror is mounted; a failure is a note and leaves the caller without the
    --user-data-dir flag, so a Chrome start never lands on an empty directory
    while the live profile stays where Chrome expects it.
    """

    username = common_values.DESKTOP_USERNAME
    profile_dir = _profile_dir()
    mirror_path = values.PROFILE_MIRROR_PATH
    unit_name = values.MOUNT_SERVICE_UNIT_NAME
    try:
        if not profile_dir.is_dir():
            profile_dir.mkdir(parents=True, exist_ok=True)
            _own_to_user(username, profile_dir)
    except OSError as exc:
        return (
            False,
            f"cannot create the Chrome profile directory {profile_dir}: {exc}",
        )
    try:
        content = _render_mount_unit(template_path, profile_dir, mirror_path, username)
    except OSError as exc:
        return False, f"cannot read the profile mirror unit template: {exc}"
    unit_file = unit_dir / unit_name
    try:
        current = unit_file.read_text(encoding="utf-8") if unit_file.is_file() else ""
        if force or current != content:
            unit_dir.mkdir(parents=True, exist_ok=True)
            unit_file.write_text(content, encoding="utf-8")
            unit_file.chmod(common_values.LAUNCHER_FILE_MODE)
            apply_owner(unit_file, owner_uid, owner_gid)
            run_command(values.MOUNT_RELOAD_COMMAND, timeout=timeout)
        run_command(
            substituted_command(values.MOUNT_ENABLE_COMMAND, {"unit_name": unit_name}),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot enable the profile mirror mount {unit_name}: {exc}"
    if not _mirror_is_mounted(profile_dir, mirror_path, timeout):
        return False, (
            f"the profile mirror {mirror_path} is not mounted; Chrome starts "
            "without --user-data-dir and the DevTools listener stays off"
        )
    return True, None


def _local_proxy_server(
    timeout: float
) -> tuple[str, str | None]:
    """The SOCKS5 address of the local proxy; (proxy text, note).

    The proxy is the mixed inbound that three_x_ui_xray_setup creates on the
    loopback address. A machine that is the remote server itself never raises
    that inbound, and a panel that is down has no listener either, so the port
    is asked for a listener before the flag enters the desktop entry: Chrome
    with a proxy flag and no proxy behind it opens every request with a
    connection error, while Chrome without the flag keeps working. An empty
    proxy text and a note are returned when no listener answers, so the caller
    reports the missing proxy instead of writing a flag that cannot work.
    """

    address = panel_values.LOCAL_PROXY_LISTEN_ADDRESS
    port = panel_values.LOCAL_PROXY_PORT
    if not address or not port:
        return "", (
            "the three_x_ui_xray_setup section carries no local proxy address; "
            "Chrome starts without the proxy"
        )
    if port_listener_pid(port, timeout) is None:
        return "", (
            f"no local proxy listens on {address}:{port}; Chrome starts without it"
        )
    return f"socks5://{address}:{port}", None


def _desktop_content(
    source_text: str,
    *,
    proxy_server: str,
    user_data_dir: str,
) -> str:
    """The packaged desktop entry with the launch flags on every Exec line.

    The flags come from LAUNCH_FLAGS in order, each rendered with the values of
    this run: the local proxy the browser must use, the profile mirror that
    makes the DevTools listener work with the live profile, and the DevTools
    listener itself. A flag whose placeholder has no value is left out, so a
    piece that is not in place costs the browser that one flag instead of the
    whole start.
    """

    placeholders = {
        "proxy_server": proxy_server,
        "user_data_dir": user_data_dir,
        "cdp_port": str(values.CDP_PORT),
        "cdp_address": values.CDP_ADDRESS,
    }
    flags = ""
    for flag in values.LAUNCH_FLAGS:
        names = FLAG_PLACEHOLDER_PATTERN.findall(flag)
        if any(not placeholders[name] for name in names):
            continue
        flags += " " + flag.format(**placeholders)
    lines: list[str] = []
    for line in source_text.splitlines(keepends=True):
        if line.startswith(values.DESKTOP_ENTRY_EXEC_KEY):
            lines.append(line.rstrip("\n") + flags + "\n")
        else:
            lines.append(line)
    return "".join(lines)


def _ensure_desktop_override(
    *,
    proxy_server: str,
    user_data_dir: str,
    force: bool,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, str | None]:
    """Write the desktop override with the launch flags; (changed, warning)."""

    source = values.DESKTOP_SOURCE_PATH
    if not source.is_file():
        return (
            False,
            f"the packaged desktop entry is missing; launch flags not applied: {source}",
        )
    content = _desktop_content(
        source.read_text(encoding="utf-8"),
        proxy_server=proxy_server,
        user_data_dir=user_data_dir,
    )
    target = values.DESKTOP_OVERRIDE_PATH
    try:
        if (
            not force
            and target.is_file()
            and target.read_text(encoding="utf-8") == content
        ):
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(common_values.LAUNCHER_FILE_MODE)
        apply_owner(target, owner_uid, owner_gid)
    except OSError as exc:
        return False, f"cannot write the desktop override: {exc}"
    return True, None


def _refresh_menu_database(*, timeout: float) -> str | None:
    """Rebuild the KDE menu cache for the desktop user; return warning text.

    A best-effort step: the override is picked up by the session when the
    cache is rebuilt, so a failure is a warning and the menu catches up at
    the next login or cache rebuild. The command carries the
    XDG_MENU_PREFIX of the Plasma session so the rebuild looks up
    plasma-applications.menu instead of warning about a missing default
    applications.menu.
    """

    try:
        run_command(
            substituted_command(
                values.MENU_REFRESH_COMMAND,
                {
                    "username": common_values.DESKTOP_USERNAME,
                    "home_dir": common_values.DESKTOP_HOME_DIR,
                },
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return f"menu database refresh failed: {exc}"
    return None


def _as_user_command(command: list[str]) -> list[str]:
    """Prefix a command with the wrapper of the desktop user.

    The wrapper is a value of this section, so a machine whose desktop user is
    reached another way is a values change.
    """

    return [
        *substituted_command(
            values.RUNUSER_COMMAND,
            {"username": common_values.DESKTOP_USERNAME},
        ),
        *command,
    ]


def _home_env() -> dict[str, str]:
    """Environment that points the KDE config tools at the user home."""

    return {"HOME": common_values.DESKTOP_HOME_DIR}


def _kconfig_command(
    base_command: tuple[str, ...],
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the base, the groups and the key.

    The base call carries the file name and every selector is a value of the
    section, so another KConfig version or another tool is a values change.
    The reader and the writer share this builder, so the two calls can never
    drift apart.
    """

    command = substituted_command(
        base_command, {"file_name": values.APPLETSRC_FILE_NAME}
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
    *,
    timeout: float,
) -> str:
    """Current value of one appletsrc key of the desktop user."""

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
    """Write one appletsrc key with the writer of the section as the user."""

    command = _kconfig_command(values.KWRITECONFIG_COMMAND, group_segments, key)
    command.append(value)
    run_command(
        _as_user_command(command),
        extra_env=_home_env(),
        timeout=timeout,
    )


def _taskbar_launcher_groups(text: str) -> list[tuple[str, ...]]:
    """The group of every task manager applet that holds pinned launchers.

    Plasma appletsrc nests groups as [Containments][X][Applets][Y]; the
    applet whose section declares one of the configured task manager
    plugins holds its pinned launchers in the group below that section,
    named by the configured appletsrc_launcher_group. Returns the group
    segments of every matching applet, so a desktop with both widget types
    or several panels pins all of them.
    """

    groups: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    # The key of the line that names the applet plugin in an appletsrc
    # section; removeprefix keeps the reader free of an index.
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


def _pin_chrome_launcher(*, timeout: float) -> tuple[bool, str | None]:
    """Pin the CDP Chrome launcher to the Plasma taskbars; (changed, note).

    Every task manager applet of the desktop user appletsrc, icons-only
    or classic, receives the launcher id in its pinned launchers when it
    is missing, so the button appears in whichever taskbar exists. The
    launcher id resolves to the CDP desktop override. A missing appletsrc
    (the user has no Plasma panel config yet) is a note, not an error; a
    failing read or write is reported as a note so the remaining steps
    still run.
    """

    appletsrc_path = (
        Path(common_values.DESKTOP_HOME_DIR) / values.APPLETSRC_RELATIVE_PATH
    )
    try:
        groups = _taskbar_launcher_groups(appletsrc_path.read_text(encoding="utf-8"))
    except OSError:
        _log(
            "no Plasma panel config yet; the Chrome launcher pins after the first login"
        )
        return False, None
    if not groups:
        _log("no Plasma task manager applet found; the Chrome launcher is not pinned")
        return False, None
    changed = False
    try:
        for group in groups:
            current = _kreadconfig(
                group, values.APPLETSRC_LAUNCHERS_KEY, timeout=timeout
            )
            entries = [entry for entry in current.split(",") if entry]
            if values.PANEL_LAUNCHER_ID in entries:
                continue
            _kwriteconfig(
                group,
                values.APPLETSRC_LAUNCHERS_KEY,
                ",".join([*entries, values.PANEL_LAUNCHER_ID]),
                timeout=timeout,
            )
            changed = True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:
        return changed, f"cannot pin the Chrome launcher: {exc}"
    return changed, None


def task(ctx: Context) -> TaskResult:
    """Install Chrome and apply the browser settings and the CDP entry.

    The target state is reached when google-chrome-stable is installed,
    the Google apt repository is registered, the settings repository is
    current, its system/ tree and profile preferences are applied, the
    desktop override with the CDP flags is in place and the Chrome
    launcher sits in the Plasma taskbar; the task then returns
    changed=False. Force mode reinstalls Chrome and rewrites the deployed
    files; the profile merge itself is identical in both modes.
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
            message="the chrome_setup values are not declared, nothing was changed",
            warnings=(
                "the chrome_setup values are not declared: " + ", ".join(absent),
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
    mount_unit_template_path = template_dir / values.MOUNT_UNIT_TEMPLATE_FILE_NAME

    if apt_source_template_path.is_file():
        _log("registering the Google Chrome apt repository")
        repo_changed, error = _ensure_repository(
            apt_source_template_path,
            timeout,
            owner_uid,
            owner_gid,
        )
        if error:
            warnings.append(error)
        elif repo_changed:
            messages.append("registered the Google Chrome apt repository")
            changed = True
    else:
        warnings.append(f"missing apt source template: {apt_source_template_path}")

    _log("checking the Google Chrome installation")
    install_changed, error = _ensure_chrome_installed(
        force=force,
        skip_apt_update=ctx.skip_apt_update,
        timeout=timeout,
    )
    if error:
        warnings.append(error)
    elif install_changed:
        messages.append(f"installed {values.PACKAGE_NAME}")
        changed = True

    _log("updating the browser settings repository")
    sync_changed, error = _sync_settings_repo(timeout=timeout)
    if error:
        warnings.append(error)
    elif sync_changed:
        messages.append("updated the browser settings repository")
        changed = True

    tree_changed, tree_warnings = _deploy_system_tree(
        force=force, owner_uid=owner_uid, owner_gid=owner_gid
    )
    warnings.extend(tree_warnings)
    if tree_changed:
        messages.append(f"deployed system browser settings to {values.SYSTEM_ROOT}")
        changed = True

    profile_changed, profile_note = _apply_profile_preferences(timeout=timeout)
    if profile_note:
        warnings.append(profile_note)
    if profile_changed:
        messages.append(
            f"merged the browser profile settings over {_profile_preferences_path()}"
        )
        changed = True

    _log("checking the local proxy of the Xray client")
    proxy_server, proxy_note = _local_proxy_server(timeout=timeout)
    if proxy_note:
        warnings.append(proxy_note)
    else:
        _log(f"Chrome starts with the local proxy {proxy_server}")

    _log("mounting the Chrome profile mirror for the DevTools listener")
    if mount_unit_template_path.is_file():
        mirror_mounted, mirror_note = _ensure_profile_mirror(
            engine_values.SYSTEMD_UNIT_DIR,
            mount_unit_template_path,
            force=force,
            timeout=timeout,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
        if mirror_note:
            warnings.append(mirror_note)
        else:
            _log(f"the profile mirror is mounted at {values.PROFILE_MIRROR_PATH}")
    else:
        mirror_mounted = False
        warnings.append(f"missing mirror unit template: {mount_unit_template_path}")

    override_changed, override_note = _ensure_desktop_override(
        proxy_server=proxy_server,
        user_data_dir=str(values.PROFILE_MIRROR_PATH) if mirror_mounted else "",
        force=force,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    if override_note:
        warnings.append(override_note)
    if override_changed:
        messages.append(
            f"wrote the browser launcher entry to {values.DESKTOP_OVERRIDE_PATH}"
        )
        changed = True
        menu_note = _refresh_menu_database(timeout=timeout)
        if menu_note:
            warnings.append(menu_note)

    _log("pinning the Chrome launcher to the Plasma taskbar")
    pin_changed, pin_note = _pin_chrome_launcher(timeout=timeout)
    if pin_note:
        warnings.append(pin_note)
    if pin_changed:
        messages.append("pinned the Chrome launcher to the Plasma taskbar")
        changed = True
        try:
            run_command(
                substituted_command(
                    values.PANEL_RESTART_COMMAND,
                    {"username": common_values.DESKTOP_USERNAME},
                ),
                timeout=timeout,
            )
            _log("restarted the Plasma panel")
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warnings.append(f"cannot restart the Plasma panel: {exc}")

    if port_listener_pid(values.CDP_PORT, timeout) is None:
        if _chrome_is_running(timeout):
            warnings.append(
                "Chrome is running but the DevTools listener does not answer on "
                f"{values.CDP_ADDRESS}:{values.CDP_PORT}; restart Chrome from "
                "the menu so the launcher flags apply"
            )
        else:
            _log(
                "the DevTools listener starts with the next Chrome launch from "
                f"the menu, on {values.CDP_ADDRESS}:{values.CDP_PORT}"
            )

    if not messages:
        messages.append("browser already set up")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
