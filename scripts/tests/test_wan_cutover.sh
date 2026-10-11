#!/bin/bash
# The cutover is tested on what must never go wrong: its refusals and its order.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-cutover.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
UNITS_DIR="$(cd "$(dirname "$0")/../../configs/systemd" && pwd)"; export NIVUUS_UNIT_DIR="$UNITS_DIR"
EMPTY=$(mktemp -d); trap 'rmdir "$EMPTY"' EXIT
out=$(NIVUUS_TETHER_OK=1 NIVUUS_UNIT_DIR="$EMPTY" "$SCRIPT" to-dhcp --dry-run 2>&1); rc=$?
[ $rc -ne 0 ] && ok "refuses when the units are not installed" || bad "refuses when the units are not installed" "$out"
case "$out" in *"nmcli con down"*) bad "touches nothing without units" "$out" ;; *) ok "touches nothing without units" ;; esac
out=$(NIVUUS_TETHER_OK=0 "$SCRIPT" to-dhcp --dry-run 2>&1); rc=$?
[ $rc -ne 0 ] && ok "refuses without a working tether" || bad "refuses without a working tether" "$out"
case "$out" in *"nmcli con down"*) bad "touches nothing when refusing" "$out" ;; *) ok "touches nothing when refusing" ;; esac
out=$(NIVUUS_TETHER_OK=1 "$SCRIPT" to-dhcp --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && ok "dry run succeeds with a tether" || bad "dry run succeeds with a tether" "$out"
down=$(echo "$out" | grep -n "nmcli con down" | cut -d: -f1)
start=$(echo "$out" | grep -n "StartUnit.*nivuus-wan-orange.service" | cut -d: -f1)
[ -n "$down" ] && [ -n "$start" ] && [ "$down" -lt "$start" ] && ok "PPPoE goes down before DHCP starts" || bad "order" "$out"
echo "$out" | grep -q "autoconnect no" && ok "PPPoE autoconnect is disabled first" || bad "autoconnect" "$out"
out=$("$SCRIPT" sideways 2>&1); [ $? -eq 2 ] && ok "unknown mode is a usage error" || bad "usage" "$out"
echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
