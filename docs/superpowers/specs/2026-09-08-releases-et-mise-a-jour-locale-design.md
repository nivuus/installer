# Releases GitHub et mise à jour locale des packages Nivuus

**Date :** 2026-09-08
**Portée :** les neuf dépôts de `packages/` (hors `installer`, qui reçoit
l'updater et des releases de son payload), le workflow réutilisable de
`nivuus/.github`, et la construction de l'ISO.

## Le problème

Un package Nivuus posé sur une machine y reste figé pour toujours. Le moteur
copie `manifest.root` vers `{cible}/opt/nivuus-packages/<nom>/`, enregistre sa
version dans `/etc/nivuus/packages.json`, arme
`nivuus-package-activate@<nom>.service` — et plus rien ne bouge jamais.
Corriger un bug dans un package impose aujourd'hui de réinstaller la machine
par ISO.

Symétriquement, un seul dépôt de la suite sait publier une release (`shell`),
et son auto-updater pointe encore sur `maximeallanic/nivuus-shell`, un nom mort
depuis le renommage de l'organisation.

Ce document décrit deux choses indissociables : **un contrat de release commun
aux dix dépôts**, et **un chemin de mise à jour sur la machine installée** qui
ne se contente pas de rafraîchir un dépôt mais pose réellement la nouvelle
version.

## État des lieux, mesuré

- **Dix dépôts** dans `packages/`. Cinq portent un `nivuus-package.yaml`
  (`desk`, `home-desk`, `home-manager`, `home-stock`, `media-manager`) ;
  `console` est un package vivant à l'intérieur d'`installer` ; quatre n'ont
  aucun manifeste (`marketplace`, `mqtt`, `retro`, `shell`).
- **Trois sont privés** : `desk`, `home-desk`, `home-stock`.
- **Un seul workflow de release existe**, `shell/.github/workflows/release.yml`,
  déclenché à la main (`workflow_dispatch` avec choix du bump).
- **`nivuus/.github` héberge déjà neuf workflows réutilisables**, dont
  `policy.yml`, `ci-node.yml`, `ci-python.yml`, `ci-rust.yml`, `ci-shell.yml`.
  L'idiome « un workflow partagé, paramétré par nature » est donc établi.
- **La CI impose déjà des titres de PR conventionnels en anglais**
  (`policy / Coding rules`), et `main` est en squash sur les dépôts protégés :
  le sujet du commit de fusion *est* un commit conventionnel.
- **`main` est protégée avec `enforce_admins: true`** sur `installer`, `mqtt`,
  `marketplace` et `shell` — aucun workflow n'y poussera de commit de bump.
- **Les packages entrent dans l'ISO par `git archive HEAD`** du clone local
  (`PACKAGE_REPOS` dans `iso-build/build.sh`) : aucune notion de version
  publiée, aucun épinglage.
- **`shell` possède déjà la commande `nivuus`** (`bin/nivuus` :
  `install|uninstall|update|doctor|help`), avec son propre manifeste
  d'installation et un rollback.
- **L'agent `mqtt` publie déjà de la découverte Home Assistant**
  (`homeassistant/<composant>/<device>/…/config`), connaît `command_topic`, et
  tourne **sur l'hôte** en service systemd issu de son `.deb`.
- **La machine de développement n'a pas été installée par le moteur** : pas de
  `/opt/nivuus-packages/`, pas de `/etc/nivuus/packages.json`. `/opt/nivuus/`
  contient des piles posées à la main.

## Décisions

1. **Les neuf dépôts deviennent des packages du moteur.** `marketplace`,
   `mqtt`, `retro` et `shell` reçoivent un `nivuus-package.yaml` et un
   `hooks/install.py` qui enveloppe leur mécanique native. Un seul contrat, un
   seul updater, une seule commande.
2. **`installer` n'est pas un package** — il ne peut pas s'installer lui-même —
   mais il **publie des releases de son payload**, pour que l'outil qui met
   tout à jour puisse se mettre à jour lui-même.
3. **La mise à jour rejoue `install` puis `activate` avec `root="/"`.** Aucune
   phase nouvelle. En contrepartie, **l'idempotence des hooks `install` devient
   un contrat dur, prouvé mécaniquement en CI** (§ Tests).
4. **La voie autonome reste de première classe.** Un package doit rester
   installable sur une Debian ordinaire sans le moteur — c'est déjà la raison
   pour laquelle chaque manifeste déclare ses propres `apt:` au lieu de se
   coupler aux features. `shell` en particulier garde son
   `curl … | bash`.
5. **Les trois dépôts privés deviennent publics**, après balayage de leur
   historique complet. Un seul chemin de récupération, aucun secret à poser ni
   à faire tourner sur les machines.
6. **Un échec de pose échoue bruyamment.** Aucune restauration automatique :
   restaurer `/opt/nivuus-packages/<nom>/` ne défait pas ce que le hook a écrit
   ailleurs (compose relancé, fichiers dans `config/custom_components/`, `apt`
   posé), et rendrait un répertoire cohérent sur une machine qui ne l'est pas.
7. **`nivuus` est la CLI du moteur ; `shell` renomme la sienne en
   `nivuus-shell`.** Un nom, un propriétaire. `nivuus` délègue tout premier mot
   inconnu à `nivuus-<mot>` du `PATH`, sur le patron de `git`.

## Le contrat de release

**Un workflow réutilisable unique**, `nivuus/.github/.github/workflows/release.yml@main`,
appelé par un stub de quelques lignes dans chacun des dix dépôts. Déclencheur :
`push` sur `main`.

**La version est dérivée, jamais saisie.** Le workflow lit les commits
conventionnels depuis le dernier tag :

| Commit                         | Bump      |
|--------------------------------|-----------|
| `feat`                         | mineur    |
| `fix`, `perf`, `refactor`      | correctif |
| `!` ou `BREAKING CHANGE`       | majeur    |
| `docs`, `chore`, `ci`, `test`, `style`, `build`, `revert` | aucun |

**Aucun commit qualifiant ⇒ aucune release.** Un lot purement documentaire ne
publie rien.

**Le tag est l'unique source de vérité.** `main` étant protégée avec
`enforce_admins: true` sur quatre dépôts, le workflow ne peut pas y pousser un
commit de bump — et se battre contre une protection qu'on a voulue serait une
erreur. Donc :

- `nivuus-package.yaml` porte `version: 0.0.0` **dans le dépôt**.
  `manifest.py:186` impose `MAJOR.MINOR.PATCH` strict ; `0.0.0` est valide et
  aucune release ne portera jamais ce numéro, donc il fait un marqueur de dev
  sans ambiguïté et sans toucher au parseur.
- Le workflow **injecte la version réelle dans l'archive publiée**, ainsi que
  dans `package.json`, `pyproject.toml` ou `manifest.json` selon la nature.
- Effet de bord voulu : une copie installée depuis un clone git affiche
  `0.0.0` et se déclare ainsi elle-même « en dev, pas sur une release » — le
  raisonnement exact de `_nivuus_is_dev_checkout` dans `shell`, généralisé.

**Trois assets, toujours les mêmes :**

- `<nom>-<version>.tar.gz`, produit par `git archive HEAD`. C'est le mécanisme
  déjà employé par `iso-build/build.sh`, avec sa garantie explicite : fichiers
  suivis uniquement, jamais l'arbre de travail, donc jamais un `.env` ni un log.
- `SHA256SUMS`.
- Les notes de version, générées depuis les commits.

**Plus, quand la nature l'exige, l'artefact natif** que le hook consommera : le
`.deb` pour `mqtt`, le wheel pour `retro`. L'archive source reste le contrat
commun ; le natif est un supplément, pas un second format.

**Vérification fail-closed** : sommes SHA256 obligatoires — toute impossibilité
de récupérer ou de faire correspondre une somme abandonne la pose, de sorte
qu'une panne réseau ne puisse pas servir à sauter la vérification (le patron de
`shell` est déjà correct sur ce point). Plus
`actions/attest-build-provenance`, gratuite sur dépôt public.

