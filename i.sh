#!/usr/bin/env bash
# Fail fast on script errors, undefined variables, and pipeline errors.
set -euo pipefail

# Centralized runtime configuration.
# Keeping all numbers and paths here makes tuning safer and avoids magic constants in logic.
ROOT_EUID=0
EXIT_OK=0
EXIT_ERROR=1
PRIMARY_PIPESTATUS_INDEX=0
BASH_SOURCE_INDEX=0
RETRY_MAX_ATTEMPTS=3
RETRY_INITIAL_DELAY_SEC=1
RETRY_MULTIPLIER=2
APT_UPDATE_TIMEOUT_SEC=600
APT_INSTALL_TIMEOUT_SEC=1200
UV_INSTALL_TIMEOUT_SEC=600
UV_SYNC_TIMEOUT_SEC=1200
PYNTARA_RUN_TIMEOUT_SEC=7200
APT_UPDATE_TTL_SEC=3600
TIMESTAMP_FORMAT="+%Y-%m-%d-%H-%M-%S"
STATE_DIR="/var/lib/pyntara"
APT_UPDATE_STAMP="${STATE_DIR}/apt-update.stamp"
UV_ROOT_BIN="/root/.local/bin/uv"
UV_GLOBAL_BIN="/usr/local/bin/uv"

# Bootstrap must run with root privileges because it installs packages and writes system paths.
if [[ "${EUID}" -ne "${ROOT_EUID}" ]]; then
  echo "Please run as root."
  exit "${EXIT_ERROR}"
fi

# Ensure apt never prompts for interactive input during unattended install.
export DEBIAN_FRONTEND=noninteractive

# Resolve repository path to run uv commands from the project root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[${BASH_SOURCE_INDEX}]}")" && pwd)"
# Standard Linux paths for persistent logs and local runtime state.
LOG_DIR="/var/log/pyntara"
LOG_FILE="${LOG_DIR}/install.log"
mkdir -p "${STATE_DIR}"
mkdir -p "${LOG_DIR}"

# Unified logger: same message goes to terminal and install log.
log() {
  echo "[$(date "${TIMESTAMP_FORMAT}")] $*" | tee -a "${LOG_FILE}"
}

# Run a command with live output mirrored to log; return original command exit code.
run_logged() {
  log "Running: $*"
  "$@" |& tee -a "${LOG_FILE}"
  return "${PIPESTATUS[${PRIMARY_PIPESTATUS_INDEX}]}"
}

# Generic retry wrapper for transient failures (network/package mirror instability).
run_with_retry() {
  local max_attempts="$1"
  shift
  local attempt="${RETRY_INITIAL_DELAY_SEC}"
  local delay="${RETRY_INITIAL_DELAY_SEC}"

  while true; do
    if "$@"; then
      return "${EXIT_OK}"
    fi

    if [[ "${attempt}" -ge "${max_attempts}" ]]; then
      return "${EXIT_ERROR}"
    fi

    log "Command failed (attempt ${attempt}/${max_attempts}): $*"
    log "Retrying in ${delay}s"
    sleep "${delay}"
    delay=$((delay * RETRY_MULTIPLIER))
    attempt=$((attempt + RETRY_INITIAL_DELAY_SEC))
  done
}

# Hard requirement check used after installation steps.
require_command() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    log "Required command is missing after installation: ${cmd}"
    exit "${EXIT_ERROR}"
  fi
}

# Install only minimal packages required for bootstrap and initial CLI execution.
install_apt_packages() {
  # Skip apt index refresh when a recent successful update exists.
  if should_run_apt_update; then
    log "Updating apt index"
    run_logged timeout "${APT_UPDATE_TIMEOUT_SEC}" apt-get update -y
    # Stamp is written only after successful update.
    update_apt_stamp
  else
    log "Skipping apt index update: last successful update is fresh."
  fi

  log "Installing minimal runtime packages"
  run_logged timeout "${APT_INSTALL_TIMEOUT_SEC}" apt-get install -y \
    ca-certificates \
    curl \
    python3 \
    python3-venv
}

# Decide whether apt index refresh is needed based on TTL.
should_run_apt_update() {
  if [[ ! -f "${APT_UPDATE_STAMP}" ]]; then
    return "${EXIT_OK}"
  fi

  local now_epoch
  local stamp_epoch
  now_epoch="$(date +%s)"
  stamp_epoch="$(stat -c %Y "${APT_UPDATE_STAMP}")"

  if (( now_epoch - stamp_epoch >= APT_UPDATE_TTL_SEC )); then
    return "${EXIT_OK}"
  fi
  return "${EXIT_ERROR}"
}

# Write a temporary stamp file then atomically move it into place.
# This avoids partial/corrupted state if the script is interrupted mid-write.
update_apt_stamp() {
  local temp_stamp
  temp_stamp="$(mktemp)"
  date "${TIMESTAMP_FORMAT}" > "${temp_stamp}"
  mv -f "${temp_stamp}" "${APT_UPDATE_STAMP}"
}

# Install uv only when missing to keep reruns fast and idempotent.
install_uv() {
  if command -v uv &>/dev/null; then
    log "uv is already installed"
    return "${EXIT_OK}"
  fi

  log "Installing uv"
  run_logged timeout "${UV_INSTALL_TIMEOUT_SEC}" bash -c "curl -fsSL https://astral.sh/uv/install.sh | sh"

  if [[ -x "${UV_ROOT_BIN}" ]]; then
    # Expose uv in a global PATH location for root/system scripts.
    ln -sf "${UV_ROOT_BIN}" "${UV_GLOBAL_BIN}"
  fi
}

# Basic platform sanity check. Script is optimized for Ubuntu/Kubuntu behavior.
verify_environment() {
  if [[ ! -f /etc/os-release ]]; then
    log "Cannot detect OS: /etc/os-release is missing"
    exit "${EXIT_ERROR}"
  fi

  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"ubuntu"* ]]; then
    log "Warning: this script is optimized for Ubuntu/Kubuntu; detected ${ID:-unknown}"
  fi
}

# Prepare Python environment from project metadata.
bootstrap_python_env() {
  cd "${SCRIPT_DIR}"
  if [[ ! -f "pyproject.toml" ]]; then
    log "pyproject.toml is missing in ${SCRIPT_DIR}"
    exit "${EXIT_ERROR}"
  fi

  log "Synchronizing Python environment with uv"
  # Prefer locked dependencies for reproducibility, then fallback for first-time lock creation.
  if ! run_logged timeout "${UV_SYNC_TIMEOUT_SEC}" uv sync --locked; then
    run_logged timeout "${UV_SYNC_TIMEOUT_SEC}" uv sync
  fi
}

# Launch the Pyntara CLI with a large timeout suitable for provisioning workflows.
run_pyntara() {
  cd "${SCRIPT_DIR}"
  log "Starting Pyntara CLI"
  run_logged timeout "${PYNTARA_RUN_TIMEOUT_SEC}" uv run pyntara run
}

# Main bootstrap flow:
# 1) validate environment
# 2) install minimal runtime dependencies
# 3) ensure required commands are present
# 4) sync Python env and run Pyntara
main() {
  log "Bootstrap started"
  verify_environment
  run_with_retry "${RETRY_MAX_ATTEMPTS}" install_apt_packages
  run_with_retry "${RETRY_MAX_ATTEMPTS}" install_uv

  require_command python3
  require_command uv
  require_command curl

  bootstrap_python_env
  run_pyntara
  log "Bootstrap finished"
}

main "$@"
