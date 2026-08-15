#!/usr/bin/env bash
# Unit tests for the pre-commit version bump hook.
# Run with: bash tests/test_pre_commit_hook.sh
# The hook is exercised against a temporary git repository with the real
# hooks/pre-commit file; the real repository files are never touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/../hooks/pre-commit"

pass_count=0
fail_count=0

record_pass() {
    pass_count=$((pass_count + 1))
    echo "PASS: $1"
}

record_fail() {
    fail_count=$((fail_count + 1))
    echo "FAIL: $1"
}

run_test() {
    local name="$1"
    local output
    if output="$("$name" 2>&1)"; then
        record_pass "$name"
    else
        record_fail "$name"
        echo "$output" | sed 's/^/    /'
    fi
}

# Create a temporary git repository with a versioned package and installer
# and one baseline commit at 0.1.0; the hook is enabled only after the
# baseline commit, so the tested commits observe a single bump each.
# The real src/pyntara package is copied so the hook can import the
# bump module and its config_edit dependency from the temporary source.
make_versioned_repo() {
    local tmp="$1"
    git -C "$tmp" init -q
    git -C "$tmp" config user.email test@example.com
    git -C "$tmp" config user.name Test
    mkdir -p "$tmp/src"
    cp -r "$SCRIPT_DIR/../src/pyntara" "$tmp/src/"
    rm -rf "$tmp/src/pyntara/__pycache__"
    cat > "$tmp/src/pyntara/__init__.py" <<'EOF'
"""Pyntara package."""

__version__ = "0.1.0"
EOF
    cat > "$tmp/inst.sh" <<'EOF'
#!/usr/bin/env bash

PYNTARA_VERSION="0.1.0"
EOF
    git -C "$tmp" add -A
    git -C "$tmp" commit -q -m "initial"
    git -C "$tmp" config core.hooksPath "$(dirname "$HOOK")"
}

test_commit_bumps_package_and_installer() {
    # A normal commit must raise the patch version in both files inside
    # the same commit.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    echo "change" > "$tmp/change.txt"
    git -C "$tmp" add change.txt
    git -C "$tmp" commit -q -m "second"
    if ! grep -q '__version__ = "0.1.1"' "$tmp/src/pyntara/__init__.py"; then
        echo "package version not bumped to 0.1.1" >&2
        cat "$tmp/src/pyntara/__init__.py" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q 'PYNTARA_VERSION="0.1.1"' "$tmp/inst.sh"; then
        echo "installer version not bumped to 0.1.1" >&2
        cat "$tmp/inst.sh" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:src/pyntara/__init__.py | grep -q '0.1.1'; then
        echo "bumped version not part of the commit" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_without_change_does_not_block() {
    # A commit whose files need no bump must still succeed; the hook may
    # bump the version again, but must never fail the commit.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    echo "change" > "$tmp/change.txt"
    git -C "$tmp" add change.txt
    git -C "$tmp" commit -q -m "second"
    echo "more" >> "$tmp/change.txt"
    git -C "$tmp" add change.txt
    git -C "$tmp" commit -q -m "third"
    if ! git -C "$tmp" log --oneline | grep -q "third"; then
        echo "third commit missing, hook blocked the commit" >&2
        git -C "$tmp" log --oneline >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

run_test test_commit_bumps_package_and_installer
run_test test_commit_without_change_does_not_block

echo "Tests passed: $pass_count, failed: $fail_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
