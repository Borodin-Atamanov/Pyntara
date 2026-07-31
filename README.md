# Pyntara

Pyntara is a Kubuntu automation system with an idempotent task model and strict typed runtime context.

## Project-wide defaults

- By default, every command must stream output to the screen and be written to a log file.
- Default datetime format is `YYYY-MM-DD-HH-MM-SS` (use exceptions only when a tool or protocol requires another format).
- Canonical implementation rules are described in `docs/project-rules.md`.
