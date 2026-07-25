# ADR-0001: Source layout and baseline stack

## Context

The project rewrites a shell-based post-installation tool into Python.
We need a package layout, build configuration, and quality toolchain that support long-term maintainability, strict typing, and testability.

## Considered options

1. Flat repository layout without `src` directory.
2. `src` layout with package isolation.
3. Minimal checks only (formatter without strict type checking).
4. Strict quality baseline using static typing and linting.

## Decision

We choose:
- `src` layout with the package under `src/Pyntara`;
- test directories split into unit and integration scopes;
- `pytest` for tests;
- `ruff` for linting and formatting;
- `mypy` in strict mode for static typing validation.

## Consequences

Positive consequences:
- Better import isolation during tests.
- Clear separation of source, tests, and documentation.
- Early detection of type and style defects.

Trade-offs:
- Slightly higher setup cost for contributors.
- Contributors must follow stricter tooling rules.
