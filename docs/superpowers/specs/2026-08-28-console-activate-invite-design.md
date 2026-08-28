# `activate` construit l'invité (phase 2d) — conception

**Date** : 2026-08-28
**Statut** : approuvé (conception validée en séance, avant rédaction du plan)
**Spec parent** : `2026-08-27-decoupage-installer-console-design.md`

## Le problème

Le package `console` sait tout faire sauf ce pour quoi il existe. Depuis la
phase 2c il pose le dispatcher libvirt, les hooks GPU, les forward-ports, le
partitionnement CPU, six unités systemd, et il sait **construire son domaine** —
`console/guest/domain.py xml` produit du vrai XML sur le matériel de référence.
Ce qu'il ne sait pas faire, c'est installer Windows dedans. `hooks/activate.py`
arme les unités de réveil et s'arrête là.

La chaîne existe pourtant, entièrement, et elle est éprouvée : `fetch_payload.py`
récupère les binaires, `build.py` en fait une ISO sans surveillance de 993 Mo,
`domain.py define` déclare le domaine, et quatorze scripts PowerShell
provisionnent l'invité hors ligne. Elle a été lancée à la main en août 2026.
**Ce spec ne conçoit pas cette chaîne : il conçoit qui l'appelle, dans quel
ordre, et ce qui se passe quand elle échoue.**

## Ce qui a été mesuré avant de concevoir

Tout ce qui suit a été constaté sur l'hôte de référence, pas supposé :

| Fait | Conséquence sur la conception |
| --- | --- |
| L'ISO **volume** est présente (`/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso`, 4,8 Go) — celle qui s'active en `IoTEnterpriseS / VOLUME_MAK / Licensed` | La question du média Evaluation n'est **pas** bloquante. Elle reste ouverte pour une machine qui n'a pas d'ISO. |
| Les trois fichiers de secrets existent en 0600 (`windows-ltsc.key`, `windows-admin.pass`, `apollo-ui.pass`) | Le repli « fichier déjà posé » est réel ici, mais inexistant sur une machine neuve. |
| Le payload de pilotes pèse 1,8 Go, l'ISO produite 993 Mo | La chaîne complète est de l'ordre de l'heure, pas de la minute. |
| `/media/data` est à **94 %** (1,1 To libres) et n'existe pas ailleurs | Les chemins de sortie ne peuvent pas être codés en dur sur cet hôte. |
| `provision/99-marker.ps1` : « the host treats a reachable **5985** as "the guest is provisioned" », et l'ordre des étapes est délibérément tel que tout le reste est vrai avant que le port s'ouvre | L'hôte a déjà un signal d'achèvement fiable. Il ne faut pas en inventer un autre. |
| `build.py --disk-mode` vaut `wipe` par défaut, et `--data-partition-gb` vaut **820** | Une constante de taille codée en dur casse sur un NVMe qui n'est pas celui-ci. |
| `build.py` lit la clé produit dans un **fichier 0600** et jamais depuis `argv`, « where it would leak into ps output and shell history » | `activate` doit écrire les secrets dans des fichiers, pas les passer en arguments. |

## Décisions

### 1. `activate` fait le travail borné, puis rend la main

