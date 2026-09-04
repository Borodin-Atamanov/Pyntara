#!/usr/bin/env bash
# Non-interactive Pyntara installer.
# The download and run command is documented in README.md under Start;
# README.md is the only place that holds the command.
set -euo pipefail

# Every step ends with a short English status message using variables, so
# the user gets the maximum useful information about what happened.
# Flow: root check, FHS directories, dependencies, uv, source fetch, uv sync,
# vault password and install mode resolution, engine launch.
# Requirements source: docs/contracts/bootstrap.md, docs/spec/install-modes.md.


# --- Implementation: phase 1.1 (root check) ---
# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f check_root &>/dev/null; then
check_root() {
    # Bootstrap contract, Entry point: must run as root, otherwise exit with an error.
    if [[ "$EUID" -ne 0 ]]; then
        echo "Error: Pyntara installer must run as root. Restart with: sudo bash $0" >&2
        exit 1
    fi
    echo "Running as root"
}
fi

# Implementation: phase 1.2 (FHS directories)
# FHS base directories, bootstrap contract, FHS paths.
# Overridable via environment so tests never touch real system paths.
CACHE_DIR="${PYNTARA_CACHE_DIR:-/var/cache/pyntara}"
STATE_DIR="${PYNTARA_STATE_DIR:-/var/lib/pyntara}"
LOG_DIR="${PYNTARA_LOG_DIR:-/var/log/pyntara}"

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f ensure_fhs_dirs &>/dev/null; then
ensure_fhs_dirs() {
    # Bootstrap contract, FHS paths: create cache, state and log directories.
    install -d "$CACHE_DIR" "$STATE_DIR" "$LOG_DIR"
    echo "FHS directories ready: $CACHE_DIR, $STATE_DIR, $LOG_DIR"
}
fi

# Implementation: phase 1.3 (logging)
# Single log file for the whole installer run, bootstrap contract, Logging.
# Overridable via environment so tests never touch real system paths.
LOG_FILE="${PYNTARA_LOG_FILE:-$LOG_DIR/install.log}"

