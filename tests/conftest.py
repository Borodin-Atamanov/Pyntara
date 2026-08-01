from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# KeePass database fixture (existing)
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_kdbx(tmp_path: Path) -> tuple[Path, str]:
    """Create a KeePass database with AES-KDF (fast) for testing.

    Also creates the companion .password file.
    Returns (vault_path, password) tuple.
    """
    pykeepass = pytest.importorskip("pykeepass", reason="pykeepass is required")
    vault_path = tmp_path / "test.vault"
    password = "test-password-123"
    pykeepass.create_database(str(vault_path), password=password)
    # Create companion .password file
    password_path = vault_path.with_suffix(".password")
    password_path.write_text(password + "\n", encoding="utf-8")
    return vault_path, password


def create_password_file(vault_path: Path, password: str) -> Path:
    """Create a .password file alongside a vault file.

    Returns the path to the password file.
    """
    password_path = vault_path.with_suffix(".password")
    password_path.write_text(password + "\n", encoding="utf-8")
    return password_path


# ---------------------------------------------------------------------------
# PTY session helper for interactive CLI tests
# ---------------------------------------------------------------------------


class PtySession:
    """Run a subprocess through a pseudo-terminal with scripted I/O.

    Usage:
        with PtySession(cmd, cwd=path, env=env) as session:
            output = session.read_until(b"prompt")
            session.write(b"input\\n")
            output = session.read_until(b"done")
            session.close()
        assert session.returncode == 0
    """

    def __init__(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cmd = cmd
        self._cwd = cwd
        self._env = env
        self._timeout = timeout
        self._master_fd: int | None = None
        self._proc: subprocess.Popen[Any] | None = None
        self._output = b""
        self._start_time = 0.0

    def start(self) -> None:
        """Open PTY and start the subprocess."""
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._start_time = time.monotonic()

        env = dict(os.environ) if self._env is None else {**os.environ, **self._env}

        self._proc = subprocess.Popen(
            self._cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self._cwd,
            env=env,
            close_fds=True,
        )
        # Slave fd is owned by the child process now
        os.close(slave_fd)

    def read_until(
        self, marker: bytes, timeout: float | None = None, strip_ansi: bool = True
    ) -> bytes:
        """Read PTY output until *marker* is found or *timeout* expires.

        Returns all output accumulated so far (including the marker).
        Raises TimeoutError if marker is not found in time.
        """
        deadline = time.monotonic() + (timeout if timeout is not None else self._timeout)
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                # Process exited; read any remaining data
                self._drain_output()
                break

            self._drain_output()
            if marker in self._output:
                return self._output

            time.sleep(0.02)

        if marker not in self._output:
            display = self._output.decode("utf-8", errors="replace")[:500]
            raise TimeoutError(
                f"Marker {marker!r} not found within {timeout or self._timeout}s. "
                f"Output so far:\n{display}"
            )
        return self._output

    def write(self, data: bytes) -> None:
        """Write *data* to the PTY (child's stdin)."""
        if self._master_fd is None:
            raise RuntimeError("Session not started. Call start() first.")
        os.write(self._master_fd, data)

    def writeline(self, text: str) -> None:
        """Write a line of text followed by newline to the PTY."""
        self.write((text + "\n").encode("utf-8"))

    def close(self) -> None:
        """Close PTY and wait for the subprocess to finish."""
        if self._master_fd is not None:
            self._drain_output()
            os.close(self._master_fd)
            self._master_fd = None

        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)

    @property
    def returncode(self) -> int | None:
        """Return the subprocess exit code, or None if still running."""
        if self._proc is None:
            return None
        return self._proc.returncode

    @property
    def output(self) -> bytes:
        """Return all output accumulated so far."""
        return self._output

    @property
    def output_text(self) -> str:
        """Return all output as text, replacing unreadable characters."""
        return self._output.decode("utf-8", errors="replace")

    def _drain_output(self) -> None:
        """Read any available data from the PTY master without blocking."""
        if self._master_fd is None:
            return
        try:
            while True:
                ready, _, _ = select.select([self._master_fd], [], [], 0)
                if not ready:
                    break
                chunk = os.read(self._master_fd, 4096)
                if not chunk:
                    break
                self._output += chunk
        except (OSError, ValueError):
            pass

    def __enter__(self) -> PtySession:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Test workspace fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def test_workspace(tmp_path: Path, fast_kdbx: tuple[Path, str]) -> Path:
    """Create a minimal workspace with config files and a test KeePass vault.

    The workspace contains:
      - config.yaml with paths pointing to tmp_path
      - tasks.yaml with hostname and users tasks
      - install_modes.yaml with all three modes
      - secrets/default.vault (KeePass, password from fast_kdbx)
      - secrets/default.password (companion password file)

    Returns the workspace path.
    """
    vault_path, vault_password = fast_kdbx
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(vault_path), str(secrets_dir / "default.vault"))
    # Also copy the companion .password file
    password_src = vault_path.with_suffix(".password")
    if password_src.exists():
        shutil.copy2(str(password_src), str(secrets_dir / "default.password"))

    # config.yaml
    (tmp_path / "config.yaml").write_text(
        f"paths:\n"
        f"  secrets_dir: {secrets_dir}\n"
        f"  task_data_dir: {tmp_path / 'task_data'}\n"
        f"timeouts:\n"
        f"  command_sec: 30\n"
        f"  task_sec: 60\n"
        f"logging:\n"
        f"  command_output_to_console: false\n"
        f"  command_output_to_log: false\n"
        f"  datetime_format: '%Y-%m-%d-%H-%M-%S'\n"
        f"ui:\n"
        f"  task_pre_interaction_timeout_sec: 2\n"
    )

    # tasks.yaml with real task modules
    (tmp_path / "tasks.yaml").write_text(
        "tasks:\n"
        "  - name: hostname\n"
        "    order: 10\n"
        "    description: Generate random hostname.\n"
        "    module: pyntara.tasks.hostname:run\n"
        "    idempotent: true\n"
        "    default_enabled: true\n"
        "    timeout_sec: 30\n"
        "    depends_on: []\n"
        "    data_subdir: hostname\n"
        "  - name: users\n"
        "    order: 20\n"
        "    description: Prepare user accounts.\n"
        "    module: pyntara.tasks.users:run\n"
        "    idempotent: true\n"
        "    default_enabled: true\n"
        "    timeout_sec: 30\n"
        "    depends_on:\n"
        "      - hostname\n"
        "    data_subdir: users\n"
    )

    # install_modes.yaml
    (tmp_path / "install_modes.yaml").write_text(
        "minimal:\n"
        "  - hostname\n"
        "server:\n"
        "  - hostname\n"
        "  - users\n"
        "desktop:\n"
        "  - hostname\n"
        "  - users\n"
        "default_desktop_mode: minimal\n"
        "default_server_mode: minimal\n"
        "auto_select_timeout_sec: 2\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Helper: find pyntara CLI executable
# ---------------------------------------------------------------------------


def find_pyntara_bin() -> list[str]:
    """Locate the pyntara CLI executable.

    Returns a list suitable for subprocess.Popen, e.g. ['pyntara'] or
    the full path to the .venv pyntara script.
    """
    # Prefer the .venv pyntara script (always available in dev environment)
    venv_pyntara = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "pyntara"
    if venv_pyntara.is_file() and os.access(venv_pyntara, os.X_OK):
        return [str(venv_pyntara)]

    candidates: list[list[str]] = [
        ["pyntara"],
        [str(Path.home() / ".local" / "bin" / "pyntara")],
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [*candidate, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Last resort: hope pyntara is on PATH
    return ["pyntara"]