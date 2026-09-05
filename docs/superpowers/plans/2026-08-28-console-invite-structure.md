# L'invité entre dans le package (phase 2c) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** faire entrer `installer/windows-guest/` dans le package `console`, réparer `domain.py`, et rassembler sous `console/` tout ce qui teste `console/` — sans ajouter un seul comportement nouveau.

**Architecture:** c'est un déplacement, pas une fonctionnalité. La frontière que les phases 2a et 2b ont prouvée côté hôte s'étend à l'invité : après ce plan, `console/` contient tout ce qui lui appartient, n'importe rien de `installer/`, et porte ses propres tests. La phase 3 (`git filter-repo --path console`) devient alors ce que le spec promet — mécanique. `domain.py` est réparé ici parce que sa panne *est* la frontière : il appelle cinq fonctions dont trois ont déjà migré et deux vivent encore du mauvais côté.

**Tech Stack:** Python 3.11 (stdlib seule), `git mv`, Make.

**Spec:** `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`

## Global Constraints

- **`console/` n'importe RIEN de `installer/`.** C'est l'autonomie qui lui permet de tourner sur la cible et sur une Debian qui n'a jamais vu cet installateur. Un littéral dupliqué est préférable à un import. Cette contrainte est la raison d'être du plan entier : chaque tâche doit la laisser vraie.
- **Chaque déplacement se fait par `git mv`** et doit être enregistré par git comme un **renommage**, pas comme une suppression suivie d'un ajout. Vérifier avec `git show --stat -M` ou `git status`. L'historique de `windows-guest/` porte les mesures HDR, les pièges Apollo et quatre specs ; il ne doit pas devenir un commit d'import.
- **Aucun comportement nouveau.** Si une tâche donne envie d'améliorer le code déplacé, c'est le signe qu'elle sort du périmètre. La seule exception est `domain.py`, dont la réparation est explicitement au programme.
- **Commentaires de code en anglais.** Messages destinés à l'opérateur et messages de commit en **français sans accents**.
- **`command grep`, jamais `grep` nu.** Le profil zsh de cet hôte livre une fonction `grep` cassée qui ne préfixe pas les correspondances récursives par `./` : tout filtre `grep -v '^./…'` laisse alors passer silencieusement, et tout `wc -l` sur son résultat ment sans erreur. Cinq comptages faux en sont déjà venus dans ce projet.
- **La suite complète doit rester verte** : `cd installer && make test-packages PYTHON="$PY"`, où `$PY` est un interpréteur disposant de `pydantic` et `jinja2`. Le `python3` système ne les a pas et **l'agrégateur s'arrête au premier échec** — un `python3` nu affiche 8 suites vertes et masque les 22 autres. Poser une fois : `PY=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-installer/bfd96116-6ef4-4bd1-8191-ca092cfaf289/scratchpad/venv/bin/python`. **30 suites au départ de ce plan.**
- **Un fichier au-delà de ~200 lignes est un signal, pas une interdiction** (18 fichiers `.py` suivis le dépassent déjà).

## Ce que cette phase ne fait PAS

`activate` ne construit toujours pas la VM, et les constantes propres à la machine de référence (adresse PCI en dur, interface réseau, chemins `/opt/nivuus`, `winvm` sans son client `winrm`) restent telles quelles — elles sont déjà nommées dans la section « Limites connues » de `console/README.md`. Les deux appartiennent à la phase 2d.

## Structure des fichiers

