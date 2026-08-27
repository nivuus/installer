#!/bin/bash
# Nivuus PCIe WiFi link guard — hardens and watches the PCIe chain that carries
# the two QCA9984 WiFi cards.
#
# Why this exists (2026-08-05): the host froze hard twice in 12 h (04/08 19:30,
# 05/08 03:50), each time leaving a FATAL firmware error record in ACPI BERT.
# The 04/08 freeze is fully traced in the kernel log and reads:
#
#   AER: Multiple Correctable error message received from 0000:0a:00.0
#   ath10k_pci 0000:0a:00.0: firmware crashed!
#   AER: Multiple Uncorrectable (Fatal) ... from 0000:09:03.0   (x30, ~10 s)
#   ath10k_pci ...: [00]: 0x0004a000 4294967295 ...             (link dead)
#   <journal stops — whole machine gone, WAN included>
#
# Both cards hang off a cheap ASMedia ASM1182e x1 Gen2 packet switch, and ASPM
# L1 was enabled on the single upstream link (root port 00:1d.0 <-> 08:00.0)
# while disabled everywhere downstream. Both freezes happened with the machine
# idle — exactly when L1 entry/exit is most frequent. ASPM L1 on ASMedia
# switches is a known source of link-recovery failures.
#
# The hardware watchdog (iTCO, 30 s) did NOT reset the machine on either freeze,
# so this is a platform-level halt, not a kernel hang: only prevention helps.
#
# Deployed as /usr/local/sbin/nivuus-pcie-wifi-link-guard.sh.
#   apply  — disable ASPM L1 on the chain (boot, idempotent)
#   check  — sample AER counters + link state, log any change (timer)
#   status — human-readable dump of the chain
#
# Fail-open by design: a missing device or sysfs attribute is never fatal.

set -uo pipefail

MODE="${1:-status}"
# Both roots are overridable so the guard can be exercised against a fake sysfs
# tree (see scripts/tests/test_pcie_wifi_link_guard.sh) — AER errors cannot be
# injected on demand, so the alerting path is otherwise untestable.
SYSFS="${NIVUUS_SYSFS:-/sys}"
STATE_DIR="${NIVUUS_STATE_DIR:-/run/nivuus-pcie-guard}"
DRIVER="$SYSFS/bus/pci/drivers/ath10k_pci"

log()  { echo "$*"; }
warn() { echo "$*" >&2; }

# Every PCI device between the root port and an ath10k endpoint, deduplicated
# and ordered upstream first. Derived from the sysfs topology so no bus address
# is ever hardcoded: the chain is whatever currently carries the WiFi cards.
pcie_chain() {
    local ep path seg
    for ep in "$DRIVER"/0000:*; do
        [ -e "$ep" ] || continue
        path=$(readlink -f "$ep") || continue
        # .../pci0000:00/0000:00:1d.0/0000:08:00.0/0000:09:03.0/0000:0a:00.0
        for seg in ${path//\// }; do
            case "$seg" in
                0000:*) echo "$seg" ;;
            esac
        done
    done | awk '!seen[$0]++'
}

# --- apply -----------------------------------------------------------------
# Disable ASPM L1 through the kernel's own interface (link/l1_aspm) rather than
# setpci: the kernel then keeps the setting consistent across both ends of the
# link and across its own ASPM re-evaluations.
# At boot the cards may not be bound to ath10k_pci yet. Rather than ordering the
# unit against udev — brittle, and ordering games around multi-user.target once
# cost this host a boot without Docker — wait for the chain to appear.
wait_for_chain() {
    local _attempt
    for _attempt in $(seq 1 "${NIVUUS_CHAIN_WAIT:-30}"); do
        [ -n "$(pcie_chain)" ] && return 0
        sleep 1
    done
    return 1
}

do_apply() {
    local dev f changed=0
    if ! wait_for_chain; then
        warn "no ath10k device found — nothing to harden"
        return 0
    fi
    for dev in $(pcie_chain); do
        f="$SYSFS/bus/pci/devices/$dev/link/l1_aspm"
        [ -w "$f" ] || continue
        if [ "$(cat "$f" 2>/dev/null)" = "1" ]; then
            if echo 0 > "$f" 2>/dev/null; then
                log "ASPM L1 disabled on $dev"
                changed=1
            else
                warn "failed to disable ASPM L1 on $dev"
            fi
        fi
    done
    [ "$changed" = 0 ] && log "ASPM L1 already disabled on the WiFi PCIe chain"
    return 0
}

# --- check -----------------------------------------------------------------
# AER counters are the leading indicator: the 04/08 freeze was preceded by
# correctable errors on the endpoint. Anything non-zero is logged at error
# priority so journald syncs it to disk immediately — a buffered message would
# be lost in the freeze it is meant to explain.
counters_of() {
    local dev="$1" kind f
    for kind in correctable nonfatal fatal; do
        f="$SYSFS/bus/pci/devices/$dev/aer_dev_$kind"
        [ -r "$f" ] || continue
        # "TOTAL_ERR_COR 3" / "TOTAL_ERR_FATAL 0" — the totals are enough here.
        awk -v k="$kind" '/^TOTAL_ERR/ && $2 != 0 {printf "%s=%s ", k, $2}' "$f"
    done
}

do_check() {
    local dev now prev sf link
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    for dev in $(pcie_chain); do
        now=$(counters_of "$dev")
        [ -n "$now" ] || continue

        sf="$STATE_DIR/${dev//:/_}.aer"
        prev=$(cat "$sf" 2>/dev/null)
        if [ "$now" != "$prev" ]; then
            link=$(lspci -s "${dev#0000:}" -vv 2>/dev/null |
                   awk -F'LnkSta:' '/LnkSta:/ {print $2; exit}')
            warn "PCIe AER on $dev: ${now}(LnkSta:${link:- n/a })"
            echo "$now" > "$sf" 2>/dev/null || true
        fi
    done
    return 0
}

# --- status ----------------------------------------------------------------
do_status() {
    local dev f
    for dev in $(pcie_chain); do
        f="$SYSFS/bus/pci/devices/$dev/link/l1_aspm"
        printf '%s  l1_aspm=%-5s %s\n' \
            "$dev" \
            "$([ -r "$f" ] && cat "$f" 2>/dev/null || echo 'n/a')" \
            "$(counters_of "$dev")"
        lspci -s "${dev#0000:}" -vv 2>/dev/null |
            awk '/LnkCtl:|LnkSta:/ {print "    " $0}'
    done
    return 0
}

case "$MODE" in
    apply)  do_apply  ;;
    check)  do_check  ;;
    status) do_status ;;
    *) warn "usage: $0 {apply|check|status}"; exit 2 ;;
esac
