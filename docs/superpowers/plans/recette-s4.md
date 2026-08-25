# Recette S4 — hibernation sous Secure Boot (sous-projet C)

> ✅ **Exécutée le 2026-08-25, tous les critères passés.** Voir « Résultat de
> la première exécution » en fin de document pour les mesures et les deux
> dérives constatées entre ce mode opératoire et l'arbre.

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
# 0a. Neutraliser les automatismes systemd pour toute la durée de la recette.
# vm-idle-shutdown.timer tourne toutes les dix minutes et, tant que Windows
# est éteint (ce que l'étape 0 garantit pour toute la recette), son script
# RELANCE nivuus-ollama (le conteneur GPU qu'on vient d'arrêter à l'étape 1)
# et RÉARME les sockets de réveil vm-trigger-47984/47989 : une simple sonde
# Moonlight sur 47989 démarrerait alors le domaine Windows de PRODUCTION en
# pleine recette, entrant en concurrence avec le domaine de test pour le GPU.
#
# systemctl NE FONCTIONNE PAS depuis une session automatisée sur cet hôte
# (voir CLAUDE.md, "Host Shell Gotchas" — la session tourne dans son propre
# PID namespace, systemd authentifie par SO_PEERCRED). Piloter systemd via
# le bus D-Bus système à la place. Un humain sur une vraie console peut
# utiliser `systemctl disable --now vm-idle-shutdown.timer vm-trigger-47984.socket
# vm-trigger-47989.socket` directement.
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StopUnit string:"vm-idle-shutdown.timer" string:"replace"
dbus-send $M.StopUnit string:"vm-trigger-47984.socket" string:"replace"
dbus-send $M.StopUnit string:"vm-trigger-47989.socket" string:"replace"
# MaskUnitFiles ECHOUE ici : les trois unites sont des fichiers reguliers dans
# /etc/systemd/system, pas des liens, et systemd renvoie -EEXIST avant meme de
# consulter le drapeau « force » — l'etape errerait, ce qui aborte un script en
# set -e APRES l'extinction de la VM de production. DisableUnitFiles retire les
# liens .wants, ce qui suffit : seul vm-idle-shutdown.sh rearme les sockets, et
# son timer vient d'etre arrete.
dbus-send $M.DisableUnitFiles array:string:"vm-idle-shutdown.timer","vm-trigger-47984.socket","vm-trigger-47989.socket" boolean:false
dbus-send $M.Reload
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
sed -i "s#</domain>#  <pm><suspend-to-mem enabled='no'/><suspend-to-disk enabled='yes'/></pm>\n</domain>#" /tmp/s4-test.xml
# Equivalent interactively: virsh edit Windows-LTSC-test, then add the same
# <pm> block by hand just before </domain>.
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
# 12 x 5s = 60s, pour rester cohérent avec le critère "moins de 60 s" du
# tableau ci-dessous.
for i in $(seq 1 12); do
  [ "$(LC_ALL=C virsh domstate Windows-LTSC-test)" = "shut off" ] && break
  sleep 5
done
LC_ALL=C virsh domstate Windows-LTSC-test

# 4. Reprendre
virsh start Windows-LTSC-test

# 4b. ATTENDRE que l'invite ait fini de reprendre. Sans cette attente, les
#     appels WinRM de l'etape 5 visent un invite encore en sortie de S4 et
#     leurs echecs se lisent comme des echecs de critere.
(cd installer/windows-guest && python3 testdomain.py wait-ready) >/dev/null