| Fichier | Responsabilité | Sort |
| --- | --- | --- |
| `console/hardware.py` | Détection matérielle du package | **Étendu** — gagne `list_gpus()` et `cpu_topology()`, que `domain.py` réclame |
| `installer/windows-guest/**` (41 fichiers) | Construction de l'ISO sans surveillance, domaine libvirt, provisionnement | **Déplacé** vers `console/guest/` |
| `console/retro.py` | Chemin du témoin retrogaming, source unique du package | **Créé** |
| `installer/common/retro.py` | Le même, côté installateur | **Supprimé** — son dernier importateur part avec l'invité |
| `console/guest/domain.py` | Domaine de production depuis le matériel détecté | **Réparé** — importe `console/hardware.py` |
| `scripts/tests/test_windows_guest_*.py` (11) | Suites de l'invité | **Déplacées** vers `console/tests/` |
| `scripts/tests/test_console_*.py` (6), `test_retro_marker_bridge.py`, `test_vm_wake_gate.py`, `test_handle_vm_start.sh` | Suites du package hôte | **Déplacées** vers `console/tests/` |
| `console/Makefile` | Cible `test` du package | **Créé** |
| `installer/Makefile` | Agrégateur | **Modifié** — délègue au package au lieu de nommer ses suites |

---

### Task 1 : `console/hardware.py` gagne ce que `domain.py` réclame

**Files:**
- Modify: `console/hardware.py`
- Test: `scripts/tests/test_console_hardware.py`

**Interfaces:**
- Consumes: rien.
- Produces: `list_gpus() -> list[dict]` et `cpu_topology() -> dict` dans `console.hardware`, avec **exactement** les mêmes signatures et les mêmes clés de sortie que leurs jumelles de `installer/common/hardware.py`. La tâche 4 les consomme.

**Pourquoi copier plutôt qu'importer.** `domain.py` tourne après le reboot, éventuellement à la main, sur une machine où `installer/` peut ne jamais avoir existé. C'est la contrainte globale nº 1, et c'est déjà ainsi que `_run`, `_read_int` et `HardwareError` sont arrivés dans ce fichier à la phase 2a.

**Attention à un piège de découpage.** Le moteur détecte les **capacités** (grossier), le package détecte les **détails**. `common/hardware.py::list_gpus()` a été volontairement appauvri à la phase 2a : il ne porte plus `ids`. La copie qui arrive ici doit rendre ce dont `domain.py` a besoin — lis `domain.py` pour savoir quelles clés il consomme réellement (`slot`, `discrete`, …) et vérifie-le dans le code, ne le déduis pas de ce paragraphe.

- [ ] **Step 1: écrire le test qui échoue**

Ajouter à `scripts/tests/test_console_hardware.py`. Ce fichier importe le module sous le nom **`hardware`** (ligne 18, `import hardware`) et définit `check(label, got, want)` — **trois** arguments, ce qui n'est pas le cas partout dans ce dépôt.

```python
# domain.py calls these two directly, and it runs after the reboot on a host
# where installer/ may never have existed. They are copied, not imported:
# console/ importing installer/ is what this whole split exists to prevent.
check("console.hardware expose list_gpus",
      callable(getattr(hardware, "list_gpus", None)), True)
check("console.hardware expose cpu_topology",
      callable(getattr(hardware, "cpu_topology", None)), True)

# Parsing is what can be tested without hardware; detection itself cannot.
# Feed the parsers the same shape lspci and sysfs produce.
gpus = hardware.parse_gpus(SAMPLE_LSPCI_VGA)
check("un GPU discret est reconnu", [g["slot"] for g in gpus if g["discrete"]], ["01:00.0"])
check("l iGPU n est pas discret", [g["discrete"] for g in gpus if g["slot"] == "00:02.0"], [False])
```

L'échantillon `SAMPLE_LSPCI_VGA` doit refléter une sortie réelle : va la chercher avec `lspci -nn | command grep -i 'vga\|3d\|display'` sur cette machine, qui a exactement la configuration visée (un iGPU Intel et une NVIDIA discrète), et recopie deux lignes verbatim.

