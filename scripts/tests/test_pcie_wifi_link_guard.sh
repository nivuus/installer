#!/bin/bash
# Tests for scripts/pcie-wifi-link-guard.sh against a fake sysfs tree.
#
# The guard exists to catch a failure (PCIe AER storm on the WiFi chain) that
# cannot be reproduced on demand, so the alerting path has to be proven here
# rather than in production. Run: scripts/tests/test_pcie_wifi_link_guard.sh

set -uo pipefail

GUARD="$(cd "$(dirname "$0")/.." && pwd)/pcie-wifi-link-guard.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

FAKE="$TMP/sys"
CHAIN="pci0000:00/0000:00:1d.0/0000:08:00.0/0000:09:03.0/0000:0a:00.0"
EP="$FAKE/devices/$CHAIN"

mkdir -p "$EP" "$FAKE/bus/pci/drivers/ath10k_pci" "$FAKE/bus/pci/devices"
ln -s "$EP" "$FAKE/bus/pci/drivers/ath10k_pci/0000:0a:00.0"

# Per-device attribute dirs, addressed the way the guard addresses them.
for dev in 0000:00:1d.0 0000:08:00.0 0000:09:03.0 0000:0a:00.0; do
    mkdir -p "$FAKE/bus/pci/devices/$dev"
    printf 'TOTAL_ERR_COR 0\n'   > "$FAKE/bus/pci/devices/$dev/aer_dev_correctable"
    printf 'TOTAL_ERR_FATAL 0\n' > "$FAKE/bus/pci/devices/$dev/aer_dev_fatal"
done
mkdir -p "$FAKE/bus/pci/devices/0000:08:00.0/link"
echo 1 > "$FAKE/bus/pci/devices/0000:08:00.0/link/l1_aspm"

run() { NIVUUS_SYSFS="$FAKE" NIVUUS_STATE_DIR="$TMP/state" "$GUARD" "$@" 2>&1; }

PASS=0 FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
check() { # description, expected-substring, actual
    case "$3" in *"$2"*) ok "$1" ;; *) bad "$1" "$3" ;; esac
}
check_absent() {
    case "$3" in *"$2"*) bad "$1" "$3" ;; *) ok "$1" ;; esac
}

echo "== chain discovery =="
out=$(run status)
for dev in 0000:00:1d.0 0000:08:00.0 0000:09:03.0 0000:0a:00.0; do
    check "chain contains $dev" "$dev" "$out"
done

echo "== apply =="
out=$(run apply)
check "reports the device it disabled" "ASPM L1 disabled on 0000:08:00.0" "$out"
check "writes 0 to l1_aspm" "0" "$(cat "$FAKE/bus/pci/devices/0000:08:00.0/link/l1_aspm")"
out=$(run apply)
check "second run is idempotent" "already disabled" "$out"

echo "== check: silent while counters are zero =="
out=$(run check)
check_absent "no alert on a clean chain" "PCIe AER" "$out"

echo "== check: alerts once a counter goes non-zero =="
printf 'TOTAL_ERR_COR 7\n' > "$FAKE/bus/pci/devices/0000:0a:00.0/aer_dev_correctable"
out=$(run check)
check "alerts on the faulty device" "PCIe AER on 0000:0a:00.0" "$out"
check "reports the counter value" "correctable=7" "$out"

echo "== check: does not repeat an unchanged alert =="
out=$(run check)
check_absent "same value stays quiet" "PCIe AER" "$out"

echo "== check: re-alerts when the counter grows =="
printf 'TOTAL_ERR_COR 9\n' > "$FAKE/bus/pci/devices/0000:0a:00.0/aer_dev_correctable"
out=$(run check)
check "alerts again on a new value" "correctable=9" "$out"

echo "== check: fatal counters are reported too =="
printf 'TOTAL_ERR_FATAL 3\n' > "$FAKE/bus/pci/devices/0000:09:03.0/aer_dev_fatal"
out=$(run check)
check "reports fatal errors" "fatal=3" "$out"

echo "== fail-open: missing driver dir must not crash =="
out=$(NIVUUS_SYSFS="$TMP/nonexistent" NIVUUS_STATE_DIR="$TMP/state" "$GUARD" check 2>&1)
rc=$?
[ "$rc" = 0 ] && ok "exits 0 with no ath10k device" \
              || bad "exits 0 with no ath10k device" "rc=$rc $out"

echo "== fail-open: apply gives up instead of hanging when no card appears =="
out=$(NIVUUS_SYSFS="$TMP/nonexistent" NIVUUS_STATE_DIR="$TMP/state" \
      NIVUUS_CHAIN_WAIT=2 "$GUARD" apply 2>&1)
rc=$?
check "reports that nothing was hardened" "nothing to harden" "$out"
[ "$rc" = 0 ] && ok "apply still exits 0" || bad "apply still exits 0" "rc=$rc"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
