#!/bin/bash
# Tests for the idle check of console/host/vm-idle-shutdown.sh.
#
# Only the strike counter is observed. Every scenario seeds 1 strike and stays
# below IDLE_STRIKES_LIMIT (3), so the hibernation branch is never reached.
#
# State file layout written by the script: "<guest_time> <guest_idle> <strikes>".
# The guest CPU comes from a fake `winvm` (VM_IDLE_WINVM) printing "<time> <idle>"
# from $GUEST_SAMPLE; the default sample is a fully idle guest 10 minutes later.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/../host/vm-idle-shutdown.sh"

PASS=0
FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin" "$SANDBOX/state"
    cat > "$SANDBOX/bin/virsh" <<'EOF'
#!/bin/bash
case "$1" in
    domstate) echo running ;;
esac
EOF
    cat > "$SANDBOX/bin/winvm" <<'EOF'
#!/bin/bash
# 10 minutes = 6000000000 units of 100 ns. Idle counter moves as much as time.
[ "${GUEST_SAMPLE:-}" = "fail" ] && exit 1
echo "${GUEST_SAMPLE:-6000000001 6000000001}"
EOF
    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/log"
EOF
    chmod +x "$SANDBOX/bin/"*
    : > "$SANDBOX/log"
    : > "$SANDBOX/conntrack"
    # A previous pass existed (PREV_T != 0) and left 1 strike.
    echo "1 1 1" > "$SANDBOX/state/state"
}
teardown_sandbox() { rm -rf "$SANDBOX"; }

run_target() {
    VM_IDLE_STATE_DIR="$SANDBOX/state" NF_CONNTRACK="$SANDBOX/conntrack" \
        VM_IDLE_WINVM="$SANDBOX/bin/winvm" \
        PATH="$SANDBOX/bin:$PATH" bash "$TARGET" >/dev/null 2>&1
}
strikes() { awk '{print $3}' "$SANDBOX/state/state"; }

echo "== vm-idle-shutdown.sh =="

echo "[1] no signal at all -> one more strike"
setup_sandbox
run_target
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[2] fresh app-activity -> strikes reset"
setup_sandbox
date +%s > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[3] stale app-activity (10 min old) -> one more strike"
setup_sandbox
echo $(( $(date +%s) - 600 )) > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[4] app-activity just inside the window (170 s) -> strikes reset"
setup_sandbox
echo $(( $(date +%s) - 170 )) > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[5] app-activity just outside the window (190 s) -> one more strike"
setup_sandbox
echo $(( $(date +%s) - 190 )) > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[6] unreadable app-activity -> no activity, and the cause is logged"
setup_sandbox
echo "not-a-timestamp" > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
grep -q "not a timestamp" "$SANDBOX/log" && pass "logs why the file was ignored" \
    || fail "no log line about the unreadable file"
teardown_sandbox

echo "[7] app-activity dated in the future (clock skew) -> treated as active"
setup_sandbox
echo $(( $(date +%s) + 3600 )) > "$SANDBOX/state/app-activity"
run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[8] established Moonlight flow still resets strikes (unchanged)"
setup_sandbox
echo "tcp 6 431999 ESTABLISHED src=1.2.3.4 dst=5.6.7.8 sport=50000 dport=47984 src=192.168.3.2 dst=1.2.3.4 sport=47984 dport=50000 [ASSURED] mark=0" > "$SANDBOX/conntrack"
run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[9] app-activity with leading zeros (octal trap) -> no activity, no arithmetic error"
setup_sandbox
echo "0000000009" > "$SANDBOX/state/app-activity"
# LC_ALL=C pins the wording of bash's own error messages.
LC_ALL=C VM_IDLE_STATE_DIR="$SANDBOX/state" NF_CONNTRACK="$SANDBOX/conntrack" \
    VM_IDLE_WINVM="$SANDBOX/bin/winvm" PATH="$SANDBOX/bin:$PATH" bash "$TARGET" >/dev/null 2>"$SANDBOX/stderr"
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
if grep -q "value too great for base" "$SANDBOX/stderr" "$SANDBOX/log"; then
    fail "bash arithmetic error: $(cat "$SANDBOX/stderr")"
else
    pass "no base error on leading zeros"
fi
teardown_sandbox

echo "[10] guest CPU busy (idle moved 90 % of the time -> 10 % busy) -> strikes reset"
setup_sandbox
GUEST_SAMPLE="6000000001 5400000001" run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[11] guest CPU just under the threshold (4 % busy) -> one more strike"
setup_sandbox
GUEST_SAMPLE="6000000001 5760000001" run_target
[ "$(strikes)" = "2" ] && pass "strikes 1 -> 2" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[12] guest restarted (counters went backwards) -> strikes reset"
setup_sandbox
echo "9000000000 9000000000 1" > "$SANDBOX/state/state"
GUEST_SAMPLE="100 100" run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
teardown_sandbox

echo "[13] guest cannot be measured -> counted as active, and the cause is logged"
setup_sandbox
GUEST_SAMPLE=fail run_target
[ "$(strikes)" = "0" ] && pass "strikes reset to 0" || fail "strikes=$(strikes)"
grep -q "guest CPU unavailable" "$SANDBOX/log" && pass "logs why the pass counted as active" \
    || fail "no log line about the failed measurement"
teardown_sandbox

echo "[14] first pass after a wake (no baseline) -> strikes stay 0, baseline stored"
setup_sandbox
echo "0 0 0" > "$SANDBOX/state/state"
run_target
[ "$(strikes)" = "0" ] && pass "strikes stay 0" || fail "strikes=$(strikes)"
[ "$(awk '{print $1}' "$SANDBOX/state/state")" = "6000000001" ] && pass "baseline stored" \
    || fail "state=$(cat "$SANDBOX/state/state")"
teardown_sandbox

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
