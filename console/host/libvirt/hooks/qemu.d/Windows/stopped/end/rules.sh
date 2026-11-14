#!/bin/bash
# libvirt `stopped/end` hook of the Windows domain: removes its forward-ports.
# Which zones and ports: ../../forward-ports.sh, shared with the started hook,
# so this removes exactly what that one added.
#
# Exit status: non-zero when firewalld refused any removal. libvirt ignores it
# at this phase (the shutdown has already happened, and the dispatcher exits 0
# for every phase but `prepare`); the status is for the journal and for anyone
# running the hook by hand.

LOG_TAG="libvirt-hook-Windows-stopped"
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../forward-ports.sh
. "$HOOK_DIR/../../forward-ports.sh" || {
    logger -t "$LOG_TAG" "ERROR: cannot read $HOOK_DIR/../../forward-ports.sh"
    exit 1
}

logger -t "$LOG_TAG" "Hook triggered for $1: removing forward-ports."

apply_forward_plan remove "$LOG_TAG"
rc=$?
logger -t "$LOG_TAG" "Done removing forward-ports (failures: $rc)."
exit "$rc"