L'ordre : écrire les secrets → `fetch_payload.py` → `build.py` → `domain.py
define` → **un** démarrage de la VM → sortir.

L'installation Windows et le provisionnement se poursuivent seuls. C'est la
seule forme qui tienne : l'unité d'activation est un `oneshot`
`WantedBy=multi-user.target`, et lui faire tenir une heure au premier démarrage
demanderait de désarmer son délai — ce qui transformerait le moindre blocage en
machine qui ne finit jamais de démarrer, sans rien dire.

**Rejeté : aller jusqu'au provisionnement terminé.** Un seul témoin de succès
serait plus simple à raisonner, mais un échec à la minute cinquante rejouerait
tout au redémarrage suivant.

**Rejeté : s'arrêter à `define`.** La console ne serait pas fonctionnelle après
un reboot, et rien dans le chemin de réveil ne démarre une VM jamais installée :
les sockets attendent une sonde Moonlight, or personne n'a encore appairé de
client sur une console qui vient de naître.

### 2. Chaque étape est sautable sur constat

C'est le cœur de la conception, pas un raffinement. Le témoin
(`/var/lib/nivuus/packages/console.activated`) n'est écrit qu'en cas de succès,
donc **toute défaillance rejoue au démarrage suivant**. Sans idempotence, chaque
reprise coûte une heure ; avec, elle coûte le temps de constater.

| Étape | Constat qui la fait sauter |
| --- | --- |
| Écriture des secrets | Fichier présent, en 0600, et de contenu identique |
| `fetch_payload` | Le payload est complet — c'est `fetch_payload` lui-même qui le sait, il est déjà « offline-first » |
| `build` | L'ISO existe **et** ses entrées n'ont pas changé (voir ci-dessous) |
| `domain define` | `virsh dumpxml` répond pour le domaine |
| Démarrage | `virsh domstate` dit autre chose que `shut off` |

**« Les entrées n'ont pas changé » demande un témoin explicite.** Comparer des
dates de fichiers est faux : le payload est régulièrement retouché sans que
l'ISO ait besoin d'être refaite, et l'inverse existe aussi. Le plan devra poser
un fichier d'empreintes à côté de l'ISO — média source, arborescence du payload,
réponses du wizard qui entrent dans l'image — et ne reconstruire que sur
divergence. Reconstruire à tort coûte vingt minutes ; ne pas reconstruire à tort
livre une console qui ne correspond pas aux réponses données, ce qui est pire.
**En cas de doute, reconstruire.**

### 3. Les secrets viennent du wizard et atterrissent dans des fichiers 0600

Trois questions de type `secret` : la clé produit Windows, le mot de passe
administrateur (déjà présent), le mot de passe de l'interface Apollo. `activate`
les écrit dans les fichiers que `build.py` attend, puis passe `--key-file`,
`--password-file`, `--apollo-password-file`.

Sur une machine neuve, aucun fichier n'existe : le wizard est le seul chemin qui
fonctionne depuis l'ISO. **Rejeté : le fichier seul**, qui obligerait à se
connecter en SSH entre le reboot et l'activation — impasse sur une console
installée depuis une clé USB. **Rejeté : le repli automatique sur un fichier
existant**, qui donnerait deux sources de vérité pour un secret.

Le transit par `etc/nivuus/packages.json` est acceptable **depuis que ce fichier
est en 0600** (corrigé le 2026-08-28 ; il était en 0644 et publiait le mot de
passe administrateur à tout compte local). Les fichiers écrits par `activate` le
sont aussi, et le plan devra le vérifier plutôt que l'affirmer.

### 4. La taille de la partition de données est dérivée, jamais codée

`--data-partition-gb 820` est la valeur de ce NVMe-ci. `activate` la calcule
depuis la taille réelle du disque choisi — que `resolve` connaît déjà.

**Attention au sens, il est contre-intuitif** : c'est la partition **de jeux**
qui porte la taille fixe, et **Windows prend ce qui reste** (`autounattend.py`
l'explique : la partition de données est créée en premier, et `<Extend>` ne
s'applique qu'à la dernière créée). La dérivation soustrait donc ce que Windows
exige, elle ne réserve pas ce que les jeux demandent. Se tromper de sens sur un
petit disque produit un C: minuscule plutôt qu'un D: minuscule — une console qui
s'installe puis étouffe au premier correctif Windows.

Une console dont le disque dédié fait 500 Go ne doit pas recevoir une partition
de 820 : l'installation échouerait, ou pire, réussirait de travers.

**Le disque dédié est effacé, et cela doit rester dit en toutes lettres.**
`--disk-mode` vaut `wipe` par défaut : `activate` détruit le contenu du NVMe
choisi. La question du wizard l'annonce déjà (« il sera donné entièrement à la
VM ») et le moteur refuse une réponse qui désigne le disque d'installation. Le
plan ne doit rien ajouter de plus — mais rien retirer non plus : c'est la seule
opération irréversible de toute la chaîne.

### 5. L'observabilité passe par 5985, et sépare deux échecs

Une unité distincte surveille le port WinRM sur l'IP de l'invité et enregistre
l'issue. Elle existe parce que `activate` rend la main avant la fin : sans elle,
« la console est en train de s'installer » et « la console a échoué » sont
indiscernables pendant une heure, puis pour toujours.

Elle doit distinguer deux échecs que rien ne sépare aujourd'hui :

* **la VM n'a jamais démarré** — `virsh domstate` le dit ;
* **elle tourne mais le provisionnement n'aboutit pas** — le domaine est
  `running`, 5985 reste fermé au-delà d'un délai raisonnable.

Le second est le cas intéressant, et le seul que le journal de l'hôte puisse
rendre lisible : l'invité, lui, ne peut rien dire tant que WinRM est fermé.

### 5 bis. AMENDEMENT du 2026-08-28 — la décision 5 reposait sur deux prémisses fausses

La décision 5 ci-dessus **est révoquée**. Elle disait de sonder le port 5985 et
citait `99-marker.ps1` comme autorité. Les deux moitiés étaient fausses, et la
revue finale les a mesurées sur l'hôte :

**Le port 5985 n'est plus le témoin de fin, et ne l'est plus depuis le
2026-08-26.** `console/guest/provision/00-bootstrap.ps1` l'ouvre désormais à
l'étape **00**, délibérément, et son en-tête explique pourquoi : le port fermé
était un proxy qui « failed exactly where it mattered » — quand une étape
échoue, `99-marker.ps1` n'est jamais atteint, la règle reste fermée, et la seule
porte d'entrée disparaît précisément quand il faudrait regarder. Trois
provisionnements sont morts ainsi ce jour-là. **« The marker file IS the truth
about readiness; the port never was. »** Seul l'en-tête de `99-marker.ps1` dit
encore le contraire : il est périmé, et c'est lui que le spec citait.

**La découverte d'IP par `virsh domifaddr` ne peut aboutir sur cette
topologie.** Mesuré sur la VM de production en marche : `--source agent` échoue
(aucun agent invité n'est configuré, le domaine ne déclare aucun `<channel>`),
`--source lease` et `--source arp` rendent des tables vides, et
`virsh net-list --all` ne déclare **aucun** réseau libvirt — le domaine est sur
un pont externe. `handle-vm-start.sh`, dont la méthode avait été reprise, porte
le même défaut ; il ne l'a jamais trahi parce que le hook
`started/begin/rules.sh` pose les redirections de toute façon.

#### Ce qui les remplace, mesuré avant d'être écrit

**L'IP vient de la table de voisinage de l'hôte.** Le domaine déclare son MAC et
son pont ; `ip neigh show dev <pont>` associe l'un à l'autre dès que l'invité a
parlé. Vérifié sur la VM de production :

```
192.168.3.2 dev internalBridge lladdr 52:54:00:48:e0:3e REACHABLE
```

Aucun réseau libvirt n'est requis. Une entrée absente signifie « l'invité n'a pas
encore parlé », ce qui est exactement l'état à distinguer.

**Le témoin est un fichier, lu par-dessus WinRM, et vérifié en version.**
`console/guest/winrm_exec.py` existe, lit son mot de passe dans un **fichier et
jamais depuis `argv`** — même discipline que `build.py` — et prend `GUEST_IP`,
`GUEST_USER`, `GUEST_PASS_FILE` dans l'environnement. `testdomain.py` s'en sert
déjà exactement ainsi.

**La vérification de version n'est pas un raffinement.** `testdomain.py` le dit :
« a rebuild boots a disk that already holds the PREVIOUS run's marker, so its
mere presence proves nothing ». Le témoin doit porter
`provision_version=<PROVISION_VERSION courant>` — `B3` à ce jour, défini dans
`console/guest/payload.py`. Sans ce contrôle, une réinstallation lirait le témoin
de l'installation précédente et se déclarerait prête avant d'avoir commencé.

**`pywinrm` entre au manifeste.** Le paquet Debian est `python3-winrm`
(0.5.0-2). C'est le quatrième trou de dépendance de cette série, après
`firewalld`, `python3-jinja2` et `xorriso` : un package doit déclarer ce dont
son propre code a besoin.

#### La leçon, qui vaut au-delà de ce spec

J'ai cité un commentaire comme autorité sans vérifier qu'il était encore vrai.
Il datait de deux jours avant, et le fichier qui le contredisait était à côté.
**Un commentaire décrit l'intention de son auteur au moment où il l'a écrit ;
seul le code dit ce qui se passe aujourd'hui.** Quand deux fichiers se
contredisent, c'est une mesure qui tranche, pas le ton du plus affirmatif.

### 6. Les chemins de sortie ont un défaut portable

`/var/lib/nivuus/guest/` par défaut — payload, ISO produite, empreintes —
surchargeable par une réponse de wizard. `/media/data` est le choix de cet
hôte-ci, pas une propriété du package, et il est à 94 %.

## Ce que cette phase ne fait PAS

1. **Le paramétrage des constantes propres à la machine de référence** —
   adresse PCI en dur dans les hooks GPU, interface réseau en désaccord avec le
   reste de la chaîne, chemins `/opt/nivuus`, `winvm` déposé sans son client
   `winrm`. Toutes nommées dans « Limites connues » de `console/README.md`.
   Chantier séparé : son critère de succès — marcher sur une machine dont le GPU
   n'est pas au même emplacement — est **invisible depuis celle-ci**, et le
   mélanger à 2d rendrait indiscernable laquelle des deux moitiés a cassé.
2. **Le média d'évaluation.** Le fwlink `linkid=2270353` sert bien une IoT LTSC
   2024 (build 26100.1742, la base 24H2 sur laquelle le HDR a été mesuré), mais
   en édition **Evaluation**. Qu'une clé MAK licencie ce média n'est pas mesuré,
   et une édition Evaluation de Windows **client** ne se convertit
   historiquement pas par `slmgr /ipk` — `DISM /Set-Edition` ne vaut que pour
   Server. La question ne se pose que sur une machine sans ISO volume.
3. **Le wizard n'offre toujours aucun package.** `webapp/static/js/app.js`
   n'appelle aucune route `/api/packages` : la console reste inatteignable
   depuis le portail, et le seul chemin d'installation est une config portée à
   la main.
4. **La reprise d'une installation Windows à moitié faite.** Si l'invité échoue
   en cours de provisionnement, `activate` ne sait pas reprendre au milieu : il
   reconstruit et réinstalle. C'est assumé — le provisionnement est conçu pour
   être rejoué depuis zéro, pas repris.

## Risques

* **La voie nominale d'`activate` n'a jamais tourné contre un vrai systemd**,
  seulement contre un bouchon : `systemctl` est inutilisable depuis une session
  Claude (namespace PID). Cela vaut déjà pour l'armement des unités posé en
  phase 2b. Le plan ne peut pas lever ce risque ; il doit le nommer et laisser
  l'échec tolérant.
* **Un démarrage de VM déclenche les hooks GPU**, qui détachent la carte de
  l'hôte, arrêtent ollama, persistenced et Tdarr. `activate` démarre donc la VM
  au premier boot d'une machine fraîchement installée, où ces hooks n'ont jamais
  tourné. Un refus de hook fait échouer le démarrage — comportement voulu — mais
  `activate` doit le rapporter comme tel plutôt que comme un échec de
  construction.
* **La chaîne écrit environ 3 Go** (payload plus ISO). Sur une cible dont le
  disque système est petit, `/var/lib/nivuus/guest/` peut ne pas convenir. Le
  plan devra vérifier la place avant de commencer, pas au milieu.
