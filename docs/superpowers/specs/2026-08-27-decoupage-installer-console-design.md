# Découpage de `nivuus/installer` en deux packages : l'installateur Debian et la console

**Date** : 2026-08-27
**Statut** : design validé, prêt pour le plan d'implémentation

## Objectif

Séparer `nivuus/installer` en **deux dépôts git**, selon la convention de
l'organisation (`~/Projects/Nivuus/packages/<nom>`) :

* **`nivuus/installer`** — installe et configure Debian, **et expose une API de
  packages**. Il ne connaît plus « la VM » : il connaît des packages.
* **`nivuus/console`** — pose libvirt, génère le domaine depuis le matériel
  détecté, construit l'invité Windows 11 IoT et ses options. **Il est le premier
  package**, et passe par la même API que n'importe quel tiers.

Le nom `console` reprend le vocabulaire déjà tranché dans ce dépôt (voir le
commentaire de `windows-guest/domain.py` et les partages `Console` /
`ConsoleSave`) : il décrit ce que la machine **est pour celui qui l'utilise**,
reste juste si l'invité cesse un jour d'être Windows, et ne se confond pas avec
l'hôte, qui s'appelle Nivuus.

## Décisions de cadrage

| Question | Décision |
| --- | --- |
| Nature du découpage | Deux dépôts git séparés |
| Moment de l'installation | Une passe : l'ISO embarque les deux |
| Niveau du point d'extension tiers | Sur l'hôte Debian uniquement |
| Statut du dépôt 2 | Package **ordinaire** — même API que les tiers |
| Forme du contrat | Manifeste déclaratif + hooks |
| Disque dédié à la VM | Passthrough PCI uniquement, refus motivé sinon |
| Contrainte ajoutée | `console` doit s'installer **seul** sur un Debian existant (GPU dédié + NVMe dédié) |

## État des lieux : où passe la couture aujourd'hui

Elle n'est pas propre. Six points de couplage, mesurés dans le code :

| Point | Détail |
| --- | --- |
| `install-engine/steps/features.py` → `install.sh` | La feature `kvm-vfio` appelle `install.sh` dans le chroot : c'est l'installateur Debian qui pose libvirt, VFIO, IOMMU, hugepages et les hooks CPU |
| `common/hardware.py` (637 l.) | Disques/NIC/WiFi (l. 45–223, côté Debian) **et** GPU/fonctions PCI/NVMe passthrough/topologie CPU (l. 224–622, côté console) |
| `common/retro.py` | Marqueur partagé : `features.py` l'écrit pendant l'install, `windows-guest/build.py` le lit plus tard |
| `iso-build/build.sh` | `git archive HEAD` du dépôt **entier** → `windows-guest/` voyage dans l'image |
| `scripts/tests/` | 20 fichiers, une seule suite ; 13 sont `test_windows_guest_*` |
| `webapp/models.py` | `KNOWN_FEATURES` mêle `os-base`/`networking`/`firewall` et `kvm-vfio`/`gpu-passthrough`/`retro` |

### `install.sh` ne survit pas au découpage

Sur ses sept blocs, **cinq sont de la mise en place de VM** : paquets
qemu/libvirt, hooks de partitionnement CPU, hugepages, `nohz_full` + IOMMU +
`vfio-pci.ids` dans GRUB, modules VFIO. Il ne reste côté hôte que la thermique —
le script et son unité systemd, une quinzaine de lignes, qui deviennent une
feature ordinaire d'`install-engine`.

Conséquence heureuse : toute la machinerie `NIVUUS_DIR` / `NIVUUS_IN_CHROOT` /
`NIVUUS_ISOLCPUS` / `NIVUUS_VFIO_IDS` n'existe que pour rendre ce script
exécutable dans un chroot. Elle disparaît avec lui.

### Deux fichiers morts, trouvés au passage

* `configs/vm-template.xml` — plus rien ne le lit. `domain.py` génère le XML
  depuis `templates/domain.xml.j2`. Il n'est cité que par un `echo` d'`install.sh`
  et de la doc périmée (`QUICKSTART.md`, `docs/vm-configuration.md:462`).