# Journal identifier for own installer messages. An empty value disables
# journal forwarding, matching the engine semantics in logger.py; only an
# unset variable falls back to the default. The variable is not exported,
# so the Python engine keeps its own identifier (pyntara-engine) when
# launched by run_pyntara.
JOURNAL_IDENTIFIER="${PYNTARA_JOURNAL_IDENTIFIER-default}"

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f log &>/dev/null; then
log() {
    # Bootstrap contract, Logging: timestamped message written to log file
    # and terminal, duplicated into the system journal without the timestamp
    # because the journal stamps its own time.
    local message="$1"
    local timestamp
    timestamp="$(date +%Y-%m-%d-%H-%M-%S)"
    echo "[$timestamp] $message" | tee -a "$LOG_FILE"
    if [[ -n "$JOURNAL_IDENTIFIER" ]] && command -v systemd-cat >/dev/null 2>&1; then
        printf '%s\n' "$message" | systemd-cat --identifier "$JOURNAL_IDENTIFIER" || true
    fi
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f run_stream_to_log &>/dev/null; then
run_stream_to_log() {
    # Bootstrap contract, Logging: run a command, stream output to terminal and log file.
    # stderr is merged into stdout so errors are captured too.
    # The if guard captures the command exit code without triggering errexit.
    if "$@" 2>&1 | tee -a "$LOG_FILE"; then
        return 0
    fi
    return "${PIPESTATUS[0]}"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f run_logged &>/dev/null; then
run_logged() {
    # Bootstrap contract, Logging: run a command, stream output to terminal and log file.
    run_stream_to_log "$@"
}
fi

# Implementation: phase 1.4 (timing)
# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f run_timed &>/dev/null; then
run_timed() {
    # Bootstrap contract, Verbose execution and timing: run a command, time it, log duration and exit code.
    local start rc elapsed
    start="$(date +%s)"
    # The if guard captures the command exit code without triggering errexit.
    if run_stream_to_log "$@"; then
        rc=0
    else
        rc=$?
    fi
    elapsed="$(($(date +%s) - start))"
    log "Finished in ${elapsed}s with exit code ${rc}: $*"
    return "$rc"
}
fi

# Implementation: phase 2.1 (apt package install)
# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f apt_update_skipped &>/dev/null; then
apt_update_skipped() {
    # Bootstrap contract, Package installation: true when PYNTARA_SKIP_APT_UPDATE
    # is 1, true or yes; mirrors the engine flag semantics in pyntara.py. Any
    # other value, including 0, does not skip the refresh.
    local value
    value="${PYNTARA_SKIP_APT_UPDATE:-}"
    [[ -n "$value" ]] && [[ "${value,,}" == "1" || "${value,,}" == "true" || "${value,,}" == "yes" ]]
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f apt_install &>/dev/null; then
apt_install() {
    # Bootstrap contract, Package installation: refresh the apt index before the
    # install by default, so packages resolve from a fresh index; skip the refresh
    # when PYNTARA_SKIP_APT_UPDATE is set (test or offline runs). A failed refresh
    # is a warning, not a failure: the existing index may still satisfy the install.
    # There is no optimistic first attempt and no retry.
    # All apt operations are noninteractive.
    local packages=("$@")
    if apt_update_skipped; then
        log "Package index refresh skipped"
    else
        log "Refreshing package index before install"
        if ! DEBIAN_FRONTEND=noninteractive run_timed apt-get update; then
            log "Package index refresh failed, continuing with the existing index"
        fi
    fi
    if DEBIAN_FRONTEND=noninteractive run_timed apt-get install -y "${packages[@]}"; then
        log "Packages installed: ${packages[*]}"
        return 0
    fi
    log "Package install failed: ${packages[*]}"
    return 1
}
fi

# Implementation: phase 2.2 (package set)
# Minimal runtime dependencies, bootstrap contract, Installed packages, in required order.
RUNTIME_PACKAGES=(python3 python3-venv git curl ca-certificates)

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f install_dependencies &>/dev/null; then
install_dependencies() {
    # Bootstrap contract, Installed packages: install only the packages that are missing.
    # dpkg -s reports installed state, so repeated runs install nothing new.
    local missing=()
    local pkg
    for pkg in "${RUNTIME_PACKAGES[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null; then
            missing+=("$pkg")
        fi
    done
    if [[ "${#missing[@]}" -eq 0 ]]; then
        log "All runtime packages already installed: ${RUNTIME_PACKAGES[*]}"
        return 0
    fi
    log "Installing runtime packages: ${missing[*]}"
    apt_install "${missing[@]}"
}
fi

# Implementation: phase 2.3 (uv package manager)
# Official Astral installer, bootstrap contract, Installed packages.
# uv cache lives in its own subdirectory of the FHS cache directory
# (bootstrap contract, FHS paths). It must not equal the cache root itself:
# the git clone lives at $CACHE_DIR/repo, and uv refuses a project directory
# that is inside its own cache directory.
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
export UV_CACHE_DIR="$CACHE_DIR/uv-cache"

# curl timeout and retries of the uv installer download, mirroring the
# [engine] curl_timeout_seconds and curl_retries config values. The
# installer runs before the config exists, so the values live here as
# overridable environment defaults (bootstrap contract, Runtime
# configuration).
CURL_TIMEOUT_SECONDS="${PYNTARA_CURL_TIMEOUT_SECONDS:-777}"
CURL_RETRIES="${PYNTARA_CURL_RETRIES:-13}"

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f install_uv &>/dev/null; then
install_uv() {
    # Install uv only when missing. The Astral installer runs in a subprocess
    # so its own environment changes never leak into this shell.
    if command -v uv &>/dev/null; then
        log "uv already installed: $(command -v uv)"
        return 0
    fi
    local installer="$CACHE_DIR/uv-install.sh"
    log "Downloading uv installer to $installer"
    run_timed curl -LsSf --max-time "$CURL_TIMEOUT_SECONDS" --retry "$CURL_RETRIES" --retry-delay 3 --retry-connrefused -o "$installer" "$UV_INSTALL_URL"
    log "Running uv installer"
    # The installer runs in a subprocess so its own environment changes never
    # leak into this shell. UV_CACHE_DIR is set for the child explicitly.
    env UV_CACHE_DIR="$CACHE_DIR" bash "$installer" 2>&1 | tee -a "$LOG_FILE"
    # The Astral installer places uv into $HOME/.local/bin, which is not on
    # root's PATH by default, so add it explicitly for later phases.
    export PATH="$HOME/.local/bin:$PATH"
    log "uv installed: $(command -v uv)"
}
fi

# Implementation: phase 3.1 (source delivery via git)

# Repository and branch are configurable so tests never touch the real remote.
REPO_URL="${PYNTARA_REPO_URL:-https://github.com/Borodin-Atamanov/Pyntara.git}"
REPO_BRANCH="${PYNTARA_REPO_BRANCH:-main}"
SOURCE_DIR="${PYNTARA_SOURCE_DIR:-$CACHE_DIR/repo}"

# Installer version, bumped together with src/pyntara/__init__.py by the
# pre-commit hook (hooks/pre-commit). The value is informational.
PYNTARA_VERSION="0.3.183"

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f fetch_source &>/dev/null; then
fetch_source() {
    # Bootstrap contract, Source delivery: clone the repo, or update an existing clone.
    # A broken clone (no .git) is removed and recreated instead of failing.
    if [[ ! -d "$SOURCE_DIR" || -z "$(ls -A "$SOURCE_DIR")" ]]; then
        log "Cloning repository: $REPO_URL branch $REPO_BRANCH"
        run_timed git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$SOURCE_DIR"
        log "Repository cloned to $SOURCE_DIR"
    elif [[ ! -d "$SOURCE_DIR/.git" ]]; then
        log "Removing broken clone at $SOURCE_DIR"
        rm -rf "$SOURCE_DIR"
        log "Cloning repository: $REPO_URL branch $REPO_BRANCH"
        run_timed git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$SOURCE_DIR"
        log "Repository cloned to $SOURCE_DIR"
    else
        log "Updating existing repository at $SOURCE_DIR"
        # A corrupted object store (empty or truncated .git objects) makes
        # fetch fail with exit code 128, so re-clone instead of aborting.
        if run_timed git -C "$SOURCE_DIR" fetch && run_timed git -C "$SOURCE_DIR" reset --hard "origin/$REPO_BRANCH"; then
            log "Repository updated to origin/$REPO_BRANCH"
        else
            log "Repository update failed, removing broken clone at $SOURCE_DIR"
            rm -rf "$SOURCE_DIR"
            log "Cloning repository: $REPO_URL branch $REPO_BRANCH"
            run_timed git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$SOURCE_DIR"
            log "Repository cloned to $SOURCE_DIR"
        fi
    fi
}
fi

# Implementation: phase 3.2 (python environment via uv)

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f setup_python &>/dev/null; then
setup_python() {
    # Bootstrap contract, Python environment: uv sync in the repo directory.
    # Use --locked when the lockfile is current, plain sync otherwise.
    # cd runs in a subshell so the caller's working directory never changes.
    if [[ ! -d "$SOURCE_DIR" ]]; then
        log "Source directory missing: $SOURCE_DIR"
        return 1
    fi
    if [[ ! -f "$SOURCE_DIR/uv.lock" ]]; then
        # No lockfile yet: sync generates it. Avoids a confusing uv error
        # from `lock --check` on the very first run.
        log "No uv.lock found, syncing without --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync )
    elif ( cd "$SOURCE_DIR" && run_timed uv lock --check ); then
        log "Lockfile is current, syncing with --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync --locked )
    else
        log "Lockfile outdated, syncing without --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync )
    fi
    log "Python environment ready in $SOURCE_DIR"
}
fi

# Implementation: phase 3.3 (launch pyntara)

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f run_pyntara &>/dev/null; then
run_pyntara() {
    # Bootstrap contract, Python environment: launch uv run pyntara.
    # Bootstrap contract, No timeout on Pyntara: no time limit on the pyntara process.
    # cd runs in a subshell so the caller's working directory never changes.
    # The if guard captures the exit code without triggering errexit.
    if [[ ! -d "$SOURCE_DIR" ]]; then
        log "Source directory missing: $SOURCE_DIR"
        return 1
    fi
    local rc=0
    log "Starting Pyntara from $SOURCE_DIR"
    if ( cd "$SOURCE_DIR" && run_timed uv run pyntara "$@" ); then
        rc=0
    else
        rc=$?
    fi
    log "Pyntara finished with exit code $rc"
    return "$rc"
}
fi

# Implementation: phase 4 (runtime configuration)
# Vault and task paths are overridable via environment so tests never touch
# real system files.
PRODUCTION_VAULT="${PYNTARA_PRODUCTION_VAULT:-$SOURCE_DIR/secrets/production.vault}"
DEFAULT_VAULT="${PYNTARA_DEFAULT_VAULT:-$SOURCE_DIR/secrets/default.vault}"
DEFAULT_VAULT_PASSWORD_FILE="${PYNTARA_DEFAULT_PASSWORD_FILE:-$SOURCE_DIR/secrets/default.password}"
# Countdown for the default vault fallback notice. The notice requires no
# input; the user may interrupt with Ctrl-C.
FALLBACK_NOTICE_TIMEOUT="${PYNTARA_FALLBACK_NOTICE_TIMEOUT:-7}"

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f show_message &>/dev/null; then
show_message() {
    # Print a message as plain text without waiting for any input. The
    # installer never blocks on the user: messages are informational.
    log "$1"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f log_status &>/dev/null; then
log_status() {
    # Print a status message as plain text without pausing for Enter.
    # Status messages report what the installer decided.
    log "$1"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f show_countdown &>/dev/null; then
show_countdown() {
    # Print a message with a visible second-by-second countdown. No input is
    # read: the notice is informational and the user may interrupt the
    # installer with Ctrl-C. The message is logged once, the countdown is
    # cosmetic.
    local seconds="${1:-7}"
    local message="$2"
    log "$message"
    local remaining
    for ((remaining = seconds; remaining >= 1; remaining--)); do
        printf '\r%s -- %ss left ' "$message" "$remaining" >&2
        sleep 1
    done
    # The trailing newline ends the carriage-return countdown line.
    printf '\n' >&2
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f check_vault_password &>/dev/null; then
check_vault_password() {
    # Verify a candidate password by asking the Python engine to open the
    # KeePass database. The shell must not decrypt vaults itself (bootstrap
    # contract, Secrets files): decryption happens in pyntara check-vault via
    # pykeepass. The password travels through stdin, never as an argument,
    # so it cannot leak into the process list or the run_timed log line.
    local password="$1"
    ( cd "$SOURCE_DIR" && printf '%s' "$password" | run_timed uv run pyntara check-vault --vault "$PRODUCTION_VAULT" )
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f fallback_to_default_vault &>/dev/null; then
fallback_to_default_vault() {
    # Export the well-known fallback password so the Python engine can decrypt
    # default.vault. A missing password file is fatal: without it no vault can
    # be opened, so the installer must stop with an explicit error.
    if [[ ! -f "$DEFAULT_VAULT_PASSWORD_FILE" ]]; then
        show_message "ERROR: default vault password file not found at $DEFAULT_VAULT_PASSWORD_FILE. Cannot fall back to default vault."
        return 1
    fi
    local password
    # The file holds one password on the first line; read only that line.
    password="$(head -n 1 "$DEFAULT_VAULT_PASSWORD_FILE")"
    export PYNTARA_VAULT_PASSWORD="$password"
    export PYNTARA_VAULT_SOURCE="default"
    # A status line, not a confirmation: nothing follows that needs an
    # explicit Enter, so log_status does not block on read.
    log_status "Using default vault $DEFAULT_VAULT with password from $DEFAULT_VAULT_PASSWORD_FILE"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f prompt_vault_password &>/dev/null; then
prompt_vault_password() {
    # Resolve the vault password and source for the Python engine.
    # Non-interactive: a password from the environment is used as-is when
    # PYNTARA_VAULT_SOURCE is set, otherwise the source is auto-detected:
    # production wins when the password opens production.vault, default
    # when it matches the well-known default.password. An unmatched
    # password falls back to the default vault after a countdown notice,
    # same as when no password is provided at all.
    if [[ -n "${PYNTARA_VAULT_PASSWORD:-}" ]]; then
        if [[ -n "${PYNTARA_VAULT_SOURCE:-}" ]]; then
            log_status "Vault password from environment, source: $PYNTARA_VAULT_SOURCE"
            return 0
        fi
        if [[ -f "$PRODUCTION_VAULT" ]] && check_vault_password "$PYNTARA_VAULT_PASSWORD"; then
            export PYNTARA_VAULT_SOURCE="production"
            log_status "Vault password from environment, auto-detected source: production"
            return 0
        fi
        if [[ -f "$DEFAULT_VAULT_PASSWORD_FILE" ]]; then
            local env_default_password
            env_default_password="$(head -n 1 "$DEFAULT_VAULT_PASSWORD_FILE")"
            if [[ "$PYNTARA_VAULT_PASSWORD" == "$env_default_password" ]]; then
                export PYNTARA_VAULT_SOURCE="default"
                log_status "Vault password from environment, auto-detected source: default"
                return 0
            fi
        fi
        show_countdown "$FALLBACK_NOTICE_TIMEOUT" "ERROR: PYNTARA_VAULT_PASSWORD does not match any vault. Falling back to default vault."
        fallback_to_default_vault
        return $?
    fi
    # No password in the environment: the production vault cannot be opened,
    # so the installer falls back to the default vault. A countdown notice
    # informs the user; no input is required and the user may interrupt with
    # Ctrl-C.
    show_countdown "$FALLBACK_NOTICE_TIMEOUT" "ERROR: production vault password not provided. Falling back to default vault."
    fallback_to_default_vault
    return $?
}
fi

# Implementation: phase 4.2 (install mode selection)

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f detect_default_mode &>/dev/null; then
detect_default_mode() {
    # Choose the default install mode without asking the user, install-modes
    # spec: desktop when a desktop session is present, otherwise server.
    # PYNTARA_DEFAULT_INSTALL_MODE overrides detection for tests and for
    # unattended runs where no session information is available.
    if [[ -n "${PYNTARA_DEFAULT_INSTALL_MODE:-}" ]]; then
        echo "$PYNTARA_DEFAULT_INSTALL_MODE"
        return 0
    fi
    # A desktop session sets one of these variables; their absence means a
    # server or a bare login shell.
    if [[ -n "${XDG_CURRENT_DESKTOP:-}" || -n "${DESKTOP_SESSION:-}" ]]; then
        echo "desktop"
        return 0
    fi
    # No session variables: a running desktop process still means desktop.
    # pgrep runs even when the caller has no tty, and exit code 1 means no
    # process matched, so desktop is only chosen when a match exists.
    if pgrep -x kwin_wayland &>/dev/null || pgrep -x kwin_x11 &>/dev/null || pgrep -x plasmashell &>/dev/null || pgrep -x gnome-shell &>/dev/null; then
        echo "desktop"
        return 0
    fi
    echo "server"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f prompt_install_mode &>/dev/null; then
prompt_install_mode() {
    # Decide the install mode and export it for the Python engine.
    # PYNTARA_INSTALL_MODE fixes the mode; otherwise the system default is
    # detected. No screen is shown.
    if [[ -n "${PYNTARA_INSTALL_MODE:-}" ]]; then
        log_status "Install mode from environment: $PYNTARA_INSTALL_MODE"
        return 0
    fi
    local mode
    mode="$(detect_default_mode)"
    export PYNTARA_INSTALL_MODE="$mode"
    log_status "Install mode (default): $mode"
}
fi

# Guard so the test harness can inject a mock main via source (bootstrap contract, Testability).
if ! declare -f main &>/dev/null; then
main() {
    # The version is the very first line of output: it must be visible even
    # when a later phase fails, including the root check right after it.
    echo "Pyntara installer version $PYNTARA_VERSION"
    check_root
    ensure_fhs_dirs
    log "Install log started: $LOG_FILE"
    install_dependencies
    install_uv
    fetch_source
    setup_python
    # Phase 4.1: the vault password is resolved after the Python environment
    # is ready. A vault failure is fatal: the engine cannot provision
    # without secrets.
    if ! prompt_vault_password; then
        log "Vault setup failed, aborting"
        exit 1
    fi
    # Phase 4.2: the install mode is fixed after the vault is open. The
    # engine resolves the task set itself: the mode defaults or
    # PYNTARA_TASKS with dependencies.
    prompt_install_mode
    run_pyntara "$@"
    log "Bootstrap finished, pyntara installer version $PYNTARA_VERSION"
}
fi

# Run only on direct execution so tests can source this file safely.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

