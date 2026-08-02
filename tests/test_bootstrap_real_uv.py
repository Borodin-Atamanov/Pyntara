from __future__ import annotations

import os
import pty
import shutil
import select
import subprocess
import re
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.bootstrap_deep


def _resolve_uv_bin(repo_root: Path) -> str | None:
    local_uv = repo_root / ".tmp-uv-bin" / "uv"
    if local_uv.is_file() and os.access(local_uv, os.X_OK):
        return str(local_uv)

    found = shutil.which("uv")
    if found is not None:
        return found

    return None


def test_bootstrap_i_sh_with_real_uv_reports_fast_secrets_stage(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    uv_bin = _resolve_uv_bin(repo_root)
    if uv_bin is None:
        pytest.skip("real uv executable is unavailable for this environment")

    password_file = repo_root / "secrets" / "default.password"
    if not password_file.exists():
        pytest.skip("default password file is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_git.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{Path(uv_bin).parent}:{env.get('PATH', '')}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_UV_USER"] = os.environ.get("USER", "")
    env["PYNTARA_UV_USER_HOME"] = str(tmp_path / "home-user")
    env["PYNTARA_CLI_STDIN_PATH"] = "/dev/null"
    env["PYNTARA_UI__TASK_PRE_INTERACTION_TIMEOUT_SEC"] = "2"
    env["PYNTARA_VAULT_PASSWORD"] = password_file.read_text(encoding="utf-8").strip()
    completed = subprocess.run(
        ["bash", str(repo_root / "i.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output

    assert "CLI_STAGE before_mode_selector" in output, output
    assert "CLI_STAGE after_mode_selector" in output, output
    assert "CLI_STAGE before_secrets_load" in output, output
    assert "CLI_STAGE after_secrets_load elapsed=" in output, output

    match = re.search(r"CLI_STAGE after_secrets_load elapsed=([0-9]+\.[0-9]{3})s", output)
    assert match is not None, output
    elapsed = float(match.group(1))
    assert elapsed < 10.0, output


def test_bootstrap_i_sh_pipe_with_real_uv_interactive_tty(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    uv_bin = _resolve_uv_bin(repo_root)
    if uv_bin is None:
        pytest.skip("real uv executable is unavailable for this environment")

    password_file = repo_root / "secrets" / "default.password"
    if not password_file.exists():
        pytest.skip("default password file is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_git.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{Path(uv_bin).parent}:{env.get('PATH', '')}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_UV_USER"] = os.environ.get("USER", "")
    env["PYNTARA_UV_USER_HOME"] = str(tmp_path / "home-user")
    env["PYNTARA_UI__TASK_PRE_INTERACTION_TIMEOUT_SEC"] = "2"
    env["PYNTARA_VAULT_PASSWORD"] = password_file.read_text(encoding="utf-8").strip()

    script_bytes = (repo_root / "i.sh").read_bytes()
    master_fd, slave_fd = pty.openpty()
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        ["bash"],
        stdin=read_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=repo_root,
        env=env,
        close_fds=True,
    )

    os.close(read_fd)
    os.close(slave_fd)

    try:
        written = 0
        while written < len(script_bytes):
            written += os.write(write_fd, script_bytes[written:])
    finally:
        os.close(write_fd)

    output = bytearray()
    deadline = time.monotonic() + 130.0
    sent_enters = False

    def _read_chunk(fd: int) -> bytes:
        try:
            return os.read(fd, 4096)
        except OSError:
            return b""

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break

        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            chunk = _read_chunk(master_fd)
            if chunk:
                output.extend(chunk)
            else:
                break

        text = output.decode("utf-8", errors="replace")
        if not sent_enters and "CLI_STAGE before_mode_selector" in text:
            os.write(master_fd, b"\r\r\r\r")
            sent_enters = True

        if "Bootstrap finished" in text:
            break

    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
    finally:
        os.close(master_fd)

    text = output.decode("utf-8", errors="replace")
    assert proc.returncode == 0, text
    assert "Using CLI stdin source: /dev/tty" in text, text
    assert "CLI_STAGE before_mode_selector" in text, text
    assert "CLI_STAGE after_mode_selector" in text, text
    assert "CLI_STAGE before_secrets_load" in text, text
    assert "CLI_STAGE after_secrets_load elapsed=" in text, text
    assert "Bootstrap finished" in text, text
