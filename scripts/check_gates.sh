#!/usr/bin/env bash
# Every gate the developer guide requires, in one command, so a landing
# cannot forget one of them (docs/guides/developer-guide.md, CI
# requirements). The GitHub workflow calls this same script, so the local
# run and the pipeline check exactly the same set and no second copy of
# the list can drift from it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
    printf "uv is not installed, install it first (docs/guides/developer-guide.md, quick start)\n" >&2
    exit 1
fi

run_gate() {
    local gate_name="$1"
    shift
    printf "gate %s\n" "$gate_name"
    "$@"
}

run_gate ruff uv run ruff check .
run_gate "mypy strict src" uv run mypy --strict src/
run_gate "mypy tests" uv run mypy
run_gate pytest uv run pytest
run_gate "bootstrap installer" bash tests/test_inst.sh
run_gate "pre-commit hook" bash tests/test_pre_commit_hook.sh
run_gate "landing step" bash tests/test_land_version_commit.sh
run_gate "system metrics commit script" bash tests/test_commit_script.sh

printf "every gate passed\n"
