# shellcheck shell=bash
# Forward-ports of the Windows domain: the ONE place that says which firewalld
# zones get which DNAT towards the guest. Sourced by started/begin/rules.sh
# (adds) and stopped/end/rules.sh (removes), so start and stop can never
# disagree. It sits outside every <phase>/<sub-phase> directory on purpose:
# the libvirt dispatcher only executes files found there, never this one.
#
# Zones that are not listed below are never written to. Until 2026-11 both
# hooks wrote to EVERY active zone: the WAN and guest zones got a DNAT for RDP
# (a forwarded packet never meets a zone's input-side "drop 3389" rule), and
# the default zone, printed `internal (default)`, was split into a bogus zone
# named `(default)` on which every call failed.

MANAGED_VM_IP="192.168.3.2"

# Streaming (Apollo / Moonlight).
STREAM_TCP_PORTS="47984 47989 48010"
STREAM_UDP_PORTS="47998 47999 48000"
# Remote desktop: LAN only, never a zone facing the Internet or guests.
RDP_TCP_PORTS="3389"

# LAN zones: streaming + RDP. The default zone must be one of them:
# handle-vm-start.sh proves the hook ran by listing the DEFAULT zone's
# forward-ports.
LAN_ZONES="internal home"
# Zones facing the Internet or guests: streaming only. `external` carries the
# streaming forwards permanently as well; the stopped hook still removes them
# from its RUNTIME set, deliberately: while the VM is off, WAN packets on
# 47984/47989 must reach the host wake sockets (vm-wake-gate.py) instead of
# being DNAT'ed to a guest that is not there, which is what lets a remote
# Moonlight client wake the console.
STREAM_ONLY_ZONES="external"

# Names of the active zones: the first field of each line of
# `firewall-cmd --get-active-zones` that does not start with whitespace
# (`internal (default)` -> `internal`; indented lines list interfaces/sources).
active_zone_names() {
    firewall-cmd --get-active-zones | awk '/^[^[:space:]]/ { print $1 }'
}

# The forward-ports this domain owns, one "zone proto port" per line, for the
# allow-listed zones that are active. Fails when the active zones cannot be read.
forward_plan() {
    local active zone port
    active=$(active_zone_names) || return 1
    [ -n "$active" ] || return 1
    for zone in $LAN_ZONES $STREAM_ONLY_ZONES; do
        if ! printf '%s\n' "$active" | grep -qxF "$zone"; then
            continue
        fi
        for port in $STREAM_TCP_PORTS; do echo "$zone tcp $port"; done
        for port in $STREAM_UDP_PORTS; do echo "$zone udp $port"; done
        case " $LAN_ZONES " in
            *" $zone "*) for port in $RDP_TCP_PORTS; do echo "$zone tcp $port"; done ;;
        esac
    done
}

# Apply the plan. $1 = add|remove, $2 = syslog tag. Every rule is attempted;
# returns the number of rules firewalld refused (capped at 255), or 1 when the
# plan itself could not be computed.
#
# firewalld answers ALREADY_ENABLED / NOT_ENABLED as warnings with exit 0, so
# a non-zero status here is a real failure, never "already in that state".
apply_forward_plan() {
    local action tag plan zone proto port rule out failed
    action="$1"
    tag="$2"
    if ! plan=$(forward_plan) || [ -z "$plan" ]; then
        logger -t "$tag" "ERROR: cannot read the active firewalld zones, or none of [$LAN_ZONES $STREAM_ONLY_ZONES] is active"
        return 1
    fi
    failed=0
    while read -r zone proto port; do
        rule="port=${port}:proto=${proto}:toport=${port}:toaddr=${MANAGED_VM_IP}"
        if out=$(firewall-cmd --zone="$zone" --"$action"-forward-port="$rule" 2>&1); then
            logger -t "$tag" "zone [$zone]: $action $proto/$port ok"
        else
            failed=$((failed + 1))
            logger -t "$tag" "ERROR zone [$zone]: $action $proto/$port failed: $out"
        fi
    done <<< "$plan"
    [ "$failed" -gt 255 ] && failed=255
    return "$failed"
}
