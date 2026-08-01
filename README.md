# Pyntara

Pyntara is a Kubuntu automation system with an idempotent task model and strict typed runtime context.

## Architecture baseline

- Runtime architecture contract: `docs/architecture.md`
- Repository layout contract: `docs/project-structure.md`
- Project-wide defaults: `docs/project-rules.md`
- Main agent specification: `AGENTS.md` (mandatory for every agent before any edits or commands)

## Run

```bash
curl -fsSL https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/i.sh | sudo bash
```

## Development checks

```bash
make test
make check
```

If `uv` is not installed, `make` will create `.venv` and install dev dependencies automatically.

For bootstrap-specific verification, use these lanes:

```bash
make test-quick
make test-bootstrap-contract
make test-bootstrap-deep
make test-bootstrap-rootlike
```

- `make test-quick` is the fast default lane for day-to-day work.
- `make test-bootstrap-contract` runs the script-level bootstrap contract tests with mocked `apt`, `git`, and `uv`.
- `make test-bootstrap-deep` runs the PTY-based `curl | bash` style scenarios.
- `make test-bootstrap-rootlike` is an optional containerized root-like run when `podman` is available.

