#!/usr/bin/env bash
# this is DEPRECATED script, doesn't work at all. 
# We don't need to support it anymore!

# install.sh — Interactive Pyntara installer using dialog.
# Usage: cat install.sh | sudo bash
set -euo pipefail

# Section 0: Constants and configuration
ROOT_EUID=0
EXIT_OK=0
EXIT_ERROR=1
AUTO_TIMEOUT=11          # seconds for mode/force auto-select
TASK_TIMEOUT=30          # seconds for task checklist auto-accept
MAX_PASSWORD_ATTEMPTS=3
BACKTITLE="Pyntara Installer"
APT_INSTALL_TIMEOUT_SEC=600

export DEBIAN_FRONTEND=noninteractive

# Detect pipe mode: when stdin is not a TTY (cat install.sh | sudo bash),
# dialog cannot access the controlling terminal because sudo doesn't
# forward it.  We use 'script' (PTY allocator) in pipe mode to work around
# this kernel-level limitation.
PIPE_MODE=false
if [[ ! -t 0 ]]; then
    PIPE_MODE=true
fi

# Section 1: Hardcoded data stubs
INSTALL_MODES=(minimal server desktop)

# Task format: tag|description|default_on
TASKS=(
  "hostname|Generate random 9-character hostname|on"
  "users|Create users i, j, k with sudo access|on"
  "zram|Configure aggressive ZRAM compression|off"
  "ssh|Install and configure SSH server|on"
  "proxy|Setup local proxy server with auth|off"
  "telemetry|Install telemetry service|off"
)

DEFAULT_VAULT_PASSWORD="test123"

# Auto-detect default install mode based on desktop environment.
detect_default_mode() {
  if command -v kwin_x11 &>/dev/null || [[ "${XDG_CURRENT_DESKTOP:-}" == *"KDE"* ]]; then
    echo "desktop"
  else
    echo "server"
  fi
}

# Section 2: TTY check
ensure_tty() {
  if [[ -e /dev/tty && -r /dev/tty ]]; then
    return "${EXIT_OK}"
  fi
  echo "No TTY available. This installer requires an interactive terminal."
  exit "${EXIT_ERROR}"
}

ensure_dialog() {
  if command -v dialog &>/dev/null; then
    return "${EXIT_OK}"
  fi

  echo "dialog is not installed. Trying to install dialog via apt..."
  if apt-get install -y dialog; then
    echo "dialog installed successfully."
    return "${EXIT_OK}"
  fi

  echo "First install attempt failed. Updating apt index and retrying..."
  apt-get update -y
  if apt-get install -y dialog; then
    echo "dialog installed successfully."
    return "${EXIT_OK}"
  fi

  echo "ERROR: Failed to install dialog. Please install it manually: apt-get install dialog"
  exit "${EXIT_ERROR}"
}

# Section 3: Helper functions

# Run dialog with proper terminal access.
# In pipe mode uses 'script' to allocate a PTY because sudo doesn't forward
# the controlling terminal when stdin is a pipe.  In direct mode, runs dialog
# as-is (stdin is already the real terminal).
run_dialog() {
  if [[ "${PIPE_MODE}" == "true" ]]; then
    local cmd="${1}"
    shift
    for arg in "$@"; do
      cmd+=" $(printf '%q' "${arg}")"
    done
    script -q -c "${cmd}" /dev/null
  else
    "$@"
  fi
}

# Run dialog and capture its --stdout result via global CAPTURE_DIALOG_OUTPUT.
# In pipe mode, uses 'script' to provide a PTY and parses the typescript file
# to extract dialog's result (the last non-escape line).
# Usage: capture_dialog dialog <args>; result="${CAPTURE_DIALOG_OUTPUT}"
capture_dialog() {
  local exit_code=0

  if [[ "${PIPE_MODE}" == "true" ]]; then
    local typescript
    typescript=$(mktemp)
    local cmd="${1} --stdout"
    shift
    for arg in "$@"; do
      cmd+=" $(printf '%q' "${arg}")"
    done
    script -q -c "${cmd}" "${typescript}" || exit_code=$?
    CAPTURE_DIALOG_OUTPUT=$(grep -v $'\e' "${typescript}" | grep -v '^$' | tail -1 || true)
    rm -f "${typescript}"
  else
    local tmpfile
    tmpfile=$(mktemp)
    "${1}" --stdout "${@:2}" >"${tmpfile}" || exit_code=$?
    CAPTURE_DIALOG_OUTPUT=$(cat "${tmpfile}")
    rm -f "${tmpfile}"
  fi

  return "${exit_code}"
}

