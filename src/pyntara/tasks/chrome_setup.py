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
and writes a desktop entry override that appends the CDP flags to every
Exec line of the packaged entry.

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
import shutil
import subprocess
import tempfile
from pathlib import Path

from pyntara.config import ChromeSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    APT_NONINTERACTIVE_ENV,
    CURL_DOWNLOAD_WRITE_OUT,
    curl_flags,
    ensure_root_owner,
    install_package_once,
    package_is_installed,
    run_command,
)

# The apt package name of Google Chrome and the process name pgrep sees
# for a running main browser process (comm is "chrome", not the wrapper).
PACKAGE_NAME = "google-chrome-stable"
CHROME_PROCESS_NAME = "chrome"
# Mode of every deployed config and desktop file.
FILE_MODE = 0o644
# The settings repository tree deployed to the filesystem root.
SYSTEM_TREE_REL = Path("system")
# The repository profile file merged over the live Chrome profile.
REPO_PREFERENCES_REL = Path("Default") / "Preferences"
# The live profile preferences under the desktop user home.
PROFILE_PREFERENCES_REL = (
    Path(".config") / "google-chrome" / "Default" / "Preferences"
)


def _source_text(keyring_path: Path) -> str:
    """The deb822 apt source of the official Google Chrome repository."""

    return (
        "Types: deb\n"
        "URIs: https://dl.google.com/linux/chrome-stable/deb/\n"
        "Suites: stable\n"
        "Components: main\n"
        "Architectures: amd64\n"
        f"Signed-By: {keyring_path}\n"
    )


def _ensure_repository(
    cfg: ChromeSetupConfig,
    timeout: float,
    curl_timeout: float,
    retries: int,
    connect_timeout: float,
    retry_max_time: int,
) -> tuple[bool, str | None]:
    """Register the Google apt source and its keyring; (changed, error).

    The keyring is downloaded from Google when missing and dearmored into
    the configured path; the deb822 source file is written when its content
    differs. Both files are root-owned mode 0644.
    """

    changed = False
    try:
        if not (
            cfg.keyring_path.is_file() and cfg.keyring_path.stat().st_size > 0
        ):
            cfg.keyring_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="pyntara-chrome-") as tmp:
                armored = Path(tmp) / "google-chrome-key.pub"
                run_command(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--show-error",
                        "--output",
                        str(armored),
                        "--write-out",
                        CURL_DOWNLOAD_WRITE_OUT,
                        *curl_flags(
                            curl_timeout,
                            retries,
                            connect_timeout,
                            retry_max_time,
                        ),
                        cfg.google_key_url,
                    ],
                    timeout=timeout,
                )
                run_command(
                    [
                        "gpg",
                        "--dearmor",
                        "--output",
                        str(cfg.keyring_path),
                        str(armored),
                    ],
                    timeout=timeout,
                )
            cfg.keyring_path.chmod(FILE_MODE)
            ensure_root_owner(cfg.keyring_path)
            changed = True
        content = _source_text(cfg.keyring_path)
        if not (
            cfg.apt_source_path.is_file()
            and cfg.apt_source_path.read_text(encoding="utf-8") == content
        ):
            cfg.apt_source_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.apt_source_path.write_text(content, encoding="utf-8")
            cfg.apt_source_path.chmod(FILE_MODE)
            ensure_root_owner(cfg.apt_source_path)
            changed = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return changed, f"cannot register the Google Chrome apt repository: {exc}"
    return changed, None


def _ensure_chrome_installed(
    cfg: ChromeSetupConfig,
    *,
    force: bool,
    skip_apt_update: bool,
    timeout: float,
) -> tuple[bool, str | None]:
    """Install google-chrome-stable when missing or forced; (changed, error)."""

    if not force and package_is_installed(PACKAGE_NAME, timeout):
        return False, None
    try:
        if not skip_apt_update:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_NONINTERACTIVE_ENV,
                timeout=timeout,
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot refresh the apt index: {exc}"
    ok, error = install_package_once(PACKAGE_NAME, timeout)
    if not ok:
        return False, f"cannot install {PACKAGE_NAME}: {error}"
    return True, None


