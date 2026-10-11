#!/bin/bash
# Tests for scripts/wan-cutover.sh.
#
# The cutover is the one script that can leave a household without Internet,
# so its real body is run here, not only its dry run: `--detached` keeps it in
# the foreground, and NIVUUS_TEST_PATH puts fake nmcli/busctl/curl/sleep ahead
# of the real ones. The fakes model only what the script observes: the PPPoE
# profile state, whether DHCP brings the Internet back, and what was called.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-cutover.sh"
# Tripwire. On 2026-10-11 this suite was run against a version of the script
# that did not honour NIVUUS_TEST_PATH: the fakes were ignored and the test
# performed a real, unattended cutover as root. A script without the seam is
# never executed here.
grep -q "NIVUUS_TEST_PATH" "$SCRIPT" ||
    { echo "REFUSING: $SCRIPT ignores NIVUUS_TEST_PATH; running it would act on the real network"; exit 1; }
UNITS_DIR="$(cd "$(dirname "$0")/../../configs/systemd" && pwd)"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
check() { if eval "$2"; then ok "$1"; else bad "$1" "$(cat "$F/calls" 2>/dev/null | tr '\n' '|') out=$OUT rc=$RC"; fi; }

fakes() { # fresh fake world: PPPoE active, DHCP dead unless told otherwise
    F="$TMP/world.$RANDOM"; mkdir -p "$F/bin"; : > "$F/calls"; echo activated > "$F/pppoe_state"
    cat > "$F/bin/nmcli" <<EOF
#!/bin/sh
echo "nmcli \$*" >> "$F/calls"
case "\$*" in
    *"GENERAL.STATE con show"*) cat "$F/pppoe_state" ;;
    "con down"*) [ -e "$F/con_down_fails" ] && exit 4; : > "$F/pppoe_state" ;;
    "con up"*) echo activated > "$F/pppoe_state" ;;
esac
EOF
    cat > "$F/bin/busctl" <<EOF
#!/bin/sh
echo "busctl \$*" >> "$F/calls"
case "\$*" in *"StartUnit ss nivuus-wan-orange-dhcp4.service"*) touch "$F/dhcp_started" ;; esac
EOF
    cat > "$F/bin/curl" <<EOF
#!/bin/sh
code=000
case "\$*" in
    *"--interface enp5s0.832"*) [ -e "$F/dhcp_works" ] && [ -e "$F/dhcp_started" ] && code=204 ;;
    *"--interface ppp0"*) [ "\$(cat "$F/pppoe_state")" = activated ] && [ ! -e "$F/ppp_probe_fails" ] && code=204 ;;
esac
printf %s "\$code"
EOF
    printf '#!/bin/sh\nexit 0\n' > "$F/bin/sleep"; cp "$F/bin/sleep" "$F/bin/journalctl"
    chmod +x "$F/bin/"*
}
cutover() { # runs the script against the current fake world
    OUT=$(NIVUUS_TEST_PATH="$F/bin" NIVUUS_UNIT_DIR="${UNITS:-$UNITS_DIR}" NIVUUS_TETHER_OK="${TETHER:-1}" \
          NIVUUS_CUTOVER_LOCK="$F/lock" NIVUUS_CUTOVER_LOG="$F/log" \
          NIVUUS_CUTOVER_WAIT_S=1 NIVUUS_CUTOVER_COOLDOWN_S=0 NIVUUS_CUTOVER_RETRY_S=0 "$SCRIPT" "$@" 2>&1); RC=$?
}
called() { grep -qE "$1" "$F/calls"; }
count()  { grep -cE "$1" "$F/calls"; }
line()   { grep -nE "$1" "$F/calls" | head -1 | cut -d: -f1; }

echo "== refusals touch nothing =="
fakes; EMPTY="$TMP/empty"; mkdir -p "$EMPTY"; UNITS="$EMPTY" cutover to-dhcp --detached
check "refuses when the units are not installed" '[ $RC -ne 0 ] && ! called "con down"'
fakes; TETHER=0 cutover to-dhcp --detached
check "refuses without a working tether" '[ $RC -ne 0 ] && ! called "con down"'
fakes; : > "$F/pppoe_state"; cutover to-dhcp --detached
check "refuses when PPPoE is not the active uplink" '[ $RC -ne 0 ] && ! called "con down|StartUnit"'
fakes; exec 8>"$F/lock"; flock -n 8; cutover to-dhcp --detached; exec 8>&-
check "refuses while another cutover holds the lock" '[ $RC -ne 0 ] && ! called "con down"'
fakes; cutover sideways; check "unknown mode is a usage error" '[ $RC -eq 2 ]'

echo "== dry run executes nothing, whatever the flag order =="
fakes; cutover to-dhcp --dry-run
check "dry run succeeds and prints the order" '[ $RC -eq 0 ] && ! called "con down|busctl" && echo "$OUT" | grep -q "would run: nmcli con down"'
fakes; cutover to-dhcp --detached --dry-run
check "--detached --dry-run is still a dry run" '! called "con down|busctl"'
fakes; cutover --dry-run to-dhcp
check "--dry-run before the mode is still a dry run" '[ $RC -eq 0 ] && ! called "con down|busctl"'

echo "== success =="
fakes; touch "$F/dhcp_works"; cutover to-dhcp --detached
check "returns 0 when DHCP brings the Internet back" '[ $RC -eq 0 ]'
check "PPPoE goes down before DHCP starts" '[ "$(line "con down")" -lt "$(line "StartUnit")" ]'
check "units are enabled, then PPPoE autoconnect is turned off" 'called "EnableUnitFiles" && [ "$(line "EnableUnitFiles")" -lt "$(line "autoconnect no")" ]'
check "PPPoE is not redialled" '! called "con up"'

echo "== until DHCP is proven, a reboot must still find PPPoE =="
fakes; cutover to-dhcp --detached
check "autoconnect is never turned off on the failure path" '! called "autoconnect no"'
check "the DHCP units are never enabled on the failure path" '! called "EnableUnitFiles"'

echo "== rollback =="
check "returns 3" '[ $RC -eq 3 ]'
check "stops the DHCP units" 'called "StopUnit ss nivuus-wan-orange-dhcp4.service"'
check "redials PPPoE exactly once" '[ "$(count "con up")" -eq 1 ] && [ "$(cat "$F/pppoe_state")" = activated ]'
fakes; touch "$F/ppp_probe_fails"; cutover to-dhcp --detached
check "a session that is up is never torn down again because the probe fails" '[ "$(count "con up")" -eq 1 ]'

echo "== the step that must not be skipped =="
fakes; touch "$F/con_down_fails" "$F/dhcp_works"; cutover to-dhcp --detached
check "aborts when PPPoE cannot be taken down" '[ $RC -ne 0 ] && ! called "StartUnit|EnableUnitFiles|autoconnect no"'

echo "== to-pppoe =="
fakes; cutover to-pppoe --detached
check "does not redial a PPPoE session that is already up" '[ $RC -eq 0 ] && ! called "con up" && called "DisableUnitFiles"'
fakes; : > "$F/pppoe_state"; cutover to-pppoe --detached
check "redials when PPPoE is down" '[ $RC -eq 0 ] && [ "$(count "con up")" -eq 1 ]'

echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
