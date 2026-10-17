"""Task ventoy_install: install the latest Ventoy release for the desktop.

The described goal is Ventoy on the machine, installed the simplest way it
allows. Ventoy publishes only a release archive: it has no package in the
Ubuntu archive and no apt repository of its own, so the newest release of the
configured GitHub repository is the single source, exactly as i2pd, yggdrasil
and the other release installs of this repository work. The version of the
release is chased on every run, so a rerun after an upstream release updates
the installation.

The archive is unpacked into a directory named after its version under the
install directory, and a symbolic link of the install directory names the
active version. The link is switched only after the unpacked tree answered its
own version query, so a broken tree never replaces a working one, and every
reader uses the stable link, so the path of the menu entry never changes. A
version directory that a new unpack replaces is renamed aside and never
deleted, so the previous tree stays recoverable.

The menu entry starts the graphical launcher of the release as the desktop
user. The launcher raises its own privileges through pkexec, which is how
upstream ships it, and it carries the user environment across that restart, so
the window opens in the running session. A machine whose linker cache holds
neither a GTK nor a Qt library cannot start the window, which the task reports
as a warning instead of leaving a menu entry that silently does nothing.

Every step is reported to stdout, and a step that cannot run is a warning of a
completed task, so the runner continues with the remaining tasks
(docs/spec/ventoy-install.md).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.github_release import asset_name_urls, fetch_latest_release, release_tag
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult, warned_result
from pyntara.utils import (
    discard_downloaded_files,
    download_file,
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
from pyntara.values import ventoy_install as values


def _version_dir(version: str) -> Path:
    """The directory of one installed release version."""

    return values.INSTALL_DIR / version


def _gui_binary_name(asset_arch: str) -> str:
    """The name of the graphical launcher for one architecture."""

    return values.GUI_BINARY_NAME_TEMPLATE.format(asset_arch=asset_arch)


def _tree_is_complete_at(tree: Path, version: str, gui_binary_name: str) -> bool:
    """True when one directory carries a usable Ventoy release tree.

    The tree is complete when every file of the release sits in it and the
    version file agrees with the expected version, because the version the
    task compares is the one the release names. A directory without the
    graphical launcher is a half finished install: the menu entry could not
    start it. The same check serves an installed version directory and a tree
    that is still under its temporary unpack name.
    """

    if not tree.is_dir() or not (tree / gui_binary_name).is_file():
        return False
    for relative_path in values.TREE_MARKER_FILES:
        if not (tree / relative_path).is_file():
            return False
    try:
        text = (tree / values.VERSION_FILE_RELATIVE_PATH).read_text(encoding="utf-8")
    except OSError:
        return False
    return trim_whitespace(text) == version


def _tree_is_complete(version: str, gui_binary_name: str) -> bool:
    """True when the version directory of an installed release is usable."""

    return _tree_is_complete_at(_version_dir(version), version, gui_binary_name)


def _installed_version(gui_binary_name: str) -> str | None:
    """The version the current link names, or None without a usable link.

    The link target is <install dir>/<version>, so the last part of the target
    is the installed version. The version is read from the link and never by
    running the program, so the decision about what to download does not
    depend on the installed tree being able to start.
    """

    link = values.CURRENT_LINK_PATH
    try:
        if not link.is_symlink():
            return None
        target = os.readlink(link)
    except OSError:
        return None
    version = Path(target).name
    if not version or not _tree_is_complete(version, gui_binary_name):
        return None
    return version


def _move_aside(path: Path) -> str:
    """Rename a version directory that a fresh unpack replaces; never deletes.

    The name carries the declared suffix, and a name that is already taken gets
    a counter, so nothing is ever overwritten and every replaced tree stays
    recoverable. Raises OSError, which the caller reports as a warning.
    """

    target = path.parent / (path.name + values.SUPERSEDED_SUFFIX)
    counter = 2
    while target.exists() or target.is_symlink():
        target = path.parent / f"{path.name}{values.SUPERSEDED_SUFFIX}-{counter}"
        counter += 1
    shutil.move(str(path), str(target))
    return f"moved {path.name} to {target.name}"


def _switch_current_link(target: Path) -> None:
    """Point the current link at one version directory in a single step.

    The new link is created next to the old one and renamed over it, so a
    reader never meets a moment without a link and never a link to a version
    that is still being unpacked.
    """

    link = values.CURRENT_LINK_PATH
    temporary = link.parent / (link.name + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX)
    try:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        os.symlink(str(target), temporary)
        os.replace(temporary, link)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _download_archive(version: str, url: str, timeout: float) -> Path:
    """Download the release archive into the cache; raises on failure.

    The transfer goes to a sibling with the declared partial suffix and is
    renamed only after a complete transfer, so a file under the final name is
    always a whole archive and a broken transfer is resumed on the next run.
    """

    archive_name = values.ARCHIVE_NAME_TEMPLATE.format(version=version)
    partial = values.DOWNLOAD_DIR / (
        archive_name + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX
    )
    values.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"downloading {url}")
    download_file(partial, url, timeout=timeout)
    archive = values.DOWNLOAD_DIR / archive_name
    partial.replace(archive)
    return archive


def _probe_tree(tree: Path, version: str, timeout: float) -> str | None:
    """Ask an unpacked tree for its own version; None means it answers.

    The console front end of the release prints the version it carries and its
    usage without touching a disk, so the query proves that the archive was
    unpacked whole and that the packed tools of the release inflate. The help
    flag keeps the front end at a successful exit; any other answer is the
    reason the tree is not used.
    """

    command = substituted_command(
        values.ACCEPTANCE_COMMAND, {"script": str(tree / "Ventoy2Disk.sh")}
    )
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"the unpacked tree does not answer: {exc}"
    expected = values.ACCEPTANCE_EXPECTED_TEXT.format(version=version)
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0 or expected not in output:
        return (
            f"the unpacked tree answers nothing like {expected!r} "
            f"(exit {result.returncode})"
        )
    return None

def _mark_executables(tree: Path, gui_binary_name: str) -> None:
    """Give the scripts and the graphical launcher the executable mode.

    The archive carries the modes already; the step is a safety net for an
    unpack that lost them, because a launcher without the mode is a menu entry
    that cannot start. A failure is reported and changes nothing else.
    """

    for path in (tree / gui_binary_name, *sorted(tree.glob("*.sh"))):
        try:
            path.chmod(common_values.EXECUTABLE_FILE_MODE)
        except OSError as exc:
            _log(f"cannot set the executable mode of {path}: {exc}")


def _deploy_release(
    version: str,
    url: str,
    gui_binary_name: str,
    timeout: float,
) -> tuple[bool, str]:
    """Download, unpack, probe and activate one release; (deployed, detail).

    The probe runs on the unpacked tree before the link is switched, so an
    unusable tree never becomes the active one. Every failing step answers
    False with the reason, and the caller turns that reason into a warning.
    """

    try:
        archive = _download_archive(version, url, timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot download {url}: {exc}"
    work_dir = values.INSTALL_DIR / (values.EXTRACT_DIR_PREFIX + version)
    try:
        values.INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        values.INSTALL_DIR.chmod(values.INSTALL_DIR_MODE)
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir()
        command = substituted_command(
            values.ARCHIVE_EXTRACT_COMMAND,
            {"archive": str(archive), "extract_dir": str(work_dir)},
        )
        try:
            run_command(command, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot unpack {archive.name}: {exc}"
        unpacked = [entry for entry in work_dir.iterdir() if entry.is_dir()]
        if len(unpacked) != 1:
            return False, (
                f"{archive.name} does not carry exactly one top directory, "
                f"found {len(unpacked)}"
            )
        source = unpacked[0]
        if not _tree_is_complete_at(source, version, gui_binary_name):
            return False, f"{archive.name} does not carry a complete Ventoy tree"
        probe_error = _probe_tree(source, version, timeout)
        if probe_error is not None:
            return False, probe_error
        target = _version_dir(version)
        try:
            if target.exists() or target.is_symlink():
                _log(_move_aside(target))
            os.replace(source, target)
        except OSError as exc:
            return False, f"cannot install into {target}: {exc}"
        _mark_executables(target, gui_binary_name)
        try:
            _switch_current_link(target)
        except OSError as exc:
            return False, f"cannot point {values.CURRENT_LINK_PATH} at {target}: {exc}"
        return True, f"installed Ventoy {version} from the GitHub release"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_desktop_entry(ctx: Context, gui_binary_name: str) -> tuple[bool, str | None]:
    """Deploy the menu entry that starts the graphical launcher; (changed, error).

    The entry runs the launcher through the current link as the desktop user
    and lets the launcher raise its own privileges, which is the flow upstream
    ships: pkexec alone in the entry would start the window without a display.
    An entry that already holds the rendered text is left alone.
    """

    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name) / values.DESKTOP_TEMPLATE_FILE_NAME
    )
    if not template_path.is_file():
        return False, f"missing the menu entry template: {template_path}"
    try:
        content = Template(template_path.read_text(encoding="utf-8")).substitute(
            gui_path=str(values.CURRENT_LINK_PATH / gui_binary_name),
            icon_path=str(values.CURRENT_LINK_PATH / values.ICON_RELATIVE_PATH),
        )
    except (OSError, KeyError) as exc:
        return False, f"cannot render {template_path}: {exc}"
    target = values.DESKTOP_ENTRY_PATH
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(values.DESKTOP_ENTRY_MODE)
    except OSError as exc:
        return False, f"cannot write {target}: {exc}"
    return True, None


def _toolkit_warning(timeout: float) -> str | None:
    """The warning of a machine that cannot show the window, or None.

    The graphical launcher of the release is a GTK or a Qt program, and the
    launcher itself picks the flavour the linker cache offers. A cache without
    either library leaves a menu entry that opens nothing, so the task says so
    instead of reporting a finished installation.
    """

    try:
        result = run_command(
            list(values.LINKER_CACHE_QUERY_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot read the library cache: {exc}"
    if result.returncode != 0:
        return f"cannot read the library cache: exit {result.returncode}"
    if any(name in result.stdout for name in values.TOOLKIT_LIBRARY_NAMES):
        return None
    return (
        "the linker cache holds neither "
        + " nor ".join(values.TOOLKIT_LIBRARY_NAMES)
        + ", so the Ventoy window will not start"
    )


def task(ctx: Context) -> TaskResult:
    """Install the newest Ventoy release and its menu entry.

    The goal is reached when the current link names a complete tree of the
    newest release and the menu entry starts that tree; a rerun then changes
    nothing and downloads nothing. Otherwise the release is downloaded,
    unpacked, probed and activated, the menu entry is written and a machine
    that cannot show the window is reported as a warning. A release the
    GitHub API does not answer keeps an installed tree untouched and is a
    warning of its own (docs/spec/ventoy-install.md).
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
        return TaskResult(
            success=True,
            message="the ventoy_install values are not declared, nothing was changed",
            warnings=(
                "the ventoy_install values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    try:
        architecture = dpkg_architecture(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return warned_result(
            changed=False,
            message="Ventoy was not installed",
            warnings=[f"cannot determine the machine architecture: {exc}"],
        )
    asset_arch = release_asset_architecture(
        engine_values.RELEASE_ASSET_ARCHITECTURES, architecture
    )
    gui_binary_name = _gui_binary_name(asset_arch)
    installed = _installed_version(gui_binary_name)

    version = ""
    asset_url = ""
    release_error = ""
    try:
        release = fetch_latest_release(values.GITHUB_REPO)
        tag = release_tag(release)
        assets = dict(asset_name_urls(release))
    except RuntimeError as exc:
        release_error = str(exc)
    else:
        version = tag.removeprefix(values.RELEASE_TAG_PREFIX)
        asset_name = values.ARCHIVE_NAME_TEMPLATE.format(version=version)
        if asset_name not in assets:
            release_error = f"release {tag} carries no {asset_name} asset"
        else:
            asset_url = assets[asset_name]
        _log(f"newest Ventoy release is {tag}, installed: {installed or 'none'}")

    if release_error:
        if installed is not None:
            warnings.append(f"the GitHub release is unavailable: {release_error}")
            messages.append(f"keeping the installed Ventoy {installed}")
        else:
            return warned_result(
                changed=False,
                message="Ventoy was not installed",
                warnings=[f"the GitHub release is unavailable: {release_error}"],
            )
    elif installed == version and not force:
        messages.append(f"already installed Ventoy {version}")
    else:
        _log(f"installing Ventoy {version} from the GitHub release")
        deployed, detail = _deploy_release(version, asset_url, gui_binary_name, timeout)
        if deployed:
            changed = True
            messages.append(detail)
            try:
                discard_downloaded_files(ctx, values.DOWNLOAD_DIR)
            except OSError as exc:
                warnings.append(f"cannot remove downloaded files: {exc}")
        else:
            warnings.append(detail)
            if installed is not None:
                messages.append(f"keeping the installed Ventoy {installed}")
            else:
                messages.append("Ventoy was not installed")

    if _installed_version(gui_binary_name) is None:
        warnings.append(
            "no complete Ventoy tree is active, so no menu entry was written"
        )
    else:
        written, error = _write_desktop_entry(ctx, gui_binary_name)
        if error is not None:
            warnings.append(error)
        elif written:
            changed = True
            messages.append(f"wrote the menu entry {values.DESKTOP_ENTRY_PATH}")

    toolkit = _toolkit_warning(timeout)
    if toolkit is not None:
        warnings.append(toolkit)

    if not messages:
        messages.append("already configured")
    return warned_result(
        changed=changed, message="; ".join(messages), warnings=warnings
    )
