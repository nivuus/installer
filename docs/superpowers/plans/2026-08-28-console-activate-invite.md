# `activate` construit l'invité (phase 2d) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `activate` récupère le payload, construit l'ISO sans surveillance, définit le domaine, démarre la VM une fois, puis rend la main — chaque étape sautable sur constat, et l'issue observable après coup.

**Architecture:** la chaîne existe déjà et est éprouvée (`fetch_payload.py` → `build.py` → `domain.py define`). Ce plan écrit **qui l'appelle**, dans quel ordre, ce qui permet de sauter une étape déjà faite, et comment un échec se distingue d'une installation encore en cours. Le témoin d'activation n'étant écrit qu'en cas de succès, toute défaillance rejoue au démarrage suivant : sans idempotence, chaque reprise coûte une heure.

**Tech Stack:** Python 3.11 (stdlib seule), systemd, libvirt.

**Spec:** `docs/superpowers/specs/2026-08-28-console-activate-invite-design.md`

## Global Constraints

- **`console/` n'importe RIEN de `installer/`.** Le package doit tourner sur une Debian qui n'a jamais vu cet installateur. Un littéral dupliqué est préférable à un import.
- **INTERDIT : lancer la chaîne réelle sur cette machine.** Elle construirait une ISO de 993 Mo et **démarrerait la VM de production**, ce qui détache le GPU de l'hôte, arrête ollama, `nvidia-persistenced` et les nœuds Tdarr. Aucune tâche de ce plan n'a le droit d'appeler `build.py`, `fetch_payload.py`, `virsh define`, `virsh start` ou `domain.py define` pour de vrai. Les tests pilotent les **décisions** avec les sous-processus simulés. Si une tâche semble exiger la chaîne réelle, elle est mal conçue : signale-le.
- **Les secrets ne passent jamais par `argv`.** `build.py` lit la clé produit dans un fichier 0600 précisément parce qu'un argument fuirait dans `ps` et l'historique. Tout fichier de secret écrit par ce plan est en **0600**, et un test le vérifie.
- **En cas de doute, reconstruire.** Reconstruire l'ISO à tort coûte vingt minutes ; ne pas la reconstruire à tort livre une console qui ne correspond pas aux réponses données.
- **Commentaires de code en anglais.** Messages destinés à l'opérateur et messages de commit en **français sans accents**.
- **`command grep`, jamais `grep` nu.** La fonction `grep` du profil zsh de cet hôte ne préfixe pas les correspondances récursives par `./` : tout filtre `grep -v '^./…'` laisse passer silencieusement, et tout `wc -l` sur son résultat ment sans erreur. Sept comptages faux de ce projet viennent de là.
- **La suite complète doit rester verte** : `cd installer && make test-packages PYTHON="$PY"`, où `$PY` a `pydantic` et `jinja2`. Le `python3` système ne les a pas et **l'agrégateur s'arrête au premier échec** — un `python3` nu affiche 8 suites vertes et masque les 23 autres. Poser une fois : `PY=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-installer/bfd96116-6ef4-4bd1-8191-ca092cfaf289/scratchpad/venv/bin/python`. **31 suites au départ.**
- Toute suite ajoutée entre dans `console/Makefile` (les suites du package) et non dans `installer/Makefile`, qui délègue.

## Structure des fichiers

| Fichier | Responsabilité | Sort |
| --- | --- | --- |
| `console/hardware.py` | Détection matérielle du package | **Étendu** — `block_device_size_bytes()` |
| `console/wizard.yaml` | Questions posées à l'opérateur | **Étendu** — clé produit, mot de passe Apollo, répertoire de sortie |
| `console/guest_steps.py` | Les cinq étapes et leurs prédicats de saut, sans effet de bord | **Créé** |
| `console/hooks/activate.py` | Orchestration, classification des échecs | **Étendu** |
| `console/host/guest-ready-watch.py` | Surveille 5985, distingue les deux échecs | **Créé** |
| `console/host/systemd/nivuus-guest-ready.{service,timer}` | Déclenche la surveillance | **Créés** |
| `console/hooks/install.py` | Placement | **Étendu** — le script et les deux unités |
| `console/tests/test_console_guest_steps.py` | Prédicats, empreinte, dérivation | **Créé** |
| `console/tests/test_console_guest_ready.py` | Classification des deux échecs | **Créé** |

---

### Task 1 : la taille du disque dédié, lue et non supposée

