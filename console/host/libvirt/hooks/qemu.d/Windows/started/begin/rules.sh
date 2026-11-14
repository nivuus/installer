#!/bin/bash
# libvirt `started/begin` hook of the Windows domain: adds its forward-ports.
# Which zones and ports: ../../forward-ports.sh, shared with the stopped hook.
#
# Exit status: non-zero when firewalld refused any rule this hook attempted.
# Two callers read it differently, on purpose:
#   - the libvirt dispatcher (/etc/libvirt/hooks/qemu) logs it and exits 0 for
#     every phase but `prepare`: libvirt STOPS the domain when a `started` hook
#     fails (qemuProcessStart -> goto stop), and a missing forward-port is no
#     reason to kill a running console;
#   - handle-vm-start.sh, which re-runs this hook when the rules went missing,
#     propagates it, so the wake unit fails visibly instead of reporting success.

LOG_TAG="libvirt-hook-Windows-started"
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../forward-ports.sh
. "$HOOK_DIR/../../forward-ports.sh" || {
    logger -t "$LOG_TAG" "ERROR: cannot read $HOOK_DIR/../../forward-ports.sh"
    exit 1
}

logger -t "$LOG_TAG" "Hook triggered for $1: adding forward-ports."
# firewalld 2.x has no --wait; give it a moment after the domain came up.
sleep 2

apply_forward_plan add "$LOG_TAG"
rc=$?
logger -t "$LOG_TAG" "Done adding forward-ports (failures: $rc)."
exit "$rc"
