#!/bin/sh
# Switches the WAN between PPPoE (VLAN 835) and Orange DHCP (VLAN 832).
#
# Run by the operator, never by an agent: it cuts the uplink. It detaches at
# once, logs to $LOG, and rolls back to PPPoE by itself if DHCP does not bring
# the Internet back. It refuses to start unless the phone tether works, because
# that link is what keeps the operator and any session reachable meanwhile.
#
# Nothing persistent changes until DHCP is proven: PPPoE keeps `autoconnect
# yes` and the DHCP units stay disabled for the whole attempt, so a reboot or a
# freeze in the middle boots back on PPPoE. `nmcli con down` is enough to keep
# NetworkManager from redialling meanwhile: a manual disconnect blocks
# autoconnect until the next manual activation.
#
# Orange rejects a PPPoE reconnection for minutes after a teardown: the
# rollback waits COOLDOWN_S after the PADT and retries every RETRY_S. A PPPoE
# session that is up is never activated again: `nmcli con up` on a live session
# tears it down first and buys another cooldown.
set -u
# NIVUUS_TEST_PATH lets the test suite put fakes ahead of the real tools.
PATH=${NIVUUS_TEST_PATH:+$NIVUUS_TEST_PATH:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOG=${NIVUUS_CUTOVER_LOG:-/var/log/nivuus-wan-cutover.log}
LOCK=${NIVUUS_CUTOVER_LOCK:-/run/nivuus-wan-cutover.lock}
UNIT_DIR=${NIVUUS_UNIT_DIR:-/etc/systemd/system}
PROFILE=pppoe-enp6s0.835
IF=enp5s0.832
START_UNITS="nivuus-wan-orange.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service nivuus-wan-orange-check.timer"
STOP_UNITS="nivuus-wan-orange-check.timer nivuus-wan-orange-dhcp6.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-options.service nivuus-wan-orange.service"
PROBE=https://www.gstatic.com/generate_204
WAIT_S=${NIVUUS_CUTOVER_WAIT_S:-150}
COOLDOWN_S=${NIVUUS_CUTOVER_COOLDOWN_S:-300}
RETRY_S=${NIVUUS_CUTOVER_RETRY_S:-360}
SD="busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"

usage() { echo "usage: ${0##*/} to-dhcp|to-pppoe [--dry-run]" >&2; exit 2; }
MODE=''; DRY=0; DETACHED=0
for arg in "$@"; do
    case "$arg" in
        to-dhcp|to-pppoe) MODE=$arg ;;
        --dry-run) DRY=1 ;;
        --detached) DETACHED=1 ;;
        *) usage ;;
    esac
done
[ -n "$MODE" ] || usage

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
units_ok() { for u in $STOP_UNITS; do [ -f "$UNIT_DIR/$u" ] || return 1; done; }
pppoe_active() { [ "$(nmcli -g GENERAL.STATE con show "$PROFILE" 2>/dev/null)" = activated ]; }
start_units() { for u in $START_UNITS; do run $SD StartUnit ss "$u" replace; done; }
stop_units()  { for u in $STOP_UNITS; do run $SD StopUnit ss "$u" replace; done; }
enable_units()  { run $SD EnableUnitFiles asbb 4 $START_UNITS false true; }
disable_units() { run $SD DisableUnitFiles asb 4 $START_UNITS false; }

pppoe_up() { # $1: when PPPoE went down
    run nmcli con modify "$PROFILE" connection.autoconnect yes
    if pppoe_active; then log "PPPoE is already up: left alone"; return 0; fi
    wait_s=$(( COOLDOWN_S - ($(date +%s) - $1) ))
    [ "$DRY" = 0 ] && [ "$wait_s" -gt 0 ] && { log "waiting ${wait_s}s for Orange's cooldown"; sleep "$wait_s"; }
    while :; do
        run nmcli con up "$PROFILE"
        [ "$DRY" = 1 ] && return 0
        sleep 15
        if pppoe_active; then
            if http_ok ppp0; then log "PPPoE is back"; else log "PPPoE is up but the probe fails: session left alone"; fi
            return 0
        fi
        log "PPPoE not back, next attempt in ${RETRY_S}s"; sleep "$RETRY_S"
    done
}

rollback() {
    stop_units
    pppoe_up "$DOWN_AT"
}

to_dhcp() {
    units_ok || { echo "refusing: the nivuus-wan-orange units are not installed" >&2; return 1; }
    tether_ok || { echo "refusing: no working phone tether (plug it in, enable USB tethering)" >&2; return 1; }
    pppoe_active || { echo "refusing: PPPoE is not the active uplink, there is nothing to cut over from" >&2; return 1; }
    run nmcli con down "$PROFILE" || { log "PPPoE could not be taken down: nothing else was changed"; return 1; }
    DOWN_AT=$(date +%s)
    [ "$DRY" = 1 ] || trap 'log "interrupted: rolling back"; trap - INT TERM HUP; rollback; exit 130' INT TERM HUP
    start_units
    if [ "$DRY" = 0 ]; then
        proven=0
        # Probe at least once, whatever the clock says: a deadline already
        # passed must not turn into a rollback that never looked.
        while :; do
            sleep 5
            if http_ok "$IF"; then proven=1; break; fi
            [ $(( $(date +%s) - DOWN_AT )) -lt "$WAIT_S" ] || break
        done
        if [ "$proven" = 0 ]; then
            log "no Internet through $IF after ${WAIT_S}s: rolling back to PPPoE"
            journalctl -u nivuus-wan-orange-dhcp4.service -u nivuus-wan-orange-dhcp6.service --since "-5min" --no-pager -o cat | tail -40
            trap - INT TERM HUP
            rollback
            return 3
        fi
        log "Internet reachable through $IF"
        trap - INT TERM HUP
    fi
    enable_units
    run nmcli con modify "$PROFILE" connection.autoconnect no
    [ "$DRY" = 1 ] || log "DHCP mode is now the boot default"
}

to_pppoe() {
    disable_units
    stop_units
    pppoe_up "$(date +%s)"
}

fn=$(echo "$MODE" | tr - _)
if [ "$DRY" = 1 ]; then $fn; exit $?; fi
if [ "$DETACHED" = 0 ]; then
    if [ "$MODE" = to-dhcp ]; then
        units_ok || { echo "refusing: the nivuus-wan-orange units are not installed" >&2; exit 1; }
        tether_ok || { echo "refusing: no working phone tether" >&2; exit 1; }
    fi
    umask 077
    setsid nohup "$0" "$MODE" --detached >> "$LOG" 2>&1 < /dev/null &
    echo "started in the background; follow it with: tail -f $LOG"; exit 0
fi
exec 9> "$LOCK"
flock -n 9 || { echo "refusing: another cutover is running" >&2; exit 1; }
log "=== $MODE ==="; $fn; rc=$?; log "=== $MODE finished, rc=$rc ==="; exit $rc
