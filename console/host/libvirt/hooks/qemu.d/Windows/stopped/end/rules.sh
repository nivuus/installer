#!/bin/bash
# policy: allow-fr-file - ruled 2026-09-05, reason below.
# This marker is the escape hatch the socle PROVIDES, and it requires a
# written reason: it is a named, dated exception, not the control being
# lowered. The real translation is tracked in docs/console-dettes.md
# under CI-3.
# 9 lines of libvirt hook comments, measured on this machine.

# Script exécuté par le répartiteur quand la VM "Windows" est arrêtée (stopped/end)
# Modifié pour supprimer les règles de TOUTES les zones actives.

# === Configuration ===
MANAGED_VM_IP="192.168.3.2"
TCP_PORTS="3389 47984 47989 48010"
UDP_PORTS="47998 47999 48000"
LOG_TAG="libvirt-hook-Windows-stopped"
# === Fin Configuration ===

logger -t "$LOG_TAG" "Hook triggered: Suppression des règles firewalld pour $1 de toutes les zones actives..."

# Récupérer les zones actives
ACTIVE_ZONES=$(firewall-cmd --get-active-zones | grep -v ':' | grep -v 'interfaces' | xargs)

if [ -z "$ACTIVE_ZONES" ]; then
    logger -t "$LOG_TAG" "AVERTISSEMENT: Impossible de récupérer les zones actives ou aucune zone active trouvée! Tentative de nettoyage quand même..."
    # On pourrait définir une liste de zones par défaut ici si nécessaire
    # ACTIVE_ZONES="home public external" # Exemple
else
    logger -t "$LOG_TAG" "Zones actives détectées: $ACTIVE_ZONES"
fi

# Boucle sur chaque zone active (ou la liste par défaut si définie)
for zone in $ACTIVE_ZONES; do
     logger -t "$LOG_TAG" "Traitement de la zone: [$zone]"

    # Suppression règles TCP pour cette zone (SANS --wait, erreurs masquées)
    for port in $TCP_PORTS; do
         RULE_TO_REMOVE="port=${port}:proto=tcp:toport=${port}:toaddr=${MANAGED_VM_IP}"
         firewall-cmd --zone="$zone" --remove-forward-port="$RULE_TO_REMOVE" >/dev/null 2>&1
         # logger -t "$LOG_TAG" "Zone [$zone]: Tentative suppression règle TCP port $port" # Décommentez pour log détaillé
    done

    # Suppression règles UDP pour cette zone (SANS --wait, erreurs masquées)
     for port in $UDP_PORTS; do
         RULE_TO_REMOVE="port=${port}:proto=udp:toport=${port}:toaddr=${MANAGED_VM_IP}"
         firewall-cmd --zone="$zone" --remove-forward-port="$RULE_TO_REMOVE" >/dev/null 2>&1
         # logger -t "$LOG_TAG" "Zone [$zone]: Tentative suppression règle UDP port $port" # Décommentez pour log détaillé
    done
done # Fin boucle zones

logger -t "$LOG_TAG" "Fin tentative de suppression des règles."
