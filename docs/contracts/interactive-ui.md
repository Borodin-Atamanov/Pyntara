# Interactive UI contract

This document is the source of truth for interactive terminal UX in Pyntara.

## 1. Scope and boundaries

System/root authentication is outside Pyntara UI scope. Root password is requested
by sudo before Pyntara starts. Pyntara interactive flow begins after package
installation and environment setup. Choice screens are plain bash text screens:
interactive widgets from dialog are unusable where stdout is not a terminal
(e.g. under sudo), so every screen uses bash read and visible real-time
countdowns instead. The vault password prompt uses bash read -s (hidden input).
No custom termios code in the installer.

## 2. Global rules

### 2.1. Timers

Every choice screen displays a visible real-time countdown.

Timeout values:
  Password prompt — 333 s (VAULT_PASSWORD_TIMEOUT)
  Install mode selector — 11 s
  Force-mode selector — 11 s
  Main task selection — 30 s

Any countdown stops immediately on first keypress. After that, the user can
interact without time pressure. If no interaction happens before timeout, the
default option is accepted automatically.

Informational and error messages are printed as plain terminal text (not
dialog widgets) and held for 11 s (MESSAGE_TIMEOUT) or until the user presses
Enter.

### 2.2. Prompt language

All user-facing prompts and helper text must be in clear simple English.
Prompts must explain both what to enter and why it is needed.

## 3. Screen flow overview

1. Vault decryption password prompt (bash read -s, hidden input, no dialog)
2. Install mode selector: minimal / server / desktop (dialog --menu)
3. Main task selection (dialog --checklist)
4. Force-mode question: Yes / No, default No (dialog --yesno)
5. Force-task checkboxes — shown only when force-mode is Yes (dialog --checklist)

## 4. Password prompt

Offers decryption of production.vault. User gets 3 attempts.

On each attempt:
  The password is read with bash read -s (hidden input, plain bash, no termios),
  with a total timeout of 333 s (VAULT_PASSWORD_TIMEOUT). Pressing Enter submits the password; if no key
  is pressed before the timeout, the attempt times out.
  A failed attempt prints a plain-text error message held for MESSAGE_TIMEOUT
  seconds (default 11 s) or until the user presses Enter.

If secrets/production.vault does not exist, the installer prints a loud error
message and falls back to default.vault immediately, without asking for a
password.

After 3 failed attempts — fallback to default.vault, decrypted with the password
from default.password.

KeePass decryption is done by a Python library, not shell tools.

## 5. Install mode selector

Plain text screen with three options: minimal, server, desktop.
Options are numbered; the user answers with a number or the first letter
of the mode name. Empty input, EOF or a timeout accepts the default.

Default: auto-detected. Desktop when a desktop session is present, server otherwise.
Arrow keys are not needed; digits and letters are accepted.

## 6. Main task selection

dialog --checklist with task name, short description, and on/off state.

Default checked tasks depend on the selected mode as defined in tasks.yaml.
Space toggles a checkbox, Enter confirms and submits the selection.

Dependency rule on enable: enabling a task auto-enables all its required
dependencies transitively.
Rule on disable: disabling a task does not auto-disable dependent tasks.

## 7. Force-mode

Binary choice: Yes / No (default No).

If No — force-task screen is skipped entirely.
If Yes — a dialog --checklist is shown, containing only tasks selected in step 6.

Force-task checkboxes are independent (no dependency-driven auto-toggle).
The final force set must be a subset of the selected execution task set.

