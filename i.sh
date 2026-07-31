#!/usr/bin/env bash
# Fail fast on script errors, undefined variables, and pipeline errors.
set -euo pipefail

# Centralized runtime configuration.
# Keeping all numbers and paths here makes tuning safer and avoids magic constants in logic.
ROOT_EUID="${PYNTARA_ROOT_EUID:-0}"
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
GIT_CLONE_TIMEOUT_SEC=1200
GIT_FETCH_TIMEOUT_SEC=600
GIT_EXPORT_TIMEOUT_SEC=300
APT_UPDATE_TTL_SEC=3600
TIMESTAMP_FORMAT="+%Y-%m-%d-%H-%M-%S"
STATE_DIR="${PYNTARA_STATE_DIR:-/var/lib/pyntara}"
APT_UPDATE_STAMP="${STATE_DIR}/apt-update.stamp"
UV_ROOT_BIN="/root/.local/bin/uv"
UV_GLOBAL_BIN="/usr/local/bin/uv"
WORK_BASE_DIR="${PYNTARA_WORK_BASE_DIR:-/var/lib/pyntara/workspaces}"
REPO_CACHE_DIR="${PYNTARA_REPO_CACHE_DIR:-/var/cache/pyntara/repos/Pyntara.git}"
PYNTARA_SOURCE_REPO="${PYNTARA_SOURCE_REPO:-Borodin-Atamanov/Pyntara}"
PYNTARA_SOURCE_REF="${PYNTARA_SOURCE_REF:-main}"
SOURCE_REMOTE_URL="https://github.com/${PYNTARA_SOURCE_REPO}.git"
SCRIPT_DIR=""
BOOTSTRAP_SOURCE_DIR=""

# Bootstrap must run with root privileges because it installs packages and writes system paths.
if [[ "${EUID}" -ne "${ROOT_EUID}" ]]; then
  echo "Please run as root."
  exit "${EXIT_ERROR}"
fi

# Ensure apt never prompts for interactive input during unattended install.
export DEBIAN_FRONTEND=noninteractive

# Resolve local source location if script was launched from a file.
if [[ -n "${BASH_SOURCE[${BASH_SOURCE_INDEX}]-}" ]]; then
  BOOTSTRAP_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[${BASH_SOURCE_INDEX}]}")" && pwd)"
else
  BOOTSTRAP_SOURCE_DIR="$(pwd)"
fi

# Standard Linux paths for persistent logs and local runtime state.
LOG_DIR="${PYNTARA_LOG_DIR:-/var/log/pyntara}"
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

# Check whether the bootstrap source directory contains a runnable Pyntara project.
has_local_project_source() {
  [[ -f "${BOOTSTRAP_SOURCE_DIR}/pyproject.toml" ]]
}

# Synchronize persistent git cache with depth=1 to download only deltas on each run.
sync_cached_repository() {
  mkdir -p "$(dirname "${REPO_CACHE_DIR}")"

  if [[ -d "${REPO_CACHE_DIR}" ]]; then
    if ! run_logged timeout "${GIT_FETCH_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" fetch --depth 1 --prune origin "${PYNTARA_SOURCE_REF}"; then
      return "${EXIT_ERROR}"
    fi
    return "${EXIT_OK}"
  fi

  if ! run_logged timeout "${GIT_CLONE_TIMEOUT_SEC}" git clone --bare --depth 1 --branch "${PYNTARA_SOURCE_REF}" "${SOURCE_REMOTE_URL}" "${REPO_CACHE_DIR}"; then
    return "${EXIT_ERROR}"
  fi
}

