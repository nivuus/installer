#!/bin/bash
# Proves the 802.1p marking in a network namespace: what leaves the VLAN is
# captured on its parent, the only place the tag is visible.
#
# The parent is a dummy interface on purpose. Like the real WAN NIC (igc), it
# does not insert VLAN tags in hardware, and that changes what a classifier
# sees: the VLAN device then announces an 18-byte link header, so a frame
# written whole on a packet socket -- which is how dhclient -4 sends -- carries
# a network-header offset 4 bytes too far. A classifier that parses from that
# offset misses the frame. On 2026-10-11 the marking was proven on a veth
# (hardware tagging) with a UDP socket, passed, and the real DISCOVER left with
# priority 0: Orange parked the client.
set -uo pipefail
[ "$(id -u)" = 0 ] && command -v tcpdump >/dev/null || { echo "SKIP: needs root and tcpdump"; exit 0; }
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-orange.sh"
NS=nvcos$$; TMP=$(mktemp -d)
trap 'ip netns del $NS 2>/dev/null; rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
ip netns add $NS
ip -n $NS link add wan0 type dummy
ip -n $NS link set wan0 arp on multicast on up   # a dummy is NOARP by default: no ARP, no ND
n() { ip netns exec $NS env NIVUUS_WAN_PARENT=wan0 NIVUUS_WAN_NO_FIREWALL=1 NIVUUS_WAN_STATE_DIR="$TMP/state" "$@"; }
ip netns exec $NS tcpdump -i wan0 -e -n -l -c 60 vlan 832 > "$TMP/cap" 2>/dev/null &
CAP=$!; sleep 1
n "$SCRIPT" prepare || { echo "prepare failed"; exit 1; }
# dhclient -6 binds the link-local address at once and dies if it is still
# tentative ("Can't bind to dhcp address", seen for real on 2026-10-11).
[ -z "$(n ip -6 addr show dev wan0.832 scope link tentative)" ] && [ -n "$(n ip -6 addr show dev wan0.832 scope link)" ] &&
    ok "prepare returns with a usable link-local address" || bad "link-local still tentative" "$(n ip -6 addr show dev wan0.832)"
n ip addr add 192.0.2.2/24 dev wan0.832
n ping -c 1 -W 1 192.0.2.1 >/dev/null 2>&1                         # triggers ARP
n python3 -c "
import socket, struct
IF = 'wan0.832'
def sock(family=socket.AF_INET, port=0):
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, IF.encode())
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', port))
    return s
plain = sock()
plain.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x10)           # IPTOS_LOWDELAY -> priority 6
plain.sendto(b'x', ('192.0.2.255', 9999))                          # ordinary traffic
sock(port=68).sendto(b'x', ('255.255.255.255', 67))                # DHCPv4 from a UDP socket
sock(socket.AF_INET6, 546).sendto(b'x', ('ff02::1:2', 547, 0, socket.if_nametoindex(IF)))  # DHCPv6
# DHCPv4 the way dhclient sends it: a whole frame on a packet socket.
mac = bytes.fromhex(open('/sys/class/net/%s/address' % IF).read().strip().replace(':', ''))
udp = struct.pack('!HHHH', 68, 67, 8 + 300, 0) + bytes(300)
ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0x10, 20 + len(udp), 0, 0, 128, 17, 0, bytes(4), b'\xff' * 4)
raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
raw.bind((IF, 0))
raw.send(b'\xff' * 6 + mac + b'\x08\x00' + ip + udp)
"
n ping -6 -c 1 -W 1 -I wan0.832 ff02::2 >/dev/null 2>&1            # ordinary ICMPv6 echo
n ping -6 -c 1 -W 1 fe80::1%wan0.832 >/dev/null 2>&1               # unknown neighbour: triggers an NS
sleep 2; kill $CAP 2>/dev/null; wait $CAP 2>/dev/null
pcp() { grep -E "$1" "$TMP/cap" | head -1 | grep -oE "vlan 832, p [0-9]" | grep -oE "[0-9]$"; }
is() { [ "$(pcp "$2")" = "$3" ] && ok "$1 with priority $3" || bad "$1 priority" "$(pcp "$2") in: $(grep -E "$2" "$TMP/cap" | head -1 | cut -c1-140)"; }
is "ARP leaves" 'ethertype ARP' 6
is "DHCPv4 from a packet socket (dhclient) leaves" ' 0\.0\.0\.0\.68 > 255\.255\.255\.255\.67' 6
is "DHCPv4 from a UDP socket leaves" ' 192\.0\.2\.2\.68 > 255\.255\.255\.255\.67' 6
is "DHCPv6 leaves" '\.546 > ff02::1:2\.547' 6
is "neighbour solicitation leaves" 'neighbor solicitation' 6
is "low-delay traffic leaves" '\.9999' 0
is "ICMPv6 echo leaves" 'echo request' 0
is "multicast listener report leaves" 'multicast listener report' 0
n "$SCRIPT" prepare && ok "prepare can be run again on a live interface" || bad "second prepare" "rc=$?"
n "$SCRIPT" teardown; n ip link show wan0.832 >/dev/null 2>&1 && bad "teardown" "interface still there" || ok "teardown removes the interface"
echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
