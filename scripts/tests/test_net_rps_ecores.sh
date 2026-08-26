#!/bin/bash
# Tests for scripts/net-rps-ecores.sh against a fake sysfs tree.
#
# The script's whole job is picking the right CPUs, and getting that wrong is
# silent: a bad mask still writes, still returns 0, and only shows up as lost
# throughput or as softirq stealing a pinned vCPU during a game. So the mask is
# proven here, on a synthetic hybrid CPU, rather than trusted in production.
# Run: scripts/tests/test_net_rps_ecores.sh

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/net-rps-ecores.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
check() { case "$3" in *"$2"*) ok "$1" ;; *) bad "$1" "$3" ;; esac; }
check_absent() { case "$3" in *"$2"*) bad "$1" "$3" ;; *) ok "$1" ;; esac; }

# A hybrid CPU: `pcores` fast cores then `ecores` slow ones, and one interface
# carrying `queues` rx queues.
build() { # dir, pcores, ecores, iface, queues
    local root=$1 p=$2 e=$3 iface=$4 nq=$5 c=0 q
    rm -rf "$root"; mkdir -p "$root/devices/system/cpu" "$root/class/net/$iface/queues"
    while [ "$c" -lt "$((p + e))" ]; do
        mkdir -p "$root/devices/system/cpu/cpu$c/cpufreq"
        if [ "$c" -lt "$p" ]; then echo 5100000; else echo 3900000; fi \
            > "$root/devices/system/cpu/cpu$c/cpufreq/cpuinfo_max_freq"
        c=$((c + 1))
    done
    q=0
    while [ "$q" -lt "$nq" ]; do
        mkdir -p "$root/class/net/$iface/queues/rx-$q"
        echo 0 > "$root/class/net/$iface/queues/rx-$q/rps_cpus"
        q=$((q + 1))
    done
}

run() { NIVUUS_SYSFS="$1" "$SCRIPT" "${@:2}" 2>&1; }

echo "== mask picks the slow cores =="
build "$TMP/a" 16 8 ppp0 1
check "16 P + 8 E gives the top byte" "00ff0000" "$(run "$TMP/a" mask)"

build "$TMP/b" 4 4 ppp0 1
check "4 P + 4 E gives cpu4-7" "000000f0" "$(run "$TMP/b" mask)"

echo "== mask spans several 32-bit words =="
build "$TMP/c" 32 8 ppp0 1
check "cpu32-39 land in the high word" "000000ff,00000000" "$(run "$TMP/c" mask)"

echo "== a uniform CPU has no slow half =="
build "$TMP/d" 8 0 ppp0 1
check "every core qualifies when all are equal" "000000ff" "$(run "$TMP/d" mask)"

echo "== apply writes every rx queue =="
build "$TMP/e" 16 8 ppp0 4
out=$(run "$TMP/e" apply ppp0)
for q in 0 1 2 3; do
    check "rx-$q reported" "rx-$q rps_cpus=00ff0000" "$out"
    check "rx-$q written" "00ff0000" "$(cat "$TMP/e/class/net/ppp0/queues/rx-$q/rps_cpus")"
done

echo "== apply never touches the fast cores =="
build "$TMP/f" 16 8 ppp0 1
run "$TMP/f" apply ppp0 >/dev/null
mask=$(cat "$TMP/f/class/net/ppp0/queues/rx-0/rps_cpus")
# The low 16 bits are the P-cores; any bit set there would steal a pinned vCPU.
low=$(( 0x$mask & 0xffff ))
[ "$low" -eq 0 ] && ok "no P-core bit set" || bad "no P-core bit set" "$mask"

echo "== failures are loud =="
out=$(run "$TMP/f" apply nosuch); rc=$?
check "unknown interface is reported" "no rx queue on nosuch" "$out"
[ "$rc" -ne 0 ] && ok "unknown interface exits non-zero" || bad "unknown interface exits non-zero" "rc=$rc"

build "$TMP/g" 0 0 ppp0 1   # no cpufreq data at all
out=$(run "$TMP/g" apply ppp0); rc=$?
check "missing frequency data is reported" "no CPU frequency data" "$out"
[ "$rc" -ne 0 ] && ok "missing frequency data exits non-zero" || bad "missing frequency data exits non-zero" "rc=$rc"

out=$(run "$TMP/f"); rc=$?
check "no argument prints usage" "usage:" "$out"
[ "$rc" -eq 2 ] && ok "no argument exits 2" || bad "no argument exits 2" "rc=$rc"

echo "== status reads back =="
build "$TMP/h" 16 8 ppp0 2
run "$TMP/h" apply ppp0 >/dev/null
check "status shows the applied mask" "ppp0 rx-1 rps_cpus=00ff0000" "$(run "$TMP/h" status ppp0)"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
