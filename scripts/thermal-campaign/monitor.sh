#!/bin/bash
# Nivuus thermal campaign sampler.
#
# Usage: monitor.sh <label> <duration_s> <csv_path>
#
# Samples power/frequency/temperature every 2 s and appends CSV rows.
# Aborts if the package or the GPU crosses a hard limit; on abort it touches
# $ABORT_FLAG so the runner kills the load immediately.
#
# Power comes from the RAPL energy counters rather than turbostat: one counter
# read per sample, no subprocess, and it is the very quantity the thermal
# contract is written in (PL1=50 W / PL2=58 W, calibrated 2026-07-17).

set -u

LABEL="$1"
DURATION="$2"
CSV="$3"

INTERVAL=2
ABORT_FLAG="${ABORT_FLAG:-/run/nivuus-thermal-abort}"

# Hard limits. TjMax is 100 C; 95 leaves a margin for the sampling period.
MAX_PKG_TEMP_MC=95000
MAX_GPU_TEMP_C=83

RAPL=/sys/class/powercap/intel-rapl:0
PKG_ZONE=/sys/class/thermal/thermal_zone2/temp

# --- discover the nct6798 hwmon and its labelled inputs -------------------

NCT=""
for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$h/name" 2>/dev/null)" = "nct6798" ] && { NCT="$h"; break; }
done

systin_in=""; cputin_in=""
if [ -n "$NCT" ]; then
    for l in "$NCT"/temp*_label; do
        [ -e "$l" ] || continue
        case "$(cat "$l" 2>/dev/null)" in
            SYSTIN) systin_in="${l%_label}_input" ;;
            CPUTIN) cputin_in="${l%_label}_input" ;;
        esac
    done
fi

read_or_zero() { cat "$1" 2>/dev/null || echo 0; }

RAPL_MAX=$(read_or_zero "$RAPL/max_energy_range_uj")
energy_uj() { read_or_zero "$RAPL/energy_uj"; }

# Average frequency over the online CPUs, in MHz.
avg_mhz() {
    local sum=0 n=0 f
    for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq; do
        f=$(cat "$c" 2>/dev/null) || continue
        sum=$((sum + f)); n=$((n + 1))
    done
    [ "$n" -gt 0 ] && echo $((sum / n / 1000)) || echo 0
}

if [ ! -s "$CSV" ]; then
    echo "timestamp,label,elapsed_s,pkg_watt,pkg_temp_c,avg_mhz,cputin_c,systin_c,fan1_rpm,fan2_rpm,gpu_temp_c,gpu_watt,gpu_mhz,gpu_util_pct,loadavg1" > "$CSV"
fi

start=$(date +%s)
prev_e=$(energy_uj)
prev_t=$(date +%s.%N)

while :; do
    sleep "$INTERVAL"

    now=$(date +%s)
    elapsed=$((now - start))
    [ "$elapsed" -ge "$DURATION" ] && break

    cur_e=$(energy_uj)
    cur_t=$(date +%s.%N)
    d_e=$((cur_e - prev_e))
    [ "$d_e" -lt 0 ] && d_e=$((d_e + RAPL_MAX))     # counter rollover
    d_t=$(awk -v a="$cur_t" -v b="$prev_t" 'BEGIN{printf "%.3f", a-b}')
    pkg_w=$(awk -v e="$d_e" -v t="$d_t" 'BEGIN{ if (t>0) printf "%.1f", e/t/1000000; else print 0 }')
    prev_e=$cur_e; prev_t=$cur_t

    pkg_mc=$(read_or_zero "$PKG_ZONE")
    pkg_c=$((pkg_mc / 1000))
    mhz=$(avg_mhz)

    cputin=$([ -n "$cputin_in" ] && echo $(( $(read_or_zero "$cputin_in") / 1000 )) || echo 0)
    systin=$([ -n "$systin_in" ] && echo $(( $(read_or_zero "$systin_in") / 1000 )) || echo 0)
    fan1=$([ -n "$NCT" ] && read_or_zero "$NCT/fan1_input" || echo 0)
    fan2=$([ -n "$NCT" ] && read_or_zero "$NCT/fan2_input" || echo 0)

    gpu=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,utilization.gpu \
          --format=csv,noheader,nounits 2>/dev/null | tr -d ' ' | head -1)
    gpu_t=$(echo "$gpu" | cut -d, -f1); gpu_t=${gpu_t:-0}
    gpu_w=$(echo "$gpu" | cut -d, -f2); gpu_w=${gpu_w:-0}
    gpu_m=$(echo "$gpu" | cut -d, -f3); gpu_m=${gpu_m:-0}
    gpu_u=$(echo "$gpu" | cut -d, -f4); gpu_u=${gpu_u:-0}

    la1=$(cut -d' ' -f1 /proc/loadavg)

    echo "$(date -Is),$LABEL,$elapsed,$pkg_w,$pkg_c,$mhz,$cputin,$systin,$fan1,$fan2,$gpu_t,$gpu_w,$gpu_m,$gpu_u,$la1" >> "$CSV"

    if [ "$pkg_mc" -ge "$MAX_PKG_TEMP_MC" ]; then
        echo "ABORT: package ${pkg_c}C >= $((MAX_PKG_TEMP_MC/1000))C" >&2
        touch "$ABORT_FLAG"; exit 2
    fi
    if [ "${gpu_t%.*}" -ge "$MAX_GPU_TEMP_C" ] 2>/dev/null; then
        echo "ABORT: GPU ${gpu_t}C >= ${MAX_GPU_TEMP_C}C" >&2
        touch "$ABORT_FLAG"; exit 2
    fi
done

exit 0
