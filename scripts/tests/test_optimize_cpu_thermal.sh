#!/bin/bash
# Tests for scripts/optimize-cpu-thermal.sh, against a fake sysfs tree.
#
# RAPL is the machine's only thermal contract: if firmware locks the power
# limits, PL1 stays at the firmware default, and the script must say so with
# a non-zero exit instead of "applied successfully". The suite may run as
# root, which writes through any permission bit, so a locked knob is modelled
# as a symlink to /dev/null (the write is accepted and nothing sticks, as with
# a locked MSR) or as a directory (the write is refused). Never /dev/full: it
# reads back as an endless stream of zeros.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/optimize-cpu-thermal.sh"
# Tripwire: without both seams the script writes to the real /sys and calls
# the real systemctl. A script lacking them is never executed here.
for seam in NIVUUS_TEST_PATH NIVUUS_SYSFS_ROOT; do
    grep -q "$seam" "$SCRIPT" ||
        { echo "REFUSING: $SCRIPT ignores $seam; running it would act on the real host"; exit 1; }
done
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }

B="$TMP/bin"; mkdir -p "$B"
for tool in systemctl logger; do
    printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\n' "$tool" "$TMP" > "$B/$tool"
done
printf '#!/bin/sh\necho "shut off"\n' > "$B/virsh"
chmod +x "$B"/*

make_tree() {
    S="$TMP/sys.$RANDOM/sys"
    local rapl="$S/class/powercap/intel-rapl/intel-rapl:0" hw="$S/class/hwmon/hwmon3" cpu n k
    mkdir -p "$rapl" "$hw"
    for f in constraint_0_power_limit_uw constraint_0_time_window_us constraint_1_power_limit_uw enabled; do
        echo 0 > "$rapl/$f"
    done
    echo nct6798 > "$hw/name"
    for n in 1 2; do
        for f in temp_sel enable; do echo 0 > "$hw/pwm${n}_$f"; done
        for k in 1 2 3 4 5; do echo 0 > "$hw/pwm${n}_auto_point${k}_temp"; echo 0 > "$hw/pwm${n}_auto_point${k}_pwm"; done
    done
    for cpu in $(seq 0 23); do
        mkdir -p "$S/devices/system/cpu/cpu$cpu/cpufreq"
        for f in scaling_governor scaling_max_freq energy_performance_preference scaling_cur_freq; do
            echo 0 > "$S/devices/system/cpu/cpu$cpu/cpufreq/$f"
        done
    done
}
run() { NIVUUS_TEST_PATH="$B" NIVUUS_SYSFS_ROOT="${S%/sys}" bash "$SCRIPT" "$@" 2>&1; }
RAPL_DIR() { echo "$S/class/powercap/intel-rapl/intel-rapl:0"; }

# --- a fully writable tree: exit 0 and the documented values ----------------
make_tree; : > "$TMP/calls"
OUT=$(run); RC=$?
[ "$RC" -eq 0 ] && ok "a writable tree exits 0" || bad "a writable tree exits 0" "rc=$RC $OUT"
R=$(RAPL_DIR)
[ "$(cat "$R/constraint_0_power_limit_uw")" = 80000000 ] && ok "PL1 is 80 W" || bad "PL1 is 80 W" "$(cat "$R/constraint_0_power_limit_uw")"
[ "$(cat "$R/constraint_0_time_window_us")" = 4000000 ] && ok "the PL1 window is 4 s" || bad "the PL1 window is 4 s" "$(cat "$R/constraint_0_time_window_us")"
[ "$(cat "$R/constraint_1_power_limit_uw")" = 90000000 ] && ok "PL2 is 90 W" || bad "PL2 is 90 W" "$(cat "$R/constraint_1_power_limit_uw")"
[ "$(cat "$S/class/hwmon/hwmon3/pwm2_auto_point1_pwm")" = 58 ] && ok "the chassis fan never stops (point1 pwm 58)" ||
    bad "the chassis fan never stops (point1 pwm 58)" "$(cat "$S/class/hwmon/hwmon3/pwm2_auto_point1_pwm")"
[ "$(cat "$S/class/hwmon/hwmon3/pwm1_temp_sel")" = 8 ] && ok "fan1 follows PECI (source 8)" || bad "fan1 follows PECI (source 8)" "$OUT"
[ "$(cat "$S/devices/system/cpu/cpu0/cpufreq/scaling_max_freq")" = 3600000 ] && ok "idle mode caps the P-cores at 3.6 GHz" ||
    bad "idle mode caps the P-cores at 3.6 GHz" "$(cat "$S/devices/system/cpu/cpu0/cpufreq/scaling_max_freq")"
echo "$OUT" | grep -q "applied successfully" && ok "success is reported" || bad "success is reported" "$OUT"
echo "$OUT" | grep -q "50 W" && bad "no stale 50 W text" "$OUT" || ok "no stale 50 W text"

# --- PL1 accepted but not stored (locked): exit non-zero, naming it ---------
make_tree; R=$(RAPL_DIR); rm "$R/constraint_0_power_limit_uw"; ln -s /dev/null "$R/constraint_0_power_limit_uw"
OUT=$(run); RC=$?
[ "$RC" -ne 0 ] && ok "a PL1 that does not stick exits non-zero" || bad "a PL1 that does not stick exits non-zero" "rc=$RC"
echo "$OUT" | grep -q "constraint_0_power_limit_uw" && ok "the failure names PL1" || bad "the failure names PL1" "$OUT"
echo "$OUT" | grep -q "applied successfully" && bad "no success message then" "$OUT" || ok "no success message then"
[ "$(cat "$S/class/hwmon/hwmon3/pwm2_auto_point1_pwm")" = 58 ] && ok "the fan curve is still applied after a RAPL failure" ||
    bad "the fan curve is still applied after a RAPL failure" "$OUT"

# --- PL2 write refused -------------------------------------------------------
make_tree; R=$(RAPL_DIR); rm "$R/constraint_1_power_limit_uw"; mkdir "$R/constraint_1_power_limit_uw"
OUT=$(run); RC=$?
[ "$RC" -ne 0 ] && echo "$OUT" | grep -q "constraint_1_power_limit_uw.*write refused" &&
    ok "a refused PL2 write exits non-zero and says so" || bad "a refused PL2 write exits non-zero and says so" "rc=$RC $OUT"

# --- a fan curve point that does not stick ----------------------------------
make_tree; rm "$S/class/hwmon/hwmon3/pwm2_auto_point1_pwm"; ln -s /dev/null "$S/class/hwmon/hwmon3/pwm2_auto_point1_pwm"
OUT=$(run); RC=$?
[ "$RC" -ne 0 ] && ok "a fan point that does not stick exits non-zero" || bad "a fan point that does not stick exits non-zero" "rc=$RC"

# --- an optional knob (EPP) that does not stick only warns ------------------
make_tree; f="$S/devices/system/cpu/cpu3/cpufreq/energy_performance_preference"; rm "$f"; ln -s /dev/null "$f"
OUT=$(run); RC=$?
[ "$RC" -eq 0 ] && echo "$OUT" | grep -q "optional .*cpu3/cpufreq/energy_performance_preference" &&
    ok "an EPP mismatch warns without failing" || bad "an EPP mismatch warns without failing" "rc=$RC $OUT"

# --- policy-only modes never touch RAPL --------------------------------------
make_tree; R=$(RAPL_DIR)
OUT=$(run gaming); RC=$?
[ "$RC" -eq 0 ] && [ "$(cat "$R/constraint_0_power_limit_uw")" = 0 ] &&
    ok "gaming mode exits 0 and leaves RAPL alone" || bad "gaming mode exits 0 and leaves RAPL alone" "rc=$RC"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
