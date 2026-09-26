"""Task scrcpy_setup: install the scrcpy mirroring client for the desktop user.

The described goal is a scrcpy that the desktop user starts from the KDE
menu and points at an Android device over USB. The newest release of the
configured GitHub repository is the primary source: the release archive is
self-contained (the client, the server pushed to the device and its own
adb), so no system dependency is needed and the installed version is the
newest one. The archive is verified against the checksum file published in
the same release, unpacked into a directory named after its version, and the
command in the user prefix is a symbolic link that is switched only after
the delivered client answered its version query, so an unusable tree never
replaces a working one. The client and the server must carry the exact same
version, which is why the whole archive is deployed as one unit and never
file by file.

The Ubuntu archive is the fallback, not a second primary source: it carries
the distribution build and receives its updates through the regular apt
upgrade. The fallback runs when the release path is unavailable, that is a
failed release query, no asset for the machine architecture, a failed
download or extraction, or a client that does not answer. A checksum
mismatch is not an availability failure: the download is retried once and a
repeated mismatch keeps the current installation and is reported, because an
integrity alarm must never downgrade or replace a machine.

Both sources need the Android USB rules of the Ubuntu archive: the release
archive carries no udev rules, and without them a desktop user cannot reach
a device over the cable, so the rules package is installed when it is
missing and its failure is reported with what will not work.

Every step is reported to stdout, and a step that cannot run is a warning of
a completed task, so the runner continues with the remaining tasks and never
stops here (docs/spec/scrcpy-setup.md).
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path
from string import Template
from typing import NamedTuple

from pyntara.context import Context
from pyntara.github_release import asset_name_urls, fetch_latest_release, release_tag
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import failure_detail, install_missing_packages
from pyntara.utils import (
    discard_downloaded_files,
    download_command,
    dpkg_architecture,
    release_asset_architecture,
    run_command,
    substituted_command,
    task_data_dir,
    trim_whitespace,
    version_without_tag_prefix,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import scrcpy_setup as values


class _ReleaseOutcome(NamedTuple):
    """What the release path did.

    installed means the new version is in place and the command points at
    it. fall_back means the Ubuntu archive may be used instead. Both are
    False for an integrity failure, which keeps the machine as it is,
    because an integrity alarm must never replace an installation.
    """

    version: str
    detail: str
    installed: bool
    fall_back: bool


def _home() -> Path:
    """The home directory of the desktop user."""

    return Path(common_values.DESKTOP_HOME_DIR)


def _install_dir() -> Path:
    """The directory that holds one subdirectory per installed release."""

    return _home() / values.INSTALL_DIR_RELATIVE_PATH


def _command_path() -> Path:
    """The symbolic link the desktop user starts."""

    return _home() / values.COMMAND_RELATIVE_PATH


def _version_dir(version: str) -> Path:
    """The directory of one installed release version."""

    return _install_dir() / version


def _client_path(version: str) -> Path:
    """The client binary inside one installed release version."""

    return _version_dir(version) / values.BINARY_FILE_NAME


def _tree_is_complete(version: str) -> bool:
    """True when the version directory carries every file of one release.

    The client, the server it pushes to the device and its own adb are the
    three files the release archive delivers; a directory without all three
    is a half finished install and is never treated as usable.
    """

    tree = _version_dir(version)
    return all(
        (tree / name).is_file()
        for name in (
            values.BINARY_FILE_NAME,
            values.SERVER_FILE_NAME,
            values.ADB_FILE_NAME,
        )
    )


def _installed_release_version() -> str | None:
    """The version of the installed release, read from the symbolic link.

    The link target is <install dir>/<version>/<client>, so the parent
    directory name is the installed version. A missing link, a plain file
    in its place or an unreadable link answers None, which stands for no
    release install; the version is read this way, and not by running the
    client, so the decision about what to download never depends on the
    installed client being able to answer.
    """

    link = _command_path()
    try:
        if not link.is_symlink():
            return None
        target = os.readlink(link)
    except OSError:
        return None
    return Path(target).parent.name or None


def _own_to_user(
    path: Path,
    *,
    recursive: bool = False,
    follow_symlinks: bool = True,
) -> None:
    """Give a deployed path to the desktop user.

    The provisioning engine runs as root, so everything written into the
    user home must belong to that user; a non-root test run and an unknown
    configured user leave the ownership untouched (telegram_setup follows
    the same rule).
    """

    if os.geteuid() != 0:
        return
    try:
        record = pwd.getpwnam(common_values.DESKTOP_USERNAME)
    except KeyError:
        return
    try:
        if recursive:
            for current, _dirs, files in os.walk(path):
                os.chown(current, record.pw_uid, record.pw_gid)
                for name in files:
                    os.chown(Path(current) / name, record.pw_uid, record.pw_gid)
        os.chown(
            path,
            record.pw_uid,
            record.pw_gid,
            follow_symlinks=follow_symlinks,
        )
    except OSError as exc:
        _log(f"cannot set the owner of {path}: {exc}")


def _move_to_trash(path: Path) -> str:
    """Move a superseded path into the user trash; never deletes it.

    A name that is already taken in the trash gets a counter, so nothing is
    overwritten. A failed move is reported and leaves the path where it is.
    """

    trash_dir = _home() / values.TRASH_DIR_RELATIVE_PATH
    target = trash_dir / path.name
    counter = 2
    while target.exists():
        target = trash_dir / f"{path.name}-{counter}"
        counter += 1
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    except OSError as exc:
        return f"cannot move {path} into the trash: {exc}"
    _own_to_user(target, recursive=True)
    return f"moved {path.name} into the trash"


def _probe_client_answer(client: Path, timeout: float) -> str | None:
    """The words the delivered client answers with, or None when it does not answer.

    The probe is the acceptance check of the release path, so it asks the
    client whether it runs at all and takes its own first line as the
    answer. The version is not parsed out of it: scrcpy prints a two part
    version (4.1), which the shared three part parser does not read, and the
    version the task compares is read from the symbolic link instead. A
    missing binary, a nonzero exit and a timeout all answer None.
    """

    command = substituted_command(values.VERSION_COMMAND, {"binary": str(client)})
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if trim_whitespace(line):
            return trim_whitespace(line)
    return None


def _download_into_cache(
    name: str,
    url: str,
    timeout: float,
) -> Path:
    """Download a release file into the cache; raises RuntimeError on failure.

    The download goes to a sibling with the declared partial suffix and is
    renamed only after a successful transfer, so a file in the cache is
    always a complete one.
    """

    values.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    partial = values.DOWNLOAD_DIR / (name + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX)
    try:
        run_command(download_command(partial, url), timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"cannot download {url}: {exc}") from None
    target = values.DOWNLOAD_DIR / name
    partial.replace(target)
    return target


def _printed_digest(path: Path, timeout: float) -> str | None:
    """The digest the configured checksum command prints for a file, or None.

    The command prints "<digest>  <name>"; only the digest is read, because
    the name is resolved by the caller from the published checksum file.
    """

    command = substituted_command(values.CHECKSUM_COMMAND, {"file": str(path)})
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    fields = trim_whitespace(result.stdout).split()
    return fields[0].casefold() if fields else None


def _published_digest(text: str, file_name: str) -> str | None:
    """The digest the published checksum file gives for one file, or None.

    A checksum file lists its own files and may carry others of the same
    release, so the line is selected by the name in its last column, which
    sha256sum writes with a possible binary mode marker.
    """

    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and Path(fields[-1].lstrip("*")).name == file_name:
            return fields[0].casefold()
    return None


def _verify_archive(
    archive: Path,
    checksum_file: Path,
    timeout: float,
) -> tuple[bool, str]:
    """Check the downloaded archive against the published checksum; (ok, note)."""

    try:
        published = _published_digest(
            checksum_file.read_text(encoding="utf-8"), archive.name
        )
    except OSError as exc:
        return False, f"cannot read {checksum_file}: {exc}"
    if published is None:
        return False, f"{checksum_file.name} carries no line for {archive.name}"
    printed = _printed_digest(archive, timeout)
    if printed is None:
        return False, f"cannot read the digest of {archive.name}"
    if printed != published:
        return False, (
            f"the digest of {archive.name} is {printed}, "
            f"the published one is {published}"
        )
    return True, f"{archive.name} matches the published digest"


def _extract_tree(archive: Path, timeout: float) -> tuple[Path, Path]:
    """Unpack the archive; return (the release tree, the temporary directory).

    The directory inside the archive is discovered instead of assumed: a
    release whose top directory is renamed still installs. The temporary
    directory is returned so the caller removes it after the tree is moved.
    Raises RuntimeError when the archive cannot be unpacked.
    """

    work_dir = Path(tempfile.mkdtemp(prefix=values.EXTRACT_DIR_PREFIX))
    try:
        run_command(
            substituted_command(
                values.ARCHIVE_EXTRACT_COMMAND,
                {"archive": str(archive), "extract_dir": str(work_dir)},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(f"cannot unpack {archive.name}: {exc}") from None
    entries = sorted(work_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0], work_dir
    return work_dir, work_dir


def _switch_command_link(client: Path) -> None:
    """Point the user command at a client, replacing the previous link atomically.

    The new link is created next to the old one and renamed over it, so a
    reader never sees a missing command, and the link belongs to the desktop
    user like the rest of the install.
    """

    link = _command_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    staged = link.parent / (link.name + ".staged")
    staged.unlink(missing_ok=True)
    os.symlink(str(client), staged)
    os.replace(staged, link)
    _own_to_user(link, follow_symlinks=False)


def _deploy_release(
    asset_name: str,
    asset_url: str,
    checksum_url: str,
    version: str,
    timeout: float,
) -> _ReleaseOutcome:
    """Download, verify, unpack and switch to one release; never raises.

    The checksum mismatch retries the download once and then reports the
    release as untrusted, which the caller answers by changing nothing. Every
    other failure before the switch is reported as unavailable, so the caller
    can take the Ubuntu archive. The client is probed before the link is
    switched, so a tree that cannot run never replaces a working install.
    """

    note = ""
    for _attempt in range(2):
        try:
            archive = _download_into_cache(asset_name, asset_url, timeout)
            checksum_file = _download_into_cache(
                values.CHECKSUM_FILE_NAME, checksum_url, timeout
            )
        except RuntimeError as exc:
            return _ReleaseOutcome(version, str(exc), installed=False, fall_back=True)
        verified, note = _verify_archive(archive, checksum_file, timeout)
        if verified:
            break
    else:
        return _ReleaseOutcome(version, note, installed=False, fall_back=False)

    try:
        tree, work_dir = _extract_tree(archive, timeout)
    except RuntimeError as exc:
        return _ReleaseOutcome(version, str(exc), installed=False, fall_back=True)
    target = _version_dir(version)
    try:
        _install_dir().mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            _log(_move_to_trash(target))
        shutil.move(str(tree), str(target))
    except OSError as exc:
        return _ReleaseOutcome(
            version,
            f"cannot install into {target}: {exc}",
            installed=False,
            fall_back=True,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    for name in (values.BINARY_FILE_NAME, values.ADB_FILE_NAME):
        path = target / name
        try:
            if path.is_file():
                path.chmod(common_values.EXECUTABLE_FILE_MODE)
        except OSError as exc:
            _log(f"cannot set the mode of {path}: {exc}")
    _own_to_user(target, recursive=True)
    # The install directory is created above when it was missing, and it
    # belongs to the desktop user like the tree inside it.
    _own_to_user(_install_dir())

    client = target / values.BINARY_FILE_NAME
    answer = _probe_client_answer(client, timeout)
    if answer is None:
        return _ReleaseOutcome(
            version,
            f"the client of {asset_name} does not answer "
            f"{' '.join(values.VERSION_COMMAND)}; {_move_to_trash(target)}",
            installed=False,
            fall_back=True,
        )

    previous = _installed_release_version()
    try:
        _switch_command_link(client)
    except OSError as exc:
        return _ReleaseOutcome(
            version,
            f"cannot point {values.COMMAND_RELATIVE_PATH} at {client}: {exc}",
            installed=False,
            fall_back=True,
        )
    if previous and previous != version:
        superseded = _version_dir(previous)
        if superseded.exists():
            _log(_move_to_trash(superseded))
    return _ReleaseOutcome(
        version,
        f"installed scrcpy {version} from the GitHub release, "
        f"the client answers: {answer}",
        installed=True,
        fall_back=False,
    )


def _install_from_apt(ctx: Context, warnings: list[str]) -> tuple[bool, str]:
    """Install the fallback packages; return (installed something, note)."""

    missing, installed, failures, apt_warnings = install_missing_packages(
        ctx, values.FALLBACK_PACKAGES
    )
    if not missing:
        return False, "the Ubuntu archive client is installed already"
    warnings.extend(apt_warnings)
    if failures:
        detail = failure_detail(failures)
        return False, f"the Ubuntu archive packages did not install: {detail}"
    return True, f"installed from the Ubuntu archive: {', '.join(installed)}"


def _ensure_udev_rules(ctx: Context, warnings: list[str]) -> bool:
    """Install the Android USB rules when they are missing; True when changed.

    The release archive carries no udev rules, so both sources need this
    package for a desktop user to reach a device over the cable. A failure is
    reported with what will not work instead of stopping the task.
    """

    missing, installed, failures, apt_warnings = install_missing_packages(
        ctx, [values.UDEV_RULES_PACKAGE_NAME]
    )
    if not missing:
        return False
    warnings.extend(apt_warnings)
    if failures:
        detail = failure_detail(failures)
        warnings.append(
            f"cannot install {values.UDEV_RULES_PACKAGE_NAME}: {detail}; a device "
            "connected over the cable may stay unreachable for the desktop user"
        )
        return False
    return bool(installed)


def _launcher_binary_and_icon() -> tuple[Path | None, str | None]:
    """The client and the icon the menu entry starts, or (None, None).

    The installed release wins because it is the primary source; without it
    the client of the Ubuntu archive is used, with the icon name the desktop
    resolves through its theme, because the distribution places the icon in
    the system theme and not in an install directory.
    """

    version = _installed_release_version()
    if version and _tree_is_complete(version):
        tree = _version_dir(version)
        return tree / values.BINARY_FILE_NAME, str(tree / values.ICON_FILE_NAME)
    apt_binary = Path(values.APT_BINARY_PATH)
    if apt_binary.is_file():
        return apt_binary, values.THEME_ICON_NAME
    return None, None


def _write_launcher(
    template_path: Path,
    target: Path,
    client: Path,
    icon: str | None,
) -> tuple[bool, str | None]:
    """Render and deploy one menu entry; return (changed, error).

    The template carries the entry text, the task fills the two paths in, so
    the wording lives in task_data/ and only the machine paths live here. An
    entry that already holds the rendered text is left alone.
    """

    try:
        content = Template(template_path.read_text(encoding="utf-8")).substitute(
            binary=str(client), icon=icon or values.THEME_ICON_NAME
        )
    except (OSError, KeyError) as exc:
        return False, f"cannot render {template_path}: {exc}"
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(common_values.LAUNCHER_FILE_MODE)
        _own_to_user(target)
        _own_to_user(target.parent)
    except OSError as exc:
        return False, f"cannot write {target}: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install scrcpy for the desktop user from the release, or from the archive.

    The goal is reached when a client is installed, the menu entry starts it
    and the Android USB rules are installed; a rerun whose installed release
    version equals the newest release tag changes nothing. Otherwise the
    newest release is downloaded, verified, unpacked, probed and switched to;
    a release path that is unavailable falls back to the Ubuntu archive, and
    a checksum mismatch keeps the machine as it is. Every step is reported to
    stdout and a step that cannot run becomes a warning of a completed task.
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
            message="the scrcpy_setup values are not declared, nothing was changed",
            warnings=(
                "the scrcpy_setup values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    arch = ""
    release_error = ""
    try:
        arch = dpkg_architecture(timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        release_error = f"cannot determine the machine architecture: {exc}"
        warnings.append(release_error)

    tag = ""
    asset_name = ""
    asset_url = ""
    checksum_url = ""
    if not release_error:
        try:
            release = fetch_latest_release(values.GITHUB_REPO)
            tag = release_tag(release)
            assets = dict(asset_name_urls(release))
        except RuntimeError as exc:
            release_error = str(exc)
        else:
            asset_arch = release_asset_architecture(
                engine_values.RELEASE_ASSET_ARCHITECTURES, arch
            )
            asset_name = values.ARCHIVE_NAME_TEMPLATE.format(
                asset_arch=asset_arch, release_tag=tag
            )
            if asset_name not in assets:
                release_error = f"release {tag} carries no {asset_name} asset"
            elif values.CHECKSUM_FILE_NAME not in assets:
                release_error = (
                    f"release {tag} carries no {values.CHECKSUM_FILE_NAME} asset, "
                    "so the archive cannot be verified"
                )
            else:
                asset_url = assets[asset_name]
                checksum_url = assets[values.CHECKSUM_FILE_NAME]
    version = version_without_tag_prefix(tag) if tag else ""
    installed = _installed_release_version()
    complete = installed is not None and _tree_is_complete(installed)
    if tag:
        _log(f"checking latest release: {tag}")

    if not release_error and complete and installed == version and not force:
        messages.append(f"already installed scrcpy {version} from the GitHub release")
    elif not release_error:
        _log(f"installing scrcpy {version} from the GitHub release")
        outcome = _deploy_release(asset_name, asset_url, checksum_url, version, timeout)
        if outcome.installed:
            changed = True
            try:
                discard_downloaded_files(ctx, values.DOWNLOAD_DIR)
            except OSError as exc:
                warnings.append(f"cannot remove downloaded files: {exc}")
            messages.append(outcome.detail)
        elif not outcome.fall_back:
            warnings.append(f"the release archive was not used: {outcome.detail}")
        else:
            release_error = outcome.detail

    if release_error:
        if complete:
            warnings.append(f"the GitHub release is unavailable: {release_error}")
            messages.append(f"keeping the installed scrcpy {installed}")
        else:
            _log(f"the GitHub release is unavailable: {release_error}")
            apt_changed, apt_note = _install_from_apt(ctx, warnings)
            changed = changed or apt_changed
            messages.append(apt_note)
            apt_binary = Path(values.APT_BINARY_PATH)
            if not apt_binary.is_file():
                warnings.append(
                    f"no scrcpy client at {apt_binary} after the Ubuntu archive install"
                )
            elif _probe_client_answer(apt_binary, timeout) is None:
                warnings.append(
                    f"the client from the Ubuntu archive does not answer "
                    f"{' '.join(values.VERSION_COMMAND)}"
                )
            else:
                messages.append("the Ubuntu archive client answers")

    if _ensure_udev_rules(ctx, warnings):
        changed = True
        messages.append(f"installed {values.UDEV_RULES_PACKAGE_NAME}")

    client, icon = _launcher_binary_and_icon()
    if client is None:
        warnings.append("no scrcpy client to start, so no menu entry was written")
    else:
        data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
        for template_name, relative_path in (
            (values.LAUNCHER_TEMPLATE_FILE_NAME, values.LAUNCHER_RELATIVE_PATH),
            (
                values.CONSOLE_LAUNCHER_TEMPLATE_FILE_NAME,
                values.CONSOLE_LAUNCHER_RELATIVE_PATH,
            ),
        ):
            target = _home() / relative_path
            written, error = _write_launcher(
                data_dir / template_name, target, client, icon
            )
            if error:
                warnings.append(error)
            elif written:
                changed = True
                messages.append(f"wrote the menu entry {target}")

    if not messages:
        messages.append("already configured")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
