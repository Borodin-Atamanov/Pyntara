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