* `scripts/vm-cpu-partition.sh.deployed-backup` — détritus.

À supprimer pendant le découpage, pas à déménager.

## Répartition

### `nivuus/installer`

Garde le nom, le badge, l'ISO et les releases.

| | Contenu |
| --- | --- |
| `installer/` | `ap/`, `webapp/`, `install-engine/`, `iso-build/`, `common/progress.py`, `common/hardware.py` réduit aux disques/NIC/WiFi (l. 45–223) **plus la détection de capacités** (voir couture 2) |
| `scripts/` | `disk-maintenance`, `hw-blackbox`, `net-rps-ecores`, `pcie-wifi-link-guard`, `ups-shutdown`, `validate-install`, `optimize-cpu-thermal`, `nivuus-cpu-latency`, `thermal-campaign/`, `ha/` |
| `configs/` | `cpu/`, `firewall/`, `network/`, `homeassistant/`, `systemd/` (thermique, cpu-latency, cpu-mode@, disk-maintenance, hw-blackbox, pcie-link-*) |
| `docs/` | `system-audit.md`, `thermal-optimization.md`, `homeassistant-cli.md` |
| **Nouveau** | Le moteur de packages : schéma du manifeste, chargeur, validation, résolution de conflits (`claims:`), collecte des `kernel-cmdline`, extension du wizard, unité `nivuus-package-activate@.service` |
| Disparaît | `install.sh` |

### `nivuus/console`

| | Contenu |
| --- | --- |
| `guest/` | Tout `windows-guest/` : `build`, `domain`, `payload`, `fetch_payload`, `provision/`, `templates/`, `retro_sync`, `apollo`, `media`, `autounattend`, `unattend_iso`, `winrm_exec`, `testdomain`, `probe/` |
| `host/` | `vm-cpu-partition.sh`, `vm-wake-gate.py`, `handle-vm-start.sh`, `winvm`, `install-winrm-cli.sh`, `gpu-rebind-debug/`, les 9 hooks `configs/libvirt/hooks/**`, `configs/setup-winrm.ps1` |
| `hardware.py` | Les ~400 lignes GPU/PCI/NVMe/topologie extraites de `common/` |
| `retro.py` | Cesse d'être partagé — devient interne, le pont disparaît |
| `docs/` | `vm-configuration.md`, `winrm-setup.md`, les 4 specs `2026-08-22-windows-guest-*` |
| **Nouveau** | `nivuus-package.yaml`, `hooks/{resolve,install,activate}.py`, le runner autonome, sa propre CI, son propre `CLAUDE.md` |

## Le contrat de package

### Trois phases, nommées par rapport au reboot — pas au chroot

C'est ce qui rend les deux chemins d'entrée symétriques.

| Phase | Quand | Reçoit | Peut |
| --- | --- | --- | --- |
| `resolve` | Avant toute écriture | `hw.json` + réponses du wizard | **Lecture seule.** Retourne le bloc `platform` résolu **et ses faits mesurés**, ou un refus motivé |
| `install` | Sur un système de fichiers cible | `--root` (`/mnt/target` en ISO, `/` en autonome) | Écrire dans ce root |
| `activate` | Après le reboot, réseau disponible | `hw` détecté à frais **+ les faits de son `resolve`** | Tout : téléchargement de Windows, `virsh define`, provisionnement |

En mode ISO, `install` tourne dans le chroot ; en autonome, à `/`. **Même code,
root différent.** `activate` passe dans les deux cas par
`nivuus-package-activate@.service`, une unité oneshot fournie par
`nivuus/installer` et que le runner autonome de `console` sait poser lui-même —
le reboot est de toute façon obligatoire pour l'IOMMU.

`resolve` est la pièce qui tient le design : parce qu'il est en lecture seule et
qu'il tourne **avant tout**, le moteur connaît la ligne de commande noyau exacte
avant de partitionner. Le bootloader reste donc à sa place actuelle dans
`run.py` (`partition → debootstrap → base → bootloader → features`), sans
réordonnancement. C'est aussi là que vit le refus motivé du « passthrough PCI
uniquement » : personne ne perd un disque avant d'apprendre que son matériel ne
convient pas.

