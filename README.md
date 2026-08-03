# Pyntara

Pyntara is a Kubuntu automation system with an idempotent task model and strict typed runtime context.

## Architecture baseline

- Runtime architecture contract: `docs/architecture.md`
- Repository layout contract: `docs/project-structure.md`
- Project-wide defaults: `docs/project-rules.md`
- Interactive terminal UX contract: `docs/interactive-ui-contract.md`
- Bootstrap installer contract: `docs/bootstrap-contract.md`
- Main agent specification: `AGENTS.md` (mandatory for every agent before any edits or commands)

## Run

```bash
curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o insta.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash inst.sh
```