def _sync_settings_repo(cfg: ChromeSetupConfig, *, timeout: float) -> tuple[bool, str | None]:
    """Clone or update the browser settings repository; (changed, error)."""

    try:
        if not (cfg.settings_dir / ".git").is_dir():
            cfg.settings_dir.parent.mkdir(parents=True, exist_ok=True)
            run_command(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    cfg.settings_repo_ref,
                    cfg.settings_repo_url,
                    str(cfg.settings_dir),
                ],
                timeout=timeout,
            )
            return True, None
        run_command(
            [
                "git",
                "-C",
                str(cfg.settings_dir),
                "fetch",
                "--quiet",
                "origin",
                cfg.settings_repo_ref,
            ],
            timeout=timeout,
        )
        head = run_command(
            ["git", "-C", str(cfg.settings_dir), "rev-parse", "HEAD"],
            check=False,
            capture=True,
            timeout=timeout,
        )
        fetched = run_command(
            ["git", "-C", str(cfg.settings_dir), "rev-parse", "FETCH_HEAD"],
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
            ["git", "-C", str(cfg.settings_dir), "reset", "--hard", "FETCH_HEAD"],
            timeout=timeout,
        )
        return True, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot update the browser settings repository: {exc}"


def _deploy_system_tree(
    cfg: ChromeSetupConfig, *, force: bool
) -> tuple[bool, list[str]]:
    """Deploy the repository system/ tree under system_root; (changed, warnings).

    Each file is copied to cfg.system_root with its relative path
    preserved, root-owned mode 0644, only when the target differs (or in
    force mode). A per-file failure is a warning, never a fatal error.
    """

    source_root = cfg.settings_dir / SYSTEM_TREE_REL
    if not source_root.is_dir():
        return False, ["the settings repository carries no system/ tree"]
    changed = False
    warnings: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root)
        target = cfg.system_root / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not force and target.is_file() and target.read_bytes() == path.read_bytes():
                continue
            shutil.copyfile(path, target)
            target.chmod(FILE_MODE)
            ensure_root_owner(target)
            changed = True
        except OSError as exc:
            warnings.append(f"cannot deploy {rel}: {exc}")
    return changed, warnings


def _profile_preferences_path(home_dir: str) -> Path:
    """The live Chrome profile preferences of the desktop user."""

    return Path(home_dir) / PROFILE_PREFERENCES_REL


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
        ["pgrep", "-x", CHROME_PROCESS_NAME],
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


def _apply_profile_preferences(
    cfg: ChromeSetupConfig, *, timeout: float
) -> tuple[bool, str | None]:
    """Merge the repository profile over the live profile; (changed, note).

    The merge is identical in normal and force mode: the repository
    preferences are laid over the current profile file, the configured
    keys win and unrelated current settings are never deleted. A merge
    that reproduces the current content writes nothing. A running Chrome
    and an unreadable profile file leave the file untouched and report a
    warning, because a live Chrome would overwrite the file from its own
    memory.
    """

    repo_prefs = cfg.settings_dir / REPO_PREFERENCES_REL
    if not repo_prefs.is_file():
        return False, "the settings repository carries no Default/Preferences"
    if _chrome_is_running(timeout):
        return (
            False,
            "Google Chrome is running; the profile settings apply on the next Chrome start",
        )
    target = _profile_preferences_path(cfg.home_dir)
    try:
        try:
            current: object = (
                json.loads(target.read_text(encoding="utf-8"))
                if target.is_file()
                else {}
            )
        except (json.JSONDecodeError, OSError):
            return False, f"the profile preferences are unreadable; left untouched: {target}"
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
        target.chmod(FILE_MODE)
        _own_to_user(cfg.username, target)
    except OSError as exc:
        return False, f"cannot write the profile preferences: {exc}"
    return True, None


def _desktop_content(source_text: str, cdp_port: int, cdp_address: str) -> str:
    """The packaged desktop entry with the CDP flags on every Exec line."""

    flags = (
        f" --remote-debugging-port={cdp_port} "
        f"--remote-debugging-address={cdp_address}"
    )
    lines: list[str] = []
    for line in source_text.splitlines(keepends=True):
        if line.startswith("Exec="):
            lines.append(line.rstrip("\n") + flags + "\n")
        else:
            lines.append(line)
    return "".join(lines)