**Files:**
- Modify: `console/hardware.py`
- Test: `console/tests/test_console_hardware.py`

**Interfaces:**
- Consomme : rien.
- Produit : `block_device_size_bytes(device: str, sysfs_root: str = "/sys/block") -> int`, qui lève `HardwareError` si le périphérique est introuvable. La tâche 3 en dérive la taille de partition.

**Pourquoi une fonction et pas un appel direct.** `activate` reçoit la réponse `dedicated_nvme` (un chemin comme `/dev/nvme1n1`) et doit connaître la taille du disque **entier** : la passthrough donne le périphérique PCI complet à l'invité. Le noyau expose cela dans `/sys/block/<nom>/size`, en secteurs de **512 octets — toujours 512, quelle que soit la taille de secteur physique du disque**. C'est le piège de cette tâche : un NVMe formaté en secteurs de 4 Ko rapporte quand même sa taille en unités de 512 ici, et multiplier par 4096 donnerait huit fois la vérité.

Le fichier a déjà `_whole_disk_name()` et `_sysfs_block_root()` — sers-t'en plutôt que de réimplémenter, et vérifie leur signature avant d'écrire.

- [ ] **Step 1: écrire le test qui échoue**

Ajouter à `console/tests/test_console_hardware.py` (qui importe le module sous le nom `hardware` et définit `check(label, got, want)` — **trois** arguments) :

```python
# /sys/block/<name>/size is ALWAYS in 512-byte sectors, whatever the disk's
# physical sector size. A 4 KiB-formatted NVMe still reports here in units of
# 512; multiplying by the physical size would claim eight times the truth.
with tempfile.TemporaryDirectory() as tmp:
    fake = pathlib.Path(tmp) / "nvme1n1"
    fake.mkdir()
    (fake / "size").write_text("1953525168\n")      # 931.5 GiB in 512B sectors
    got = hardware.block_device_size_bytes("/dev/nvme1n1", sysfs_root=tmp)
    check("la taille vient des secteurs de 512 octets", got, 1953525168 * 512)

    # A partition, not the whole disk: the passthrough hands over the entire
    # PCI device, so sizing must resolve to the parent.
    part = fake / "nvme1n1p1"
    part.mkdir()
    (part / "size").write_text("2048\n")
    check("une partition remonte au disque entier",
          hardware.block_device_size_bytes("/dev/nvme1n1p1", sysfs_root=tmp),
          1953525168 * 512)

check_raises("un peripherique inconnu est refuse", hardware.HardwareError,
             lambda: hardware.block_device_size_bytes("/dev/nexistepas",
                                                      sysfs_root="/nowhere"))
```

Vérifie que `check_raises` existe dans ce fichier et quelle signature il a ; adapte si besoin.

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 console/tests/test_console_hardware.py`
Expected: FAIL — la fonction n'existe pas.

- [ ] **Step 3: écrire la fonction**

Dans `console/hardware.py`, après les helpers de bloc existants. Elle résout le nom du disque entier (`_whole_disk_name`), lit `size`, multiplie par 512, et lève `HardwareError` en nommant le chemin absent.

- [ ] **Step 4: relancer, puis mesurer sur le vrai matériel**

Run: `python3 console/tests/test_console_hardware.py`
Expected: PASS

Puis, sur cette machine, compare à une source indépendante :

```bash
python3 -c "
import sys; sys.path.insert(0,'console')
import hardware
print(hardware.block_device_size_bytes('/dev/nvme1n1'))"
lsblk -bdno SIZE /dev/nvme1n1
```

Les deux nombres doivent être **identiques**. Rapporte-les. Si le disque n'existe pas sur cette machine, prends-en un qui existe et dis lequel.

- [ ] **Step 5: agrégateur puis commit**

Run: `cd installer && make test-packages PYTHON="$PY"` → 31 suites, exit 0.

```bash
git add console/hardware.py console/tests/test_console_hardware.py
git commit -m "feat(console): lire la taille du disque dedie plutot que la supposer

