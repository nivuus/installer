# Invité Windows automatisé — sous-projet B : provisionnement de l'invité

**Date** : 2026-08-22
**Statut** : design validé, prêt pour le plan d'implémentation
**Spec parente** : [`2026-08-22-windows-guest-iso-design.md`](2026-08-22-windows-guest-iso-design.md)
**Sous-projets liés** : [C — domaine libvirt](2026-08-22-windows-guest-domaine-design.md), [bascule](2026-08-22-windows-guest-bascule-design.md)

## Objectif

Amener l'invité LTSC 26100, fraîchement installé par le sous-projet A, à
l'état d'appliance de cloud gaming : pilote NVIDIA, écran virtuel SudoVDA,
Apollo configuré, Steam, agent Guacamole, énergie et hibernation, politique de
mise à jour. Le tout **hors-ligne**, depuis une charge utile embarquée à la
fabrication.

## Le fait mesuré qui fonde ce sous-projet

Le 2026-08-22, sur un invité LTSC jetable avec le GPU réel, Apollo 0.4.6 et un
flux Moonlight vivant :

```
[avant] target=256 rc=0 supported=1 enabled=0 bpc=8  name=nivuus-probe
set     target=256 rc=0
[apres] target=256 rc=0 supported=1 enabled=1 bpc=10 name=nivuus-probe
```

**HDR 10 bits réel sur l'écran virtuel SudoVDA.** Identification verrouillée :
`DISPLAY\SMKD1CE\…UID256_0`, fabricant `SMK` (SudoMaker), `UID256` = la cible
sondée. `paths=1` : avec `dd_configuration_option = ensure_only_display`,
l'écran virtuel était **le seul actif**.

Deux réserves à porter dans le plan :

1. **La sonde a forcé l'état** (`DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE`)
   plutôt que d'attendre le client — le Moonlight de l'hôte décode en logiciel
   et son `dynamicRange` oscillait. Ce qui est prouvé, c'est que **Windows
   26100 sait allumer le HDR sur cet écran** ; que la TV pilote la chaîne de
   bout en bout est le test d'acceptation de B.
2. **Le démarrage à froid sans aucun écran n'est pas testé.** L'invité jetable
   portait une VGA émulée. C conserve donc un périphérique vidéo émulé, ce qui
   referme ce risque.

## Modèle d'exécution : les étapes de A, étendues

B reconduit le mécanisme du sous-projet A — scripts `provision/NN-*.ps1`
enchaînés par `run-all.ps1`, avec le **jeton de redémarrage**. Ce n'est pas de
la fidélité de principe : c'est le seul mécanisme du projet **éprouvé en
conditions réelles**, le journal de l'invité du 2026-08-22 montrant
`skip 10-nvidia.ps1 (already done)` après le redémarrage du pilote.

Deux alternatives ont été écartées. Un **manifeste déclaratif** avec exécuteur
générique serait plus élégant pour le sous-projet D, mais inventerait
l'abstraction avant d'avoir assez de cas pour la dessiner, et rendrait chaque
étape plus difficile à déboguer dans un invité où déboguer coûte cher
(WinRM en session 0, affichage en session 1). Un **outil de gestion de
configuration** apporterait une vraie idempotence au prix d'une dépendance
qu'il faut elle-même installer hors-ligne.

Une discipline est reprise du premier : **chaque étape déclare ses artefacts en
un seul endroit**, pour que le contrôle à la fabrication et la vérification
dans l'arbre déposé ne puissent pas diverger — le couple
`missing_binaries`/`verify_staged` qu'une revue de A avait imposé.

## La charge utile

| Artefact | Provenance | Note |
| --- | --- | --- |
| Pilote NVIDIA 610.88 | déjà dans `/media/data/nivuus-win-payload/nvidia` | éprouvé en A |
| Installeur Apollo 0.4.6 | déjà dans `…/apollo` | éprouvé le 2026-08-22 |
| SudoVDA | **inclus dans l'installeur Apollo** | vérifié : `ROOT\DISPLAY\0000`, `CM_PROB_NONE` |
| `SteamSetup.exe` | récupéré à la fabrication | **NSIS 3 confirmé** : `/S /D=` disponibles |
| NetKVM, `viofs` | extraits de l'ISO virtio-win à la fabrication | ~50 Mo |
| WinFsp | version publiée, à la fabrication | requis par virtiofs |
| `agent.exe` | **extrait de la VM actuelle avant l'effacement** | voir la spec de bascule |

Le marqueur `\nivuus\PAYLOAD.id` et le balayage des lettres de lecteur sont
repris de A — la lettre du média n'est pas prévisible. `PROVISION_VERSION`
passe de `A1` à `B1`.

🔴 **La fabrication peut aller sur le réseau ; l'installation dans l'invité,
jamais.** Le réseau de l'invité n'existe pas de façon fiable au moment du
provisionnement, et une URL qui périme casserait des installations futures.

## L'ordre des étapes

