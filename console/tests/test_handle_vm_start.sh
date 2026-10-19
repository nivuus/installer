#!/bin/bash
# Tests for handle-vm-start.sh against fake `virsh`, `firewall-cmd`, `logger`
# and a fake libvirt started hook.
#
# The fakes reproduce this host's topology, not a convenient one:
#   - `virsh domifaddr` never answers (no guest agent, no libvirt network);
#   - `firewall-cmd --wait` is rejected, as firewalld 2.x does;
#   - the forward-ports appear only when the started hook runs - which libvirt
#     does itself on `virsh start`.
# Until 2026-10-19 the fake `virsh` handed out an address and the fake
# firewall-cmd accepted `--wait`: the suite was green on exactly the two calls
# that failed in production, every cold start, for weeks.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/../../console/host/handle-vm-start.sh"

PASS=0
FAIL=0

fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }

# Build a sandbox: rigged PATH, lock and hook isolated from production.
# $1 = fake `virsh` body (bash, receives virsh's arguments). It can call
#      "$HOOK" to play libvirt running the started hook.
setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin"
    RULES="$SANDBOX/rules"

    # The started hook: installs the forward-ports, like rules.sh.
    cat > "$SANDBOX/hook" <<EOF
#!/bin/bash
echo "hook \$*" >> "$SANDBOX/calls"
echo "port=47984:proto=tcp:toport=47984:toaddr=192.168.3.2" > "$RULES"
EOF

    cat > "$SANDBOX/bin/virsh" <<EOF
#!/bin/bash
STATE_FILE="$SANDBOX/state"
HOOK="$SANDBOX/hook"
echo "\$*" >> "$SANDBOX/calls"
case "\$1" in
    domifaddr) exit 0 ;;  # never answers on this topology
esac
$1
EOF

    cat > "$SANDBOX/bin/firewall-cmd" <<EOF
#!/bin/bash
echo "firewall-cmd \$*" >> "$SANDBOX/calls"
case " \$* " in
    *" --wait "*) echo "firewall-cmd: error: unrecognized arguments: --wait" >&2; exit 2 ;;
    *--list-forward-ports*) cat "$RULES" 2>/dev/null; exit 0 ;;
    *) exit 0 ;;
esac
EOF

    # logger: capture instead of writing to the system journal.
    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
shift 2 2>/dev/null
cat >> "$SANDBOX/log" 2>/dev/null || true
echo "\$*" >> "$SANDBOX/log"
EOF

    chmod +x "$SANDBOX/bin/"* "$SANDBOX/hook"
    export VM_TRIGGER_LOCK="$SANDBOX/lock"
    export VM_STARTED_HOOK="$SANDBOX/hook"
    export VM_RULES_WAIT_SECONDS=4
    : > "$SANDBOX/calls"
    : > "$SANDBOX/log"
}

teardown_sandbox() {
    rm -rf "$SANDBOX"
    unset VM_TRIGGER_LOCK VM_STARTED_HOOK VM_RULES_WAIT_SECONDS
}

# Run the script under the rigged PATH and time it. Sets RC and ELAPSED.
run_target() {
    local start end
    start=$(date +%s)
    PATH="$SANDBOX/bin:$PATH" timeout 120 bash "$TARGET" >/dev/null 2>&1
    RC=$?
    end=$(date +%s)
    ELAPSED=$((end - start))
}

# Calls the script must never make on this host.
check_no_dead_calls() {
    if grep -q "domifaddr" "$SANDBOX/calls"; then
        fail "asks virsh domifaddr, which cannot answer here"
    else
        pass "does not look the VM's address up"
    fi
    if grep -qE "add-forward-port|--wait" "$SANDBOX/calls"; then
        fail "adds a rule itself (the hook owns them) or passes --wait"
    else
        pass "leaves the forward-ports to the hook"
    fi
}

echo "== handle-vm-start.sh =="

# --- 1 (regression 2026-08-24): libvirtd unreachable ----------------------
echo "[1] libvirtd unreachable -> gives up at once"
setup_sandbox '
echo "error: failed to connect to the hypervisor" >&2
exit 1
'
run_target
if [ "$RC" -ne 0 ]; then pass "exits with an error (rc=$RC)"; else fail "should fail (rc=$RC)"; fi
if [ "$ELAPSED" -lt 30 ]; then
    pass "gives up in ${ELAPSED}s (< 30s)"
else
    fail "waited ${ELAPSED}s: the empty string is still read as a transitional state"
fi
if grep -qiE "hypervisor|libvirt" "$SANDBOX/log"; then
    pass "logs the real cause (hypervisor unreachable)"
else
    fail "does not say the hypervisor is unreachable"
fi
if grep -q "^start Windows" "$SANDBOX/calls"; then
    fail "tried 'virsh start' with the hypervisor unreachable"
