#!/bin/bash
# Graceful shutdown script for UPS critical battery event
# Called by: mqtt-system-agent UpsMonitor + NUT upsmon SHUTDOWNCMD
# Total budget: ~90s (well within 2 min UPS runtime at critical)

set -euo pipefail

LOG_TAG="ups-shutdown"

log() {
  logger -t "$LOG_TAG" "$1"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "=== UPS GRACEFUL SHUTDOWN INITIATED ==="

# Phase 1: Shutdown Windows VM (up to 60s)
log "Phase 1: Shutting down Windows VM..."
VM_NAME="win10"
if virsh -c qemu:///system domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
  virsh -c qemu:///system shutdown "$VM_NAME"
  log "Sent ACPI shutdown to $VM_NAME, waiting up to 60s..."
  for i in $(seq 1 60); do
    if ! virsh -c qemu:///system domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
      log "$VM_NAME shut down after ${i}s"
      break
    fi
    sleep 1
  done
  # Force destroy if still running
  if virsh -c qemu:///system domstate "$VM_NAME" 2>/dev/null | grep -q "running"; then
    log "$VM_NAME still running after 60s, force destroying..."
    virsh -c qemu:///system destroy "$VM_NAME" 2>/dev/null || true
  fi
else
  log "$VM_NAME is not running, skipping"
fi

# Phase 2: Stop Docker containers (15s timeout)
log "Phase 2: Stopping Docker containers..."
mapfile -t running_containers < <(docker ps -q)
if [ "${#running_containers[@]}" -gt 0 ]; then
  docker stop --time 15 "${running_containers[@]}" 2>/dev/null || true
fi
log "Docker containers stopped"

# Phase 3: System shutdown
log "Phase 3: Initiating system shutdown..."
shutdown -h now
