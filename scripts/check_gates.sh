#!/usr/bin/env bash
# Every gate the developer guide requires, in one command, so a landing
# cannot forget one of them (docs/guides/developer-guide.md, CI
# requirements). The GitHub workflow calls this script with no argument, so
# the pipeline and the full local run check exactly the same set and no
# second copy of the list can drift from it.
#
# The --fast argument is the command of the development loop: it checks the
# touched files and the test modules that match them by name, which takes
# seconds instead of a minute. It is a loop tool and not a gate, because it
# checks a touched module with what it imports but never the modules that
# import it, and because no local run can see a difference of the runner
# environment.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ $# -gt 1 ]] || [[ "${1:-}" != "" && "${1:-}" != "--fast" ]]; then
    printf "usage: %s [--fast]\n" "${0##*/}" >&2
    exit 2
fi
fast_mode=0
if [[ "${1:-}" == "--fast" ]]; then
    fast_mode=1
fi

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

# The python files this working copy touches: the commits of the branch that
# are not in main yet plus the uncommitted work. A deletion is left out,
# because a file that is gone has nothing to check.
touched_python_files() {
    local path
    {
        git diff --name-only --diff-filter=ACMR origin/main...HEAD 2>/dev/null || true
        git status --porcelain --untracked-files=all 2>/dev/null | awk '{ print $NF }' || true
    } | sort -u | while IFS= read -r path; do
        case "$path" in
            src/*.py|tests/*.py|task_data/*.py) if [[ -f "$path" ]]; then printf '%s\n' "$path"; fi ;;
        esac
    done
}

# The test modules that match a touched file by name: a touched test module
# is itself, any other touched module is tests/test_<name>.py or
# tests/test_config_<name>.py. Matching by name is a heuristic, so a module
# whose test carries another name runs only in the full gate.
matching_test_modules() {
    local path stem candidate
    local found=()
    for path in "$@"; do
        if [[ "$path" == tests/* ]]; then
            found+=("$path")
            continue
        fi
        stem="$(basename "$path" .py)"
        for candidate in "tests/test_$stem.py" "tests/test_config_$stem.py"; do
            if [[ -f "$candidate" ]]; then
                found+=("$candidate")
            fi
        done
    done
    if [[ "${#found[@]}" -gt 0 ]]; then
        printf '%s\n' "${found[@]}" | sort -u
    fi
}

if [[ "$fast_mode" -eq 0 ]]; then
    run_gate ruff uv run ruff check .
    run_gate "mypy strict sources" uv run mypy --strict src/ task_data
    run_gate "mypy tests" uv run mypy
    run_gate pytest uv run pytest
    run_gate "bootstrap installer" bash tests/test_inst.sh
    run_gate "launcher" bash tests/test_launcher.sh
    run_gate "pre-commit hook" bash tests/test_pre_commit_hook.sh
    run_gate "landing step" bash tests/test_land_version_commit.sh
    run_gate "system metrics commit script" bash tests/test_commit_script.sh
    printf "every gate passed\n"
    exit 0
fi

mapfile -t touched < <(touched_python_files)
if [[ "${#touched[@]}" -eq 0 ]]; then
    printf "no touched python file, the fast mode has nothing to check, use the full run\n"
    exit 0
fi
printf "fast mode, touched files:\n"
printf '  %s\n' "${touched[@]}"

run_gate ruff uv run ruff check .

touched_strict_sources=()
touched_tests=()
for path in "${touched[@]}"; do
    if [[ "$path" == tests/* ]]; then
        touched_tests+=("$path")
    else
        touched_strict_sources+=("$path")
    fi
done
if [[ "${#touched_strict_sources[@]}" -gt 0 ]]; then
    run_gate "mypy strict touched sources" uv run mypy --strict "${touched_strict_sources[@]}"
fi
if [[ "${#touched_tests[@]}" -gt 0 ]]; then
    run_gate "mypy touched tests" uv run mypy "${touched_tests[@]}"
fi

# One worker process per test module: pytest-xdist spends more on starting
# its workers than a handful of modules cost, so a short selection runs
# serially and only the full suite goes wide.
mapfile -t matched < <(matching_test_modules "${touched[@]}")
if [[ "${#matched[@]}" -eq 0 ]]; then
    printf "no test module matches the touched files by name, the full run covers the rest\n"
else
    printf "fast mode, test modules:\n"
    printf '  %s\n' "${matched[@]}"
    run_gate "pytest matched modules" uv run pytest -q -n 0 "${matched[@]}"
fi

printf "the fast gate passed, run the full gate before a landing\n"