Le noyau la donne en secteurs de 512 octets quelle que soit la taille de
secteur physique — s y tromper multiplierait la taille par huit sur un NVMe
formate en 4 Ko."
```

---

### Task 2 : le wizard demande ce qui manque

**Files:**
- Modify: `console/wizard.yaml`
- Test: `console/tests/test_console_resolve.py`

**Interfaces:**
- Consomme : rien.
- Produit : trois clés de réponse — `windows_iso` (texte, requis), `ltsc_key` (secret, requis), `apollo_password` (secret, requis) — plus `guest_workdir` (texte, facultatif). Les tâches 3 et 4 les lisent.

**Le média n'a pas de valeur par défaut, et c'est une décision.** Aucun téléchargement automatique n'existe pour l'édition volume ; le fwlink `linkid=2270353` sert une édition **Evaluation** (90 jours) dont la conversion par clé MAK n'est pas mesurée. Un défaut qui télécharge livrerait une console qui expire — pire qu'un refus clair. La question accepte un chemin local **ou** une URL ; la tâche 3 décide laquelle.

Le vocabulaire autorisé est `bool`, `choix`, `texte`, `secret`, `disque`, `gpu` — n'en invente pas d'autre, le moteur refuse.

- [ ] **Step 1: écrire le test qui échoue**

Dans `console/tests/test_console_resolve.py`, ou dans la suite qui valide déjà le manifeste et le wizard du package — **lis les deux et choisis celle qui charge `wizard.yaml`**. Vérifie :

```python
# Four answers now reach the guest build. The two secrets must be typed as
# such: a `texte` would come back in the portal's payload with its default,
# and land in a log. The engine's own vocabulary check is the other half.
by_key = {q["key"]: q for q in questions}
check("le media Windows est demande", by_key["windows_iso"]["type"], "texte")
check("le media est requis", by_key["windows_iso"].get("required"), True)
check("la cle produit est un secret", by_key["ltsc_key"]["type"], "secret")
check("le mot de passe Apollo est un secret",
      by_key["apollo_password"]["type"], "secret")
check("le repertoire de travail est facultatif",
      by_key["guest_workdir"].get("required", False), False)

# No default on the medium: the only automatic download available serves an
# Evaluation edition whose MAK conversion is unmeasured, and a console that
# installs then expires is worse than one that refuses with a reason.
check("le media n a pas de valeur par defaut",
      by_key["windows_iso"].get("default"), None)
```

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Expected: FAIL — les quatre clés manquent.

- [ ] **Step 3: étendre `console/wizard.yaml`**

```yaml
- key: windows_iso
  type: texte
  label: "Media Windows 11 IoT Enterprise LTSC : chemin local ou URL"
  required: true

- key: ltsc_key
  type: secret
  label: "Clé produit Windows LTSC"
  required: true

- key: apollo_password
  type: secret
  label: "Mot de passe de l'interface Apollo"
  required: true

- key: guest_workdir
  type: texte
  label: "Répertoire de travail pour la construction de l'invité"
  default: "/var/lib/nivuus/guest"
```

- [ ] **Step 4: relancer et vérifier que le moteur accepte le manifeste**

Run: la suite modifiée, puis `cd installer && make test-packages PYTHON="$PY"`
Expected: 31 suites, exit 0. Une erreur de vocabulaire se manifesterait ici.

- [ ] **Step 5: commit**

```bash
git add console/wizard.yaml console/tests/
git commit -m "feat(console): le wizard demande le media, la cle et le mot de passe Apollo

