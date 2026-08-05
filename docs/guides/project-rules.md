# Project rules

These defaults are mandatory for all Pyntara modules and scripts unless a concrete external constraint requires an exception.

## 1. Command execution output policy

Every command execution must stream output to the terminal in real time by default.
The same output must be persisted to a log file by default.
Exceptions are allowed only when command output must be suppressed for security or when a third-party tool breaks with streamed mode.

### 1.1 Task presentation

Before each new task the engine prints an empty line, then the task title.
After the title there is a 0.5 second pause, so the user sees which task starts.
The task then runs and its output streams in real time, showing what is being done.
After the task finishes the engine prints a completion line with a brief, informative report that tells how the run went, including the task status and the details from the result.

### 1.2 Task progress output

Every task reports its progress to stdout so the user sees what is being done.

1. Each progress line starts with a task name prefix taken from `__name__`, where the name equals the task name from the catalog (task-model contract) and never diverges from it. A timestamp in the project datetime format YYYY-MM-DD-HH-MM-SS is prepended only when more than one second has passed since the previous progress line. Prefix and timestamp are plain text without brackets: `2026-08-05-02-42-37 swapfile_service_install: message`.
2. Each action is printed as one line in the form "what is being done: result". If an action is expected to take more than one second, a line announcing it is printed before the action starts. If an action has a non-obvious result, a second line with the result is printed after the action. The command output itself is also shown to the user.
3. A calculation is printed as one line: the input values with the parameters substituted, then the result after the equals sign.
4. A state check is printed as one line with the check result.
5. A decision is printed as a line explaining the chosen branch, including the value the decision is based on.
6. Lines are printed to stdout with `flush=True`, so they reach the inst.sh tee log immediately.

## 2. Datetime format policy

Use YYYY-MM-DD-HH-MM-SS as the default datetime format across logs, filenames, task metadata, and generated artifacts.
Use a different format only when integration requirements make this format incompatible.

## 3. Output and comment style — token economy

No pseudographics, ASCII art, or decorative separators in comments or output.
No decorative bullets or box-drawing characters. Use plain text for lists.
Tables or box-drawn layouts are allowed only on explicit user request.
Comments must be concise and explain intent, not decorate. Every unnecessary character wastes tokens.

## 4. General engineering requirements

Full type annotations for all arguments and return values are mandatory.
Type checking: mypy --strict, zero errors.
Formatting and static analysis: ruff, zero warnings before merge.
Descriptive naming: functions, methods, variables and task names must state what they do or hold, so the name alone explains the purpose.

Subprocess calls:
no shell=True
mandatory return-code checking

All setup tasks must be idempotent.
Re-runs must not break the system and must not overwrite already generated secrets.
Plaintext secret storage is forbidden (including code and logs).
External inputs (including config) are validated via Pydantic.
Internal structures without validation need use dataclass.
All package-install operations and other operations must have timeouts.
Tasks must also have reasonable large timeouts configured.
All processes started from Python must provide return code used for correctness control.

## 5. Documentation and comment style

When creating code and configurations, add comments in simple English.
Comments must explain:
what the code does
what each configuration line does
why the action is performed
why the architecture was chosen

Explanations must be detailed enough for both humans and machines.
One consistent formatting/style standard is required across the project.