# Export requested revision from local git cache into a private workspace directory.
populate_workspace_from_cache() {
  local workspace_dir="$1"
  local source_tar="${workspace_dir}/source.tar"
  local archive_ref=""

  # Bare repositories may not have origin/<branch> as a valid revision.
  # Prefer origin/<branch>, then local <branch>, then FETCH_HEAD from recent fetch.
  if timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" rev-parse --verify --quiet "origin/${PYNTARA_SOURCE_REF}" &>/dev/null; then
    archive_ref="origin/${PYNTARA_SOURCE_REF}"
  elif timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" rev-parse --verify --quiet "${PYNTARA_SOURCE_REF}" &>/dev/null; then
    archive_ref="${PYNTARA_SOURCE_REF}"
  elif timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" rev-parse --verify --quiet "FETCH_HEAD" &>/dev/null; then
    archive_ref="FETCH_HEAD"
  else
    log "Cannot resolve git ref for archive: origin/${PYNTARA_SOURCE_REF}, ${PYNTARA_SOURCE_REF}, FETCH_HEAD"
    return "${EXIT_ERROR}"
  fi

  if ! run_logged timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" archive --format=tar --output "${source_tar}" "${archive_ref}"; then
    return "${EXIT_ERROR}"
  fi
  if ! run_logged timeout "${GIT_EXPORT_TIMEOUT_SEC}" tar -xf "${source_tar}" -C "${workspace_dir}"; then
    return "${EXIT_ERROR}"
  fi
  rm -f "${source_tar}"
}

# Copy local unpacked source into workspace (offline fallback for flash-drive runs).
populate_workspace_from_local_source() {
  local workspace_dir="$1"
  if ! run_logged timeout "${GIT_EXPORT_TIMEOUT_SEC}" cp -a "${BOOTSTRAP_SOURCE_DIR}/." "${workspace_dir}/"; then
    return "${EXIT_ERROR}"
  fi
}

# Create a dedicated private workspace and fill it from cached git or local source.
prepare_workspace() {
  local workspace_dir

  umask 077
  mkdir -p "${WORK_BASE_DIR}"
  workspace_dir="$(mktemp -d "${WORK_BASE_DIR}/run.XXXXXXXX")"

  log "Preparing workspace at ${workspace_dir}"
  if command -v git &>/dev/null && sync_cached_repository; then
    if ! populate_workspace_from_cache "${workspace_dir}"; then
      return "${EXIT_ERROR}"
    fi
    SCRIPT_DIR="${workspace_dir}"
    log "Using workspace source from git cache ${REPO_CACHE_DIR}"
    return "${EXIT_OK}"
  fi

  if has_local_project_source; then
    if ! populate_workspace_from_local_source "${workspace_dir}"; then
      return "${EXIT_ERROR}"
    fi
    SCRIPT_DIR="${workspace_dir}"
    log "Using workspace source from local directory ${BOOTSTRAP_SOURCE_DIR}"
    return "${EXIT_OK}"
  fi

  log "Cannot prepare source workspace: git sync failed and no local project source found in ${BOOTSTRAP_SOURCE_DIR}"
  return "${EXIT_ERROR}"
}

# Install only minimal packages required for bootstrap and initial CLI execution.
install_apt_packages() {
  # Skip apt index refresh when a recent successful update exists.
  if should_run_apt_update; then
    log "Updating apt index"
    if ! run_logged timeout "${APT_UPDATE_TIMEOUT_SEC}" apt-get update -y; then
      return "${EXIT_ERROR}"
    fi
    # Stamp is written only after successful update.
    update_apt_stamp
  else
    log "Skipping apt index update: last successful update is fresh."
  fi

  log "Installing minimal runtime packages"
  if ! run_logged timeout "${APT_INSTALL_TIMEOUT_SEC}" apt-get install -y \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv; then
    return "${EXIT_ERROR}"
  fi
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
  if ! run_logged timeout "${UV_INSTALL_TIMEOUT_SEC}" bash -o pipefail -c "curl -fsSL https://astral.sh/uv/install.sh | sh"; then
    return "${EXIT_ERROR}"
  fi

  if [[ -x "${UV_ROOT_BIN}" ]]; then
    # Expose uv in a global PATH location for root/system scripts.
    ln -sf "${UV_ROOT_BIN}" "${UV_GLOBAL_BIN}"
  fi

  if ! command -v uv &>/dev/null; then
    log "uv installation did not expose an executable in PATH"
    return "${EXIT_ERROR}"
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
  if command -v python3 &>/dev/null && command -v uv &>/dev/null; then
    log "Skipping apt bootstrap: python3 and uv already available."
  else
    run_with_retry "${RETRY_MAX_ATTEMPTS}" install_apt_packages
  fi
  run_with_retry "${RETRY_MAX_ATTEMPTS}" install_uv

  require_command python3
  require_command uv
  require_command tar

  run_with_retry "${RETRY_MAX_ATTEMPTS}" prepare_workspace
  bootstrap_python_env
  run_pyntara
  log "Bootstrap finished"
}

main "$@"