Pas de valeur par defaut pour le media: le seul telechargement automatique
disponible sert une edition Evaluation dont la conversion par cle MAK n est
pas mesuree, et une console qui s installe puis expire est pire qu un refus."
```

---

### Task 3 : les cinq étapes et leurs prédicats de saut

**Files:**
- Create: `console/guest_steps.py`
- Test: `console/tests/test_console_guest_steps.py`

**Interfaces:**
- Consomme : `console.hardware.block_device_size_bytes` (tâche 1), les réponses de la tâche 2.
- Produit, et la tâche 4 comme les tests s'appuient sur ces noms exacts :
  - `plan_steps(answers, hw, workdir) -> list[Step]`, où chaque `Step` porte un
    nom, un prédicat `already_done() -> bool` et une action `run()` ;
  - `data_partition_gib(disk_bytes: int) -> int` ;
  - `build_fingerprint(iso, payload_files, answers, data_gib) -> str` ;
  - `build_is_current(stamp_path: str, expected: str) -> bool`, qui rend
    **faux** sur toute erreur de lecture ou d'analyse ;
  - `GuestBuildError`, l'exception de refus motivé, levée par
    `data_partition_gib` sur un disque trop petit.

**C'est la tâche centrale du plan, et elle ne fait aucun effet de bord.** Les actions sont des commandes à lancer, pas des lancements ; les prédicats sont des constats sur le système de fichiers et sur `virsh`. Cette séparation est ce qui rend l'ensemble testable sans construire d'ISO ni démarrer de VM.

**La dérivation de la partition, et son sens contre-intuitif.** `build.py --data-partition-gb` dimensionne la partition **de jeux** ; **Windows prend ce qui reste** (`autounattend.py` : la partition de données est créée en premier, et `<Extend>` ne s'applique qu'à la dernière créée). La dérivation **soustrait donc ce que Windows exige** — elle ne réserve pas ce que les jeux demandent. Se tromper de sens sur un petit disque produit un `C:` minuscule plutôt qu'un `D:` minuscule : une console qui s'installe puis étouffe au premier correctif Windows. Réserve au moins 120 Gio à Windows, et **refuse** un disque trop petit pour laisser une partition de jeux utile plutôt que d'en produire une absurde.

**L'empreinte de construction.** Comparer des dates de fichiers est faux : le payload est retouché sans que l'ISO ait à être refaite, et l'inverse existe. L'empreinte couvre ce qui entre réellement dans l'image — identité du média source, arborescence du payload, et les réponses qui la façonnent (`retro`, `apollo_user`, la taille de partition, le nom d'hôte). Elle est écrite à côté de l'ISO. **Toute erreur de lecture ou d'analyse de ce fichier vaut « il faut reconstruire »**, jamais « on peut sauter ».

- [ ] **Step 1: écrire le test qui échoue**

Créer `console/tests/test_console_guest_steps.py`. Il doit couvrir, au minimum :

```python
# --- la derivation, dans le bon sens ------------------------------------- #
# The GAMES partition carries the fixed size and Windows takes the rest, so
# the derivation SUBTRACTS what Windows needs. Getting this backwards yields
# a tiny C: on a small disk - a console that installs, then suffocates on its
# first Windows update.
GIB = 1024 ** 3
check("un disque de 1 To laisse la place aux jeux",
      steps.data_partition_gib(1000 * GIB) < 1000 - 120, True)
check("et en garde assez pour Windows",
      1000 - steps.data_partition_gib(1000 * GIB) >= 120, True)
check("un disque de 500 Go donne une partition plus petite",
      steps.data_partition_gib(500 * GIB) < steps.data_partition_gib(1000 * GIB),
      True)
check_raises("un disque trop petit est refuse", steps.GuestBuildError,
             lambda: steps.data_partition_gib(100 * GIB))

# --- l empreinte -------------------------------------------------------- #
# Two builds agree only if what ENTERS the image is the same. Dates are not
# part of it: the payload gets touched without the ISO needing a rebuild.
a = steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                            answers={"retro": False}, data_gib=800)
b = steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                            answers={"retro": True}, data_gib=800)
check("une reponse differente change l empreinte", a == b, False)

# --- un fichier d empreinte illisible veut dire RECONSTRUIRE ------------- #
with tempfile.TemporaryDirectory() as tmp:
    stamp = pathlib.Path(tmp) / "build.fingerprint"
    stamp.write_text("{ceci n est pas du json")
    check("une empreinte illisible ne fait jamais sauter la construction",
          steps.build_is_current(str(stamp), a), False)
```

Ajoute aussi une vérification par étape que `already_done()` est **faux** quand rien n'existe et **vrai** quand l'artefact est là, avec `virsh` simulé pour les deux dernières.

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Expected: FAIL — le module n'existe pas.

- [ ] **Step 3: écrire `console/guest_steps.py`**

Cinq étapes, chacune avec son prédicat :

| Étape | `already_done()` |
| --- | --- |
| `secrets` | Les trois fichiers existent, en 0600, et leur contenu correspond aux réponses |
| `payload` | Délégué à `fetch_payload.py`, déjà « offline-first » — le prédicat vérifie seulement que le répertoire existe et n'est pas vide ; l'étape est bon marché et sûre à rejouer |
| `build` | L'ISO existe **et** `build_is_current()` dit vrai |
| `define` | `virsh dumpxml <domaine>` répond |
| `start` | `virsh domstate <domaine>` dit autre chose que `shut off` |

Le module **construit** les lignes de commande sans les lancer. `virsh` est appelé via une fonction injectable pour que les tests la remplacent.

- [ ] **Step 4: relancer**

Expected: PASS. Puis brancher la suite dans `console/Makefile` (32 suites) et lancer l'agrégateur — **mesure le compte**.

- [ ] **Step 5: prouver que le test peut échouer**

Inverse le sens de la dérivation (réserve la taille des jeux au lieu de celle de Windows), vérifie que le test échoue **en se nommant**, restaure, vérifie `git diff` propre.

- [ ] **Step 6: commit**

```bash
git add console/guest_steps.py console/tests/test_console_guest_steps.py console/Makefile
git commit -m "feat(console): cinq etapes, chacune sautable sur constat

