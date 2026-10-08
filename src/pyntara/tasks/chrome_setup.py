"""Task chrome_setup: install Google Chrome and apply the browser settings.

The described goal is a Google Chrome installed from the official Google
apt repository, carrying the browser settings of the chromium-default-
settings repository, and launched from the KDE menu with a Chrome DevTools
Protocol listener on the loopback address. The task registers the official
Google apt source (a deb822 file with the keyring downloaded from Google),
installs google-chrome-stable when missing, clones or updates the settings
repository into the root cache, deploys its system/ tree (the machine
policy and the external extension files) under the configured system root,
applies the rest of its content to the live profile of the desktop user
(its Default/Preferences is merged over the current profile file, every
other file it carries is copied with its relative path preserved, the
first-run marker First Run among them), and writes a desktop entry
override that appends the launch flags to every
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

The profile is applied as a whole, so the profile of the machine is what the
repository declares and no file of it is named in code. The preferences merge
is identical in normal and force mode by design: the repository preferences
are laid over the current profile, so the configured keys win on conflict and
unrelated current settings are never deleted, and a merge that would produce
the current file writes nothing. A running Chrome makes the whole profile step
wait: it warns and applies on the next Chrome start, because a live Chrome
would rewrite its own profile files from memory, and one wait for the whole
step costs one message instead of one per file. The desktop override lives in
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
import subprocess
import tempfile
from pathlib import Path
from string import Template

from pyntara import plasma_panel, settings_repo
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    download_command,
    hand_to_user,
    install_package_once,
    package_is_installed,
    port_listener_pid,
    process_is_running,
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

    return process_is_running(values.PROCESS_NAME, timeout)


def _apply_profile_preferences() -> tuple[bool, str | None]:
    """Merge the repository preferences over the live profile; (changed, note).

    The merge is identical in normal and force mode: the repository
    preferences are laid over the current profile file, the configured keys win
    and unrelated current settings are never deleted. A merge that reproduces
    the current content writes nothing, and an unreadable profile file is left
    untouched and reported. The caller has asked whether Chrome runs and has
    handed the profile directory over, because both answers hold for every
    other piece of the profile as well.
    """

    repo_prefs = values.SETTINGS_DIR / values.PREFERENCES_RELATIVE_PATH
    if not repo_prefs.is_file():
        return False, "the settings repository carries no preferences file"
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
        target.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        target.chmod(common_values.LAUNCHER_FILE_MODE)
        hand_to_user(target)
    except OSError as exc:
        return False, f"cannot write the profile preferences: {exc}"
    return True, None


def _deploy_profile_content(*, force: bool) -> tuple[bool, list[str]]:
    """Copy the repository content into the live profile; (changed, warnings).

    Everything the settings repository carries is profile content except the
    tree deployed under the system root, the bookkeeping entries of the
    repository itself and the preferences file, which is merged instead of
    copied. The copy walks the repository, so a file added there arrives in the
    profile without a code change, which is how the first-run marker of the
    profile root reaches the machine.
    """

    return settings_repo.deploy_tree(
        values.SETTINGS_DIR,
        _profile_dir(),
        force=force,
        skip_relative_paths=(
            common_values.SETTINGS_SYSTEM_TREE_RELATIVE_PATH,
            *values.SETTINGS_REPO_BOOKKEEPING_PATHS,
            values.PREFERENCES_RELATIVE_PATH,
        ),
    )


def _apply_profile(*, force: bool, timeout: float) -> tuple[bool, list[str], list[str]]:
    """Apply the settings repository to the live browser profile.

    Returns whether anything was applied, one message per applied piece (the
    copied repository content and the merged preferences file, reported apart
    because a machine whose browser rewrites its preferences on every start
    would otherwise hide an unchanged repository behind one line) and the notes
    of the run.

    The repository is applied as a whole, so the profile carries what the
    repository declares and the task names no file of it: the preferences file
    is merged over the current profile, and every other file the repository
    carries is copied with its relative path preserved. A running Chrome makes
    the whole step wait, because a live Chrome rewrites its own profile files
    from its memory, and one wait for the whole step costs one message instead
    of one per file. The profile directory and everything in it belong to the
    desktop user, so the handover comes before the merge can decide that
    nothing has to be written, and it also takes the files the copy has just
    written.
    """

    if _chrome_is_running(timeout):
        running_note = (
            "Google Chrome is running; the profile settings apply on the next "
            "Chrome start"
        )
        return False, [], [running_note]
    profile_dir = _profile_dir()
    target = _profile_preferences_path()
    notes: list[str] = []
    messages: list[str] = []
    try:
        content_changed, warnings = _deploy_profile_content(force=force)
    except OSError as exc:
        return (
            False,
            [],
            [f"cannot apply the repository content to the profile: {exc}"],
        )
    notes.extend(warnings)
    if content_changed:
        messages.append(f"copied the repository browser settings to {profile_dir}")
    try:
        hand_to_user(profile_dir)
        hand_to_user(target.parent)
    except OSError as exc:
        return (
            bool(messages),
            messages,
            [
                *notes,
                f"cannot prepare the profile directory: {exc}",
            ],
        )
    merged_changed, note = _apply_profile_preferences()
    if note:
        notes.append(note)
    if merged_changed:
        messages.append(f"merged the browser preferences over {target}")
    return bool(messages), messages, notes


def _same_directory(left: Path, right: Path) -> bool:
    """True when two paths name the same directory.

    A bind mount shares the device and the inode of the directory it mounts,
    so the two paths of a correct mirror answer the same pair whatever
    filesystem carries them, while a copy or a second directory answers a
    different one. A path that cannot be inspected answers False.
    """

    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _mirror_is_mounted(profile_dir: Path, mirror_path: Path, timeout: float) -> bool:
    """True when the mirror path is a bind mount of the live profile.

    findmnt answers the mount that contains the target path, so the mirror is
    a mount point when it reports the mirror path itself. The filesystem root
    is not compared: a bind mount of a directory on a btrfs subvolume reports
    it with the subvolume prefix (a mirror of /home/i/.config/google-chrome
    reports /@home/i/.config/google-chrome), so the same directory would look
    like another one and every correct mirror would be reported unmounted. The
    identity of the two directories is proven by their inode instead.
    """

    result = run_command(
        substituted_command(values.MOUNT_CHECK_COMMAND, {"path": str(mirror_path)}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return False
    if trim_whitespace(result.stdout) != str(mirror_path):
        return False
    return _same_directory(profile_dir, mirror_path)


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
        hand_to_user(profile_dir)
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


def _local_proxy_server(timeout: float) -> tuple[str, str | None]:
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


def _chrome_cache_home() -> str:
    """The XDG cache home the browser is started with.

    The path sits under the home of the desktop user, so a machine whose
    desktop account is not the one this package was written for sends the font
    cache of its browser into the cache directory of that account.
    """

    return str(
        Path(common_values.DESKTOP_HOME_DIR) / values.CHROME_CACHE_HOME_RELATIVE_PATH
    )


def _exec_prefix(placeholders: dict[str, str]) -> str:
    """The command every Exec line starts through, with a trailing space.

    The prefix is all or nothing: it carries the program that runs the browser
    with a variable of its own, so a placeholder without a value drops the
    whole prefix instead of leaving a program without its variable or a
    variable without its program.
    """

    names = [
        name
        for part in values.EXEC_PREFIX
        for name in FLAG_PLACEHOLDER_PATTERN.findall(part)
    ]
    if any(not placeholders[name] for name in names):
        return ""
    rendered = " ".join(part.format(**placeholders) for part in values.EXEC_PREFIX)
    return rendered + " "


def _desktop_content(
    source_text: str,
    *,
    proxy_server: str,
    user_data_dir: str,
    chrome_cache_home: str,
) -> str:
    """The packaged desktop entry with the launch flags on every Exec line.

    The flags come from LAUNCH_FLAGS in order, each rendered with the values of
    this run: the local proxy the browser must use, the profile mirror that
    makes the DevTools listener work with the live profile, and the DevTools
    listener itself. A flag whose placeholder has no value is left out, so a
    piece that is not in place costs the browser that one flag instead of the
    whole start. The prefix of EXEC_PREFIX stands in front of the program of
    every such line, so the browser receives a cache home of its own on every
    launch of the entry, the ones from the menu and the ones from the panel
    alike.
    """

    placeholders = {
        "proxy_server": proxy_server,
        "user_data_dir": user_data_dir,
        "chrome_cache_home": chrome_cache_home,
        "cdp_port": str(values.CDP_PORT),
        "cdp_address": values.CDP_ADDRESS,
    }
    prefix = _exec_prefix(placeholders)
    flags = ""
    for flag in values.LAUNCH_FLAGS:
        names = FLAG_PLACEHOLDER_PATTERN.findall(flag)
        if any(not placeholders[name] for name in names):
            continue
        flags += " " + flag.format(**placeholders)
    lines: list[str] = []
    for line in source_text.splitlines(keepends=True):
        if line.startswith(values.DESKTOP_ENTRY_EXEC_KEY):
            key = values.DESKTOP_ENTRY_EXEC_KEY
            body = line[len(key) :].rstrip("\n")
            lines.append(f"{key}{prefix}{body}{flags}\n")
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
        chrome_cache_home=_chrome_cache_home(),
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


def task(ctx: Context) -> TaskResult:
    """Install Chrome and apply the browser settings and the CDP entry.

    The target state is reached when google-chrome-stable is installed,
    the Google apt repository is registered, the settings repository is
    current, its system/ tree and its profile content are applied, the
    desktop override with the CDP flags is in place and the Chrome
    launcher sits in the Plasma taskbar; the task then returns
    changed=False. Force mode reinstalls Chrome and rewrites the deployed
    files; the profile preferences are merged identically in both modes.
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
    sync_changed, error = settings_repo.sync_repository(
        url=values.SETTINGS_REPO_URL,
        directory=values.SETTINGS_DIR,
        timeout=timeout,
    )
    if error:
        warnings.append(error)
    elif sync_changed:
        messages.append("updated the browser settings repository")
        changed = True

    tree_changed, tree_warnings = settings_repo.deploy_system_tree(
        values.SETTINGS_DIR, force=force, owner_uid=owner_uid, owner_gid=owner_gid
    )
    warnings.extend(tree_warnings)
    if tree_changed:
        messages.append(
            f"deployed system browser settings to {common_values.SYSTEM_ROOT}"
        )
        changed = True

    _log("applying the browser profile settings")
    profile_changed, profile_messages, profile_notes = _apply_profile(
        force=force, timeout=timeout
    )
    warnings.extend(profile_notes)
    messages.extend(profile_messages)
    if profile_changed:
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
    pin_changed, pin_note = plasma_panel.pin_launcher(
        values.PANEL_LAUNCHER_ID, timeout=timeout
    )
    if pin_note:
        warnings.append(pin_note)
    if pin_changed:
        messages.append("pinned the Chrome launcher to the Plasma taskbar")
        changed = True

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
