#!/bin/bash
# Tests for the Orange dhclient hooks, against a fake `ip` and a temp state dir.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/../../configs/network/orange" && pwd)"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
mkdir -p "$TMP/bin"; printf '#!/bin/sh\necho "$*" >> "%s/ip.log"\n' "$TMP" > "$TMP/bin/ip"; chmod +x "$TMP/bin/ip"
run_exit() { # reason new old interface
    ( PATH="$TMP/bin:$PATH"; export NIVUUS_WAN_STATE_DIR="$TMP/state"
      reason=$1 new_ip6_prefix=$2 old_ip6_prefix=$3 interface=${4:-enp5s0.832}
      . "$DIR/dhclient-exit-hook" ); }

echo "== a bound prefix is parked and recorded =="
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:1:100::/56 ""
grep -q "route replace unreachable 2a01:cb00:1:100::/56" "$TMP/ip.log" && ok "unreachable route" || bad "unreachable route" "$(cat "$TMP/ip.log")"
[ "$(cat "$TMP/state/wan-orange.prefix")" = "2a01:cb00:1:100::/56" ] && ok "state file" || bad "state file" "$(cat "$TMP/state/wan-orange.prefix" 2>&1)"

echo "== a changed prefix replaces the old one =="
: > "$TMP/ip.log"; run_exit RENEW6 2a01:cb00:2:200::/56 2a01:cb00:1:100::/56
grep -q "route del unreachable 2a01:cb00:1:100::/56" "$TMP/ip.log" && ok "old route removed" || bad "old route removed" "$(cat "$TMP/ip.log")"
[ "$(cat "$TMP/state/wan-orange.prefix")" = "2a01:cb00:2:200::/56" ] && ok "state follows" || bad "state follows" "$(cat "$TMP/state/wan-orange.prefix")"

echo "== an unchanged renewal deletes nothing =="
: > "$TMP/ip.log"; run_exit RENEW6 2a01:cb00:2:200::/56 2a01:cb00:2:200::/56
grep -q "route del" "$TMP/ip.log" && bad "no deletion" "$(cat "$TMP/ip.log")" || ok "no deletion"

echo "== release clears everything =="
: > "$TMP/ip.log"; run_exit RELEASE6 "" 2a01:cb00:2:200::/56
[ ! -e "$TMP/state/wan-orange.prefix" ] && ok "state removed" || bad "state removed" "still there"

echo "== another interface is ignored =="
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:9:900::/56 "" eth9
[ ! -s "$TMP/ip.log" ] && ok "no route touched" || bad "no route touched" "$(cat "$TMP/ip.log")"

echo "== resolv.conf is left alone on the WAN only =="
out=$( make_resolv_conf() { echo REWRITTEN; }; interface=enp5s0.832; . "$DIR/dhclient-enter-hook"; make_resolv_conf )
[ -z "$out" ] && ok "neutralised on enp5s0.832" || bad "neutralised on enp5s0.832" "$out"
out=$( make_resolv_conf() { echo REWRITTEN; }; interface=eth9; . "$DIR/dhclient-enter-hook"; make_resolv_conf )
[ "$out" = REWRITTEN ] && ok "untouched elsewhere" || bad "untouched elsewhere" "$out"

echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
