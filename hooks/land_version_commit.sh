#!/usr/bin/env bash
# Landing step: bump the patch version once and record it as one commit.
# Run it on the branch tip right before the push to main, so the version
# grows once per landing instead of once per commit and the version lines
# stop colliding in every rebase (docs/guides/developer-guide.md, Version
# bumping). The commit is built from the version carriers alone, so work
# staged by another agent in the same clone is never swept into it.
# Unlike the pre-commit hook this step replaced, it stops loudly: a
# landing without a version is a defect, a stopped landing is not.
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

if ! new_version="$(run_bump)"; then
    echo "landing: the version bump failed, nothing was committed" >&2
    exit 1
fi

readarray -t carrier_paths <<< "$carriers"
if ! git -C "$repo_root" commit --quiet -m "version: bump to $new_version" -- "${carrier_paths[@]}"; then
    echo "landing: the version commit failed, the bumped files stay in the working tree" >&2
    exit 1
fi
echo "landing: version $new_version committed"
