"""Task telegram_setup: install the latest Telegram Desktop for the desktop user.

The described goal is a Telegram Desktop that the desktop user launches
from the application menu and that keeps itself updated. The task installs
the official static Linux build, the only build with the built-in
auto-update enabled: the download link configured as latest_url answers a
redirect to the archive of the newest release, so the redirect is the
single source of the latest release and no version list is tracked
anywhere. The archive of the installed release stays in the root
download_dir under the name the redirect gave it, and that name doubles as
the idempotency record: a rerun whose cached archive name matches the
redirect target and whose Telegram binary and launcher entry are present
changes nothing, so a current install is never downloaded again. When the
redirect points to a newer archive, the task downloads it, installs
Telegram and its Updater
into the install directory under the desktop user home (so the built-in
updater can rewrite them in place), removes the stale cached archives,
writes the launcher entry to the user applications directory and downloads
the configured icon. Auto-update is enabled by default in the official
build (docs/spec/telegram-setup.md); the user-writable install directory is
what lets the built-in Updater apply releases on its own.

Before the redirect is resolved, the download host is probed once with the
short budget of reachability_probe_timeout_seconds: a host that does not
answer within it is a blocked destination, which is reported as a warning
at once instead of spending the retry budget of the resolve on silence,
while a host that answers however slowly is resolved and downloaded with
the retry settings of the engine values.

Force mode bypasses the already-installed shortcut and reinstalls the
release the redirect points to; it never touches the user chat data, which
lives separately in the TelegramDesktop data directory.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from string import Template
from typing import NamedTuple

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    curl_command,
    discard_downloaded_files,
    download_command,
    run_command,
    substituted_command,
    task_data_dir,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import telegram_setup as values


class _InstallPaths(NamedTuple):
    """The paths the task deploys under the home of the desktop user."""

    install_dir: Path
    binary: Path
    updater: Path
    launcher: Path
    icon: Path


def _install_paths() -> _InstallPaths:
    """Compose the deployed paths from the home and the names of this section.

    One place derives the five paths from the relative paths and file names of
    the values module, so the files the task writes and the paths the launcher
    entry names can never disagree.
    """

    home = Path(common_values.DESKTOP_HOME_DIR)
    install_dir = home / values.INSTALL_DIR_RELATIVE_PATH
    return _InstallPaths(
        install_dir=install_dir,
        binary=install_dir / values.BINARY_FILE_NAME,
        updater=install_dir / values.UPDATER_FILE_NAME,
        launcher=home / values.LAUNCHER_RELATIVE_PATH,
        icon=home / values.ICON_RELATIVE_PATH,
    )


def _cache_name(url: str) -> str:
    """The cache file name of a download url: its basename, as is.

    The redirect is the single source of the archive name; nothing about
    the name or the format is assumed, because the name only keys the
    cache and tar detects the compression by itself
    (docs/spec/telegram-setup.md). A redirect that points to no usable
    archive fails naturally later, at the download or the extraction step.
    """

    return url.rstrip("/").rsplit("/", 1)[-1]


def _resolve_latest_url() -> str:
    """The download url the latest url redirect resolves to.

    A HEAD request follows the redirect chain and reports the final url
    through --write-out, so the newest release is discovered without
    downloading the archive. The command is the value of this section and the
    retry bounds are declared values, so the request settings live in the
    values. Raises RuntimeError when the request fails.
    """

    result = run_command(
        curl_command(
            values.LATEST_URL_COMMAND,
            values.LATEST_URL,
            timeout_seconds=engine_values.CURL_TIMEOUT_SECONDS,
        ),
        check=False,
        capture=True,
        timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot resolve {values.LATEST_URL}: curl exit {result.returncode}"
        )
    url = result.stdout.strip()
    if not url:
        raise RuntimeError(f"cannot resolve {values.LATEST_URL}: empty download url")
    return url


def _probe_download_host() -> tuple[bool, str]:
    """Ask the download host to answer within a few short attempts.

    A host that is blocked never answers, and the retry settings of the
    resolve below turn that silence into one connect timeout per attempt,
    which is minutes on a machine whose network drops the packets. The
    probe is the configured reachability_probe_command without any retry
    flag, so it costs its budget once, and it answers whether the host is
    reachable at all: a slow but working link answers inside that budget
    and the resolve then retries as usual (docs/spec/telegram-setup.md).
    Returns (reachable, reason), and reason is empty when the host
    answered.
    """

    command = substituted_command(
        values.REACHABILITY_PROBE_COMMAND,
        {"timeout_seconds": str(values.REACHABILITY_PROBE_TIMEOUT_SECONDS)},
    )
    last_exit = 0
    for attempt in range(1, values.REACHABILITY_PROBE_ATTEMPTS + 1):
        try:
            result = run_command(
                [*command, values.LATEST_URL],
                check=False,
                capture=True,
                timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, (
                f"cannot resolve {values.LATEST_URL}: the reachability probe "
                f"could not be run: {exc}"
            )
        if result.returncode == 0:
            return True, ""
        last_exit = result.returncode
        if attempt < values.REACHABILITY_PROBE_ATTEMPTS:
            time.sleep(values.REACHABILITY_PROBE_PAUSE_SECONDS)
    return False, (
        f"cannot resolve {values.LATEST_URL}: the host did not answer within "
        f"{values.REACHABILITY_PROBE_TIMEOUT_SECONDS} s in "
        f"{values.REACHABILITY_PROBE_ATTEMPTS} attempts (last curl exit "
        f"{last_exit}), so the download is skipped instead of retrying for "
        f"up to {engine_values.CURL_RETRY_MAX_TIME_SECONDS} s"
    )


def _download_archive(
    url: str,
    name: str,
) -> None:
    """Download the archive into the cache directory under its final name.

    The download goes to a sibling file with the declared suffix first and
    is renamed only after a successful transfer, so a cached archive name
    always means a complete archive. Raises RuntimeError on failure.
    """

    values.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    partial = values.DOWNLOAD_DIR / (name + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX)
    try:
        run_command(
            download_command(partial, url),
            timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"cannot download {url}: {exc}") from None
    partial.replace(values.DOWNLOAD_DIR / name)


def _own_to_user(username: str, path: Path) -> None:
    """Chown path to username when running as root.

    The provisioning engine runs as root, so files and directories created
    under the user home must belong to the desktop user: the built-in
    Telegram updater rewrites them on self-update. A non-root test run and
    an unknown configured user leave the ownership untouched.
    """

    if os.geteuid() != 0:
        return
    try:
        import pwd

        entry = pwd.getpwnam(username)
    except KeyError:
        return
    os.chown(path, entry.pw_uid, entry.pw_gid)


def _install_archive(archive: Path, timeout: float) -> None:
    """Install the Telegram and Updater files from the archive.

    The archive is extracted to a temporary directory named by the value of
    this section, then each configured binary is copied from the directory of
    the archive into the install directory under the user home when it differs
    from what is already there, and the install directory and its files are
    owned by the desktop user. Raises RuntimeError on any failure.
    """

    paths = _install_paths()
    extract_dir = Path(tempfile.mkdtemp(prefix=values.EXTRACT_DIR_PREFIX))
    try:
        run_command(
            substituted_command(
                values.TAR_EXTRACT_COMMAND,
                {"archive": str(archive), "extract_dir": str(extract_dir)},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise RuntimeError(f"cannot extract {archive.name}: {exc}") from None
    try:
        paths.install_dir.mkdir(parents=True, exist_ok=True)
        _own_to_user(common_values.DESKTOP_USERNAME, paths.install_dir)
        for target in (paths.binary, paths.updater):
            source = extract_dir / values.ARCHIVE_DIRECTORY_NAME / target.name
            if not source.is_file():
                raise RuntimeError(f"archive {archive.name} contains no {target.name}")
            if not (target.is_file() and filecmp.cmp(source, target, shallow=False)):
                shutil.copyfile(source, target)
            target.chmod(common_values.EXECUTABLE_FILE_MODE)
            _own_to_user(common_values.DESKTOP_USERNAME, target)
    except OSError as exc:
        raise RuntimeError(
            f"cannot install Telegram into {paths.install_dir}: {exc}"
        ) from None
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _cleanup_old_archives(download_dir: Path, current_name: str) -> None:
    """Remove every cached file except the current archive.

    The cache directory holds only Telegram archives, so every file whose
    name differs from the current one is a stale release from an earlier
    scheme or a leftover partial download.
    """

    for stale in download_dir.iterdir():
        if stale.is_file() and stale.name != current_name:
            stale.unlink(missing_ok=True)


def _desktop_content(template_path: Path) -> str:
    """The launcher entry that starts Telegram from the application menu.

    The body of the entry lives in the template under task_data/ and the two
    paths are substituted from the home of the desktop user, so the entry text
    stays with the template and the paths cannot drift from the files the task
    writes.
    """

    paths = _install_paths()
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(binary=paths.binary, icon=paths.icon)


def _ensure_launcher(template_path: Path) -> tuple[bool, str | None]:
    """Write the launcher entry; return (changed, error)."""

    launcher = _install_paths().launcher
    content = _desktop_content(template_path)
    try:
        if launcher.is_file() and launcher.read_text(encoding="utf-8") == content:
            return False, None
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(content, encoding="utf-8")
        launcher.chmod(common_values.LAUNCHER_FILE_MODE)
        _own_to_user(common_values.DESKTOP_USERNAME, launcher)
        _own_to_user(common_values.DESKTOP_USERNAME, launcher.parent)
    except OSError as exc:
        return False, f"cannot write the Telegram desktop entry: {exc}"
    return True, None


def _ensure_icon() -> tuple[bool, str | None]:
    """Download the icon of this section when missing; return (changed, error).

    A failed icon download is a warning, never a fatal error: the launcher
    entry still starts Telegram and only the icon stays generic until the
    next run retries.
    """

    path = _install_paths().icon
    if path.is_file() and path.stat().st_size > 0:
        return False, None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            download_command(path, values.ICON_URL),
            timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
        )
        path.chmod(values.ICON_FILE_MODE)
        _own_to_user(common_values.DESKTOP_USERNAME, path)
        _own_to_user(common_values.DESKTOP_USERNAME, path.parent)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        path.unlink(missing_ok=True)
        return False, f"cannot download the Telegram icon: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install the latest Telegram Desktop and its launcher entry.

    The target state is reached when the archive the redirect points to is
    cached, the Telegram binary and the launcher entry are present; the
    task then returns changed=False. Otherwise it probes the download host
    once with a short budget, resolves the latest archive from the redirect
    when that host answered, downloads it when not cached, installs the
    Telegram and Updater files under the user home, removes stale cached
    archives, writes the launcher entry and downloads the icon. Force mode
    reinstalls the current release instead of trusting the cached archive.
    A step that cannot run is reported as a warning of a completed task: a
    host that does not answer within the probe budget is reported at once
    and its retry budget is never spent, a release that cannot be resolved
    and a failed download skip the install, and the launcher entry and the
    icon are still handled, so the runner continues with the remaining
    tasks and never stops here.
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
            message="the telegram_setup values are not declared, nothing was changed",
            warnings=(
                "the telegram_setup values are not declared: " + ", ".join(absent),
            ),
        )
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []
    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name) / values.LAUNCHER_TEMPLATE_FILE_NAME
    )
    paths = _install_paths()

    url: str | None = None
    reachable, probe_warning = _probe_download_host()
    if not reachable:
        _log(probe_warning)
        warnings.append(probe_warning)
    else:
        try:
            url = _resolve_latest_url()
        except RuntimeError as exc:
            warnings.append(str(exc))
    name = _cache_name(url) if url is not None else ""
    if url is not None:
        _log(f"checking the latest Telegram Desktop release: {name}")

    archive = values.DOWNLOAD_DIR / name if name else None
    already_latest = (
        not force
        and archive is not None
        and archive.is_file()
        and paths.binary.is_file()
        and paths.launcher.is_file()
    )

    if url is None:
        _log("skipping the Telegram Desktop download: no release resolved")
    elif already_latest:
        _log(f"latest Telegram Desktop release {name} is already installed")
    else:
        installed = archive is not None and archive.is_file()
        if not installed:
            _log(f"downloading Telegram Desktop release {name}")
            try:
                _download_archive(url, name)
            except RuntimeError as exc:
                warnings.append(str(exc))
            else:
                installed = True
        if installed and archive is not None:
            _log(f"installing Telegram Desktop release {name}")
            try:
                _install_archive(archive, engine_values.COMMAND_TIMEOUT_SECONDS)
            except RuntimeError as exc:
                warnings.append(str(exc))
            else:
                try:
                    discard_downloaded_files(ctx, values.DOWNLOAD_DIR)
                except OSError as exc:
                    warnings.append(f"cannot remove downloaded files: {exc}")
                _cleanup_old_archives(values.DOWNLOAD_DIR, name)
                messages.append(f"installed Telegram Desktop {name}")
                changed = True

    launcher_changed, launcher_error = _ensure_launcher(template_path)
    if launcher_error:
        warnings.append(launcher_error)
    if launcher_changed:
        messages.append(f"wrote the Telegram launcher entry to {paths.launcher}")
        changed = True

    icon_changed, icon_error = _ensure_icon()
    if icon_error:
        warnings.append(icon_error)
    if icon_changed:
        messages.append(f"downloaded the Telegram icon to {paths.icon}")
        changed = True

    if not messages:
        messages.append("already installed the latest release")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
