# Interactive UI — acceptance checklist

Root password prompt is not part of Pyntara UI.
All interactive screens use dialog.
Vault password prompt appears before mode selector.
Password prompt: 11s timeout, 3 attempts, fallback to default.vault.
Any countdown stops on first keypress.
Mode selector: arrows + Enter, 11s auto-select.
Task checkboxes: defaults from install_modes.yaml, 30s auto-accept.
Enabling a task auto-enables dependencies transitively.
Disabling a task does not auto-disable linked tasks.
Force question defaults to No with 11s timeout.
Force-task checkboxes appear only when Yes is chosen.
Force checkboxes are independent.
All prompts are in clear English.
