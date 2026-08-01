# Local bootstrap test architecture

## Goal

Automatically test the exact `curl ... | sudo bash` flow **without**:
- Network access to GitHub
- Waiting for git push/download
- Running as root
- Modifying the host system

## How `i.sh` resolves source code

The script has a built-in fallback chain (lines 170-199):

```
sync_cached_repository() → git clone/fetch from $SOURCE_REMOTE_URL
    ↓ on failure
has_local_project_source() → checks if $BOOTSTRAP_SOURCE_DIR has pyproject.toml
    ↓ on failure
exit with error
```

`BOOTSTRAP_SOURCE_DIR` is set to the directory containing `i.sh` when launched from a file:
```bash
BOOTSTRAP_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

## Key insight

When `i.sh` is run from a file inside a complete project checkout, `has_local_project_source()` returns True, and `prepare_workspace()` copies the local source directly — no git clone, no network.

## Architecture: Layered approach

### Layer 0: Unit tests for `i.sh` functions (EXISTING)
- `test_bootstrap_script.py` — tests individual bash functions with mocked commands
- Already works, no changes needed

### Layer 1: Local-source bootstrap (NEW)
Run `i.sh` from a local project directory, using real commands (git, uv, python).

```python
def test_bootstrap_local_source(tmp_path):
    # 1. Copy project to temp dir (excluding .venv, __pycache__)
    project_dir = tmp_path / "project"
    shutil.copytree(REPO_ROOT, project_dir, ignore=...)

    # 2. Set env vars to control paths
    env = {
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        "PYNTARA_STATE_DIR": str(tmp_path / "state"),
        "PYNTARA_LOG_DIR": str(tmp_path / "logs"),
        "PYNTARA_WORK_BASE_DIR": str(tmp_path / "work"),
        "PYNTARA_UV_CACHE_DIR": str(tmp_path / "cache" / "uv"),
        "PYNTARA_VAULT_PASSWORD": "test-password-123",
        "HOME": str(tmp_path / "home"),
    }

    # 3. Run i.sh from the project directory
    # The script will detect local source via has_local_project_source()
    result = subprocess.run(
        ["bash", str(project_dir / "i.sh")],
        cwd=project_dir,
        env={**os.environ, **env},
        capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0
    assert "Bootstrap finished" in result.stdout
    assert "hostname: done" in result.stdout
```

### Layer 2: Piped-stdin bootstrap (curl simulation)
Pipe `i.sh` content to `bash` like curl does, with a PTY providing `/dev/tty`.

```python
def test_bootstrap_via_pipe_local_source(tmp_path):
    # 1. Copy project to temp dir
    project_dir = tmp_path / "project"
    shutil.copytree(REPO_ROOT, project_dir, ...)

    # 2. Set env vars
    # PYNTARA_SOURCE_REPO is not needed — local source is detected

    # 3. Create a PTY, run bash with the script piped to stdin
    # The PTY provides /dev/tty for interactive input
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["bash"], stdin=subprocess.PIPE,
        stdout=slave_fd, stderr=slave_fd,
        env=env, cwd=tmp_path,
    )
    # Write script content to pipe
    script_content = (project_dir / "i.sh").read_bytes()
    proc.stdin.write(script_content)
    proc.stdin.close()
    # Interact via PTY master for /dev/tty
    ...
```

### Layer 3: Git-based bootstrap from local bare repo
Test the full git clone/fetch path without GitHub.

```python
def test_bootstrap_via_local_git(tmp_path):
    # 1. Create a local bare git repo from the project
    bare_repo = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", REPO_ROOT, str(bare_repo)], check=True)

    # 2. Copy i.sh to a separate location (not inside the project)
    # so has_local_project_source() returns False
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    shutil.copy2(REPO_ROOT / "i.sh", script_dir / "i.sh")

    # 3. Set env vars to use local git repo
    # PYNTARA_SOURCE_REPO is not enough — URL is always github.com
    # So we need to create a local git server or use file:// protocol
    # Solution: override PYNTARA_REPO_CACHE_DIR + pre-populate cache
    env = {
        "PYNTARA_REPO_CACHE_DIR": str(bare_repo),
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        ...
    }
    # The script will use the pre-populated cache (git fetch from there)
    ...