Le temoin d activation n est ecrit qu en cas de succes, donc toute defaillance
rejoue au demarrage suivant: sans predicats, chaque reprise coute une heure.
Aucune etape n a d effet de bord ici — le module construit des commandes, il
ne les lance pas, ce qui rend l ensemble testable sans construire d ISO."
```

---

### Task 3b : le domaine d'installation attache les deux médias

**Files:**
- Modify: `console/guest/domain.py`
- Modify: `console/guest/templates/domain.xml.j2`
- Modify: `console/guest_steps.py`
- Test: `console/tests/test_console_guest_steps.py`, `console/tests/test_windows_guest_domain.py`

**Interfaces:**
- Consomme : `plan_steps` et les étapes `define`/`start` de la tâche 3.
- Produit : un `domain.py` capable d'émettre le domaine **avec** les médias d'installation. La tâche 5 le redéfinira **sans** eux une fois l'invité prêt.

**Cette tâche existe parce que le plan était faux, et la mesure le dit.** Le gabarit de production `templates/domain.xml.j2` ne contient **aucun** `cdrom`, et l'unique `<boot order='1'/>` porte sur le NVMe — vérifié aussi sur le domaine `Windows` réellement défini sur l'hôte de référence. `define` puis `start`, tels que la tâche 3 les construit, démarreraient donc un disque vierge : Windows Setup ne se lancerait jamais.

**Et le piège est plus profond qu'il n'y paraît : il faut DEUX médias, pas un.** `templates/domain-test.xml.j2` — le seul gabarit du dépôt qui attache quoi que ce soit — en monte deux :

```xml
<disk type='file' device='cdrom'>          <!-- le media Windows officiel -->
  <source file='{{ windows_iso }}'/>
  <target dev='sdb' bus='sata'/>
  <readonly/>
  <boot order='1'/>                        <!-- c est LUI qui amorce -->
</disk>
<disk type='file' device='cdrom'>          <!-- l ISO construite par build.py -->
  <source file='{{ unattend_iso }}'/>
  <target dev='sdc' bus='sata'/>
  <readonly/>                              <!-- pas d ordre: elle n amorce pas -->
</disk>
```

L'ISO que `build.py` produit **n'est pas amorçable** : c'est le média de réponses et de charge utile, que Windows Setup lit une fois démarré depuis le média officiel. La docstring de `build.py` le dit — « The Windows medium is only read: it is never rebuilt ». Ne fabrique donc pas un domaine à un seul lecteur.

**Ce que la tâche ne fait pas** : elle ne retire pas les médias après l'installation. C'est la tâche 5 qui le fera, quand elle saura que l'invité est prêt.

- [ ] **Step 1: mesurer l'écart avant de le combler**

```bash
command grep -c cdrom console/guest/templates/domain.xml.j2        # attendu: 0
command grep -c cdrom console/guest/templates/domain-test.xml.j2   # attendu: >0
command grep -n "boot order" console/guest/templates/domain.xml.j2
```

Rapporte les trois sorties. C'est la référence contre laquelle la correction se juge.

- [ ] **Step 2: écrire les tests qui échouent**

Dans `console/tests/test_windows_guest_domain.py`, vérifie que le domaine émis **en mode installation** monte les deux médias, que seul le média Windows porte un ordre de démarrage, et que le mode **normal** n'en monte aucun :

```python
# Two media, not one. build.py's output is the ANSWER medium - Windows Setup
# reads it once booted from the official ISO, which is why only that one
# carries a boot order. A single-drive install domain boots into a blank NVMe.
xml_install = domain.domain_xml(..., windows_iso="/m/w.iso",
                                unattend_iso="/v/nivuus-unattend.iso")
check("le media Windows est monte", "/m/w.iso" in xml_install, True)
check("l ISO de reponses est montee",
      "/v/nivuus-unattend.iso" in xml_install, True)
