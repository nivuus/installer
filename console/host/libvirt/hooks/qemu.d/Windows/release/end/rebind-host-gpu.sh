#!/bin/bash
# Hot rebind RTX après VM via libvirt/udev
#
# Logging added 2026-07-22: this hook ran blind. Its twin bind-vfio-gpu.sh
# redirects to a log file, this one did not, so every failure below was
# invisible — and the libvirt dispatcher reports code 0 regardless.
exec > /var/log/libvirt-gpu-rebind.log 2>&1
set -x
date
logger -t gpu-rebind "Début du rebind GPU (managed='yes', pas de virsh)"

# NO `virsh nodedev-reattach` here. Deliberately — it deadlocked libvirtd.
#
# The three <hostdev> of the Windows domain are managed='yes', so libvirt
# reattaches them to their host driver (01:00.0 -> nvidia, 01:00.1 ->
# snd_hda_intel, 03:00.0 -> vfio-pci for the NVMe) by itself as part of domain
# teardown, before this release hook even runs. The explicit reattach was
# therefore redundant — exactly like the nodedev-detach we already removed from
# bind-vfio-gpu.sh once managed='yes' proved to do the detach unaided.
#
# It was also actively dangerous: when a `prepare` hook REFUSES a start (a GPU
# holder blocks the handover to vfio), libvirt runs these release hooks to undo
# the aborted start *while still holding the domain job lock*. `virsh
# nodedev-reattach` then re-enters libvirtd, blocks on that lock, and wedges the
# daemon — a running worker thread keeps the /run/libvirtd.pid flock so no fresh
# libvirtd can start, and only a reboot clears it. Found 2026-07-22 the first
# time the refusal path was exercised end-to-end. Same recursion trap as the
# bind side, documented in CLAUDE.md.
#
# Wait (via sysfs, no libvirtd call) for libvirt's managed reattach to have put
# the GPU back on nvidia before we touch modules / persistenced.
GPU_PCI=0000:01:00.0
for i in $(seq 1 15); do
    drv=$(basename "$(readlink -f "/sys/bus/pci/devices/$GPU_PCI/driver" 2>/dev/null)" 2>/dev/null)
    [ "$drv" = nvidia ] && break
    echo "waiting for libvirt's managed reattach of the GPU to nvidia ($i/15, driver=${drv:-none})"
    sleep 1
done
[ "$drv" = nvidia ] || echo "WARNING: GPU still on '${drv:-none}', not nvidia — managed reattach did not run as expected"

modprobe -r vfio_pci
modprobe -r vfio_iommu_type1
modprobe -r vfio

modprobe nvidia

# Start the persistence daemon BEFORE anything opens the GPU.
#
# bind-vfio-gpu.sh stops it on VM start and nothing ever restarted it, which
# broke two things at once (found 2026-07-22):
#   1. nvidia-container-toolkit refuses to create any --gpus container without
#      /run/nvidia-persistenced/socket, so the `docker compose up` calls below
#      failed with "exit 127 ... no such file or directory" — silently, since
#      this hook checks nothing and always returns 0.
#   2. suspected: with persistence mode off, the first CUDA client to touch a
#      freshly rebound GPU initialises it into a state where NVML keeps working
#      but every CUDA call returns 999 until the next reboot.
# Hence started here, before the containers, not after.
#
# But wait for the driver to have registered the card first. Starting
# persistenced too early loses a race: on 2026-07-22 the hook launched it ~4 s
# after the rebind, it hung on GPU init, and systemd killed it on its start
# timeout ("start operation timed out. Terminating."). Started by hand a minute
# later it came up immediately. /proc/driver/nvidia/gpus/*/information is only
# readable once the driver owns the GPU, and reading a file needs no exec — see
# the nvidia-smi note below for why that matters here.
for i in $(seq 1 30); do
    grep -qs . /proc/driver/nvidia/gpus/*/information && break
    echo "waiting for the nvidia driver to register the GPU ($i/30)"
    sleep 1
done
grep -qs . /proc/driver/nvidia/gpus/*/information \
    || echo "WARNING: the driver never registered the GPU — persistenced will likely fail"

systemctl start nvidia-persistenced
for i in $(seq 1 10); do
    [ -S /run/nvidia-persistenced/socket ] && break
    echo "waiting for the persistenced socket ($i/10)"
    sleep 1
done
[ -S /run/nvidia-persistenced/socket ] || echo "WARNING: no persistenced socket — GPU containers will fail to start"

lspci -k | grep -A5 'VGA compatible controller.*NVIDIA'

# Do NOT exec nvidia-smi here. It failed with "/usr/bin/nvidia-smi: Permission
# denied" and no AppArmor DENIED line in dmesg (2026-07-22). Reason: hooks run
# inheriting the libvirtd profile, which grants `/usr/bin/* PUx`, but AppArmor
# resolves symlinks first and /usr/bin/nvidia-smi is an update-alternatives
# chain ending at /usr/lib/nvidia/current/nvidia-smi — outside every PUx path.
# CLAUDE.md documents this trap for /usr/local/*; it is in fact wider: any
# alternatives-managed binary is affected. Reading procfs gives the same
# confirmation without an exec.
if grep -qs . /proc/driver/nvidia/gpus/*/information; then
    grep -hE '^(Model|GPU UUID)' /proc/driver/nvidia/gpus/*/information
else
    echo 'NVIDIA toujours non opérationnel'
fi

# Regenerate the CDI spec. THIS is what silently killed CUDA on the host after
# every gaming session (root-caused 2026-07-22).
#
# nvidia_uvm gets a *dynamically allocated* char-device major, so unloading and
# reloading it — which bind-vfio-gpu.sh must do to hand the card to vfio — can
# change it (observed: 510 -> 511). /var/run/cdi/nvidia.yaml is a frozen
# snapshot of the device nodes, generated once at boot and never refreshed, so
# every `--gpus` container was then given /dev/nvidia-uvm with a stale major.
# The device opened fine but was not the UVM driver, and every CUDA entry point
# returned 999 "unknown error" — while nvidia-smi kept working, because NVML
# only touches /dev/nvidiactl whose major (195) is static.
#
# That is why nothing fixed it but a reboot: /var/run is a tmpfs, so the spec
# was rebuilt at boot. Reloading the modules by hand made it *worse*, changing
# the major again. Symptom in practice: ollama silently fell back to CPU after
# every VM cycle.
#
# nvidia-ctk lives at a real /usr/bin path, so unlike nvidia-smi it survives the
# libvirtd AppArmor profile (see the note above).
nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml \
    || echo "WARNING: could not regenerate the CDI spec — GPU containers will fall back to CPU"

# ollama runs in docker; `up -d` (re)creates with the GPU device now that nvidia is back
docker compose -f /opt/nivuus/ollama/docker-compose.yml --env-file /opt/nivuus/ollama/.env up -d
# le node NVENC de Tdarr reprend la file d'encodage sur la carte redevenue disponible
docker compose -f /opt/nivuus/MediaManager/docker-compose.yml --env-file /opt/nivuus/MediaManager/.env up -d tdarr-node-nvenc

logger -t gpu-rebind "Fin du rebind GPU (managed reattach + modules/persistenced/CDI)"
