#!/usr/bin/env bash
# Pyntara launcher: downloads the bootstrap installer inst.sh and runs it as root.
# The parameters of the run live in this file and nowhere else: the inherited
# environment is ignored, so an exported PYNTARA_ name never reaches the
# installer. Requirements source: docs/contracts/bootstrap.md.
set -euo pipefail

# Every PYNTARA_ name inherited from the caller is cleared before the values
# below are set, so this file is the only source of the run parameters.
for inherited_name in $(compgen -e PYNTARA_ 2>/dev/null || true); do
    unset "$inherited_name"
done

# Repository and branch of the run: the branch selects both the installer
# downloaded below and the checkout the installer clones.
PYNTARA_REPO_URL="https://github.com/Borodin-Atamanov/Pyntara.git"
PYNTARA_REPO_BRANCH="main"

# Source of the installer, the raw branch of the same repository, and the
# temporary copy. /dev/shm is a memory filesystem, so the downloaded installer
# never touches the disk.
INSTALLER_URL_BASE="https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara"
INSTALLER_PATH="/dev/shm/pyntara-inst.sh"

# One log for the whole run: the launcher and the installer append to the same
# file, and both halves report to the journal under the same identifier.
PYNTARA_LOG_DIR="/var/log/pyntara"
PYNTARA_LOG_FILE="$PYNTARA_LOG_DIR/install.log"
PYNTARA_JOURNAL_IDENTIFIER="pyntara-install"

# Vault password of the run. The value below is the published password of the
# default vault; it is also the shape of the line to replace with the password
# of the production vault, which is never committed and never written to a log.
# While the value is unchanged the installer runs without a password, so it
# warns, waits and falls back to the default vault; a password that opens no
# vault takes the same path, and one that opens production.vault is used.
PYNTARA_VAULT_PASSWORD_DEFAULT="test-password-123"
PYNTARA_VAULT_PASSWORD="$PYNTARA_VAULT_PASSWORD_DEFAULT"

# Install mode and task selection. An omitted value is resolved by the engine:
# the mode is auto-detected, the task set is the default set of the mode.
# Uncomment a line to fix the value for this run.
# PYNTARA_INSTALL_MODE="desktop"
# The whole catalog in catalog order (src/pyntara/values/tasks.py).
# PYNTARA_TASKS="add_extra_repos hostname swapfile_service_install zram_service zswap_service local_vault_setup system_metrics_setup ssh_daemon_setup ssh_client_setup port_forwarding_setup kde_keyboard_setup kde_settings vocalinux_setup nextdns_setup_system_wide dnsproxy_setup i2pd_service_setup yggdrasil_service_setup tor_setup three_x_ui_xray_setup sotavpn_setup rustdesk_setup imagemagick_setup ffmpeg_setup upnp_forwarding_setup system_metrics_initial_collect chrome_setup playwright_setup cli_tools telegram_setup scrcpy_setup commit_final_system_metrics"
# PYNTARA_FORCE_TASKS=""
# PYNTARA_SKIP_APT_UPDATE=1