```

## Env vars matrix

| Env var | Purpose | Layer 0 | Layer 1 | Layer 2 | Layer 3 |
|---------|---------|---------|---------|---------|---------|
| `PYNTARA_ROOT_EUID` | Bypass root check | yes | yes | yes | yes |
| `PYNTARA_STATE_DIR` | State storage path | yes | yes | yes | yes |
| `PYNTARA_LOG_DIR` | Log path | yes | yes | yes | yes |
| `PYNTARA_WORK_BASE_DIR` | Workspace path | yes | yes | yes | yes |
| `PYNTARA_UV_CACHE_DIR` | uv cache path | yes | yes | yes | yes |
| `PYNTARA_CLI_STDIN_PATH` | CLI stdin source | — | — | — | — |
| `PYNTARA_VAULT_PASSWORD` | Vault password | — | yes | yes | yes |
| `HOME` | User home for uv | — | yes | yes | yes |
| `PYNTARA_REPO_CACHE_DIR` | Pre-populated git cache | — | — | — | yes |

## What each layer actually tests

| Layer | Commands | Network | Speed | Coverage |
|-------|----------|---------|-------|----------|
| 0 (existing) | mocked apt/git/uv | no | ~1s | bash logic only |
| 1 (local source) | real git, real uv, real python | no | ~30s | full bootstrap but no git clone |
| 2 (piped stdin) | same as Layer 1 + PTY | no | ~30s | + stdin piping, /dev/tty |
| 3 (git bootstrap) | real git clone/fetch | no | ~30s | + git clone from local repo |

## Test file structure

```
tests/
  test_bootstrap_script.py    # Layer 0 (existing)
  test_bootstrap_pty.py       # Layer 2 (existing, mocked commands)
  test_bootstrap_local.py     # Layer 1 + Layer 3 (NEW, real commands)
```

## Implementation notes

### Layer 1: `test_bootstrap_local.py`

```python
import shutil, subprocess
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PTY_TIMEOUT = 120.0

def _copy_project(tmp_path: Path) -> Path:
    """Copy the project to a temp dir, excluding heavy cache dirs."""
    dest = tmp_path / "project"
    shutil.copytree(
        _REPO_ROOT, dest,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".git", ".mypy_cache",
            ".ruff_cache", ".pytest_cache",
        ),
    )
    return dest

def _bootstrap_env(tmp_path: Path) -> dict:
    return {
        "PYNTARA_ROOT_EUID": str(os.geteuid()),
        "PYNTARA_STATE_DIR": str(tmp_path / "state"),
        "PYNTARA_LOG_DIR": str(tmp_path / "logs"),
        "PYNTARA_WORK_BASE_DIR": str(tmp_path / "work"),
        "PYNTARA_UV_CACHE_DIR": str(tmp_path / "cache" / "uv"),
        "PYNTARA_VAULT_PASSWORD": "test-password-123",
        "HOME": str(tmp_path / "home"),
    }

def test_bootstrap_local_source(tmp_path):
    project_dir = _copy_project(tmp_path)
    env = {**os.environ, **_bootstrap_env(tmp_path)}
    result = subprocess.run(
        ["bash", str(project_dir / "i.sh")],
        cwd=project_dir, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "Bootstrap finished" in result.stdout
    assert "hostname: done" in result.stdout
```

### Layer 3: git-based bootstrap

```python
def test_bootstrap_via_local_git(tmp_path):
    project_dir = _copy_project(tmp_path)

    # Create a local bare repo
    bare_repo = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(project_dir), str(bare_repo)],
        check=True, capture_output=True, timeout=30,
    )

    # Copy i.sh to a separate location (no pyproject.toml beside it)
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    shutil.copy2(project_dir / "i.sh", script_dir / "i.sh")

    env = {**os.environ, **_bootstrap_env(tmp_path)}
    env["PYNTARA_REPO_CACHE_DIR"] = str(bare_repo)

    result = subprocess.run(
        ["bash", str(script_dir / "i.sh")],
        cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "Bootstrap finished" in result.stdout
```

## Required conditions

1. **Real `git` must be available** — true in CI and dev environments
2. **Real `uv` must be available** — true in CI and dev environments
3. **Real `python3` must be available** — always true
4. **`pykeepass` must be installable** — uv sync installs it
5. **No root required** — `PYNTARA_ROOT_EUID` bypasses the check
6. **No network required** — all source is local
7. **No /dev/tty required** — `PYNTARA_VAULT_PASSWORD` provides password

## Files to create

- `tests/test_bootstrap_local.py` — Layer 1 and Layer 3 tests
- `plans/test-bootstrap-architecture.md` — this document