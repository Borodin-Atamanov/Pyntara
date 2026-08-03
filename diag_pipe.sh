#!/usr/bin/env bash
# Diagnostic: test dialog under sudo with piped stdin
echo "=== Diagnostic Start ==="
echo "PID: $$"
echo "PPID: ${PPID}"
echo "EUID: ${EUID}"
echo "stdin isatty: $([[ -t 0 ]] && echo YES || echo NO)"
echo "stdout isatty: $([[ -t 1 ]] && echo YES || echo NO)"
echo "stderr isatty: $([[ -t 2 ]] && echo YES || echo NO)"
echo "tty: $(tty 2>&1)"
echo "/dev/tty: $(ls -la /dev/tty 2>&1)"

echo ""
echo "=== Attempting exec </dev/tty ==="
if exec </dev/tty 2>/tmp/diag_exec_stderr.log; then
    echo "exec OK"
else
    echo "exec FAILED: $(cat /tmp/diag_exec_stderr.log)"
fi
echo "stdin isatty after exec: $([[ -t 0 ]] && echo YES || echo NO)"

echo ""
echo "=== Running dialog --stdout --msgbox ==="
dialog --stdout --msgbox "Can you see this dialog window?" 8 50 2>/tmp/diag_dialog_stderr.log
DIALOG_RC=$?
echo "dialog exit code: ${DIALOG_RC}"
echo "dialog stderr:"
cat /tmp/diag_dialog_stderr.log
echo "=== Diagnostic End ==="
