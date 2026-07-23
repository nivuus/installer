#!/bin/bash
# Nivuus thermal campaign runner.
#
# Purpose: decide between the static RAPL cap (PL1=50 W) and a closed-loop
# thermal daemon. Runs each scenario to thermal equilibrium, sampling into one
# CSV, with a cooldown between them. Any hard-limit breach raises the abort
# flag and stops the campaign.
#
# Usage: run-campaign.sh [scenario ...]      (default: all, ~40 min)
#
# Prerequisite: the GPU stressor image, built once with
#   docker build -t nivuus/gpuburn:1 scripts/thermal-campaign/

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CSV="$DIR/campaign.csv"
LOG="$DIR/campaign.log"
ABORT_FLAG=/run/nivuus-thermal-abort
export ABORT_FLAG

BURN_IMAGE=nivuus/gpuburn:1
BURN_CONTAINER=nivuus-gpuburn

declare -A DURATION=(
    [s0_idle]=180
    [s1_cpu]=480
    [s2_igpu]=300
    [s3_gpu]=480
    [s4_cpu_gpu]=480
)
COOLDOWN=120

rm -f "$ABORT_FLAG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

kill_loads() {
    pkill -f "stress-ng" 2>/dev/null
    docker rm -f "$BURN_CONTAINER" >/dev/null 2>&1
    sleep 1
}

start_cpu_load()  { stress-ng --cpu 24 --cpu-method matrixprod --timeout "$1"s >/dev/null 2>&1 & }
start_igpu_load() { stress-ng --gpu 1 --timeout "$1"s >/dev/null 2>&1 & }
start_gpu_load()  {
    # No bind mount: the image carries the compiled stressor, so this works
    # regardless of the caller's mount namespace.
    docker run --rm -d --name "$BURN_CONTAINER" --gpus all "$BURN_IMAGE" "$1" >/dev/null 2>&1
}

run_scenario() {
    local name="$1" dur="${DURATION[$1]}"

    log "--- $name (${dur}s) ---"
    case "$name" in
        s0_idle)    ;;
        s1_cpu)     start_cpu_load "$dur" ;;
        s2_igpu)    start_igpu_load "$dur" ;;
        s3_gpu)     start_gpu_load "$dur" ;;
        s4_cpu_gpu) start_cpu_load "$dur"; start_gpu_load "$dur" ;;
    esac

    "$DIR/monitor.sh" "$name" "$dur" "$CSV"
    local rc=$?
    kill_loads

    if [ -f "$ABORT_FLAG" ]; then
        log "!!! ABORT during $name (monitor rc=$rc) — campaign stopped"
        return 1
    fi

    log "$name done"
    if [ "$name" != "s0_idle" ]; then
        log "cooldown ${COOLDOWN}s"
        "$DIR/monitor.sh" "cooldown_after_$name" "$COOLDOWN" "$CSV" || true
    fi
    return 0
}

SCENARIOS=("$@")
[ ${#SCENARIOS[@]} -eq 0 ] && SCENARIOS=(s0_idle s1_cpu s2_igpu s3_gpu s4_cpu_gpu)

log "=== campaign start — VM=$(LC_ALL=C virsh domstate Windows 2>/dev/null | tr -d '\n') ==="
for s in "${SCENARIOS[@]}"; do
    run_scenario "$s" || break
done
kill_loads
log "=== campaign end ==="