def _ensure_desktop_override(
    cfg: ChromeSetupConfig, *, force: bool
) -> tuple[bool, str | None]:
    """Write the CDP desktop override; (changed, warning)."""

    source = cfg.desktop_source_path
    if not source.is_file():
        return (
            False,
            f"the packaged desktop entry is missing; CDP flags not applied: {source}",
        )
    content = _desktop_content(
        source.read_text(encoding="utf-8"), cfg.cdp_port, cfg.cdp_address
    )
    target = cfg.desktop_override_path
    try:
        if not force and target.is_file() and target.read_text(encoding="utf-8") == content:
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(FILE_MODE)
        ensure_root_owner(target)
    except OSError as exc:
        return False, f"cannot write the desktop override: {exc}"
    return True, None


def _refresh_menu_database(cfg: ChromeSetupConfig, *, timeout: float) -> str | None:
    """Rebuild the KDE menu cache for the desktop user; return warning text.

    A best-effort step: the override is picked up by the session when the
    cache is rebuilt, so a failure is a warning and the menu catches up at
    the next login or cache rebuild.
    """

    try:
        run_command(
            [
                "runuser",
                "-u",
                cfg.username,
                "--",
                "env",
                f"HOME={cfg.home_dir}",
                "kbuildsycoca6",
                "--noincremental",
            ],
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return f"menu database refresh failed: {exc}"
    return None


def task(ctx: Context) -> TaskResult:
    """Install Chrome and apply the browser settings and the CDP entry.

    The target state is reached when google-chrome-stable is installed,
    the Google apt repository is registered, the settings repository is
    current, its system/ tree and profile preferences are applied, and the
    desktop override with the CDP flags is in place; the task then returns
    changed=False. Force mode reinstalls Chrome and rewrites the deployed
    files; the profile merge itself is identical in both modes.
    """

    cfg = ctx.config.chrome_setup
    timeout = ctx.config.engine.command_timeout_seconds
    curl_timeout = ctx.config.engine.curl_timeout_seconds
    curl_retries = ctx.config.engine.curl_retries
    connect_timeout = ctx.config.engine.curl_connect_timeout_seconds
    retry_max_time = ctx.config.engine.curl_retry_max_time_seconds
    force = "chrome_setup" in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    messages: list[str] = []

    _log("registering the Google Chrome apt repository")
    repo_changed, error = _ensure_repository(
        cfg,
        timeout,
        curl_timeout,
        curl_retries,
        connect_timeout,
        retry_max_time,
    )
    if error:
        return TaskResult(success=False, changed=changed, error=error)
    if repo_changed:
        messages.append("registered the Google Chrome apt repository")

    _log("checking the Google Chrome installation")
    install_changed, error = _ensure_chrome_installed(
        cfg,
        force=force,
        skip_apt_update=ctx.skip_apt_update,
        timeout=timeout,
    )
    if error:
        return TaskResult(success=False, changed=changed, error=error)
    if install_changed:
        messages.append("installed google-chrome-stable")
        changed = True

    _log("updating the browser settings repository")
    sync_changed, error = _sync_settings_repo(cfg, timeout=timeout)
    if error:
        return TaskResult(success=False, changed=changed, error=error)
    if sync_changed:
        messages.append("updated the browser settings repository")
        changed = True

    tree_changed, tree_warnings = _deploy_system_tree(cfg, force=force)
    warnings.extend(tree_warnings)
    if tree_changed:
        messages.append(f"deployed system browser settings to {cfg.system_root}")
        changed = True

    profile_changed, profile_note = _apply_profile_preferences(
        cfg, timeout=timeout
    )
    if profile_note:
        warnings.append(profile_note)
    if profile_changed:
        messages.append(
            "merged the browser profile settings over "
            f"{_profile_preferences_path(cfg.home_dir)}"
        )
        changed = True

    override_changed, override_note = _ensure_desktop_override(cfg, force=force)
    if override_note:
        warnings.append(override_note)
    if override_changed:
        messages.append(
            f"wrote the CDP desktop entry to {cfg.desktop_override_path}"
        )
        changed = True
        menu_note = _refresh_menu_database(cfg, timeout=timeout)
        if menu_note:
            warnings.append(menu_note)

    if not messages:
        messages.append("browser already set up")
    message = "; ".join(messages)
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
