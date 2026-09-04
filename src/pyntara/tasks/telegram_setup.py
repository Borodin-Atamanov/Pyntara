"""Task telegram_setup: install the latest Telegram Desktop for the desktop user.

The described goal is a Telegram Desktop that the desktop user launches
from the application menu and that keeps itself updated. The task installs
the official static Linux build, the only build with the built-in
auto-update enabled: the download link configured as latest_url answers a
redirect to the newest versioned archive tsetup.<version>.tar.xz, so the
redirect is the single source of the latest release and no version list is
tracked anywhere. The archive of the installed release stays in the root
download_dir under its own name, and that name doubles as the idempotency
record: a rerun whose cached archive name matches the redirect target and
whose Telegram binary and launcher entry are present changes nothing, so a
current install is never downloaded again. When the redirect points to a
newer archive, the task downloads it, installs Telegram and its Updater
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

from pyntara.config import TelegramSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    CURL_DOWNLOAD_WRITE_OUT,
    curl_flags,
    run_command,
)

# The release archive the redirect points to is named tsetup.<version>.tar.xz.
ARCHIVE_PREFIX = "tsetup."
ARCHIVE_SUFFIX = ".tar.xz"
# The two files the official archive carries, under a Telegram/ prefix.
BINARY_NAME = "Telegram"
UPDATER_NAME = "Updater"
# Derived paths under the desktop user home (docs/spec/telegram-setup.md).
INSTALL_DIR_REL = Path(".local/share/Telegram")
LAUNCHER_REL = Path(".local/share/applications/telegramdesktop.desktop")
ICON_REL = Path(".local/share/icons/telegram-desktop.png")
LAUNCHER_MODE = 0o644
ICON_MODE = 0o644
EXECUTABLE_MODE = 0o755


def _archive_name(url: str) -> str:
    """The archive file name of a download url; raises RuntimeError."""

    name = url.rstrip("/").rsplit("/", 1)[-1]
    if not (name.startswith(ARCHIVE_PREFIX) and name.endswith(ARCHIVE_SUFFIX)):
        raise RuntimeError(f"unexpected latest download URL: {url}")
    return name


def _resolve_latest_url(
    latest_url: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> str:
    """The download url the latest_url redirect resolves to.

    A HEAD request follows the redirect chain and reports the final url
    through --write-out, so the newest release is discovered without
    downloading the archive. Raises RuntimeError when the request fails or
    the final url does not look like a tsetup archive.
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
                curl_timeout, retries, connect_timeout, retry_max_time
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
    _archive_name(url)
    return url


