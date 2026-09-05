#!/bin/bash
# policy: allow-fr-file - ruled 2026-09-05, reason below.
# This marker is the escape hatch the socle PROVIDES, and it requires a
# written reason: it is a named, dated exception, not the control being
# lowered. The real translation is tracked in docs/console-dettes.md
# under CI-3.
# 3 lines carrying the measurement 'fuser does not see containerised
# processes (tested: silent on llama-server)', which is the reason for the
# direct /proc scan.

exec > /var/log/libvirt-gpu-hook.log 2>&1
set -x
date
echo "==== [HOOK BEGIN] ===="

lspci -k | grep -A5 'VGA compatible controller.*NVIDIA'

systemctl stop nvidia-persistenced
# ollama runs in docker (nivuus-ollama) and needs the GPU: stop it while the VM owns the card
docker compose -f /opt/nivuus/ollama/docker-compose.yml --env-file /opt/nivuus/ollama/.env stop ollama
# tdarr-node-nvenc encodes on the same card: stop it too. An in-flight ffmpeg dies with it;
# Tdarr requeues the file after 300s of limbo and the QSV node picks it up later.
docker compose -f /opt/nivuus/media-manager/docker-compose.yml --env-file /opt/nivuus/media-manager/.env stop tdarr-node-nvenc

# modprobe -r échoue si un process tient encore /dev/nvidia*, et la VM démarrerait alors
# sans GPU. `fuser` ne voit PAS les process conteneurisés (testé : muet sur llama-server),
# d'où le scan direct de /proc. On laisse aux conteneurs le temps de rendre la carte.
# Uses find(1), not a shell loop over every /proc/*/fd.
#
# The original bash version forked a readlink per descriptor. On this host
# under load (965 processes, ~16000 fds) one scan took **40 seconds**, and the
# 15-iteration wait below therefore ran for up to 10 minutes — during which
# `virsh start` simply does not return. That is not a deadlock, but it is
# indistinguishable from one, and on 2026-07-22 it sent the whole GPU
# investigation chasing phantom hangs for hours. Measured replacement: 226 ms,
# ~180x faster, same output format.
gpu_holders() {
  find /proc/[0-9]*/fd -lname '/dev/nvidia*' -printf '%h\n' 2>/dev/null \
    | sed 's#/proc/##; s#/fd$##' | sort -u \
    | while read -r pid; do
        printf '%s(%s) ' "$pid" "$(cat "/proc/$pid/comm" 2>/dev/null)"
      done
}

for i in $(seq 1 15); do
  holders=$(gpu_holders)
  [ -z "$holders" ] && break
  echo "attente libération GPU ($i/15) — tenu par: $holders"
  sleep 1
done
if [ -n "$holders" ]; then
    # Refuse the start rather than boot the gaming VM without its GPU.
    #
    # This hook only knows how to stop the containers it names above. Anything
    # else touching the card — a user-session tool, a stray CUDA process — is
    # invisible to it: on 2026-07-22 `mcp-memory-service`, started by hand,
    # silently made every VM start fail. Refusing loudly beats the old
    # behaviour, which carried on regardless: `modprobe -r nvidia` then failed,
    # libvirt could not detach, and the start hung.
    #
    # This only aborts because the dispatcher now propagates a non-zero code
    # for `prepare` hooks — it used to `exit 0` unconditionally, so a hook
    # could not refuse anything. See /etc/libvirt/hooks/qemu.
    echo "ÉCHEC : /dev/nvidia* toujours tenu par $holders"
    echo "Le GPU ne peut pas être cédé à vfio — démarrage refusé."
    echo "Arrêtez le ou les processus ci-dessus, puis relancez la VM."

    # Put back what we stopped: the start is being refused, so nothing will
    # detach the card and the host must not be left degraded.
    systemctl start nvidia-persistenced || true
    docker compose -f /opt/nivuus/ollama/docker-compose.yml --env-file /opt/nivuus/ollama/.env start ollama || true
    docker compose -f /opt/nivuus/media-manager/docker-compose.yml --env-file /opt/nivuus/media-manager/.env start tdarr-node-nvenc || true

    echo "==== [HOOK END - REFUS] ===="
    exit 1
fi

# Unload in dependency order. `modprobe -r nvidia` alone ALWAYS fails once
# anything has used CUDA or persistenced has run, because nvidia_uvm and
# nvidia_modeset hold a reference (observed 2026-07-22: nvidia refcount 13,
# "used by: nvidia_uvm,nvidia_modeset"). The old code ignored the failure, left
# nvidia bound to the card, and the VM start then wedged.
for m in nvidia_drm nvidia_uvm nvidia_modeset nvidia; do
    lsmod | grep -q "^$m " || continue
    modprobe -r "$m" || echo "ATTENTION : impossible de décharger $m"
done

if lsmod | grep -q '^nvidia '; then
    # Not fatal: libvirt owns the detach (managed='yes') and unbinds through
    # sysfs, which usually succeeds even with the module loaded. Log loudly and
    # let libvirt try, so a failure surfaces as a clean libvirt error instead of
    # a hang.
    echo "ATTENTION : le module nvidia est toujours chargé — le détachement de libvirt peut échouer"
    lsmod | grep '^nvidia'
fi

modprobe vfio
modprobe vfio_iommu_type1
modprobe vfio_pci

# NO virsh call here. Deliberately.
#
# The three <hostdev> entries of the Windows domain are managed='yes', so
# libvirt detaches them from their host driver and binds vfio-pci by itself,
# right after this hook returns. The `virsh nodedev-detach` calls that used to
# live here were therefore redundant — and actively harmful: this hook runs
# *inside* libvirtd while it is starting the domain, so calling virsh re-enters
# libvirtd and deadlocks it. Observed 2026-07-22: `virsh start Windows` never
# returned, five bind-vfio-gpu.sh instances piled up and survived a libvirtd
# restart, the GPU was never detached, and persistenced stayed down (breaking
# every --gpus container). The same recursion is documented for the rebind hook.
#
# The dead `bind_check` helper went with them: it built its sysfs path with
# underscores (/sys/bus/pci/devices/0000:01_00_1/driver instead of
# 0000:01:00.1), so its readlink always failed with "basename: missing operand"
# and the "already vfio-pci, skip" branch never once ran.
#
# This hook's only remaining job is to free the card from userspace so that
# libvirt's own detach can succeed.

# Nothing is restored here on failure: starting persistenced back at this point
# would reopen /dev/nvidia-modeset and grab the card again, right before
# libvirt tries to detach it. If the start does fail, libvirt runs the release
# hooks, and rebind-host-gpu.sh restores persistenced there.

lspci -k | grep -A5 'VGA compatible controller.*NVIDIA'
echo "==== [HOOK END] ===="
