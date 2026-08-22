#!/bin/bash
# Script exécuté par le répartiteur quand la VM "Windows" est démarrée (started/begin)
# Modifié pour appliquer les règles à TOUTES les zones actives.

# === Configuration ===
MANAGED_VM_IP="192.168.3.2"
TCP_PORTS="3389 47984 47989 48010"
UDP_PORTS="47998 47999 48000"
LOG_TAG="libvirt-hook-Windows-started"
# === Fin Configuration ===

logger -t "$LOG_TAG" "Hook triggered: Ajout des règles firewalld pour $1 dans toutes les zones actives..."

# Délai simple (car --wait non supporté sur cette version de firewalld)
logger -t "$LOG_TAG" "Attente 2 secondes..."
sleep 2

# Récupérer les zones actives (en filtrant les lignes non pertinentes)
# 'grep -v' exclut les lignes avec ':' (interfaces) et le mot 'interfaces'
# 'xargs' met tout sur une seule ligne, facile à boucler
ACTIVE_ZONES=$(firewall-cmd --get-active-zones | grep -v ':' | grep -v 'interfaces' | xargs)

if [ -z "$ACTIVE_ZONES" ]; then
    logger -t "$LOG_TAG" "ERREUR: Impossible de récupérer les zones actives ou aucune zone active trouvée!"
    # Vous pourriez vouloir ajouter une zone par défaut ici, ex: ACTIVE_ZONES="public"
    exit 1 # Sortir si aucune zone trouvée
fi
logger -t "$LOG_TAG" "Zones actives détectées: $ACTIVE_ZONES"

# Boucle sur chaque zone active trouvée
for zone in $ACTIVE_ZONES; do
    logger -t "$LOG_TAG" "Traitement de la zone: [$zone]"

    # Ajout des règles TCP pour cette zone (SANS --wait)
    for port in $TCP_PORTS; do
        ERR_OUTPUT=$(firewall-cmd --zone="$zone" --add-forward-port=port=$port:proto=tcp:toport=$port:toaddr=$MANAGED_VM_IP 2>&1)
        ADD_EXIT_CODE=$?
        if [ $ADD_EXIT_CODE -eq 0 ]; then
             logger -t "$LOG_TAG" "Zone [$zone]: Regle TCP ajoutee pour port $port";
        else
             logger -t "$LOG_TAG" "Zone [$zone]: ERREUR ajout regle TCP port $port (Code: $ADD_EXIT_CODE). Sortie: $ERR_OUTPUT";
        fi
    done

    # Ajout des règles UDP pour cette zone (SANS --wait)
    for port in $UDP_PORTS; do
        ERR_OUTPUT=$(firewall-cmd --zone="$zone" --add-forward-port=port=$port:proto=udp:toport=$port:toaddr=$MANAGED_VM_IP 2>&1)
        ADD_EXIT_CODE=$?
        if [ $ADD_EXIT_CODE -eq 0 ]; then
             logger -t "$LOG_TAG" "Zone [$zone]: Regle UDP ajoutee pour port $port";
        else
             logger -t "$LOG_TAG" "Zone [$zone]: ERREUR ajout regle UDP port $port (Code: $ADD_EXIT_CODE). Sortie: $ERR_OUTPUT";
        fi
    done
done # Fin de la boucle sur les zones

logger -t "$LOG_TAG" "Fin ajout des règles."
exit 0
