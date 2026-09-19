#!/usr/bin/env bash
# Unit tests for the launcher pyntara.sh.
# Run with: bash tests/test_launcher.sh
# Every test runs in its own subshell through run_test, so the values the
# launcher sets while it is sourced never leak between tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$REPO_ROOT/pyntara.sh"
DEFAULT_PASSWORD_FILE="$REPO_ROOT/secrets/default.password"

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

assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    local detail="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "expected output to miss [$needle]: $detail" >&2
        return 1
    fi
}

assert_unset() {
    local variable_name="$1"
    local detail="$2"
    if declare -p "$variable_name" &>/dev/null; then
        echo "expected [$variable_name] to be unset: $detail" >&2
        return 1
    fi
}

# Load the launcher the way a run does: the file is sourced, so its own
# parameter block and the clearing of the inherited environment both run.
source_launcher() {
    # shellcheck disable=SC1090
    source "$LAUNCHER"
}

# Point the launcher log at a temporary directory and disable journal
# forwarding, so a test never writes the real log or the system journal.
use_test_log() {
    local log_dir="$1"
    PYNTARA_LOG_DIR="$log_dir"
    PYNTARA_LOG_FILE="$log_dir/install.log"
    PYNTARA_JOURNAL_IDENTIFIER=""
}

test_launcher_syntax_is_valid() {
    bash -n "$LAUNCHER"
}

test_launcher_takes_no_interactive_input() {
    local content
    content="$(cat "$LAUNCHER")"
    assert_not_contains "$content" "read -s" "the launcher never reads a password from the terminal" || return 1
    assert_not_contains "$content" "read -r -s" "the launcher never reads a password from the terminal" || return 1
    assert_not_contains "$content" "read -p" "the launcher never prompts on the terminal" || return 1
    assert_not_contains "$content" "/dev/tty" "the launcher never opens the controlling terminal" || return 1
}

test_launcher_keeps_the_password_out_of_a_trace() {
    local content
    content="$(cat "$LAUNCHER")"
    assert_not_contains "$content" "set -x" "a shell trace would print the password" || return 1
}

test_launcher_carries_no_version_line() {
    local content
    content="$(cat "$LAUNCHER")"
    assert_not_contains "$content" "PYNTARA_VERSION" "the launcher is not a version carrier" || return 1
}

test_launcher_ships_the_default_vault_password() {
    local expected
    expected="$(head -n 1 "$DEFAULT_PASSWORD_FILE")"
    source_launcher
    assert_equals "$expected" "$PYNTARA_VAULT_PASSWORD" "the run starts from the published default vault password" || return 1
}

test_launcher_holds_no_second_copy_of_the_password() {
    # A second copy is what let a wrongly replaced line fall back to the
    # default vault without a word (2026-09-19). The launcher holds one
    # password line and hands its value to the installer, which resolves the
    # vault that password opens.
    local content
    content="$(cat "$LAUNCHER")"
    assert_not_contains "$content" "PYNTARA_VAULT_PASSWORD_DEFAULT" "one password line only" || return 1
}

test_launcher_never_names_the_production_password_file() {
    local content
    content="$(cat "$LAUNCHER")"
    assert_not_contains "$content" "production.password" "the production password file is never read by the launcher" || return 1
    assert_not_contains "$content" "secrets/production" "a production secret file is never referenced by the launcher" || return 1
}

test_launcher_hands_its_password_to_the_installer() {
    source_launcher
    use_test_log "$(mktemp -d)"
    resolve_vault_password >/dev/null
    assert_equals "$(head -n 1 "$DEFAULT_PASSWORD_FILE")" "$PYNTARA_VAULT_PASSWORD" "the password of the file reaches the installer, which resolves the vault it opens" || return 1
}

test_launcher_reports_a_missing_password() {
    source_launcher
    use_test_log "$(mktemp -d)"
    unset PYNTARA_VAULT_PASSWORD
    resolve_vault_password >/dev/null
    assert_unset PYNTARA_VAULT_PASSWORD "an empty launcher passes no password, so the installer warns and waits" || return 1
}

test_launcher_replaced_password_is_passed_through() {
    source_launcher
    use_test_log "$(mktemp -d)"
    PYNTARA_VAULT_PASSWORD="a-replaced-password"
    resolve_vault_password >/dev/null
    assert_equals "a-replaced-password" "$PYNTARA_VAULT_PASSWORD" "a replaced password reaches the installer, which resolves the vault it opens" || return 1
}

test_launcher_clears_the_inherited_environment() {
    export PYNTARA_TASKS="task-from-caller"
    export PYNTARA_INSTALL_MODE="server"
    export PYNTARA_REPO_BRANCH="branch-from-caller"
    export PYNTARA_VAULT_PASSWORD="password-from-caller"
    source_launcher
    assert_unset PYNTARA_TASKS "an inherited task list never reaches the installer" || return 1
    assert_unset PYNTARA_INSTALL_MODE "an inherited install mode never reaches the installer" || return 1
    assert_equals "main" "$PYNTARA_REPO_BRANCH" "the branch of the run comes from the file" || return 1
    assert_equals "$(head -n 1 "$DEFAULT_PASSWORD_FILE")" "$PYNTARA_VAULT_PASSWORD" "an inherited password is replaced by the value of the file" || return 1
}

