#!/bin/bash
# Nivuus installer — bring up the setup hotspot (or Ethernet fallback).
#
# Started by nivuus-ap.service at live boot, BEFORE the web portal. Detects an
# AP-capable WiFi interface and starts hostapd + dnsmasq on 10.42.0.1/24 with a
# captive-portal DNS. If no AP-capable WiFi exists, falls back to serving DHCP
# on the first wired interface so the portal is still reachable. When WiFi works
# we also keep listening on Ethernet, maximising the chance the user gets in.
set -u

AP_IP="10.42.0.1"
AP_CIDR="${AP_IP}/24"
DHCP_RANGE="10.42.0.10,10.42.0.200,255.255.255.0,12h"
SSID_PREFIX="Nivuus-Setup"
TMPL_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="/run/nivuus-ap"
HOSTAPD_CONF="${RUN_DIR}/hostapd.conf"
DNSMASQ_CONF="${RUN_DIR}/dnsmasq.conf"
STATE_FILE="${RUN_DIR}/state"

mkdir -p "$RUN_DIR"
log() { echo "[nivuus-ap] $*"; }

# --- helpers --------------------------------------------------------------- #
ap_capable_iface() {
    # Echo the first WiFi interface whose phy supports AP mode. Empty if none.
    command -v iw >/dev/null 2>&1 || return 0
    local iface phy
    while read -r iface; do
        [ -z "$iface" ] && continue
        phy=$(iw dev "$iface" info 2>/dev/null | awk '/wiphy/ {print "phy"$2}')
        [ -z "$phy" ] && continue
        if iw phy "$phy" info 2>/dev/null | awk '/Supported interface modes/{f=1;next} /valid interface combinations/{f=0} f' | grep -qw "AP"; then
            echo "$iface"; return 0
        fi
    done < <(iw dev 2>/dev/null | awk '/Interface/ {print $2}')
}

first_wired() {
    for d in /sys/class/net/*; do
        local n; n=$(basename "$d")
        case "$n" in lo|veth*|docker*|br-*|vnet*|ppp*|wl*) continue ;; esac
        [ -d "$d/wireless" ] && continue
        echo "$n"; return 0
    done
}

gen_pass() {
    # 10-char alphanumeric setup password (avoids ambiguous chars).
    tr -dc 'A-HJ-NP-Za-km-z2-9' < /dev/urandom | head -c 10
}

show_console() {
    local msg="$1"
    # Banner on every TTY so the operator sees how to connect. English: the
    # operator's language is not known before the wizard asks for it.
    for tty in /dev/tty1 /dev/console; do
        [ -w "$tty" ] && printf '\n%s\n' "$msg" > "$tty" 2>/dev/null
    done
}

# --- DHCP/DNS (shared by both modes) --------------------------------------- #
write_dnsmasq() {
    local iface="$1"
    cat > "$DNSMASQ_CONF" <<EOF
interface=${iface}
bind-interfaces
except-interface=lo
dhcp-range=${DHCP_RANGE}
dhcp-option=3,${AP_IP}
dhcp-option=6,${AP_IP}
# Captive portal: resolve every name to the portal so detection fires.
address=/#/${AP_IP}
no-resolv
log-dhcp
EOF
}

start_dnsmasq() {
    pkill -f "dnsmasq.*${DNSMASQ_CONF}" 2>/dev/null
    dnsmasq -C "$DNSMASQ_CONF" --pid-file="${RUN_DIR}/dnsmasq.pid"
}

# --- captive redirect (nftables) ------------------------------------------- #
setup_captive_nft() {
    local iface="$1"
    nft -f - <<EOF 2>/dev/null
table ip nivuus_captive {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname "${iface}" tcp dport 80 ip daddr != ${AP_IP} dnat to ${AP_IP}:80
    }
}
EOF
}

# --- modes ----------------------------------------------------------------- #
start_wifi_ap() {
    local iface="$1"
    local suffix mac
    mac=$(cat "/sys/class/net/${iface}/address" 2>/dev/null | tr -d ':')
    suffix=$(echo "${mac:6:4}" | tr 'a-f' 'A-F'); [ -z "$suffix" ] && suffix="0000"
    local ssid="${SSID_PREFIX}-${suffix}"
    local passphrase; passphrase=$(gen_pass)

    sed -e "s/@IFACE@/${iface}/" -e "s/@SSID@/${ssid}/" \
        -e "s/@PASSPHRASE@/${passphrase}/" \
        "${TMPL_DIR}/hostapd-setup.conf.tmpl" > "$HOSTAPD_CONF"

    ip link set "$iface" up 2>/dev/null
    ip addr flush dev "$iface" 2>/dev/null
    ip addr add "$AP_CIDR" dev "$iface" 2>/dev/null

    write_dnsmasq "$iface"; start_dnsmasq
    setup_captive_nft "$iface"

    pkill -f "hostapd.*${HOSTAPD_CONF}" 2>/dev/null
    hostapd -B "$HOSTAPD_CONF"

    echo "wifi ${iface} ${ssid} ${passphrase}" > "$STATE_FILE"
    show_console "==== Nivuus — connect to the installer ====
  Wi-Fi:    ${ssid}
  Password: ${passphrase}
  Then open http://${AP_IP}/ (or wait for the page to open by itself)
=============================================="
}

start_ethernet_fallback() {
    local iface="$1"
    ip link set "$iface" up 2>/dev/null
    ip addr add "$AP_CIDR" dev "$iface" 2>/dev/null
    write_dnsmasq "$iface"; start_dnsmasq
    setup_captive_nft "$iface"
    echo "ethernet ${iface}" >> "$STATE_FILE"
    local shown; shown=$(ip -4 addr show "$iface" | awk '/inet /{print $2}' | head -1)
    show_console "==== Nivuus — connect to the installer (Ethernet) ====
  Plug a cable into ${iface}, then open:
    http://${AP_IP}/   (or http://${shown%/*}/)
=========================================================="
}

# --- main ------------------------------------------------------------------ #
: > "$STATE_FILE"
WIFI_IFACE=$(ap_capable_iface)

if [ -n "$WIFI_IFACE" ]; then
    log "AP-capable WiFi found: $WIFI_IFACE"
    start_wifi_ap "$WIFI_IFACE"
else
    log "No AP-capable WiFi interface detected."
fi

WIRED=$(first_wired)
if [ -z "$WIFI_IFACE" ] && [ -n "$WIRED" ]; then
    log "Falling back to Ethernet on $WIRED"
    start_ethernet_fallback "$WIRED"
elif [ -z "$WIFI_IFACE" ] && [ -z "$WIRED" ]; then
    show_console "==== Nivuus ====
  No access-point-capable Wi-Fi and no Ethernet interface found.
  The portal listens on every interface, port 80."
fi

exit 0
