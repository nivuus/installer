#!/bin/sh
# Owns the Orange DHCP interface (VLAN 832): creation, 802.1p marking, firewalld
# zone, shaper, liveness. NetworkManager does not manage this interface.
#
# Orange wants priority 6 on DHCPv4, DHCPv6, ARP and ICMPv6 RS/NS/NA, and 0 on
# everything else. The marking is done here, on the VLAN, by tc: it then holds
# for any DHCP client, for the kernel's own ARP and ND, and for forwarded
# traffic, whose TOS would otherwise map to priority 6 (IPTOS_LOWDELAY).
set -u
# NIVUUS_TEST_PATH lets the test suite put fakes ahead of the real tools.
PATH=${NIVUUS_TEST_PATH:+$NIVUUS_TEST_PATH:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PARENT=${NIVUUS_WAN_PARENT:-enp5s0}
IF=${NIVUUS_WAN_IF:-$PARENT.832}
[ -r /etc/default/nivuus-wan-cake ] && . /etc/default/nivuus-wan-cake
RATE=${NIVUUS_WAN_RATE:-${RATE:-780mbit}}
STATE=${NIVUUS_WAN_STATE_DIR:-/run/nivuus}

mark() {
    f="tc filter add dev $IF egress"
    tc qdisc replace dev "$IF" clsact || return 1
    tc filter del dev "$IF" egress 2>/dev/null
    $f pref 10 protocol arp  matchall action skbedit priority 6 pass &&
    $f pref 20 protocol ip   flower ip_proto udp src_port 68 dst_port 67 action skbedit priority 6 pass &&
    $f pref 30 protocol ipv6 flower ip_proto udp src_port 546 dst_port 547 action skbedit priority 6 pass &&
    $f pref 40 protocol ipv6 flower ip_proto icmpv6 type 133 action skbedit priority 6 pass &&
    $f pref 41 protocol ipv6 flower ip_proto icmpv6 type 135 action skbedit priority 6 pass &&
    $f pref 42 protocol ipv6 flower ip_proto icmpv6 type 136 action skbedit priority 6 pass &&
    $f pref 100 protocol all matchall action skbedit priority 0 pass
}

prepare() {
    ip link show "$IF" >/dev/null 2>&1 ||
        ip link add link "$PARENT" name "$IF" type vlan id 832 egress-qos-map 0:0 6:6 || return 1
    echo 0 > "/proc/sys/net/ipv6/conf/$IF/accept_ra"
    echo 0 > "/proc/sys/net/ipv6/conf/$IF/disable_ipv6"
    [ "${NIVUUS_WAN_NO_FIREWALL:-0}" = 1 ] || firewall-cmd --zone=external --add-interface="$IF" || return 1
    mark || return 1
    tc qdisc replace dev "$IF" root cake bandwidth "$RATE" besteffort nat dual-srchost ethernet ether-vlan || return 1
    ip link set "$IF" up || return 1
    # dhclient -6 binds the link-local address at once and exits if duplicate
    # address detection has not finished ("Can't bind to dhcp address").
    n=0
    while [ "$n" -lt 50 ] && { ip -6 addr show dev "$IF" scope link tentative | grep -q inet6 ||
                               ! ip -6 addr show dev "$IF" scope link | grep -q inet6; }; do
        sleep 0.1; n=$((n + 1))
    done
}

teardown() {
    [ "${NIVUUS_WAN_NO_FIREWALL:-0}" = 1 ] || firewall-cmd --zone=external --remove-interface="$IF" >/dev/null 2>&1
    ip link del "$IF" 2>/dev/null
    rm -f "$STATE/wan-orange.failures"
    return 0
}

status() {
    ip -d -br addr show "$IF" 2>&1
    ip route show default dev "$IF" 2>/dev/null
    tc -s filter show dev "$IF" egress 2>/dev/null | grep -E "pref|Sent"
    cat "$STATE/wan-orange.prefix" 2>/dev/null
}

# Orange asks for a restart from DISCOVER once the gateway has been silent for
# 150 s: three consecutive failures of a check run every 50 s.
#
# The probe is ARP, as on a Livebox, never ICMP: a gateway may ignore echo
# requests for ever while routing perfectly, and each restart sends a RELEASE
# and drops the route. A router cannot ignore ARP and still route.
check() {
    mkdir -p "$STATE"
    gw=$(ip route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')
    # No default route means no lease yet: the client is still asking and
    # retries by itself. Restarting it would only add RELEASEs and DISCOVERs.
    if [ -z "$gw" ]; then rm -f "$STATE/wan-orange.failures"; return 0; fi
    if arping -q -I "$IF" -c 2 -w 3 "$gw" >/dev/null 2>&1; then
        rm -f "$STATE/wan-orange.failures"; return 0
    fi
    n=$(( $(cat "$STATE/wan-orange.failures" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$STATE/wan-orange.failures"
    echo "wan-orange: gateway $gw silent, failure $n" >&2
    [ "$n" -lt 3 ] && return 0
    rm -f "$STATE/wan-orange.failures"
    echo "wan-orange: rendering fresh options and restarting both DHCP clients" >&2
    systemctl restart nivuus-wan-orange-options.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service
}

case "${1:-}" in
    prepare|teardown|status|check) "$1" ;;
    *) echo "usage: ${0##*/} prepare|teardown|status|check" >&2; exit 2 ;;
esac
