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

inst_check_root_rejects_non_root_with_error() {
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

inst_check_root_accepts_root_with_success_message() {
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

inst_ensure_fhs_dirs_creates_all_three_directories() {
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

inst_ensure_fhs_dirs_is_idempotent_on_second_run() {
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

inst_log_writes_timestamped_line_to_terminal_and_file() {
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

inst_log_appends_lines_instead_of_overwriting() {
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

inst_log_file_defaults_inside_log_dir() {
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

inst_run_logged_streams_both_streams_and_preserves_exit_code() {
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

inst_main_calls_root_then_dirs_then_log_in_order() {
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

inst_run_timed_logs_duration_and_exit_code() {
    # run_timed runs the command, logs duration with exit code 0, preserves success.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local rc
    set +e
    PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; run_timed true' _ "$INSTALLER" \
        > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
        echo "expected exit code 0, got $rc" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -qE 'Finished in [0-9]+s with exit code 0: true' "$logfile"; then
        echo "duration line missing or malformed in log file" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_run_timed_preserves_failing_exit_code() {
    # A failing command must return its own exit code and log it.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local rc
    set +e
    PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; run_timed sh -c "exit 3"' _ "$INSTALLER" \
        > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -ne 3 ]]; then
        echo "expected exit code 3, got $rc" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -qE 'Finished in [0-9]+s with exit code 3: sh -c exit 3' "$logfile"; then
        echo "duration line missing or malformed in log file" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_apt_install_succeeds_without_index_refresh() {
    # When the first install works, apt-get update must never be called.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    # Mock apt-get records its subcommand and arguments.
    cat > "$bin/apt-get" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$APT_CALLS_FILE"
if [[ "$1" == "install" ]]; then
    exit 0
fi
if [[ "$1" == "update" ]]; then
    exit 0
fi
exit 1
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; apt_install dialog python3' _ "$INSTALLER" 2>&1)"
    local update_calls
    update_calls="$(grep -c '^update$' "$calls" || true)"
    if [[ "$update_calls" -ne 0 ]]; then
        echo "apt-get update called on successful first install" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q '^install -y dialog python3$' "$calls"; then
        echo "install arguments missing from mock calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "Packages installed without index refresh" "success message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_apt_install_refreshes_index_after_first_failure() {
    # When the first install fails, update must run before the retry.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/apt-get" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$APT_CALLS_FILE"
if [[ "$1" == "install" && -f "$INSTALL_FAILED_MARKER" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$bin/apt-get"
    : > "$tmp/failed"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        INSTALL_FAILED_MARKER="$tmp/failed" \
        bash -c 'source "$1"; apt_install dialog' _ "$INSTALLER"
    local update_line
    update_line="$(grep -c '^update$' "$calls" || true)"
    local install_lines
    install_lines="$(grep -c '^install -y dialog$' "$calls" || true)"
    if [[ "$update_line" -ne 1 ]]; then
        echo "apt-get update must be called exactly once" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$install_lines" -ne 2 ]]; then
        echo "expected install twice (before and after update), got $install_lines" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_apt_install_passes_package_list() {
    # All packages must be passed to apt-get install in order.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/apt-get" <<'EOF'
#!/usr/bin/env bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; apt_install dialog python3-venv git' _ "$INSTALLER"
    if ! grep -q '^install -y dialog python3-venv git$' "$calls"; then
        echo "package list not passed in order" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

run_test inst_check_root_rejects_non_root_with_error
run_test inst_check_root_accepts_root_with_success_message
run_test inst_ensure_fhs_dirs_creates_all_three_directories
run_test inst_ensure_fhs_dirs_is_idempotent_on_second_run
run_test inst_log_writes_timestamped_line_to_terminal_and_file
run_test inst_log_appends_lines_instead_of_overwriting
run_test inst_log_file_defaults_inside_log_dir
run_test inst_run_logged_streams_both_streams_and_preserves_exit_code
run_test inst_run_timed_logs_duration_and_exit_code
run_test inst_run_timed_preserves_failing_exit_code
run_test inst_apt_install_succeeds_without_index_refresh
run_test inst_apt_install_refreshes_index_after_first_failure
run_test inst_apt_install_passes_package_list
run_test inst_main_calls_root_then_dirs_then_log_in_order

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
