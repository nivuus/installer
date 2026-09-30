#!/bin/bash
# Control channel for the Windows VM.
#
# Run once per connection by nivuus-vm-control@.service (systemd socket
# activation, Accept=yes): one request line on stdin, one reply line on
# stdout. The verbs are an allow-list and NONE takes an argument - the VM is
# always the one this host owns, so a client cannot name another target.
#
#   wake -> ask systemd to start nivuus-vm-wake.service (handle-vm-start.sh).
#           --no-block: handle-vm-start.sh waits up to 180 s for the guest IP,
#           and the caller only needs to know the start was REQUESTED.
#   busy -> record "a desk app window is open right now" for
#           vm-idle-shutdown.sh, which reads it on its next pass.

STATE_DIR="${VM_IDLE_STATE_DIR:-/run/nivuus-vm-idle}"
ACTIVITY_FILE="$STATE_DIR/app-activity"
WAKE_UNIT="nivuus-vm-wake.service"
LOG_TAG="vm-control"

reply() { printf '%s\n' "$1"; }

# 17 bytes: one more than the longest verb + newline would need, so an
# over-long request is cut instead of buffered without bound.
if ! IFS= read -r -t 5 -n 17 verb; then
    reply "err no-request"
    exit 0
fi

case "$verb" in
    wake)
        if out=$(systemctl start --no-block "$WAKE_UNIT" 2>&1); then
            logger -t "$LOG_TAG" "wake requested"
            reply "ok"
        else
            logger -t "$LOG_TAG" "wake failed: ${out:0:200}"
            reply "err wake-failed"
        fi
        ;;
    busy)
        # Write-then-rename: the idle check must never read a half-written file.
        if mkdir -p "$STATE_DIR" \
            && date +%s > "$ACTIVITY_FILE.tmp" \
            && mv "$ACTIVITY_FILE.tmp" "$ACTIVITY_FILE"; then
            reply "ok"
        else
            logger -t "$LOG_TAG" "busy: cannot write $ACTIVITY_FILE"
            reply "err busy-failed"
        fi
        ;;
    *)
        reply "err unknown-verb"
        ;;
esac
exit 0