# 5. Mesurer chaque critère du tableau ci-dessous, dans l'ordre. Ce sont ces
#    invocations qui décident le verdict — sans elles, il n'y a que le
#    domstate de l'étape 3, qui ne couvre aucun des critères Secure
#    Boot/vTPM/GPU/session.
python3 installer/windows-guest/winrm_exec.py ps "Get-Process notepad -ErrorAction SilentlyContinue"
python3 installer/windows-guest/winrm_exec.py ps "Get-Content C:\\temoin-s4.txt"
python3 installer/windows-guest/winrm_exec.py cmd "nvidia-smi"
python3 installer/windows-guest/winrm_exec.py ps "Confirm-SecureBootUEFI"
python3 installer/windows-guest/winrm_exec.py ps "Get-Tpm"
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

## Ce que cette recette prouve, et ce qu'elle ne prouve pas

Elle patche un bloc `<pm>` sur le domaine jetable de A (`testdomain.py`),
elle n'exerce pas la sortie de `domain.py` (sous-projet C). C'est une bonne
mesure de la **physique** de S4 sous Secure Boot — la question que la spec
de A avait laissée ouverte — mais pas un test de bout en bout du domaine de
production.

**Couvert** : l'hibernation S4 fonctionne (ou pas) avec Secure Boot actif et
un vTPM émulé présents en même temps, sur le GPU réel passé en hostdev —
c'est-à-dire exactement la combinaison que la spec de A prétendait avoir
vérifiée et ne l'avait pas.

**Pas couvert, dette qui reste due** :
- **Le NVMe passé en hostdev.** Le domaine jetable de A démarre depuis un
  qcow2 sur `/media/data`, jamais depuis le Samsung NVMe passé par
  `hardware.passthrough_nvme()` — ce disque reste délibérément avec la
  production. S4 depuis un disque `<hostdev>` réel n'est donc jamais exercé
  ici.
- **`cputune` / `vm-cpu-partition.sh status`.** Le domaine de test n'a ni
  `vcpupin` ni `emulatorpin` (A ne les génère pas) : l'étape `status` du
  script de partitionnement CPU que la spec mentionne est inapplicable ici,
  faute de `cputune` à lire.
- **virtio-net.** Le domaine de test utilise `e1000e` (le média LTSC n'a pas
  le pilote virtio en boîte) ; la production utilise `virtio-net`. Le
  chemin réseau n'est donc pas identique.
- **Le rendu réel de `domain.py`.** Cette recette ne définit jamais le XML
  que `domain.py xml` produirait ; elle ne peut donc pas détecter une
  régression dans son gabarit.

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