## L'état sur la machine cible

**Un champ nouveau au manifeste, et un seul :**

```yaml
source:
  github: nivuus/home-manager
```

Rien aujourd'hui ne dit d'où vient un package. Sans ce champ, aucune mise à
jour n'est possible, et un package tiers n'aurait aucun moyen de déclarer sa
provenance. **Un package sans `source:` n'est pas mis à jour, et le dit** :
refus nommé, jamais une absence silencieuse — c'est la thèse explicite de
`discovery.py`.

**`/etc/nivuus/packages.json` s'étend sans casser.** Il porte aujourd'hui
`{"<nom>": {"version": …, "answers": {…}, "facts": {…}}}`. Il gagne :

- `state` : `installed` | `failed`
- `target_version` : la version visée quand `state` vaut `failed`
- `updated_at` : horodatage ISO 8601 de la dernière pose réussie

Un fichier écrit par l'installeur actuel ne porte aucune de ces clés et se lit
`installed` : **pas de migration**.

**Tous les chemins sont surchargeables par variable d'environnement**, sur
l'idiome déjà posé par `NIVUUS_PACKAGES_DIR` et `NIVUUS_PROGRESS_DIR` :
`NIVUUS_STATE_FILE` (aujourd'hui codé en dur dans `activate_cli.py`),
`NIVUUS_STAMP_DIR`, `NIVUUS_CACHE_DIR`. C'est ce qui rend un essai de bout en
bout possible sur une machine de production sans y toucher.

## L'updater

`installer/packages/updater.py`, livré là où `activate_cli.py` vit déjà
(`/opt/nivuus/installer/packages/`). Il réutilise `discovery.py`,
`manifest.py`, `runner.py`, `dependencies.py` et `facts.py` tels quels : le
parseur, le protocole jsonl, le tri topologique et la fusion des facts sont
déjà écrits, déjà éprouvés, et tournent déjà sur la cible à chaque boot.

**La découverte** interroge `https://api.github.com/repos/<source.github>/releases/latest`
pour chaque package installé. Neuf requêtes anonymes contre une limite de 60/h
par IP : un timer quotidien passe très large.

**La pose reprend le contrat que le moteur s'est déjà donné — décider sans
écrire, puis écrire :**

1. Télécharger l'archive et `SHA256SUMS` sous `NIVUUS_CACHE_DIR`, vérifier.
   Échec ⇒ abandon **avant toute écriture**.
2. Extraire, puis charger le nouveau manifeste **avec le vrai parseur**. Un
   manifeste cassé refuse la mise à jour avant tout remplacement.
3. Vérifier `requires.packages` : une nouvelle version peut avoir gagné une
   dépendance. Quand plusieurs packages montent ensemble, poser dans l'ordre de
   `install_order()` — le tri topologique existe, et son cas d'école
   (`home-desk` avant `home-manager`) est déjà documenté dans
   `dependencies.py`.
4. Remplacer `/opt/nivuus-packages/<nom>/`.
5. Rejouer `run_install(manifest, hw, answers, root="/")` avec **les réponses
   déjà enregistrées** dans l'état : il n'y a personne à qui demander, exactement
   comme `activate_cli.py` les relit au premier boot. Les hooks écrivent tous
   en `os.path.join(root, "etc/…")`, donc `root="/"` fonctionne tel quel.
6. Effacer l'estampille `<nom>.activated` et rejouer `run_activate` dans la
   foulée : l'updater tourne sur un système en ligne, attendre le prochain boot
   n'aurait pas de sens.
7. Écrire la nouvelle version dans l'état, **uniquement en cas de succès**.
   Échec ⇒ `state: failed`, `target_version`, et le journal ; l'updater ne
   retente pas seul.

**Un verrou `flock`** sur `NIVUUS_STAMP_DIR/.lock`. Il y aura trois
déclencheurs — timer, CLI, Home Assistant — donc deux poses simultanées sont
une question de temps, pas une hypothèse.

## Les surfaces

### La CLI

`nivuus`, livrée par `installer` dans `/usr/local/sbin/` :

```
nivuus list                 # packages installés, version posée, version disponible
nivuus check                # rafraîchit l'état sans rien poser
nivuus update [nom]         # pose ; sans argument, tous ceux en retard
nivuus status [nom]         # détail, y compris le journal d'un échec
```

**`shell` renomme `bin/nivuus` en `bin/nivuus-shell`.** Son analyse d'arguments
est déjà `install|uninstall|update|doctor|help` en premier mot : le renommage
est un `git mv` plus la documentation. Coût : un changement d'interface
publique, donc un `feat!:` et un bump majeur — ce que la machinerie de release
décrite plus haut sait faire, et c'est le moment le moins cher de le faire.

**`nivuus` délègue tout premier mot inconnu à `nivuus-<mot>` du `PATH`**, sur
le patron de `git`. Donc `nivuus shell doctor` fonctionne sans que le moteur
connaisse `shell`, et aucun nom n'a deux propriétaires. Sur une machine sans
moteur, `nivuus-shell` reste utilisable seul.

**L'auto-updater zsh de `shell` se retire** quand `shell` figure dans l'état du
moteur : `nivuus-shell update` renvoie alors vers `nivuus update shell`. Deux
updaters ne doivent jamais écrire dans le même répertoire.

### Home Assistant

**Par l'agent MQTT, pas par une nouvelle intégration.** Home Assistant tourne
dans un conteneur posé par `home-manager` : un `custom_component` ne peut pas
exécuter une pose root sur l'hôte, et lui ouvrir une API ajouterait un service,
un port et une frontière de privilège. L'agent `mqtt` est déjà sur l'hôte, en
root, et publier des entités HA est son métier.

Le contrat entre les deux est mince et sans réseau :

- `nivuus` écrit son état — versions posées, versions disponibles, échecs et
  leur cible — en JSON sous `NIVUUS_STAMP_DIR`.
- L'agent le lit et publie une entité par package sur
  `homeassistant/update/<device>/<nom>/config` : version installée, version
  disponible, notes de release. Le composant `update` de la découverte MQTT
  couvre déjà ces champs.
- Le bouton **Installer** revient par le `command_topic` de l'entité ; l'agent
  appelle `nivuus update <nom>` et republie l'état. Un package en `failed`
  reste visible en erreur avec son journal.

**Dégradation propre** : sans agent `mqtt`, pas d'entités, et la CLI fonctionne
à l'identique. La surface HA est un supplément, jamais un prérequis.

### Le timer

`nivuus-check.timer`, quotidien, `Persistent=true`. Il **vérifie et rafraîchit
l'état seulement** ; il ne pose rien. La décision reste à l'opérateur, par la
CLI ou par le bouton.

### Le cas `installer`, et `console`

`installer` n'est pas un package du moteur : il ne peut pas s'installer
lui-même, et rien sous `/opt/nivuus/` n'est découvert par `discovery.py`. Mais
il publie une release de son payload comme les autres, et `nivuus` sait la
poser :

```
nivuus update --self
```

La pose est une décompression de l'archive par-dessus `/opt/nivuus/installer/`,
sans hook : ce répertoire n'a ni service ni état à préserver — c'est du code
que `activate_cli.py` et l'updater lisent, rien de plus. `install.sh` de
`shell` prouve depuis longtemps que ce mécanisme suffit. La version posée est
enregistrée sous une clé réservée `_installer` de l'état, hors de l'espace des
noms de packages.

Deux garde-fous : la mise à jour de soi est **toujours séparée** d'une pose de
packages (jamais dans la même invocation, pour qu'un updater à moitié remplacé
ne pose rien), et elle refuse de s'exécuter si `/opt/nivuus/.git` existe — même
protection que `_nivuus_is_dev_checkout` dans `shell`.

**`console` voyage avec `installer`.** Il vit dans ce dépôt et n'a donc pas de
release propre. Son manifeste porte
`source: {github: nivuus/installer, path: console}` : l'updater extrait ce
sous-répertoire de l'archive d'`installer` et le traite ensuite comme n'importe
quel package — remplacement, `install`, `activate`. Le champ `path` est
optionnel et vaut la racine par défaut ; c'est le seul package de la suite qui
l'utilise, et il évite d'inventer un dixième dépôt pour un répertoire.

## Les neuf packages

| Package | Nature | Hook `install` | Asset natif |
|---|---|---|---|
| `desk` | plateforme WebRTC | existe | — |
| `home-desk` | bundle JS + composant HA | existe | — |
| `home-manager` | socle HA + compose | existe | — |
| `home-stock` | intégration HACS | existe | — |
| `media-manager` | compose + timers | existe | — |
| `marketplace` | intégration HACS | **à écrire** | — |
| `mqtt` | agent Node | **à écrire** | `.deb` |
| `retro` | projet Python | **à écrire** | wheel |
| `shell` | dotfiles zsh | **à écrire** | — |

- **`marketplace`** est le cas trivial : `home-stock` est déjà un package du
  moteur *et* une intégration HACS, avec un `hooks/install.py` qui dépose dans
  `config/custom_components/`. On copie ce patron, avec
  `requires.packages: [home-manager]`.
- **`mqtt`** produit déjà son `.deb` (`debian/`, `npm run package:deb`). Son
  hook le pose depuis l'asset de release.
- **`shell`** appelle son propre `nivuus-shell install --yes` depuis son hook.
- **`retro`** se pose dans `/opt/retro` — le répertoire que `console` cherche
  déjà (voir § Couplages).

## Les couplages à défaire

**`mqtt` a aujourd'hui un autre propriétaire.**
`install-engine/steps/features.py::_home_assistant_mqtt` cherche un `.deb` dans
`{cible}/opt/nivuus/mqtt/` et fait un `dpkg -i … check=False` — l'échec est
avalé. Faire de `mqtt` un package du moteur créerait deux propriétaires du même
artefact : **cette feature se vide**. Le chemin parallèle `BUILD_MQTT_DEB` de
`build.sh` disparaît avec elle.

**L'ISO doit consommer les releases, pas le clone local.** Avec le versionnage
par tag, la copie embarquée par `git archive HEAD` porterait `version: 0.0.0` —
donc toute machine fraîchement installée se croirait immédiatement en retard
sur les neuf packages. `build.sh` récupère à la place les archives des
dernières releases : l'ISO devient reproductible, et la machine naît à jour.

**`retro` répare une dette connue.** Le point (5) de `installer/CLAUDE.md`
constate que `retro: true` *ne peut pas réussir* : `fetch_payload.py` résout
`RETRO_SRC` vers `/opt/retro` une fois le package `console` copié, et rien ne
crée ce répertoire. Un package `retro` qui se pose lui-même comble le trou. Mais
`console` garde la case à cocher et son `retro_state_path`, et `requires` est
statique — voir § Points ouverts.

## Prérequis

**Balayage avant ouverture.** `gitleaks` sur l'**historique complet** de `desk`,
`home-desk` et `home-stock` — la configuration existe déjà
(`installer/.gitleaks.toml`). Ouvrir un dépôt expose tout son passé, pas son
état. Purge (`git filter-repo`) avant bascule si quelque chose sort.
Vérifié : `desk/.env` est ignoré et n'a jamais été suivi.

**Correction du dépôt mort.** `shell/config/20-autoupdate.zsh` porte encore
`NIVUUS_GITHUB_REPO=maximeallanic/nivuus-shell`. À corriger ou à supprimer avec
le retrait de l'auto-updater.

## Tests

**La preuve d'idempotence est la contrepartie de la décision 3.** Le workflow
réutilisable exécute, pour chaque package : monter une racine factice, jouer
`install` **deux fois**, comparer arborescence et contenu octet à octet. Une
deuxième passe différente de la première rend la CI rouge et **bloque la
release**. Le harnais obtient le vrai moteur en clonant `nivuus/installer` —
c'est déjà littéralement ce que fait le `Makefile` de `home-manager` via
`NIVUUS_INSTALLER_DIR`.

C'est un contrôle **capable d'échouer** : il compare un résultat mesuré, pas
une intention déclarée.

**Essai de bout en bout sur la machine de développement.** Elle n'a pas été
installée par le moteur, donc l'état que l'updater attend n'existe pas — il
faut le fabriquer, et c'est précisément ce que les variables d'environnement
de la § État permettent de faire sans toucher à la production :

1. `NIVUUS_PACKAGES_DIR`, `NIVUUS_STATE_FILE`, `NIVUUS_STAMP_DIR` et
   `NIVUUS_CACHE_DIR` pointés vers une racine d'essai.
2. Y poser un package à une version antérieure à sa dernière release.
3. `nivuus check` doit voir le retard ; `nivuus update <nom>` doit poser, et
   l'état doit porter la nouvelle version.
4. Rejouer `nivuus update <nom>` : aucun changement, aucune erreur.
5. Provoquer un échec (hook qui sort en erreur) et vérifier que l'état porte
   `failed` et `target_version`, et que l'updater ne retente pas seul.

**Tests unitaires** dans `scripts/tests/`, sur le style existant du dépôt :
dérivation de version depuis une liste de commits, refus d'une somme SHA256
fausse, refus d'un manifeste cassé avant remplacement, ordre de pose
multi-packages, extension rétro-compatible de l'état.

## Points ouverts, nommés

1. **`retro` et `console`.** `requires` est statique ; `console` n'active le
   rétrogaming que si la réponse `retro` vaut `true`. On ne peut donc pas
   déclarer `requires.packages: [retro]` sur `console` sans imposer `retro` à
   tout le monde. Trois pistes : `console` cesse de construire les wheels et
   se contente de lire `/opt/retro` s'il existe ; ou le wizard sélectionne le
   package `retro` quand la case est cochée ; ou `requires` gagne une forme
   conditionnelle. **À trancher après lecture complète de
   `console/hooks/install.py` et `console/guest/fetch_payload.py`.**
2. **Le compte utilisateur de `shell`.** Il s'installe dans `~/.nivuus-shell`
   alors que les hooks tournent en root. Son `wizard.yaml` doit poser la
   question, ou reprendre l'utilisateur créé par l'installeur.
3. **Le premier tag.** Les dépôts n'ont pas d'historique de tags ; la première
   exécution du workflow doit produire un `v0.1.0` ou un `v1.0.0` explicite
   plutôt que de dériver depuis la racine de l'historique.

## Ordre des lots

1. **Le workflow de release partagé + la preuve d'idempotence.** Rien ne peut
   être publié avant que la CI puisse refuser une régression.
2. **Le champ `source:`, l'extension de l'état, les variables
   d'environnement.** Le socle que l'updater consomme.
3. **`updater.py` et la CLI `nivuus`**, avec le renommage `nivuus-shell`,
   `nivuus update --self` et le `path` de `console`.
4. **Les quatre migrations** (`marketplace`, `mqtt`, `retro`, `shell`) et les
   couplages défaits.
5. **La surface MQTT / Home Assistant.**
6. **L'ISO qui consomme les releases, et l'ouverture des trois dépôts privés.**

Si le plan d'implémentation déborde, c'est là qu'on découpera.