### Les faits : ce que `resolve` mesure et que le redémarrage efface

Un package `platform` mesure régulièrement une chose que l'installation
elle-même détruit : un disque sur le point d'être lié à `vfio-pci`, un
périphérique que la nouvelle ligne de commande noyau capture, un état
transitoire. `resolve` est la seule phase qui les voit encore ; `activate`
reconstruit son `hw` par une détection fraîche d'après le redémarrage, où ils
ont disparu. D'où un quatrième événement, **`{"event":"facts","facts":{…}}`** :
`resolve` **retourne** ses faits, **le moteur les persiste** dans
`etc/nivuus/packages.json` (0600, à côté des réponses du wizard — les faits ne
sont pas des secrets mais partagent leur fichier, et le mode ne s'élargit pas),
et `activate_cli.py` les fusionne dans le `hw` du hook. `resolve` n'écrit
toujours rien : c'est cette propriété qui garde `bootloader` à sa place dans
`run.py`.

Les faits **ne sont pas soumis au tier**. `kernel-cmdline`, `modules` et
`hugepages-mib` sont refusés à un package `userspace` parce qu'ils atteignent
la chaîne d'amorçage et que le wizard doit les montrer pour une confirmation
distincte ; un fait n'atteint rien d'autre que la phase `activate` du package
qui l'a produit.

**Précédence, tranchée : la détection fraîche gagne.** Un fait décrit le monde
d'*avant* le redémarrage ; il ne comble que les clefs que la détection ne
produit pas. Là où la mesure reste possible, « maintenant » l'emporte sur
« alors » — un fait périmé ne peut donc jamais masquer une mesure vivante. Un
fait ne vaut que là où la détection s'est tue, ce qui est exactement le cas
pour lequel il existe. Un package voulant la valeur d'avant d'une chose encore
observable la nomme distinctement (`console` nomme la sienne
`dedicated_nvme_size_bytes`, pas `size_bytes`). Le contrat normatif est
`installer/packages/facts.py`.

### Le manifeste de `console`

```yaml
apiVersion: nivuus.dev/v1
name: console
version: 1.0.0
label: "Console de jeu Windows"
tier: platform                    # voir « Les deux tiers » ci-dessous

requires:
  capabilities: [iommu, gpu-discrete, nvme-dedicated]
  features: [networking]

claims:
  gpu:  exclusive                 # conflit avec tout package réclamant le GPU
  nvme: exclusive

platform:                         # part STATIQUE ; resolve complète le reste
  modules: [vfio, vfio_pci, vfio_iommu_type1]
  kernel-cmdline: ["intel_iommu=on", "iommu=pt"]

apt: [qemu-kvm, libvirt-daemon-system, libvirt-clients, ovmf, virtiofsd]

wizard:
  questions: wizard.yaml          # vocabulaire restreint

hooks:
  resolve:  hooks/resolve.py
  install:  hooks/install.py
  activate: hooks/activate.py
```

`resolve` ajoute précisément ce que le statique ne peut pas savoir :
`vfio-pci.ids=<détecté>`, `nohz_full=<calculé>`, et le nombre de hugepages
dérivé de la RAM disponible.

### Les deux tiers

`tier: userspace` — le package ne pose que des paquets apt, des services, des
conteneurs et de la configuration. Il ne peut pas contribuer de
`kernel-cmdline`, de `modules` ni de `hugepages-mib` : le moteur rejette un
manifeste qui les déclare à ce tier.

`tier: platform` — le package touche à l'amorçage. Le moteur affiche alors dans
le wizard, **avant l'installation**, la ligne de commande noyau exacte que
`resolve` a retournée, et exige une confirmation distincte. C'est ce qui rend
acceptable de donner la ligne de commande noyau à un package tiers : elle est
montrée, en toutes lettres, à celui qui accepte.

### Wizard : vocabulaire restreint, pas de schéma libre

Types de questions autorisés : `bool`, `choix`, `texte`, `secret`, `disque`,
`gpu`. Un package tiers ne doit pas pouvoir dessiner des formulaires arbitraires
dans le portail, et un sélecteur de disque a besoin de la détection matérielle du
moteur pour être utilisable. `console` n'a que trois questions : le disque dédié,
les packages invités optionnels, le mot de passe administrateur.

### Protocole de progression : le contrat, pas la bibliothèque

Un hook émet du jsonl sur stdout selon une forme documentée. Le moteur de
`nivuus/installer` le relaie au portail par WebSocket ; le runner autonome de
`console` l'imprime. Chacun a son lecteur, une trentaine de lignes. **`console`
ne dépend jamais de `common/progress.py`.**

### Propriété du contrat

`nivuus/installer` possède le contrat, versionné par `apiVersion`. Pas de
troisième dépôt : un dépôt de schéma pour deux consommateurs serait de la
cérémonie.

**Le contrat normatif est du code, pas un document** — `installer/packages/manifest.py`
et `installer/packages/wizard.py`. Un fichier JSON Schema séparé serait une
seconde source de vérité qui dériverait de l'implémentation, exactement comme
on redoute que les deux dépôts dérivent l'un de l'autre. Les deux modules
exposent donc leurs constantes (`API_VERSION`, `TIERS`, `HOOK_PHASES`,
`CLAIM_MODES`, `PLATFORM_KEYS`, `QUESTION_TYPES`) — `facts.py` de même
(`FACTS_EVENT`, `STATE_KEY`) —, et la CI de `nivuus/console`
assertera ces valeurs — une dérive du contrat casse alors un test, au lieu de
laisser un document mentir en silence.

## Trois coutures tranchées

1. **La thermique reste à `installer`, mais devient une interface publique.**
   `optimize-cpu-thermal.sh` est une politique d'hôte, mais ses modes
   `gaming`/`idle` sont pilotés par les hooks libvirt de `console`.
   `nivuus-cpu-mode@{gaming,idle}.service` devient un **contrat nommé** :
   `console` l'appelle s'il existe, et ne fait rien sinon. C'est ce qui rend
   `console` installable sur un Debian nu qui n'a jamais vu `installer`.

2. **Le moteur détecte les *capacités*, le package détecte les *détails*.** La
   ligne est nette et elle est nécessaire : le moteur doit pouvoir évaluer
   `requires.capabilities` **avant** d'exécuter le moindre hook, donc il ne peut
   pas déléguer cette réponse au package. Il détecte donc, pour tout package, un
   vocabulaire grossier et générique — « un IOMMU est-il actif », « existe-t-il un
   GPU dédié », « un NVMe est-il libre de tout montage hôte » — qui tient en une
   cinquantaine de lignes et sert à n'importe quel tiers.

   Le **précis** reste à `console` et arrive en phase `resolve` : quelles fonctions
   PCI exactement, dans quel groupe IOMMU, quels `vfio-pci.ids`, quelle topologie
   de cœurs. C'est là que partent les ~400 lignes extraites de `common/hardware.py`.

   Le package **raffine**, il ne refait pas : `hw.json` lui est remis, et en
   autonome son runner produit le même `hw.json` lui-même. Une seule forme de
   données, deux producteurs.

3. **La ligne de commande noyau : deux implémentations, et c'est justifié.** Dans
   le flux ISO, le moteur collecte les `kernel-cmdline` de tous les packages et
   les écrit **une fois**, à l'installation du bootloader. En autonome, `console`
   doit modifier un bootloader **existant** — un tout autre problème, dont cette
   machine est la preuve vivante (systemd-boot + kernelstub + entrées BLS à la
   main, voir `CLAUDE.md`). Ce ne sont pas deux copies, ce sont deux problèmes.

## `iso-build/build.sh` : généraliser un mécanisme qui tourne

Le script sait déjà embarquer un dépôt frère : `MQTT_REPO_DIR` + `BUILD_MQTT_DEB=1`
pour l'agent MQTT. Les packages suivent le même chemin — `git archive` d'un tag
épinglé vers `includes.chroot/opt/nivuus-packages/console/`. Le moteur découvre
les manifestes dans `/opt/nivuus-packages/*/`. Rien de neuf à inventer.

## Migration : l'ordre compte plus que le contenu

L'instinct est de découper git d'abord et de réparer ensuite. C'est l'inverse.
**La chirurgie git vient en dernier, quand elle est devenue ennuyeuse.**

| Phase | Contenu | Livrable |
| --- | --- | --- |
| **0** | Nettoyage : suppression de `vm-template.xml` et du `.deployed-backup`, correction de `QUICKSTART.md` et `docs/vm-configuration.md:462` | Un commit |
| **1** | **Le moteur de packages, sans rien déplacer.** Schéma, chargeur, validation, conflits, les trois phases, `nivuus-package-activate@.service`. Un package factice sert de preuve. `kvm-vfio` reste intact | Shippable, rien ne casse |
| **2** | **`console` devient un package, toujours dans ce dépôt.** Le manifeste et les hooks appellent le code existant. `_kvm_vfio_thermal` se scinde en `_thermal` et le package. `install.sh` se dissout. `hardware.py` est coupé. `retro` cesse d'être un pont | **Le découpage est fait et prouvé, sans avoir touché à git** |
| **3** | `git filter-repo` → `nivuus/console` avec son historique. Suppression des mêmes chemins côté `installer`. `build.sh` apprend `CONSOLE_REPO_DIR` | Mécanique |
| **4** | Le runner autonome de `console` : détection, questions en CLI, bootloader existant (GRUB **et** systemd-boot) | Indépendant |

**Note a posteriori (2026-08-28) : la phase 2 ci-dessus, écrite comme une seule
ligne, s'est exécutée en quatre étapes distinctes — 2a à 2d — mais ce
découpage est venu APRÈS ce spec, à l'exécution, pas ici.** Ce document ne les
a jamais nommées ; l'historique git le fait (`phase 2a` : la console devient
un package côté hôte ; `2b` : câblage du cycle de vie de la VM côté hôte ;
`2c` : l'invité entre dans le package ; `2d` : `activate` construit l'invité).
Le volet invité de la phase 2 — `activate` sait désormais construire et
démarrer l'invité Windows, pas seulement gérer un domaine déjà défini — est
**fait**, daté 2026-08-28 : voir `2026-08-28-console-activate-invite-design.md`
pour sa conception et `CLAUDE.md` pour l'état mesuré (33 suites, exit 0) et les
résidus qu'il laisse ouverts. Cela ne clôt pas la phase 2 dans son ensemble —
2a à 2c avaient déjà leurs propres livraisons — seulement le morceau que ce
spec-ci décrivait encore comme à faire.

