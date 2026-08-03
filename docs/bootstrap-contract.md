# Bootstrap installer contract

This document is the source of truth for the bootstrap installer `inst.sh`.

## 1. Entry point

- User downloads and runs: `curl ... -o insta.sh https://raw.githubusercontent.com/.../inst.sh && sudo bash insta.sh`
- The only startup check: script must be running as root. If not, exit with an error.
- No OS detection. No distribution or version checks.

## 2. Package installation: optimistic apt strategy

- First attempt: `apt-get install -y` without `apt-get update`.
- If packages are missing (stale index), run `apt-get update`, then retry install.
- This avoids wasting time on `apt-get update` during test runs where packages are already cached.
- All apt operations run with `DEBIAN_FRONTEND=noninteractive`.

## 3. Installed packages (in order)

1. `dialog` — required for all interactive screens before anything else.
2. `python3`, `python3-venv`, `git`, `curl`, `ca-certificates` — minimal runtime dependencies.
3. `uv` — Python package manager, installed via official Astral script.

User-level utilities (`htop`, `nload`, `wget`, etc.) are not installed by the bootstrap. They are handled by separate Pyntara tasks.

## 4. Source delivery: git only

- The only supported method: `git clone --depth 1` into a temporary directory.
- On repeated runs: if the directory already exists and contains files, `git fetch` + reset to the latest revision instead of re-cloning from scratch.
- No archive downloads, no USB fallback, no local source copy.

## 5. FHS paths

All directories follow POSIX Linux standards as adopted by Ubuntu:

| Path | Purpose |
|---|---|
| `/var/cache/pyntara/` | Git clone cache, uv cache |
| `/var/lib/pyntara/` | Runtime state, workspaces |
| `/var/log/pyntara/` | Install logs |

## 6. Python environment

- `uv sync` in the cloned repository directory.
- If lockfile is current, use `--locked`. Otherwise sync without it.
- After sync, launch: `uv run pyntara` (no timeout).

## 7. Verbose execution and timing

- All programs run in maximum verbosity, non-interactive mode.
- Every significant command is wrapped in `time` so the user sees execution duration.
- Trivial commands (`echo`, `mkdir`, `cd`) are not wrapped.

## 8. No timeout on Pyntara

- The `uv run pyntara` process runs without any time limit. Provisioning tasks take as long as they need.

## 9. Logging

- Every command and its output are written to `/var/log/pyntara/install.log`.
- The same output is streamed to the terminal in real time.
- Timestamps use `YYYY-MM-DD-HH-MM-SS` format.
- Logging is always verbose. There is no quiet mode.

## 10. Testability: conditional function declaration

Every function in `inst.sh` is declared with a guard:

```bash
if ! declare -f function_name &>/dev/null; then
function_name() {
    ...
}
fi
```

If a function is already declared (test harness injected a mock via `source`), the script skips its own declaration. This allows isolated testing of every function by substitution.

## 11. Interactive screens (via dialog)

All user interaction goes through the `dialog` utility. See `docs/interactive-ui-contract.md` for the full UX flow. Summary:

1. Password prompt for `production.vault` (11s timeout, 3 attempts, fallback to `default.vault`).
2. Install mode selector: `minimal` / `server` / `desktop` (11s auto-select).
3. Task selection checkboxes (30s auto-accept).
4. Force-mode question: Yes / No (11s, default No).
5. Force-task checkboxes (only if Yes).

## 12. Secrets files

| File | In git | Purpose |
|---|---|---|
| `secrets/default.vault` | Yes | Test/fallback KeePass database |
| `secrets/production.vault` | Yes | Production KeePass database |
| `secrets/default.password` | Yes | Password for `default.vault` (well-known test value) |
| `secrets/production.password` | **No** (`.gitignore`) | Password for `production.vault`, must never be committed |

KeePass decryption is handled by a Python library, not shell tools.