# Confirm cancel and exit if user agrees.
handle_cancel() {
  local screen_name="$1"
  if run_dialog dialog --backtitle "${BACKTITLE}" --title "Cancel" \
    --defaultno --yesno "Are you sure you want to cancel?\n(Current screen: ${screen_name})" 8 50; then
    clear
    echo "Installation cancelled by user."
    exit "${EXIT_ERROR}"
  fi
}

# Section 3: Screen 1 — Welcome
screen_welcome() {
  echo "=== Pyntara Installer ==="
  echo "This tool will configure your Kubuntu system."
  echo "You will be guided through:"
  echo "  - Installation mode selection"
  echo "  - Task selection"
  echo "  - System configuration"
  echo ""
}

# Section 4: Screen 2 — Vault password
screen_vault_password() {
  local attempt=1
  local password=""
  local vault_mode="default"

  while [[ "${attempt}" -le "${MAX_PASSWORD_ATTEMPTS}" ]]; do
    capture_dialog dialog --backtitle "${BACKTITLE}" --title "Vault Password" \
      --insecure --passwordbox "Enter the administrator password\nto decrypt the secrets vault.\n\nAttempt ${attempt}/${MAX_PASSWORD_ATTEMPTS}" 12 50 || {
      handle_cancel "Vault Password"
      return "${EXIT_ERROR}"
    }
    password="${CAPTURE_DIALOG_OUTPUT}"

    if [[ "${password}" == "${DEFAULT_VAULT_PASSWORD}" ]]; then
      vault_mode="production"
      echo "Password accepted. Using production secrets."
      VAULT_MODE="${vault_mode}"
      return "${EXIT_OK}"
    fi

    echo "Incorrect password. Please try again."
    attempt=$((attempt + 1))
  done

  # All attempts exhausted — ask about default secrets.
  if run_dialog dialog --backtitle "${BACKTITLE}" --title "Vault" \
    --defaultno --yesno "Incorrect password after ${MAX_PASSWORD_ATTEMPTS} attempts.\n\nUse default (test) secrets instead?" 8 60; then
    vault_mode="default"
    VAULT_MODE="${vault_mode}"
    echo "Using default test secrets."
    return "${EXIT_OK}"
  fi

  echo "Cannot proceed without secrets. Exiting."
  return "${EXIT_ERROR}"
}

# Section 5: Screen 3 — Install mode selector
screen_mode_selector() {
  local default_mode
  default_mode=$(detect_default_mode)

  local menu_items=()
  for mode in "${INSTALL_MODES[@]}"; do
    menu_items+=("${mode}" "${mode^} installation")
  done

  local result
  if capture_dialog dialog --backtitle "${BACKTITLE}" --title "Install Mode" \
    --default-item "${default_mode}" \
    --timeout "${AUTO_TIMEOUT}" \
    --menu "Select installation mode.\nAuto-selecting ${default_mode} in ${AUTO_TIMEOUT}s" 14 50 3 \
    "${menu_items[@]}"; then
    result="${CAPTURE_DIALOG_OUTPUT}"
  else
    local rc=$?
    if [[ "${rc}" -eq 1 ]]; then
      handle_cancel "Install Mode"
      return "${EXIT_ERROR}"
    fi
    # Timeout (255) — use default
    result="${default_mode}"
  fi

  SELECTED_MODE="${result}"
  echo "Selected mode: ${SELECTED_MODE}"
}

# Section 6: Screen 4 — Task checklist
screen_task_selector() {
  local checklist_items=()
  local task tag desc default_on

  for task in "${TASKS[@]}"; do
    IFS='|' read -r tag desc default_on <<< "${task}"
    checklist_items+=("${tag}" "${desc}" "${default_on}")
  done

  local result
  if capture_dialog dialog --backtitle "${BACKTITLE}" --title "Select Tasks" \
    --timeout "${TASK_TIMEOUT}" \
    --checklist "Choose tasks to execute.\nAuto-accepting defaults in ${TASK_TIMEOUT}s" 18 60 8 \
    "${checklist_items[@]}"; then
    result="${CAPTURE_DIALOG_OUTPUT}"
  else
    local rc=$?
    if [[ "${rc}" -eq 1 ]]; then
      handle_cancel "Task Selection"
      return "${EXIT_ERROR}"
    fi
    # Timeout — use defaults (empty result means all defaults)
    result=""
  fi

  # Parse result: dialog returns space-separated quoted tags
  SELECTED_TASKS=()
  if [[ -n "${result}" ]]; then
    # shellcheck disable=SC2207
    SELECTED_TASKS=( $(echo "${result}") )
  else
    # Timeout or empty — collect all default-on tasks
    local task tag desc default_on
    for task in "${TASKS[@]}"; do
      IFS='|' read -r tag desc default_on <<< "${task}"
      if [[ "${default_on}" == "on" ]]; then
        SELECTED_TASKS+=("${tag}")
      fi
    done
  fi

  echo "Selected ${#SELECTED_TASKS[@]} task(s): ${SELECTED_TASKS[*]}"
}

