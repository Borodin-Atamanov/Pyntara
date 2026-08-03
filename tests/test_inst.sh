#!/usr/bin/env bash
# Unit tests for the bootstrap installer inst.sh.
# Run with: bash tests/test_inst.sh
# Every test runs in a separate bash process so state never leaks between tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/../inst.sh"

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

assert_equals() {
    local expected="$1"
    local actual="$2"
    local detail="$3"
    if [[ "$expected" != "$actual" ]]; then
        echo "expected [$expected], got [$actual]: $detail" >&2
        return 1
    fi
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    local detail="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        echo "expected output to contain [$needle]: $detail" >&2
        echo "got: [$haystack]" >&2
        return 1
    fi
}

test_check_root_rejects_non_root() {
    # Non-root must fail with exit code 1 and an error on stderr.
    local output
    local rc
    if [[ "$EUID" -eq 0 ]]; then
        # Running as root, drop privileges to test the non-root branch.
        if command -v setpriv >/dev/null 2>&1; then
            set +e
            output="$(setpriv --reuid=65534 --regid=65534 --clear-groups \
                bash -c 'source "$1"; check_root' _ "$INSTALLER" 2>&1)"
            rc=$?
            set -e
        else
            echo "SKIP: setpriv unavailable to drop privileges"
            return 0
        fi
    else
        set +e
        output="$(bash -c 'source "$1"; check_root' _ "$INSTALLER" 2>&1)"
        rc=$?
        set -e
    fi
    assert_equals "1" "$rc" "non-root check_root exit code" || return 1
    assert_contains "$output" "must run as root" "non-root check_root error message" || return 1
}

test_check_root_accepts_root() {
    # Root must print the success message and exit with code 0.
    local output
    if [[ "$EUID" -eq 0 ]]; then
        output="$(bash -c 'source "$1"; check_root' _ "$INSTALLER" 2>&1)"
    else
        if command -v unshare >/dev/null 2>&1; then
            output="$(unshare -r bash -c 'source "$1"; check_root' _ "$INSTALLER" 2>&1)"
        else
            echo "SKIP: unshare unavailable to simulate root"
            return 0
        fi
    fi
    assert_contains "$output" "Running as root" "root check_root success message" || return 1
}

test_ensure_fhs_dirs() {
    # All three FHS directories must be created and listed in the message.
    local tmp
    tmp="$(mktemp -d)"
    local output
    output="$(PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_STATE_DIR="$tmp/lib" PYNTARA_LOG_DIR="$tmp/log" \
        bash -c 'source "$1"; ensure_fhs_dirs' _ "$INSTALLER" 2>&1)"
    if [[ ! -d "$tmp/cache" ]]; then
        echo "cache directory missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ ! -d "$tmp/lib" ]]; then
        echo "state directory missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ ! -d "$tmp/log" ]]; then
        echo "log directory missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "FHS directories ready: $tmp/cache" "message lists cache path" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

test_ensure_fhs_dirs_idempotent() {
    # A second run must succeed and leave the directories intact.
    local tmp
    tmp="$(mktemp -d)"
    PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_STATE_DIR="$tmp/lib" PYNTARA_LOG_DIR="$tmp/log" \
        bash -c 'source "$1"; ensure_fhs_dirs; ensure_fhs_dirs' _ "$INSTALLER"
    if [[ ! -d "$tmp/cache" || ! -d "$tmp/lib" || ! -d "$tmp/log" ]]; then
        echo "directories missing after second run" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_log() {
    # log writes a timestamped line to stdout and to the log file.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; log "hello world"' _ "$INSTALLER" 2>&1)"
    if [[ ! "$output" =~ ^\[[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}\]\ hello\ world$ ]]; then
        echo "unexpected log line: [$output]" >&2
        rm -rf "$tmp"
        return 1
    fi
    local file_line
    file_line="$(cat "$logfile")"
    if [[ "$file_line" != "$output" ]]; then
        echo "log file line differs from terminal line" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_log_appends() {
    # Repeated calls must append lines, not overwrite the file.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    PYNTARA_LOG_FILE="$logfile" bash -c 'source "$1"; log "first"; log "second"' _ "$INSTALLER"
    local lines
    lines="$(wc -l < "$logfile")"
    if [[ "$lines" -ne 2 ]]; then
        echo "expected 2 log lines, got $lines" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_log_file_default() {
    # Without PYNTARA_LOG_FILE the log file must live inside LOG_DIR.
    local tmp
    tmp="$(mktemp -d)"
    local output
    output="$(PYNTARA_LOG_DIR="$tmp/log" bash -c 'source "$1"; echo "$LOG_FILE"' _ "$INSTALLER" 2>&1)"
    assert_equals "$tmp/log/install.log" "$output" "LOG_FILE defaults into LOG_DIR" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

test_run_logged() {
    # stdout and stderr go to terminal and log file, exit code is preserved.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local rc
    set +e
    PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; run_logged sh -c "echo out; echo err >&2; exit 3"' _ "$INSTALLER" \
        > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -ne 3 ]]; then
        echo "expected exit code 3, got $rc" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^out$" "$tmp/out"; then
        echo "stdout missing from terminal output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^err$" "$tmp/out"; then
        echo "stderr missing from terminal output" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^out$" "$logfile" || ! grep -q "^err$" "$logfile"; then
        echo "stdout or stderr missing from log file" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

test_main_composition() {
    # main must call check_root, then ensure_fhs_dirs, then log.
    # Mocks are declared before source so the guard keeps them.
    local tmp
    tmp="$(mktemp -d)"
    local flags="$tmp/flags"
    bash -c '
        set -euo pipefail
        flags_file="$2"
        check_root() { echo check_root >> "$flags_file"; }
        ensure_fhs_dirs() { echo ensure_fhs_dirs >> "$flags_file"; }
        log() { echo log >> "$flags_file"; }
        source "$1"
        main
    ' _ "$INSTALLER" "$flags"
    local expected
    expected="$(printf 'check_root\nensure_fhs_dirs\nlog')"
    local actual
    actual="$(cat "$flags")"
    if [[ "$actual" != "$expected" ]]; then
        echo "unexpected call order: [$actual]" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

run_test test_check_root_rejects_non_root
run_test test_check_root_accepts_root
run_test test_ensure_fhs_dirs
run_test test_ensure_fhs_dirs_idempotent
run_test test_log
run_test test_log_appends
run_test test_log_file_default
run_test test_run_logged
run_test test_main_composition

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
