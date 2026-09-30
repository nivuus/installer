#!/bin/bash
# Tests for console/host/vm-control.sh against a fake systemctl / logger.
#
# The script is the per-connection handler of nivuus-vm-control.socket: one
# request line on stdin, one reply line on stdout. The verb list is an
# allow-list and no verb takes an argument.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/../host/vm-control.sh"

PASS=0
FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

# $1 = exit code of the fake systemctl
setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin" "$SANDBOX/state"
    cat > "$SANDBOX/bin/systemctl" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/calls"
exit $1
EOF
    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/log"
EOF
    chmod +x "$SANDBOX/bin/"*
    : > "$SANDBOX/calls"
    : > "$SANDBOX/log"
}
teardown_sandbox() { rm -rf "$SANDBOX"; }

# $1 = the bytes the client sends (printf %b syntax). Sets REPLY_LINE.
run_target() {
    REPLY_LINE=$(printf '%b' "$1" \
        | VM_IDLE_STATE_DIR="$SANDBOX/state" PATH="$SANDBOX/bin:$PATH" bash "$TARGET")
}

echo "== vm-control.sh =="

echo "[1] wake -> starts the wake unit without blocking"
setup_sandbox 0
run_target 'wake\n'
[ "$REPLY_LINE" = "ok" ] && pass "replies ok" || fail "reply was '$REPLY_LINE'"
grep -qx "start --no-block nivuus-vm-wake.service" "$SANDBOX/calls" \
    && pass "runs systemctl start --no-block nivuus-vm-wake.service" \
    || fail "systemctl calls: $(cat "$SANDBOX/calls")"
teardown_sandbox

echo "[2] wake when systemctl fails -> err wake-failed"
setup_sandbox 1
run_target 'wake\n'
[ "$REPLY_LINE" = "err wake-failed" ] && pass "replies err wake-failed" || fail "reply was '$REPLY_LINE'"
teardown_sandbox

echo "[3] busy -> records a fresh epoch timestamp"
setup_sandbox 0
run_target 'busy\n'
[ "$REPLY_LINE" = "ok" ] && pass "replies ok" || fail "reply was '$REPLY_LINE'"
TS=$(cat "$SANDBOX/state/app-activity" 2>/dev/null)
NOW=$(date +%s)
if [[ "$TS" =~ ^[0-9]+$ ]] && [ $((NOW - TS)) -le 5 ] && [ $((NOW - TS)) -ge -1 ]; then
    pass "app-activity holds the current epoch ($TS)"
else
    fail "app-activity content is '$TS' (now=$NOW)"
fi
teardown_sandbox

echo "[4] unknown verb -> refused, nothing executed"
setup_sandbox 0
run_target 'reboot\n'
[ "$REPLY_LINE" = "err unknown-verb" ] && pass "replies err unknown-verb" || fail "reply was '$REPLY_LINE'"
[ ! -s "$SANDBOX/calls" ] && pass "systemctl not called" || fail "systemctl was called"
teardown_sandbox

echo "[5] a verb with an argument is refused (no verb takes one)"
setup_sandbox 0
run_target 'wake extra\n'
[ "$REPLY_LINE" = "err unknown-verb" ] && pass "replies err unknown-verb" || fail "reply was '$REPLY_LINE'"
[ ! -s "$SANDBOX/calls" ] && pass "systemctl not called" || fail "systemctl was called"
teardown_sandbox

echo "[6] empty input -> err no-request"
setup_sandbox 0
run_target ''
[ "$REPLY_LINE" = "err no-request" ] && pass "replies err no-request" || fail "reply was '$REPLY_LINE'"
teardown_sandbox

echo "[7] over-long line without newline -> refused, nothing executed"
setup_sandbox 0
run_target 'wakewakewakewakewakewakewake'
[ "$REPLY_LINE" = "err unknown-verb" ] && pass "replies err unknown-verb" || fail "reply was '$REPLY_LINE'"
[ ! -s "$SANDBOX/calls" ] && pass "systemctl not called" || fail "systemctl was called"
teardown_sandbox

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
