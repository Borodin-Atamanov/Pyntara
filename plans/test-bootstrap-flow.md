# Testing the full bootstrap flow (curl ... i.sh | sudo bash)

## Problem
The program hangs for ~30 seconds after entering the KeePass password when run via `curl ... i.sh | sudo bash`. We need to automatically test this exact use case.

## Architectural Approaches

### Approach A: PTY-based subprocess test ⭐ RECOMMENDED
Use `pty.openpty()` to create a pseudo-terminal, run `uv run pyntara` with the PTY as stdin, and simulate interactive input.

```python
import os
import pty
import subprocess
import time

def test_cli_does_not_hang_with_wrong_password_via_pty():
    """Simulate interactive CLI session via PTY, feed wrong password, verify no hang."""
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["uv", "run", "pyntara"],
        stdin=slave_fd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    os.close(slave_fd)
    
    # Read until password prompt appears
    output = b""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master_fd, 4096)
            output += chunk
            if b"KeePass password" in output:
                break
        except BlockingIOError:
            time.sleep(0.1)
    
    assert b"KeePass password" in output, f"Prompt not found. Output: {output.decode(errors='replace')}"
    
    # Send wrong password
    os.write(master_fd, b"wrong-password\n")
    
    # Wait for process to exit (should fail with CredentialsError)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("CLI hung for 30s after wrong password - BUG!")
    
    assert proc.returncode != 0
```

**Pros:** ✅ Tests the real CLI with real stdin/stdout/stderr ✅ No Docker needed ✅ Fast (~1s) ✅ Catches the exact hang bug  
**Cons:** ❌ PTY handling is platform-specific ❌ Need to parse output to find prompts

### Approach B: Docker container integration test
Build a Docker container that simulates the target environment and runs the full bootstrap.

```dockerfile
FROM ubuntu:26.04
COPY . /src
WORKDIR /src
ENV PYNTARA_VAULT_PASSWORD=test
RUN bash i.sh
```

```python
@pytest.mark.docker
def test_bootstrap_in_container():
    """Run the full bootstrap flow in a Docker container."""
    import subprocess
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy project source
        subprocess.run(["cp", "-a", REPO_ROOT, tmpdir + "/src"], check=True)
        
        # Create test vault with known password
        subprocess.run([
            "python3", "-c", """
            from pykeepass import create_database
            create_database('/src/secrets/default.vault', password='test')
            """
        ], cwd=tmpdir + "/src", check=True)
        
        # Build Docker image
        dockerfile = f"""
        FROM ubuntu:26.04
        COPY src /src
        WORKDIR /src
        ENV PYNTARA_VAULT_PASSWORD=test
        RUN bash i.sh
        """
        Path(tmpdir + "/Dockerfile").write_text(dockerfile)
        
        result = subprocess.run(
            ["docker", "build", "--no-cache", "-t", "pyntara-test", tmpdir],
            capture_output=True, text=True, timeout=600
        )
        assert result.returncode == 0, f"Build failed: {result.stderr}"
```

**Pros:** ✅ Most realistic test ✅ Tests the entire stack (apt, uv, git, pyntara) ✅ Reproducible  
**Cons:** ❌ Very slow (~5-10 min per run) ❌ Requires Docker ❌ Hard to debug failures

### Approach C: Test `run_pyntara()` bash function in isolation
Source `i.sh` in a test script, mock external dependencies, and test `run_pyntara()` directly.

```bash
#!/bin/bash
# test_run_pyntara.sh
source ../i.sh

# Mock workspace
SCRIPT_DIR=$(mktemp -d)
cp -a /home/i/Downloads/Pyntara/. "$SCRIPT_DIR/"
cd "$SCRIPT_DIR"

# Mock external commands
uv() { echo "uv mock: $*"; }
git() { echo "git mock: $*"; }

# Set up test vault
python3 -c "
from pykeepass import create_database
create_database('secrets/default.vault', password='test')
"

# Test run_pyntara with env password
export PYNTARA_VAULT_PASSWORD=test
run_pyntara
echo "Exit code: $?"
```

