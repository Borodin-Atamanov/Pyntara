# Project rules

These defaults are mandatory for all Pyntara modules and scripts unless a concrete external constraint requires an exception.

## Command execution output policy

Every command execution must stream output to the terminal in real time by default.  
The same output must be persisted to a log file by default.  
The system journal is the primary destination for own messages; the file log is a residual copy of the full stream.  
Exceptions are allowed only when command output must be suppressed for security or when a third-party tool breaks with streamed mode.

### Agent command reading

When the agent runs a long-running or large-output command from the repository, the output goes to a temporary file first (mktemp or /dev/shm) and is read with tail afterwards; a direct pipe through grep or tail buffers the stream and hides progress and intermediate lines until the command ends. The temporary log path is printed before the command starts, so the log is always reachable while the command runs.

### Task presentation

Before each new task the engine prints an empty line, then the task title.  
After the title there is a pause of TASK_START_DELAY_SECONDS of the engine values module, so the user sees which task starts.  
The task then runs and its output streams in real time, showing what is being done.  
After the task finishes the engine prints a completion line with a brief, informative report that tells how the run went, including the task status, the details from the result and the task execution duration.

### Task progress output

Every task reports its progress to stdout so the user sees what is being done.

Each progress line starts with a task name prefix taken from `__name__`, where the name equals the task name from the catalog (task-model contract) and never diverges from it. A moment in the format the `datetime_format` key of the engine values module names opens a line only when more than one second has passed since the previous line that carried one, so a burst of lines stays compact and a pause is visible at the line that follows it; a logger nobody configured writes no moment. The rule belongs to every own line of a run, not only to a progress line: one renderer, `_emit_line` of `src/pyntara/logger.py`, builds the banner, the result line, the status line, the progress line and the tracking pair of a command, so the moment and the journal copy are the same for every line kind. Prefix and moment are plain text without brackets.
Each action is printed as one line in the form "what is being done: result". If an action is expected to take more than one second, a line announcing it is printed before the action starts. If an action has a non-obvious result, a second line with the result is printed after the action. The command output itself is also shown to the user.  
A calculation is printed as one line: the input values with the parameters substituted, then the result after the equals sign.  
A state check is printed as one line with the check result.  
A decision is printed as a line explaining the chosen branch, including the value the decision is based on.  
Lines are printed to stdout with `flush=True`, so they reach the inst.sh tee log immediately.

Every command that runs through run_command is framed by two tracking lines: `  run : <command>` before the process and `  /run: <exit_code> <seconds>s <command>` after it, so walls of subprocess output stay attributed to the command that produced them. The duration is printed with three decimal places.

### Central logging

The system journal is the primary destination for all own messages: the engine mirrors them under the journal_identifier value of the engine values module, the installer under pyntara-install. The engine values module reaches the journal writer through configure_journal of src/pyntara/logger.py, called by the composition root and by the entry point of every deployed service right after the config is loaded, before the first message; a logger nobody configured forwards nothing, so a component started outside the engine never guesses a name for itself. The file log is residual: it persists the full stream for offline review. All engine messages go through src/pyntara/logger.py: task progress through log_progress, task banners through log_task_start, result lines through log_result_line, status and error lines through log_event. The public helpers only build the text of their line and hand it to _emit_line, the one renderer of an own line, so the console shape and the journal copy cannot drift apart between line kinds. Task modules never print directly and never copy logging code. Every rendered line is mirrored into the journal without the console moment; subprocess output streams from run_command and stays out of the journal, while the run_command tracking lines (run and /run) are mirrored to the journal like other engine messages. A command whose line carries a secret runs with log_command=False and is never printed: logging secret values is forbidden.

Journal message priority is passed to the logging helpers as an optional numeric parameter, a syslog level. The declared values of the engine hold the two levels: progress_priority (7, debug) for every message reporting an action inside a task, and error_priority (3, error) for serious failures. A task reads both from the values module and passes them explicitly to log_progress, so the levels live in one place. The priority is passed as a number, never embedded in the message text and never parsed from it.

## Datetime format policy

Use YYYY-MM-DD-HH-MM-SS as the default datetime format across logs, filenames, task metadata, and generated artifacts.  
Use a different format only when integration requirements make this format incompatible.

## Output and comment style (token economy)

No pseudographics, ASCII art, or decorative separators in comments or output.  
No decorative bullets or box-drawing characters. Use plain text for lists.  
Tables or box-drawn layouts are allowed only on explicit user request.  
Comments must be concise and explain intent, not decorate. Every unnecessary character wastes tokens.

## General engineering requirements

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
External inputs (the environment and the files a task reads) are validated by explicit checks, and those checks belong to the test suite: a task takes every value as it is, without inventing one and without stopping the run. Every rule of a declared value lives in tests/value_checks.py, and tests/test_values.py applies it to every declared value during development.  
Internal structures without external validation use frozen dataclasses.  
All package-install operations and other operations must have timeouts.  
Tasks must also have reasonable large timeouts configured.  
All processes started from Python must provide return code used for correctness control.

All variables and constants live in src/pyntara/values/, one module per task, never as constants inside task modules: this includes paths, file modes, unit file names, journal identifiers, queue and spool directory names. A module constant elsewhere is allowed only as an exception explicitly approved by the user and recorded in docs/contracts/architecture.md; without such a recorded approval the value must live in the values module of its task. A constant found without a recorded approval is an error to fix immediately: move it into that module on discovery, never leave it in place. The same value or the same logic must never be duplicated across modules: shared values and helpers are defined once in a common module and imported. The rule covers the tests too: a test reads the declared values through their module alias instead of restating the literals, so a value change reaches every reader at once.

All text that crosses an external boundary must be passed through the shared trim_whitespace helper (pyntara.utils) before it is stored or reported, whenever trimming cannot damage the content: output captured from console commands, values read from files, and user data must never carry trailing newlines or stray edge whitespace into telemetry reports, logs or persisted values, while internal whitespace is preserved. Do not trim binary payloads: the rule applies to text.

## Documentation and comment style

When creating code and configurations, add comments in simple English.
Comments must explain:
what the code does  
what each configuration line does  
why the action is performed  
why the architecture was chosen

Explanations must be detailed enough for both humans and machines.
One consistent formatting/style standard is required across the project.

Documentation rules:

Heading hierarchy: one H1 title, H2 sections, and H3 subsections where a section grows long. A heading name states the content, so a link to it reads naturally. Numbering in headings is forbidden: a heading is not a list item, numbers burn tokens for nothing.  
Cross-references between documents are active Markdown links relative to the current file, with a heading-name anchor when a specific section is meant; GitHub resolves relative links against the file that contains them, so a path is never written from the repository root. Reference headings by name, never by section number.  
Duplication is forbidden: a fact lives in the document that specifies it (contract, spec or guide); other documents link to it and say what is under the link instead of repeating the text.  
Prose follows the token economy of [Output and comment style](#output-and-comment-style-token-economy): delete sentences that add no information.  
Numbered lists are forbidden in documentation: a list is plain text, one item per line, without markers or numbers. Order lives in the sequence of lines, not in digits; numbering burns tokens and forces renumbering on every edit. In conversation with the user, numbered lists are welcome: the numbers give convenient addresses for follow-up.
