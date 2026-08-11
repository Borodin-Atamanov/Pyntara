#!/usr/bin/env bash
# Unit tests for the generated commit_system_metrics command script.
# Run with: bash tests/test_commit_script.sh
# The command is rendered from the repository template with temporary
# values, so the real spool directory is never touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../task_data/system_metrics_setup/commit_system_metrics.sh"

pass_count=0
fail_count=0
skip_count=0

record_pass() {
    pass_count=$((pass_count + 1))
    echo "PASS: $1"
}

record_fail() {
    fail_count=$((fail_count + 1))
    echo "FAIL: $1"
}

# Run one test function. A test prints SKIP: to skip, otherwise it passes.
run_test() {
    local name="$1"
    local output
    if output="$("$name" 2>&1)"; then
        if [[ "$output" == SKIP:* ]]; then
            skip_count=$((skip_count + 1))
            echo "SKIP: $name: ${output#SKIP: }"
        else
            record_pass "$name"
        fi
    else
        record_fail "$name"
        echo "$output" | sed 's/^/    /'
    fi
}

# Render the command from the repository template into a temporary file.
make_command() {
    local spool_dir="$1"
    local identifier="${2:-commit_system_metrics_test}"
    local script
    script="$(mktemp)"
    sed -e "s|\$spool_dir|$spool_dir|g" \
        -e "s|\$commit_journal_identifier|$identifier|g" \
        -e "s|\$spool_temp_prefix|.commit-|g" \
        "$TEMPLATE" > "$script"
    chmod +x "$script"
    echo "$script"
}

