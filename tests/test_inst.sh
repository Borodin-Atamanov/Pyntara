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

inst_log_forwards_to_system_journal() {
    # log must duplicate its message into the system journal under the
    # identifier from PYNTARA_JOURNAL_IDENTIFIER, without the timestamp.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local identifier
    identifier="pyntara-test-$$"
    local marker
    marker="journal-smoke-$$"
    # Probe journal availability first; without journald the test skips.
    if ! printf '%s\n' "probe-$marker" | systemd-cat --identifier "$identifier" 2>/dev/null; then
        echo "SKIP: systemd-cat unavailable"
        rm -rf "$tmp"
        return 0
    fi
    sleep 0.3
    if ! journalctl --user SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "probe-$marker" &&
        ! journalctl SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "probe-$marker"; then
        echo "SKIP: journal not readable"
        rm -rf "$tmp"
        return 0
    fi
    PYNTARA_LOG_FILE="$logfile" PYNTARA_JOURNAL_IDENTIFIER="$identifier" \
        bash -c 'source "$1"; log "smoke $2"' _ "$INSTALLER" "$marker"
    local found
    found=""
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if journalctl --user SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "smoke $marker"; then
            found="user"
            break
        fi
        if journalctl SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep -q "smoke $marker"; then
            found="system"
            break
        fi
        sleep 0.1
    done
    if [[ -z "$found" ]]; then
        echo "journal line missing for identifier $identifier" >&2
        rm -rf "$tmp"
        return 1
    fi
    # The journal line must be the plain message, not the timestamped one.
    local line
    line="$(journalctl --user SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep "smoke $marker" | head -n 1)"
    if [[ -z "$line" ]]; then
        line="$(journalctl SYSLOG_IDENTIFIER="$identifier" --no-pager -o cat 2>/dev/null | grep "smoke $marker" | head -n 1)"
    fi
    if [[ "$line" != "smoke $marker" ]]; then
        echo "journal line must be plain, got [$line]" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_log_empty_identifier_disables_journal() {
    # An empty PYNTARA_JOURNAL_IDENTIFIER must disable journal forwarding.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local marker
    marker="no-journal-$$"
    PYNTARA_LOG_FILE="$logfile" PYNTARA_JOURNAL_IDENTIFIER="" \
        bash -c 'source "$1"; log "quiet $2"' _ "$INSTALLER" "$marker"
    local seen
    seen=""
    local i
    for i in 1 2 3 4 5; do
        if journalctl --user SYSLOG_IDENTIFIER=pyntara-install --no-pager -o cat 2>/dev/null | grep -q "$marker"; then
            seen="user"
            break
        fi
        if journalctl SYSLOG_IDENTIFIER=pyntara-install --no-pager -o cat 2>/dev/null | grep -q "$marker"; then
            seen="system"
            break
        fi
        sleep 0.1
    done
    if [[ -n "$seen" ]]; then
        echo "message reached the journal despite empty identifier" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "$marker" "$logfile"; then
        echo "message missing from log file" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_log_survives_failing_systemd_cat() {
    # A failing systemd-cat must never stop the installer (best effort).
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/systemd-cat" <<'EOF'
#!/bin/bash
cat > /dev/null
exit 1
EOF
    chmod +x "$bin/systemd-cat"
    local rc
    set +e
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; log "still works"' _ "$INSTALLER"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 ]]; then
        echo "log must succeed even when systemd-cat fails" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "still works" "$logfile"; then
        echo "message missing from log file" >&2
        rm -rf "$tmp"
        return 1
    fi
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
    # main must echo the installer version first, then call check_root,
    # ensure_fhs_dirs, log (install log started), install_dependencies,
    # install_uv, fetch_source, setup_python, then run_pyntara and the final
    # log. Mocks are declared before source so the guard keeps them.
    local tmp
    tmp="$(mktemp -d)"
    local flags="$tmp/flags"
    bash -c '
        set -euo pipefail
        flags_file="$2"
        check_root() { echo check_root >> "$flags_file"; }
        ensure_fhs_dirs() { echo ensure_fhs_dirs >> "$flags_file"; }
        log() { echo log >> "$flags_file"; }
        install_dependencies() { echo install_dependencies >> "$flags_file"; }
        install_uv() { echo install_uv >> "$flags_file"; }
        fetch_source() { echo fetch_source >> "$flags_file"; }
        setup_python() { echo setup_python >> "$flags_file"; }
        prompt_vault_password() { echo prompt_vault_password >> "$flags_file"; }
        prompt_install_mode() { echo prompt_install_mode >> "$flags_file"; }
        run_pyntara() { echo "run_pyntara $*" >> "$flags_file"; }
        source "$1"
        main "--test-arg"
    ' _ "$INSTALLER" "$flags"
    local expected
    expected="$(printf 'check_root\nensure_fhs_dirs\nlog\ninstall_dependencies\ninstall_uv\nfetch_source\nsetup_python\nprompt_vault_password\nprompt_install_mode\nrun_pyntara --test-arg\nlog')"
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

inst_apt_install_updates_before_install() {
    # By default the index is refreshed before the install, exactly once.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; apt_install dialog python3' _ "$INSTALLER" 2>&1)"
    local update_calls
    update_calls="$(grep -c '^update$' "$calls" || true)"
    local install_calls
    install_calls="$(grep -c '^install -y dialog python3$' "$calls" || true)"
    if [[ "$update_calls" -ne 1 ]]; then
        echo "apt-get update must be called exactly once by default" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$install_calls" -ne 1 ]]; then
        echo "apt-get install must be called exactly once, got $install_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$(head -1 "$calls")" != "update" ]]; then
        echo "update must precede install in the call order" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "Refreshing package index before install" "refresh message" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "Packages installed: dialog python3" "success message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_apt_install_skips_update_when_flag_set() {
    # PYNTARA_SKIP_APT_UPDATE=1 must skip the index refresh but keep the install.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        PYNTARA_SKIP_APT_UPDATE=1 \
        bash -c 'source "$1"; apt_install dialog' _ "$INSTALLER" 2>&1)"
    local update_calls
    update_calls="$(grep -c '^update$' "$calls" || true)"
    local install_calls
    install_calls="$(grep -c '^install -y dialog$' "$calls" || true)"
    if [[ "$update_calls" -ne 0 ]]; then
        echo "apt-get update must be skipped when PYNTARA_SKIP_APT_UPDATE is set" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$install_calls" -ne 1 ]]; then
        echo "apt-get install must still run once, got $install_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "Package index refresh skipped" "skip message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_apt_install_warns_and_continues_when_update_fails() {
    # A failed apt-get update is a warning, not a failure: install still runs.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local calls="$tmp/apt_calls"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
if [[ "$1" == "update" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; apt_install dialog' _ "$INSTALLER" 2>&1)"
    local update_calls
    update_calls="$(grep -c '^update$' "$calls" || true)"
    local install_calls
    install_calls="$(grep -c '^install -y dialog$' "$calls" || true)"
    if [[ "$update_calls" -ne 1 ]]; then
        echo "apt-get update must be attempted once" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$install_calls" -ne 1 ]]; then
        echo "install must continue after a failed update, got $install_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "Package index refresh failed, continuing with the existing index" "warning message" || {
        rm -rf "$tmp"
        return 1
    }
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
#!/bin/bash
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

inst_install_dependencies_skips_when_all_present() {
    # When every package is installed, apt_install must not be called.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/apt_calls"
    mkdir -p "$bin"
    cat > "$bin/dpkg" <<'EOF'
#!/bin/bash
exit 0
EOF
    chmod +x "$bin/dpkg"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; install_dependencies' _ "$INSTALLER" 2>&1)"
    if [[ -s "$calls" ]]; then
        echo "apt_install called when all packages present" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "already installed" "skip message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_install_dependencies_installs_missing_packages() {
    # When dpkg reports a package missing, only that package is installed.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/apt_calls"
    mkdir -p "$bin"
    cat > "$bin/dpkg" <<'EOF'
#!/bin/bash
# Simulate a missing python3 package.
if [[ "$1" == "-s" && "$2" == "python3" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$bin/dpkg"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; install_dependencies' _ "$INSTALLER"
    if ! grep -q '^install -y python3$' "$calls"; then
        echo "missing package python3 not installed" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q 'git' "$calls"; then
        echo "present package git installed unnecessarily" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_install_dependencies_reports_all_installed() {
    # The success message must list the installed package set.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/apt_calls"
    mkdir -p "$bin"
    cat > "$bin/dpkg" <<'EOF'
#!/bin/bash
exit 0
EOF
    chmod +x "$bin/dpkg"
    cat > "$bin/apt-get" <<'EOF'
#!/bin/bash
echo "$@" >> "$APT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/apt-get"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" APT_CALLS_FILE="$calls" \
        bash -c 'source "$1"; install_dependencies' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "All runtime packages already installed" "message text" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_install_uv_skips_when_already_installed() {
    # When uv is already on PATH, the installer must not be downloaded.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/curl_calls"
    mkdir -p "$bin"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
exit 0
EOF
    chmod +x "$bin/uv"
    cat > "$bin/curl" <<'EOF'
#!/bin/bash
echo "$@" >> "$CURL_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/curl"
    local output
    output="$(PATH="$bin:/usr/bin:/bin" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
        CURL_CALLS_FILE="$calls" bash -c 'source "$1"; install_uv' _ "$INSTALLER" 2>&1)"
    if [[ -s "$calls" ]]; then
        echo "curl called although uv is installed" >&2
        rm -rf "$tmp"
        return 1
    fi
    assert_contains "$output" "uv already installed" "skip message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_install_uv_downloads_then_runs_installer() {
    # The official URL is downloaded to a file, then that file is executed.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local curl_calls="$tmp/curl_calls"
    local bash_calls="$tmp/bash_calls"
    mkdir -p "$bin"
    cat > "$bin/curl" <<'EOF'
#!/bin/bash
# Record arguments, then create the target file as the downloaded installer.
echo "$@" >> "$CURL_CALLS_FILE"
out=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "-o" ]]; then
        out="$arg"
    fi
    prev="$arg"
done
printf '#!/usr/bin/env bash\nexit 0\n' > "$out"
exit 0
EOF
    chmod +x "$bin/curl"
    PATH="$bin:/usr/bin:/bin" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
        CURL_CALLS_FILE="$curl_calls" \
        bash -c 'source "$1"; install_uv' _ "$INSTALLER"
    if ! grep -q -- "-o $tmp/cache/uv-install.sh https://astral.sh/uv/install.sh" "$curl_calls"; then
        echo "curl not called with official URL and -o target" >&2
        cat "$curl_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q -- "--max-time 777" "$curl_calls"; then
        echo "curl not called with the configured --max-time" >&2
        cat "$curl_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q -- "--retry 13" "$curl_calls"; then
        echo "curl not called with the configured --retry" >&2
        cat "$curl_calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_install_uv_runs_installer_script() {
    # The downloaded installer must be executed: the curl mock creates a fake
    # uv binary, and install_uv must report it as installed afterwards.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local curl_calls="$tmp/curl_calls"
    mkdir -p "$bin"
    cat > "$bin/curl" <<'EOF'
#!/bin/bash
echo "$@" >> "$CURL_CALLS_FILE"
out=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "-o" ]]; then
        out="$arg"
    fi
    prev="$arg"
done
# Create a fake uv binary that the installer would have placed on PATH.
mkdir -p "$(dirname "$out")/bin"
printf '#!/bin/bash\nexit 0\n' > "$(dirname "$out")/bin/uv"
chmod +x "$(dirname "$out")/bin/uv"
# The installer script itself does nothing when executed.
printf '#!/bin/bash\nexit 0\n' > "$out"
exit 0
EOF
    chmod +x "$bin/curl"
    local output
    output="$(PATH="$bin:/usr/bin:/bin" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
        CURL_CALLS_FILE="$curl_calls" \
        bash -c 'source "$1"; install_uv' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "uv installed" "installer run confirmation" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_install_uv_adds_local_bin_to_path() {
    # After install, $HOME/.local/bin must be on PATH for later phases.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local curl_calls="$tmp/curl_calls"
    mkdir -p "$bin"
    cat > "$bin/curl" <<'EOF'
#!/bin/bash
echo "$@" >> "$CURL_CALLS_FILE"
out=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "-o" ]]; then
        out="$arg"
    fi
    prev="$arg"
done
# Create a fake uv binary so command -v finds it after install.
mkdir -p "$(dirname "$out")/bin"
printf '#!/bin/bash\nexit 0\n' > "$(dirname "$out")/bin/uv"
chmod +x "$(dirname "$out")/bin/uv"
printf '#!/bin/bash\nexit 0\n' > "$out"
exit 0
EOF
    chmod +x "$bin/curl"
    local output
    output="$(PATH="$bin:/usr/bin:/bin" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
        CURL_CALLS_FILE="$curl_calls" \
        bash -c 'source "$1"; install_uv; echo "PATH_MARKER:$PATH"' _ "$INSTALLER" 2>&1)"
    local path_line
    path_line="$(echo "$output" | grep PATH_MARKER || true)"
    if [[ "$path_line" != *"$HOME/.local/bin"* ]]; then
        echo ".local/bin not added to PATH" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_fetch_source_clones_when_dir_missing() {
    # When the source directory does not exist, git clone must run, not fetch.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/git_calls"
    mkdir -p "$bin"
    cat > "$bin/git" <<'EOF'
#!/bin/bash
echo "$@" >> "$GIT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/git"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" GIT_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; fetch_source' _ "$INSTALLER"
    if ! grep -q "^clone --depth 1 -b main https://github.com/Borodin-Atamanov/Pyntara.git $tmp/repo$" "$calls"; then
        echo "clone not called with expected arguments" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^fetch$' "$calls"; then
        echo "fetch must not run when cloning fresh" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_fetch_source_clones_empty_dir() {
    # An empty existing directory must be cloned, not fetched.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/git_calls"
    mkdir -p "$bin" "$tmp/repo"
    cat > "$bin/git" <<'EOF'
#!/bin/bash
echo "$@" >> "$GIT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/git"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" GIT_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; fetch_source' _ "$INSTALLER"
    if ! grep -q "^clone --depth 1 -b main " "$calls"; then
        echo "clone not called for empty directory" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^fetch$' "$calls"; then
        echo "fetch must not run for empty directory" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_fetch_source_reclones_broken_dir() {
    # A non-empty directory without .git is a broken clone and must be recreated.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/git_calls"
    local rm_calls="$tmp/rm_calls"
    mkdir -p "$bin" "$tmp/repo"
    echo "stale file" > "$tmp/repo/stale.txt"
    cat > "$bin/git" <<'EOF'
#!/bin/bash
echo "$@" >> "$GIT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/git"
    cat > "$bin/rm" <<'EOF'
#!/bin/bash
echo "$@" >> "$RM_CALLS_FILE"
/bin/rm -f "$@"
EOF
    chmod +x "$bin/rm"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" GIT_CALLS_FILE="$calls" \
        RM_CALLS_FILE="$rm_calls" PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; fetch_source' _ "$INSTALLER"
    if ! grep -q -- "-rf $tmp/repo" "$rm_calls"; then
        echo "broken clone not removed" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^clone --depth 1 -b main " "$calls"; then
        echo "clone not called after removing broken clone" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_fetch_source_reclones_corrupted_repo() {
    # A repo whose object store is corrupted makes fetch fail; fetch_source
    # must remove it and re-clone instead of failing the install.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/git_calls"
    local rm_calls="$tmp/rm_calls"
    mkdir -p "$bin" "$tmp/repo/.git"
    cat > "$bin/git" <<'EOF'
#!/bin/bash
echo "$@" >> "$GIT_CALLS_FILE"
if [[ "$1" == "-C" && "$3" == "fetch" ]]; then
    exit 128
fi
exit 0
EOF
    chmod +x "$bin/git"
    cat > "$bin/rm" <<'EOF'
#!/bin/bash
echo "$@" >> "$RM_CALLS_FILE"
/bin/rm -rf "$@"
EOF
    chmod +x "$bin/rm"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" GIT_CALLS_FILE="$calls" \
        RM_CALLS_FILE="$rm_calls" PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; fetch_source' _ "$INSTALLER"
    if ! grep -q -- "-rf $tmp/repo" "$rm_calls"; then
        echo "corrupted clone not removed" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^clone --depth 1 -b main " "$calls"; then
        echo "clone not called after removing corrupted clone" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_fetch_source_fetches_existing_repo() {
    # An existing repo with .git must fetch and reset, not re-clone.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/git_calls"
    mkdir -p "$bin" "$tmp/repo/.git"
    cat > "$bin/git" <<'EOF'
#!/bin/bash
echo "$@" >> "$GIT_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/git"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" GIT_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; fetch_source' _ "$INSTALLER"
    if ! grep -q -- "-C $tmp/repo fetch" "$calls"; then
        echo "fetch not called for existing repo" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q -- "-C $tmp/repo reset --hard origin/main" "$calls"; then
        echo "reset --hard origin/main not called" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^clone ' "$calls"; then
        echo "clone must not run for existing repo" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_setup_python_syncs_locked_when_lock_current() {
    # When uv.lock exists and uv lock --check succeeds, sync must run with --locked.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    : > "$tmp/repo/uv.lock"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; setup_python' _ "$INSTALLER"
    if ! grep -q '^lock --check$' "$calls"; then
        echo "uv lock --check not called" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q '^sync --locked$' "$calls"; then
        echo "uv sync --locked not called" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^sync$' "$calls"; then
        echo "plain sync must not run when lockfile is current" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_setup_python_syncs_without_locked_when_lock_missing() {
    # When uv.lock does not exist, sync must run immediately without --locked
    # and without a confusing lock --check error.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; setup_python' _ "$INSTALLER"
    if ! grep -q '^sync$' "$calls"; then
        echo "plain sync not called when lockfile is missing" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^lock --check$' "$calls"; then
        echo "lock --check must not run when lockfile is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^sync --locked$' "$calls"; then
        echo "sync --locked must not run when lockfile is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_setup_python_syncs_without_locked_when_lock_stale() {
    # When uv.lock exists but uv lock --check fails, sync must run without --locked.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    : > "$tmp/repo/uv.lock"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
if [[ "$1" == "lock" && "$2" == "--check" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$bin/uv"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; setup_python' _ "$INSTALLER"
    if ! grep -q '^sync$' "$calls"; then
        echo "plain sync not called when lockfile is stale" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q '^sync --locked$' "$calls"; then
        echo "sync --locked must not run when lockfile is stale" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_setup_python_fails_when_source_missing() {
    # When the source directory is missing, setup_python must fail without uv.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    local rc
    set +e
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/missing" \
        bash -c 'source "$1"; setup_python' _ "$INSTALLER" > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
        echo "setup_python must fail when source directory is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ -s "$calls" ]]; then
        echo "uv must not run when source directory is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_run_pyntara_launches_in_source_dir() {
    # run_pyntara must run uv pyntara from the source directory.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
pwd >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; run_pyntara' _ "$INSTALLER"
    if ! grep -q '^run pyntara$' "$calls"; then
        echo "uv run pyntara not called" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "^$tmp/repo$" "$calls"; then
        echo "pyntara not launched from source directory" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_run_pyntara_forwards_arguments() {
    # Arguments passed to run_pyntara must reach uv run pyntara.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; run_pyntara --help' _ "$INSTALLER"
    if ! grep -q '^run pyntara --help$' "$calls"; then
        echo "arguments not forwarded to uv run pyntara" >&2
        cat "$calls" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_run_pyntara_preserves_failing_exit_code() {
    # A failing pyntara must return its own exit code and log it.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 3
EOF
    chmod +x "$bin/uv"
    local rc
    set +e
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/repo" \
        bash -c 'source "$1"; run_pyntara' _ "$INSTALLER" > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -ne 3 ]]; then
        echo "expected exit code 3, got $rc" >&2
        rm -rf "$tmp"
        return 1
    fi
    if ! grep -q "Pyntara finished with exit code 3" "$logfile"; then
        echo "failure not logged with exit code 3" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_run_pyntara_fails_when_source_missing() {
    # When the source directory is missing, run_pyntara must fail without uv.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local calls="$tmp/uv_calls"
    mkdir -p "$bin"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    local rc
    set +e
    PATH="$bin:$PATH" PYNTARA_LOG_FILE="$logfile" UV_CALLS_FILE="$calls" \
        PYNTARA_SOURCE_DIR="$tmp/missing" \
        bash -c 'source "$1"; run_pyntara' _ "$INSTALLER" > "$tmp/out" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
        echo "run_pyntara must fail when source directory is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ -s "$calls" ]]; then
        echo "uv must not run when source directory is missing" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_uv_cache_dir_is_subdirectory_of_cache() {
    # The uv cache must be a subdirectory of the cache root, never the root
    # itself: the git clone lives inside the cache root, and uv refuses a
    # project directory that is inside its own cache directory.
    local tmp
    tmp="$(mktemp -d)"
    local output
    output="$(PYNTARA_CACHE_DIR="$tmp/cache" \
        bash -c 'source "$1"; echo "$UV_CACHE_DIR"' _ "$INSTALLER" 2>&1)"
    if [[ "$output" == "$tmp/cache" ]]; then
        echo "UV_CACHE_DIR must not equal the cache root" >&2
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$output" != "$tmp/cache"/* ]]; then
        echo "UV_CACHE_DIR must be inside the cache root" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_show_message_prints_text_and_logs_it() {
    # show_message prints the text to the terminal and to the log, then waits
    # for Enter or the message timeout (0 here so tests never block).
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(PYNTARA_LOG_FILE="$logfile" PYNTARA_MESSAGE_TIMEOUT=0 \
        bash -c 'source "$1"; show_message "hello message"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "hello message" "message text on terminal" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$(cat "$logfile")" "hello message" "message text in log" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_log_status_prints_without_consuming_input() {
    # log_status prints the text and the log line but must not block or read
    # from stdin: a read after it must still see the next keystroke. This is
    # the property that fixes the double-Enter bug after status messages.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(printf 'keystroke\n' | PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; log_status "status line"; read -r -t 1 next; echo "NEXT=$next"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "status line" "status text on terminal" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$(cat "$logfile")" "status line" "status text in log" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "NEXT=keystroke" "following read sees the keystroke" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_show_countdown_prints_message_and_countdown() {
    # show_countdown prints the message and logs it once; seconds 0 returns
    # immediately without reading any input.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(PYNTARA_LOG_FILE="$logfile" \
        bash -c 'source "$1"; show_countdown 0 "notice text"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "notice text" "notice on terminal" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$(cat "$logfile")" "notice text" "notice in log" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_prompt_vault_password_no_env_falls_back_to_default() {
    # Without PYNTARA_VAULT_PASSWORD the production vault cannot be opened:
    # the installer shows a fallback notice, exports the default vault
    # password and source, and never calls uv.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local uv_calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo/secrets"
    echo "fallback-password" > "$tmp/repo/secrets/default.password"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_PRODUCTION_VAULT="$tmp/repo/secrets/production.vault" \
        PYNTARA_DEFAULT_VAULT="$tmp/repo/secrets/default.vault" \
        PYNTARA_DEFAULT_PASSWORD_FILE="$tmp/repo/secrets/default.password" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_FALLBACK_NOTICE_TIMEOUT=0 \
        UV_CALLS_FILE="$uv_calls" \
        bash -c 'source "$1"; prompt_vault_password; echo "VAULT_SOURCE=$PYNTARA_VAULT_SOURCE"; echo "VAULT_PASSWORD=$PYNTARA_VAULT_PASSWORD"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "Falling back to default vault" "fallback notice" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_SOURCE=default" "default source export" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_PASSWORD=fallback-password" "default password export" || {
        rm -rf "$tmp"
        return 1
    }
    if [[ -s "$uv_calls" ]]; then
        echo "uv must not run when falling back to default vault" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_prompt_vault_password_missing_default_password_aborts() {
    # Without the default password file the fallback cannot work: the
    # installer must print a loud error and fail with a non-zero exit code.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    mkdir -p "$bin" "$tmp/repo/secrets"
    # default.password is missing on purpose.
    local rc
    set +e
    PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_PRODUCTION_VAULT="$tmp/repo/secrets/production.vault" \
        PYNTARA_DEFAULT_VAULT="$tmp/repo/secrets/default.vault" \
        PYNTARA_DEFAULT_PASSWORD_FILE="$tmp/repo/secrets/default.password" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_FALLBACK_NOTICE_TIMEOUT=0 \
        bash -c 'source "$1"; prompt_vault_password' _ "$INSTALLER" > "$tmp/out" 2>&1
    rc=$?
    set -e
    assert_equals "1" "$rc" "exit code on missing fallback password" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$(cat "$tmp/out")" "ERROR: default vault password file not found" "loud error message" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_detect_default_mode_uses_override() {
    # PYNTARA_DEFAULT_INSTALL_MODE must win over every other signal.
    local output
    output="$(PYNTARA_DEFAULT_INSTALL_MODE=minimal bash -c 'source "$1"; detect_default_mode' _ "$INSTALLER" 2>&1)"
    assert_equals "minimal" "$output" "override wins" || return 1
}

inst_detect_default_mode_desktop_when_session_vars() {
    # A desktop session variable means desktop.
    local output
    output="$(XDG_CURRENT_DESKTOP=KDE bash -c 'source "$1"; detect_default_mode' _ "$INSTALLER" 2>&1)"
    assert_equals "desktop" "$output" "XDG_CURRENT_DESKTOP wins" || return 1
}

inst_detect_default_mode_server_when_no_session() {
    # No session variables and no desktop process: server. pgrep is mocked to
    # report no match.
    local tmp
    tmp="$(mktemp -d)"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$bin/pgrep"
    local output
    output="$(env -u XDG_CURRENT_DESKTOP -u DESKTOP_SESSION PATH="$bin:$PATH" bash -c 'source "$1"; detect_default_mode' _ "$INSTALLER" 2>&1)"
    assert_equals "server" "$output" "headless means server" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_detect_default_mode_desktop_when_process() {
    # A desktop process means desktop even without session variables.
    local tmp
    tmp="$(mktemp -d)"
    local bin="$tmp/bin"
    mkdir -p "$bin"
    cat > "$bin/pgrep" <<'EOF'
#!/usr/bin/env bash
# Match plasmashell, reject everything else.
case "$*" in
    *plasmashell*) exit 0 ;;
    *) exit 1 ;;
esac
EOF
    chmod +x "$bin/pgrep"
    local output
    output="$(env -u XDG_CURRENT_DESKTOP -u DESKTOP_SESSION PATH="$bin:$PATH" bash -c 'source "$1"; detect_default_mode' _ "$INSTALLER" 2>&1)"
    assert_equals "desktop" "$output" "desktop process means desktop" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_prompt_install_mode_uses_environment() {
    # PYNTARA_INSTALL_MODE skips the screen and is logged.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(PYNTARA_INSTALL_MODE=minimal PYNTARA_LOG_FILE="$logfile" bash -c 'source "$1"; prompt_install_mode; echo "MODE=$PYNTARA_INSTALL_MODE"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "MODE=minimal" "env mode kept" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "Install mode from environment: minimal" "env mode logged" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_prompt_install_mode_exports_selected_mode() {
    # Without an env override the detected default is exported without any
    # interactive screen. PYNTARA_DEFAULT_INSTALL_MODE fixes the detection.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local output
    output="$(PYNTARA_DEFAULT_INSTALL_MODE=server PYNTARA_LOG_FILE="$logfile" bash -c 'source "$1"; prompt_install_mode; echo "MODE=$PYNTARA_INSTALL_MODE"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "MODE=server" "default mode exported" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "Install mode (default): server" "default mode logged" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_prompt_vault_password_uses_environment_with_source() {
    # PYNTARA_VAULT_PASSWORD together with PYNTARA_VAULT_SOURCE skips the
    # prompt and the vault check entirely: no uv call, no password prompt,
    # the given source is kept.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local uv_calls="$tmp/uv_calls"
    mkdir -p "$bin"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    cat > "$tmp/prompt_mock.sh" <<'EOF'
prompt_password_input() {
    echo "password prompt must not be called" >&2
    return 99
}
EOF
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_MESSAGE_TIMEOUT=0 \
        PYNTARA_VAULT_PASSWORD="correct-password" PYNTARA_VAULT_SOURCE="production" \
        UV_CALLS_FILE="$uv_calls" \
        bash -c 'source "$2"; source "$1"; prompt_vault_password; echo "VAULT_SOURCE=$PYNTARA_VAULT_SOURCE"; echo "VAULT_PASSWORD=$PYNTARA_VAULT_PASSWORD"' _ "$INSTALLER" "$tmp/prompt_mock.sh" 2>&1)"
    assert_contains "$output" "Vault password from environment, source: production" "env source message" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_SOURCE=production" "vault source kept" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_PASSWORD=correct-password" "vault password kept" || {
        rm -rf "$tmp"
        return 1
    }
    if [[ -s "$uv_calls" ]]; then
        echo "uv must not run when password comes from environment" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q "password prompt must not be called" "$output"; then
        echo "password prompt must be skipped in environment mode" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_prompt_vault_password_uses_environment_autodetect_production() {
    # PYNTARA_VAULT_PASSWORD without PYNTARA_VAULT_SOURCE auto-detects the
    # source by checking the password against production.vault via uv.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local uv_calls="$tmp/uv_calls"
    local uv_stdin="$tmp/uv_stdin"
    mkdir -p "$bin" "$tmp/repo/secrets"
    echo "fallback-password" > "$tmp/repo/secrets/default.password"
    : > "$tmp/repo/secrets/production.vault"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
cat > "$UV_STDIN_FILE"
exit "$UV_EXIT_CODE"
EOF
    chmod +x "$bin/uv"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_PRODUCTION_VAULT="$tmp/repo/secrets/production.vault" \
        PYNTARA_DEFAULT_VAULT="$tmp/repo/secrets/default.vault" \
        PYNTARA_DEFAULT_PASSWORD_FILE="$tmp/repo/secrets/default.password" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_MESSAGE_TIMEOUT=0 \
        UV_CALLS_FILE="$uv_calls" UV_STDIN_FILE="$uv_stdin" UV_EXIT_CODE=0 \
        PYNTARA_VAULT_PASSWORD="correct-password" \
        bash -c 'source "$1"; prompt_vault_password; echo "VAULT_SOURCE=$PYNTARA_VAULT_SOURCE"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "auto-detected source: production" "autodetect message" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_SOURCE=production" "autodetect production" || {
        rm -rf "$tmp"
        return 1
    }
    local stdin_content
    stdin_content="$(cat "$uv_stdin")"
    assert_equals "correct-password" "$stdin_content" "password delivered via stdin" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_prompt_vault_password_uses_environment_autodetect_default() {
    # PYNTARA_VAULT_PASSWORD matching default.password auto-detects default
    # as the source without any uv call: production.vault is absent.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local uv_calls="$tmp/uv_calls"
    mkdir -p "$bin" "$tmp/repo/secrets"
    echo "fallback-password" > "$tmp/repo/secrets/default.password"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
exit 0
EOF
    chmod +x "$bin/uv"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_PRODUCTION_VAULT="$tmp/repo/secrets/production.vault" \
        PYNTARA_DEFAULT_VAULT="$tmp/repo/secrets/default.vault" \
        PYNTARA_DEFAULT_PASSWORD_FILE="$tmp/repo/secrets/default.password" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_MESSAGE_TIMEOUT=0 \
        UV_CALLS_FILE="$uv_calls" \
        PYNTARA_VAULT_PASSWORD="fallback-password" \
        bash -c 'source "$1"; prompt_vault_password; echo "VAULT_SOURCE=$PYNTARA_VAULT_SOURCE"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "auto-detected source: default" "autodetect message" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_SOURCE=default" "autodetect default" || {
        rm -rf "$tmp"
        return 1
    }
    if [[ -s "$uv_calls" ]]; then
        echo "uv must not run when default password matches" >&2
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

inst_prompt_vault_password_uses_environment_wrong_password_falls_back_to_default() {
    # A PYNTARA_VAULT_PASSWORD matching no vault is not fatal: the installer
    # shows a countdown notice and falls back to the default vault, exactly
    # like the no-password path. The production vault check still runs once
    # with the candidate password.
    local tmp
    tmp="$(mktemp -d)"
    local logfile="$tmp/install.log"
    local bin="$tmp/bin"
    local uv_calls="$tmp/uv_calls"
    local uv_stdin="$tmp/uv_stdin"
    mkdir -p "$bin" "$tmp/repo/secrets"
    echo "fallback-password" > "$tmp/repo/secrets/default.password"
    : > "$tmp/repo/secrets/production.vault"
    cat > "$bin/uv" <<'EOF'
#!/bin/bash
echo "$@" >> "$UV_CALLS_FILE"
cat > "$UV_STDIN_FILE"
exit "$UV_EXIT_CODE"
EOF
    chmod +x "$bin/uv"
    local output
    output="$(PATH="$bin:$PATH" PYNTARA_SOURCE_DIR="$tmp/repo" \
        PYNTARA_PRODUCTION_VAULT="$tmp/repo/secrets/production.vault" \
        PYNTARA_DEFAULT_VAULT="$tmp/repo/secrets/default.vault" \
        PYNTARA_DEFAULT_PASSWORD_FILE="$tmp/repo/secrets/default.password" \
        PYNTARA_LOG_FILE="$logfile" PYNTARA_MESSAGE_TIMEOUT=0 \
        PYNTARA_FALLBACK_NOTICE_TIMEOUT=0 \
        UV_CALLS_FILE="$uv_calls" UV_STDIN_FILE="$uv_stdin" UV_EXIT_CODE=1 \
        PYNTARA_VAULT_PASSWORD="wrong" \
        bash -c 'source "$1"; prompt_vault_password; echo "VAULT_SOURCE=$PYNTARA_VAULT_SOURCE"; echo "VAULT_PASSWORD=$PYNTARA_VAULT_PASSWORD"' _ "$INSTALLER" 2>&1)"
    assert_contains "$output" "Falling back to default vault" "fallback notice" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_SOURCE=default" "default source export" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$output" "VAULT_PASSWORD=fallback-password" "default password export" || {
        rm -rf "$tmp"
        return 1
    }
    local uv_call_count
    uv_call_count="$(wc -l < "$uv_calls")"
    assert_equals "1" "$uv_call_count" "one uv call for the production check" || {
        rm -rf "$tmp"
        return 1
    }
    local stdin_content
    stdin_content="$(cat "$uv_stdin")"
    assert_equals "wrong" "$stdin_content" "candidate password delivered via stdin" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

run_test inst_check_root_rejects_non_root_with_error
run_test inst_check_root_accepts_root_with_success_message
run_test inst_ensure_fhs_dirs_creates_all_three_directories
run_test inst_ensure_fhs_dirs_is_idempotent_on_second_run
run_test inst_log_writes_timestamped_line_to_terminal_and_file
run_test inst_log_appends_lines_instead_of_overwriting
run_test inst_log_file_defaults_inside_log_dir
run_test inst_log_forwards_to_system_journal
run_test inst_log_empty_identifier_disables_journal
run_test inst_log_survives_failing_systemd_cat
run_test inst_run_logged_streams_both_streams_and_preserves_exit_code
run_test inst_run_timed_logs_duration_and_exit_code
run_test inst_run_timed_preserves_failing_exit_code
run_test inst_apt_install_updates_before_install
run_test inst_apt_install_skips_update_when_flag_set
run_test inst_apt_install_warns_and_continues_when_update_fails
run_test inst_apt_install_passes_package_list
run_test inst_install_dependencies_skips_when_all_present
run_test inst_install_dependencies_installs_missing_packages
run_test inst_install_dependencies_reports_all_installed
run_test inst_install_uv_skips_when_already_installed
run_test inst_install_uv_downloads_then_runs_installer
run_test inst_install_uv_runs_installer_script
run_test inst_install_uv_adds_local_bin_to_path
run_test inst_uv_cache_dir_is_subdirectory_of_cache
run_test inst_fetch_source_clones_when_dir_missing
run_test inst_fetch_source_clones_empty_dir
run_test inst_fetch_source_reclones_broken_dir
run_test inst_fetch_source_reclones_corrupted_repo
run_test inst_fetch_source_fetches_existing_repo
run_test inst_setup_python_syncs_locked_when_lock_current
run_test inst_setup_python_syncs_without_locked_when_lock_missing
run_test inst_setup_python_syncs_without_locked_when_lock_stale
run_test inst_setup_python_fails_when_source_missing
run_test inst_run_pyntara_launches_in_source_dir
run_test inst_run_pyntara_forwards_arguments
run_test inst_run_pyntara_preserves_failing_exit_code
run_test inst_run_pyntara_fails_when_source_missing
run_test inst_show_message_prints_text_and_logs_it
run_test inst_show_countdown_prints_message_and_countdown
run_test inst_log_status_prints_without_consuming_input
run_test inst_prompt_vault_password_no_env_falls_back_to_default
run_test inst_prompt_vault_password_missing_default_password_aborts
run_test inst_detect_default_mode_uses_override
run_test inst_detect_default_mode_desktop_when_session_vars
run_test inst_detect_default_mode_server_when_no_session
run_test inst_detect_default_mode_desktop_when_process
run_test inst_prompt_install_mode_uses_environment
run_test inst_prompt_install_mode_exports_selected_mode
run_test inst_prompt_vault_password_uses_environment_with_source
run_test inst_prompt_vault_password_uses_environment_autodetect_production
run_test inst_prompt_vault_password_uses_environment_autodetect_default
run_test inst_prompt_vault_password_uses_environment_wrong_password_falls_back_to_default
run_test inst_main_calls_root_then_dirs_then_log_in_order

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