# Guards so the test harness can inject a mock via source (bootstrap contract,
# Testability).
if ! declare -f launcher_log &>/dev/null; then
launcher_log() {
    # Own message of the launcher: timestamped into the log file and the
    # terminal, plain text into the journal under the installer identifier. An
    # empty identifier disables journal forwarding, matching the installer.
    local message="$1"
    local timestamp
    timestamp="$(date +%Y-%m-%d-%H-%M-%S)"
    echo "[$timestamp] $message" | tee -a "$PYNTARA_LOG_FILE"
    if [[ -n "$PYNTARA_JOURNAL_IDENTIFIER" ]] && command -v systemd-cat >/dev/null 2>&1; then
        printf '%s\n' "$message" | systemd-cat --identifier "$PYNTARA_JOURNAL_IDENTIFIER" || true
    fi
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f check_root &>/dev/null; then
check_root() {
    # The installer installs packages and writes system files, so the launcher
    # runs as root and an unprivileged start stops before anything happens.
    if [[ "$EUID" -ne 0 ]]; then
        printf 'Error: the Pyntara launcher must run as root. Start it with: sudo bash %s\n' "$0" >&2
        exit 1
    fi
    echo "Running as root"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f prepare_log_dir &>/dev/null; then
prepare_log_dir() {
    # The launcher logs before the installer creates its own directories.
    install -d "$PYNTARA_LOG_DIR"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f download_installer &>/dev/null; then
download_installer() {
    # The installer is fetched from the branch of the run, so a branch run
    # differs from a main run by one value above. The exit code of curl is kept
    # and named in the log, so a failed download reports its own reason.
    local installer_url="$INSTALLER_URL_BASE/$PYNTARA_REPO_BRANCH/inst.sh"
    launcher_log "Downloading installer: $installer_url"
    local exit_code=0
    if curl --fail --location --connect-timeout 60 --retry 17 --retry-delay 3 --retry-all-errors --retry-max-time 7777 --retry-connrefused --output "$INSTALLER_PATH" "$installer_url"; then
        exit_code=0
    else
        exit_code=$?
    fi
    if [[ "$exit_code" -eq 0 ]]; then
        launcher_log "Installer downloaded to $INSTALLER_PATH"
        return 0
    fi
    launcher_log "ERROR: installer download failed with exit code $exit_code: $installer_url"
    return "$exit_code"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f resolve_vault_password &>/dev/null; then
resolve_vault_password() {
    # A password equal to the published default leaves the installer without
    # one, so the run warns, waits and falls back to the default vault, exactly
    # as a run started without a password does. Any other value is handed to
    # the installer, which resolves the vault it opens: production when it
    # opens production.vault, default when it matches default.password, and the
    # same warning and wait when it matches neither.
    if [[ "$PYNTARA_VAULT_PASSWORD" == "$PYNTARA_VAULT_PASSWORD_DEFAULT" ]]; then
        unset PYNTARA_VAULT_PASSWORD
        launcher_log "Vault password unchanged, the installer warns and falls back to the default vault"
        return 0
    fi
    launcher_log "Vault password set in the launcher, the installer resolves the vault it opens"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f export_run_parameters &>/dev/null; then
export_run_parameters() {
    # Only the values of this file reach the installer. A name left commented
    # out stays unset, so the installer and the engine resolve their own
    # default instead of reading one from the caller.
    export PYNTARA_REPO_URL PYNTARA_REPO_BRANCH
    export PYNTARA_LOG_DIR PYNTARA_LOG_FILE PYNTARA_JOURNAL_IDENTIFIER
    export PYNTARA_INSTALL_MODE PYNTARA_TASKS PYNTARA_FORCE_TASKS PYNTARA_SKIP_APT_UPDATE
    if [[ -n "${PYNTARA_VAULT_PASSWORD:-}" ]]; then
        export PYNTARA_VAULT_PASSWORD
    fi
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract, Testability).
if ! declare -f run_installer &>/dev/null; then
run_installer() {
    # The installer runs as a child process, so this shell keeps its state and
    # the exit code travels back to the caller.
    local exit_code=0
    launcher_log "Starting the Pyntara installer"
    if bash "$INSTALLER_PATH"; then
        exit_code=0
    else
        exit_code=$?
    fi
    launcher_log "Pyntara installer finished with exit code $exit_code"
    return "$exit_code"
}
fi

# Guard so the test harness can inject a mock main via source (bootstrap contract, Testability).
if ! declare -f main &>/dev/null; then
main() {
    check_root
    prepare_log_dir
    launcher_log "Pyntara launcher started, repository $PYNTARA_REPO_URL branch $PYNTARA_REPO_BRANCH"
    download_installer
    resolve_vault_password
    export_run_parameters
    run_installer "$@"
}
fi

# Run only on direct execution so tests can source this file safely.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
