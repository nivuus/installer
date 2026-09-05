#!/bin/bash
# policy: allow-fr-file - ruled 2026-09-05, reason below.
# This marker is the escape hatch the socle PROVIDES, and it requires a
# written reason: it is a named, dated exception, not the control being
# lowered. The real translation is tracked in docs/console-dettes.md
# under CI-3.
# 28 lines. The French log lines here are what the operator reads when
# wake-on-demand fails: they are themselves the interface.


# === Configuration ===
VM_NAME="Windows"           # <<< REMPLACEZ CECI
VM_INTERFACE="localBridge" # <<< REMPLACEZ CECI (ex: 'eth0' si vu comme ça par libvirt, ou le nom de l'interface sur l'hôte)
VM_PORT="47984"
# ====================

LOG_TAG="vm-trigger-$VM_PORT"
MAX_WAIT_SECONDS=180 # Temps max d'attente pour l'IP (secondes)
CHECK_INTERVAL=5     # Intervalle de vérification (secondes)
LOCK_FILE="${VM_TRIGGER_LOCK:-/run/vm-trigger-start.lock}"

# Interroge l'état du domaine en distinguant "état inconnu" de "hyperviseur
# injoignable". Renseigne VM_STATE et VM_STATE_ERR ; retourne non-zéro si virsh
# a échoué ou n'a rien renvoyé.
#
# Sans cette distinction, un libvirtd mort donnait VM_STATE="" (stderr jeté),
# la chaîne vide tombait dans la branche "état transitoire" et le script
# attendait 90 s avant d'échouer — en boucle, à chaque sonde Moonlight, puisque
# le socket redéclenche le service. Incident du 2026-08-24.
query_vm_state() {
    local err_file rc
    err_file=$(mktemp)
    VM_STATE=$(LANG=C virsh domstate "$VM_NAME" 2>"$err_file" | head -n1)
    rc=$?
    VM_STATE_ERR=$(tr '\n' ' ' < "$err_file" | cut -c1-200)
    rm -f "$err_file"
    [ $rc -eq 0 ] && [ -n "$VM_STATE" ]
}

logger -t "$LOG_TAG" "Service triggered for port $VM_PORT."

# Identifier l'appelant (diagnostic) : connexions récentes vers les ports de réveil
grep -hE "dport=4798[49] " /proc/net/nf_conntrack 2>/dev/null | grep -oE "src=[0-9.]+" | sort -u | head -4 | logger -t "$LOG_TAG" 2>/dev/null

# Garde mono-instance BLOQUANTE : les sondes Moonlight re-déclenchent en rafale.
# Attendre (au lieu de sortir) garde le service actif -> le socket ne re-déclenche
# pas en boucle, et le check de règle ci-dessous fait le fast-path à la sortie.
exec 9>"$LOCK_FILE"
flock 9

# Vérifier si la règle existe déjà (évite les lancements multiples ou erreurs)
EXISTING_RULE=$(firewall-cmd --list-forward-ports | grep -oE "port=${VM_PORT}:proto=tcp:toport=${VM_PORT}:toaddr=[0-9.]+")
if [ -n "$EXISTING_RULE" ]; then
    logger -t "$LOG_TAG" "Firewall rule '$EXISTING_RULE' already exists. Exiting."
    exit 0
fi

# Vérifier l'état de la VM (LANG=C pour forcer la sortie en anglais)
if ! query_vm_state; then
    logger -t "$LOG_TAG" "Error: hypervisor unreachable, cannot query VM '$VM_NAME' (libvirtd down?): ${VM_STATE_ERR:-no detail}. Giving up."
    exit 1
fi
logger -t "$LOG_TAG" "Current state of VM '$VM_NAME' is: '$VM_STATE'."

if [ "$VM_STATE" == "running" ]; then
    logger -t "$LOG_TAG" "VM '$VM_NAME' is already running. Attempting to configure firewall."
    # Passe directement à l'étape d'ajout de règle si la VM tourne déjà mais sans règle active
elif [ "$VM_STATE" == "shut off" ]; then
    logger -t "$LOG_TAG" "Starting VM '$VM_NAME'..."
    virsh start "$VM_NAME"
    if [ $? -ne 0 ]; then
        logger -t "$LOG_TAG" "Error: Failed to start VM '$VM_NAME'."
        exit 1
    fi
    sleep 5 # Petite pause pour laisser le démarrage s'initier