La phase 2 porte tout le risque, et c'est pourquoi elle se fait **avec les deux
moitiés encore dans la même suite de tests**. Quand la phase 3 arrive, la
frontière est déjà prouvée : il ne reste qu'à déplacer des fichiers dont on sait
qu'ils ne s'appellent plus.

L'historique git est préservé par `git filter-repo` : le passé de
`windows-guest/` porte les mesures HDR, les pièges Apollo et quatre specs, qui
ne doivent pas devenir un commit d'import unique.

## Les 20 fichiers de tests

**`nivuus/installer` (5)** — `test_install_engine_features` et
`test_webapp_models` (à réécrire : `kvm-vfio` quitte `KNOWN_FEATURES`, le
validateur `retro` part avec), `test_hw_blackbox`, `test_net_rps_ecores`,
`test_pcie_wifi_link_guard`. **Plus les nouveaux** : validation de manifeste,
détection de conflits, contrat de `resolve`, collecte des `kernel-cmdline`.

**`nivuus/console` (14)** — les 13 `test_windows_guest_*`, plus
`test_vm_wake_gate` et `test_handle_vm_start`. `test_windows_guest_hardware`
suit le code extrait et change ses imports.

**Un test devait mourir, et c'est le bon signe** : `test_retro_marker_bridge.py`
n'existe que pour prouver que `features.py` et `build.py` s'accordent sur le
chemin du marqueur — un couplage inter-moitiés. Après le découpage, `retro` est
une question du wizard de `console`, répondue dans sa propre config : il n'y a
plus de pont, donc plus de garde-fou à maintenir.