check("seul le media Windows amorce", xml_install.count("<boot order="), 1)

xml_steady = domain.domain_xml(...)            # sans media
check("le domaine de regime ne monte aucun cdrom",
      "cdrom" in xml_steady, False)
```

Adapte à la signature réelle de `domain_xml()` — **lis-la**, elle prend des arguments nommés explicites.

Dans `console/tests/test_console_guest_steps.py`, vérifie que l'étape `define` passe bien les deux chemins.

- [ ] **Step 3: implémenter**

`domain.xml.j2` accepte les deux médias, conditionnellement. `domain.py` gagne le moyen de les fournir. `guest_steps.py` les passe. Reprends la forme exacte de `domain-test.xml.j2` — bus, cibles, `readonly`, ordre — plutôt que d'en inventer une : elle a servi à des installations réelles.

- [ ] **Step 4: le garde-fou de dérive de l'empreinte**

`package_inputs()` de `console/guest_steps.py` est une **liste explicite**. Un nouveau module de `console/guest/` entrant dans l'image sans y être ajouté échapperait à l'empreinte, **silencieusement** — et une mise à jour du paquet réutiliserait alors une ISO périmée. C'est la même classe de dérive qu'une table de placement incomplète, et ce dépôt l'a déjà payée une fois.

Ajoute une vérification exigeant que **chaque** `.py` directement sous `console/guest/` figure soit dans la liste des entrées, soit dans une liste d'exclusions **explicite et commentée**. Le message d'échec doit **nommer** le fichier orphelin.

- [ ] **Step 5: prouver que tout cela peut échouer**

Trois épreuves, chacune restaurée : retirer le média de réponses du domaine d'installation ; donner un ordre de démarrage à l'ISO de réponses ; ajouter un `.py` bidon sous `console/guest/` sans le déclarer. Chacune doit produire un échec **nommé**.

- [ ] **Step 6: agrégateur et commit**

Run: `cd installer && make test-packages PYTHON="$PY"` → 32 suites, exit 0, 0 `FAIL`, 0 `✗`.

```bash
git add console/guest/domain.py console/guest/templates/domain.xml.j2 \
        console/guest_steps.py console/tests/
git commit -m "feat(console): le domaine d installation attache les deux medias

Le gabarit de production n avait aucun cdrom et amorcait sur le NVMe: define
puis start auraient demarre un disque vierge. Et il en faut DEUX — l ISO que
build.py produit n est pas amorcable, c est le media de reponses que Windows
Setup lit une fois demarre depuis le media officiel."
```

---

### Task 4 : `activate` orchestre, et classe ses échecs

**Files:**
- Modify: `console/hooks/activate.py`
- Test: `console/tests/test_console_activate.py`

**Interfaces:**
- Consomme : `console.guest_steps.plan_steps` (tâche 3).
- Produit : un `activate` qui arme les unités (comportement existant, à conserver) **puis** exécute les étapes. Aucune tâche ultérieure n'en dépend.

**Ce que la tâche ne doit pas casser.** `activate.py` arme aujourd'hui trois unités par lien symbolique, de façon idempotente, refuse un lien pendant, et ne touche au systemd de la machine que lorsque `--root` vaut `/`. Tout cela reste, et ses tests aussi.

**La classification des échecs est le vrai livrable.** Un démarrage de VM déclenche les hooks GPU, qui détachent la carte de l'hôte et arrêtent ollama, `nvidia-persistenced` et Tdarr. Sur une machine fraîchement installée, ces hooks n'ont jamais tourné. **Un hook qui refuse le démarrage est un comportement voulu**, pas une panne de construction : `activate` doit le rapporter comme tel, avec ce que dit le journal du hook, et non comme « la construction a échoué ». Trois classes à distinguer dans les messages émis :

1. **refus motivé** — une entrée manque ou ne convient pas (média introuvable, disque trop petit, secret vide) ;
2. **refus d'un hook au démarrage** — tout est construit, la VM n'a pas démarré, et la cause est ailleurs ;
3. **panne** — une étape a échoué pour une raison que le hook ne sait pas nommer.

- [ ] **Step 1: écrire le test qui échoue**

Étendre `console/tests/test_console_activate.py` — il pilote déjà le hook réel contre une racine jetable. Ajoute, avec les étapes simulées :

```python
# Arming still works, and still comes FIRST: if the build fails, the wake
# sockets must already be armed - an operator who fixes the medium by hand
# then reboots should not also have to re-arm anything.
# (les verifications de liens existantes restent inchangees)

