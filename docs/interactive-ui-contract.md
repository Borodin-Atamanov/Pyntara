# Interactive UI contract

This document is the source of truth for interactive terminal UX in Pyntara.

## 1. Scope and boundaries

- System/root authentication is outside Pyntara UI scope. Root password is requested by sudo before Pyntara starts.
- Pyntara interactive flow begins after package installation and environment setup.
- All interactive screens use the `dialog` utility. No custom termios code in the installer.

Bootstrap terminal handoff:

- For pipe-based launch (`curl ... | sudo bash`), `inst.sh` must reconnect stdin to controlling terminal via `/dev/tty` before interactive UI starts.
- Required guard: `if [ -t 0 ] || [ -e /dev/tty ]; then exec < /dev/tty; fi`.
- If `/dev/tty` is unavailable, the installer must log the reason and switch to non-interactive fallback without hanging.

## 2. Screen order (default flow)

1. Production vault decryption password prompt (`dialog --passwordbox`).
2. Install mode selector: `minimal`, `server`, `desktop` (`dialog --menu`).
3. Main task selection checkboxes (`dialog --checklist`).
4. Force-mode question: Yes / No, default No (`dialog --menu` or `dialog --yesno`).
5. Force-task checkboxes, shown only when force-mode is Yes (`dialog --checklist`).

## 3. Prompt language

- All user-facing prompts and helper text must be in clear English.
- Prompts must explain both what to enter and why it is needed.

## 4. Timers and auto-selection

- Every choice screen displays a visible real-time countdown.
- Any countdown stops immediately when the user presses any key. After that, the user can interact without time pressure.
- Install mode selector timeout: 11 seconds.
- Force-mode selector timeout: 11 seconds.
- Main task selection timeout: 30 seconds.
- Password prompt timeout: 11 seconds per attempt.
- If no interaction happens before timeout expires, the default option is accepted automatically.

## 5. Password prompt behavior

- The password prompt offers decryption of `production.vault`.
- Timeout: 11 seconds. If user presses no key within 11 seconds, fallback to `default.vault` immediately.
- If user starts typing, the countdown stops and hidden input mode begins.
- User gets 3 attempts to enter the correct password for `production.vault`.
- After 3 failed attempts, the system falls back to `default.vault`, decrypted with the password from `default.password`.
- Each failed attempt shows an explicit error message via `dialog --msgbox`.
- KeePass decryption is done by a Python library, not shell tools.

## 6. Install mode selector

- `dialog --menu` with three options: `minimal`, `server`, `desktop`.
- Default is auto-detected: `desktop` when a desktop session is present, `server` otherwise.
- Timeout: 11 seconds. Any keypress stops the timer.
- Arrow keys navigate, Enter confirms.

## 7. Main task checkbox semantics

- `dialog --checklist` with task name, short description, and on/off state.
- Default checked tasks depend on the selected mode (`minimal` / `server` / `desktop`) as defined in `install_modes.yaml`.
- Timeout: 30 seconds. Any keypress stops the timer.
- Space toggles a checkbox. Enter confirms and submits the selection.
- Dependency rule on enable: enabling a task auto-enables all its required dependencies transitively.
- Rule on disable: disabling a task does not auto-disable dependent tasks.

## 8. Force-mode behavior

- Binary choice: Yes / No (default No). Timeout: 11 seconds.
- If No, the force-task screen is skipped entirely.
- If Yes, a `dialog --checklist` is shown containing only tasks selected in step 7.
- Force-task checkboxes are independent (no dependency-driven auto-toggle).
- The final force set must be a subset of the selected execution task set.

## 9. Minimal acceptance checklist

1. Root password prompt is not part of Pyntara UI.
2. All interactive screens use `dialog`.
3. Vault password prompt appears before mode selector.
4. Password prompt: 11s timeout, 3 attempts, fallback to `default.vault`.
5. Any countdown stops on first keypress.
6. Mode selector: arrows + Enter, 11s auto-select.
7. Task checkboxes: defaults from `install_modes.yaml`, 30s auto-accept.
8. Enabling a task auto-enables dependencies transitively.
9. Disabling a task does not auto-disable linked tasks.
10. Force question defaults to No with 11s timeout.
11. Force-task checkboxes appear only when Yes is chosen.
12. Force checkboxes are independent.
13. All prompts are in clear English.
14. Bootstrap reconnects stdin to `/dev/tty` before interactive screens.
15. If `/dev/tty` is unavailable, bootstrap logs the reason and does not hang.
