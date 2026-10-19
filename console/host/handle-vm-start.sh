#!/bin/bash
# Wake the Windows VM on demand and make sure its forward-ports are in place.
#
# Called by vm-wake-gate.py (a Moonlight client knocking on 47989) and by
# nivuus-vm-wake.service (the desk platform's `wake` through vm-control.sh).
#
# The forward-ports have ONE owner: the libvirt hook started/begin/rules.sh,
# which libvirt runs on every start and which installs every port (TCP
# 3389/47984/47989/48010, UDP 47998-48000) towards the VM's fixed address in
# every active zone. This script never adds a rule of its own and never needs
# the VM's address: it starts the domain, then checks that the hook's rules are
# there, re-running that same hook if they went missing (a firewalld reload
# drops runtime rules while the VM keeps running).
#
# Until 2026-10-19 it looked the address up with `virsh domifaddr` and added a
# rule itself. That lookup cannot answer on this topology - the domain declares
# no guest-agent channel and sits on an externally managed bridge, so neither
# `--source agent` nor `--source lease` ever returns anything (measured
# 2026-08-28, see guest-ready-watch.py) - and `firewall-cmd --wait` does not
# exist in firewalld 2.x. Every cold start therefore waited 180 s and exited 1
# AFTER the VM had booted and the hook had opened the ports: the wake worked,
# the unit stayed failed (nivuus-vm-wake.service, 2026-10-08).

VM_NAME="Windows"
# The port whose forward-port proves the hook ran. Any of the hook's ports
# would do; 47984 is the one the fast path has always checked.
VM_PORT="47984"
STARTED_HOOK="${VM_STARTED_HOOK:-/etc/libvirt/hooks/qemu.d/$VM_NAME/started/begin/rules.sh}"
LOCK_FILE="${VM_TRIGGER_LOCK:-/run/vm-trigger-start.lock}"
# libvirt runs the started hook before `virsh start` returns, so the rules are
# normally there at once; the margin covers firewalld being slow to answer.
RULES_WAIT_SECONDS="${VM_RULES_WAIT_SECONDS:-30}"
CHECK_INTERVAL=2

LOG_TAG="vm-trigger-$VM_PORT"

# Query the domain state, telling "unknown state" apart from "hypervisor
# unreachable". Sets VM_STATE and VM_STATE_ERR; returns non-zero when virsh
# failed or printed nothing.
#
# Without that distinction a dead libvirtd gave VM_STATE="" (stderr discarded),
# the empty string fell into the "transitional state" branch and the script
# waited 90 s before failing - in a loop, on every Moonlight probe, since the
# socket triggers the service again. Incident of 2026-08-24.
query_vm_state() {
    local err_file rc
    err_file=$(mktemp)
    VM_STATE=$(LANG=C virsh domstate "$VM_NAME" 2>"$err_file" | head -n1)
    rc=$?
    VM_STATE_ERR=$(tr '\n' ' ' < "$err_file" | cut -c1-200)
    rm -f "$err_file"
    [ $rc -eq 0 ] && [ -n "$VM_STATE" ]
}

# True when the hook's forward-port for VM_PORT is in the default zone.
forward_rule_present() {
    firewall-cmd --list-forward-ports 2>/dev/null \
        | grep -qE "port=${VM_PORT}:proto=tcp:toport=${VM_PORT}:toaddr=[0-9.]+"
}

start_vm() {
    logger -t "$LOG_TAG" "Starting VM '$VM_NAME'..."
    if ! virsh start "$VM_NAME"; then
        logger -t "$LOG_TAG" "Error: failed to start VM '$VM_NAME'."
        exit 1
    fi
}

logger -t "$LOG_TAG" "Service triggered for port $VM_PORT."

# Identify the caller (diagnostics): recent connections to the wake ports.
grep -hE "dport=4798[49] " /proc/net/nf_conntrack 2>/dev/null | grep -oE "src=[0-9.]+" | sort -u | head -4 | logger -t "$LOG_TAG" 2>/dev/null

# BLOCKING single-instance guard: Moonlight probes trigger in bursts. Waiting
# (instead of exiting) keeps the service active, so the socket does not loop,
# and the rule check below is the fast path on the way out.
exec 9>"$LOCK_FILE"
flock 9

if forward_rule_present; then
    logger -t "$LOG_TAG" "Forward-ports already in place. Nothing to do."
    exit 0
fi

if ! query_vm_state; then
    logger -t "$LOG_TAG" "Error: hypervisor unreachable, cannot query VM '$VM_NAME' (libvirtd down?): ${VM_STATE_ERR:-no detail}. Giving up."
    exit 1
fi
logger -t "$LOG_TAG" "Current state of VM '$VM_NAME' is: '$VM_STATE'."

if [ "$VM_STATE" == "running" ]; then
    # Running without the rules: they were dropped behind the VM's back. Their
    # owner puts them back, never a second copy of its logic here.
    logger -t "$LOG_TAG" "VM '$VM_NAME' is running without its forward-ports. Re-running $STARTED_HOOK."
    if ! "$STARTED_HOOK" "$VM_NAME" started begin -; then
        logger -t "$LOG_TAG" "Error: $STARTED_HOOK failed."
        exit 1
    fi
elif [ "$VM_STATE" == "shut off" ]; then
    start_vm
else
    # Transitional state (in shutdown, pm-suspended...): wait for it to settle.
    logger -t "$LOG_TAG" "VM '$VM_NAME' is in state '$VM_STATE'. Waiting for it to settle..."
    for _ in $(seq 1 18); do
        sleep 5
        if ! query_vm_state; then
            logger -t "$LOG_TAG" "Error: hypervisor became unreachable while waiting (libvirtd down?): ${VM_STATE_ERR:-no detail}. Giving up."
            exit 1
        fi
        if [ "$VM_STATE" == "running" ] || [ "$VM_STATE" == "shut off" ]; then break; fi
    done
    if [ "$VM_STATE" == "shut off" ]; then
        start_vm
    elif [ "$VM_STATE" != "running" ]; then
        logger -t "$LOG_TAG" "VM '$VM_NAME' still in state '$VM_STATE'. Giving up."
        exit 1
    fi
fi

# The hook installs the rules; wait until they are visible.
SECONDS_WAITED=0
until forward_rule_present; do
    if [ "$SECONDS_WAITED" -ge "$RULES_WAIT_SECONDS" ]; then
        logger -t "$LOG_TAG" "Error: VM '$VM_NAME' is up but port $VM_PORT is still not forwarded after ${RULES_WAIT_SECONDS}s - check the libvirt hook journal (tag libvirt-hook-$VM_NAME-started)."
        exit 1
    fi
    sleep "$CHECK_INTERVAL"
    SECONDS_WAITED=$((SECONDS_WAITED + CHECK_INTERVAL))
done

logger -t "$LOG_TAG" "VM '$VM_NAME' is up and its forward-ports are in place."
exit 0
