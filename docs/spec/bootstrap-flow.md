# Bootstrap flow

The system starts via inst.sh (a regular Bash script).
The script is downloaded from GitHub and run as superuser:

```
curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o insta.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash inst.sh
```

The only startup check: the script must be running as root.

## Package installation

The script installs packages: try apt-get install first, run apt-get update only if packages are missing, then retry.

Install order: dialog first, then python3, python3-venv, git, curl, ca-certificates, then uv.

User-level utilities (htop, nload, wget, and similar) are installed by separate tasks, not by the bootstrap.

## Source delivery

git clone --depth 1 only. On repeated runs, git fetch + reset in the existing directory instead of re-cloning.

## Execution

All commands run in maximum verbosity, non-interactive mode. Significant commands are wrapped in time.

After cloning, uv sync prepares the Python environment and uv run pyntara starts the CLI.

There is no timeout on the Pyntara process. It runs as long as needed.

## Interactive UI

All interactive screens use dialog. See docs/contracts/interactive-ui.md.

## Testability

Every function in inst.sh is declared with a guard (if ! declare -f name ...) so tests can substitute any function via source.

Full contract: docs/contracts/bootstrap.md.
