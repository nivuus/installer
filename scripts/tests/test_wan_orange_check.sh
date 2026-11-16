#!/bin/bash
# Tests for the liveness check of scripts/wan-orange.sh, against fake tools.
#
# The check decides whether both DHCP clients are released and restarted. A
# probe the gateway may legitimately ignore (ICMP echo) would do that every
# 150 s for ever on a healthy line, which is exactly the incoherent client
# Orange penalises. ARP cannot be ignored by a router that routes.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-orange.sh"
# Tripwire. On 2026-10-11 this suite was run against a version of the script
# that did not honour NIVUUS_TEST_PATH: the fakes were ignored and the test
# performed a real, unattended cutover as root. A script without the seam is
# never executed here.
grep -q "NIVUUS_TEST_PATH" "$SCRIPT" ||
    { echo "REFUSING: $SCRIPT ignores NIVUUS_TEST_PATH; running it would act on the real network"; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
B="$TMP/bin"; mkdir -p "$B" "$TMP/state"; : > "$TMP/calls"
cat > "$B/ip" <<EOF
#!/bin/sh
case "\$*" in "route show default dev"*) [ -e "$TMP/no_route" ] || echo "default via 192.0.2.1 proto dhcp metric 50" ;; esac
EOF
# Models the arping Debian ships in the `arping` package (Thomas Habets,
# 2.25), read in its source: exit 0 when a reply came back, 1 otherwise, AND 1
# on its own errors (unknown option, interface lookup, pcap). Only the closing
# statistics line, printed when the probe really ran, tells silence from error.
# $TMP/arp_mode: absent = silent gateway; answers; usage (exit 1, no
# statistics); exit2 (iputils' error code); notransmit (ran, sent nothing).
cat > "$B/arping" <<EOF
#!/bin/sh
echo "arping \$*" >> "$TMP/calls"
mode=\$(cat "$TMP/arp_mode" 2>/dev/null || echo silent)
[ -e "$TMP/arp_answers" ] && mode=answers
case "\$mode" in
    usage) echo "arping: unknown option" >&2; exit 1 ;;
    exit2) echo "arping: interface not found" >&2; exit 2 ;;
esac
echo "ARPING \$*"
case "\$mode" in
    answers) printf '\n--- x statistics ---\n2 packets transmitted, 2 packets received,   0%% unanswered (0 extra)\n'; exit 0 ;;
    notransmit) printf '\n--- x statistics ---\n0 packets transmitted, 0 packets received,   0%% unanswered (0 extra)\n'; exit 1 ;;
    *) printf '\n--- x statistics ---\n2 packets transmitted, 0 packets received, 100%% unanswered (0 extra)\n'; exit 1 ;;
