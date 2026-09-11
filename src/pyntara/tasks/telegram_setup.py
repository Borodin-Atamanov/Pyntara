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
from pathlib import Path
from string import Template
from typing import NamedTuple

from pyntara.config import TelegramSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    CURL_DOWNLOAD_WRITE_OUT,
    curl_flags,
    run_command,
    task_data_dir,
)


class _InstallPaths(NamedTuple):
    """The paths the task deploys under the home of the desktop user."""

    install_dir: Path
    binary: Path
    updater: Path
    launcher: Path
    icon: Path


def _install_paths(cfg: TelegramSetupConfig) -> _InstallPaths:
    """Compose the deployed paths from the configured home and names.

    One place derives the five paths from the relative paths and file
    names of the section, so the files the task writes and the paths the
    launcher entry names can never disagree.
    """

    home = Path(cfg.home_dir)
    install_dir = home / cfg.install_dir_relative_path
    return _InstallPaths(
        install_dir=install_dir,
        binary=install_dir / cfg.binary_file_name,
        updater=install_dir / cfg.updater_file_name,
        launcher=home / cfg.launcher_relative_path,
        icon=home / cfg.icon_relative_path,
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


def _resolve_latest_url(
    latest_url: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
    retry_delay: int,
) -> str:
    """The download url the latest_url redirect resolves to.

    A HEAD request follows the redirect chain and reports the final url
    through --write-out, so the newest release is discovered without
    downloading the archive. Raises RuntimeError when the request fails.
    """

    result = run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--head",
            "--location",
            "--output",
            "/dev/null",
            "--write-out",
            "%{url_effective}",
            *curl_flags(
                curl_timeout, retries, connect_timeout, retry_max_time, retry_delay
            ),
            latest_url,
        ],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot resolve {latest_url}: curl exit {result.returncode}"
        )
    url = result.stdout.strip()
    if not url:
        raise RuntimeError(f"cannot resolve {latest_url}: empty download url")
    return url


