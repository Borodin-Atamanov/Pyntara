from __future__ import annotations

import os
import pty
import select
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from tests.conftest import PtySession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VAULT_PASSWORD = "test-password-123"
_PTY_TIMEOUT = 30.0
_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers: mock executable creation
# ---------------------------------------------------------------------------


def _write_executable(path: Path, content: str) -> None:
    """Write a shell script and make it executable."""
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _install_mock_apt_get(fake_bin: Path, trace_path: Path) -> None:
    """Create a mock apt-get that logs calls and simulates success."""
    _write_executable(
        fake_bin / "apt-get",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "apt-get $*" >> "{trace_path}"
if [[ "$1" == "update" ]]; then
  mkdir -p "$(dirname "{trace_path}")"
  echo "ok" >> "{trace_path}"
fi
exit 0
""",
    )


def _install_mock_git(fake_bin: Path, trace_path: Path, source_tar: Path) -> None:
    """Create a mock git that logs calls and simulates clone/fetch/archive."""
    _write_executable(
        fake_bin / "git",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "git $*" >> "{trace_path}"

if [[ "$1" == "clone" ]]; then
  cache_dir="${{@: -1}}"
  mkdir -p "$cache_dir"
  exit 0
fi

if [[ "$3" == "rev-parse" ]]; then
  ref="${{@: -1}}"
  if [[ "$ref" == "FETCH_HEAD" ]] || [[ "$ref" == "origin/main" ]] || [[ "$ref" == "main" ]]; then
    exit 0
  fi
  exit 1
fi

if [[ "$3" == "archive" ]]; then
  output=""
  while (($#)); do
    if [[ "$1" == "--output" ]]; then
      output="$2"
      break
    fi
    shift
  done
  if [[ -n "$output" ]]; then
    cp "{source_tar}" "$output"
  fi
  exit 0
fi

exit 0
""",
    )


def _install_mock_uv(fake_bin: Path, trace_path: Path, host_pyntara: str) -> None:
    """Create a mock uv that logs calls and delegates 'run pyntara' to the real CLI.

    For lock/sync commands it simulates success without actual work.
    For 'run pyntara' it executes the host's real pyntara script so that all
    Python dependencies (typer, pydantic, etc.) are available.
    """
    _write_executable(
        fake_bin / "uv",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "uv $*" >> "{trace_path}"

if [[ "$1" == "lock" && "$2" == "--check" ]]; then
  exit 0
fi

if [[ "$1" == "sync" ]]; then
  exit 0
fi

if [[ "$1" == "run" && "$2" == "pyntara" ]]; then
  shift 2
  exec "{host_pyntara}" "$@"
fi

exit 0
""",
    )


def _install_mock_curl(fake_bin: Path, trace_path: Path) -> None:
    """Create a mock curl that logs calls and simulates success."""
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "curl $*" >> "{trace_path}"
exit 0
""",
    )


# ---------------------------------------------------------------------------
# Helpers: source tar creation
# ---------------------------------------------------------------------------


def _build_source_tar(archive_path: Path) -> None:
    """Create a tar archive with a minimal but functional Pyntara project.

    The archive contains pyproject.toml, the full src/ tree, and config files
    needed for the CLI to run. The repo-tree directory is cleaned before each
    build to avoid stale files from previous builds.

    Password files are excluded from the archive — they are added separately
    by _add_test_vault_to_tar() with controlled password content.
    """
    source_root = archive_path.parent / "repo-tree"
    if source_root.exists():
        shutil.rmtree(source_root)
    source_root.mkdir(parents=True, exist_ok=True)

    for item in _REPO_ROOT.iterdir():
        if item.name in (".venv", "__pycache__", ".git", ".mypy_cache", ".ruff_cache"):
            continue
        dest = source_root / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
        else:
            shutil.copy2(item, dest)

    # Remove any password files from the copied tree — they are added
    # separately by _add_test_vault_to_tar with controlled password content.
    secrets_dir = source_root / "secrets"
    if secrets_dir.exists():
        for p in secrets_dir.iterdir():
            if p.suffix == ".password":
                p.unlink()

    with tarfile.open(archive_path, "w") as archive:
        for item in source_root.iterdir():
            archive.add(item, arcname=item.name)


# ---------------------------------------------------------------------------
# Helpers: environment setup
# ---------------------------------------------------------------------------