esac
EOF
cat > "$B/ping" <<EOF
#!/bin/sh
echo "ping \$*" >> "$TMP/calls"; exit 1
EOF
cat > "$B/systemctl" <<EOF
#!/bin/sh
echo "systemctl \$*" >> "$TMP/calls"
EOF
chmod +x "$B"/*
check() { NIVUUS_TEST_PATH="$B" NIVUUS_WAN_STATE_DIR="$TMP/state" "$SCRIPT" check 2>"$TMP/stderr"; }
restarts() { grep -c "^systemctl restart" "$TMP/calls"; }

echo "== a gateway that answers ARP but not ping is alive =="
touch "$TMP/arp_answers"; check; check; check; check
[ "$(restarts)" = 0 ] && ok "no restart" || bad "no restart" "$(cat "$TMP/calls")"
[ ! -e "$TMP/state/wan-orange.failures" ] && ok "no failure recorded" || bad "no failure recorded" "$(cat "$TMP/state/wan-orange.failures")"
grep -q "^arping .*-i enp5s0.832 .*192.0.2.1" "$TMP/calls" && ok "probes the gateway by ARP on the WAN interface" || bad "ARP probe" "$(cat "$TMP/calls")"

echo "== three silent checks restart both clients once, options first =="
rm "$TMP/arp_answers"; : > "$TMP/calls"
check; check
[ "$(restarts)" = 0 ] && ok "two failures do nothing" || bad "two failures do nothing" "$(cat "$TMP/calls")"
check
[ "$(restarts)" = 1 ] && ok "the third restarts" || bad "the third restarts" "$(cat "$TMP/calls")"
grep -q "^systemctl restart nivuus-wan-orange-options.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service" "$TMP/calls" &&
    ok "options are rendered again before the clients" || bad "restart order" "$(grep systemctl "$TMP/calls")"
check
[ "$(restarts)" = 1 ] && ok "the count starts again from zero" || bad "the count starts again" "$(cat "$TMP/calls")"

echo "== one answer clears the count =="
: > "$TMP/calls"; rm -f "$TMP/state/wan-orange.failures"
check; check; touch "$TMP/arp_answers"; check; rm "$TMP/arp_answers"; check; check
[ "$(restarts)" = 0 ] && ok "no restart after an answer in between" || bad "no restart after an answer" "$(cat "$TMP/calls")"

echo "== no lease yet: nothing to probe, and nothing to restart =="
# Seen for real on 2026-10-11: counting a missing route as a failure restarted
# both clients every 150 s while they were still asking for a lease.
: > "$TMP/calls"; echo 2 > "$TMP/state/wan-orange.failures"; touch "$TMP/no_route"
check; check; check; check
[ "$(restarts)" = 0 ] && ok "a client still acquiring is left alone" || bad "client still acquiring" "$(cat "$TMP/calls")"
grep -q "^arping" "$TMP/calls" && bad "no probe without a gateway" "$(cat "$TMP/calls")" || ok "no probe without a gateway"

echo "== a probe that could not run is an error, never a silent gateway =="
# 2026-11-16 audit: any non-zero arping counted as silence, so a missing or
# different arping released both leases every 150 s, for ever.
rm -f "$TMP/no_route" "$TMP/arp_answers"
for mode in usage exit2 notransmit; do
    : > "$TMP/calls"; echo 1 > "$TMP/state/wan-orange.failures"; echo "$mode" > "$TMP/arp_mode"
    rcs=""; for _ in 1 2 3 4; do check; rcs="$rcs$?"; done
    [ "$(restarts)" = 0 ] && ok "$mode: no restart" || bad "$mode: no restart" "$(cat "$TMP/calls")"
    [ "$(cat "$TMP/state/wan-orange.failures")" = 1 ] && ok "$mode: the silence count is left as it was" ||
        bad "$mode: silence count untouched" "$(cat "$TMP/state/wan-orange.failures")"
    case "$rcs" in *0*) bad "$mode: the check fails (unit goes failed)" "rcs=$rcs" ;; *) ok "$mode: the check fails (unit goes failed)" ;; esac
    grep -q "could not probe" "$TMP/stderr" && ok "$mode: says the probe could not run" || bad "$mode: says so" "$(cat "$TMP/stderr")"
done
rm -f "$TMP/arp_mode"
if PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin command -v arping >/dev/null; then
    echo "  skip — arping is installed on this machine: the missing-binary case needs a machine without it"
else
    mv "$B/arping" "$TMP/arping.off"; : > "$TMP/calls"; echo 1 > "$TMP/state/wan-orange.failures"
    check; rc=$?
    [ "$rc" -ne 0 ] && [ "$(restarts)" = 0 ] && [ "$(cat "$TMP/state/wan-orange.failures")" = 1 ] &&
        ok "missing arping: error, no restart, count untouched" || bad "missing arping" "rc=$rc $(cat "$TMP/calls")"
    grep -q "arping" "$TMP/stderr" && ok "missing arping: named in the error" || bad "missing arping named" "$(cat "$TMP/stderr")"
    mv "$TMP/arping.off" "$B/arping"
fi

echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
