#!/usr/bin/env bash
# Unit tests for the landing version commit step.
# Run with: bash tests/test_land_version_commit.sh
# The step is exercised against a temporary git repository with the real
# hooks/land_version_commit.sh file; the real repository files are never
# touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANDING_STEP="$SCRIPT_DIR/../hooks/land_version_commit.sh"
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
# installer and README and one baseline commit at 0.1.0. The real
# src/pyntara package is copied so the step can import the bump module and
# its config_edit dependency from the temporary source.
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
}

# Run the landing step inside the temporary repository, the way an operator
# runs it on a branch tip: the step works on the repository of the current
# directory.
run_landing_step() {
    local tmp="$1"
    (cd "$tmp" && "$LANDING_STEP")
}

test_landing_step_is_executable() {
    # The step is called directly, so the executable bit must be set.
    if [[ ! -x "$LANDING_STEP" ]]; then
        echo "landing step is not executable: $LANDING_STEP" >&2
        return 1
    fi
}

test_landing_step_commits_all_carriers_in_one_commit() {
    # One run must raise the patch version in the build carrier, the
    # installer and the README and record them in a single commit.
    local tmp output
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    if ! output="$(run_landing_step "$tmp" 2>&1)"; then
        echo "landing step failed: $output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$output" != *"0.1.1"* ]]; then
        echo "the new version is not reported: $output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" rev-list --count HEAD)" != "2" ]]; then
        echo "expected one new commit, got $(git -C "$tmp" rev-list --count HEAD)" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" log -1 --pretty=%s)" != "version: bump to 0.1.1" ]]; then
        echo "unexpected subject: $(git -C "$tmp" log -1 --pretty=%s)" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:"$CARRIER" | grep -q '__version__ = "0.1.1"'; then
        echo "carrier version not in the commit" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:inst.sh | grep -q 'PYNTARA_VERSION="0.1.1"'; then
        echo "installer version not in the commit" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" show HEAD:README.md | grep -q '# Pyntara 0.1.1'; then
        echo "readme version not in the commit" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ -n "$(git -C "$tmp" status --porcelain -- "$CARRIER" inst.sh README.md)" ]]; then
        echo "carriers left dirty after the landing commit" >&2
        git -C "$tmp" status --porcelain >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_landing_step_carries_no_foreign_staged_work() {
    # The commit is built from the carriers alone, so a file another agent
    # staged in the same clone stays staged and uncommitted.
    local tmp changed
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    echo "other work" > "$tmp/other.txt"
    git -C "$tmp" add other.txt
    if ! run_landing_step "$tmp" >/dev/null 2>&1; then
        echo "landing step failed" >&2
        rm -rf "$tmp"
        return 1
    fi
    changed="$(git -C "$tmp" show --name-only --pretty=format: HEAD | LC_ALL=C sort | tr '\n' ' ')"
    if [[ "$changed" != "README.md inst.sh src/pyntara/_version.py " ]]; then
        echo "unexpected files in the landing commit: [$changed]" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! git -C "$tmp" status --porcelain | grep -q '^A  other.txt$'; then
        echo "the staged foreign file was not left staged" >&2
        git -C "$tmp" status --porcelain >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_landing_step_grows_the_version_on_each_run() {
    # Every landing raises the patch step by one, so the version stays
    # monotone across landings.
    local tmp
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    run_landing_step "$tmp" >/dev/null 2>&1
    run_landing_step "$tmp" >/dev/null 2>&1
    if ! git -C "$tmp" show HEAD:"$CARRIER" | grep -q '__version__ = "0.1.2"'; then
        echo "the second landing did not reach 0.1.2" >&2
        git -C "$tmp" show HEAD:"$CARRIER" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" rev-list --count HEAD)" != "3" ]]; then
        echo "expected three commits, got $(git -C "$tmp" rev-list --count HEAD)" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_landing_step_fails_loudly_without_a_version_line() {
    # A repository without the version line stops the landing with a
    # message instead of recording a commit without a version.
    local tmp output rc
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    printf 'no version here\n' > "$tmp/$CARRIER"
    git -C "$tmp" commit -q -am "drop the version line"
    set +e
    output="$(run_landing_step "$tmp" 2>&1)"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
        echo "the landing step succeeded without a version line" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ -z "$output" ]]; then
        echo "the failure was silent" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" rev-list --count HEAD)" != "2" ]]; then
        echo "a commit was recorded without a version" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_landing_step_refuses_a_dirty_carrier() {
    # An uncommitted change in a carrier is work in progress, possibly of
    # another agent: the step stops instead of committing it with the
    # version.
    local tmp output rc
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    printf '\nHalf written note.\n' >> "$tmp/README.md"
    set +e
    output="$(run_landing_step "$tmp" 2>&1)"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
        echo "the landing step landed with a dirty carrier" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$output" != *"uncommitted"* ]]; then
        echo "the refusal does not name the reason: $output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(git -C "$tmp" rev-list --count HEAD)" != "1" ]]; then
        echo "a commit was recorded while a carrier was dirty" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_landing_step_fails_when_a_carrier_lost_its_line() {
    # A README title rewritten by hand stops the landing with a message
    # naming the carrier, instead of mirroring nothing and keeping the old
    # number without a word.
    local tmp output rc
    tmp="$(mktemp -d)"
    make_versioned_repo "$tmp"
    printf '# Pyntara\n' > "$tmp/README.md"
    git -C "$tmp" commit -q -am "docs: reword the title"
    set +e
    output="$(run_landing_step "$tmp" 2>&1)"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
        echo "the landing step landed with a carrier that has no version line" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$output" != *"README.md"* ]]; then
        echo "the failure does not name the carrier: $output" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

run_test test_landing_step_is_executable
run_test test_landing_step_commits_all_carriers_in_one_commit
run_test test_landing_step_carries_no_foreign_staged_work
run_test test_landing_step_grows_the_version_on_each_run
run_test test_landing_step_fails_loudly_without_a_version_line
run_test test_landing_step_refuses_a_dirty_carrier
run_test test_landing_step_fails_when_a_carrier_lost_its_line

echo "Tests passed: $pass_count, failed: $fail_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
