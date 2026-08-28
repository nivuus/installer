#!/bin/bash
# Shut down the Windows VM after sustained inactivity (energy saving).
# Activity = established streaming/RDP flows to the VM, or VM CPU usage.
# Wake-on-demand is provided by vm-trigger-47984.socket (re-armed here).
# Run periodically by vm-idle-shutdown.timer.

VM_NAME="Windows"
VM_IP="192.168.3.2"
TCP_PORTS="3389|47984|47989|48010"
UDP_PORTS="47998|47999|48000"
STATE_DIR="/run/nivuus-vm-idle"
STATE_FILE="$STATE_DIR/state"
IDLE_STRIKES_LIMIT=3          # checks in a row before shutdown (3 x 10 min)
CPU_ACTIVE_THRESHOLD=50       # % of one vCPU-core averaged since last check
LOG_TAG="vm-idle-shutdown"

mkdir -p "$STATE_DIR"

reset_state() { echo "0 0 0" > "$STATE_FILE"; }

# --- VM not running: nothing to do, keep wake trigger healthy ---
if ! LC_ALL=C virsh domstate "$VM_NAME" 2>/dev/null | grep -q running; then
    reset_state
    for p in 47984 47989; do
        if ! systemctl is-active --quiet "vm-trigger-$p.socket"; then
            logger -t "$LOG_TAG" "VM off but wake socket $p inactive - re-arming"
            systemctl reset-failed "vm-trigger-$p.service" "vm-trigger-$p.socket" 2>/dev/null
            systemctl start "vm-trigger-$p.socket"
        fi
    done
    # Self-heal: VM off means the GPU belongs to the host - ollama (docker) should be up
    if ! docker inspect -f "{{.State.Running}}" nivuus-ollama 2>/dev/null | grep -q true; then
        logger -t "$LOG_TAG" "VM off but ollama container down - starting it"
        docker compose -f /opt/nivuus/ollama/docker-compose.yml --env-file /opt/nivuus/ollama/.env up -d 2>&1 | logger -t "$LOG_TAG"
    fi
    # Self-heal: VM off means the host owns every CPU. The release hook shares a
    # directory with rebind-host-gpu.sh, which the dispatcher runs in unordered
    # find(1) order and which has deadlocked before - so re-assert it here.
    if [ "$(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective 2>/dev/null)" \
         != "$(tr -d '\n' < /sys/devices/system/cpu/online)" ]; then
        logger -t "$LOG_TAG" "VM off but host cpuset still restricted - releasing CPUs"
        /etc/libvirt/hooks/vm-cpu-partition.sh release 2>&1 | logger -t "$LOG_TAG"
    fi
    exit 0
fi

# --- Activity check 1: established flows to the VM (Sunshine/Moonlight/RDP) ---
# conntrack line layout: proto ... [state] src= dst= sport= dport= [reply tuple] ...
# DNAT'ed flows carry the VM IP in the reply tuple, so match it on either side.
FLOWS_TCP=$(grep -scE "ESTABLISHED.*=$VM_IP .*port=($TCP_PORTS)( |$)" /proc/net/nf_conntrack)
FLOWS_UDP=$(grep -scE "udp.*=$VM_IP .*port=($UDP_PORTS)( |$).*ASSURED" /proc/net/nf_conntrack)
FLOWS=$(( ${FLOWS_TCP:-0} + ${FLOWS_UDP:-0} ))

# --- Activity check 2: VM CPU usage since last check ---
NOW_NS=$(date +%s%N)
CPU_NS=$(LC_ALL=C virsh domstats --cpu-total "$VM_NAME" 2>/dev/null | awk -F= "/cpu.time/ {print \$2}")
read PREV_NS PREV_CPU STRIKES < "$STATE_FILE" 2>/dev/null || { PREV_NS=0; PREV_CPU=0; STRIKES=0; }

CPU_PCT=0
if [ -n "$CPU_NS" ] && [ "$PREV_NS" -gt 0 ] && [ "$NOW_NS" -gt "$PREV_NS" ]; then
    CPU_PCT=$(( (CPU_NS - PREV_CPU) * 100 / (NOW_NS - PREV_NS) ))
fi

# --- Decide ---
# Negative CPU delta = VM restarted since last check (counter reset): treat as active
if [ "$FLOWS" -gt 0 ] || [ "$CPU_PCT" -ge "$CPU_ACTIVE_THRESHOLD" ] || [ "$CPU_PCT" -lt 0 ] || [ "$PREV_NS" -eq 0 ]; then
    STRIKES=0
else
    STRIKES=$((STRIKES + 1))
fi

echo "$NOW_NS ${CPU_NS:-0} $STRIKES" > "$STATE_FILE"
logger -t "$LOG_TAG" "flows=$FLOWS cpu=${CPU_PCT}% strikes=$STRIKES/$IDLE_STRIKES_LIMIT"

if [ "$STRIKES" -ge "$IDLE_STRIKES_LIMIT" ]; then
    logger -t "$LOG_TAG" "VM idle for $((STRIKES * 10)) min - hibernating (session preserved)"
    # WinRM times out while the guest goes to sleep - ignore its exit code,
    # watch the domain state instead; fall back to ACPI shutdown if needed.
    # Short timeout: the WinRM call hangs while the guest falls asleep.
    # Then poll fast: the shut-off window can be only a few seconds long if a
    # Moonlight poll re-wakes the VM - leaving "running" at ANY point = success.
    timeout 10 /usr/local/bin/winvm "shutdown /h /f" >/dev/null 2>&1
    HIBERNATED=0
    for i in $(seq 1 45); do
        sleep 2
        if ! LC_ALL=C virsh domstate "$VM_NAME" 2>/dev/null | grep -q running; then
            HIBERNATED=1; break
        fi
    done
    if [ "$HIBERNATED" -eq 0 ]; then
        logger -t "$LOG_TAG" "Hibernate did not complete - falling back to ACPI shutdown"
        virsh shutdown --mode acpi "$VM_NAME"
    fi
    reset_state
    # Give the guest time to power off, then make sure wake-on-demand is armed
    sleep 60
    for p in 47984 47989; do
        systemctl reset-failed "vm-trigger-$p.service" "vm-trigger-$p.socket" 2>/dev/null
        systemctl start "vm-trigger-$p.socket" 2>/dev/null
    done
fi
exit 0