Si `common/hardware.py::list_gpus()` ne sépare pas déjà l'analyse de la détection, **introduis `parse_gpus(raw)` dans la copie** et fais appeler cette fonction par `list_gpus()`. C'est la seule structure qui rend le comportement testable sans matériel, et c'est déjà le motif du fichier (`parse_pci_functions`, `parse_nvme_controllers`, `resolve_passthrough_nvme`).

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_console_hardware.py`
Expected: FAIL — `console.hardware expose list_gpus` et les suivantes.

- [ ] **Step 3: copier les deux fonctions**

Recopier `list_gpus()` et `cpu_topology()` depuis `installer/common/hardware.py` vers `console/hardware.py`, en scindant la partie analyse comme décrit ci-dessus. Conserver les docstrings ; ajouter une ligne disant que la copie est délibérée et pourquoi.

- [ ] **Step 4: relancer le test**

Run: `python3 scripts/tests/test_console_hardware.py`
Expected: PASS

- [ ] **Step 5: agrégateur**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: 30 suites, exit 0.

- [ ] **Step 6: commit**

```bash
git add console/hardware.py scripts/tests/test_console_hardware.py
git commit -m "feat(console): la detection du package sait enfin lire un GPU et la topologie CPU

domain.py les appelle directement et tourne apres le reboot, sur une machine
ou installer/ peut n avoir jamais existe. Copiees, pas importees."
```

---

### Task 2 : l'invité déménage

**Files:**
- Move: `installer/windows-guest/` → `console/guest/` (41 fichiers, `git mv`)
- Modify: les 11 suites `scripts/tests/test_windows_guest_*.py` (une ligne de chemin chacune)
- Modify: `console/guest/build.py` (le `sys.path` qui atteint `common/`)

**Interfaces:**
- Consumes: rien de la tâche 1.
- Produces: l'arborescence `console/guest/`. Les tâches 3 et 4 y travaillent.

**Ce qui casse au déplacement, et rien d'autre.** Les modules de `windows-guest/` s'ajoutent eux-mêmes au `sys.path` :

```python
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))   # installer/, pour `common`
```

La première ligne survit au déplacement. **La seconde devient fausse** : `os.path.dirname(_HERE)` vaudra `console/` au lieu de `installer/`, donc `from common.retro import retro_state_path` ne résoudra plus. Ne la répare pas ici — la tâche 3 supprime ce pont. Contente-toi de constater l'échec et de le laisser à la tâche suivante **si** il n'empêche pas les autres suites de passer ; s'il les bloque, dis-le et je fusionnerai les deux tâches.

Les 11 suites portent chacune une ligne de la forme `sys.path.insert(0, str(REPO / "installer" / "windows-guest"))`, où `REPO` remonte de deux niveaux depuis `scripts/tests/`. Une seule substitution par fichier.

- [ ] **Step 1: constater l'état de départ**

```bash
cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'
cd .. && find installer/windows-guest -type f -not -path '*__pycache__*' | wc -l
```

Note les deux chiffres : ils doivent être identiques à la fin, aux fichiers près qui changent de place.

- [ ] **Step 2: déplacer**

```bash
git mv installer/windows-guest console/guest
git status --short | head -50
```

Vérifie que git enregistre des **renommages** (`R`), pas des paires suppression/ajout. Si `__pycache__` gêne, supprime-le d'abord (il n'est pas suivi).

- [ ] **Step 3: recâbler les 11 suites**

Dans chacune des 11 `scripts/tests/test_windows_guest_*.py`, remplacer

```python
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
```

par

```python
sys.path.insert(0, str(REPO / "console" / "guest"))
```

Certaines suites ajoutent aussi `str(REPO / "installer")` pour atteindre `common` — laisse ces lignes en place pour l'instant, la tâche 3 s'en occupe.

- [ ] **Step 4: lancer et rendre compte honnêtement**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: 30 suites exécutées. Les suites qui dépendent du pont `common.retro` (`test_windows_guest_build`, `test_retro_marker_bridge`) **peuvent** échouer ici — c'est attendu et c'est le sujet de la tâche 3. Toutes les autres doivent passer. **Rapporte exactement lesquelles échouent** ; ne « répare » pas au-delà du recâblage de chemin.

- [ ] **Step 5: commit**

```bash
git add -A installer/windows-guest console/guest scripts/tests
git commit -m "refactor(console): l invite rejoint le package

