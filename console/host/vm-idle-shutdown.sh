#!/bin/bash
# Shut down the Windows VM after sustained inactivity (energy saving).
# Activity = established streaming/RDP flows to the VM, a desk app window, or
# CPU usage measured INSIDE the guest.
# Wake-on-demand is provided by vm-trigger-47984.socket (re-armed here).
# Run periodically by vm-idle-shutdown.timer.

VM_NAME="Windows"
VM_IP="192.168.3.2"
TCP_PORTS="3389|47984|47989|48010"
UDP_PORTS="47998|47999|48000"
STATE_DIR="${VM_IDLE_STATE_DIR:-/run/nivuus-vm-idle}"
STATE_FILE="$STATE_DIR/state"
NF_CONNTRACK="${NF_CONNTRACK:-/proc/net/nf_conntrack}"
IDLE_STRIKES_LIMIT=3          # checks in a row before shutdown (3 x 10 min)
# Guest CPU, % of ALL its logical processors, averaged since the last pass.
# Measured in Windows, not from the host: libvirt's cpu.time also counts the
# hypervisor cost of an idle guest (timer interrupts, halt exits), about 50 % of
# one core for this 14-vCPU VM - the old "50 % of one core" threshold sat ON that
# floor and hibernation never fired (seen 2026-09-30: host 53-66 % while
# Windows reported 1 %). Idle reads ~1 %; one busy thread of 14 is ~7 %.
CPU_ACTIVE_THRESHOLD=5
WINVM="${VM_IDLE_WINVM:-/usr/local/bin/winvm}"
# A desk app window is "open now" when the platform said so recently. The
# platform sends `busy` every APP_HEARTBEAT_S (plateforme BUSY_PERIOD_MS, keep
# them equal); three missed beats mean the last window is gone. The 30 minutes
# of idle time come from IDLE_STRIKES_LIMIT, NOT from this window - widening
# it to 30 min would double-count and keep the VM up for ~1 h.
APP_HEARTBEAT_S=60
APP_ACTIVITY_MAX_AGE_S=$((3 * APP_HEARTBEAT_S))
APP_ACTIVITY_FILE="$STATE_DIR/app-activity"
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
FLOWS_TCP=$(grep -scE "ESTABLISHED.*=$VM_IP .*port=($TCP_PORTS)( |$)" "$NF_CONNTRACK")
FLOWS_UDP=$(grep -scE "udp.*=$VM_IP .*port=($UDP_PORTS)( |$).*ASSURED" "$NF_CONNTRACK")
FLOWS=$(( ${FLOWS_TCP:-0} + ${FLOWS_UDP:-0} ))

# --- Activity check 3: a desk app window reported by the platform ---
# Written by vm-control.sh `busy`. Missing = no window. Unreadable = ignored
# and logged. Dated in the future (clock skew) = active: never cut a session
# because of a clock step.
APP_ACTIVE=0
if [ -r "$APP_ACTIVITY_FILE" ]; then
    APP_TS=$(cat "$APP_ACTIVITY_FILE")
    if [[ "$APP_TS" =~ ^[0-9]+$ ]]; then
        if [ $(( $(date +%s) - 10#$APP_TS )) -lt "$APP_ACTIVITY_MAX_AGE_S" ]; then
            APP_ACTIVE=1
        fi
    else
        logger -t "$LOG_TAG" "ignoring $APP_ACTIVITY_FILE: not a timestamp"
    fi
fi

# --- Activity check 2: guest CPU usage since the last pass ---
# The raw performance counter is cumulative, so the average since the previous
# pass needs no sampling window: busy = 100 * (1 - dIdle / dTime), where the
# guest's own clock (100 ns units) and the _Total idle counter come from the
# same query. State layout: "<guest_time> <guest_idle> <strikes>".
#
# Unknown is NOT idle: if the guest cannot be measured (WinRM down, VM still
# booting, password file broken) the pass counts as active and says so in the
# journal. Hibernating a guest we cannot see could cut a session.
GUEST_CPU_QUERY='$o = Get-CimInstance Win32_PerfRawData_PerfOS_Processor | Where-Object { $_.Name -eq "_Total" }; "$($o.Timestamp_Sys100NS) $($o.PercentProcessorTime)"'
read PREV_T PREV_IDLE STRIKES < "$STATE_FILE" 2>/dev/null || { PREV_T=0; PREV_IDLE=0; STRIKES=0; }

CPU_PCT=0
CPU_KNOWN=1
if ! read -r GUEST_T GUEST_IDLE _ < <(timeout 30 "$WINVM" --ps "$GUEST_CPU_QUERY" 2>/dev/null | tr -d "\r") \
   || ! [[ "$GUEST_T" =~ ^[0-9]+$ && "$GUEST_IDLE" =~ ^[0-9]+$ ]]; then
    CPU_KNOWN=0
    GUEST_T=0
    GUEST_IDLE=0
    logger -t "$LOG_TAG" "guest CPU unavailable (winvm failed): counting this pass as active"
elif [ "$PREV_T" -gt 0 ]; then
    if [ "$GUEST_T" -gt "$PREV_T" ] && [ "$GUEST_IDLE" -ge "$PREV_IDLE" ]; then
        CPU_PCT=$(( 100 - (GUEST_IDLE - PREV_IDLE) * 100 / (GUEST_T - PREV_T) ))
    else
        # The counters went backwards: the guest restarted since the last pass.
        CPU_PCT=-1
    fi
fi

# --- Decide ---
if [ "$FLOWS" -gt 0 ] || [ "$APP_ACTIVE" -eq 1 ] || [ "$CPU_KNOWN" -eq 0 ] \
   || [ "$CPU_PCT" -ge "$CPU_ACTIVE_THRESHOLD" ] || [ "$CPU_PCT" -lt 0 ] || [ "$PREV_T" -eq 0 ]; then
    STRIKES=0
else
    STRIKES=$((STRIKES + 1))
fi

echo "$GUEST_T $GUEST_IDLE $STRIKES" > "$STATE_FILE"
logger -t "$LOG_TAG" "flows=$FLOWS app=$APP_ACTIVE cpu=${CPU_PCT}% strikes=$STRIKES/$IDLE_STRIKES_LIMIT"

if [ "$STRIKES" -ge "$IDLE_STRIKES_LIMIT" ]; then
    logger -t "$LOG_TAG" "VM idle for $((STRIKES * 10)) min - hibernating (session preserved)"
    # WinRM times out while the guest goes to sleep - ignore its exit code,
    # watch the domain state instead; fall back to ACPI shutdown if needed.
    # Short timeout: the WinRM call hangs while the guest falls asleep.
    # Then poll fast: the shut-off window can be only a few seconds long if a
    # Moonlight poll re-wakes the VM - leaving "running" at ANY point = success.
    timeout 10 "$WINVM" "shutdown /h /f" >/dev/null 2>&1
    HIBERNATED=0
    # 90 x 2 s = 180 s. Windows needs ~70-80 s to write the 16 GB guest's
    # hibernation file (measured 2026-09-30: 80 s from the request to "shut
    # off"); a 90 s budget left 10 s of margin, and an ACPI shutdown sent to a
    # guest that is still hibernating loses the session.
    for _ in $(seq 1 90); do
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