else
    # État transitoire (in shutdown, pm-suspended...) : attendre qu'il se stabilise
    logger -t "$LOG_TAG" "VM '$VM_NAME' is in state '$VM_STATE'. Waiting for it to settle..."
    for _ in $(seq 1 18); do
        sleep 5
        if ! query_vm_state; then
            logger -t "$LOG_TAG" "Error: hypervisor became unreachable while waiting (libvirtd down?): ${VM_STATE_ERR:-no detail}. Giving up."
            exit 1
        fi
        if [ "$VM_STATE" == "running" ] || [ "$VM_STATE" == "shut off" ]; then break; fi
    done
    if [ "$VM_STATE" == "shut off" ]; then
        logger -t "$LOG_TAG" "Starting VM '$VM_NAME' (after settle)..."
        virsh start "$VM_NAME" || { logger -t "$LOG_TAG" "Error: Failed to start VM '$VM_NAME'."; exit 1; }
        sleep 5
    elif [ "$VM_STATE" != "running" ]; then
        logger -t "$LOG_TAG" "VM '$VM_NAME' still in state '$VM_STATE'. Giving up."
        exit 1
    fi
fi

# Attendre que la VM soit prête et obtienne une IP
logger -t "$LOG_TAG" "Waiting for VM '$VM_NAME' to get an IP on interface '$VM_INTERFACE'..."
SECONDS_WAITED=0
VM_IP=""
while [ $SECONDS_WAITED -lt $MAX_WAIT_SECONDS ]; do
    # Priorité à l'agent QEMU si disponible
    VM_IP=$(virsh domifaddr "$VM_NAME" --interface "$VM_INTERFACE" --source agent 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)
    # Sinon, essayer via les baux DHCP connus par libvirt
    if [ -z "$VM_IP" ]; then
        VM_IP=$(virsh domifaddr "$VM_NAME" --interface "$VM_INTERFACE" --source lease 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)
    fi

    if [ -n "$VM_IP" ]; then
        logger -t "$LOG_TAG" "VM '$VM_NAME' obtained IP: $VM_IP after $SECONDS_WAITED seconds."
        break
    fi

    # Vérifier si la VM est toujours en cours d'exécution
    if ! query_vm_state; then
        logger -t "$LOG_TAG" "Error: hypervisor unreachable while waiting for IP (libvirtd down?): ${VM_STATE_ERR:-no detail}. Aborting."
        exit 1
    fi
    if [ "$VM_STATE" != "running" ]; then
        logger -t "$LOG_TAG" "VM '$VM_NAME' is no longer running (state: $VM_STATE). Aborting."
        exit 1
    fi

    sleep $CHECK_INTERVAL
    SECONDS_WAITED=$((SECONDS_WAITED + CHECK_INTERVAL))
done

if [ -z "$VM_IP" ]; then
    logger -t "$LOG_TAG" "Error: VM '$VM_NAME' did not get an IP within $MAX_WAIT_SECONDS seconds."
    # Optionnel: Tenter un arrêt forcé? Ex: virsh destroy "$VM_NAME"
    exit 1
fi

# Ajouter la règle de redirection firewalld (non-permanente, gérée dynamiquement)
logger -t "$LOG_TAG" "Adding firewalld forward rule: port $VM_PORT -> $VM_IP:$VM_PORT"
# --wait est utile si firewalld est occupé par une autre opération
firewall-cmd --wait --add-forward-port=port=$VM_PORT:proto=tcp:toport=$VM_PORT:toaddr=$VM_IP
if [ $? -ne 0 ]; then
    logger -t "$LOG_TAG" "Error: Failed to add firewalld rule."
    # Optionnel: Tenter un arrêt forcé? Ex: virsh destroy "$VM_NAME"
    exit 1
fi

# Stocker l'IP utilisée pour la suppression par le hook
# Créer le répertoire s'il n'existe pas
mkdir -p /var/run/vm-trigger-rules/
echo "$VM_IP" > "/var/run/vm-trigger-rules/$VM_NAME-$VM_PORT.ip"

logger -t "$LOG_TAG" "Setup complete for VM '$VM_NAME' on port $VM_PORT."
exit 0
