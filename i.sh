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
UV_ROOT_BIN="${PYNTARA_UV_ROOT_BIN:-/root/.local/bin/uv}"
UV_GLOBAL_BIN="${PYNTARA_UV_GLOBAL_BIN:-/usr/local/bin/uv}"
WORK_BASE_DIR="${PYNTARA_WORK_BASE_DIR:-/var/lib/pyntara/workspaces}"
REPO_CACHE_DIR="${PYNTARA_REPO_CACHE_DIR:-/var/cache/pyntara/repos/Pyntara.git}"
UV_CACHE_DIR="${PYNTARA_UV_CACHE_DIR:-/var/cache/pyntara/uv}"
PYNTARA_SOURCE_REPO="${PYNTARA_SOURCE_REPO:-Borodin-Atamanov/Pyntara}"
PYNTARA_SOURCE_REF="${PYNTARA_SOURCE_REF:-main}"
UV_TARGET_USER="${PYNTARA_UV_USER:-${SUDO_USER:-i}}"
UV_TARGET_HOME="${PYNTARA_UV_USER_HOME:-}"
PYNTARA_CLI_STDIN_PATH="${PYNTARA_CLI_STDIN_PATH:-/dev/tty}"
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
mkdir -p "${UV_CACHE_DIR}"
export UV_CACHE_DIR

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
    if ! run_logged timeout "${GIT_FETCH_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" fetch --depth 1 --prune origin "+refs/heads/${PYNTARA_SOURCE_REF}:refs/heads/${PYNTARA_SOURCE_REF}"; then
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
  local candidate_ref=""

  # Bare repositories may not have origin/<branch> as a valid revision.
  # Prefer the just-fetched FETCH_HEAD to avoid stale local refs in long-lived bare caches.
  # Then fallback to local/remote refs for clone-first or offline-style scenarios.
  # Some caches keep a ref that resolves but points to an object that archive cannot read.
  for candidate_ref in "FETCH_HEAD" "origin/${PYNTARA_SOURCE_REF}" "${PYNTARA_SOURCE_REF}"; do
    if ! timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" rev-parse --verify --quiet "${candidate_ref}" &>/dev/null; then
      continue
    fi
    if run_logged timeout "${GIT_EXPORT_TIMEOUT_SEC}" git --git-dir "${REPO_CACHE_DIR}" archive --format=tar --output "${source_tar}" "${candidate_ref}"; then
      archive_ref="${candidate_ref}"
      break
    fi
  done

  if [[ -z "${archive_ref}" ]]; then
    log "Cannot export workspace tar from refs: FETCH_HEAD, origin/${PYNTARA_SOURCE_REF}, ${PYNTARA_SOURCE_REF}"
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
  if [[ -x "${UV_ROOT_BIN}" ]]; then
    # Keep uv accessible for non-root interactive shells as a stable global entrypoint.
    ln -sf "${UV_ROOT_BIN}" "${UV_GLOBAL_BIN}"
  fi

  if command -v uv &>/dev/null; then
    log "uv is already installed"
    return "${EXIT_OK}"
  fi

  log "Installing uv"
  if ! run_logged timeout "${UV_INSTALL_TIMEOUT_SEC}" bash -o pipefail -c "curl -fsSL https://astral.sh/uv/install.sh | sh"; then
    return "${EXIT_ERROR}"
  fi

  if [[ -x "${UV_ROOT_BIN}" ]]; then
    # Expose uv in a global PATH location for root/system scripts and user shells.
    ln -sf "${UV_ROOT_BIN}" "${UV_GLOBAL_BIN}"
  fi

  if ! command -v uv &>/dev/null; then
    log "uv installation did not expose an executable in PATH"
    return "${EXIT_ERROR}"
  fi
}

# Ensure uv is also reachable from a regular user account, not only from root PATH.
expose_uv_for_regular_user() {
  local target_user="${UV_TARGET_USER}"
  local target_home="${UV_TARGET_HOME}"
  local uv_bin

  if [[ -z "${target_user}" ]]; then
    log "Skipping user-level uv link: target user is empty."
    return "${EXIT_OK}"
  fi

  if ! id "${target_user}" &>/dev/null; then
    log "Skipping user-level uv link: user ${target_user} does not exist yet."
    return "${EXIT_OK}"
  fi

  if [[ -z "${target_home}" ]]; then
    target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  fi
  if [[ -z "${target_home}" ]]; then
    log "Skipping user-level uv link: cannot resolve home for ${target_user}."
    return "${EXIT_OK}"
  fi

  uv_bin="$(command -v uv || true)"
  if [[ -z "${uv_bin}" ]]; then
    log "Skipping user-level uv link: uv is not available in PATH."
    return "${EXIT_OK}"
  fi

  mkdir -p "${target_home}/.local/bin"
  ln -sf "${uv_bin}" "${target_home}/.local/bin/uv"
  chown -h "${target_user}:${target_user}" "${target_home}/.local/bin/uv"
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
  # Prefer locked sync only when lockfile is current. This avoids expected error noise.
  # Fallback keeps first-run bootstrap working when lockfile is missing/outdated.
  if timeout "${UV_SYNC_TIMEOUT_SEC}" uv lock --check &>/dev/null; then
    run_logged timeout "${UV_SYNC_TIMEOUT_SEC}" uv sync --locked
  else
    log "uv.lock is missing or outdated; running uv sync without --locked"
    run_logged timeout "${UV_SYNC_TIMEOUT_SEC}" uv sync
  fi
}

# Launch the Pyntara CLI with a large timeout suitable for provisioning workflows.
run_pyntara() {
  cd "${SCRIPT_DIR}"
  log "Starting Pyntara CLI"
  if exec 3<"${PYNTARA_CLI_STDIN_PATH}"; then
    log "Using CLI stdin source: ${PYNTARA_CLI_STDIN_PATH}"
    if [[ "${PYNTARA_CLI_STDIN_PATH}" == "/dev/tty" ]]; then
      log "Running: uv run pyntara"
      set +e
      uv run pyntara <&3
      local run_status="$?"
      set -e
      exec 3<&-
      return "${run_status}"
    fi

    log "Running: timeout ${PYNTARA_RUN_TIMEOUT_SEC} uv run pyntara"
    set +e
    timeout "${PYNTARA_RUN_TIMEOUT_SEC}" uv run pyntara <&3
    local run_status="$?"
    set -e
    exec 3<&-
    return "${run_status}"
  fi
  log "CLI stdin source is unavailable: ${PYNTARA_CLI_STDIN_PATH}"
  run_logged timeout "${PYNTARA_RUN_TIMEOUT_SEC}" uv run pyntara
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
  run_with_retry "${RETRY_MAX_ATTEMPTS}" expose_uv_for_regular_user

  require_command python3
  require_command uv
  require_command tar

  run_with_retry "${RETRY_MAX_ATTEMPTS}" prepare_workspace
  bootstrap_python_env
  run_pyntara
  log "Bootstrap finished"
}

main "$@"