git mv, donc l historique suit: windows-guest porte les mesures HDR, les
pieges Apollo et quatre specs, qui ne doivent pas devenir un commit d import."
```

Vérifie après coup : `git show --stat -M HEAD | command grep -c '=>'` doit compter les renommages.

---

### Task 3 : le témoin retrogaming devient une affaire interne au package

**Files:**
- Create: `console/retro.py`
- Modify: `console/guest/build.py`, `console/hooks/install.py`
- Delete: `installer/common/retro.py`
- Modify: `scripts/tests/test_retro_marker_bridge.py`

**Interfaces:**
- Consumes: l'arborescence `console/guest/` de la tâche 2.
- Produces: `console.retro.RETRO_STATE_REL_PATH` et `retro_state_path(root)`. Plus aucun consommateur de `installer/common/retro.py`.

**Le spec dit que ce test doit mourir. Je tranche contre, et voici pourquoi.** Le spec écrit : « Après le découpage, `retro` est une question du wizard de `console`, répondue dans sa propre config : il n'y a plus de pont, donc plus de garde-fou à maintenir. » La prémisse ne s'est pas réalisée. La phase 2a a implémenté `retro` comme un **témoin sur disque** (`/etc/nivuus/retro.json`), précisément parce que `build.py` s'exécute bien plus tard, éventuellement à la main. Le pont existe donc toujours : il est simplement plus court, entièrement à l'intérieur de `console/`. Le mode de panne qu'il empêche — case cochée dans le wizard, rien d'installé sur l'invité, aucun test pour le dire — ne devient pas moins silencieux parce que les deux extrémités partagent un répertoire. **Le garde-fou reste, réécrit à l'échelle du package.**

**Vérifie l'état réel avant d'écrire.** `console/hooks/install.py` porte aujourd'hui le chemin en littéral (`etc/nivuus/retro.json`, écrit à la main), tandis que `build.py` l'importe de `common/retro.py`. Les deux doivent finir par lire `console/retro.py`. Lis les deux fichiers pour savoir ce que chacun fait vraiment.

- [ ] **Step 1: écrire le test qui échoue**

Réécrire `scripts/tests/test_retro_marker_bridge.py` pour qu'il n'importe plus rien de `installer/`. Il doit prouver la même chose qu'avant, à l'échelle du package : le témoin que `console/hooks/install.py` écrit atterrit exactement là où `console/guest/build.py` le cherche.

```python
"""Both ends of the retrogaming marker still agree on one path.

The spec expected this guard to disappear with the split, on the premise
that `retro` would be answered in the package's own config with no file in
between. It is not: phase 2a made it a DURABLE MARKER on disk, because
build.py runs much later - possibly by hand, possibly on this very host
once it has booted. So the bridge still exists; it is merely shorter, and
entirely inside console/. The failure it prevents - box ticked in the
wizard, nothing installed on the guest, no test saying so - is exactly as
silent as before.

It asserts the PATH, by driving the real install hook and reading the
result with build.py's own default, rather than comparing two literals.
"""
```

Le test doit : lancer `console/hooks/install.py --phase install --root <racine jetable>` avec un contexte JSON, puis vérifier que le fichier produit se trouve à `console.retro.retro_state_path(<racine>)`, et que `console.guest.build.DEFAULT_RETRO_MARKER` vaut `console.retro.retro_state_path()`. Reprends la structure de la version actuelle, qui fait déjà exactement cela en passant par `common.retro`.

- [ ] **Step 2: le lancer pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_retro_marker_bridge.py`
Expected: FAIL — `console/retro.py` n'existe pas.

