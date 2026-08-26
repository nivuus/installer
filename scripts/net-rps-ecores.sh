#!/bin/bash
# Spread an interface's receive processing across the E-cores, through RPS.
#
# The WAN is a single PPPoE session, so every inbound packet lands on one
# hardware queue and therefore one CPU, which becomes the ceiling. Measured
# 2026-08-26 against ash-speed.hetzner.com, four alternating passes:
#
#     RPS off   147 / 121 / 137 / 122 Mbit/s   (cpu17 alone, 26-29 % softirq)
#     RPS on    203 / 235 / 180 / 227 Mbit/s
#
# +60 %, for one sysfs write. The core was the bottleneck, not the line.
#
# RPS never splits a single TCP flow across cores -- that would reorder the
# packets. The gain comes from moving the TCP/IP half onto a second core while
# the first keeps doing PPPoE decapsulation, so a mono-flow download still ends
# up with one hot core; it is just no longer doing all of the work alone.
#
# E-cores and not P-cores because the Windows VM pins every P-core while it
# runs (see vm-cpu-partition.sh): a softirq landing there would compete with a
# vCPU. They are detected by their lower max frequency, never hard-coded --
# this box has no /sys/devices/system/cpu/types to ask.
#
# Called by /etc/ppp/ip-up.d/nivuus-rps-ecores: ppp0 is destroyed and recreated
# at every reconnection, and its rps_cpus goes back to 0 with it.
set -uo pipefail

SYSFS="${NIVUUS_SYSFS:-/sys}"

usage() {
    echo "usage: ${0##*/} apply <interface>...   # write the E-core mask" >&2
    echo "       ${0##*/} status <interface>...  # read it back" >&2
    echo "       ${0##*/} mask                   # print the mask alone" >&2
}

# Hex RPS mask of every CPU whose max frequency is the lowest on the machine.
# Emitted as comma-separated 32-bit words, most significant first, the format
# rps_cpus reads back.
ecore_mask() {
    local dir cpu freq min='' w b maxword=0 out=''
    local -a freqs=() words=()

    for dir in "$SYSFS"/devices/system/cpu/cpu[0-9]*; do
        cpu=${dir##*/cpu}
        freq=$(cat "$dir/cpufreq/cpuinfo_max_freq" 2>/dev/null) || continue
        [ -n "$freq" ] || continue
        freqs[cpu]=$freq
        if [ -z "$min" ] || [ "$freq" -lt "$min" ]; then min=$freq; fi
    done
    [ -n "$min" ] || return 1

    for cpu in "${!freqs[@]}"; do
        [ "${freqs[cpu]}" = "$min" ] || continue
        w=$((cpu / 32)); b=$((cpu % 32))
        words[w]=$(( ${words[w]:-0} | (1 << b) ))
        [ "$w" -gt "$maxword" ] && maxword=$w
    done

    for ((w = maxword; w >= 0; w--)); do
        out+=$(printf '%08x' "${words[w]:-0}")
        [ "$w" -gt 0 ] && out+=','
    done
    printf '%s\n' "$out"
}

queues() {
    local iface=$1 q found=1
    for q in "$SYSFS/class/net/$iface"/queues/rx-*; do
        [ -e "$q/rps_cpus" ] || continue
        printf '%s\n' "$q"
        found=0
    done
    return $found
}

apply() {
    local iface=$1 mask q rc=0
    mask=$(ecore_mask) || { echo "${0##*/}: no CPU frequency data" >&2; return 1; }
    local -a qs
    mapfile -t qs < <(queues "$iface")
    [ ${#qs[@]} -gt 0 ] || { echo "${0##*/}: no rx queue on $iface" >&2; return 1; }
    for q in "${qs[@]}"; do
        if printf '%s' "$mask" > "$q/rps_cpus" 2>/dev/null; then
            echo "$iface ${q##*/} rps_cpus=$mask"
        else
            echo "${0##*/}: cannot write $q/rps_cpus" >&2
            rc=1
        fi
    done
    return $rc
}

status() {
    local iface=$1 q rc=0
    local -a qs
    mapfile -t qs < <(queues "$iface")
    [ ${#qs[@]} -gt 0 ] || { echo "${0##*/}: no rx queue on $iface" >&2; return 1; }
    for q in "${qs[@]}"; do
        echo "$iface ${q##*/} rps_cpus=$(cat "$q/rps_cpus")"
    done
    return $rc
}

cmd=${1:-}
[ $# -gt 0 ] && shift

case "$cmd" in
    mask)
        ecore_mask || exit 1
        ;;
    apply|status)
        [ $# -ge 1 ] || { usage; exit 2; }
        rc=0
        for iface in "$@"; do "$cmd" "$iface" || rc=1; done
        exit $rc
        ;;
    *)
        usage
        exit 2
        ;;
esac
