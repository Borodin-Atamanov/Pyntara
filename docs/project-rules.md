# Project rules

These defaults are mandatory for all Pyntara modules and scripts unless a concrete external constraint requires an exception.

## 1. Command execution output policy

- Every command execution must stream output to the terminal in real time by default.
- The same output must be persisted to a log file by default.
- Exceptions are allowed only when command output must be suppressed for security or when a third-party tool breaks with streamed mode.

## 2. Datetime format policy

- Use `YYYY-MM-DD-HH-MM-SS` as the default datetime format across logs, filenames, task metadata, and generated artifacts.
- Use a different format only when integration requirements make this format incompatible.

