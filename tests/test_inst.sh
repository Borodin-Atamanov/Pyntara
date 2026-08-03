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
    # main must call check_root, ensure_fhs_dirs, log, install_dependencies,
    # install_uv, then fetch_source. Mocks are declared before source so the
    # guard keeps them.
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
        source "$1"
        main
    ' _ "$INSTALLER" "$flags"
    local expected
    expected="$(printf 'check_root\nensure_fhs_dirs\nlog\ninstall_dependencies\ninstall_uv\nfetch_source')"
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
#!/bin/bash
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
#!/bin/bash
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
# Simulate a missing dialog package.
if [[ "$1" == "-s" && "$2" == "dialog" ]]; then
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
    if ! grep -q '^install -y dialog$' "$calls"; then
        echo "missing package dialog not installed" >&2
        rm -rf "$tmp"
        return 1
    fi
    if grep -q 'python3' "$calls"; then
        echo "present package python3 installed unnecessarily" >&2
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
    output="$(PATH="$bin:$PATH" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
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
    PATH="$bin:$PATH" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
        CURL_CALLS_FILE="$curl_calls" \
        bash -c 'source "$1"; install_uv' _ "$INSTALLER"
    if ! grep -q -- "-o $tmp/cache/uv-install.sh https://astral.sh/uv/install.sh" "$curl_calls"; then
        echo "curl not called with official URL and -o target" >&2
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
    output="$(PATH="$bin:$PATH" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
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
    output="$(PATH="$bin:$PATH" PYNTARA_CACHE_DIR="$tmp/cache" PYNTARA_LOG_FILE="$logfile" \
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
run_test inst_install_dependencies_skips_when_all_present
run_test inst_install_dependencies_installs_missing_packages
run_test inst_install_dependencies_reports_all_installed
run_test inst_install_uv_skips_when_already_installed
run_test inst_install_uv_downloads_then_runs_installer
run_test inst_install_uv_runs_installer_script
run_test inst_install_uv_adds_local_bin_to_path
run_test inst_fetch_source_clones_when_dir_missing
run_test inst_fetch_source_clones_empty_dir
run_test inst_fetch_source_reclones_broken_dir
run_test inst_fetch_source_fetches_existing_repo
run_test inst_main_calls_root_then_dirs_then_log_in_order

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