def _download_archive(
    download_dir: Path,
    url: str,
    name: str,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> None:
    """Download the archive into download_dir under its final name.

    The download goes to a sibling .download file first and is renamed only
    after a successful transfer, so a cached archive name always means a
    complete archive. Raises RuntimeError on failure.
    """

    download_dir.mkdir(parents=True, exist_ok=True)
    partial = download_dir / (name + ".download")
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
                    curl_timeout, retries, connect_timeout, retry_max_time
                ),
                url,
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"cannot download {url}: {exc}") from None
    partial.replace(download_dir / name)


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

    The archive is extracted to a temporary directory, then each file is
    copied into the install directory under the user home when it differs
    from what is already there, and the install directory and its files are
    owned by the desktop user. Raises RuntimeError on any failure.
    """

    install_dir = Path(cfg.home_dir) / INSTALL_DIR_REL
    extract_dir = Path(tempfile.mkdtemp(prefix="pyntara-telegram-"))
    try:
        run_command(
            [
                "tar",
                "--extract",
                "--xz",
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
        install_dir.mkdir(parents=True, exist_ok=True)
        _own_to_user(cfg.username, install_dir)
        for name in (BINARY_NAME, UPDATER_NAME):
            source = extract_dir / "Telegram" / name
            if not source.is_file():
                raise RuntimeError(f"archive {archive.name} contains no {name}")
            target = install_dir / name
            if not (target.is_file() and filecmp.cmp(source, target, shallow=False)):
                shutil.copyfile(source, target)
            target.chmod(EXECUTABLE_MODE)
            _own_to_user(cfg.username, target)
    except OSError as exc:
        raise RuntimeError(
            f"cannot install Telegram into {install_dir}: {exc}"
        ) from None
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _cleanup_old_archives(download_dir: Path, current_name: str) -> None:
    """Remove every cached archive except the current one."""

    for stale in download_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"):
        if stale.name != current_name:
            stale.unlink(missing_ok=True)


def _desktop_content(cfg: TelegramSetupConfig) -> str:
    """The launcher entry that starts Telegram from the application menu."""

    home = Path(cfg.home_dir)
    binary = home / INSTALL_DIR_REL / BINARY_NAME
    icon = home / ICON_REL
    return (
        "[Desktop Entry]\n"
        "Name=Telegram Desktop\n"
        "Comment=Official messaging client for Telegram\n"
        f"Exec={binary}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Network;InstantMessaging;\n"
    )


def _ensure_launcher(cfg: TelegramSetupConfig) -> tuple[bool, str | None]:
    """Write the launcher entry; return (changed, error)."""

    path = Path(cfg.home_dir) / LAUNCHER_REL
    content = _desktop_content(cfg)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False, None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(LAUNCHER_MODE)
        _own_to_user(cfg.username, path)
        _own_to_user(cfg.username, path.parent)
    except OSError as exc:
        return False, f"cannot write the Telegram desktop entry: {exc}"
    return True, None


def _ensure_icon(
    cfg: TelegramSetupConfig,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> tuple[bool, str | None]:
    """Download the configured icon when missing; return (changed, error).

    A failed icon download is a warning, never a fatal error: the launcher
    entry still starts Telegram and only the icon stays generic until the
    next run retries.
    """

    path = Path(cfg.home_dir) / ICON_REL
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
                    curl_timeout, retries, connect_timeout, retry_max_time
                ),
                cfg.icon_url,
            ],
            timeout=timeout,
        )
        path.chmod(ICON_MODE)
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
    curl_retries = ctx.config.engine.curl_retries
    connect_timeout = ctx.config.engine.curl_connect_timeout_seconds
    retry_max_time = ctx.config.engine.curl_retry_max_time_seconds
    force = "telegram_setup" in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    try:
        url = _resolve_latest_url(
            cfg.latest_url,
            timeout,
            curl_timeout,
            curl_retries,
            connect_timeout,
            retry_max_time,
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    name = _archive_name(url)
    _log(f"checking the latest Telegram Desktop release: {name}")

    install_dir = Path(cfg.home_dir) / INSTALL_DIR_REL
    binary = install_dir / BINARY_NAME
    launcher = Path(cfg.home_dir) / LAUNCHER_REL
    archive = cfg.download_dir / name
    already_latest = (
        not force and archive.is_file() and binary.is_file() and launcher.is_file()
    )

    if already_latest:
        _log(f"latest Telegram Desktop release {name} is already installed")
    else:
        if not archive.is_file():
            _log(f"downloading Telegram Desktop release {name}")
            try:
                _download_archive(
                    cfg.download_dir,
                    url,
                    name,
                    timeout,
                    curl_timeout,
                    curl_retries,
                    connect_timeout,
                    retry_max_time,
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

    launcher_changed, launcher_error = _ensure_launcher(cfg)
    if launcher_error:
        return TaskResult(success=False, changed=changed, error=launcher_error)
    if launcher_changed:
        messages.append(f"wrote the Telegram launcher entry to {launcher}")
        changed = True

    icon_changed, icon_error = _ensure_icon(
        cfg,
        timeout,
        curl_timeout,
        curl_retries,
        connect_timeout,
        retry_max_time,
    )
    if icon_error:
        warnings.append(icon_error)
    if icon_changed:
        messages.append(f"downloaded the Telegram icon to {Path(cfg.home_dir) / ICON_REL}")
        changed = True

    if not messages:
        messages.append("already installed the latest release")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