else
    pass "does not try to start the VM"
fi
teardown_sandbox

# --- 2: rules already in place ---------------------------------------------
echo "[2] forward-ports already in place -> nothing to do"
setup_sandbox '
case "$1" in
    domstate) echo "running"; exit 0 ;;
    *) exit 0 ;;
esac
'
"$VM_STARTED_HOOK"
: > "$SANDBOX/calls"
run_target
if [ "$RC" -eq 0 ]; then pass "exits successfully"; else fail "should succeed (rc=$RC)"; fi
if grep -qE "^start |^hook " "$SANDBOX/calls"; then
    fail "started the VM or re-ran the hook for nothing"
else
    pass "neither starts the VM nor re-runs the hook"
fi
teardown_sandbox

# --- 3 (regression 2026-10-08): cold start ----------------------------------
# The case that left nivuus-vm-wake.service failed: the VM boots, the hook
# opens the ports, and the script must report success - quickly.
echo "[3] VM 'shut off' -> starts it and succeeds once the hook has run"
setup_sandbox '
case "$1" in
    domstate)
        if [ -f "$STATE_FILE" ]; then echo "running"; else echo "shut off"; fi
        exit 0 ;;
    start) touch "$STATE_FILE"; "$HOOK" Windows started begin -; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if grep -q "^start Windows" "$SANDBOX/calls"; then pass "calls 'virsh start Windows'"; else fail "did not start the VM"; fi
if [ "$RC" -eq 0 ]; then pass "exits successfully"; else fail "should succeed (rc=$RC)"; fi
if [ "$ELAPSED" -lt 10 ]; then
    pass "returns in ${ELAPSED}s once the ports are open"
else
    fail "took ${ELAPSED}s although the ports were open"
fi
check_no_dead_calls
teardown_sandbox

# --- 4: the hook never installs the rules -----------------------------------
# A real failure must still be one: bounded wait, then an error naming the hook.
echo "[4] VM started but the hook installs nothing -> bounded failure"
setup_sandbox '
case "$1" in
    domstate)
        if [ -f "$STATE_FILE" ]; then echo "running"; else echo "shut off"; fi
        exit 0 ;;
    start) touch "$STATE_FILE"; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if [ "$RC" -ne 0 ]; then pass "exits with an error (rc=$RC)"; else fail "reported success without forward-ports"; fi
if [ "$ELAPSED" -lt 20 ]; then pass "gives up within the configured wait (${ELAPSED}s)"; else fail "waited ${ELAPSED}s"; fi
if grep -q "hook" "$SANDBOX/log"; then pass "points at the libvirt hook"; else fail "does not say where to look"; fi
teardown_sandbox

# --- 5: running VM whose rules were dropped ---------------------------------
echo "[5] VM 'running' without its rules -> re-runs the started hook"
setup_sandbox '
case "$1" in
    domstate) echo "running"; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if grep -q "^hook Windows started begin" "$SANDBOX/calls"; then
    pass "re-runs the hook with libvirt's arguments"
else
    fail "did not re-run the hook"
fi
if grep -q "^start " "$SANDBOX/calls"; then fail "tried to start a running VM"; else pass "does not start a running VM"; fi
if [ "$RC" -eq 0 ]; then pass "exits successfully"; else fail "should succeed (rc=$RC)"; fi
check_no_dead_calls
teardown_sandbox

# --- 6: genuine transitional state ------------------------------------------
echo "[6] real 'in shutdown' -> waits, then starts"
setup_sandbox '
case "$1" in
    domstate)
        N=$(cat "$STATE_FILE.n" 2>/dev/null || echo 0)
        echo $((N + 1)) > "$STATE_FILE.n"
        if [ -f "$STATE_FILE" ]; then echo "running"
        elif [ "$N" -lt 2 ]; then echo "in shutdown"
        else echo "shut off"; fi
        exit 0 ;;
    start) touch "$STATE_FILE"; "$HOOK" Windows started begin -; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if grep -q "^start Windows" "$SANDBOX/calls"; then pass "starts the VM once settled"; else fail "did not start the VM after settling"; fi
if [ "$RC" -eq 0 ]; then pass "exits successfully"; else fail "should succeed (rc=$RC)"; fi
teardown_sandbox

# --- 7: nothing written outside the sandbox ---------------------------------
# The previous version wrote /var/run/vm-trigger-rules/*.ip, and this suite,
# run as root on the host, wrote it in production (found 2026-10-19).
echo "[7] every path the script writes is overridable"
if grep -q "VM_TRIGGER_LOCK" "$TARGET"; then pass "the lock honours VM_TRIGGER_LOCK"; else fail "the lock is hard-coded"; fi
if grep -qE "/var/run/|mkdir" "$TARGET"; then
    fail "the script writes a hard-coded path"
else
    pass "no other write to a fixed path"
fi

echo
echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
