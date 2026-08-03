# Bootstrap installer contract

This document is the source of truth for the bootstrap installer inst.sh.

## 1. Entry point

User downloads and runs: curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o inst.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash inst.sh
Startup check: script must be running as root. If not, exit with an error.

## 2. Package installation: optimistic apt strategy

First attempt: apt-get install -y without apt-get update.
If packages are missing (stale index), run apt-get update, then retry install.
This avoids wasting time on apt-get update during test runs where packages are already cached.
All apt operations run with DEBIAN_FRONTEND=noninteractive.

## 3. Installed packages (in order)

dialog — required for all interactive screens before anything else.
python3, python3-venv, git, curl, ca-certificates — minimal runtime dependencies.
bsdutils — provides script(1), which allocates a pseudo-tty for dialog screens
where stdin is not a terminal (e.g. under sudo).
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

Every command and its output are written to /var/log/pyntara/install.log.
The same output is streamed to the terminal in real time.
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

## 11. Interactive screens (via dialog)

All user interaction goes through the dialog utility. See docs/contracts/interactive-ui.md for the full UX flow. Summary:

Password prompt for production.vault (VAULT_PASSWORD_TIMEOUT 333s, 3 attempts, fallback to default.vault).
Install mode selector: minimal / server / desktop (auto-select default after DIALOG_TIMEOUT).
Task selection checkboxes (30s auto-accept via dialog --timeout, fallback to defaults with SLEEP_AFTER_IMPORTANT_MESSAGE).
Force-mode question: Yes / No (11s, default No).
Force-task checkboxes (only if Yes).

## 12. Secrets files

secrets/default.vault — Test/fallback KeePass database. In git.
secrets/production.vault — Production KeePass database. In git.
secrets/default.password — Password for default.vault (well-known test value). In git.
secrets/production.password — Password for production.vault. Not in git (.gitignore).

KeePass decryption is handled by a Python library, not shell tools.
