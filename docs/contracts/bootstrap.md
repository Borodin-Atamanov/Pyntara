# Bootstrap installer contract

This document is the source of truth for the bootstrap installer inst.sh.

## Entry point

The download and run commands are documented in README.md under Start; README.md is the only place that holds them. They download the launcher pyntara.sh into /dev/shm, where no file survives a reboot, and run it as root from there.
The installer runs non-interactively and never asks the user anything. The production vault password is optional: without PYNTARA_VAULT_PASSWORD, or with a password that matches no vault, the installer shows a countdown notice and falls back to the default vault. The fallback is a warning and never a detail: the installer writes a WARNING line into the log for every reason that leads to it, and the engine reports a run whose source is the default vault as a warning of its own, whatever that reason was, which fails the run exit code. Optional overrides: PYNTARA_VAULT_SOURCE, PYNTARA_INSTALL_MODE, PYNTARA_TASKS.
Startup check: script must be running as root. If not, exit with an error.

## Launcher pyntara.sh

pyntara.sh is the user-facing entry point of a machine: it downloads inst.sh from the raw branch of the repository into /dev/shm and runs it as root. It carries no version line, so it is not a version carrier.
Run parameters live in the file and only in the file. The launcher clears every PYNTARA_ name inherited from the caller before it sets its own values, so an exported variable never reaches the installer; a name left commented out stays unset and the installer or the engine resolves its own default.
The vault password is the PYNTARA_VAULT_PASSWORD line, and it is the only password line of the file: the launcher hands its value to the installer and never decides the vault itself, so the second copy a comparison would need does not exist there (a replaced copy once silenced the production password, 2026-09-19). The shipped value is the published password of default.vault, and it is the shape of the line a user replaces with the production password. The installer decides: production when the password opens production.vault, default when it matches default.password, and the same warning and wait when it opens neither. The launcher never sets PYNTARA_VAULT_SOURCE and never reads the terminal, so the run has no interactive input. The production password is never a value in the repository, never an argument and never a log line.
One log for the whole run: the launcher exports PYNTARA_LOG_DIR, PYNTARA_LOG_FILE and PYNTARA_JOURNAL_IDENTIFIER, appends its own download phase to the same file the installer writes and reports under the same journal identifier, so the two halves cannot drift apart. PYNTARA_REPO_URL and PYNTARA_REPO_BRANCH are exported too, and the branch selects both the downloaded installer and the checkout the installer clones.

## Package installation: apt update before install

The apt index is refreshed before the first install by default, so packages resolve from a fresh index.  
PYNTARA_SKIP_APT_UPDATE (1, true or yes) skips the refresh for test or offline runs.  
A failed refresh is logged as a warning and the install continues with the existing index.  
There is no optimistic first attempt and no retry.  
All apt operations run with DEBIAN_FRONTEND=noninteractive.

## Installed packages (in order)

python3, python3-venv, git, curl, ca-certificates — minimal runtime dependencies.  
uv — Python package manager, installed via official Astral script.

## Source delivery: git only

The only supported method: git clone --depth 1 into a temporary directory.  
On repeated runs: if the directory already exists and contains files, git fetch + reset to the latest revision instead of re-cloning from scratch.

## FHS paths

All directories follow POSIX Linux standards as adopted by Ubuntu:

/var/cache/pyntara/ — Git clone cache, uv cache  
/var/lib/pyntara/ — Runtime state, workspaces  
/var/log/pyntara/ — Install logs

## Python environment

uv sync in the cloned repository directory.  
If lockfile is current, use --locked. Otherwise sync without it.  
After sync, launch: uv run pyntara (no timeout).

## Verbose execution and timing

All programs run in maximum verbosity, non-interactive mode.  
Every significant command is wrapped in time so the user sees execution duration.  
The engine mirrors this in run_command: every command is reported with the lines `  run : <command>` and `  /run: <exit_code> <seconds>s <command>`, so the install log shows the duration and exit code of every command.
Trivial commands (echo, mkdir, cd) are not wrapped.

## No timeout on Pyntara

The uv run pyntara process runs without any time limit. Provisioning tasks take as long as they need.

## Logging

Every own message of the installer (log) goes to the system journal as the primary destination, with the identifier pyntara-install and without the console timestamp: the journal stamps its own time. The Python engine mirrors its own messages under the journal_identifier value of the engine values module (src/pyntara/values/engine.py), handed to the logger before the first message ([Central logging](../guides/project-rules.md#central-logging)); the two identifiers stay distinct, so a journal query separates the bootstrap from the run it launched. Journal forwarding is best effort: a missing systemd-cat or a failed write never stops the run.
The full stream is persisted to /var/log/pyntara/install.log as a residual copy: every command and its output are written there, and the same output is streamed to the terminal in real time.  
Timestamps use YYYY-MM-DD-HH-MM-SS format.  
Logging is always verbose. There is no quiet mode.

## Testability: conditional function declaration

Every function in inst.sh is declared with a guard:

if ! declare -f function_name &>/dev/null; then
function_name() {
    ...
}
fi

If a function is already declared (test harness injected a mock via source), the script skips its own declaration. This allows isolated testing of every function by substitution.

## Runtime configuration (environment only)

The installer never shows interactive screens. All user interaction happens through environment variables:

Password: PYNTARA_VAULT_PASSWORD (optional; without it, or when it matches no vault, the default vault is used after a countdown notice).  
Vault source: PYNTARA_VAULT_SOURCE (optional, auto-detected when omitted).  
Install mode: PYNTARA_INSTALL_MODE (optional, auto-detected when omitted).  
Task selection: PYNTARA_TASKS (optional, space-separated task names; the engine resolves dependencies, otherwise the mode defaults are used).  
Apt index refresh: PYNTARA_SKIP_APT_UPDATE (optional; 1, true or yes skips the apt-get update that the package install tasks run before the first install).

The dialog-based screens were removed together with their supporting functions (select_tasks, select_install_mode, prompt_password_input) and the task-catalog command. The interactive UI contract was deleted.

## Secrets files

secrets/default.vault — Test/fallback KeePass database. In git.  
secrets/production.vault — Production KeePass database. In git.  
secrets/default.password — Password for default.vault (well-known test value). In git.  
secrets/production.password — Password for production.vault. Not in git (.gitignore).

KeePass decryption is handled by a Python library, not shell tools.