# A hook refusing the VM start is DESIGNED behaviour, not a build failure.
# Reporting it as one would send the operator hunting through build logs for
# a problem that lives in the GPU handover.
check("un refus de hook au demarrage est rapporte comme tel",
      "hook" in message_for(_start_refused_run()).lower(), True)
check("et n est pas presente comme un echec de construction",
      "construction" in message_for(_start_refused_run()).lower(), False)
```

Adapte les noms des helpers à ce que le fichier utilise réellement ; **lis-le avant d'écrire**.

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

- [ ] **Step 3: étendre le hook**

L'armement reste **en premier** : un échec de construction ne doit pas laisser les sockets de réveil désarmées. Puis les étapes, chacune sautée si son prédicat le dit, chacune émettant sa progression.

- [ ] **Step 4: relancer, et vérifier que l'armement n'a pas régressé**

Run: `python3 console/tests/test_console_activate.py` puis l'agrégateur complet.
Expected: 32 suites, exit 0.

- [ ] **Step 5: prouver la classification**

Simule un refus de hook au démarrage et vérifie que le message le nomme ; simule une panne de construction et vérifie qu'il ne parle pas de hook. Rapporte les deux messages **verbatim**.

- [ ] **Step 6: commit**

```bash
git add console/hooks/activate.py console/tests/test_console_activate.py
git commit -m "feat(console): activate construit l invite, et sait pourquoi il echoue

L armement passe en premier: un echec de construction ne doit pas laisser
les sockets de reveil desarmees. Un hook qui refuse le demarrage est un
comportement voulu, pas une panne de construction — le confondre enverrait
l operateur chercher dans les journaux de build un probleme qui vit dans la
bascule GPU."
```

---

### Task 5 : l'issue devient observable

**Files:**
- Create: `console/host/guest-ready-watch.py`
- Create: `console/host/systemd/nivuus-guest-ready.service`, `.timer`
- Modify: `console/hooks/install.py`, `console/hooks/activate.py`
- Test: `console/tests/test_console_guest_ready.py`

**Interfaces:**
- Consomme : les unités posées par `install.py`, armées par `activate.py` — les deux mécanismes existent déjà, réutilise-les plutôt que d'en inventer.
- Produit : rien pour une tâche ultérieure.

**Pourquoi 5985 et pas un témoin inventé.** `console/guest/provision/99-marker.ps1` le dit : « the host treats a reachable 5985 as "the guest is provisioned" », et l'ordre des quatorze étapes est délibérément tel que **tout le reste est vrai avant que ce port s'ouvre**. Le signal existe donc déjà ; en fabriquer un second créerait deux vérités.

**Les deux échecs à séparer**, parce que rien ne les distingue aujourd'hui et qu'ils appellent des gestes opposés :

* le domaine n'est pas `running` — la VM n'a jamais démarré, regarder les hooks ;
* le domaine est `running` et 5985 reste fermé au-delà d'un délai raisonnable — l'invité s'installe ou a échoué, et **lui ne peut rien dire tant que WinRM est fermé**, donc seul le journal de l'hôte peut le rendre lisible.

- [ ] **Step 1: écrire le test qui échoue**

Créer `console/tests/test_console_guest_ready.py`, avec `virsh` et la sonde réseau simulés :

```python
check("domaine eteint : la VM n a jamais demarre",
      watch.classify(domstate="shut off", port_open=False, elapsed_s=60),
      watch.NOT_STARTED)
check("domaine actif, port ferme, tot : installation en cours",
      watch.classify(domstate="running", port_open=False, elapsed_s=60),
      watch.INSTALLING)
check("domaine actif, port ferme, trop longtemps : echec",
      watch.classify(domstate="running", port_open=False, elapsed_s=4 * 3600),
      watch.FAILED)
check("port ouvert : provisionne",
      watch.classify(domstate="running", port_open=True, elapsed_s=600),
      watch.READY)
# Le port ouvert prime sur tout le reste : un invite joignable est provisionne,
# meme si l horloge dit qu il a mis trop longtemps.
check("un port ouvert tardif reste un succes",
      watch.classify(domstate="running", port_open=True, elapsed_s=9 * 3600),
      watch.READY)