```
00-bootstrap   (existe) WinRM, découverte de la charge utile
10-nvidia      (existe) pilote, jeton de redémarrage
15-virtio      NetKVM ; puis WinFsp + viofs — ÉTAPE NON BLOQUANTE
20-disque      assigne D:, crée D:\Steam et D:\state
25-apollo      installe Apollo (donc SudoVDA), jonctionne sa config vers D:,
               écrit sunshine.conf et apps.json, sème les identifiants
30-steam       SteamSetup.exe /S /D=D:\Steam
40-agent       dépose agent.exe, crée la tâche planifiée de session 1
50-energie     hibernation, plan de performances, écran de verrouillage
55-maj         exclusion des pilotes de Windows Update, pas de redémarrage auto
99-marqueur    vérifie, écrit PROVISION.done, ouvre 5985 EN DERNIER
```

🔴 **Chaque étape est idempotente vis-à-vis de D:.** Le volume D: survit aux
reconstructions : on **sème si absent**, on ne réécrit jamais. Sans cette
règle, une reconstruction écraserait les appairages d'Apollo et la session
Steam — précisément ce que la partition de persistance existe pour éviter.

⚠️ **Le port 5985 n'est pas un signal de fin.** `00-bootstrap.ps1` l'ouvre
transitoirement au *début* du provisionnement ; un sondage du port a déjà
annoncé « terminé » alors que le pilote NVIDIA commençait à s'installer. Le
seul signal est le **marqueur** `C:\nivuus\state\PROVISION.done`.

## C: jetable, D: persistant

L'appliance ne vaut que si on peut la reconstruire ; or si les jeux vivent sur
C:, chaque reconstruction coûte des centaines de gigaoctets de
téléchargement. Le partitionnement sépare donc **C: (système, ~200 Go)** de
**D: (état)**.

**Steam est installé sur D:**, pas configuré pour y installer. Pré-remplir
`libraryfolders.vdf` fonctionne mal — Steam le réécrit et le dossier par
défaut dérive. `SteamSetup.exe /S /D=D:\Steam` rend `D:\Steam\steamapps`
bibliothèque par défaut **par construction**, et rien ne peut retomber sur C:.

Conséquence qui dépasse les jeux : un effacement de C: laisse l'installation
Steam entière intacte, **y compris `config/loginusers.vdf`**, le jeton de
session. Relancer l'installeur avec le même `/D=` sur le C: neuf ne recrée que
les entrées de registre et les raccourcis. Ni « ajouter un dossier de
bibliothèque », ni reconnexion.

**La configuration d'Apollo est jonctionnée vers `D:\state\apollo`**, si bien
que les clients appairés et les identifiants de l'IHM traversent les
reconstructions : la TV n'est jamais à réappairer, et `game.allanic.me`
continue de fonctionner sans retoucher Pomerium.

**D'où viennent les identifiants de l'IHM Apollo** : d'un fichier en mode 600
sur l'hôte, lu à la fabrication, **jamais depuis la ligne de commande** — même
posture que le mot de passe administrateur et la clé produit en A. Ils sont
**distincts** du mot de passe administrateur Windows, que la bascule sépare.
L'étape 25 les sème seulement si `D:\state\apollo` est vide.

⚠️ **Aucun identifiant Steam n'entre jamais dans l'image.** Le propriétaire se
connecte une fois. Steam Guard peut redemander une validation après une
réinstallation (empreinte machine neuve) : c'est un courriel, pas un
re-téléchargement.

⚠️ **La lettre `D:` doit être assignée explicitement** dans le fichier de
réponses, sinon le lecteur optique peut la prendre — A a déjà payé ce piège
avec le balayage des lettres pour trouver la charge utile.

## L'ouverture de session automatique reste active

En A, le dernier script **désactivait** l'ouverture de session automatique :
c'était le prix assumé pour naître en session 1. **B la garde active en
permanence**, parce que les deux consommateurs en dépendent — l'agent
Guacamole doit tourner en session 1 (`check-session.sh` existe pour le
vérifier) et Apollo capture un bureau interactif.

Ce que cela implique, dit franchement : la machine tient en permanence une
session ouverte sur un bureau déverrouillé. **Le dummy plug retiré, aucun
affichage physique n'existe plus** : ce bureau n'est atteignable que par
Apollo (client appairé), par l'agent (plateforme authentifiée), ou par la
console VNC en écoute sur `127.0.0.1`, donc réservée à root sur l'hôte. C'est
la posture qu'a déjà la VM actuelle, qui reprend d'hibernation sur un bureau
déverrouillé par politique délibérée.

## Apollo

```
dd_configuration_option         = ensure_only_display
isolated_virtual_display_option = disabled
dd_hdr_option                   = auto
```

`ensure_only_display` est **mesuré** : `paths=1`, écran virtuel seul actif.
`isolated_virtual_display_option = disabled` évite la disposition multi-écrans
en coin, inadaptée à une machine sans tête. `dd_hdr_option = auto` suit la
demande du client et cesse d'être décoratif : la TV qui demande du HDR en
obtient enfin.

