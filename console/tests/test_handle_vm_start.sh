#!/bin/bash
# Tests de handle-vm-start.sh contre un faux `virsh` / `firewall-cmd` / `logger`.
#
# Le cas qui motive ce fichier : quand libvirtd est mort, `virsh domstate` échoue
# et n'écrit rien sur stdout. Le script prenait cette chaîne vide pour un état
# transitoire ("in shutdown", "pm-suspended") et attendait 90 s avant d'échouer.
# Comme chaque sonde Moonlight redéclenche le service, cela bouclait indéfiniment.
# Voir l'incident du 2026-08-24.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/../../console/host/handle-vm-start.sh"

PASS=0
FAIL=0

fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }

# Construit un bac à sable : PATH truqué, verrou isolé de la production.
# $1 = script `virsh` factice (corps bash, reçoit les arguments de virsh)
setup_sandbox() {
    SANDBOX=$(mktemp -d)
    mkdir -p "$SANDBOX/bin"

    cat > "$SANDBOX/bin/virsh" <<EOF
#!/bin/bash
STATE_FILE="$SANDBOX/state"
CALLS="$SANDBOX/calls"
echo "\$*" >> "\$CALLS"
$1
EOF

    # firewall-cmd : aucune règle existante, puis accepte l'ajout.
    cat > "$SANDBOX/bin/firewall-cmd" <<EOF
#!/bin/bash
echo "\$*" >> "$SANDBOX/calls"
case "\$*" in
    *--list-forward-ports*) exit 0 ;;
    *) exit 0 ;;
esac
EOF

    # logger : capture au lieu d'écrire dans le journal système.
    cat > "$SANDBOX/bin/logger" <<EOF
#!/bin/bash
shift 2 2>/dev/null
cat >> "$SANDBOX/log" 2>/dev/null || true
echo "\$*" >> "$SANDBOX/log"
EOF

    chmod +x "$SANDBOX/bin/"*
    export VM_TRIGGER_LOCK="$SANDBOX/lock"
    : > "$SANDBOX/calls"
    : > "$SANDBOX/log"
}

teardown_sandbox() {
    rm -rf "$SANDBOX"
    unset VM_TRIGGER_LOCK
}

# Lance le script sous le PATH truqué et mesure sa durée.
# Renseigne RC et ELAPSED.
run_target() {
    local start end
    start=$(date +%s)
    PATH="$SANDBOX/bin:$PATH" timeout 120 bash "$TARGET" >/dev/null 2>&1
    RC=$?
    end=$(date +%s)
    ELAPSED=$((end - start))
}

echo "== handle-vm-start.sh =="

# --- Test 1 (régression) : libvirtd injoignable ---------------------------
# `virsh` échoue et n'imprime rien sur stdout. Le script doit renoncer
# immédiatement, pas attendre la fenêtre de stabilisation de 90 s.
echo "[1] libvirtd injoignable -> abandon immédiat"
setup_sandbox '
echo "error: failed to connect to the hypervisor" >&2
exit 1
'
run_target
if [ "$RC" -eq 0 ]; then
    fail "devrait sortir en erreur (rc=$RC)"
else
    pass "sort en erreur (rc=$RC)"
fi
if [ "$ELAPSED" -ge 30 ]; then
    fail "a attendu ${ELAPSED}s : la chaîne vide est encore traitée comme un état transitoire"
else
    pass "renonce en ${ELAPSED}s (< 30s)"
fi
if grep -qiE "hypervisor|libvirt" "$SANDBOX/log" 2>/dev/null; then
    pass "journalise la cause réelle (hyperviseur injoignable)"
else
    fail "n'indique pas que l'hyperviseur est injoignable"
fi
if grep -q "start Windows" "$SANDBOX/calls" 2>/dev/null; then
    fail "a tenté un 'virsh start' malgré l'hyperviseur injoignable"
else
    pass "ne tente pas de démarrer la VM"
fi
teardown_sandbox

# --- Test 2 : VM déjà démarrée -------------------------------------------
echo "[2] VM déjà 'running' -> pose la règle de redirection"
setup_sandbox '
case "$1" in
    domstate) echo "running"; exit 0 ;;
    domifaddr) echo " vnet0 52:54:00:aa:bb:cc ipv4 192.168.3.2/24"; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if [ "$RC" -eq 0 ]; then
    pass "sort en succès"
else
    fail "devrait réussir (rc=$RC)"
fi
if grep -q "add-forward-port.*192.168.3.2" "$SANDBOX/calls" 2>/dev/null; then
    pass "ajoute la redirection vers l'IP de la VM"
else
    fail "n'a pas ajouté la redirection"
fi
teardown_sandbox

# --- Test 3 : VM éteinte --------------------------------------------------
echo "[3] VM 'shut off' -> démarre la VM"
setup_sandbox '
case "$1" in
    domstate)
        if [ -f "$STATE_FILE" ]; then echo "running"; else echo "shut off"; fi
        exit 0 ;;
    start) touch "$STATE_FILE"; exit 0 ;;
    domifaddr) echo " vnet0 52:54:00:aa:bb:cc ipv4 192.168.3.2/24"; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if grep -q "^start Windows" "$SANDBOX/calls" 2>/dev/null; then
    pass "appelle 'virsh start Windows'"
else
    fail "n'a pas démarré la VM"
fi
if [ "$RC" -eq 0 ]; then
    pass "sort en succès"
else
    fail "devrait réussir (rc=$RC)"
fi
teardown_sandbox

# --- Test 4 : état transitoire réel --------------------------------------
# Ne pas casser le comportement légitime : "in shutdown" doit toujours être
# attendu, puis la VM redémarrée une fois stabilisée.
echo "[4] 'in shutdown' réel -> attend puis démarre"
setup_sandbox '
case "$1" in
    domstate)
        N=$(cat "$STATE_FILE.n" 2>/dev/null || echo 0)
        echo $((N + 1)) > "$STATE_FILE.n"
        if [ -f "$STATE_FILE" ]; then echo "running"
        elif [ "$N" -lt 2 ]; then echo "in shutdown"
        else echo "shut off"; fi
        exit 0 ;;
    start) touch "$STATE_FILE"; exit 0 ;;
    domifaddr) echo " vnet0 52:54:00:aa:bb:cc ipv4 192.168.3.2/24"; exit 0 ;;
    *) exit 0 ;;
esac
'
run_target
if grep -q "^start Windows" "$SANDBOX/calls" 2>/dev/null; then
    pass "démarre la VM après stabilisation"
else
    fail "n'a pas démarré la VM après stabilisation"
fi
teardown_sandbox

# --- Test 5 : le verrou est paramétrable ---------------------------------
# Sans cela le test écrirait dans le verrou de production.
echo "[5] verrou paramétrable via VM_TRIGGER_LOCK"
if grep -q "VM_TRIGGER_LOCK" "$TARGET"; then
    pass "le script honore VM_TRIGGER_LOCK"
else
    fail "le script écrit toujours en dur dans /run/vm-trigger-start.lock"
fi

echo
echo "Résultat : $PASS réussis, $FAIL échoués"
[ "$FAIL" -eq 0 ]