```

Le seuil doit être une constante nommée, pas un nombre en clair, et le test doit s'y référer plutôt que de le recopier.

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

- [ ] **Step 3: écrire le script, les unités, et les poser**

Le script lit l'IP de l'invité comme le fait déjà `handle-vm-start.sh` — **va voir comment**, ne réinvente pas. Le minuteur cesse de sonder une fois l'état terminal atteint.

`install.py` place le script sous `usr/local/sbin/` et les deux unités sous `etc/systemd/system/` — ajoute-les aux tables existantes plutôt que d'écrire de nouveaux appels. `activate.py` arme le minuteur avec les trois autres.

- [ ] **Step 4: vérifier les placements et l'armement**

Les suites `test_console_install` et `test_console_activate` doivent être étendues pour couvrir le nouveau script et les deux unités — **elles vérifient déjà l'identité octet pour octet des fichiers posés et l'absence de liens créés par `install`** ; suis ces conventions.

Run: agrégateur complet → 33 suites, exit 0. **Mesure.**

- [ ] **Step 5: commit**

```bash
git add console/host console/hooks console/tests console/Makefile
git commit -m "feat(console): savoir si l invite est pret, et sinon lequel des deux echecs

activate rend la main avant la fin, donc « en cours d installation » et
« a echoue » seraient indiscernables pendant une heure puis pour toujours.
Le signal n est pas invente: 99-marker.ps1 ordonne les quatorze etapes pour
que tout soit vrai avant que 5985 s ouvre."
```

---

### Task 6 : la documentation dit ce que la console sait faire

**Files:**
- Modify: `console/README.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`

**Interfaces:** aucune. Documentaire, dernière volontairement.

**Aucune phrase ne doit être écrite sans que la commande correspondante ait été lancée.** Sept affirmations de ce projet ont déjà été corrigées par mesure, dont plusieurs venaient du planificateur.

- [ ] **Step 1: mesurer**

```bash
cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'; cd ..
bash -c "grep -rn 'from common\|import common' console/ | grep -v __pycache__"
python3 - <<'PY'
import json, os, subprocess, sys, tempfile
# ce que install pose reellement, depuis une racine jetable
CTX = json.dumps({"package": {"name": "console", "version": "1.0.0",
                              "root": "console"},
                  "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
                  "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": False,
                              "admin_password": "x", "ltsc_key": "y",
                              "apollo_password": "z",
                              "windows_iso": "/m/w.iso"}})
with tempfile.TemporaryDirectory() as root:
    p = subprocess.run([sys.executable, "hooks/install.py", "--phase",
                        "install", "--root", root],
                       input=CTX, capture_output=True, text=True, cwd="console")
    print("rc =", p.returncode, (p.stderr or "").strip()[:120])
    for base, _d, files in sorted(os.walk(root)):
        for n in sorted(files):
            print("  " + os.path.relpath(os.path.join(base, n), root))
PY
```

- [ ] **Step 2: `console/README.md`**

La table des phases dit que `activate` « ne construit pas l'invité (phase 2d) » — c'est fait. Décris ce qu'il fait, dans l'ordre, et **ce qu'il ne fait pas** : il rend la main avant la fin de l'installation Windows, et c'est le minuteur qui dit l'issue. Ajoute les quatre nouvelles questions du wizard. **Ne touche pas** à « Limites connues », qui reste vraie.

- [ ] **Step 3: `CLAUDE.md`**

Le paragraphe qui dit que la console « n'est pas fonctionnelle depuis une installation seule » devient faux — remplace-le, en gardant ce qui reste vrai (les constantes propres à la machine de référence, chantier séparé). Mets à jour le compte de suites, mesuré.

- [ ] **Step 4: le spec parent**

`2026-08-27-…-design.md` décrit la phase 2 comme un tout. Consigne que son côté invité est terminé et renvoie au spec de cette phase. Ne réécris pas l'histoire : les phases 2a à 2d sont un découpage postérieur, dis-le.

- [ ] **Step 5: relancer et commiter**

Run: agrégateur → le compte mesuré au Step 1, exit 0.

```bash
git add console/README.md CLAUDE.md docs/superpowers/specs/
git commit -m "docs: la console sait desormais construire son invite

Elle rend la main avant la fin de l installation Windows, et c est le
minuteur de disponibilite qui en dit l issue — le README doit annoncer les
deux, sans quoi une console qui s installe ressemble a une console en panne."
```
