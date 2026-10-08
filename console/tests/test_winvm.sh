#!/bin/bash
# Tests for console/host/winvm against a fake winrm_exec.py.
#
# winvm is what vm-idle-shutdown.sh calls to measure the guest CPU
# (`winvm --ps ...`) and to hibernate it (`winvm "shutdown /h /f"`). The fake
# client records the mode, the command and the password file it was handed,
# so these tests prove the wrapper speaks winrm_exec.py's real interface.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/../host/winvm"
REAL_EXEC="$SCRIPT_DIR/../guest/winrm_exec.py"

PASS=0
FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/guest"
    cat > "$SANDBOX/guest/winrm_exec.py" <<EOF
import os, sys
with open("$SANDBOX/calls", "w") as fh:
    fh.write("mode=" + sys.argv[1] + "\n")
    fh.write("cmd=" + " ".join(sys.argv[2:]) + "\n")
    fh.write("pass=" + os.environ.get("GUEST_PASS_FILE", "") + "\n")
EOF
    printf 'secret\n' > "$SANDBOX/admin.pass"
}
teardown_sandbox() { rm -rf "$SANDBOX"; }

run_target() {
    NIVUUS_CONSOLE_DIR="$SANDBOX" GUEST_PASS_FILE="$SANDBOX/admin.pass" \
        bash "$TARGET" "$@" >"$SANDBOX/stdout" 2>"$SANDBOX/stderr"
    RC=$?
}

echo "== winvm =="

echo "[1] plain command -> cmd mode, password handed by file"
setup_sandbox
run_target "shutdown /h /f"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "exit $RC: $(cat "$SANDBOX/stderr")"
grep -qx "mode=cmd" "$SANDBOX/calls" && pass "cmd mode" || fail "$(cat "$SANDBOX/calls")"
grep -qx "cmd=shutdown /h /f" "$SANDBOX/calls" && pass "command passed through" \
    || fail "$(cat "$SANDBOX/calls")"
grep -qx "pass=$SANDBOX/admin.pass" "$SANDBOX/calls" \
    && pass "GUEST_PASS_FILE is the name winrm_exec.py reads" || fail "$(cat "$SANDBOX/calls")"
teardown_sandbox

echo "[2] --ps -> powershell mode (vm-idle-shutdown.sh's CPU query)"
setup_sandbox
run_target --ps 'Get-Date'
grep -qx "mode=ps" "$SANDBOX/calls" && pass "ps mode" || fail "$(cat "$SANDBOX/calls")"
grep -qx "cmd=Get-Date" "$SANDBOX/calls" && pass "--ps is not forwarded as text" \
    || fail "$(cat "$SANDBOX/calls")"
teardown_sandbox

echo "[3] unreadable password -> loud failure naming the file"
setup_sandbox
rm "$SANDBOX/admin.pass"
run_target hostname
[ "$RC" -eq 1 ] && pass "exit 1" || fail "exit $RC"
grep -q "admin.pass" "$SANDBOX/stderr" && pass "names the missing file" \
    || fail "stderr: $(cat "$SANDBOX/stderr")"
[ ! -e "$SANDBOX/calls" ] && pass "client never started" || fail "client ran"
teardown_sandbox

echo "[4] no argument -> usage, exit 2"
setup_sandbox
run_target
[ "$RC" -eq 2 ] && pass "exit 2" || fail "exit $RC"
teardown_sandbox

echo "[5] the default client is the package's own winrm_exec.py"
grep -q '^EXEC="${NIVUUS_WINRM_EXEC:-$PACKAGE_DIR/guest/winrm_exec.py}"' "$TARGET" \
    && [ -f "$REAL_EXEC" ] && pass "guest/winrm_exec.py, shipped in the package" \
    || fail "default client path does not match the package layout"
grep -q 'GUEST_PASS_FILE' "$REAL_EXEC" && pass "winrm_exec.py reads GUEST_PASS_FILE" \
    || fail "winrm_exec.py no longer reads GUEST_PASS_FILE"

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
