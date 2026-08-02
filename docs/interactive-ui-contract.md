# Interactive UI Contract

This document is the source of truth for interactive terminal UX in Pyntara bootstrap and CLI flows.

## 1. Scope and boundaries

- System/root authentication is outside Pyntara UI scope.
- Root password is requested by sudo or OS facilities before Pyntara flow starts.
- Pyntara interactive flow begins with secrets and installation choices.

Bootstrap terminal handoff requirement:

- For pipe-based launch (`curl ... | sudo bash`), `i.sh` must reconnect stdin to controlling terminal via `/dev/tty` before interactive UI starts.
- Required guard: `if [ -t 0 ] || [ -e /dev/tty ]; then exec < /dev/tty; fi`.
- If `/dev/tty` is unavailable, bootstrap must not wait for interactive input and must switch to non-interactive fallback with a clear reason in logs.

## 2. Screen order (default flow)

1. Production vault decryption password prompt.
2. Install mode selector (`minimal`, `server`, `desktop`).
3. Main task selection checkboxes.
4. Force-mode question (`Yes` or `No`, default `No`).
5. Optional force-task checkboxes (shown only when force-mode is `Yes`).

## 3. Prompt language

- All user-facing prompts and helper text must be in clear English.
- Prompts must explain both what to enter and why it is needed.

## 4. Timers and auto-selection

- All choice screens display a visible real-time countdown.
- Install mode selector timeout: 11 seconds.
- Force-mode selector timeout: 11 seconds.
- Main task selection pre-interaction timeout: 30 seconds.
- If no interaction happens in main task selection during those 30 seconds, default task selection is accepted.
- In main task selection, the 30-second timer stops immediately after the first user interaction (navigation or toggle).

## 5. Password retry behavior

- Password retry behavior for vault decryption remains as currently implemented.
- Any future change must preserve explicit user feedback for failed attempts.

## 6. Main task checkbox semantics

- Rows should stay concise: task name, short description, checkbox state.
- Enter confirms and submits current selection.
- Dependency rule on enable: enabling a task auto-enables all required dependencies transitively.
- Rule on disable: disabling a task does not auto-disable related tasks.

## 7. Force-mode behavior

- Force question is a binary selector with left/right navigation.
- Default value is `No`.
- If `No`, no force-task screen is shown.
- If `Yes`, show a force-task checkbox list containing only tasks selected in the main task list.
- Force-task checkboxes are independent from each other (no dependency-driven auto-toggle in force selection).
- The final force set must be a subset of the selected execution task set.

## 8. Minimal acceptance checklist

1. Root password prompt is not implemented inside Pyntara UI.
2. Vault prompt appears before mode selector.
3. Mode selector supports arrows + Enter and 11-second auto-select.
4. Main task checkboxes appear before force question.
5. Main task screen auto-accepts defaults after 30 seconds of no interaction.
6. Main task timer stops on first interaction.
7. Enabling task auto-enables dependencies transitively.
8. Disabling task does not auto-disable linked tasks.
9. Force question defaults to `No` with 11-second timeout.
10. Force checkbox list appears only when `Yes` is chosen.
11. Force checkboxes are independent.
12. All prompts are clear English.
13. Bootstrap reconnects stdin to controlling tty via `/dev/tty` before interactive screens.
14. If `/dev/tty` is unavailable, bootstrap explicitly logs non-interactive fallback reason and does not hang waiting for input.

## 9. Future note

- If support is later required for environments where `/dev/tty` is universally unavailable, a PTY wrapper can be considered as a separate fallback design.
