#!/bin/bash
# Dynamic host/VM CPU partitioning.
#
# The Windows VM pins its vCPUs and emulator threads to the P-cores. Instead of
# removing those CPUs from the scheduler for the whole uptime (isolcpus, which
# leaves the host on the 8 E-cores even when the VM is shut off), this script
# confines the host's cgroups to the CPUs the VM does not pin, and only while
# the VM actually runs.
#
#   confine [vm]  - host slices restricted to the CPUs the VM does not pin
#   release [vm]  - host slices given every online CPU back
#   status  [vm]  - show the current split
#
# The domain name defaults to Windows; the libvirt hooks pass the one they fired
# for, so the same script serves any guest.
#
# The VM itself lives in machine.slice, which is never touched, so it keeps the
# full P-core set. Kernel threads are outside the cgroup tree and stay global.
#
# Called by the libvirt hooks (prepare/begin, release/end) and re-asserted by
# vm-idle-shutdown.sh. The cpusets are set --runtime: a reboot always comes back
# to the unrestricted state, so a half-applied partition cannot survive.
#
# Fail-open by design: on any error the current cpuset is left untouched.

set -u

VM_NAME="${2:-${NIVUUS_VM_NAME:-Windows}}"
HOST_SLICES=(system.slice user.slice init.scope)
MIN_HOST_CPUS=4               # never strangle the host below this
LOG_TAG="vm-cpu-partition"

log() { logger -t "$LOG_TAG" "$*"; echo "$(date '+%F %T') $*"; }

# Source of the domain XML.
#
# NEVER call virsh from inside a libvirt hook. Hooks run while libvirtd holds
# the domain job lock and waits for them to return, so a virsh call re-enters
# libvirtd, which cannot answer: deadlock. This is what broke every VM start on
# 2026-07-22 — `virsh start Windows` never returned, virsh clients piled up,
# and bind-vfio-gpu.sh (which runs after this hook in find(1) order) was never
# even reached, so the GPU was never handed to vfio.
#
# libvirt already feeds the domain XML to its qemu hooks on stdin, and the
# dispatcher (/etc/libvirt/hooks/qemu) runs sub-hooks with `"$file" "$@"`
# without consuming it, so it arrives here intact. Outside a hook — `status`,
# or `release` re-asserted by vm-idle-shutdown.sh — there is no XML on stdin
# and virsh is safe, because no hook is holding libvirtd.
#
# Note: reading stdin consumes it for the sibling hooks that run after this
# one. None of them use it today (bind-vfio-gpu.sh does not), but a future hook
# needing the XML must read it itself rather than rely on ordering.
domain_xml() {
    local xml=""
    if [ ! -t 0 ]; then
        xml="$(timeout 2 cat 2>/dev/null)"
    fi
    case "$xml" in
        *"<domain"*) printf '%s' "$xml"; return 0 ;;
    esac
    virsh dumpxml "$VM_NAME" 2>/dev/null
}

# CPUs the VM pins (vcpupin + emulatorpin + iothreadpin), subtracted from the
# online set. Prints "host_cpus|vm_cpus" as kernel range lists, or nothing if
# the result would be unusable.
compute_partition() {
    domain_xml | python3 -c '
import sys, xml.etree.ElementTree as ET

def parse(spec):
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part or part.startswith("^"):
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out

def fmt(cpus):
    cpus, out, i = sorted(cpus), [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else "%d-%d" % (cpus[i], cpus[j]))
        i = j + 1
    return ",".join(out)

try:
    root = ET.fromstring(sys.stdin.read())
except Exception:
    sys.exit(1)

vm = set()
for tune in root.findall("cputune"):
    for tag in ("vcpupin", "emulatorpin", "iothreadpin"):
        for el in tune.findall(tag):
            vm |= parse(el.get("cpuset", ""))

with open("/sys/devices/system/cpu/online") as fh:
    online = parse(fh.read().strip())

host = online - vm
if not vm or not host:
    sys.exit(2)
print("%s|%s" % (fmt(host), fmt(vm)))
' 2>/dev/null
}

count_cpus() {
    python3 - "$1" <<'PY' 2>/dev/null
import sys
n = 0
for part in sys.argv[1].split(","):
    if "-" in part:
        lo, hi = part.split("-", 1)
        n += int(hi) - int(lo) + 1
    elif part:
        n += 1
print(n)
PY
}

apply_cpus() {
    local cpus="$1" slice out rc=0
    for slice in "${HOST_SLICES[@]}"; do
        # Do not pipe systemctl into logger: that would mask its exit status.
        if ! out="$(systemctl set-property --runtime "$slice" "AllowedCPUs=$cpus" 2>&1)"; then
            log "WARN: could not set AllowedCPUs=$cpus on $slice: $out"
            rc=1
        fi
    done
    return $rc
}

online_cpus() { tr -d '\n' < /sys/devices/system/cpu/online; }

case "${1:-}" in
    confine)
        partition="$(compute_partition)"
        if [ -z "$partition" ]; then
            log "no usable vCPU pinning found for $VM_NAME - leaving cpusets untouched"
            exit 0
        fi
        host_cpus="${partition%%|*}"
        vm_cpus="${partition##*|}"
        if [ "$(count_cpus "$host_cpus")" -lt "$MIN_HOST_CPUS" ]; then
            log "refusing to confine host to '$host_cpus' (< $MIN_HOST_CPUS CPUs)"
            exit 0
        fi
        log "VM starting - confining host to CPUs $host_cpus (VM pins $vm_cpus)"
        apply_cpus "$host_cpus"
        ;;
    release)
        cpus="$(online_cpus)"
        [ -n "$cpus" ] || exit 0
        log "VM stopped - releasing all CPUs ($cpus) back to the host"
        apply_cpus "$cpus"
        ;;
    status)
        for slice in "${HOST_SLICES[@]}"; do
            path="/sys/fs/cgroup/$slice/cpuset.cpus.effective"
            echo "$slice: $(cat "$path" 2>/dev/null || echo '?')"
        done
        echo "online: $(online_cpus)"
        echo "partition (host|vm): $(compute_partition || echo 'n/a')"
        ;;
    *)
        echo "usage: $(basename "$0") {confine|release|status}" >&2
        exit 1
        ;;
esac
exit 0
