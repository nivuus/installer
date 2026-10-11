#!/bin/bash
# Proves the 802.1p marking on a veth pair in a network namespace: what leaves
# the VLAN is captured on its parent, the only place the tag is visible.
set -uo pipefail
[ "$(id -u)" = 0 ] && command -v tcpdump >/dev/null || { echo "SKIP: needs root and tcpdump"; exit 0; }
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-orange.sh"
NS=nvcos$$; TMP=$(mktemp -d)
trap 'ip netns del $NS 2>/dev/null; rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
ip netns add $NS
ip -n $NS link add wan0 type veth peer name peer0
ip -n $NS link set wan0 up; ip -n $NS link set peer0 up
n() { ip netns exec $NS env NIVUUS_WAN_PARENT=wan0 NIVUUS_WAN_NO_FIREWALL=1 "$@"; }
n "$SCRIPT" prepare || { echo "prepare failed"; exit 1; }
# dhclient -6 binds the link-local address at once and dies if it is still
# tentative ("Can't bind to dhcp address", seen for real on 2026-10-11).
[ -z "$(n ip -6 addr show dev wan0.832 scope link tentative)" ] && [ -n "$(n ip -6 addr show dev wan0.832 scope link)" ] &&
    ok "prepare returns with a usable link-local address" || bad "link-local still tentative" "$(n ip -6 addr show dev wan0.832)"
n ip addr add 192.0.2.2/24 dev wan0.832
ip netns exec $NS tcpdump -i peer0 -e -n -l -c 40 vlan 832 > "$TMP/cap" 2>/dev/null &
CAP=$!; sleep 1
n ping -c 1 -W 1 192.0.2.1 >/dev/null 2>&1                         # triggers ARP
n python3 -c "
import socket
def sock(port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wan0.832')
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('0.0.0.0', port))
    return s
plain = sock()
plain.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x10)           # IPTOS_LOWDELAY -> priority 6
plain.sendto(b'x', ('192.0.2.255', 9999))                          # ordinary traffic
sock(68).sendto(b'x', ('255.255.255.255', 67))                     # DHCPv4 shape
"
n ping -6 -c 1 -W 1 -I wan0.832 ff02::2 >/dev/null 2>&1            # ordinary ICMPv6 echo
n ping -6 -c 1 -W 1 fe80::1%wan0.832 >/dev/null 2>&1               # unknown neighbour: triggers an NS
sleep 2; kill $CAP 2>/dev/null; wait $CAP 2>/dev/null
pcp() { grep -E "$1" "$TMP/cap" | head -1 | grep -oE "vlan 832, p [0-9]" | grep -oE "[0-9]$"; }
[ "$(pcp 'ethertype ARP')" = 6 ] && ok "ARP leaves with priority 6" || bad "ARP priority" "$(pcp 'ethertype ARP')"
[ "$(pcp '\.68 > .*\.67')" = 6 ] && ok "DHCPv4 leaves with priority 6" || bad "DHCPv4 priority" "$(pcp '\.68 > .*\.67')"
[ "$(pcp '\.9999')" = 0 ] && ok "low-delay traffic leaves with priority 0" || bad "ordinary traffic priority" "$(pcp '\.9999')"
[ "$(pcp 'echo request')" = 0 ] && ok "ICMPv6 echo leaves with priority 0" || bad "ICMPv6 echo priority" "$(pcp 'echo request')"
[ "$(pcp 'neighbor solicitation|router solicitation')" = 6 ] && ok "ND leaves with priority 6" || bad "ND priority" "$(pcp 'neighbor solicitation|router solicitation')"
n "$SCRIPT" teardown; n ip link show wan0.832 >/dev/null 2>&1 && bad "teardown" "interface still there" || ok "teardown removes the interface"
echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
