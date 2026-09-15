#!/usr/bin/env bash
# Unit tests for the pre-commit build version hook.
# Run with: bash tests/test_pre_commit_hook.sh
# The hook is exercised against a temporary git repository with the real
# hooks/pre-commit file; the real repository files are never touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/../hooks/pre-commit"
CARRIER="src/pyntara/_version.py"

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

# Create a temporary git repository with a versioned build carrier,
# installer and README and one baseline commit at 0.1.0; the hook is enabled
# only after the baseline commit. The real src/pyntara package is copied so
# the hook can import the bump module and its config_edit dependency.
make_versioned_repo() {
    local tmp="$1"
    git -C "$tmp" init -q
    git -C "$tmp" config user.email test@example.com
    git -C "$tmp" config user.name Test
    mkdir -p "$tmp/src"
    cp -r "$SCRIPT_DIR/../src/pyntara" "$tmp/src/"
    rm -rf "$tmp/src/pyntara/__pycache__"
    printf '__version__ = "0.1.0"\n' > "$tmp/$CARRIER"
    cat > "$tmp/inst.sh" <<'EOF'
#!/usr/bin/env bash

PYNTARA_VERSION="0.1.0"
EOF
    cat > "$tmp/README.md" <<'EOF'
# Pyntara 0.1.0
EOF
    git -C "$tmp" add -A
    git -C "$tmp" commit -q -m "initial"
    git -C "$tmp" config core.hooksPath "$(dirname "$HOOK")"
}

test_hook_is_executable() {
    # The hook is called by git, so the executable bit must be set.
    if [[ ! -x "$HOOK" ]]; then
        echo "hook is not executable: $HOOK" >&2
        return 1
    fi
}

test_commit_bumps_the_carrier_alone() {
    # The commit carries the new number of the build carrier, and the two
    # machine owned carriers of the landing step stay untouched.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    echo "change" > "$tmp/change.txt"
    git -C "$tmp" add change.txt
    git -C "$tmp" commit -q -m "second"
    if ! git -C "$tmp" show HEAD:"$CARRIER" | grep -q '__version__ = "0.1.1"'; then
        echo "carrier not bumped to 0.1.1 in the commit" >&2
        git -C "$tmp" show HEAD:"$CARRIER" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:inst.sh | grep -q 'PYNTARA_VERSION="0.1.0"'; then
        echo "the hook touched inst.sh, which belongs to the landing step" >&2
        git -C "$tmp" show HEAD:inst.sh >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:README.md | grep -q '# Pyntara 0.1.0'; then
        echo "the hook touched README.md, which belongs to the landing step" >&2
        git -C "$tmp" show HEAD:README.md >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_every_commit_grows_the_number() {
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    echo "one" > "$tmp/one.txt"
    git -C "$tmp" add one.txt
    git -C "$tmp" commit -q -m "second"
    echo "two" > "$tmp/two.txt"
    git -C "$tmp" add two.txt
    git -C "$tmp" commit -q -m "third"
    if ! git -C "$tmp" show HEAD:"$CARRIER" | grep -q '__version__ = "0.1.2"'; then
        echo "the third commit did not reach 0.1.2" >&2
        git -C "$tmp" show HEAD:"$CARRIER" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_normalizes_a_union_carrier() {
    # A union merge can leave two version lines behind; the next commit
    # reads the highest number and writes the file back as one line.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    printf '__version__ = "0.1.9"\n__version__ = "0.1.2"\n' > "$tmp/$CARRIER"
    echo "one" > "$tmp/one.txt"
    git -C "$tmp" add one.txt
    git -C "$tmp" commit -q -m "second"
    if [[ "$(git -C "$tmp" show HEAD:"$CARRIER")" != '__version__ = "0.1.10"' ]]; then
        echo "the carrier was not normalized to a single line" >&2
        git -C "$tmp" show HEAD:"$CARRIER" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_broken_carrier_never_blocks_a_commit() {
    # The hook is best-effort: a carrier it cannot read must not stop the
    # commit, must not be rewritten, and must be reported on stderr.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    printf 'no version here\n' > "$tmp/$CARRIER"
    git -C "$tmp" add "$CARRIER"
    echo "one" > "$tmp/one.txt"
    git -C "$tmp" add one.txt
    if ! git -C "$tmp" commit -q -m "second" 2> "$tmp/warning.txt"; then
        echo "the hook blocked the commit" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" show HEAD:"$CARRIER")" != "no version here" ]]; then
        echo "the hook rewrote a carrier it could not read" >&2
        git -C "$tmp" show HEAD:"$CARRIER" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q 'pyntara pre-commit' "$tmp/warning.txt"; then
        echo "the skipped bump was silent" >&2
        cat "$tmp/warning.txt" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

run_test test_hook_is_executable
run_test test_commit_bumps_the_carrier_alone
run_test test_every_commit_grows_the_number
run_test test_commit_normalizes_a_union_carrier
run_test test_broken_carrier_never_blocks_a_commit

echo "Tests passed: $pass_count, failed: $fail_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
