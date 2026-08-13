"""Task ssh_daemon_setup: install and configure the SSH server.

The task installs the configured SSH server package, runs its systemd
service and patches the daemon configuration through a drop-in file at
the configured sshd_config_dropin_path, never through sshd_config
itself: sshd_config is only checked for an Include directive that pulls
the drop-in directory in, because a missing Include means the rendered
drop-in would be silently ignored. The drop-in is rendered from the
configured directives; an empty directives list removes the drop-in, so
the task can revoke its own settings. The pre-generated key pair lives
in task_data/ssh_daemon_setup/: the private key is encrypted with a
strong pass phrase, so it is committed to the repository as is. The
task copies both keys into the .ssh directory of root and of every
configured user and guarantees the public key in authorized_keys
without removing other keys, so passwordless login works while the
private key stays encrypted at rest. A configured user that does not
exist yet is skipped with a log line, so the task stays idempotent
while the users_setup task runs later. The task owns the key files and
the drop-in: both are rewritten whenever the content differs. The
service is enabled and started when inactive; when the configuration
changed while the service was active, the service is reloaded, which
never drops existing connections. The task is idempotent: it skips when
the package is installed, the drop-in matches the render, the keys are
in place and the service is enabled and active; force mode rewrites the
drop-in and reloads the service but never reinstalls the package.
"""

from __future__ import annotations

import fnmatch
import os
import pwd
import subprocess
import time
from pathlib import Path

from pyntara.config import SshDaemonSetupConfig, SshDirective
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    APT_NONINTERACTIVE_ENV,
    install_package_once,
    package_is_installed,
    run_command,
    service_is_active,
    service_is_enabled,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
SSH_DATA_DIR = REPO_ROOT / "task_data" / "ssh_daemon_setup"


def _render_dropin(directives: tuple[SshDirective, ...]) -> str:
    """Render the drop-in content for the configured directives.

    An empty directives list renders an empty string, which the caller
    interprets as "remove the drop-in". The header comment marks the
    file as owned by the task, so a manual edit is reverted on the next
    run.
    """

    if not directives:
        return ""
    lines = ["# Managed by the Pyntara ssh_daemon_setup task."]
    lines.extend(f"{directive.name} {directive.value}" for directive in directives)
    return "\n".join(lines) + "\n"


def _read_text(path: Path) -> str | None:
    """Current content of a text file, or None when absent."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _include_covers_dropin(
    config_path: Path, dropin_path: Path, timeout: float
) -> bool:
    """True when sshd_config pulls the drop-in directory in.

    Every Include directive of sshd_config is matched against the
    drop-in path with fnmatch, which understands the glob patterns
    OpenSSH accepts; a relative pattern resolves against the directory
    of sshd_config. A missing file, an unreadable file or a directive
    that does not cover the drop-in all mean the rendered drop-in would
    be ignored, so the task must fail loudly instead of pretending the
    configuration is in place.
    """

    content = _read_text(config_path)
    if content is None:
        return False
    base_dir = config_path.parent
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, sep, pattern = stripped.partition(" ")
        if not sep or keyword.casefold() != "include":
            continue
        pattern = pattern.strip()
        if fnmatch.fnmatch(str(dropin_path), pattern):
            return True
        if not pattern.startswith("/"):
            relative = base_dir / pattern
            if fnmatch.fnmatch(str(dropin_path), str(relative)):
                return True
    return False


def _apply_owner(path: Path, uid: int, gid: int) -> None:
    """Set the file owner when the process runs as root.

    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, uid, gid)


def _write_bytes_if_different(
    path: Path, content: bytes, mode: int, uid: int, gid: int
) -> bool:
    """Write content with the given mode and owner; True when changed.

    The file is rewritten only when the content differs, so a repeated
    run leaves an untouched file alone. The task owns the key files,
    so a file with different content is overwritten: a manual edit
    cannot wedge the deployed keys.
    """

    if path.is_file() and path.read_bytes() == content:
        return False
    path.write_bytes(content)
    os.chmod(path, mode)
    _apply_owner(path, uid, gid)
    return True


