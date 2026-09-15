#!/usr/bin/env bash
# Landing step: bump the version once and record it as one commit.
# Run it on the branch tip right before the push to main, so the installer
# line and the README title move exactly once per landing and never
# collide in a rebase (docs/guides/developer-guide.md, Version bumping).
# The commit is built from the version carriers alone, so work staged by
# another agent in the same clone is never swept into it. The step stops
# loudly: a landing without a version is a defect, a stopped landing is
# not.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    echo "landing: not inside a git repository, nothing to land" >&2
    exit 1
fi

run_bump() {
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root/src" \
        python3 -m pyntara.bump_version --root "$repo_root" "$@"
}

if ! carriers="$(run_bump --print-carrier-paths)"; then
    echo "landing: the version carriers could not be listed, nothing to land" >&2
    exit 1
fi
if [[ -z "$carriers" ]]; then
    echo "landing: the repository has no version carrier to commit" >&2
    exit 1
fi
readarray -t carrier_paths <<< "$carriers"

dirty="$(git -C "$repo_root" status --porcelain -- "${carrier_paths[@]}")"
if [[ -n "$dirty" ]]; then
    echo "landing: a version carrier carries uncommitted changes, finish them first:" >&2
    echo "$dirty" >&2
    exit 1
fi

if ! new_version="$(run_bump)"; then
    echo "landing: the version bump failed, nothing was committed" >&2
    exit 1
fi

# The pre-commit hook bumps the build carrier on every commit, and this
# commit is the bump itself, so the hook is skipped here to keep the
# three carriers on one number.
if ! git -C "$repo_root" commit --quiet --no-verify -m "version: bump to $new_version" -- "${carrier_paths[@]}"; then
    echo "landing: the version commit failed, the bumped files stay in the working tree" >&2
    exit 1
fi
echo "landing: version $new_version committed"
