# Invité Windows automatisé — la bascule vers LTSC

**Date** : 2026-08-22
**Statut** : design validé, prêt pour le plan d'implémentation
**Spec parente** : [`2026-08-22-windows-guest-iso-design.md`](2026-08-22-windows-guest-iso-design.md)
**Dépend de** : [C — domaine libvirt](2026-08-22-windows-guest-domaine-design.md), [B — provisionnement](2026-08-22-windows-guest-provisionnement-design.md)

## Objectif

Remplacer définitivement l'invité **Windows Server 2022** par l'invité
**Windows 11 IoT Enterprise LTSC 2024**, sur le même NVMe passé, sans perdre
aucun des mécanismes que l'hôte a construits autour de lui.

Cette spec existe séparément parce que la bascule n'appartient ni à B ni à C :
c'est là que vivent **toutes les décisions irréversibles**. La laisser
implicite serait la meilleure façon de la rater.

## Décision du propriétaire : effacement direct, aucun retour arrière

Le NVMe est effacé sans sauvegarde préalable. La bibliothèque Steam qu'il porte
est perdue et les jeux seront retéléchargés. Server 2022 cesse d'exister et
**aucun média ne permet de le reconstituer**.

Cette décision a été prise en connaissance de cause, après que la conception
eut établi que la bibliothèque Steam se trouve bien sur ce disque
(`/media/data/Games` ne contient que de l'émulation) et après présentation de
deux alternatives — image `ntfsclone` vers `/media/backup`, ou copie ciblée de
`steamapps`.

**La parade n'est pas un filet, c'est l'ordre des opérations** : tout est
prouvé sur un disque jetable avant que le NVMe soit touché. Quand la bascule
commence, la seule variable non éprouvée est le disque cible lui-même.

## Préconditions : rien de destructif tant que les cinq ne sont pas vraies

1. 🔴 **`agent.exe` construit sur la VM actuelle et archivé hors d'elle.**
   Bloquant absolu. L'agent Guacamole se compile aujourd'hui *dans* la VM
   (sources synchronisées en CIFS vers `C:\dev`, `cargo build` par WinRM, avec
   Rust, les Build Tools Visual Studio et cmake). Le modèle appliance retenu
   suppose un binaire pré-construit, et le poste de développement destiné à en
   produire n'existe pas encore. **Après l'effacement, plus aucune machine ne
   sait en produire.**

2. 🔴 **L'ISO d'installation reconstruite avec le correctif OOBE, et ce
   correctif vérifié.** `Microsoft-Windows-International-Core` a été ajouté à
   la passe `oobeSystem` le 2026-08-22 à 16:01, mais l'ISO sur disque date de
   15:01 et ne le porte pas : l'installation du même jour a de nouveau bloqué
   sur pays et clavier, franchis à la main par trois `virsh send-key`. **Une
   bascule ne peut pas dépendre de frappes manuelles.**

3. **C prouvé sur disque jetable** : Secure Boot + vTPM + passthrough, et
   surtout **hibernation S4 puis reprise** — jamais éprouvée à ce jour.

4. **B prouvé sur le même disque** : HDR demandé par la TV, agent en session 1,
   reconstruction préservant D:.

5. **Inventaire de ce que porte la VM actuelle** — sauvegardes hors Steam,
   scripts locaux (`C:\Apollo-scripts\maximize-steam.ps1`), licences. Le
   propriétaire a choisi l'effacement direct : ce n'est pas une sauvegarde,
   c'est un regard avant de détruire.

## La séquence

1. Depuis Windows : **extraire `agent.exe`** et l'inventaire vers un partage.
2. Arrêt propre : `virsh shutdown --mode acpi Windows`.
   ⚠️ Le mode par défaut ne délivre pas l'événement ACPI sur libvirt 11.x — il
   rapporte « en cours » puis rien.
3. `virsh undefine Windows --nvram` — le varstore actuel n'a pas les clés
   Secure Boot et ne doit pas survivre.
4. `domain.py define` : **même nom `Windows`, même MAC `52:54:00:48:e0:3e`**,
   varstore neuf issu de `OVMF_VARS_4M.ms.fd`.
5. Démarrer sur le média : l'installation sans surveillance partitionne le NVMe
   en **C: (~200 Go)** et **D: (le reste)**.
6. B provisionne jusqu'au marqueur `PROVISION.done`.
7. Vérifications (ci-dessous).

## Ce que la bascule doit retoucher sur l'hôte

### `/usr/local/bin/winvm` — sinon l'économie d'énergie meurt en silence

Ce script code en dur `VM_USERNAME="Administrateur"` et son mot de passe, et
c'est **`vm-idle-shutdown.sh` qui s'en sert pour hiberner la VM**. Le nouvel
invité utilisant `Administrator` avec un mot de passe neuf, l'hibernation sur
inactivité cesserait de fonctionner **sans aucun message** — pendant que la
porte de réveil, elle, continuerait de réveiller. La VM resterait allumée en
permanence, ce qui annule tout le travail d'économie d'énergie de cette
machine.

### 🔴 Retirer `mount-vm.sh` — sinon la première VM peut wedger libvirtd

`started/begin/mount-vm.sh` boucle **sans limite** sur `nc -z 192.168.3.2 139`
puis monte `//192.168.3.2/c` sur `/media/vm` avec les identifiants de
`/etc/vm-credential`. Ce montage sert le circuit de développement Guacamole
(`sync-agent.sh` y écrit les sources), qui disparaît avec le modèle appliance.

Une appliance n'a aucune raison d'exposer SMB — et surtout pas un invité
joignable depuis le WAN. Le port 139 ne répondra donc jamais, **le crochet ne
rendra jamais la main, et il s'exécute pendant que libvirtd attend** : c'est
exactement la classe d'interblocage déjà payée sur cette machine, où un crochet
bloquant a fait s'empiler les clients virsh et survivre les processus à un
redémarrage de libvirtd.

Retirer `started/begin/mount-vm.sh` **et** `stopped/end/umount-vm-c.sh`, et
supprimer `/etc/vm-credential` (troisième endroit où dorment les identifiants
de l'invité, après `winvm` et la configuration Pomerium).

### La route Pomerium `game.allanic.me`

Retirer l'en-tête `Authorization: Basic` injecté : Apollo 0.4.6 le rejette
(mesuré, 401 sur `/api/config` et `/api/pin`). La restriction SSO
(`allowed_users`) est conservée ; Apollo présente sa propre page de connexion
derrière elle. Deux authentifications, mais insensibles aux versions futures —
et grâce à la configuration persistée sur D:, à ne faire qu'une fois.

### Séparer deux secrets aujourd'hui confondus

Le mot de passe administrateur Windows et celui de l'IHM Apollo sont
actuellement **la même chaîne**, présente en clair dans `winvm` et, encodée en
base64, dans `config.yaml` de Pomerium. La bascule est le moment de les
séparer : deux secrets distincts, deux fichiers en mode 600 sur l'hôte, comme
A le fait déjà pour le mot de passe administrateur et la clé produit.

### Ce qui ne demande aucune retouche

Le **nom de domaine** et la **MAC** préservés font survivre sans y toucher :
les crochets de bind/rebind du GPU, ceux de confinement et libération CPU, ceux
des règles firewalld de streaming, ceux des hugepages, les redirections de
streaming, et le réveil à la demande sur 47989. C'est la raison même de ces
deux invariants — seul le montage CIFS est retiré, délibérément.

## Vérifications après bascule

| Ce qu'on vérifie | Comment |
| --- | --- |
| Adresse préservée | l'invité obtient `192.168.3.2` |
| Réveil à la demande | ouvrir Moonlight côté client → la VM démarre |
| Hibernation sur inactivité | fermer le client → hibernation au bout de 10 min |
| GPU rendu à l'hôte | après hibernation, `ollama` revoit la RTX 4070 |
| HDR | flux depuis la TV, `bpc=10` sur la cible SudoVDA |
| Agent | `check-session.sh` confirme la session 1 |
| IHM Apollo | `game.allanic.me` accessible derrière le SSO |
| Activation | `slmgr /dli` → licence active |

## Risques

| Risque | Portée | Traitement |
| --- | --- | --- |
| **Effacement sans retour arrière** | total | décision assumée ; parade = tout prouver sur disque jetable d'abord |
| **`agent.exe` non extrait** avant l'effacement | perte définitive de la capacité de compilation | précondition 1 |
| **S4 sous Secure Boot** échoue après la bascule | l'économie d'énergie tombe | précondition 3, sur disque jetable |
| **`winvm` non mis à jour** | hibernation morte, VM allumée en permanence | retouche obligatoire, à vérifier explicitement |
| **`mount-vm.sh` laissé en place** | interblocage de libvirtd au premier démarrage | retrait obligatoire, avec `umount-vm-c.sh` |
| **Anti-triche** refuse la VM démasquée | usage | conséquence assumée ; premier endroit où regarder si un jeu se ferme |
| **Effacement futur de D:** par mégarde | perte des jeux | le partitionnement conditionnel est un piège ; à documenter en tête du plan |

## Hors périmètre

Le poste de développement Guacamole, la fenêtre de maintenance mensuelle
(écartée), et le sous-projet D.