`apps.json` reproduit les deux applications de production — `Desktop` avec
`virtual-display: true` et le script de maximisation de Steam, plus
`Steam Big Picture` — avec les chemins pointant vers `D:\Steam\steam.exe`.
🔴 **C'est le drapeau `virtual-display` de l'application, et non l'option
`isolated`, qui fait naître l'écran SudoVDA** (piège du 2026-07-23).

Plus rien à propos du dummy plug : ni `output_name` à épingler, ni écran
physique à désactiver.

⚠️ **Apollo 0.4.6 rejette l'authentification Basic sur `/api/*`.** Mesuré :
`GET /api/config` et `POST /api/pin` renvoient 401 ; l'authentification passe
par `POST /api/login` qui pose un cookie `auth`. Or la route Pomerium
`game.allanic.me` injecte un en-tête `Authorization: Basic`. Le traitement
appartient à la bascule : **retirer l'en-tête injecté** et laisser Apollo
présenter sa propre page derrière le SSO.

## L'agent Guacamole

`agent.exe` est un **artefact de charge utile** : il n'a aucun état utilisateur
à préserver, et il est redéployé à chaque reconstruction par construction.
Rien à jonctionner vers D:.

🔴 **Il doit tourner en session 1, jamais en service** — même contrainte que
celle qui a interdit `SetupComplete.cmd` en A. L'outillage Guacamole le lance
déjà par tâche planifiée (`TASK_NAME="guacamole-agent"`) ; B reprend cette
forme, avec `/IT`, à l'ouverture de session, et `SIGNALING_URL` pointant sur
`ws://192.168.3.1:8080`.

⚠️ **Divergence de nom de compte, qui mordra en silence** : l'outillage
Guacamole vaut par défaut `USER_NAME="Administrateur"` (VM actuelle,
média français), alors que le média LTSC est en anglais et que A a fixé le
compte à **`Administrator`**. Il faut poser `WINDOWS_ADMIN_USERNAME` côté
Guacamole, ou aligner le nom de compte.

Le poste de développement Guacamole — conséquence du modèle appliance, puisque
`build-agent.sh` et `sync-agent.sh` visent 192.168.3.2 — est **hors périmètre**
de B.

## Windows Update

LTSC a déjà supprimé les mises à jour **de fonctionnalités** : c'est la raison
même de son choix. Restent les correctifs mensuels, et l'invité est joignable
depuis le WAN sur les ports de streaming — les couper échangerait un risque de
panne contre un risque d'intrusion, qu'une réinstallation ne répare pas.

Ce qui casserait cette configuration n'est pas un correctif de sécurité mais un
**pilote** poussé par Windows Update. C'est donc cela qu'on bloque :

```
Policies\...\WindowsUpdate\ExcludeWUDriversInQualityUpdate = 1
...\DriverSearching\SearchOrderConfig                      = 0
Policies\...\WindowsUpdate\AU\NoAutoRebootWithLoggedOnUsers = 1
```

## Structure de fichiers

| Fichier | Rôle |
| --- | --- |
| `installer/windows-guest/provision/15-virtio.ps1` … `55-maj.ps1` | les nouvelles étapes |
| `installer/windows-guest/provision/run-all.ps1` | étendu (jeton de redémarrage inchangé) |
| `installer/windows-guest/payload.py` | artefacts de B ; `PROVISION_VERSION = "B1"` |
| `installer/windows-guest/templates/sunshine.conf.j2`, `apps.json.j2` | configuration d'Apollo |
| `installer/windows-guest/tests/` | tests de rendu et de la déclaration d'artefacts |

## Tests d'acceptation

Trois, pas un — sur disque jetable, avant toute action sur le NVMe :

1. **HDR de bout en bout** : un flux depuis la TV, `bpc=10` sur la cible
   SudoVDA, cette fois **demandé par le client** et non forcé par la sonde, et
   `Client dynamicRange: 1` soutenu dans le journal d'Apollo.
2. **Agent en session 1** : `check-session.sh` le confirme.
3. **Reconstruction préservant D:** : réinstaller C:, retrouver Steam connecté
   avec ses jeux et la TV toujours appairée, sans aucun geste manuel.

## Risques

| Risque | Portée | Traitement |
| --- | --- | --- |
| **`agent.exe` perdu** avec la VM actuelle | bloque B définitivement | précondition de la bascule, avant toute action destructive |
| Le client ne demande jamais le HDR | invalide le test 1 | la TV est le client de référence ; le Moonlight logiciel de l'hôte ne l'est pas |
| **NetKVM / WinFsp** hors-ligne non éprouvés | dégrade | virtio-net bloquant, virtiofs non bloquant |
| Une mise à jour d'Apollo casse la jonction de configuration | perte des appairages | vérifier la jonction après toute mise à jour |
| **Anti-triche** : le masquage a disparu (C) | usage | conséquence assumée du refus au cadrage |

## Hors périmètre

La génération du domaine (C), la bascule, le poste de développement Guacamole,
l'écran du portail et l'intégration au moteur (D). **Explicitement refusé** :
tout contournement des systèmes anti-triche.
