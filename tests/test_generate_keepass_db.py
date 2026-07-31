from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "secrets" / "generate_new_keepass_db.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("generate_new_keepass_db", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
keepass_script = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = keepass_script
MODULE_SPEC.loader.exec_module(keepass_script)


def test_ensure_pykeepass_venv_creates_local_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_dir = tmp_path / "venv"
    pip_cache_dir = tmp_path / "pip-cache"
    monkeypatch.setenv("PYNTARA_KEEPASS_VENV_DIR", str(venv_dir))
    monkeypatch.setenv("PIP_CACHE_DIR", str(pip_cache_dir))

    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        timeout: int,
        env: dict[str, str] | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert timeout == keepass_script.PIP_INSTALL_TIMEOUT_SEC
        if command[:3] == [sys.executable, "-m", "venv"]:
            assert check is True
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "python3").write_text("", encoding="utf-8")
            (bin_dir / "pip").write_text("", encoding="utf-8")
        if command[:2] == [str(venv_dir / "bin" / "python3"), "-c"]:
            assert check is False
            assert stdout == subprocess.DEVNULL
            assert stderr == subprocess.DEVNULL
            return subprocess.CompletedProcess(command, 1)
        if command[0] == str(venv_dir / "bin" / "pip"):
            assert check is True
            assert env is not None
            assert env["PIP_CACHE_DIR"] == str(pip_cache_dir)
            assert "--quiet" in command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(keepass_script.subprocess, "run", fake_run)

    python_bin = keepass_script._ensure_pykeepass_venv()

    assert python_bin == venv_dir / "bin" / "python3"
    assert calls[0][:3] == [sys.executable, "-m", "venv"]
    assert calls[1][:2] == [str(venv_dir / "bin" / "python3"), "-c"]
    assert calls[2][0] == str(venv_dir / "bin" / "pip")


def test_ensure_pykeepass_venv_skips_pip_when_package_already_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_dir = tmp_path / "venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3").write_text("", encoding="utf-8")
    (bin_dir / "pip").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYNTARA_KEEPASS_VENV_DIR", str(venv_dir))

    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        timeout: int,
        env: dict[str, str] | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, stdout, stderr
        calls.append(command)
        assert timeout == keepass_script.PIP_INSTALL_TIMEOUT_SEC
        if command[:2] == [str(bin_dir / "python3"), "-c"]:
            assert check is False
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(keepass_script.subprocess, "run", fake_run)

    python_bin = keepass_script._ensure_pykeepass_venv()

    assert python_bin == bin_dir / "python3"
    assert calls == [[str(bin_dir / "python3"), "-c", "import pykeepass"]]


def test_bootstrap_and_reexec_sets_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(keepass_script.SELF_BOOTSTRAP_SENTINEL, raising=False)
    monkeypatch.setattr(
        keepass_script,
        "_ensure_pykeepass_venv",
        lambda: Path("/tmp/pyntara-keepass-python"),
    )
    monkeypatch.setattr(sys, "argv", ["generate_new_keepass_db.py", "vault.kdbx"])

    captured: dict[str, str] = {}

    def fake_execve(path: str, args: list[str], env: dict[str, str]) -> None:
        captured["path"] = path
        captured["arg0"] = args[0]
        captured["sentinel"] = env[keepass_script.SELF_BOOTSTRAP_SENTINEL]
        raise SystemExit(0)

    monkeypatch.setattr(keepass_script.os, "execve", fake_execve)

    with pytest.raises(SystemExit) as exc_info:
        keepass_script._bootstrap_and_reexec()
    assert exc_info.value.code == 0

    assert captured["path"] == "/tmp/pyntara-keepass-python"
    assert captured["arg0"] == "/tmp/pyntara-keepass-python"
    assert captured["sentinel"] == "1"


def test_bootstrap_and_reexec_stops_on_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(keepass_script.SELF_BOOTSTRAP_SENTINEL, "1")
    with pytest.raises(SystemExit, match="still unavailable"):
        keepass_script._bootstrap_and_reexec()


def test_open_existing_database_reports_non_keepass_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keepass_script, "_prompt_password", lambda *, confirm: "pw")

    def fake_open(_path: str, *, password: str) -> None:
        del password
        raise keepass_script.HeaderChecksumError("bad")

    monkeypatch.setattr(keepass_script, "PyKeePass", fake_open)

    with pytest.raises(SystemExit, match="not a KeePass database"):
        keepass_script._open_existing_database(Path("/tmp/default.vault"))


def test_open_existing_database_retries_credentials_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keepass_script, "_prompt_password", lambda *, confirm: "pw")

    attempts = {"count": 0}

    def fake_open(_path: str, *, password: str) -> None:
        del password
        attempts["count"] += 1
        raise keepass_script.CredentialsError("wrong")

    monkeypatch.setattr(keepass_script, "PyKeePass", fake_open)

    with pytest.raises(SystemExit, match="password attempts exhausted"):
        keepass_script._open_existing_database(Path("/tmp/does-not-matter.kdbx"))

    assert attempts["count"] == 3
