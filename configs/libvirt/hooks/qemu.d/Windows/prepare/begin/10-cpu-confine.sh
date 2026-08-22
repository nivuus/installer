#!/bin/bash
# Confine the host cgroups to the CPUs the VM does not pin, for as long as it runs.
# Real logic lives in the repo (scripts/vm-cpu-partition.sh); never fail the VM start.
/etc/libvirt/hooks/vm-cpu-partition.sh confine "$1" >> /var/log/libvirt-cpu-hook.log 2>&1

# Switch the host CPU policy to gaming (EPP performance + C6 latency ceiling).
# Via systemctl, not a direct call: the libvirtd AppArmor profile allows exec of
# /usr/bin/systemctl but NOT /usr/local/bin/optimize-cpu-thermal.sh (PUx covers
# /usr/bin/* only). Never block the VM start on a policy failure -> exit 0.
systemctl start nivuus-cpu-mode@gaming.service >> /var/log/libvirt-cpu-hook.log 2>&1 || true
exit 0
