#!/bin/bash
# Nivuus CPU Thermal Optimization Script
# Thermal safety via RAPL power capping instead of a static frequency cap:
# light-thread (gaming) loads boost to full turbo, sustained all-core loads
# converge to the package power the case can dissipate. Calibrated 2026-07-17
# on the compact case: 50 W package ≈ 85 °C, 75 W ≈ 100 °C (TjMax).
#
# VM-aware CPU power mode (2026-07-22, see
# docs/superpowers/specs/2026-07-22-vm-aware-cpu-power-mode-design.md):
#   optimize-cpu-thermal.sh          Full apply (RAPL + fan + policy). The
#                                    gaming/idle mode is auto-detected from the
#                                    Windows VM state; falls back to idle.
#   optimize-cpu-thermal.sh gaming   Policy only (EPP/freq + latency ceiling).
#   optimize-cpu-thermal.sh idle     Policy only.
#
# Modes (RAPL and the fan curve are mode-agnostic and only touched on full apply):
#   gaming  P-cores 5100 MHz / EPP performance, E-cores 2000 MHz / EPP power,
#           C-state ceiling held at C6 by nivuus-cpu-latency.service
#   idle    P-cores 3600 MHz / EPP power,       E-cores 2000 MHz / EPP power,
#           deep C-states allowed (latency service stopped)

set -e

# --- Mode parsing -----------------------------------------------------------

MODE_ARG="${1:-}"
case "$MODE_ARG" in
    ""|gaming|idle) ;;
    *) echo "usage: $0 [gaming|idle]" >&2; exit 2 ;;
esac

FULL_APPLY=0
MODE="$MODE_ARG"
if [ -z "$MODE" ]; then
    FULL_APPLY=1
    # Auto-detect: the guest pins the P-cores while running -> gaming policy.
    # Not a libvirt-hook context here (boot service / manual), so virsh is safe.
    # Any non-"running" answer, or no answer (libvirtd down at boot), -> idle.
    vm_state="$(LC_ALL=C virsh domstate Windows 2>/dev/null | head -1 || true)"
    if [ "$vm_state" = "running" ]; then MODE="gaming"; else MODE="idle"; fi
fi

echo "========================================"
echo "Nivuus CPU Thermal Optimization — mode: $MODE$([ "$FULL_APPLY" = 1 ] && echo ' (full apply)')"
echo "========================================"
echo ""

# P-cores: CPU 0-15
PCORES="0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15"
# E-cores: CPU 16-23
ECORES="16 17 18 19 20 21 22 23"

# Per-mode policy (frequency caps are RAPL-limited; they only bite under load)
ECORE_MAX_FREQ=2000000  # 2000 MHz (efficiency) — same in both modes
case "$MODE" in
    gaming)
        PCORE_MAX_FREQ=5100000  # full turbo
        PCORE_EPP="performance" # fastest ramp / least scaling jitter for the guest
        ECORE_EPP="power"
        ;;
    idle)
        PCORE_MAX_FREQ=3600000  # cooler/quieter host when the VM is off
        PCORE_EPP="power"
        ECORE_EPP="power"
        ;;
esac

RAPL_PL1_UW=50000000    # 50 W sustained (≈85 °C with fans at max)
RAPL_PL1_TAU_US=4000000 # 4 s averaging window (small thermal overshoot)
RAPL_PL2_UW=58000000    # 58 W instantaneous burst ceiling (≈93 °C peak)

# --- RAPL package power limits (the actual thermal contract; full apply only) ---

RAPL=/sys/class/powercap/intel-rapl/intel-rapl:0
if [ "$FULL_APPLY" = 1 ]; then
    if [ -d "$RAPL" ]; then
        echo "Applying RAPL package power limits..."
        echo "  PL1 (sustained): $((RAPL_PL1_UW/1000000)) W, window $((RAPL_PL1_TAU_US/1000000)) s"
        echo "  PL2 (burst):     $((RAPL_PL2_UW/1000000)) W"
        echo "$RAPL_PL1_UW"     > "$RAPL/constraint_0_power_limit_uw"  2>/dev/null || true
        echo "$RAPL_PL1_TAU_US" > "$RAPL/constraint_0_time_window_us"  2>/dev/null || true
        echo "$RAPL_PL2_UW"     > "$RAPL/constraint_1_power_limit_uw"  2>/dev/null || true
        echo 1                  > "$RAPL/enabled"                      2>/dev/null || true
    else
        echo "⚠ RAPL powercap not found — falling back to 3.6 GHz P-core cap"
        PCORE_MAX_FREQ=3600000
    fi
fi

# --- CPU Governor & EPP (both full apply and policy-only) ---

echo ""
echo "Applying P-cores configuration..."
echo "  Max Frequency: $((PCORE_MAX_FREQ/1000)) MHz (RAPL-limited)"
echo "  Governor: powersave (intel_pstate scales dynamically)"
echo "  EPP: $PCORE_EPP"

for cpu in $PCORES; do
    if [ -d "/sys/devices/system/cpu/cpu$cpu" ]; then
        echo "powersave" > /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_governor 2>/dev/null || true
        echo "$PCORE_MAX_FREQ" > /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_max_freq 2>/dev/null || true
        echo "$PCORE_EPP" > /sys/devices/system/cpu/cpu$cpu/cpufreq/energy_performance_preference 2>/dev/null || true
    fi
done