# Réarmer les automatismes désactivés en préparation (étape 0a). Sans ça,
# le réveil à la demande et l'hibernation auto restent cassés en permanence,
# pas seulement pendant la recette. Un humain sur une vraie console peut
# utiliser `systemctl enable --now vm-idle-shutdown.timer
# vm-trigger-47984.socket vm-trigger-47989.socket` à la place.
dbus-send $M.EnableUnitFiles array:string:"vm-idle-shutdown.timer","vm-trigger-47984.socket","vm-trigger-47989.socket" boolean:false boolean:true
dbus-send $M.Reload
dbus-send $M.StartUnit string:"vm-idle-shutdown.timer" string:"replace"
dbus-send $M.StartUnit string:"vm-trigger-47984.socket" string:"replace"
dbus-send $M.StartUnit string:"vm-trigger-47989.socket" string:"replace"
```

⚠️ La régénération CDI n'est pas facultative : `nvidia_uvm` reçoit un majeur
**dynamique**, et une spécification figée fait renvoyer 999 à tout CUDA
pendant que `nvidia-smi` continue de fonctionner.

---

## Résultat de la première exécution (2026-08-25)

| Ce qu'on vérifie | Attendu | Mesuré |
| --- | --- | --- |
| Le domaine passe à l'arrêt après `shutdown /h /f` | `shut off` en moins de 60 s | **~30 s** (14:17:42 → 14:18:12) |
| La reprise restitue la session | processus toujours vivants | **oui** — voir ci-dessous |
| Le GPU est réinitialisé | `nvidia-smi` répond dans l'invité | **RTX 4070, pilote 610.88, 35 °C, WDDM** |
| Secure Boot actif | `Confirm-SecureBootUEFI` → `True` | **True** |
| vTPM présent | `TpmPresent`/`TpmReady` → `True` | **True / True / True**, `ManufacturerIdTxt: IBM` (swtpm) |

**La preuve que c'est bien une reprise S4 et pas un démarrage à froid** — c'est
le point à mesurer, parce que les deux se ressemblent depuis l'hôte :

- `Kernel-Boot` **événement 27, `boot type 0x2`** à 14:19:16 (`0x0` = démarrage
  à froid, `0x1` = démarrage hybride, `0x2` = reprise d'hibernation), suivi de
  `Power-Troubleshooter` « The system has returned from a low power state ».
- `LastBootUpTime` reste **14:12:32**, soit l'amorçage d'AVANT l'hibernation,
  avec un uptime continu de 7 min 34 qui enjambe celle-ci. Un démarrage à
  froid aurait remis ce compteur à 14:18.
- Les processus antérieurs à l'hibernation sont toujours vivants avec leurs PID
  d'origine, en session 0 comme en session 1 (`winlogon` PID 928 session 1,
  démarré à 14:13:00).

⚠️ **Le témoin `notepad` prescrit plus haut ne fonctionne pas et ne doit pas
être utilisé.** Un WinRM place chaque shell dans un *Job Object* : tout
processus lancé par `Start-Process` depuis ce shell est tué quand le shell se
ferme, bien avant l'hibernation. Le témoin a donc disparu pour une raison qui
n'a rien à voir avec S4, et un opérateur qui s'y fierait conclurait à tort à
un échec. Le contrôle qui vaut est celui ci-dessus : `boot type 0x2` +
`LastBootUpTime` inchangé + PID antérieurs vivants. De plus, l'invité de A
n'ouvre pas de session interactive automatiquement (c'est `50-power.ps1`, une
étape de B), donc une tâche planifiée `/IT` ne démarre rien non plus.

### Deux dérives entre ce document et l'arbre

1. **Le bloc `<pm>` est désormais émis par `templates/domain-test.xml.j2`** (il
   y a été ajouté pendant la vague de correctifs finale de B). La section
   « Ajouter le bloc `<pm>` au domaine jetable » est donc sans objet pour un
   domaine défini par le `testdomain.py` courant — vérifier avec
   `virsh dumpxml --inactive Windows-LTSC-test | grep -A3 '<pm>'` avant de
   patcher quoi que ce soit.

2. **La carte réseau du gabarit est passée à `virtio`, ce qui rend cette
   recette-ci inexécutable telle quelle.** Le changement sert B, dont l'étape
   `15-virtio.ps1` installe NetKVM. Mais cette recette utilise l'ISO de
   réponses du sous-projet A, qui ne porte que quatre étages
   (`00-bootstrap`, `10-nvidia`, `20-sudovda`, `99-marker`) et **aucun pilote
   virtio** ; le média LTSC n'en a pas non plus en boîte. L'invité démarre,
   provisionne et termine normalement — mais sans adresse IP, donc 5985 ne
   s'ouvre jamais et `wait-ready` expire au bout de 90 min sans rien dire
   d'utile. Symptôme à reconnaître : `virsh domstate` dit `running`, l'écran
   est noir (le pilote NVIDIA a pris l'affichage), et
   `/var/lib/NetworkManager/dnsmasq-internalBridge.leases` est vide.
   Contournement employé ici : `virsh attach-interface Windows-LTSC-test bridge
   internalBridge --model e1000e --live` pour confirmer que l'invité est sain,
   puis une redéfinition avec `<model type='e1000e'/>` (adresse PCI retirée
   pour que libvirt en réassigne une). **Correctif de fond dû** :
   `testdomain.py` doit exposer le modèle de carte en paramètre
   (`--nic-model`, défaut `virtio` pour B, `e1000e` pour un média A).