def _download_archive(
    cfg: TelegramSetupConfig,
    url: str,
    name: str,
    timeout: float,
    download_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
    retry_delay: int,
) -> None:
    """Download the archive into download_dir under its final name.

    The download goes to a sibling file with the configured suffix first
    and is renamed only after a successful transfer, so a cached archive
    name always means a complete archive. Raises RuntimeError on failure.
    """

    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    partial = cfg.download_dir / (name + cfg.partial_download_file_suffix)
    try:
        run_command(
            [
                "curl",
                "--fail",
                "--location",
                "--show-error",
                "--output",
                str(partial),
                "--write-out",
                CURL_DOWNLOAD_WRITE_OUT,
                *curl_flags(
                    download_timeout,
                    retries,
                    connect_timeout,
                    retry_max_time,
                    retry_delay,
                ),
                url,
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"cannot download {url}: {exc}") from None
    partial.replace(cfg.download_dir / name)


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


def _install_archive(cfg: TelegramSetupConfig, archive: Path, timeout: float) -> None:
    """Install the Telegram and Updater files from the archive.

    The archive is extracted to a temporary directory named by the
    configured prefix, then each configured binary is copied from the
    configured directory of the archive into the install directory under
    the user home when it differs from what is already there, and the
    install directory and its files are owned by the desktop user. Raises
    RuntimeError on any failure.
    """

    paths = _install_paths(cfg)
    extract_dir = Path(tempfile.mkdtemp(prefix=cfg.extract_dir_prefix))
    try:
        run_command(
            [
                "tar",
                "--extract",
                "--file",
                str(archive),
                "--directory",
                str(extract_dir),
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise RuntimeError(f"cannot extract {archive.name}: {exc}") from None
    try:
        paths.install_dir.mkdir(parents=True, exist_ok=True)
        _own_to_user(cfg.username, paths.install_dir)
        for target in (paths.binary, paths.updater):
            source = extract_dir / cfg.archive_directory_name / target.name
            if not source.is_file():
                raise RuntimeError(
                    f"archive {archive.name} contains no {target.name}"
                )
            if not (
                target.is_file()
                and filecmp.cmp(source, target, shallow=False)
            ):
                shutil.copyfile(source, target)
            target.chmod(cfg.executable_file_mode)
            _own_to_user(cfg.username, target)
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


def _desktop_content(cfg: TelegramSetupConfig, template_path: Path) -> str:
    """The launcher entry that starts Telegram from the application menu.

    The body of the entry lives in the template under task_data/ and the
    two paths are substituted from the configured home, so the entry text
    stays with the template and the paths cannot drift from the files the
    task writes.
    """

    paths = _install_paths(cfg)
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(binary=paths.binary, icon=paths.icon)


def _ensure_launcher(
    cfg: TelegramSetupConfig, template_path: Path
) -> tuple[bool, str | None]:
    """Write the launcher entry; return (changed, error)."""

    launcher = _install_paths(cfg).launcher
    content = _desktop_content(cfg, template_path)
    try:
        if launcher.is_file() and launcher.read_text(encoding="utf-8") == content:
            return False, None
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(content, encoding="utf-8")
        launcher.chmod(cfg.launcher_file_mode)
        _own_to_user(cfg.username, launcher)
        _own_to_user(cfg.username, launcher.parent)
    except OSError as exc:
        return False, f"cannot write the Telegram desktop entry: {exc}"
    return True, None


def _ensure_icon(
    cfg: TelegramSetupConfig,
    timeout: float,
    download_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
    retry_delay: int,
) -> tuple[bool, str | None]:
    """Download the configured icon when missing; return (changed, error).

    A failed icon download is a warning, never a fatal error: the launcher
    entry still starts Telegram and only the icon stays generic until the
    next run retries.
    """

    path = _install_paths(cfg).icon
    if path.is_file() and path.stat().st_size > 0:
        return False, None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "curl",
                "--fail",
                "--location",
                "--show-error",
                "--output",
                str(path),
                *curl_flags(
                    download_timeout,
                    retries,
                    connect_timeout,
                    retry_max_time,
                    retry_delay,
                ),
                cfg.icon_url,
            ],
            timeout=timeout,
        )
        path.chmod(cfg.icon_file_mode)
        _own_to_user(cfg.username, path)
        _own_to_user(cfg.username, path.parent)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        path.unlink(missing_ok=True)
        return False, f"cannot download the Telegram icon: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install the latest Telegram Desktop and its launcher entry.

    The target state is reached when the archive the redirect points to is
    cached, the Telegram binary and the launcher entry are present; the
    task then returns changed=False. Otherwise it resolves the latest
    archive from the redirect, downloads it when not cached, installs the
    Telegram and Updater files under the user home, removes stale cached
    archives, writes the launcher entry and downloads the icon. Force mode
    reinstalls the current release instead of trusting the cached archive.
    A failure is an error TaskResult, so the runner continues with the
    remaining tasks and never stops here.
    """

    cfg = ctx.config.telegram_setup
    timeout = ctx.config.engine.command_timeout_seconds
    curl_timeout = ctx.config.engine.curl_timeout_seconds
    download_timeout = ctx.config.engine.curl_download_timeout_seconds
    curl_retries = ctx.config.engine.curl_retries
    retry_delay = ctx.config.engine.curl_retry_delay_seconds
    connect_timeout = ctx.config.engine.curl_connect_timeout_seconds
    retry_max_time = ctx.config.engine.curl_retry_max_time_seconds
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []
    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / cfg.launcher_template_file_name
    )
    paths = _install_paths(cfg)

    try:
        url = _resolve_latest_url(
            cfg.latest_url,
            timeout,
            curl_timeout,
            curl_retries,
            connect_timeout,
            retry_max_time,
            retry_delay,
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    name = _cache_name(url)
    _log(f"checking the latest Telegram Desktop release: {name}")

    archive = cfg.download_dir / name
    already_latest = (
        not force
        and archive.is_file()
        and paths.binary.is_file()
        and paths.launcher.is_file()
    )

    if already_latest:
        _log(f"latest Telegram Desktop release {name} is already installed")
    else:
        if not archive.is_file():
            _log(f"downloading Telegram Desktop release {name}")
            try:
                _download_archive(
                    cfg,
                    url,
                    name,
                    timeout,
                    download_timeout,
                    curl_retries,
                    connect_timeout,
                    retry_max_time,
                    retry_delay,
                )
            except RuntimeError as exc:
                return TaskResult(success=False, changed=changed, error=str(exc))
        _log(f"installing Telegram Desktop release {name}")
        try:
            _install_archive(cfg, archive, timeout)
        except RuntimeError as exc:
            return TaskResult(success=False, changed=changed, error=str(exc))
        _cleanup_old_archives(cfg.download_dir, name)
        messages.append(f"installed Telegram Desktop {name}")
        changed = True

    launcher_changed, launcher_error = _ensure_launcher(cfg, template_path)
    if launcher_error:
        return TaskResult(success=False, changed=changed, error=launcher_error)
    if launcher_changed:
        messages.append(
            f"wrote the Telegram launcher entry to {paths.launcher}"
        )
        changed = True

    icon_changed, icon_error = _ensure_icon(
        cfg,
        timeout,
        download_timeout,
        curl_retries,
        connect_timeout,
        retry_max_time,
        retry_delay,
    )
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
