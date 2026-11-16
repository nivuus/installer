#!/bin/bash
# Tests for the libvirt hook dispatcher, console/host/libvirt/hooks/qemu.
#
# The dispatcher runs every executable of qemu.d/<guest>/<hook>/<state>/. The
# 2026-11-16 audit found it ran them in find(1) order, which follows the
# directory, not the names, and that a `prepare` hook which refused the start
# did not stop the ones after it: bind-vfio-gpu.sh could refuse while
# 10-cpu-confine.sh still confined the host for a VM that never started.
#
# Runs on a copy of the dispatcher, against a tree of recording sub-hooks, with
# a fake logger. Nothing under /etc is read or written.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISPATCHER="$SCRIPT_DIR/../host/libvirt/hooks/qemu"

PASS=0
FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin" "$SANDBOX/hooks"
    cp "$DISPATCHER" "$SANDBOX/hooks/qemu"
    printf '#!/bin/bash\nexit 0\n' > "$SANDBOX/bin/logger"
    chmod +x "$SANDBOX/bin/logger"
    : > "$SANDBOX/ran"
}
teardown_sandbox() { rm -rf "$SANDBOX"; }

# add_hook <phase/state> <name> <exit code>: a sub-hook that records its name.
add_hook() {
    mkdir -p "$SANDBOX/hooks/qemu.d/Windows/$1"
    printf '#!/bin/bash\necho %s >> "%s/ran"\nexit %s\n' "$2" "$SANDBOX" "$3" \
        > "$SANDBOX/hooks/qemu.d/Windows/$1/$2"
    chmod +x "$SANDBOX/hooks/qemu.d/Windows/$1/$2"
}

dispatch() { # <phase> <state>
    PATH="$SANDBOX/bin:$PATH" timeout 30 "$SANDBOX/hooks/qemu" Windows "$1" "$2" - >/dev/null 2>&1
    RC=$?
}

echo "== libvirt hook dispatcher =="

echo "[1] sub-hooks run in the C-locale order of their names, whatever the directory order"
setup_sandbox
# Created in reverse, and enough of them that the directory order matching the
# name order by chance is negligible.
names="05-a 10-b 20-c 30-d 40-e 50-f 60-g 70-h Z-upper a-lower bind-x"
for n in $(printf '%s\n' $names | LC_ALL=C sort -r); do add_hook prepare/begin "$n" 0; done
dispatch prepare begin
want=$(printf '%s\n' $names | LC_ALL=C sort)
[ "$(cat "$SANDBOX/ran")" = "$want" ] && pass "sorted order" \
    || fail "ran: $(tr '\n' ' ' < "$SANDBOX/ran")"
[ "$RC" -eq 0 ] && pass "exit 0 when all succeed" || fail "rc=$RC"
teardown_sandbox

echo "[2] prepare: the first refusal stops the hooks after it and is returned"
setup_sandbox
add_hook prepare/begin 10-first 0
add_hook prepare/begin 20-refuses 3
add_hook prepare/begin 30-after 0
dispatch prepare begin
[ "$(tr '\n' ' ' < "$SANDBOX/ran")" = "10-first 20-refuses " ] && pass "30-after did not run" \
    || fail "ran: $(tr '\n' ' ' < "$SANDBOX/ran")"
[ "$RC" -eq 3 ] && pass "the refusal's code is returned to libvirt" || fail "rc=$RC"
teardown_sandbox

echo "[3] release: every hook runs even after a failure, and libvirt sees 0"
setup_sandbox
add_hook release/end 10-fails 1
add_hook release/end 20-still-runs 0
dispatch release end
[ "$(tr '\n' ' ' < "$SANDBOX/ran")" = "10-fails 20-still-runs " ] && pass "cleanup is not cut short" \
    || fail "ran: $(tr '\n' ' ' < "$SANDBOX/ran")"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "rc=$RC"
teardown_sandbox

echo "[4] a name with a space is run, not split"
setup_sandbox
add_hook started/begin "10 with space" 0
dispatch started begin
[ "$(cat "$SANDBOX/ran")" = "10 with space" ] && pass "ran as one file" \
    || fail "ran: $(cat "$SANDBOX/ran")"
teardown_sandbox

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
