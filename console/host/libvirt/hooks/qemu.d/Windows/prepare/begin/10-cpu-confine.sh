#!/bin/bash
# Confine the host cgroups to the CPUs the VM does not pin, for as long as it runs.
# Real logic lives in the repo (console/host/vm-cpu-partition.sh); never fail the VM start.
/etc/libvirt/hooks/vm-cpu-partition.sh confine "$1" >> /var/log/libvirt-cpu-hook.log 2>&1

# Switch the host CPU policy to gaming (EPP performance + C6 latency ceiling).
# Via systemctl, not a direct call: the libvirtd AppArmor profile allows exec of
# /usr/bin/systemctl but NOT /usr/local/bin/optimize-cpu-thermal.sh (PUx covers
# /usr/bin/* only). Never block the VM start on a policy failure -> exit 0.
systemctl start nivuus-cpu-mode@gaming.service >> /var/log/libvirt-cpu-hook.log 2>&1 || true

# Le node CPU de Tdarr s'arrete aussi, pas seulement son jumeau NVENC.
#
# bind-vfio-gpu.sh stoppe tdarr-node-nvenc parce qu'un conteneur tenant
# /dev/nvidia* empeche le detachement vfio : c'est une contrainte materielle, et
# elle ne dit rien du node CPU, qui restait donc a transcoder pendant les
# parties. Ce n'est pas une question de watts — mesure du 2026-08-26, x265 sur
# deux coeurs E coute 2,8 W sur un budget RAPL de 60 W, et le partitionnement
# cpuset tient deja le node hors des coeurs P. C'est une question de STABILITE :
# le compose documente quatre gels plateforme correles a du transcodage soutenu
# (04, 05, 07/08 + un demarrage jamais abouti), et c'est precisement la raison
# pour laquelle ce node a ete bride de 4 a 2 coeurs. Faire tourner cette charge
# pendant qu'une VM epingle les huit coeurs P, c'est reproduire l'etat exact ou
# ces gels sont survenus.
#
# -t 5 et non le delai par defaut : ce hook s'execute AVANT le demarrage de la
# VM et libvirt attend qu'il rende la main. Le ffmpeg en vol meurt avec le
# conteneur ; Tdarr remet le fichier en file apres 300 s de limbo, exactement
# comme pour le node NVENC.
#
# Le pendant se trouve dans release/end/10-cpu-release.sh, qui tourne aussi
# quand un hook « prepare » REFUSE le demarrage : rien ne reste arrete par un
# demarrage avorte.
docker compose -f /opt/nivuus/media-manager/docker-compose.yml \
    --env-file /opt/nivuus/media-manager/.env stop -t 5 tdarr-node \
    >> /var/log/libvirt-cpu-hook.log 2>&1 || true
exit 0
