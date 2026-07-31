#!/usr/bin/env bash
set -euo pipefail

# This script is designed to be run as root during first bootstrap.
if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/var/log/pyntara"
LOG_FILE="${LOG_DIR}/install.log"
mkdir -p "${LOG_DIR}"

log() {
  echo "[$(date +%Y-%m-%d-%H-%M-%S)] $*" | tee -a "${LOG_FILE}"
}

run_logged() {
  log "Running: $*"
  "$@" 2>&1 | tee -a "${LOG_FILE}"
  return "${PIPESTATUS[0]}"
}

run_with_retry() {
  local max_attempts="$1"
  shift
  local attempt=1
  local delay=1

  while true; do
    if "$@"; then
      return 0
    fi

    if [[ "${attempt}" -ge "${max_attempts}" ]]; then
      return 1
    fi

    log "Command failed (attempt ${attempt}/${max_attempts}): $*"
    log "Retrying in ${delay}s"
    sleep "${delay}"
    delay=$((delay * 2))
    attempt=$((attempt + 1))
  done
}

require_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log "Required command is missing after installation: ${cmd}"
    exit 1
  fi
}

install_apt_packages() {
  log "Updating apt index"
  run_logged timeout 600 apt-get update -y

  log "Installing minimal runtime packages"
  run_logged timeout 1200 apt-get install -y \
    ca-certificates \
    curl \
    python3 \
    python3-venv
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv is already installed"
    return 0
  fi

  log "Installing uv"
  run_logged timeout 600 bash -c "curl -fsSL https://astral.sh/uv/install.sh | sh"

  if [[ -x "/root/.local/bin/uv" ]]; then
    ln -sf "/root/.local/bin/uv" /usr/local/bin/uv
  fi
}

verify_environment() {
  if [[ ! -f /etc/os-release ]]; then
    log "Cannot detect OS: /etc/os-release is missing"
    exit 1
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"ubuntu"* ]]; then
    log "Warning: this script is optimized for Ubuntu/Kubuntu; detected ${ID:-unknown}"
  fi
}

bootstrap_python_env() {
  cd "${SCRIPT_DIR}"
  if [[ ! -f "pyproject.toml" ]]; then
    log "pyproject.toml is missing in ${SCRIPT_DIR}"
    exit 1
  fi

  log "Synchronizing Python environment with uv"
  if ! run_logged timeout 1200 uv sync --locked; then
    run_logged timeout 1200 uv sync
  fi
}

run_pyntara() {
  cd "${SCRIPT_DIR}"
  log "Starting Pyntara CLI"
  run_logged timeout 7200 uv run pyntara run
}

main() {
  log "Bootstrap started"
  verify_environment
  run_with_retry 3 install_apt_packages
  run_with_retry 3 install_uv

  require_command python3
  require_command uv
  require_command curl

  bootstrap_python_env
  run_pyntara
  log "Bootstrap finished"
}

main "$@"