- [ ] **Step 3: créer la source unique et brancher les deux extrémités**

`console/retro.py` reprend le contenu de `installer/common/retro.py` avec une docstring actualisée : les deux appelants sont désormais `console/hooks/install.py` (qui écrit, pendant l'installation, sous la racine cible) et `console/guest/build.py` (qui lit, plus tard, sous `/`).

Puis :
- `console/hooks/install.py` importe `retro_state_path` de `console/retro.py` au lieu de porter le littéral. Attention : ce hook est lancé comme un script avec `cwd=console/`, donc l'import doit fonctionner dans ce contexte — vérifie-le en le lançant, ne le suppose pas.
- `console/guest/build.py` importe de `console/retro.py` au lieu de `common.retro`, et sa ligne `sys.path.insert(0, os.path.dirname(_HERE))` — qui visait `installer/` et vise désormais `console/` — devient correcte pour ce nouvel usage. Confirme-le plutôt que de la supprimer par réflexe.

- [ ] **Step 4: supprimer le module mort**

```bash
git rm installer/common/retro.py
bash -c "grep -rn 'common.retro\|common import retro' --include='*.py' . | grep -v __pycache__"
```

La seconde commande doit ne rien retourner. Si elle retourne quelque chose, ce chemin n'est pas mort : arrête-toi et signale-le.

- [ ] **Step 5: relancer**

Run: `python3 scripts/tests/test_retro_marker_bridge.py` puis `cd installer && make test-packages PYTHON="$PY"`
Expected: le test passe ; 30 suites, exit 0 — y compris `test_windows_guest_build`, qui échouait à la fin de la tâche 2.

- [ ] **Step 6: commit**

```bash
git add -A console scripts/tests/test_retro_marker_bridge.py installer/common
git commit -m "refactor(console): le temoin retrogaming n a plus qu un seul cote

Les deux extremites vivent maintenant dans console/, donc le module partage
de installer/ meurt. Le garde-fou reste: le pont est plus court, pas absent,
et la panne qu il empeche est aussi silencieuse qu avant."
```

---

### Task 4 : `domain.py` remarche

**Files:**
- Modify: `console/guest/domain.py`
- Modify: `scripts/tests/test_windows_guest_production_domain.py`

**Interfaces:**
- Consumes: `console.hardware.list_gpus`, `cpu_topology`, `passthrough_nvme`, `pci_slot_functions`, `HardwareError` (tâche 1 pour les deux premières, déjà présentes pour les trois autres).
- Produces: un `domain.py` dont `main()` s'exécute. Aucune tâche ultérieure n'en dépend.

**La panne, mesurée et non déduite.** `main()` fait `from common.hardware import HardwareError` à la ligne 263, **avant** son bloc `try`, donc l'ImportError est le mode de panne réel, à l'entrée. Plus profond, `build_domain_xml()` appelle `hardware.list_gpus()`, `hardware.cpu_topology()`, `hardware.passthrough_nvme()` et `hardware.pci_slot_functions()`. Trois de ces quatre ont migré vers `console/hardware.py` à la phase 2a ; les deux premières y arrivent par la tâche 1. Les imports sont **paresseux, à l'intérieur des fonctions**, ce qui est précisément pourquoi importer le module ne casse pas et pourquoi aucune suite ne voyait le problème.

**Un test ment aujourd'hui, et il faut qu'il meure avec la panne.** `scripts/tests/test_windows_guest_production_domain.py` contient une assertion qui vérifie que `main()` **lève** `ImportError` — un marqueur rouge délibéré, posé à la phase 2a pour que la panne ne soit pas oubliée. Il doit disparaître **dans le même changement** que la réparation, remplacé par une vérification que `main()` fonctionne. Cherche-le : `check_raises` avec `ImportError`.

- [ ] **Step 1: mesurer la panne avant de la réparer**

```bash
cd console/guest && python3 -c "
import sys; sys.path.insert(0, '.')
import domain
try:
    domain.main()
except Exception as e:
    print(type(e).__name__, e)
"
```

Rapporte la sortie exacte. C'est la référence contre laquelle la réparation se juge.

- [ ] **Step 2: écrire le test qui échoue**

Dans `scripts/tests/test_windows_guest_production_domain.py`, **supprimer** l'assertion `check_raises(..., ImportError, ...)` et la remplacer par une vérification que `main()` atteint la détection matérielle. Sur une machine sans le matériel voulu, `main()` doit échouer avec `HardwareError` — un refus motivé — et non avec `ImportError` ou `AttributeError`, qui sont des pannes de câblage :

```python
# main() must now FAIL FOR HARDWARE REASONS, never for wiring ones. An
# ImportError or AttributeError here means the split broke a call path;
# a HardwareError means the code works and this machine simply does not
# match. Phase 2a pinned the broken state with a deliberate red marker
# asserting ImportError - it goes away in the same change as the repair,
# which is the whole point of having written it that way.
check_raises("main() ne casse plus au cablage",
             (SystemExit, hardware.HardwareError), _domain_main_xml)
```

**Le helper de ce fichier doit être étendu, et le piège est précis.** `check_raises(label, exc_type, fn)` fait `except exc_type`, ce qui accepte parfaitement un tuple — mais sa branche d'échec fait `exc_type.__name__`, attribut qu'un tuple n'a pas. Lui passer un tuple marche donc tant que le test **passe**, et lève un `AttributeError` au moment précis où il devrait rapporter un échec lisible. Corrige le formatage du message avant de passer un tuple, ou n'en passe pas.

Note aussi que l'assertion à retirer ne vise pas `domain.main` directement : elle passe par une petite enveloppe locale (`_domain_main_xml`, autour de la ligne 245) qui appelle `main()` pour la sous-commande `xml`. Lis-la avant de la remplacer.

`ch` n'existe pas dans ce fichier : importe explicitement le module `hardware` de `console/` pour atteindre `HardwareError`, et vérifie que le `sys.path` du test le permet.

- [ ] **Step 3: réparer**

Remplacer dans `console/guest/domain.py` tout import de `common.hardware` par `hardware` (le module `console/hardware.py`), en cohérence avec le `sys.path` que le fichier installe déjà. Vérifie que les quatre appels de `build_domain_xml()` trouvent leur fonction, et que `HardwareError` est bien celle de `console/hardware.py`.

- [ ] **Step 4: relancer, et prouver que le chemin est vivant**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: PASS

Puis rejoue la commande du Step 1 : la sortie doit avoir changé, et le nom d'exception ne doit plus être `ImportError`. Rapporte les deux sorties côte à côte.

Enfin, sur cette machine — qui a le matériel visé — essaie la vraie génération :

```bash
cd console/guest && python3 domain.py xml | head -20
```

Si elle produit du XML, dis-le : ce serait la première fois depuis la phase 2a. Si elle refuse, rapporte le refus **motivé** ; un refus clair est un succès, une trace de câblage ne l'est pas.

- [ ] **Step 5: agrégateur**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: 30 suites, exit 0.

- [ ] **Step 6: commit**

```bash
git add console/guest/domain.py scripts/tests/test_windows_guest_production_domain.py
git commit -m "fix(console): domain.py rebranche ses cinq fonctions, et le marqueur rouge tombe

Il cassait a l entree de main() depuis la phase 2a, sur un import que
personne n executait: les imports paresseux faisaient qu importer le module
reussissait. Le test qui epinglait la panne disparait avec elle."
```

---

### Task 5 : `console/` porte ses propres tests

**Files:**
- Move: les 20 suites qui testent `console/`, de `scripts/tests/` vers `console/tests/`
- Create: `console/Makefile`
- Modify: `installer/Makefile`

**Interfaces:**
- Consumes: tout l'état des tâches 1 à 4.
- Produces: une cible `test` dans `console/`, à laquelle l'agrégateur de `installer/` délègue.

**Pourquoi maintenant plutôt qu'en phase 3.** La phase 3 est un `git filter-repo --path console` que le spec qualifie de mécanique. Elle ne l'est que si tout ce qui appartient au package est **déjà** sous `console/`. Des tests restés dans `scripts/tests/` obligeraient à les déplacer pendant la chirurgie git, ce qui est exactement ce que le spec veut éviter.

**Quelles suites bougent.** La règle est simple et il faut l'appliquer, pas la deviner : **toute suite qui teste du code vivant sous `console/`**. Établis la liste toi-même en cherchant les suites qui référencent `console/` ou `console/guest/`, et rapporte-la ; à la date d'écriture de ce plan elle compte les 11 `test_windows_guest_*`, les six `test_console_*`, `test_retro_marker_bridge.py`, `test_vm_wake_gate.py` et `test_handle_vm_start.sh` — mais **vérifie**, les tâches précédentes ont pu en changer le compte.

`test_handle_vm_start.sh` est un script shell, pas Python : la cible du `Makefile` doit savoir l'exécuter aussi. Regarde comment l'agrégateur actuel s'y prend, ou s'il ne le lance simplement pas.

**Attention aux chemins relatifs.** Chaque suite calcule sa racine par `pathlib.Path(__file__).resolve().parents[N]`. En passant de `scripts/tests/` à `console/tests/`, **le nombre de niveaux change**. Une suite qui remonte de deux niveaux depuis `scripts/tests/` atteint la racine du dépôt ; depuis `console/tests/`, deux niveaux la dépassent d'un cran. C'est la source d'erreur la plus probable de toute cette tâche : vérifie chaque fichier, et ne te fie pas au fait que le test passe — un chemin faux peut rendre un test vert en le faisant porter sur rien.

- [ ] **Step 1: dresser la liste et la rapporter**

```bash
bash -c "grep -rln 'console' scripts/tests/ | sort"
cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'
```

Rapporte la liste et le compte de départ avant de bouger quoi que ce soit.

- [ ] **Step 2: déplacer**

```bash
mkdir -p console/tests
git mv scripts/tests/<chaque-suite> console/tests/
git show --stat -M HEAD 2>/dev/null | head -3   # apres le commit, pour verifier les renommages
```

- [ ] **Step 3: corriger les racines relatives**

Pour chaque suite déplacée, ajuster le calcul de `REPO` (ou équivalent). **Prouve que la correction est réelle** : dans une suite au hasard, affiche la racine calculée et vérifie qu'elle désigne bien la racine du dépôt.

```bash
cd console/tests && python3 -c "
import pathlib
print(pathlib.Path('test_console_install.py').resolve().parents[2])
"
```

- [ ] **Step 4: créer `console/Makefile`**

Une cible `test` qui exécute toutes les suites de `console/tests/`, Python et shell, avec le même contrat que l'agrégateur actuel : un `--- <nom>` avant chaque suite, un exit non nul dès la première qui échoue. Reprends la forme de la cible `test-packages` d'`installer/Makefile` plutôt que d'en inventer une autre, et garde `PYTHON ?= python3` pour que `$PY` puisse être passé.

- [ ] **Step 5: l'agrégateur délègue**

Dans `installer/Makefile`, la cible `test-packages` ne nomme plus les suites du package : elle garde les siennes et appelle `$(MAKE) -C ../console test PYTHON=$(PYTHON)`. Mets à jour le commentaire de compte, **avec le chiffre mesuré**.

- [ ] **Step 6: vérifier que rien n'a disparu**

Run: `cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'`
Expected: le même compte qu'au Step 1. **Un compte qui baisse signifie qu'une suite a cessé d'être exécutée** — c'est le mode de panne exact que ce projet a déjà connu (11 suites jamais branchées sur aucun agrégateur). Si le chiffre diffère, trouve laquelle manque avant de continuer.

- [ ] **Step 7: commit**

```bash
git add -A console scripts/tests installer/Makefile
git commit -m "refactor(console): le package porte ses propres tests

La phase 3 est un git filter-repo --path console: elle n est mecanique que
si tout ce qui appartient au package est deja dessous. L agregateur de
installer delegue desormais au lieu de nommer les suites du package."
```

---

### Task 6 : la documentation décrit la frontière réelle

**Files:**
- Modify: `console/README.md`, `CLAUDE.md`, `installer/README.md`
- Modify: `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`

**Interfaces:** aucune. Tâche documentaire, dernière volontairement.

**La règle de cette tâche est la même qu'aux phases précédentes, et elle n'est pas décorative : aucune phrase ne doit être écrite sans que la commande correspondante ait été lancée.** Six affirmations de ce projet ont déjà été corrigées par mesure, dont cinq du planificateur lui-même.

- [ ] **Step 1: mesurer**

```bash
cd installer && make test-packages PYTHON="$PY" 2>&1 | command grep -c '^--- test_'; cd ..
find console -type f -not -path '*__pycache__*' | wc -l
bash -c "grep -rn 'windows-guest' --include='*.py' --include='*.md' --include='*.sh' . | grep -v __pycache__ | grep -v docs/superpowers | grep -v '^\./\.superpowers'"
bash -c "grep -rn 'from common\|import common' console/ | grep -v __pycache__"
```

La dernière commande doit ne rien retourner : c'est la preuve mesurée de la contrainte globale nº 1. Rapporte-la.

- [ ] **Step 2: `console/README.md`**

Les trois mentions de `installer/windows-guest/build.py` désignent un chemin qui n'existe plus. La table des phases dit que `activate` « ne construit pas l'invité (phase 2c) » — c'est désormais la phase 2d. Ajouter une ligne à la structure du package pour `guest/` et `tests/`. Ne pas toucher à la section « Limites connues » : elle reste vraie.

- [ ] **Step 3: `CLAUDE.md`**

Trois passages deviennent faux : le paragraphe **« `domain.py` is DEAD until phase 2b »**, qui décrit une panne réparée et un marqueur rouge supprimé ; les chemins `installer/windows-guest/…` de la section *Development Commands* ; et le compte de suites. Le paragraphe sur `domain.py` doit être **remplacé, pas supprimé** : ce qu'il faut garder est la leçon, à savoir qu'un import paresseux à l'intérieur d'une fonction fait qu'importer un module réussit alors que l'appeler échoue, et qu'aucune suite ne le voit — c'est ce qui a permis à la panne de vivre une phase entière.

- [ ] **Step 4: `installer/README.md`**

Vérifie s'il mentionne `windows-guest/` et corrige le cas échéant. Ne réécris pas ce qui est encore vrai.

- [ ] **Step 5: le spec**

Le spec annonçait qu'un test mourrait au découpage (`test_retro_marker_bridge.py`). Il vit toujours, et c'est une décision prise en connaissance de cause à la tâche 3. Consigne-la dans le spec, avec sa raison : la prémisse — `retro` répondu dans la config, sans fichier intermédiaire — ne s'est pas réalisée, la phase 2a en a fait un témoin durable sur disque, donc le pont existe encore, plus court. Un spec qui reste faux est pire qu'un spec corrigé.

- [ ] **Step 6: relancer et commiter**

Run: `cd installer && make test-packages PYTHON="$PY"`
Expected: le compte mesuré au Step 1, exit 0.

```bash
git add console/README.md CLAUDE.md installer/README.md docs/superpowers/specs/
git commit -m "docs: la frontiere console/installer est desormais celle du disque

domain.py n est plus mort, windows-guest n est plus dans installer, et le
spec consigne pourquoi le test qu il condamnait a survecu."
```
