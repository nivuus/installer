#!/bin/bash
# What dhclient really puts on the wire with the rendered configuration.
#
# dhclient is confined by AppArmor (usr.sbin.dhclient): it may read its
# configuration only under /etc/dhcp/, and a file it cannot read is not an
# error -- it starts with its defaults and sends none of the options Orange
# authenticates on. "The configuration parses" therefore proves nothing; only
# the packet does. This runs the real client in a network namespace with no
# server and reads the DISCOVER and the Solicit off the peer interface.
#
# Needs root, tcpdump and dhclient; prints SKIP and exits 0 otherwise.
# NIVUUS_TEST_CONF_PARENT overrides where the configuration is rendered: point
# it outside /etc/dhcp to watch the test fail the way production would have.
set -uo pipefail
[ "$(id -u)" = 0 ] && command -v tcpdump >/dev/null && command -v dhclient >/dev/null ||
    { echo "SKIP: needs root, tcpdump and dhclient"; exit 0; }

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG=nvtest$$
NS=nvwire$$
TMP=$(mktemp -d)
CONF=$(mktemp -d "${NIVUUS_TEST_CONF_PARENT:-/etc/dhcp}/nivuus-test.XXXXXX")
trap 'ip netns del $NS 2>/dev/null; rm -rf "$TMP" "$CONF" /var/lib/dhcp/dhclient-$TAG* /run/dhclient-$TAG*' EXIT

PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; }
has() { if grep -qE "$2" "$TMP/cap"; then ok "$1"; else bad "$1"; fi; }
lacks() { if grep -qE "$2" "$TMP/cap"; then bad "$1"; else ok "$1"; fi; }

printf '[pppoe]\nusername=fti/abcdefg\npassword=secret\n' > "$TMP/profile"
# One call, as in production: both files must come from the same salt.
python3 "$ROOT/scripts/orange_dhcp_options.py" render \
    --profile "$TMP/profile" --templates "$ROOT/configs/network/orange" --out "$CONF" ||
    { echo "render failed"; exit 1; }

ip netns add $NS
ip -n $NS link add w0 type veth peer name w1
ip -n $NS link set w0 up; ip -n $NS link set w1 up
sleep 2   # let duplicate address detection finish: DHCPv6 needs the link-local
# One capture per protocol, one packet each: dhclient retransmits its DISCOVER
# after a random few seconds, and a shared two-packet capture would then hold
# two DISCOVERs and no Solicit.
capture() { # filter, then the client command
    ip netns exec $NS tcpdump -i w1 -n -l -vv -c 1 "$1" >> "$TMP/cap" 2>/dev/null &
    local cap=$!
    sleep 1
    shift
    ip netns exec $NS timeout 5 "$@" >/dev/null 2>&1
    sleep 1; kill $cap 2>/dev/null; wait $cap 2>/dev/null
}
: > "$TMP/cap"
capture 'udp port 67' dhclient -4 -d -1 -cf "$CONF/dhclient-orange-v4.conf" \
    -lf /var/lib/dhcp/dhclient-$TAG-4.lease -pf /run/dhclient-$TAG-4.pid w0
capture 'udp port 547' dhclient -6 -P -D LL -d -1 -cf "$CONF/dhclient-orange-v6.conf" \
    -lf /var/lib/dhcp/dhclient-$TAG-6.lease -df /var/lib/dhcp/dhclient-$TAG-6.duid \
    -pf /run/dhclient-$TAG-6.pid w0

echo "== DHCPv4 DISCOVER =="
has "client identifier is 01 + MAC"          'Client-ID \(61\), length 7: ether'
has "vendor class is sagem"                  'Vendor-Class \(60\), length 5: "sagem"'
has "user class names the Livebox"           'FSVDSL_livebox\.Internet\.softathome\.Livebox5'
has "option 90 is 70 bytes"                  '\(90\), length 70'
has "parameter request list has 12 entries"  'Parameter-Request \(55\), length 12'

echo "== DHCPv6 Solicit =="
has "DUID is link-layer"                     'client-ID hwaddr type 1 '
has "asks for a delegated prefix"            'IA_PD'
has "vendor class is sent"                   'vendor-class'
has "user class is sent"                     'user-class'
has "option 11 is sent"                      '\(auth|opt_11'
lacks "no address is requested"              'IA_NA'
lacks "no rapid commit"                      'rapid-commit'

echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
