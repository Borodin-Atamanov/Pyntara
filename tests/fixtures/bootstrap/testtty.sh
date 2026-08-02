#!/usr/bin/env bash
set -euo pipefail

run_testtty() {
  local py_bin="python3"
  if ! command -v "${py_bin}" >/dev/null 2>&1; then
    py_bin="python"
  fi

  local cli_stdin_path="${PYNTARA_CLI_STDIN_PATH:-/dev/tty}"
  local restore_stdin=0

  if [[ "${cli_stdin_path}" == "/dev/tty" ]]; then
    if [[ -t 0 || -e /dev/tty ]]; then
      exec 9<&0
      restore_stdin=1
      if exec </dev/tty; then
        printf 'TESTTTY stdin source: /dev/tty\n'
      else
        exec 0<&9
        exec 9<&-
        restore_stdin=0
        cli_stdin_path="/dev/stdin"
        printf 'TESTTTY fallback stdin source: /dev/stdin\n'
      fi
    else
      cli_stdin_path="/dev/stdin"
      printf 'TESTTTY fallback stdin source: /dev/stdin\n'
    fi
  else
    printf 'TESTTTY stdin source: %s\n' "${cli_stdin_path}"
  fi

  if exec 3<"${cli_stdin_path}"; then
    local py_code
    py_code="$(cat <<'PY'
import os
import select
import sys
import termios
import time
import tty


def render(selected: bool) -> None:
    checkbox = "[x]" if selected else "[ ]"
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.write(f"> {checkbox} demo-task")
    sys.stdout.flush()


fd = sys.stdin.fileno()
if not os.isatty(fd):
    print("TESTTTY_NO_TTY")
    raise SystemExit(1)

selected = False
print("TESTTTY_UI_READY")
print("TESTTTY_KEYS: SPACE toggles, ENTER confirms")
old_attrs = termios.tcgetattr(fd)
try:
    tty.setcbreak(fd)
    render(selected)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        payload = os.read(fd, 8)
        if not payload:
            continue
        should_finish = False
        for byte in payload:
            if byte in (0x0D, 0x0A):
                should_finish = True
                continue
            if byte == 0x20:
                selected = not selected
                render(selected)
        if should_finish:
            break
    print()
    print(f"TESTTTY_DONE selected={int(selected)}")
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
PY
    )"
    "${py_bin}" -c "${py_code}" <&3
    local status="$?"
    exec 3<&-
    if [[ "${restore_stdin}" -eq 1 ]]; then
      exec 0<&9
      exec 9<&-
    fi
    return "${status}"
  fi

  printf 'TESTTTY cannot open stdin source: %s\n' "${cli_stdin_path}" >&2
  if [[ "${restore_stdin}" -eq 1 ]]; then
    exec 0<&9
    exec 9<&-
  fi
  return 1
}

run_testtty
