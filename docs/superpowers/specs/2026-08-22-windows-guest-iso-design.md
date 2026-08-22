# Invité Windows automatisé — ISO d'installation unattended

**Date** : 2026-08-22
**Statut** : design validé, prêt pour le plan d'implémentation du sous-projet A

## Objectif

Faire de l'installation de l'invité Windows une opération scriptée et
reproductible, intégrée à l'installeur Nivuus : `make build-iso` prend le média
Windows et la clé produit, et le média résultant installe l'hôte Debian **puis**
la VM Windows entièrement provisionnée — pilotes, Apollo, agent Guacamole.

Le déclencheur immédiat est la migration HDR : le HDR est physiquement
inaccessible à l'invité actuel, et le seul remède est un changement d'OS
(voir « Le fait qui déclenche tout »).

## Le fait qui déclenche tout

L'invité actuel est **Windows Server 2022 build 20348**, dont la base est
Windows 10 21H2. SudoVDA ne gère le HDR que sur **Windows 11 24H2** ; le pilote
concurrent `VirtualDrivers/Virtual-Display-Driver` pose la même limite.

Mesures du 2026-08-22 sur l'invité actuel, sonde `DisplayConfigGetDeviceInfo`
(`GET_ADVANCED_COLOR_INFO`) exécutée **en session interactive** :

```
sizes rc=0 paths=1 modes=2
target=24832 rc=31 supported=0 enabled=0 bpc=0
```

`rc=31` = `ERROR_GEN_FAILURE`. Le dummy plug HDMI n'offre aucune issue : son
EDID (HDP-V104) fait 128 octets **sans bloc d'extension CTA-861**, donc sans
métadonnées HDR possibles. Le flux actuel est du Rec.709 8 bits encodé en AV1
10 bits — du faux HDR qui coûte du débit pour rien.

⚠️ **La sonde doit tourner en session 1.** Via WinRM (session 0),
`QueryDisplayConfig` renvoie `paths=0` même en pleine session de streaming.
Passer par `schtasks /create ... /ru Administrateur /it /f` puis `schtasks /run`.

## Décisions prises au cadrage

| Décision | Choix | Raison |
| --- | --- | --- |
| Version cible | **Windows 11 IoT Enterprise LTSC 2024** (build 26100) | Base 24H2 donc HDR ; et surtout **aucune mise à jour de fonctionnalités forcée** — décisif pour une config graphique fragile (SudoVDA, passthrough, Apollo) |
| Périmètre | Extension de l'installeur Nivuus | Un seul média du bare metal jusqu'à la VM prête |
| Média Windows | Embarqué à la fabrication de l'ISO | LTSC IoT n'est **pas téléchargeable publiquement** (canal volume/VLSC) : l'installeur ne peut pas aller le chercher |
| Agent Guacamole | Binaire pré-construit | Modèle appliance : VM légère et reproductible, provisionnement rapide |
| NVMe actuel | Effaçable sans réserve | Décision du propriétaire ; aucune étape de sauvegarde à concevoir |

**Licence** : clé Windows 11 IoT Enterprise LTSC 2024 acquise le 2026-08-22.
Il reste à obtenir le **média** correspondant, que la clé ne fournit pas et qui
n'est pas téléchargeable publiquement.

**Conséquence assumée du modèle appliance** : la VM cesse d'être la machine sur
laquelle Guacamole se développe. `build-agent.sh` et `sync-agent.sh` visent
192.168.3.2 et ne fonctionneront plus tels quels. Il faudra une VM de
développement séparée, ou reconstruire l'agent depuis l'hôte.

## Découpage

Le périmètre dépasse ce qu'une spec peut porter. Quatre sous-projets, chacun
avec son cycle spec → plan → implémentation :

| # | Sous-projet | Contenu |
| --- | --- | --- |
| **A** | Générateur d'ISO unattended | `autounattend.xml`, réinjection dans le média, amorce du provisionnement |
| **B** | Provisionnement de l'invité | NVIDIA, SudoVDA, Apollo et sa config, WinRM, alimentation/hibernation, agent Guacamole, Steam |
| **C** | Génération du domaine libvirt | vTPM, Secure Boot, hugepages, topologie, hostdev — depuis `hardware.py` |
| **D** | Intégration wizard + moteur | `WindowsGuestConfig`, `steps/windows_guest.py`, écran du portail, drapeaux de `build-iso` |

