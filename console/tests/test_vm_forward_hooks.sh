#!/bin/bash
# Tests for the Windows domain's firewalld hooks (started/begin/rules.sh and
# stopped/end/rules.sh) against a fake `firewall-cmd`.
#
# The fake reproduces this host's zone layout, not a convenient one: seven
# active zones, among them the default zone printed as `internal (default)`,
# a WAN/guest zone (`external`), and zones the VM has no business in
# (`public`, `drop`, `docker`, `trusted`). It answers INVALID_ZONE for a zone
# that does not exist, as firewalld does.
#
# The hooks are run from a copy of the whole hooks tree, through the libvirt
# dispatcher as well as directly (handle-vm-start.sh calls the started hook
# directly), so the shared configuration is resolved the way it is once
# installed.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/../host/libvirt/hooks"

PASS=0
FAIL=0

fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }

STREAM_PORTS="tcp/47984 tcp/47989 tcp/48010 udp/47998 udp/47999 udp/48000"
LAN_ZONES="internal home"

# $1 = space-separated zones on which every add/remove must fail.
setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin"
    cp -a "$HOOKS_SRC" "$SANDBOX/hooks"

    cat > "$SANDBOX/bin/firewall-cmd" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/calls"
case "\$1" in
    --get-active-zones)
        cat <<'ZONES'
docker
  interfaces: br-1 docker0
drop
  interfaces: wwan0
external
  interfaces: wan0 guestvpn0
home
  interfaces: lanbr0
internal (default)
  sources: 192.168.3.0/24
public
  interfaces: guestbr0
trusted
  interfaces: wpan0
ZONES
        exit 0 ;;
esac
zone="\${1#--zone=}"
case " block dmz docker drop external home internal libvirt public trusted work " in
    *" \$zone "*) ;;
    *) echo "Error: INVALID_ZONE: \$zone" >&2; exit 112 ;;
esac
case " $1 " in
    *" \$zone "*) echo "Error: COMMAND_FAILED" >&2; exit 13 ;;
esac
exit 0
EOF

    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/log"
EOF
    printf '#!/bin/bash\nexit 0\n' > "$SANDBOX/bin/sleep"

    chmod +x "$SANDBOX/bin/"*
    : > "$SANDBOX/calls"
    : > "$SANDBOX/log"
}

teardown_sandbox() { rm -rf "$SANDBOX"; }

# $1 = started|stopped, $2 = direct|dispatcher. Sets RC.
run_hook() {
    local phase sub path
    phase="$1"
    if [ "$phase" = started ]; then sub=begin; else sub=end; fi
    if [ "$2" = dispatcher ]; then
        PATH="$SANDBOX/bin:$PATH" timeout 30 "$SANDBOX/hooks/qemu" Windows "$phase" "$sub" - \
            >/dev/null 2>&1
    else
        path="$SANDBOX/hooks/qemu.d/Windows/$phase/$sub/rules.sh"
        PATH="$SANDBOX/bin:$PATH" timeout 30 "$path" Windows "$phase" "$sub" - \
            >/dev/null 2>&1
    fi
    RC=$?
}

# The "zone proto/port" set an action touched, sorted. $1 = add|remove.
touched() {
    grep -E -- "--$1-forward-port=" "$SANDBOX/calls" \
        | sed -E 's/^--zone=([^ ]+) --[a-z]+-forward-port=port=([0-9]+):proto=([a-z]+):.*/\1 \3\/\2/' \
        | sort
}

expected_set() {
    local zone p
    {
        for zone in $LAN_ZONES; do
            for p in $STREAM_PORTS tcp/3389; do echo "$zone $p"; done
        done
        for p in $STREAM_PORTS; do echo "external $p"; done
    } | sort
}

echo "== libvirt firewalld hooks (Windows) =="

echo "[1] started: writes only to the allow-listed zones"
setup_sandbox ""
run_hook started direct
[ "$RC" -eq 0 ] && pass "exits 0 when every add succeeds" || fail "rc=$RC on a clean run"
ADDED=$(touched add)
for zone in public drop docker trusted "(default)"; do
    if echo "$ADDED" | grep -qF "$zone "; then
        fail "added a forward-port to zone '$zone'"
    else
        pass "nothing added to zone '$zone'"
    fi
done
if grep -q -- "--zone=(default)" "$SANDBOX/calls"; then
    fail "addressed the bogus zone '(default)'"
else
    pass "never addresses the bogus zone '(default)'"
fi
RDP_OUTSIDE=$(echo "$ADDED" | grep " tcp/3389$" | grep -vE "^(internal|home) ")
if [ -z "$RDP_OUTSIDE" ]; then
    pass "3389 is forwarded only in the LAN zones"
else
    fail "3389 forwarded outside the LAN: $(echo "$RDP_OUTSIDE" | tr '\n' ' ')"
fi
if [ "$ADDED" = "$(expected_set)" ]; then
    pass "adds the LAN set (streaming + RDP) and the streaming set on 'external'"
else
    fail "unexpected add set: $(echo "$ADDED" | tr '\n' ' ')"
fi
teardown_sandbox

echo "[2] stopped removes exactly what started added"
setup_sandbox ""
run_hook started direct
run_hook stopped direct
[ "$RC" -eq 0 ] && pass "stopped exits 0 when every remove succeeds" || fail "stopped rc=$RC"
if [ "$(touched add)" = "$(touched remove)" ] && [ -n "$(touched remove)" ]; then
    pass "start and stop touch the same zone/port set"
else
    fail "add and remove sets differ: added=[$(touched add | tr '\n' ' ')] removed=[$(touched remove | tr '\n' ' ')]"
fi
teardown_sandbox

echo "[3] a failing add on an allow-listed zone is a failing hook"
setup_sandbox "internal"
run_hook started direct
if [ "$RC" -ne 0 ]; then pass "started exits non-zero (rc=$RC)"; else fail "started exited 0 although adds failed"; fi
grep -q "internal" "$SANDBOX/log" && pass "logs the zone that failed" || fail "the failure is not logged"
if touched add | grep -q "^home "; then
    pass "keeps going on the other zones"
else
    fail "stopped at the first failing zone"
fi
teardown_sandbox

echo "[4] a failing remove is a failing hook"
setup_sandbox "home"
run_hook stopped direct
if [ "$RC" -ne 0 ]; then pass "stopped exits non-zero (rc=$RC)"; else fail "stopped exited 0 although removes failed"; fi
teardown_sandbox

echo "[5] through the dispatcher, a failing started hook does not kill the VM"
# libvirt aborts the start (stops the domain) when the `started` hook returns
# non-zero; the dispatcher only propagates `prepare` failures.
setup_sandbox "internal"
run_hook started dispatcher
[ "$RC" -eq 0 ] && pass "dispatcher exits 0 for started" || fail "dispatcher rc=$RC: libvirt would stop the VM"
if touched add | grep -q "^external "; then
    pass "the dispatcher ran the installed hook (shared config resolved)"
else
    fail "the hook did nothing through the dispatcher"
fi
teardown_sandbox

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
