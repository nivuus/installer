# Invité Windows automatisé — sous-projet C : génération du domaine libvirt

**Date** : 2026-08-22
**Statut** : design validé, prêt pour le plan d'implémentation
**Spec parente** : [`2026-08-22-windows-guest-iso-design.md`](2026-08-22-windows-guest-iso-design.md)
**Sous-projets liés** : [B — provisionnement](2026-08-22-windows-guest-provisionnement-design.md), [bascule](2026-08-22-windows-guest-bascule-design.md)

## Objectif

Produire le domaine libvirt de l'invité Windows **par génération depuis le
matériel détecté**, et non par recopie d'un fichier. `hardware.py` fournit déjà
les GPU, la topologie CPU et les disques ; C ajoute ce qui manque et rend un
XML complet, prêt à `virsh define`.

C vient **avant** B : sans domaine, B n'a rien à provisionner. Et C se teste
seul, sur un disque jetable, sans toucher au NVMe.

## Le principe : repartir du besoin, pas du XML actuel

Le domaine de production a accumulé des ajouts successifs, dont certains
répondaient à des contraintes aujourd'hui disparues. Le corriger
transporterait ses accidents sans qu'on les voie. On repart donc du besoin, et
chaque élément conservé doit se justifier.

### Ce qu'on abandonne, et pourquoi

| Élément du domaine actuel | Sort | Justification |
| --- | --- | --- |
| `<kvm><hidden state='on'/>` | **retiré** | masquage d'hyperviseur, refusé au cadrage. **Mesuré le 2026-08-22** : le domaine de test n'en avait pas et le pilote NVIDIA 610.88 s'est installé, Apollo a capturé et encodé |
| `<vendor_id state='on' value='123456789123'/>` | **retiré** | contournement du Code 43 de NVIDIA, caduc depuis 2021 — et réfuté par la même mesure |
| `<sysinfo>` LENOVO/Dell + `<smbios mode='sysinfo'/>` | **retiré** | falsification d'identifiants matériels, refusée au cadrage |
| `<rom bar='on' file='/usr/share/qemu/rtx4070.rom'/>` | **retiré** | **mesuré** : aucune surcharge de vBIOS dans le domaine de test, le GPU a fonctionné. L'hôte démarre sur l'iGPU, la surcharge n'a plus d'objet |
| `<watchdog model='i6300esb' action='reset'>` | **retiré** | doublon — q35 expose `itco`, qui est conservé |
| `OVMF_CODE_4M.fd` (sans Secure Boot) | **remplacé** | Windows 11 exige Secure Boot |

⚠️ **Le retrait du masquage a une conséquence d'usage** : un anti-triche qui
refuse les machines virtuelles verra désormais l'hyperviseur. C'est la
conséquence assumée du refus acté au cadrage ; si un jeu se ferme au
lancement, c'est le premier endroit où regarder.

### Ce qu'on conserve

Les enlightenments Hyper-V (`relaxed`, `vapic`, `spinlocks`, `vpindex`,
`runtime`, `synic`, `stimer/direct`, `reset`, `frequencies`, `tlbflush`,
`ipi`, `evmcs`) — ce sont des optimisations, pas du masquage. `<clock
offset='localtime'>` et ses timers. `<cpu mode='host-passthrough'>` avec
`<cache mode='passthrough'/>`, `maxphysaddr` à 39 bits, et les features
`topoext`/`hypervisor`/`invtsc` requises, `split-lock-detect` désactivée.
`<memballoon model='none'/>`, `<audio type='none'/>`.

### Ce qu'on ajoute

**Secure Boot** : `<loader readonly='yes' secure='yes' type='pflash'>
/usr/share/OVMF/OVMF_CODE_4M.secboot.fd</loader>` avec varstore issu de
`OVMF_VARS_4M.ms.fd` (clés Microsoft pré-enrôlées), et `<smm state='on'/>`.

🔴 **Jamais la sélection automatique de firmware.** `<os firmware='efi'>` a
déjà cassé l'hibernation S4 sur cette machine : les descripteurs OVMF de
Debian ne déclarent que `acpi-s3`, et libvirt refuse alors S4 alors qu'OVMF
le gère. `<loader>` et `<nvram>` sont **explicites**, toujours.

**vTPM 2.0** : `<tpm model='tpm-crb'><backend type='emulator' version='2.0'/>`,
exigé par Windows 11 et absent du domaine actuel.

**Un périphérique vidéo émulé** — c'est nouveau, et délibéré. Le domaine de
production n'en a aucun : son seul affichage était le dummy plug HDMI, que le
propriétaire retire. Un invité sans aucun écran au démarrage à froid
emprunterait un chemin jamais éprouvé ici. Une VGA émulée supprime ce risque
pour un coût nul, et **la cohabitation est mesurée** : le 2026-08-22 les deux
écrans coexistaient et Apollo, en `ensure_only_display`, a bien désactivé la
VGA pendant le flux (`paths=1`). Bénéfice second, non théorique : `virsh
screenshot` redevient possible — sans lui, l'OOBE bloquée du même jour aurait
été invisible. Console VNC en écoute sur **`127.0.0.1` uniquement**.

## Deux invariants qui font survivre l'existant

### Le domaine garde le nom `Windows`