**A vient en premier** parce que son test d'acceptation *est* la vérification
HDR : on sait si LTSC répond au besoin avant d'avoir investi dans C et D.

**Cette spec couvre le sous-projet A uniquement.**

## Architecture d'ensemble

Deux temps distincts.

**① À la fabrication** (poste de travail) :

```
make build-iso WINDOWS_ISO=/chemin/ltsc-26100.iso WINDOWS_KEY=XXXXX-...
```

L'ISO Nivuus reçoit le média Windows tel quel, les scripts de provisionnement,
et `agent.exe` compilé en croisé pour `x86_64-pc-windows-msvc`.

**② À l'installation** (serveur) : le portail gagne un écran « Invité Windows »,
le moteur une étape `windows_guest` après `features`.

Nouveau module `installer/windows-guest/` :

| Fichier | Rôle |
| --- | --- |
| `autounattend.xml.j2` | template Jinja2 — même moteur que les `.nmconnection.j2` existants |
| `build_media.py` | réinjecte l'`autounattend` généré dans une copie du média |
| `provision/*.ps1` | ce que l'amorce enchaîne dans l'invité |
| `domain.xml.j2` | le domaine libvirt (sous-projet C) |

## Sous-projet A — conception

### `autounattend.xml.j2`

| Passe | Contenu |
| --- | --- |
| `windowsPE` | fr-FR, clé produit, `DiskConfiguration` GPT (EFI 260 Mo + MSR 16 Mo + reste), index d'image LTSC, EULA |
| `specialize` | nom de machine, activation |
| `oobeSystem` | mot de passe Administrateur, saut complet de l'OOBE, ouverture de session automatique |
| `FirstLogonCommands` | amorce du provisionnement |

