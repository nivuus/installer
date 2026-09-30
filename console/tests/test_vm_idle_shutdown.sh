#!/bin/bash
# Tests for the idle check of console/host/vm-idle-shutdown.sh.
#
# Only the strike counter is observed. Every scenario seeds 1 strike and stays
# below IDLE_STRIKES_LIMIT (3), so the hibernation branch is never reached.
#
# State file layout written by the script: "<now_ns> <cpu_ns> <strikes>".

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
    # Running guest, zero CPU time so the CPU condition never fires.
    cat > "$SANDBOX/bin/virsh" <<'EOF'
#!/bin/bash
case "$1" in
    domstate) echo running ;;
    domstats) echo "Domain: 'Windows'"; echo "  cpu.time=0" ;;
esac
EOF
    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/log"
EOF
    chmod +x "$SANDBOX/bin/"*
    : > "$SANDBOX/log"
    : > "$SANDBOX/conntrack"
    # A previous pass existed (PREV_NS != 0) and left 1 strike.
    echo "1 0 1" > "$SANDBOX/state/state"
}
teardown_sandbox() { rm -rf "$SANDBOX"; }

run_target() {
    VM_IDLE_STATE_DIR="$SANDBOX/state" NF_CONNTRACK="$SANDBOX/conntrack" \
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

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
