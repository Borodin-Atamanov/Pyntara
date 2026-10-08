"""Pinning a launcher to the Plasma taskbars of the desktop user.

Every task manager applet of the desktop user, icons-only or classic, receives
the launcher id when it is missing, so the button appears in whichever taskbar
exists. A running panel receives it through the scripting interface of the
shell, which applies it at once and stores it in the appletsrc of the user;
without a running session, or when the shell cannot be reached, the launcher is
written into the group below every task manager applet, where the panel reads it
at the next login.

chrome_setup and firefox_setup pin their own launcher this way, so the flow
lives here once and a task passes the id of its own launcher. A missing
appletsrc (the user has no Plasma panel config yet) is a note, not an error, and
a pin that was asked for and did not arrive is a warning: a task never reports a
launcher it did not place.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pyntara import appletsrc, kconfig
from pyntara.logger import log_progress as _log
from pyntara.utils import (
    as_user_command,
    home_environment,
    run_command,
    session_environment,
    substituted_command,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import kde_settings as shell_values


def launcher_groups(text: str) -> tuple[tuple[str, ...], ...]:
    """The group that holds the pinned launchers of every task manager applet."""

    return tuple(
        group + common_values.APPLET_CONFIGURATION_GROUP
        for group in appletsrc.applet_groups(text, common_values.TASKBAR_PLUGIN_NAMES)
    )


def _desktop_session_environment() -> dict[str, str] | None:
    """Environment that reaches the live Plasma session; None when none runs.

    The session variables are read from the session manager of the desktop
    user, so the launcher reaches the running panel even when the run started
    over SSH without a desktop environment. None means no live session: the
    launcher is then written into the appletsrc and appears at the next login.
    """

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
    env = home_environment()
    env.update(session)
    return env


def _launcher_script(launcher_id: str) -> str:
    """The Plasma script that gives the launcher to every taskbar of the panel.

    The script names each taskbar applet by the plugin its section declares,
    because the position of an applet on the panel differs per machine, selects
    the group below that applet and appends the launcher id to its launchers
    through the applet itself, which applies it to the running panel at once and
    stores it in the appletsrc. Every taskbar applet reports the list it holds
    after the call, so the caller can tell a pin that happened from one that did
    not.
    """

    spec = json.dumps(
        {
            "plugins": list(common_values.TASKBAR_PLUGIN_NAMES),
            "group": list(common_values.APPLET_CONFIGURATION_GROUP[1:]),
            "key": common_values.APPLETSRC_LAUNCHERS_KEY,
            "id": launcher_id,
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
    launcher_id: str, env: dict[str, str], *, timeout: float
) -> tuple[bool, str | None]:
    """Give the launcher to the running panel; (pinned, warning).

    Returns whether the running panel took the launcher and a warning when it
    was asked and did not take it, so the caller never claims a launcher that
    did not arrive. A shell that cannot be reached is a warning as well: the
    file write behind it handles the next login.
    """

    command = as_user_command(
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
                "script": _launcher_script(launcher_id),
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
        if launcher_id not in [entry for entry in held.split(",") if entry]:
            return pinned, (
                f"the running panel did not take the launcher of {widget_type}; "
                "it appears in the panel at the next login"
            )
    _log("gave the launcher to the running panel")
    return pinned, None


def _pin_launcher_in_the_appletsrc(
    launcher_id: str, groups: tuple[tuple[str, ...], ...], *, timeout: float
) -> tuple[bool, str | None]:
    """Append the launcher to the pinned list of every taskbar; (changed, note).

    This is the path for a machine without a running session and the fallback of
    a shell that could not be reached: the group below every task manager applet
    is the group the panel reads, so the button appears at the next login.
    """

    changed = False
    for group in groups:
        try:
            current = kconfig.read_config_value(
                common_values.APPLETSRC_FILE_NAME,
                group,
                common_values.APPLETSRC_LAUNCHERS_KEY,
                timeout=timeout,
            )
            entries = [entry for entry in current.split(",") if entry]
            if launcher_id in entries:
                continue
            kconfig.write_config_value(
                common_values.APPLETSRC_FILE_NAME,
                group,
                common_values.APPLETSRC_LAUNCHERS_KEY,
                ",".join([*entries, launcher_id]),
                timeout=timeout,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            return changed, f"cannot pin the launcher {launcher_id}: {exc}"
        changed = True
    return changed, None


def pin_launcher(launcher_id: str, *, timeout: float) -> tuple[bool, str | None]:
    """Pin one launcher to the Plasma taskbars; (changed, note)."""

    appletsrc_path = (
        Path(common_values.DESKTOP_HOME_DIR) / common_values.APPLETSRC_RELATIVE_PATH
    )
    try:
        groups = launcher_groups(appletsrc_path.read_text(encoding="utf-8"))
    except OSError:
        _log(
            "no Plasma panel config yet; the launcher pins after the first login"
        )
        return False, None
    if not groups:
        _log("no Plasma task manager applet found; the launcher is not pinned")
        return False, None
    env = _desktop_session_environment()
    if env is None:
        _log("no desktop session, the launcher pins at the next login")
        return _pin_launcher_in_the_appletsrc(launcher_id, groups, timeout=timeout)
    pinned, warning = _pin_launcher_in_the_running_panel(
        launcher_id, env, timeout=timeout
    )
    if warning is None:
        return pinned, None
    file_changed, file_note = _pin_launcher_in_the_appletsrc(
        launcher_id, groups, timeout=timeout
    )
    if file_note:
        return pinned or file_changed, f"{warning}; {file_note}"
    return pinned or file_changed, f"{warning}, it is written into the appletsrc"
