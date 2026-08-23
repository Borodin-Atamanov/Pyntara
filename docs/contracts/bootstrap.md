# Bootstrap installer contract

This document is the source of truth for the bootstrap installer inst.sh.

## 1. Entry point

The download and run command is documented in README.md under Start; README.md is the only place that holds it. The command writes the installer into a unique file in /tmp via mktemp, so it works from any working directory, including read-only roots, then runs it under sudo.
The installer runs non-interactively and never asks the user anything. The production vault password is optional: without PYNTARA_VAULT_PASSWORD, or with a password that matches no vault, the installer shows a countdown notice and falls back to the default vault. Optional overrides: PYNTARA_VAULT_SOURCE, PYNTARA_INSTALL_MODE, PYNTARA_TASKS.
Startup check: script must be running as root. If not, exit with an error.

## 2. Package installation: optimistic apt strategy

First attempt: apt-get install -y without apt-get update.
If packages are missing (stale index), run apt-get update, then retry install.
This avoids wasting time on apt-get update during test runs where packages are already cached.
All apt operations run with DEBIAN_FRONTEND=noninteractive.

## 3. Installed packages (in order)

python3, python3-venv, git, curl, ca-certificates — minimal runtime dependencies.
uv — Python package manager, installed via official Astral script.

## 4. Source delivery: git only

The only supported method: git clone --depth 1 into a temporary directory.
On repeated runs: if the directory already exists and contains files, git fetch + reset to the latest revision instead of re-cloning from scratch.

## 5. FHS paths

All directories follow POSIX Linux standards as adopted by Ubuntu:

/var/cache/pyntara/ — Git clone cache, uv cache
/var/lib/pyntara/ — Runtime state, workspaces
/var/log/pyntara/ — Install logs

## 6. Python environment

uv sync in the cloned repository directory.
If lockfile is current, use --locked. Otherwise sync without it.
After sync, launch: uv run pyntara (no timeout).

## 7. Verbose execution and timing

All programs run in maximum verbosity, non-interactive mode.
Every significant command is wrapped in time so the user sees execution duration.
Trivial commands (echo, mkdir, cd) are not wrapped.

## 8. No timeout on Pyntara

The uv run pyntara process runs without any time limit. Provisioning tasks take as long as they need.

## 9. Logging

Every own message of the installer (log) goes to the system journal as the primary destination, with the identifier pyntara-install and without the console timestamp: the journal stamps its own time. The Python engine mirrors its own messages with the identifier pyntara-engine ([Central logging](../guides/project-rules.md#13-central-logging)). Journal forwarding is best effort: a missing systemd-cat or a failed write never stops the run.
The full stream is persisted to /var/log/pyntara/install.log as a residual copy: every command and its output are written there, and the same output is streamed to the terminal in real time.
Timestamps use YYYY-MM-DD-HH-MM-SS format.
Logging is always verbose. There is no quiet mode.

## 10. Testability: conditional function declaration

Every function in inst.sh is declared with a guard:

if ! declare -f function_name &>/dev/null; then
function_name() {
    ...
}
fi

If a function is already declared (test harness injected a mock via source), the script skips its own declaration. This allows isolated testing of every function by substitution.

## 11. Runtime configuration (environment only)

The installer never shows interactive screens. All user interaction happens through environment variables:

Password: PYNTARA_VAULT_PASSWORD (optional; without it, or when it matches no vault, the default vault is used after a countdown notice).
Vault source: PYNTARA_VAULT_SOURCE (optional, auto-detected when omitted).
Install mode: PYNTARA_INSTALL_MODE (optional, auto-detected when omitted).
Task selection: PYNTARA_TASKS (optional, space-separated task names; the engine resolves dependencies, otherwise the mode defaults are used).
Apt index refresh: PYNTARA_SKIP_APT_UPDATE (optional; 1, true or yes skips the apt-get update that cli_tools runs before the first install).

The dialog-based screens were removed together with their supporting functions (select_tasks, select_install_mode, prompt_password_input) and the task-catalog command. The interactive UI contract was deleted.

## 12. Secrets files

secrets/default.vault — Test/fallback KeePass database. In git.
secrets/production.vault — Production KeePass database. In git.
secrets/default.password — Password for default.vault (well-known test value). In git.
secrets/production.password — Password for production.vault. Not in git (.gitignore).

KeePass decryption is handled by a Python library, not shell tools.
