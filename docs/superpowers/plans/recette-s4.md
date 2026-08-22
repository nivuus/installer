# Recette S4 — hibernation sous Secure Boot (sous-projet C)

**Ce qu'on cherche à savoir** : un invité Windows 11 démarré en Secure Boot
avec vTPM et GPU passé sait-il hiberner et reprendre, session intacte ?

**Pourquoi c'est bloquant** : `vm-idle-shutdown.timer` hiberne la VM après dix
minutes d'inactivité et le réveil par socket la reprend. Sans S4, la VM reste
allumée en permanence et tout le travail d'économie d'énergie tombe.

⚠️ **Sur disque jetable uniquement.** Le NVMe de production n'est pas touché.

## Préparation

```bash
# 0. Arrêter le domaine de production (le temps de la recette, la VM gaming est hors ligne — environ quatre-vingt-dix minutes)
virsh shutdown --mode acpi Windows
for i in $(seq 1 60); do
  [ "$(LC_ALL=C virsh domstate Windows)" = "shut off" ] && break
  sleep 1
done
```

```bash
# 1. Libérer le GPU (aucun crochet ne le fait pour un domaine jetable)
docker stop mediamanager-tdarr-node-nvenc-1 mediamanager-tdarr-node-1 \
            mediamanager-tdarr-1 nivuus-ollama
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StopUnit string:"nvidia-persistenced.service" string:"replace"

# 2. Installer un invité jetable (sous-projet A)
(cd installer/windows-guest && sudo python3 testdomain.py define \
  --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
  --unattend-iso /media/data/iso/nivuus-unattend.iso)
virsh start Windows-LTSC-test
```

⚠️ Le média Windows attend une frappe (« Press any key to boot from CD »).
Sans elle : `No bootable option or device was found`.

```bash
for i in $(seq 1 40); do
  virsh send-key Windows-LTSC-test --codeset linux KEY_ENTER >/dev/null 2>&1
  sleep 1
done
```

Attendre que le provisionnement finisse avant de toucher au domaine :

```bash
# Wait for provisioning to finish before touching the domain.
(cd installer/windows-guest && python3 testdomain.py wait-ready) >/dev/null
```

Le port WinRM 5985 s'ouvre à la fin et c'est le signal conçu (voir
`testdomain.py wait_ready()`) : `wait-ready` bloque jusqu'à ce qu'il s'ouvre.
Il existe une étroite course dans `00-bootstrap.ps1` : entre l'activation de la
communication à distance et la désactivation de la règle pare-feu, le port peut
être brièvement accessible avant que le provisionnement soit vraiment fini.
`wait-ready` sera rappelé après le redémarrage du domaine pour dériver
l'adresse IP à jour (un bail DHCP peut changer).

(Note : la course elle-même est un défaut du script de bootstrap du sous-projet A,
fermée en désactivant la règle *avant* d'activer PSRemoting. C'est un sujet de
suivi en dehors de ce périmètre.)

## Ajouter le bloc `<pm>` au domaine jetable

Le domaine de test de A n'en a pas. Le lui ajouter, sinon la recette ne mesure
rien :

```bash
virsh dumpxml Windows-LTSC-test > /tmp/s4-test.xml
# insérer avant </domain> :
#   <pm><suspend-to-mem enabled='no'/><suspend-to-disk enabled='yes'/></pm>
virsh destroy Windows-LTSC-test
virsh define /tmp/s4-test.xml
virsh start Windows-LTSC-test

# Redériver l'adresse IP après le redémarrage (DHCP peut avoir changé)
export GUEST_IP=$(cd installer/windows-guest && python3 testdomain.py wait-ready)
```

## La mesure

```bash
# Refusal guard: prevent targeting the production VM by mistake
: "${GUEST_IP:?GUEST_IP n'est pas défini — relancer l'étape wait-ready}"
if [ "$GUEST_IP" = "192.168.3.2" ]; then
    echo "REFUS : GUEST_IP pointe la VM de production, pas le domaine jetable" >&2
    exit 1
fi

# 1. Activer l'hibernation dans l'invité et poser un témoin de session
python3 installer/windows-guest/winrm_exec.py ps "powercfg /hibernate on"
python3 installer/windows-guest/winrm_exec.py ps "Set-Content C:\\temoin-s4.txt (Get-Date -Format o)"
python3 installer/windows-guest/winrm_exec.py ps "Start-Process notepad"    # une fenêtre ouverte = témoin visible

# 2. Hiberner
python3 installer/windows-guest/winrm_exec.py cmd "shutdown /h /f"

# 3. Constater l'arrêt (l'appel WinRM expire pendant l'endormissement :
#    son code de retour ne veut rien dire, c'est domstate qui compte)
for i in $(seq 1 30); do
  [ "$(LC_ALL=C virsh domstate Windows-LTSC-test)" = "shut off" ] && break
  sleep 5
done
LC_ALL=C virsh domstate Windows-LTSC-test

# 4. Reprendre
virsh start Windows-LTSC-test
```

## Critères

| Ce qu'on vérifie | Attendu |
| --- | --- |
| Le domaine passe à l'arrêt après `shutdown /h /f` | `shut off` en moins de 60 s |
| La reprise restitue la session | notepad toujours ouvert |
| Le GPU est réinitialisé | `nvidia-smi` répond dans l'invité |
| Secure Boot actif | `Confirm-SecureBootUEFI` → `True` |
| vTPM présent | `Get-Tpm` → `TpmPresent: True`, `TpmReady: True` |

🔴 **Si le domaine ne passe pas à l'arrêt** : la sélection automatique de
firmware est le premier suspect — vérifier que `<loader>` et `<nvram>` sont
explicites. Les descripteurs OVMF de Debian ne déclarent que `acpi-s3`, et
libvirt refuse alors S4 alors qu'OVMF le gère.

## Démontage

```bash
virsh destroy Windows-LTSC-test
cd installer/windows-guest && sudo python3 testdomain.py teardown
# Rendre le GPU à l'hôte
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StartUnit string:"nvidia-persistenced.service" string:"replace"
nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
docker start nivuus-ollama mediamanager-tdarr-1 \
             mediamanager-tdarr-node-1 mediamanager-tdarr-node-nvenc-1
```

⚠️ La régénération CDI n'est pas facultative : `nvidia_uvm` reçoit un majeur
**dynamique**, et une spécification figée fait renvoyer 999 à tout CUDA
pendant que `nvidia-smi` continue de fonctionner.