def _ensure_authorized_key(
    path: Path, key_line: str, mode: int, uid: int, gid: int
) -> bool:
    """Append the public key to authorized_keys; True when changed.

    The file is appended to, never rewritten, so keys the user added by
    hand survive. A key line already present is a no-op, so repeated
    runs do not accumulate duplicates.
    """

    existing: list[str] = []
    if path.is_file():
        existing = path.read_text(encoding="utf-8").splitlines()
    if key_line in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(key_line + "\n")
    os.chmod(path, mode)
    _apply_owner(path, uid, gid)
    return True


def _deploy_keys(
    ssh_dir: Path,
    private_bytes: bytes,
    public_bytes: bytes,
    public_line: str,
    cfg: SshDaemonSetupConfig,
    uid: int,
    gid: int,
) -> bool:
    """Deploy the keys into one .ssh directory; True when anything changed.

    The .ssh directory is created with the configured mode and owned by
    the user, the private and public key files are written with their
    configured modes and the public key line is guaranteed in
    authorized_keys. The private key stays encrypted, because it is
    copied as is from the repository.
    """

    changed = False
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, cfg.ssh_dir_mode)
    _apply_owner(ssh_dir, uid, gid)
    if _write_bytes_if_different(
        ssh_dir / cfg.private_key_file_name,
        private_bytes,
        cfg.private_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / cfg.public_key_file_name,
        public_bytes,
        cfg.public_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _ensure_authorized_key(
        ssh_dir / "authorized_keys",
        public_line,
        cfg.authorized_keys_file_mode,
        uid,
        gid,
    ):
        changed = True
    return changed


def _write_dropin(dropin_path: Path, content: str, mode: int) -> None:
    """Write the rendered drop-in with the configured mode, root-owned."""

    dropin_path.parent.mkdir(parents=True, exist_ok=True)
    dropin_path.write_text(content, encoding="utf-8")
    os.chmod(dropin_path, mode)
    _apply_owner(dropin_path, 0, 0)


def _remove_dropin(dropin_path: Path) -> None:
    """Remove the drop-in; a missing file is a no-op."""

    try:
        dropin_path.unlink()
    except FileNotFoundError:
        pass


def _ensure_package(
    cfg: SshDaemonSetupConfig,
    timeout: float,
    skip_update: bool,
) -> tuple[bool, str]:
    """Install the SSH package; return (success, error_text).

    The apt index is refreshed once before the install, so dependencies
    resolve from a fresh index; skip_update=True disables the refresh
    for test or offline runs. Each attempt uses the shared
    noninteractive apt environment; total attempts are one initial plus
    retries.
    """

    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_NONINTERACTIVE_ENV,
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(cfg.install_retries + 1):
        ok, error = install_package_once(cfg.package_name, timeout)
        if ok:
            break
    return ok, error


def _wait_active(
    service_name: str,
    attempts: int,
    retry_delay_seconds: float,
    timeout: float,
) -> bool:
    """True when the service reports active within the readiness loop.

    The service may report activating for a moment after start, so the
    check is repeated with a pause until attempts run out.
    """

    for _ in range(attempts):
        time.sleep(retry_delay_seconds)
        if service_is_active(service_name, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    """Install the SSH server, patch its config and deploy the keys.

    The goal is reached when the package is installed, sshd_config pulls
    the drop-in directory in, the drop-in matches the render, the keys
    are in place for root and every existing configured user and the
    service is enabled and active; the task then returns changed=False.
    Otherwise it installs the package, writes or removes the drop-in,
    deploys the keys, enables the service and starts or reloads it.
    Every step is reported to stdout as single lines; any failure is
    returned as an error TaskResult, and the runner continues with the
    remaining tasks.
    """

    cfg = ctx.config.ssh_daemon_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "ssh_daemon_setup" in ctx.force_tasks

    private_source = SSH_DATA_DIR / cfg.private_key_file_name
    public_source = SSH_DATA_DIR / cfg.public_key_file_name
    if not private_source.is_file() or not public_source.is_file():
        return TaskResult(
            success=False,
            error=(
                f"key files {cfg.private_key_file_name} and "
                f"{cfg.public_key_file_name} missing in {SSH_DATA_DIR}"
            ),
        )
    private_bytes = private_source.read_bytes()
    public_bytes = public_source.read_bytes()
    public_line = public_bytes.decode("utf-8").strip()

    changed = False

    installed = package_is_installed(
        cfg.package_name, cfg.package_status_timeout_seconds
    )
    _log(
        f"checking package {cfg.package_name}: "
        f"{'installed' if installed else 'missing'}"
    )
    if not installed:
        _log(f"installing package {cfg.package_name}")
        ok, error = _ensure_package(cfg, timeout, ctx.skip_apt_update)
        if not ok:
            return TaskResult(
                success=False,
                error=f"cannot install {cfg.package_name}: {error}",
            )
        _log("package installed")
        changed = True

    include_ok = _include_covers_dropin(
        cfg.sshd_config_path, cfg.sshd_config_dropin_path, timeout
    )
    _log(
        f"checking Include directive in {cfg.sshd_config_path}: "
        f"{'found' if include_ok else 'missing'}"
    )
    if not include_ok:
        return TaskResult(
            success=False,
            error=(
                f"{cfg.sshd_config_path} has no Include directive covering "
                f"{cfg.sshd_config_dropin_path.parent}"
            ),
        )

    target_dropin = _render_dropin(cfg.directives)
    current_dropin = _read_text(cfg.sshd_config_dropin_path)
    dropin_changed = force or current_dropin != target_dropin
    if dropin_changed:
        if target_dropin == "":
            _log(f"removing drop-in {cfg.sshd_config_dropin_path}")
            try:
                _remove_dropin(cfg.sshd_config_dropin_path)
            except OSError as exc:
                return TaskResult(
                    success=False,
                    changed=changed,
                    error=f"cannot remove drop-in: {exc}",
                )
            _log("drop-in removed")
        else:
            _log(f"writing drop-in {cfg.sshd_config_dropin_path}")
            try:
                _write_dropin(
                    cfg.sshd_config_dropin_path,
                    target_dropin,
                    cfg.dropin_file_mode,
                )
            except OSError as exc:
                return TaskResult(
                    success=False,
                    changed=changed,
                    error=f"cannot write drop-in: {exc}",
                )
            _log("drop-in written")
        changed = True

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    if not force and not changed and enabled and active:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    _log(f"deploying keys into {cfg.root_ssh_dir}")
    if _deploy_keys(
        cfg.root_ssh_dir,
        private_bytes,
        public_bytes,
        public_line,
        cfg,
        0,
        0,
    ):
        changed = True
    _log("root keys deployed")
    for user in cfg.users:
        try:
            record = pwd.getpwnam(user)
        except KeyError:
            _log(f"user {user} does not exist, skipping key deployment")
            continue
        ssh_dir = Path(record.pw_dir) / ".ssh"
        _log(f"deploying keys into {ssh_dir}")
        if _deploy_keys(
            ssh_dir,
            private_bytes,
            public_bytes,
            public_line,
            cfg,
            record.pw_uid,
            record.pw_gid,
        ):
            changed = True
        _log("user keys deployed")

    if not enabled:
        _log(f"enabling service: systemctl enable {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "enable", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl enable failed: {exc}",
            )
        _log("service enabled")
        changed = True

    if not active:
        _log(f"starting service: systemctl start {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "start", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl start failed: {exc}",
            )
        _log("service started")
        if not _wait_active(
            cfg.service_unit_name,
            cfg.start_check_attempts,
            cfg.start_check_retry_delay_seconds,
            timeout,
        ):
            return TaskResult(
                success=False,
                changed=True,
                error=(
                    f"{cfg.service_unit_name} did not become active after "
                    f"{cfg.start_check_attempts} checks"
                ),
            )
        _log("service active")
        changed = True
    elif force or dropin_changed:
        _log(f"reloading service: systemctl reload {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "reload", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl reload failed: {exc}",
            )
        _log("service reloaded")
        changed = True

    return TaskResult(
        success=True,
        changed=changed,
        message=(
            f"SSH server {cfg.package_name} configured, service "
            f"{cfg.service_unit_name} active"
        ),
    )
