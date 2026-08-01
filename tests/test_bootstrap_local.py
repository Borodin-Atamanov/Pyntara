from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BOOTSTRAP_TIMEOUT = 300  # generous timeout for uv sync + bootstrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_KNOWN_UV_LOCATIONS = [
    "/home/i/.local/bin/uv",
    "/usr/local/bin/uv",
    "/home/i/.copilot/repos/copilot-worktrees/pyntara/borodin-atamanov-cuddly-barnacle/.venv/bin/uv",
]


def _find_uv() -> str | None:
    """Find the uv binary by checking known locations and PATH."""
    for loc in _KNOWN_UV_LOCATIONS:
        p = Path(loc)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
    real_uv = shutil.which("uv")
    if real_uv:
        return real_uv
    try:
        result = subprocess.run(["which", "uv"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _copy_project(tmp_path: Path, *, include_git: bool = False) -> Path:
    """Copy the project to a temp directory, excluding heavy cache dirs.

    Also creates a minimal .venv/bin/ with symlinks to the real uv and
    python3 so that 'command -v uv' and 'command -v python3' succeed
    inside the bootstrap script, avoiding the apt-get install step.

    If *include_git* is True, the .git directory is also copied (needed
    for git-based bootstrap tests).

    Returns the path to the copied project root.
    """
    ignores = [
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    ]
    if not include_git:
        ignores.append(".git")

    dest = tmp_path / "project"
    shutil.copytree(_REPO_ROOT, dest, ignore=shutil.ignore_patterns(*ignores))

    # Create a minimal .venv/bin/ with symlinks to real tools.
    # This is needed because the bootstrap script checks 'command -v uv'
    # and 'command -v python3' to decide whether to run apt-get install.
    # Without these symlinks, the script would try apt-get and fail
    # (no root access in tests).
    venv_bin = dest / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)

    # Symlink python3 from the system
    import sys as _sys
    python3_path = _sys.executable
    (venv_bin / "python3").symlink_to(python3_path)

    # Symlink uv if found
    real_uv = _find_uv()
    if real_uv:
        (venv_bin / "uv").symlink_to(real_uv)

    return dest


def _bootstrap_env(tmp_path: Path, project_dir: Path) -> dict[str, str]:
    """Return environment variables for local bootstrap testing.

    All paths are isolated under tmp_path so the test does not touch
    the host system's /var/lib/pyntara or /var/log/pyntara.

    PATH includes the copied project's .venv/bin so that 'uv' and
    'python3' are found by the bootstrap script, avoiding the apt-get
    install step. The copied project has symlinks to real tools.
    """
    venv_bin = str(project_dir / ".venv" / "bin")
    path = f"{venv_bin}:{os.environ.get('PATH', '')}"
    return {
        "PATH": path,
        "UV_PROJECT_ENVIRONMENT": ".venv",
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        "PYNTARA_STATE_DIR": str(tmp_path / "state"),
        "PYNTARA_LOG_DIR": str(tmp_path / "logs"),
        "PYNTARA_WORK_BASE_DIR": str(tmp_path / "work"),
        "PYNTARA_REPO_CACHE_DIR": str(tmp_path / "cache" / "Pyntara.git"),
        "PYNTARA_UV_CACHE_DIR": str(tmp_path / "cache" / "uv"),
        "PYNTARA_VAULT_PASSWORD": "test-password-123",
        "HOME": str(tmp_path / "home"),
    }


# ---------------------------------------------------------------------------
# Layer 1: Local source bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_local_source(tmp_path: Path) -> None:
    """Run i.sh from a local project copy.

    The script detects the local source via has_local_project_source()
    because pyproject.toml is in the same directory as i.sh. No git
    clone or network access is needed.

    Expected: bootstrap completes, tasks run, exit 0.
    """
    project_dir = _copy_project(tmp_path)
    env = {**os.environ, **_bootstrap_env(tmp_path, project_dir)}

    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(project_dir / "i.sh")],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=_BOOTSTRAP_TIMEOUT,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, (
        f"Bootstrap failed (exit {result.returncode}) after {elapsed:.0f}s.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Bootstrap finished" in result.stdout, (
        f"Bootstrap completion marker not found.\nstdout:\n{result.stdout}"
    )
    assert "hostname: done" in result.stdout, (
        f"Task output not found.\nstdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Layer 1: Local source bootstrap with piped stdin
# ---------------------------------------------------------------------------


def test_bootstrap_local_source_piped_stdin(tmp_path: Path) -> None:
    """Run i.sh with script piped to stdin, simulating 'curl ... | bash'.

    The script content is piped to bash's stdin. The script detects
    local source via has_local_project_source() because the script
    file is in the project directory (BASH_SOURCE resolves the path).

    Expected: bootstrap completes, tasks run, exit 0.
    """
    project_dir = _copy_project(tmp_path)
    env = {**os.environ, **_bootstrap_env(tmp_path, project_dir)}
    script_content = (project_dir / "i.sh").read_text(encoding="utf-8")

    start = time.monotonic()
    result = subprocess.run(
        ["bash"],
        input=script_content,
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=_BOOTSTRAP_TIMEOUT,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, (
        f"Bootstrap via pipe failed (exit {result.returncode}) after {elapsed:.0f}s.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Bootstrap finished" in result.stdout, (
        f"Bootstrap completion marker not found.\nstdout:\n{result.stdout}"
    )
    assert "hostname: done" in result.stdout, (
        f"Task output not found.\nstdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Layer 3: Git-based bootstrap from local bare repo
# ---------------------------------------------------------------------------


def test_bootstrap_via_local_git(tmp_path: Path) -> None:
    """Run i.sh from a separate directory, using a local bare git repo.

    The script is placed in a directory WITHOUT pyproject.toml, so
    has_local_project_source() returns False. The script falls through
    to sync_cached_repository() which clones from the local bare repo.

    This tests the full git clone/fetch/archive path without GitHub.

    Expected: bootstrap completes, tasks run, exit 0.
    """
    # Create a local bare git repo from the real repo (with .git)
    bare_repo = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(_REPO_ROOT), str(bare_repo)],
        check=True,
        capture_output=True,
        timeout=30,
    )

    # Copy i.sh to a separate directory (no pyproject.toml beside it)
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    shutil.copy2(_REPO_ROOT / "i.sh", script_dir / "i.sh")

    # Create a project dir with tools for PATH
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    import sys as _sys
    (tools_dir / "python3").symlink_to(_sys.executable)
    real_uv = _find_uv()
    if real_uv:
        (tools_dir / "uv").symlink_to(real_uv)

    env = {**os.environ, **{
        "PATH": f"{tools_dir}:{os.environ.get('PATH', '')}",
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        "PYNTARA_STATE_DIR": str(tmp_path / "state"),
        "PYNTARA_LOG_DIR": str(tmp_path / "logs"),
        "PYNTARA_WORK_BASE_DIR": str(tmp_path / "work"),
        "PYNTARA_UV_CACHE_DIR": str(tmp_path / "cache" / "uv"),
        "PYNTARA_VAULT_PASSWORD": "test-password-123",
        "HOME": str(tmp_path / "home"),
    }}
    # Point the repo cache to our local bare repo
    env["PYNTARA_REPO_CACHE_DIR"] = str(bare_repo)

    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(script_dir / "i.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=_BOOTSTRAP_TIMEOUT,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, (
        f"Bootstrap via local git failed (exit {result.returncode}) after {elapsed:.0f}s.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Bootstrap finished" in result.stdout, (
        f"Bootstrap completion marker not found.\nstdout:\n{result.stdout}"
    )
    assert "hostname: done" in result.stdout, (
        f"Task output not found.\nstdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Layer 3: Git-based bootstrap with piped stdin
# ---------------------------------------------------------------------------


def test_bootstrap_via_local_git_piped_stdin(tmp_path: Path) -> None:
    """Run i.sh via piped stdin, using a local bare git repo.

    Combines the piped-stdin delivery (curl simulation) with the local
    git bootstrap path. The script is piped to bash, and the git cache
    points to a local bare repo.

    Expected: bootstrap completes, tasks run, exit 0.
    """
    bare_repo = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(_REPO_ROOT), str(bare_repo)],
        check=True,
        capture_output=True,
        timeout=30,
    )

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    import sys as _sys
    (tools_dir / "python3").symlink_to(_sys.executable)
    real_uv = _find_uv()
    if real_uv:
        (tools_dir / "uv").symlink_to(real_uv)

    env = {**os.environ, **{
        "PATH": f"{tools_dir}:{os.environ.get('PATH', '')}",
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        "PYNTARA_STATE_DIR": str(tmp_path / "state"),
        "PYNTARA_LOG_DIR": str(tmp_path / "logs"),
        "PYNTARA_WORK_BASE_DIR": str(tmp_path / "work"),
        "PYNTARA_REPO_CACHE_DIR": str(bare_repo),
        "PYNTARA_UV_CACHE_DIR": str(tmp_path / "cache" / "uv"),
        "PYNTARA_VAULT_PASSWORD": "test-password-123",
        "HOME": str(tmp_path / "home"),
    }}
    script_content = (_REPO_ROOT / "i.sh").read_text(encoding="utf-8")

    start = time.monotonic()
    result = subprocess.run(
        ["bash"],
        input=script_content,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=_BOOTSTRAP_TIMEOUT,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, (
        f"Bootstrap via local git + pipe failed (exit {result.returncode}) after {elapsed:.0f}s.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Bootstrap finished" in result.stdout, (
        f"Bootstrap completion marker not found.\nstdout:\n{result.stdout}"
    )
    assert "hostname: done" in result.stdout, (
        f"Task output not found.\nstdout:\n{result.stdout}"
    )