🔴 **Le provisionnement passe par `FirstLogonCommands` + ouverture de session
automatique, JAMAIS par `SetupComplete.cmd`.** `SetupComplete.cmd` s'exécute en
SYSTEM, session 0 — aveugle à l'affichage : `QueryDisplayConfig` y renvoie zéro
écran (mesuré aujourd'hui), et `check-session.sh` de Guacamole existe
précisément pour vérifier que l'agent tourne en session 1. Tout ce qui touche
l'affichage doit naître en session 1.

L'ouverture de session automatique est le prix de cette contrainte ; elle est
désactivée par le dernier script du provisionnement.

### `build_media.py`

Réinjection par **`xorriso`** (`oscdimg` est Windows-only) : recopie du média
d'origine en rejouant ses images de démarrage UEFI, plus `/autounattend.xml` à
la racine et un répertoire `/nivuus/` portant la charge utile.

Deux gardes :

1. **L'index d'image est lu, jamais codé en dur.** `wiminfo` sur `install.wim`
   donne le nom exact de l'édition ; le script **échoue bruyamment** si l'édition
   trouvée n'est pas une LTSC. Un média Pro glissé par erreur doit produire une
   erreur, pas une installation silencieusement différente.
2. **La bootabilité UEFI est vérifiée**, pas supposée — c'est le point fragile
   de la réinjection.

### `provision/`

Pour A, le strict minimum permettant de répondre à la question HDR :

| Script | Rôle |
| --- | --- |
| `00-bootstrap.ps1` | journalisation, politique d'exécution, activation de WinRM |
| `10-nvidia.ps1` | pilote NVIDIA (nécessaire : c'est lui qui porte la pile Advanced Color) |
| `20-sudovda.ps1` | SudoVDA |
| `99-marker.ps1` | marqueur de version, désactivation de l'ouverture de session automatique |

Apollo, Steam et l'agent Guacamole relèvent du sous-projet B.

### Acheminement de la charge utile

**Tous les binaires sont embarqués à la fabrication, dans `/nivuus/` sur le
média** : pilote NVIDIA, SudoVDA, et plus tard Apollo et `agent.exe`.
L'installation doit rester **entièrement hors-ligne** — le réseau de l'invité
n'existe pas encore de façon fiable au moment où le provisionnement s'exécute,
et un téléchargement introduirait une dépendance à des URL qui périment.

⚠️ **La lettre du lecteur d'installation n'est pas prévisible.** L'amorce ne
peut pas supposer `D:`. Elle balaie les lecteurs à la recherche d'un fichier
marqueur déposé par `build_media.py` (`\nivuus\PAYLOAD.id`) et échoue
bruyamment si elle ne le trouve pas, plutôt que de poursuivre une installation
partiellement provisionnée.

### Signal de fin d'installation

L'hôte doit savoir quand l'invité a fini. Le dernier geste du provisionnement
active WinRM et écrit un marqueur de version ; l'hôte scrute le port 5985 puis
lit le marqueur.

Pas de virtiofs à ce stade : le pilote n'est pas encore installé au moment où on
en aurait besoin.

### Le domaine de test

⚠️ **Le GPU réel est indispensable au test.** Une VM jetable sur virtio-gpu
donnerait un résultat asymétrique : un `supported=0` condamnerait LTSC, mais un
`supported=1` ne prouverait rien du cas réel, puisque c'est le pilote NVIDIA qui
porte la pile Advanced Color.

Domaine `Windows-LTSC-test`, jetable, écrit à la main :

- **disque qcow2 sur `/media/data`** — le NVMe n'est pas touché ;
- **GPU passé** (`01:00.0` + `01:00.1`), libre dès que la VM actuelle est éteinte ;
- vTPM 2.0 (`swtpm`, `<tpm model='tpm-crb'>`) et Secure Boot
  (`OVMF_CODE_4M.secboot.fd` + `OVMF_VARS_4M.ms.fd`, clés Microsoft
  pré-enrôlées), tous deux **obligatoires pour Windows 11** et absents de la VM
  actuelle ;
- `<loader>`/`<nvram>` **explicites**, jamais la sélection automatique de
  firmware — elle a déjà cassé l'hibernation S4 sur cette machine.

Bénéfice : **Server 2022 reste intact sur le NVMe** comme retour arrière tant
que la décision n'est pas prise.

### Test d'acceptation

La sonde Advanced Color, lancée en session 1 via tâche planifiée `/it`, doit
renvoyer :

```
supported=1   et   bpc>=10
```

Et, au même moment, **l'activation** : `slmgr /dli` doit rapporter une licence
activée. C'est le geste qui coûte une minute ici et une migration entière si on
l'oublie.

Référence actuelle sur Server 2022 : `rc=31, supported=0, bpc=0`.

Ce même test valide au passage **Secure Boot + vTPM + passthrough ensemble** —
la combinaison la plus risquée du projet, vérifiée nulle part.

## Risques

| Risque | Portée | Traitement |
| --- | --- | --- |
| **Secure Boot + S4 + passthrough** non vérifié ensemble | bloquant | levé par le test d'acceptation de A |
| **Compilation croisée de `agent.exe`** (`cargo-xwin`) jamais éprouvée sur ce code — DXGI, Windows.Graphics.Capture, ProjFS | bloque B | à sonder tôt, indépendamment de A |
| **Activation de la clé LTSC IoT** | bloque tout | clé acquise le 2026-08-22 ; les clés IoT LTSC revendues au détail passent par le canal volume et n'activent pas toujours — **l'activation est vérifiée dans le test d'acceptation de A**, pas en fin de migration |
| **Média LTSC absent** — la clé ne fournit pas l'ISO | bloque la fabrication | à obtenir séparément ; `build-iso` exige `WINDOWS_ISO=` et échoue sans lui |
| **Réinjection UEFI** par `xorriso` | bloque A | vérifiée par le premier démarrage du média |
| **Clé produit en clair dans l'ISO** si passée à la fabrication | sécurité | l'ISO est un média sensible ; la clé pourra être saisie au portail (sous-projet D) |

## Hors périmètre du sous-projet A

Domaine libvirt généré (C), écran du wizard et intégration au moteur (D),
Apollo, Steam et agent Guacamole (B). A se pilote par un script en ligne de
commande.

**Explicitement refusé** : tout contournement des systèmes anti-triche
(masquage d'hyperviseur, falsification d'identifiants matériels). Demandé au
cadrage, refusé, puis retiré par le demandeur. La documentation de B se
limitera à indiquer quels anti-triche acceptent une VM et lesquels la refusent.