Les crochets libvirt sont indexés par nom. `/etc/libvirt/hooks/qemu.d/Windows/`
en contient **onze**, en quatre familles : bind/rebind du GPU, confinement et
libération CPU, règles firewalld de streaming, gestion des hugepages — plus
**le montage CIFS `//192.168.3.2/c` sur `/media/vm`**, qui appartient au
circuit de développement Guacamole. Garder le nom fait survivre les trois
premières familles **sans y toucher**.

⚠️ **Le montage CIFS, lui, ne survit pas au modèle appliance, et il est
dangereux.** `started/begin/mount-vm.sh` boucle sans limite sur
`nc -z 192.168.3.2 139` avant de monter. Une appliance n'a aucune raison
d'exposer SMB — surtout un invité joignable depuis le WAN — donc le port ne
répondrait jamais et le crochet ne rendrait jamais la main. C'est la classe
d'interblocage de libvirtd déjà payée sur cette machine. Son retrait est une
étape de la bascule.

Prix : l'ancien domaine doit être supprimé avant que le nouveau prenne le nom
(étape de la bascule, pas de C).

### La MAC est réutilisée : `52:54:00:48:e0:3e`

`/etc/NetworkManager/dnsmasq-shared.d/domain.conf` contient
`dhcp-host=52:54:00:48:E0:3E,192.168.3.2`. Réutiliser la MAC préserve d'un
seul geste l'adresse `192.168.3.2`, et donc **la route Pomerium
`game.allanic.me`, les redirections firewalld de streaming, et le réveil à la
demande**. En changer casserait les trois en silence, chacun de façon
différée.

## Réseau et partage

**virtio-net**, pas `e1000e`. La raison n'est pas le débit brut mais le **coût
CPU par paquet** : cette machine est plafonnée à 60 W de RAPL et bornée par son
refroidissement, et les cycles hôte sont la ressource rare. Le pilote NetKVM
s'installe hors-ligne depuis la charge utile de B.

**virtiofs** `/media/data` → étiquette `Data`, conservé, mais son installation
côté invité est **non bloquante** (voir B) : il demande `viofs` *et* WinFsp
pour un confort. `<memoryBacking>` doit porter `<access mode='shared'/>`, que
virtiofs exige, en plus de `<hugepages/>` et `<locked/>`.

## Ce que C doit détecter, et ce qui manque à `hardware.py`

| Donnée | Source | État |
| --- | --- | --- |
| Slot du GPU discret et ses identifiants | `list_gpus()` | existe |
| **Adresses PCI de toutes les fonctions du slot** (GPU + audio HDMI) | — | **à ajouter** : `list_gpus()` rend les `ids`, pas les adresses bus/slot/fonction dont `<hostdev>` a besoin |
| **NVMe à passer à l'invité** | — | **à ajouter** : le disque est lié à `vfio-pci`, donc invisible de `list_disks()`. Détection par classe PCI `0108`, en excluant celui qui porte la racine de l'hôte |
| Cœurs performants → `vcpupin` | `cpu_topology().performance_cpus` | existe |
| Cœurs restants → `emulatorpin`, `iothreadpin` | dérivé | trivial |
| Dimensionnement hugepages | mémoire de l'invité | à calculer |

🔴 **`cputune` doit rester analysable par `vm-cpu-partition.sh`.** Ce script
dérive le cpuset de l'hôte depuis `vcpupin` + `emulatorpin` du XML que libvirt
lui passe **sur stdin**. Un XML généré qui changerait la forme de ces éléments
casserait le partitionnement CPU sans aucun message d'erreur — le crochet sort
en `exit 0` par conception, et l'échec ne paraît que dans
`/var/log/libvirt-cpu-hook.log`.

## Structure de fichiers

| Fichier | Rôle |
| --- | --- |
| `installer/windows-guest/domain.py` | détection complémentaire + rendu ; CLI `xml` / `define` |
| `installer/windows-guest/templates/domain.xml.j2` | le gabarit |
| `installer/common/hardware.py` | étendu : fonctions PCI d'un slot, NVMe passthrough |
| `installer/windows-guest/tests/test_domain.py` | tests du rendu et de la détection |

Le domaine de test de A (`testdomain.py`, `domain-test.xml.j2`) **reste** : il
sert de banc jetable et n'est pas remplacé par C.

## Test d'acceptation

Le critère n'est pas « le domaine démarre ». C'est **l'hibernation S4 sous
Secure Boot**, et il faut être exact sur l'état des lieux :

⚠️ **La spec de A annonçait « Secure Boot + S4 + passthrough » comme levé par
son test d'acceptation. Il ne l'est pas.** Le domaine de test n'avait aucun
bloc `<pm>` ; S4 n'a jamais été exercé avec Secure Boot et vTPM. Or toute la
stratégie d'énergie de cette machine en dépend : `vm-idle-shutdown.timer`
hiberne au bout de dix minutes d'inactivité, et le réveil par socket reprend.

Sur **disque jetable**, avec le GPU réel :

1. `domain.py define` puis `virsh start` — Windows voit son disque, son GPU et son réseau ;
2. `shutdown /h /f` dans l'invité → le domaine passe à l'arrêt ;
3. `virsh start` → **session intacte, GPU réinitialisé** ;
4. `vm-cpu-partition.sh status` montre un partage cohérent pendant l'exécution.

## Hors périmètre

Le provisionnement de l'invité (B), la bascule, l'écran du portail et
l'intégration au moteur (D). C se pilote par un script en ligne de commande.