**Correction (tâche 3, 2026-08-28) : il a survécu, décision prise en
connaissance de cause.** La prémisse ci-dessus ne s'est pas réalisée. `retro`
est bien une question du wizard de `console`, répondue dans sa propre
config — mais `build.py` (devenu `console/guest/build.py`) s'exécute bien
plus tard que `install`, éventuellement à la main, éventuellement après un
redémarrage : à ce moment-là la config du wizard n'existe plus. La tâche 3 a
donc fait de la réponse `retro` un témoin durable sur disque
(`console/hooks/install.py` l'écrit, `console/guest/build.py` le lit)
plutôt qu'un pont vers une config déjà éteinte. Le pont existe donc toujours,
plus court, entièrement à l'intérieur de `console/` — et
`test_retro_marker_bridge.py` avec lui, déplacé dans `console/tests/`,
toujours vert (`make -C console test`). La panne qu'il empêche — case cochée
dans le wizard, rien d'installé sur l'invité, aucun test pour le dire —
n'est pas moins silencieuse parce que les deux extrémités partagent
désormais un répertoire. Un spec qui reste faux est pire qu'un spec
corrigé : cette section documente donc la décision, pas seulement le
constat.

## Deux livrables faciles à oublier

* **`CLAUDE.md` doit être coupé.** 110 Ko documentant les deux moitiés. Le
  précédent est écrit dans ce fichier même : quand `nivuus/mqtt` a été extrait le
  2026-08-26, il a emporté sa documentation et gardé une section *Host Context*
  rappelant le minimum de faits hôte dont son code dépend. `console` fait pareil.
* **`nivuus/console` a besoin de sa propre CI.** `build-iso.yml` reste à
  `installer` ; `console` lance pytest sur ses 14 fichiers.

## Hors périmètre — chantiers réservés, un spec chacun

Le découpage laisse la place à ces quatre chantiers sans les traiter :

1. **Téléchargement de la dernière version de Windows 11 IoT.** Correction
   (2026-08-28, vérifiée) : l'affirmation « il n'existe aucun chemin officiel »
   était fausse. Le fwlink `https://go.microsoft.com/fwlink/?linkid=2270353`
   redirige (200) vers
   `26100.1742.240906-0331.ge_release_svc_refresh_CLIENT_IOT_LTSC_EVAL_x64FRE_en-us.iso`
   — bien une IoT Enterprise LTSC 2024, base 26100/24H2, la même base que celle
   sur laquelle le HDR a été mesuré de bout en bout dans ce dépôt. Mais le nom
   porte `_EVAL_` : c'est le média d'**évaluation**, 90 jours. L'invité prouvé en
   production (`slmgr /dli` → `IoTEnterpriseS edition, VOLUME_MAK channel,
   License Status: Licensed`) venait de l'ISO **volume**
   `en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso`, pas de
   celle que sert ce fwlink. Une édition Évaluation de Windows **client** ne
   s'est historiquement jamais convertie en licenciée par `slmgr /ipk` —
   `DISM /Set-Edition` ne vaut que pour Server. **La question qui reste ouverte
   n'est donc plus « existe-t-il un lien officiel » (oui) mais « la clé MAK de
   production licencie-t-elle ce média d'évaluation »** — à mesurer dans son
   propre spec en phase 2c, jamais à supposer.
2. **Packages invités optionnels** (Apollo, Steam, retro, et au-delà) — affaire
   interne à `console`, sans contrat public, puisque le point d'extension tiers
   est côté hôte.
3. **API tierce documentée** — publier le schéma, écrire le guide, fournir un
   package d'exemple.
4. **Le runner autonome** (phase 4 ci-dessus) est spécifié dans son principe mais
   son implémentation — en particulier la modification d'un bootloader existant,
   GRUB comme systemd-boot — mérite son propre plan.