# Section 7: Screen 5 — Force mode
screen_force_mode() {
  if run_dialog dialog --backtitle "${BACKTITLE}" --title "Force Mode" \
    --defaultno --timeout "${AUTO_TIMEOUT}" \
    --yesno "Run selected tasks in force mode?\n\nForce mode re-runs tasks even if they\nare already completed.\n\nAuto-selecting No in ${AUTO_TIMEOUT}s" 10 60; then
    FORCE_MODE=true
  else
    FORCE_MODE=false
  fi
}

# Section 8: Screen 6 — Force tasks (conditional)
screen_force_tasks() {
  FORCE_TASKS=()

  if [[ "${FORCE_MODE}" != "true" ]]; then
    return "${EXIT_OK}"
  fi

  local checklist_items=()
  local task tag desc default_on

  for task in "${TASKS[@]}"; do
    IFS='|' read -r tag desc default_on <<< "${task}"
    # Only include tasks that were selected in the main checklist
    local found=false
    for st in "${SELECTED_TASKS[@]}"; do
      if [[ "${st}" == "${tag}" ]]; then
        found=true
        break
      fi
    done
    if [[ "${found}" == "true" ]]; then
      checklist_items+=("${tag}" "${desc}" "off")
    fi
  done

  if [[ ${#checklist_items[@]} -eq 0 ]]; then
    return "${EXIT_OK}"
  fi

  local result
  if capture_dialog dialog --backtitle "${BACKTITLE}" --title "Force Tasks" \
    --checklist "Select tasks to force-re-run:" 18 60 8 \
    "${checklist_items[@]}"; then
    result="${CAPTURE_DIALOG_OUTPUT}"
  else
    local rc=$?
    if [[ "${rc}" -eq 1 ]]; then
      handle_cancel "Force Tasks"
      return "${EXIT_ERROR}"
    fi
    # Timeout or other error — empty result
    result=""
  fi

  if [[ -n "${result}" ]]; then
    # shellcheck disable=SC2207
    FORCE_TASKS=( $(echo "${result}") )
  fi
}

# Section 9: Screen 7 — Summary
screen_summary() {
  echo ""
  echo "Installation Summary"
  echo "Vault mode:     ${VAULT_MODE}"
  echo "Install mode:   ${SELECTED_MODE}"
  echo "Tasks selected: ${#SELECTED_TASKS[@]}"
  echo "  ${SELECTED_TASKS[*]}"
  if [[ "${FORCE_MODE}" == "true" ]]; then
    echo "Force mode:     Yes"
    echo "Force tasks:    ${FORCE_TASKS[*]:-(none)}"
  else
    echo "Force mode:     No"
  fi
  echo ""
}

# Section 10: Screen 8 — Progress gauge (stub)
screen_progress() {
  echo "Installing and configuring your system..."
  local total_steps=20
  local step=0
  while [[ "${step}" -le "${total_steps}" ]]; do
    local percent=$(( step * 100 / total_steps ))
    printf "\rProgress: ["
    local filled=$(( percent / 5 ))
    local empty=$(( 20 - filled ))
    for ((i=0; i<filled; i++)); do printf "#"; done
    for ((i=0; i<empty; i++)); do printf "."; done
    printf "] %d%%" "${percent}"
    sleep 0.1
    step=$((step + 1))
  done
  echo ""
  echo "Done."
}

# Section 11: Screen 9 — Complete
screen_complete() {
  echo ""
  echo "=== Installation Complete ==="
  echo "Mode:  ${SELECTED_MODE}"
  echo "Tasks: ${#SELECTED_TASKS[@]}"
  echo ""
}

# Main flow
main() {
  ensure_tty
  ensure_dialog

  screen_welcome
  screen_vault_password || exit "${EXIT_ERROR}"
  screen_mode_selector || exit "${EXIT_ERROR}"
  screen_task_selector || exit "${EXIT_ERROR}"
  screen_force_mode
  screen_force_tasks || exit "${EXIT_ERROR}"
  screen_summary
  screen_progress
  screen_complete

  clear
  echo "Pyntara installation complete."
  exit "${EXIT_OK}"
}

main "$@"