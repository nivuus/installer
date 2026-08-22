#!/bin/bash
# Hand every CPU back to the host once the VM is gone (shutdown or hibernation).
/etc/libvirt/hooks/vm-cpu-partition.sh release "$1" >> /var/log/libvirt-cpu-hook.log 2>&1

# Switch the host CPU policy back to idle (EPP power, 3600 MHz, deep C-states).
# systemctl indirection for the same AppArmor reason as the confine hook.
systemctl start nivuus-cpu-mode@idle.service >> /var/log/libvirt-cpu-hook.log 2>&1 || true
exit 0
