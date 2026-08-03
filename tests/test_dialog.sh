#!/usr/bin/env bash
# Isolated experiment: does dialog --checklist work from under root, and from
# a non-root user, when stdin is not a terminal? dialog needs a real tty, so
# each invocation is wrapped in script(1), which allocates a pseudo-tty. The
# checked items are captured through --output-fd 3 into a file.
# Run as root:   sudo bash tests/test_dialog.sh
# Run as user:   bash tests/test_dialog.sh
# Main files (inst.sh, test_inst.sh) are intentionally not touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

require_dialog() {
    # Both dialog and script(1) must be present; without them a dialog test
    # would fail for the wrong reason.
    if ! command -v dialog >/dev/null 2>&1 || ! command -v script >/dev/null 2>&1; then
        echo "SKIP: dialog or script(1) unavailable"
        return 1
    fi
    return 0
}

dialog_checklist_cmd() {
    # Print the dialog --checklist command that writes its checked items
    # through --output-fd 3 into the given file. Remaining arguments are
    # dialog arguments, each shell-quoted for the script -qec string.
    local result_file="$1"
    shift
    local cmd="dialog --output-fd 3"
    local arg
    for arg in "$@"; do
        cmd+=" $(printf '%q' "$arg")"
    done
    cmd+=" 3>$(printf '%q' "$result_file")"
    echo "$cmd"
}

# Two checklist items: 1 one (off), 2 two (on). Enter accepts, space toggles,
# ESC cancels with exit code 255, --timeout accepts the defaults.
DIALOG_ITEMS=(--title t --checklist pick 0 0 0 1 one off 2 two on)

inst_dialog_checklist_enter_confirms_as_root() {
    # Under root, Enter must accept the dialog and report the pre-checked
    # item through --output-fd 3.
    require_dialog || return 0
    if [[ "$EUID" -ne 0 ]]; then
        echo "SKIP: requires root"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    local res="$tmp/res"
    : > "$res"
    local cmd
    cmd="$(dialog_checklist_cmd "$res" "${DIALOG_ITEMS[@]}")"
    local rc
    set +e
    ( sleep 1; printf '\n' ) | script -qec "$cmd" /dev/null >/dev/null 2>&1
    rc=$?
    set -e
    assert_equals "0" "$rc" "dialog exit code" || {
        rm -rf "$tmp"
        return 1
    }
    assert_equals "2" "$(cat "$res")" "pre-checked item reported" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_dialog_checklist_space_toggles_as_root() {
    # Space toggles the focused checkbox: the first item turns on, so the
    # result must contain both items.
    require_dialog || return 0
    if [[ "$EUID" -ne 0 ]]; then
        echo "SKIP: requires root"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    local res="$tmp/res"
    : > "$res"
    local cmd
    cmd="$(dialog_checklist_cmd "$res" "${DIALOG_ITEMS[@]}")"
    local rc
    set +e
    ( sleep 1; printf ' \n' ) | script -qec "$cmd" /dev/null >/dev/null 2>&1
    rc=$?
    set -e
    assert_equals "0" "$rc" "dialog exit code" || {
        rm -rf "$tmp"
        return 1
    }
    assert_equals "1 2" "$(cat "$res")" "space toggled first item" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_dialog_checklist_cancel_as_root() {
    # ESC must cancel the dialog: exit code 255 and no selection written.
    require_dialog || return 0
    if [[ "$EUID" -ne 0 ]]; then
        echo "SKIP: requires root"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    local res="$tmp/res"
    : > "$res"
    local cmd
    cmd="$(dialog_checklist_cmd "$res" "${DIALOG_ITEMS[@]}")"
    local rc
    set +e
    ( sleep 1; printf '\033' ) | script -qec "$cmd" /dev/null >/dev/null 2>&1
    rc=$?
    set -e
    assert_equals "255" "$rc" "dialog cancel exit code" || {
        rm -rf "$tmp"
        return 1
    }
    assert_equals "" "$(cat "$res")" "no selection on cancel" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_dialog_checklist_timeout_as_root() {
    # --timeout without input must accept the defaults and exit 0.
    require_dialog || return 0
    if [[ "$EUID" -ne 0 ]]; then
        echo "SKIP: requires root"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    local res="$tmp/res"
    : > "$res"
    local cmd
    cmd="$(dialog_checklist_cmd "$res" --timeout 1 "${DIALOG_ITEMS[@]}")"
    local rc
    set +e
    script -qec "$cmd" /dev/null >/dev/null 2>&1 </dev/null
    rc=$?
    set -e
    assert_equals "0" "$rc" "dialog timeout exit code" || {
        rm -rf "$tmp"
        return 1
    }
    assert_equals "2" "$(cat "$res")" "defaults kept on timeout" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

inst_dialog_checklist_works_as_nonroot() {
    # The same dialog must work for a non-root user too. From a root suite
    # the command runs through setpriv as uid/gid 65534; from a non-root
    # suite it runs directly.
    require_dialog || return 0
    local tmp
    tmp="$(mktemp -d)"
    # The nobody user needs write access to the result directory and file.
    chmod 777 "$tmp"
    local res="$tmp/res"
    : > "$res"
    chmod 666 "$res"
    local cmd
    cmd="$(dialog_checklist_cmd "$res" "${DIALOG_ITEMS[@]}")"
    local out
    local rc
    if [[ "$EUID" -eq 0 ]]; then
        if ! command -v setpriv >/dev/null 2>&1; then
            echo "SKIP: setpriv unavailable to drop privileges"
            rm -rf "$tmp"
            return 0
        fi
        set +e
        out="$(setpriv --reuid=65534 --regid=65534 --clear-groups \
            env HOME="$tmp" TERM=xterm DIALOG_CMD="$cmd" bash -c \
            '( sleep 1; printf "\n" ) | script -qec "$DIALOG_CMD" /dev/null >/dev/null 2>&1; echo "rc=$?"; cat "$1"' \
            _ "$res" 2>&1)"
        rc=$?
        set -e
    else
        set +e
        out="$(DIALOG_CMD="$cmd" bash -c \
            '( sleep 1; printf "\n" ) | script -qec "$DIALOG_CMD" /dev/null >/dev/null 2>&1; echo "rc=$?"; cat "$1"' \
            _ "$res" 2>&1)"
        rc=$?
        set -e
    fi
    assert_contains "$out" "rc=0" "dialog exit code as non-root" || {
        rm -rf "$tmp"
        return 1
    }
    assert_contains "$out" "2" "pre-checked item reported as non-root" || {
        rm -rf "$tmp"
        return 1
    }
    rm -rf "$tmp"
}

run_test inst_dialog_checklist_enter_confirms_as_root
run_test inst_dialog_checklist_space_toggles_as_root
run_test inst_dialog_checklist_cancel_as_root
run_test inst_dialog_checklist_timeout_as_root
run_test inst_dialog_checklist_works_as_nonroot

echo "Tests passed: $pass_count, failed: $fail_count, skipped: $skip_count"