**Pros:** ✅ Tests the bash script logic ✅ Fast ✅ No Docker  
**Cons:** ❌ Mocks hide real issues ❌ Bash tests are fragile ❌ Doesn't test the actual Python code

### Approach D: pytest with fake /dev/tty and process substitution
Create a test that mimics the exact `i.sh` stdin setup using process substitution.

```python
def test_cli_with_dev_tty_redirection(tmp_path):
    """Simulate the exact stdin setup from i.sh's run_pyntara()."""
    import subprocess
    
    # Create workspace with test config and vault
    workspace = tmp_path / "workspace"
    shutil.copytree(REPO_ROOT, workspace, ignore=shutil.ignore_patterns(".venv"))
    
    # Create a test vault with known password
    subprocess.run([
        "python3", "-c",
        "from pykeepass import create_database; create_database(str(VAULT), password='test')",
    ], env={**os.environ, "VAULT": str(workspace / "secrets" / "default.vault")}, check=True)
    
    # Run CLI with the same stdin setup as i.sh
    # i.sh does: timeout 7200 uv run pyntara <&3 where fd3=/dev/tty
    # We simulate this with a pipe
    proc = subprocess.Popen(
        ["uv", "run", "pyntara"],
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYNTARA_VAULT_PASSWORD": "wrong"},
    )
    
    try:
        stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("CLI hung for 30s with wrong password via env var!")
    
    assert proc.returncode != 0
    assert "CredentialsError" in stderr.decode() or "password" in stderr.decode().lower()
```

**Pros:** ✅ Tests the real CLI ✅ Simple setup ✅ Fast  
**Cons:** ❌ Uses env var instead of interactive prompt ❌ Doesn't test `getpass.getpass()` path

### Approach E: pytest-timeout as safety net for all tests
Add `pytest-timeout` to kill any hanging test automatically.

```toml
# pyproject.toml
[project.optional-dependencies]
dev = [
    "pytest-timeout>=2.3.0",
]

[tool.pytest.ini_options]
timeout = 60
timeout_method = "signal"
```

**Pros:** ✅ Zero code changes ✅ Kills any hanging test ✅ Works with all approaches  
**Cons:** ❌ Doesn't fix the root cause ❌ Just masks the hang

## My Recommendation: A + D + E

| Component | What it tests | Why |
|-----------|--------------|-----|
| **A: PTY subprocess** | Interactive password prompt via `/dev/tty` | Catches the exact hang bug with `getpass.getpass()` |
| **D: Env var subprocess** | Non-interactive path via `PYNTARA_VAULT_PASSWORD` | Tests the CI/CD use case |
| **E: pytest-timeout** | Safety net for all tests | Prevents CI from hanging indefinitely |

### 5 specific tests to implement:

1. **`test_cli_does_not_hang_with_wrong_password_via_pty`** — PTY-based, feed wrong password interactively, verify process exits within 30s
2. **`test_cli_does_not_hang_with_wrong_password_via_env`** — Set `PYNTARA_VAULT_PASSWORD=wrong`, run CLI, verify exit code != 0
3. **`test_cli_succeeds_with_correct_password_via_env`** — Set `PYNTARA_VAULT_PASSWORD=correct`, run CLI with test vault, verify exit code == 0
4. **`test_cli_shows_help_with_no_args`** — Run `uv run pyntara` with no args, verify help output (no hang)
5. **`test_bootstrap_script_parses_correctly`** — Source `i.sh` and verify all variables and functions are defined correctly

### Required code changes:

1. **`secrets_store.py`**: Add `kdf_timeout_sec` parameter to `_open_keepass_database()` with `ThreadPoolExecutor` wrapper
2. **`pyproject.toml`**: Add `pytest-timeout` to dev dependencies
3. **`tests/test_cli.py`**: New test file with PTY-based and env-var-based tests
4. **`conftest.py`**: Add fixture to create test KDBX with AES-KDF (fast) for CLI tests