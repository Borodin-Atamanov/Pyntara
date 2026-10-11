"""Task install_gui_apps: install the graphical applications and pin them.

The described goal is a machine where each graphical application of GUI_APPS is
installed from the Ubuntu archive and its launcher sits in the Plasma taskbar
of the desktop user. Every application is installed on its own, so one
application whose package cannot be installed never blocks the others, and the
launcher of an application is pinned only when its menu entry is present on the
disk, so the task never claims an icon that did not arrive. The pin is the
shared flow of pyntara.plasma_panel: it reaches the running panel through the
shell scripting interface and falls back to the appletsrc of the desktop user
when no session runs.

The task takes no dependency and belongs to the desktop and the fast_desktop
modes; the catalog places it after the browser tasks, because the browser is
the program these applications are used next to. Force mode changes nothing
here: apt refreshes the packages and the task generates no persistent identity,
so there is no state that a force mode would rebuild afresh. A missing value
and a failed step are warnings of a completed task, so the run carries on and
never stops here (docs/spec/install-gui-apps.md).
"""

from __future__ import annotations

from pathlib import Path

from pyntara import plasma_panel
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import failure_detail, install_missing_packages
from pyntara.values import engine as engine_values
from pyntara.values import install_gui_apps as values
from pyntara.values import missing_value_names


def _menu_entry_path(app: values.GuiApp) -> Path:
    """The menu entry file of an application on the machine."""

    return values.APPLICATIONS_DIRECTORY / app.desktop_file_name


def _launcher_id(app: values.GuiApp) -> str:
    """The Plasma launcher id the application's menu entry is pinned under."""

    return values.PANEL_LAUNCHER_ID_PREFIX + app.desktop_file_name


def _install_application(ctx: Context, app: values.GuiApp) -> tuple[bool, list[str]]:
    """Install one application's packages; (changed, warnings).

    The shared install path of pyntara.package_set checks the real state with
    dpkg, installs exactly the missing packages through apt and reports every
    package that still failed with its reason. A package that cannot be
    installed is a warning of this application and never stops the other
    applications of the list.
    """

    _log(f"installing {app.name}")
    _missing, installed, failures, warnings = install_missing_packages(
        ctx, app.packages
    )
    found = list(warnings)
    detail = failure_detail(failures)
    if detail:
        found.append(f"{app.name}: {detail}")
    return bool(installed), found


def _pin_application(app: values.GuiApp) -> tuple[bool, list[str]]:
    """Pin one application's launcher; (changed, warnings).

    The menu entry is checked first, because the launcher names it: pinning a
    launcher whose entry is absent would put a button on the panel that starts
    nothing. A missing entry is a warning that names the path, and the panel is
    otherwise left to the shared pin flow.
    """

    entry = _menu_entry_path(app)
    if not entry.is_file():
        return False, [
            (
                f"the menu entry {entry} of {app.name} is missing; "
                "its launcher is not pinned"
            )
        ]
    _log(f"pinning the {app.name} launcher to the Plasma taskbar")
    changed, note = plasma_panel.pin_launcher(
        _launcher_id(app), timeout=engine_values.COMMAND_TIMEOUT_SECONDS
    )
    return changed, [note] if note else []


def task(ctx: Context) -> TaskResult:
    """Install the graphical applications and pin their launchers.

    The target state is reached when every declared package is installed and
    every launcher is in the taskbar of the desktop user; the task then returns
    changed=False. Force mode does not change the result. A value that is not
    declared, a package that cannot be installed and a missing menu entry are
    warnings of a completed task, so the run continues and reports the
    incomplete configuration with a nonzero exit code.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message=(
                "the install_gui_apps values are not declared, nothing was changed"
            ),
            warnings=(
                "the install_gui_apps values are not declared: " + ", ".join(absent),
            ),
        )
    changed = False
    messages: list[str] = []
    warnings: list[str] = []

    for app in values.GUI_APPS:
        app_changed, app_warnings = _install_application(ctx, app)
        warnings.extend(app_warnings)
        if app_changed:
            messages.append(f"installed {app.name}")
            changed = True

    for app in values.GUI_APPS:
        pin_changed, pin_warnings = _pin_application(app)
        warnings.extend(pin_warnings)
        if pin_changed:
            messages.append(f"pinned the {app.name} launcher to the Plasma taskbar")
            changed = True

    if not messages:
        messages.append("the graphical applications are already installed and pinned")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