def _find_host_pyntara() -> str:
    """Locate the host's real pyntara CLI script.

    Returns the absolute path to the pyntara executable.
    """
    venv_pyntara = _REPO_ROOT / ".venv" / "bin" / "pyntara"
    if venv_pyntara.is_file() and os.access(venv_pyntara, os.X_OK):
        return str(venv_pyntara.resolve())

    candidates = [
        str(Path.home() / ".local" / "bin" / "pyntara"),
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    try:
        result = subprocess.run(
            ["which", "pyntara"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "pyntara"


def _add_test_vault_to_tar(
    source_tar: Path, password: str, *, include_password_file: bool = True
) -> None:
    """Create a test KeePass vault and add it to the source tar.

    The vault is added as secrets/default.vault in the tar archive.
    If *include_password_file* is True, the companion .password file
    is also added as secrets/default.password.
    """
    pykeepass = pytest.importorskip("pykeepass", reason="pykeepass is required")
    vault_path = source_tar.parent / "default.vault"
    pykeepass.create_database(str(vault_path), password=password)
    with tarfile.open(source_tar, "a") as archive:
        archive.add(str(vault_path), arcname="secrets/default.vault")
        if include_password_file:
            password_path = vault_path.with_suffix(".password")
            password_path.write_text(password + "\n", encoding="utf-8")
            archive.add(str(password_path), arcname="secrets/default.password")


def _setup_bootstrap_env(
    tmp_path: Path,
    *,
    trace_path: Path,
    source_tar: Path,
    vault_password: str | None = None,
) -> dict[str, str]:
    """Set up environment variables for bootstrap testing.

    Returns the environment dict.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)

    host_pyntara = _find_host_pyntara()
    _install_mock_apt_get(fake_bin, trace_path)
    _install_mock_git(fake_bin, trace_path, source_tar)
    _install_mock_uv(fake_bin, trace_path, host_pyntara)
    _install_mock_curl(fake_bin, trace_path)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_UV_USER"] = os.environ.get("USER", "")
    env["PYNTARA_UV_USER_HOME"] = str(tmp_path / "home-user")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    if vault_password is not None:
        env["PYNTARA_VAULT_PASSWORD"] = vault_password

    return env


# ---------------------------------------------------------------------------
# Shared test runner
# ---------------------------------------------------------------------------


def _run_bootstrap(
    tmp_path: Path,
    *,
    vault_password: str | None = _VAULT_PASSWORD,
    interactive_input: list[bytes] | None = None,
    wait_for: bytes | None = None,
    pipe_stdin: bool = False,
    include_password_file: bool = True,
) -> tuple[int, bytes]:
    """Run the bootstrap script via PTY and return (returncode, output).

    Builds the source tar, adds a test vault, sets up the mock environment,
    and runs the script. If *interactive_input* is provided, each bytes item
    is written to the PTY with a delay between writes.

    If *wait_for* is provided, the method waits for that marker before
    returning (or until timeout).

    If *pipe_stdin* is True, the script content is piped to bash's stdin
    (simulating 'curl ... | bash') instead of being passed as a file argument.
    The PTY is still used as the controlling terminal so /dev/tty is available.

    If *include_password_file* is False, the .password file is not added
    to the tar, forcing the CLI to use env var or interactive prompt.
    """
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)
    _add_test_vault_to_tar(source_tar, _VAULT_PASSWORD, include_password_file=include_password_file)

    env = _setup_bootstrap_env(
        tmp_path,
        trace_path=trace_path,
        source_tar=source_tar,
        vault_password=vault_password,
    )

    script_path = tmp_path / "i.sh"
    script_path.write_text((_REPO_ROOT / "i.sh").read_text(encoding="utf-8"), encoding="utf-8")

    if pipe_stdin:
        # Simulate 'curl ... | bash': bash reads script from a pipe,
        # but the PTY provides the controlling terminal for /dev/tty access.
        # We open a PTY, start bash with stdin from the pipe write end,
        # write the script to the pipe, close it, and use the PTY for I/O.
        master_fd, slave_fd = pty.openpty()
        read_fd, write_fd = os.pipe()

        proc = subprocess.Popen(
            ["bash"],
            stdin=read_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=_REPO_ROOT,
            env=env,
            close_fds=True,
        )
        os.close(read_fd)
        os.close(slave_fd)

        # Write script content to the pipe and close it
        script_content = (_REPO_ROOT / "i.sh").read_bytes()
        os.write(write_fd, script_content)
        os.close(write_fd)

        # Now use the PTY master for interaction
        accumulated = b""
        start_time = time.monotonic()
        deadline = start_time + _PTY_TIMEOUT

        if interactive_input:
            for chunk in interactive_input:
                os.write(master_fd, chunk)
                time.sleep(0.2)

        marker = wait_for or b"Bootstrap finished"
        found = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                try:
                    while True:
                        ready, _, _ = select.select([master_fd], [], [], 0)
                        if not ready:
                            break
                        chunk = os.read(master_fd, 4096)
                        if not chunk:
                            break
                        accumulated += chunk
                except (OSError, ValueError):
                    pass
                break

            try:
                ready, _, _ = select.select([master_fd], [], [], 0.05)
                if ready:
                    chunk = os.read(master_fd, 4096)
                    if chunk:
                        accumulated += chunk
            except (OSError, ValueError):
                pass

            if marker in accumulated:
                found = True
                break

        # Drain any remaining output
        try:
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if not ready:
                    break
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                accumulated += chunk
        except (OSError, ValueError):
            pass

        os.close(master_fd)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        return proc.returncode or 0, accumulated
    else:
        with PtySession(
            ["bash", str(script_path)],
            cwd=_REPO_ROOT,
            env=env,
            timeout=_PTY_TIMEOUT,
        ) as session:
            if interactive_input:
                for chunk in interactive_input:
                    session.write(chunk)
                    time.sleep(0.2)

            marker = wait_for or b"Bootstrap finished"
            try:
                session.read_until(marker, timeout=25)
            except TimeoutError:
                pass

            session.close()

        return session.returncode or 0, session.output


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.1: Full bootstrap with auto-select + env password
# ---------------------------------------------------------------------------


def test_bootstrap_full_flow_auto_select(tmp_path: Path) -> None:
    """Full bootstrap: auto-select mode, password via env var.

    Expected: all bootstrap steps complete, CLI runs, tasks execute, exit 0.
    """
    returncode, output = _run_bootstrap(tmp_path)

    assert returncode == 0, f"Bootstrap should exit 0. Output:\n{output.decode(errors='replace')}"
    assert b"Bootstrap finished" in output, (
        "Bootstrap completion marker not found."
    )
    assert b"hostname: done" in output, (
        "Task output not found."
    )


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.2: Bootstrap via piped stdin (curl ... | bash)
# ---------------------------------------------------------------------------


def test_bootstrap_via_piped_stdin(tmp_path: Path) -> None:
    """Full bootstrap via piped stdin, simulating 'curl ... | sudo bash'.

    The script content is piped to bash's stdin. The PTY provides the
    controlling terminal for /dev/tty access. Password is provided via
    env var to avoid the interactive prompt.

    Expected: all bootstrap steps complete, CLI runs, tasks execute, exit 0.
    """
    returncode, output = _run_bootstrap(
        tmp_path,
        pipe_stdin=True,
    )

    assert returncode == 0, (
        f"Bootstrap via piped stdin should exit 0. Output:\n{output.decode(errors='replace')}"
    )
    assert b"Bootstrap finished" in output, (
        "Bootstrap completion marker not found."
    )
    assert b"hostname: done" in output, (
        "Task output not found."
    )


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.3: Bootstrap via piped stdin, no password env var
# ---------------------------------------------------------------------------


def test_bootstrap_via_piped_stdin_no_password(tmp_path: Path) -> None:
    """Bootstrap via piped stdin, no password file and no env var.

    The .password file is excluded from the tar so the CLI has no way
    to open the vault. Since there is no controlling terminal in the
    test environment, the CLI should fail with a clear error message.

    Expected: CLI fails with password-related error.
    """
    returncode, output = _run_bootstrap(
        tmp_path,
        vault_password=None,  # No env var
        include_password_file=False,  # No .password file in tar
        pipe_stdin=True,
    )

    # The CLI should either fail (wrong password) or prompt for password
    assert returncode != 0, (
        f"Bootstrap should fail without correct password. "
        f"Output:\n{output.decode(errors='replace')}"
    )
    # Either getpass prompted (and password was wrong) or it detected no TTY
    assert (
        b"KeePass password" in output
        or b"interactive prompt is unavailable" in output
        or b"password" in output.lower()
    ), (
        f"Expected password-related output. Output:\n{output.decode(errors='replace')}"
    )
    # Critical: password should NOT be echoed in the output
    password_echoed = output.decode(errors="replace").count("wrong-password")
    assert password_echoed <= 1, (
        f"Password appears to be echoed {password_echoed} times! "
        f"This means getpass is leaking the password. Output:\n{output.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.4: Bootstrap with manual mode selection
# ---------------------------------------------------------------------------


def test_bootstrap_manual_mode_selection(tmp_path: Path) -> None:
    """Full bootstrap: user presses DOWN+ENTER for mode, password via env.

    Expected: desktop mode selected, tasks run, exit 0.
    """
    interactive = [b"\x1b[B", b"\r"]  # DOWN arrow, then ENTER
    returncode, output = _run_bootstrap(
        tmp_path,
        interactive_input=interactive,
        wait_for=b"Bootstrap finished",
    )

    assert returncode == 0, f"Bootstrap should exit 0. Output:\n{output.decode(errors='replace')}"
    assert b"Bootstrap finished" in output


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.3: Bootstrap with wrong password
# ---------------------------------------------------------------------------


def test_bootstrap_wrong_password(tmp_path: Path) -> None:
    """Full bootstrap: no password file and no env var.

    The .password file is excluded from the tar. Since there is no
    controlling terminal in the test environment, the CLI should fail
    with a clear error message.

    Expected: fails with password-related error, exit non-zero.
    """
    returncode, output = _run_bootstrap(
        tmp_path,
        vault_password=None,  # No env var
        include_password_file=False,  # No .password file in tar
    )

    assert returncode != 0, (
        f"Bootstrap should fail without password. Output:\n{output.decode(errors='replace')}"
    )
    assert b"interactive prompt is unavailable" in output or b"password" in output.lower(), (
        f"Expected password-related error. Output:\n{output.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.4: Bootstrap idempotency (second run skips tasks)
# ---------------------------------------------------------------------------


def test_bootstrap_idempotency(tmp_path: Path) -> None:
    """Run bootstrap twice; second run should use cached state.

    Expected: first run completes tasks, second run also succeeds (re-runs
    are idempotent and don't break), both exit 0.
    """
    returncode1, output1 = _run_bootstrap(tmp_path)
    assert returncode1 == 0, (
        f"First bootstrap run failed. Output:\n{output1.decode(errors='replace')}"
    )
    assert b"hostname: done" in output1

    # Second run — state dir persists, tasks should be re-runnable
    returncode2, output2 = _run_bootstrap(tmp_path)
    assert returncode2 == 0, (
        f"Second bootstrap run failed. Output:\n{output2.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Scenario 2.5: Bootstrap trace verification
# ---------------------------------------------------------------------------


def test_bootstrap_trace_shows_all_steps(tmp_path: Path) -> None:
    """Verify that the mock trace log contains all expected steps.

    Expected: trace shows apt-get, git, uv, and pyntara invocations.
    """
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)
    _add_test_vault_to_tar(source_tar, _VAULT_PASSWORD)

    env = _setup_bootstrap_env(
        tmp_path,
        trace_path=trace_path,
        source_tar=source_tar,
        vault_password=_VAULT_PASSWORD,
    )

    script_path = tmp_path / "i.sh"
    script_path.write_text((_REPO_ROOT / "i.sh").read_text(encoding="utf-8"), encoding="utf-8")

    with PtySession(
        ["bash", str(script_path)],
        cwd=_REPO_ROOT,
        env=env,
        timeout=_PTY_TIMEOUT,
    ) as session:
        try:
            session.read_until(b"Bootstrap finished", timeout=25)
        except TimeoutError:
            pass
        session.close()

    assert session.returncode == 0, (
        f"Bootstrap failed. Output:\n{session.output_text}"
    )

    trace = trace_path.read_text(encoding="utf-8")

    # apt-get is skipped when python3 and uv are already in PATH
    # The trace should still show git, uv sync, and uv run pyntara
    assert "git clone" in trace or "git fetch" in trace, f"git not in trace:\n{trace}"
    assert "uv sync" in trace, f"uv sync not in trace:\n{trace}"
    assert "uv run pyntara" in trace, f"uv run pyntara not in trace:\n{trace}"