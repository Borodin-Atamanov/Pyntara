# Pyntara

Pyntara is a Kubuntu automation system with an idempotent task model and strict typed runtime context.

## Architecture baseline

- Runtime architecture contract: `docs/architecture.md`
- Repository layout contract: `docs/project-structure.md`
- Project-wide defaults: `docs/project-rules.md`
- Main agent specification: `AGENTS.md` (**mandatory for every agent before any edits or commands**)

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

