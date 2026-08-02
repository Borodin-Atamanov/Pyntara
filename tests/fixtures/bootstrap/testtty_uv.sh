#!/usr/bin/env bash
set -euo pipefail

run_testtty_uv() {
  local cli_stdin_path="${PYNTARA_CLI_STDIN_PATH:-/dev/tty}"
  local restore_stdin=0
  local project_dir="${PYNTARA_TESTTTY_UV_PROJECT_DIR:-$PWD}"
  local uv_bin="uv"
  local candidate=""

  cd "${project_dir}"

  for candidate in \
    "${project_dir}/.venv/bin/uv" \
    "${project_dir}/.tmp-uv-bin/uv" \
    "${HOME:-}/.local/bin/uv" \
    "/usr/local/bin/uv" \
    "$(command -v uv 2>/dev/null || true)"
  do
    if [[ -n "${candidate}" && -x "${candidate}" ]]; then
      uv_bin="${candidate}"
      break
    fi
  done

  if [[ "${uv_bin}" == "uv" ]] && ! command -v "${uv_bin}" >/dev/null 2>&1; then
    printf 'TESTTTY_UV missing uv executable\n' >&2
    return 1
  fi

  if [[ "${cli_stdin_path}" == "/dev/tty" ]]; then
    if [[ -t 0 || -e /dev/tty ]]; then
      exec 9<&0
      restore_stdin=1
      if exec </dev/tty; then
        printf 'TESTTTY_UV stdin source: /dev/tty\n'
      else
        exec 0<&9
        exec 9<&-
        restore_stdin=0
        cli_stdin_path="/dev/stdin"
        printf 'TESTTTY_UV fallback stdin source: /dev/stdin\n'
      fi
    else
      cli_stdin_path="/dev/stdin"
      printf 'TESTTTY_UV fallback stdin source: /dev/stdin\n'
    fi
  else
    printf 'TESTTTY_UV stdin source: %s\n' "${cli_stdin_path}"
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
    sys.stdout.write(f"> {checkbox} uv-demo-task")
    sys.stdout.flush()


fd = sys.stdin.fileno()
if not os.isatty(fd):
    print("TESTTTY_UV_NO_TTY")
    raise SystemExit(1)

selected = False
print("TESTTTY_UV_READY")
print("TESTTTY_UV_KEYS: SPACE toggles, ENTER confirms")
old_attrs = termios.tcgetattr(fd)
try:
    tty.setcbreak(fd)
    render(selected)
    deadline = time.monotonic() + 2.5
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
    print(f"TESTTTY_UV_DONE selected={int(selected)}")
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
PY
    )"
    UV_PROJECT_ENVIRONMENT=".venv" "${uv_bin}" run --no-sync python -c "${py_code}" <&3
    local status="$?"
    exec 3<&-
    if [[ "${restore_stdin}" -eq 1 ]]; then
      exec 0<&9
      exec 9<&-
    fi
    return "${status}"
  fi

  printf 'TESTTTY_UV cannot open stdin source: %s\n' "${cli_stdin_path}" >&2
  if [[ "${restore_stdin}" -eq 1 ]]; then
    exec 0<&9
    exec 9<&-
  fi
  return 1
}

run_testtty_uv