test_launcher_installer_receives_only_the_file_values() {
    local tmp dump stub
    tmp="$(mktemp -d)"
    dump="$tmp/environment.txt"
    stub="$tmp/installer.sh"
    printf '#!/usr/bin/env bash\nprintenv > %s\n' "$dump" > "$stub"
    export PYNTARA_TASKS="task-from-caller"
    export PYNTARA_VAULT_PASSWORD="password-from-caller"
    source_launcher
    use_test_log "$tmp"
    INSTALLER_PATH="$stub"
    resolve_vault_password >/dev/null
    export_run_parameters
    run_installer >/dev/null
    local child_environment
    child_environment="$(cat "$dump")"
    assert_contains "$child_environment" "PYNTARA_REPO_BRANCH=main" "the installer reads the branch of the file" || return 1
    assert_contains "$child_environment" "PYNTARA_LOG_FILE=$PYNTARA_LOG_FILE" "both halves agree on one log file" || return 1
    assert_not_contains "$child_environment" "PYNTARA_TASKS=" "an inherited task list never reaches the installer" || return 1
    assert_contains "$child_environment" "PYNTARA_VAULT_PASSWORD=$(head -n 1 "$DEFAULT_PASSWORD_FILE")" "the password of the file reaches the installer" || return 1
}

test_launcher_download_follows_the_branch() {
    local tmp recorded
    tmp="$(mktemp -d)"
    recorded="$tmp/curl-arguments.txt"
    source_launcher
    use_test_log "$tmp"
    INSTALLER_PATH="$tmp/inst.sh"
    curl() {
        printf '%s\n' "$*" >> "$recorded"
        return 0
    }
    download_installer >/dev/null
    local first_call
    first_call="$(cat "$recorded")"
    assert_contains "$first_call" "$INSTALLER_URL_BASE/main/inst.sh" "the installer comes from the configured branch" || return 1
    assert_contains "$first_call" "--output $INSTALLER_PATH" "the installer goes into the configured path" || return 1
    PYNTARA_REPO_BRANCH="feature-branch"
    : > "$recorded"
    download_installer >/dev/null
    assert_contains "$(cat "$recorded")" "$INSTALLER_URL_BASE/feature-branch/inst.sh" "the branch value selects the downloaded installer" || return 1
}

test_launcher_reports_a_failed_download() {
    local tmp
    tmp="$(mktemp -d)"
    source_launcher
    use_test_log "$tmp"
    INSTALLER_PATH="$tmp/inst.sh"
    curl() {
        return 22
    }
    local exit_code=0
    if download_installer >/dev/null; then
        exit_code=0
    else
        exit_code=$?
    fi
    assert_equals "22" "$exit_code" "a failed download is reported with the code of curl" || return 1
    assert_contains "$(cat "$PYNTARA_LOG_FILE")" "installer download failed" "the log names the failed download" || return 1
}

test_launcher_returns_the_installer_exit_code() {
    local tmp stub
    tmp="$(mktemp -d)"
    stub="$tmp/installer.sh"
    printf '#!/usr/bin/env bash\nexit 7\n' > "$stub"
    source_launcher
    use_test_log "$tmp"
    INSTALLER_PATH="$stub"
    local exit_code=0
    if run_installer >/dev/null; then
        exit_code=0
    else
        exit_code=$?
    fi
    assert_equals "7" "$exit_code" "the installer exit code travels back to the caller" || return 1
    assert_contains "$(cat "$PYNTARA_LOG_FILE")" "exit code 7" "the log names the installer exit code" || return 1
}

test_launcher_logs_a_timestamped_line() {
    local tmp
    tmp="$(mktemp -d)"
    source_launcher
    use_test_log "$tmp"
    launcher_log "probe message"
    local logged
    logged="$(cat "$PYNTARA_LOG_FILE")"
    assert_contains "$logged" "probe message" "the message reaches the log file" || return 1
    if [[ ! "$logged" =~ ^\[[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}\]\ probe\ message$ ]]; then
        echo "expected a timestamped line in the log, got [$logged]" >&2
        return 1
    fi
}

test_launcher_log_path_matches_the_installer_default() {
    source_launcher
    assert_equals "/var/log/pyntara/install.log" "$PYNTARA_LOG_FILE" "the launcher and the installer share one log path" || return 1
}

test_launcher_download_path_is_shared_memory() {
    source_launcher
    assert_equals "/dev/shm" "$(dirname "$INSTALLER_PATH")" "the downloaded installer never reaches the disk" || return 1
}

test_launcher_refuses_an_unprivileged_start() {
    if [[ "$EUID" -eq 0 ]]; then
        echo "SKIP: the test needs an unprivileged run"
        return 0
    fi
    local output exit_code=0
    if output="$(bash "$LAUNCHER" 2>&1)"; then
        exit_code=0
    else
        exit_code=$?
    fi
    assert_equals "1" "$exit_code" "an unprivileged start stops with an error" || return 1
    assert_contains "$output" "must run as root" "the error names the missing privilege" || return 1
    assert_contains "$output" "sudo bash" "the error shows the command to use" || return 1
}

run_test test_launcher_syntax_is_valid
run_test test_launcher_takes_no_interactive_input
run_test test_launcher_keeps_the_password_out_of_a_trace
run_test test_launcher_carries_no_version_line
run_test test_launcher_ships_the_default_vault_password
run_test test_launcher_holds_no_second_copy_of_the_password
run_test test_launcher_never_names_the_production_password_file
run_test test_launcher_hands_its_password_to_the_installer
run_test test_launcher_reports_a_missing_password
run_test test_launcher_replaced_password_is_passed_through
run_test test_launcher_clears_the_inherited_environment
run_test test_launcher_installer_receives_only_the_file_values
run_test test_launcher_download_follows_the_branch
run_test test_launcher_reports_a_failed_download
run_test test_launcher_returns_the_installer_exit_code
run_test test_launcher_logs_a_timestamped_line
run_test test_launcher_log_path_matches_the_installer_default
run_test test_launcher_download_path_is_shared_memory
run_test test_launcher_refuses_an_unprivileged_start

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
