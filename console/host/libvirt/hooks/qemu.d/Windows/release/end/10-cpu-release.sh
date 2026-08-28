#!/bin/bash
# Hand every CPU back to the host once the VM is gone (shutdown or hibernation).
/etc/libvirt/hooks/vm-cpu-partition.sh release "$1" >> /var/log/libvirt-cpu-hook.log 2>&1

# Switch the host CPU policy back to idle (EPP power, 3600 MHz, deep C-states).
# systemctl indirection for the same AppArmor reason as the confine hook.
systemctl start nivuus-cpu-mode@idle.service >> /var/log/libvirt-cpu-hook.log 2>&1 || true

# Rendre le node CPU de Tdarr, arrete par prepare/begin/10-cpu-confine.sh.
#
# Ce hook tourne aussi lorsqu'un hook « prepare » REFUSE le demarrage : libvirt
# execute les hooks de release pour defaire un demarrage avorte. C'est
# volontairement la seule voie de restauration — un demarrage refuse ne doit pas
# laisser l'hote ampute de son transcodage.
docker compose -f /opt/nivuus/media-manager/docker-compose.yml \
    --env-file /opt/nivuus/media-manager/.env start tdarr-node \
    >> /var/log/libvirt-cpu-hook.log 2>&1 || true
exit 0