test_commit_publishes_file_into_spool() {
    # A regular non-empty file is copied into the spool under its own name
    # with mode 0600 and the commit time; the source is untouched and the
    # entry path is printed on stdout.
    local tmp spool script source
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    source="$tmp/report.txt"
    printf 'hello' > "$source"
    script="$(make_command "$spool")"
    local now_before now_after output rc
    now_before="$(date +%s)"
    set +e
    output="$("$script" "$source" 2>&1)"
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -ne 0 ]]; then
        echo "expected exit 0, got $rc: $output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$output" != "$spool/report.txt" ]]; then
        echo "expected entry path on stdout, got [$output]" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(cat "$spool/report.txt")" != "hello" ]]; then
        echo "spool entry content mismatch" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(stat -c %a "$spool/report.txt")" != "600" ]]; then
        echo "expected mode 600, got $(stat -c %a "$spool/report.txt")" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(cat "$source")" != "hello" ]]; then
        echo "source file modified" >&2
        rm -rf "$tmp"
        return 1
    fi
    now_after="$(date +%s)"
    local mtime
    mtime="$(stat -c %Y "$spool/report.txt")"
    if [[ "$mtime" -lt "$now_before" || "$mtime" -gt "$now_after" ]]; then
        echo "entry mtime is not the commit time" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_rejects_collision() {
    # A second commit of the same name fails and never overwrites the
    # pending entry.
    local tmp spool script source
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    source="$tmp/report.txt"
    printf 'first' > "$source"
    script="$(make_command "$spool")"
    local first_rc second_rc
    set +e
    "$script" "$source" > /dev/null 2>&1
    first_rc=$?
    printf 'second' > "$source"
    "$script" "$source" > /dev/null 2>&1
    second_rc=$?
    set -e
    rm -f "$script"
    if [[ "$first_rc" -ne 0 || "$second_rc" -eq 0 ]]; then
        echo "first commit must succeed, second must fail" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(cat "$spool/report.txt")" != "first" ]]; then
        echo "pending entry overwritten by collision" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_rejects_empty_file() {
    # An empty source never reaches the spool.
    local tmp spool script source
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    source="$tmp/empty.txt"
    : > "$source"
    script="$(make_command "$spool")"
    local rc
    set +e
    "$script" "$source" > /dev/null 2>&1
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -eq 0 ]]; then
        echo "empty file must be rejected" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ -n "$(ls -A "$spool")" ]]; then
        echo "empty file must not reach the spool" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_rejects_directory() {
    # A directory argument is not a regular file and must be rejected.
    local tmp spool script
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    script="$(make_command "$spool")"
    local rc
    set +e
    "$script" "$tmp" > /dev/null 2>&1
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -eq 0 ]]; then
        echo "directory argument must be rejected" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_rejects_missing_spool_dir() {
    # Without the spool directory the command fails loudly.
    local tmp script source
    tmp="$(mktemp -d)"
    source="$tmp/report.txt"
    printf 'x' > "$source"
    script="$(make_command "$tmp/nonexistent")"
    local rc
    set +e
    "$script" "$source" > /dev/null 2>&1
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -eq 0 ]]; then
        echo "missing spool must be rejected" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_rejects_multiple_arguments() {
    # Exactly one file argument is expected; more must be rejected.
    local tmp spool script
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    printf 'x' > "$tmp/a.txt"
    printf 'y' > "$tmp/b.txt"
    script="$(make_command "$spool")"
    local rc
    set +e
    "$script" "$tmp/a.txt" "$tmp/b.txt" > /dev/null 2>&1
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -eq 0 ]]; then
        echo "multiple arguments must be rejected" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_leaves_no_temp_files() {
    # After a successful commit the spool holds only the published entry.
    local tmp spool script source leftovers
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    source="$tmp/report.txt"
    printf 'x' > "$source"
    script="$(make_command "$spool")"
    "$script" "$source" > /dev/null 2>&1
    leftovers="$(ls -A "$spool" | grep '^\.commit-' || true)"
    rm -f "$script"
    if [[ -n "$leftovers" ]]; then
        echo "temp files left in spool: $leftovers" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_survives_failing_systemd_cat() {
    # A failing systemd-cat must never stop the commit (best effort).
    local tmp spool script source bin rc
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool" "$tmp/bin"
    source="$tmp/report.txt"
    printf 'x' > "$source"
    script="$(make_command "$spool")"
    cat > "$tmp/bin/systemd-cat" <<'EOF'
#!/bin/bash
cat > /dev/null
exit 1
EOF
    chmod +x "$tmp/bin/systemd-cat"
    set +e
    PATH="$tmp/bin:$PATH" "$script" "$source" > /dev/null 2>&1
    rc=$?
    set -e
    rm -f "$script"
    if [[ "$rc" -ne 0 ]]; then
        echo "commit must succeed even when systemd-cat fails" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_commit_forwards_to_system_journal() {
    # The commit must duplicate its action into the system journal under
    # the embedded identifier.
    local tmp spool script source identifier marker
    tmp="$(mktemp -d)"
    spool="$tmp/spool"
    mkdir -p "$spool"
    identifier="commit_test_$$"
    marker="journal-$$"
    source="$tmp/$marker.txt"
    printf 'x' > "$source"
    script="$(make_command "$spool" "$identifier")"
    # Probe journal availability first; without journald the test skips.
    if ! printf '%s\n' "probe-$marker" | systemd-cat --identifier "$identifier" 2>/dev/null; then
        echo "SKIP: systemd-cat unavailable"
        rm -f "$script"
        rm -rf "$tmp"
        return 0
    fi
    sleep 0.2
    if ! journalctl --user SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "probe-$marker" &&
        ! journalctl SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "probe-$marker"; then
        echo "SKIP: journal not readable"
        rm -f "$script"
        rm -rf "$tmp"
        return 0
    fi
    "$script" "$source" > /dev/null 2>&1
    local found=""
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if journalctl --user SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "$marker"; then
            found="user"
            break
        fi
        if journalctl SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "$marker"; then
            found="system"
            break
        fi
        sleep 0.1
    done
    rm -f "$script"
    rm -rf "$tmp"
    if [[ -z "$found" ]]; then
        echo "journal line missing for identifier $identifier" >&2
        return 1
    fi
}

run_test test_commit_publishes_file_into_spool
run_test test_commit_rejects_collision
run_test test_commit_rejects_empty_file
run_test test_commit_rejects_directory
run_test test_commit_rejects_missing_spool_dir
run_test test_commit_rejects_multiple_arguments
run_test test_commit_leaves_no_temp_files
run_test test_commit_survives_failing_systemd_cat
run_test test_commit_forwards_to_system_journal

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