echo ""
echo "Applying E-cores configuration..."
echo "  Max Frequency: $((ECORE_MAX_FREQ/1000)) MHz"
echo "  Governor: powersave"
echo "  EPP: $ECORE_EPP"

for cpu in $ECORES; do
    if [ -d "/sys/devices/system/cpu/cpu$cpu" ]; then
        echo "powersave" > /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_governor 2>/dev/null || true
        echo "$ECORE_MAX_FREQ" > /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_max_freq 2>/dev/null || true
        echo "$ECORE_EPP" > /sys/devices/system/cpu/cpu$cpu/cpufreq/energy_performance_preference 2>/dev/null || true
    fi
done

# --- C-state ceiling via PM QoS (lever 3) ---
# The constraint lives for exactly as long as nivuus-cpu-latency.service holds
# /dev/cpu_dma_latency open. gaming keeps today's C6 ceiling; idle lifts it so
# the deep package C-states become reachable. A start failure in gaming is a
# jitter regression, not an outage — log and continue (never abort the mode).

if [ "$MODE" = "gaming" ]; then
    if systemctl start nivuus-cpu-latency.service 2>/dev/null; then
        echo "  C-state ceiling: nivuus-cpu-latency.service started (C6)"
    else
        echo "  ⚠ nivuus-cpu-latency.service failed to start — guest may reach deeper C-states"
    fi
else
    systemctl stop nivuus-cpu-latency.service 2>/dev/null || true
    echo "  C-state ceiling: released (deep states allowed)"
fi

# --- Fan Curve Optimization (nct6798; full apply only, temperature-driven) ---

if [ "$FULL_APPLY" = 1 ]; then
    NCT_HWMON=""
    for hwmon in /sys/class/hwmon/hwmon*; do
        if [ "$(cat "$hwmon/name" 2>/dev/null)" = "nct6798" ]; then
            NCT_HWMON="$hwmon"
            break
        fi
    done

    if [ -n "$NCT_HWMON" ]; then
        echo ""
        echo "Applying fan curve optimization (nct6798)..."

        # More aggressive fan curve: ramp up earlier
        # Point 1: 35°C → 30% PWM (was 20°C → 20%)
        echo 35000 > "$NCT_HWMON/pwm1_auto_point1_temp" 2>/dev/null || true
        echo 77    > "$NCT_HWMON/pwm1_auto_point1_pwm"  2>/dev/null || true
        # Point 2: 50°C → 50% PWM (was 70°C → 70%)
        echo 50000 > "$NCT_HWMON/pwm1_auto_point2_temp" 2>/dev/null || true
        echo 128   > "$NCT_HWMON/pwm1_auto_point2_pwm"  2>/dev/null || true
        # Point 3: 60°C → 75% PWM (was 75°C → 100%)
        echo 60000 > "$NCT_HWMON/pwm1_auto_point3_temp" 2>/dev/null || true
        echo 191   > "$NCT_HWMON/pwm1_auto_point3_pwm"  2>/dev/null || true
        # Point 4: 70°C → 90% PWM (was 100°C → 100%)
        echo 70000 > "$NCT_HWMON/pwm1_auto_point4_temp" 2>/dev/null || true
        echo 230   > "$NCT_HWMON/pwm1_auto_point4_pwm"  2>/dev/null || true
        # Point 5: 80°C → 100% PWM (unchanged threshold)
        echo 80000 > "$NCT_HWMON/pwm1_auto_point5_temp" 2>/dev/null || true
        echo 255   > "$NCT_HWMON/pwm1_auto_point5_pwm"  2>/dev/null || true

        echo "  Fan curve: 35°C/30% → 50°C/50% → 60°C/75% → 70°C/90% → 80°C/100%"
    else
        echo ""
        echo "⚠ nct6798 hwmon not found, skipping fan curve optimization"
    fi
fi

echo ""
echo "✅ CPU thermal optimization applied successfully (mode: $MODE)"
echo ""
echo "Current configuration:"
echo "  CPU | Current  | Max      | Governor   | EPP"
echo "  ----|----------|----------|------------|----------------"

for cpu in 0 8 15 16 23; do
    if [ -d "/sys/devices/system/cpu/cpu$cpu" ]; then
        freq=$(cat /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_cur_freq 2>/dev/null || echo "0")
        max_freq=$(cat /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_max_freq 2>/dev/null || echo "0")
        gov=$(cat /sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_governor 2>/dev/null || echo "unknown")
        epp=$(cat /sys/devices/system/cpu/cpu$cpu/cpufreq/energy_performance_preference 2>/dev/null || echo "n/a")
        printf "  %-3s | %-8s | %-8s | %-10s | %s\n" "$cpu" "$((freq/1000))MHz" "$((max_freq/1000))MHz" "$gov" "$epp"
    fi
done

if [ "$FULL_APPLY" = 1 ] && [ -d "$RAPL" ]; then
    echo ""
    echo "RAPL package limits:"
    echo "  PL1: $(($(cat "$RAPL/constraint_0_power_limit_uw")/1000000)) W / $(($(cat "$RAPL/constraint_0_time_window_us")/1000000)) s"
    echo "  PL2: $(($(cat "$RAPL/constraint_1_power_limit_uw")/1000000)) W"
fi

echo ""
echo "Expected behavior (calibrated 2026-07-17):"
echo "  Idle: ≤45°C"
echo "  Gaming (light-thread): boosts to 4.1-5.1 GHz, package ≤50 W sustained, ≤88°C"
echo "  All-core sustained: RAPL converges to 50 W (~3 GHz all-core), no thermal runaway"
