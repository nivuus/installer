#!/bin/sh
# Switches the WAN between PPPoE (VLAN 835) and Orange DHCP (VLAN 832).
#
# Run by the operator, never by an agent: it cuts the uplink. It detaches at
# once, logs to $LOG, and rolls back to PPPoE by itself if DHCP does not bring
# the Internet back. It refuses to start unless the phone tether works, because
# that link is what keeps the operator and any session reachable meanwhile.
# Orange rejects a PPPoE reconnection for minutes after a teardown: the
# rollback waits COOLDOWN_S after the PADT and retries every RETRY_S.
set -u
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOG=${NIVUUS_CUTOVER_LOG:-/var/log/nivuus-wan-cutover.log}
PROFILE=pppoe-enp6s0.835
IF=enp5s0.832
UNITS="nivuus-wan-orange.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service nivuus-wan-orange-check.timer"
PROBE=https://www.gstatic.com/generate_204
WAIT_S=150 COOLDOWN_S=300 RETRY_S=360
MODE=${1:-}; DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
SD="busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"

log() { echo "$(date '+%F %T') $*"; }
run() { if [ "$DRY" = 1 ]; then echo "would run: $*"; else log "+ $*"; "$@"; fi; }
http_ok() { [ "$(curl -4 -s -m 8 --interface "$1" -o /dev/null -w '%{http_code}' "$PROBE")" = 204 ]; }
tether() { ls /sys/class/net | grep -E '^enx' | head -1; }
tether_ok() {
    [ -n "${NIVUUS_TETHER_OK:-}" ] && { [ "$NIVUUS_TETHER_OK" = 1 ]; return; }
    t=$(tether); [ -n "$t" ] && http_ok "$t"
}
# A cutover without its units would cut PPPoE, wait, and roll back: minutes of
# outage for nothing.
units_ok() { for u in $UNITS; do [ -f "${NIVUUS_UNIT_DIR:-/etc/systemd/system}/$u" ] || return 1; done; }
start_units() { for u in $UNITS; do run $SD StartUnit ss "$u" replace; done; }
stop_units()  { for u in nivuus-wan-orange-check.timer nivuus-wan-orange-dhcp6.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange.service; do run $SD StopUnit ss "$u" replace; done; }
enable_units()  { run $SD EnableUnitFiles asbb 4 $UNITS false true; }
disable_units() { run $SD DisableUnitFiles asb 4 $UNITS false; }

pppoe_up() {
    run nmcli con modify "$PROFILE" connection.autoconnect yes
    since=$1
    wait_s=$(( COOLDOWN_S - ($(date +%s) - since) ))
    [ "$DRY" = 0 ] && [ "$wait_s" -gt 0 ] && { log "waiting ${wait_s}s for Orange's cooldown"; sleep "$wait_s"; }
    while :; do
        run nmcli con up "$PROFILE"
        [ "$DRY" = 1 ] && return 0
        sleep 15; http_ok ppp0 && { log "PPPoE is back"; return 0; }
        log "PPPoE not back, next attempt in ${RETRY_S}s"; sleep "$RETRY_S"
    done
}

to_dhcp() {
    units_ok || { echo "refusing: the nivuus-wan-orange units are not installed" >&2; return 1; }
    tether_ok || { echo "refusing: no working phone tether (plug it in, enable USB tethering)" >&2; return 1; }
    run nmcli con modify "$PROFILE" connection.autoconnect no
    run nmcli con down "$PROFILE"
    down_at=$(date +%s)
    start_units
    [ "$DRY" = 1 ] && { enable_units; return 0; }
    while [ $(( $(date +%s) - down_at )) -lt "$WAIT_S" ]; do
        sleep 5
        if http_ok "$IF"; then
            log "Internet reachable through $IF: $(ip -br addr show $IF)"
            enable_units; log "DHCP mode is now the boot default"; return 0
        fi
    done
    log "no Internet through $IF after ${WAIT_S}s: rolling back to PPPoE"
    journalctl -u nivuus-wan-orange-dhcp4.service -u nivuus-wan-orange-dhcp6.service --since "-5min" --no-pager -o cat | grep -viE "auth|option 90" | tail -40
    stop_units
    pppoe_up "$down_at"
    return 3
}

to_pppoe() {
    disable_units
    stop_units
    pppoe_up "$(date +%s)"
}

case "$MODE" in
    to-dhcp|to-pppoe) ;;
    *) echo "usage: ${0##*/} to-dhcp|to-pppoe [--dry-run]" >&2; exit 2 ;;
esac
fn=$(echo "$MODE" | tr - _)
if [ "$DRY" = 1 ]; then $fn; exit $?; fi
if [ "${3:-}${2:-}" != "--detached" ]; then
    [ "$MODE" = to-dhcp ] && ! units_ok && { echo "refusing: the nivuus-wan-orange units are not installed" >&2; exit 1; }
    [ "$MODE" = to-dhcp ] && ! tether_ok && { echo "refusing: no working phone tether" >&2; exit 1; }
    setsid nohup "$0" "$MODE" --detached >> "$LOG" 2>&1 < /dev/null &
    echo "started in the background; follow it with: tail -f $LOG"; exit 0
fi
log "=== $MODE ==="; $fn; rc=$?; log "=== $MODE finished, rc=$rc ==="; exit $rc
