# Dépendances entre packages (`requires.packages`) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner au contrat `nivuus.dev/v1` une notion de dépendance entre packages, pour qu'un satellite ne puisse jamais s'installer avant le socle dont il dépend.

**Architecture:** Un champ déclaratif `requires.packages` dans le manifeste, un module `dependencies.py` calqué sur `conflicts.py` (validation + tri topologique de Kahn), et un branchement dans `plan_packages()` **avant** `check_conflicts()` et avant tout hook `resolve` — donc avant que `partition()` ait touché le disque. L'échec arrive au wizard, jamais sur un disque déjà effacé.

**Tech Stack:** Python 3.11 (stdlib seule + PyYAML), tests en scripts autonomes `python3 scripts/tests/<nom>.py` (ni pytest, ni dépendance supplémentaire).

**Spec:** `~/Projects/Nivuus/packages/home-manager/docs/superpowers/specs/2026-08-28-package-nivuus-home-manager-design.md`, section « Extension du contrat : `requires.packages` ».

## Global Constraints

- Le contrat reste **`nivuus.dev/v1`** : ajouter un champ facultatif est rétrocompatible, la version ne change pas.
- **Aucun manifeste existant ne doit cesser de valider.** `console` et `media-manager` ne déclarent que `requires.capabilities` et `requires.features` ; les deux doivent continuer à passer après le durcissement.
- **`Manifest` est un dataclass gelé et hashable** : tout nouveau champ est un `tuple`, jamais une `list` ni un `dict`.
- Messages d'erreur destinés à l'opérateur : **en français**, et ils **nomment les packages concernés** (un opérateur lit l'erreur sur un téléphone, sans accès au log).
- Ordre déterministe partout : un même ensemble de packages doit toujours produire le même ordre d'installation.
- Répertoire de travail : `~/Projects/Nivuus/packages/installer`.

---

### Task 1 : `requires.packages` dans le manifeste

Le champ, sa validation, et le durcissement des clés inconnues sous `requires:` qui empêche la faute de frappe au singulier de passer en silence.

**Files:**
- Modify: `installer/packages/manifest.py`
- Test: `scripts/tests/test_packages_manifest.py`

**Interfaces:**
- Consumes: rien (première tâche).
- Produces: `Manifest.packages: tuple[str, ...]` — les noms des packages pré-requis, dans l'ordre de déclaration, dédupliqués par `_str_list`. Vide quand `requires.packages` est absent. Consommé par les tâches 2 et 3.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `scripts/tests/test_packages_manifest.py`, avant le bloc final `if failures:` :

```python
# --- requires.packages : dépendances entre packages ---------------------
# Un satellite (tablettes, stocks) doit s'installer APRÈS son socle. Sans ce
# champ, plan_packages() ordonne alphabétiquement et `home-desk` passe avant
# `home-manager`.
dep = parse_manifest({**MINIMAL, "name": "home-desk",
                      "requires": {"packages": ["home-manager"]}},
                     "/pkg/home-desk")
check("requires.packages est lu", dep.packages, ("home-manager",))

check("absence de requires.packages donne un tuple vide",
      parse_manifest(dict(MINIMAL), "/pkg/demo").packages, ())

check("requires.packages cohabite avec capabilities et features",
      parse_manifest({**MINIMAL, "requires": {
          "packages": ["home-manager"], "capabilities": ["iommu"],
          "features": ["networking"]}}, "/pkg/demo").packages,
      ("home-manager",))

# Un nom de package devient un répertoire et un nom d'instance systemd : un
# nom qui ne peut désigner aucun package réel est une erreur, pas une
# dépendance qu'on cherchera en vain au moment de l'installation.
check_raises("nom de dépendance invalide refusé",
             lambda: parse_manifest({**MINIMAL, "requires": {
                 "packages": ["Home Manager"]}}, "/pkg/demo"),
             "Home Manager")

# Un package qui dépend de lui-même n'a pas d'ordre d'installation possible.
# Le tri topologique le verrait comme un cycle ; le dire ici donne un message
# bien plus clair que « cycle de dépendances : demo → demo ».
check_raises("auto-dépendance refusée",
             lambda: parse_manifest({**MINIMAL, "requires": {
                 "packages": ["demo"]}}, "/pkg/demo"),
             "lui-même")

# LE cas qui justifie le durcissement : `package` au singulier est la faute de
# frappe évidente. Silencieusement ignorée, elle ferait installer un satellite
# avant son socle sans que rien ne le signale — précisément le mode d'échec que
# ce module dit vouloir rendre impossible.
check_raises("clé inconnue sous requires refusée",
             lambda: parse_manifest({**MINIMAL, "requires": {
                 "package": ["home-manager"]}}, "/pkg/demo"),
             "package")

# Non-régression : les deux clés historiques restent acceptées telles quelles.
legacy = parse_manifest({**MINIMAL, "requires": {
    "capabilities": ["iommu", "gpu-discrete"],
    "features": ["networking"]}}, "/pkg/demo")
check("capabilities toujours lues", legacy.capabilities,
      ("iommu", "gpu-discrete"))
check("features toujours lues", legacy.features, ("networking",))
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python3 scripts/tests/test_packages_manifest.py`
Expected: FAIL — plusieurs lignes, dont `requires.packages est lu: got ..., want ('home-manager',)` (le dataclass n'a pas encore d'attribut `packages`, l'erreur peut être un `AttributeError` non capturé : c'est un échec valide à ce stade).

- [ ] **Step 3: Ajouter le champ au dataclass**

Dans `installer/packages/manifest.py`, dans `@dataclass(frozen=True) class Manifest`, après `features`:

```python
    features: tuple[str, ...] = ()
    # Noms des packages qui doivent être installés AVANT celui-ci. Le moteur
    # ordonne les installations alphabétiquement par défaut, ce qui met
    # `home-desk` avant `home-manager` : un satellite déposerait son
    # custom_component dans un répertoire pas encore créé.
    packages: tuple[str, ...] = ()
```

- [ ] **Step 4: Déclarer les clés connues de `requires:`**

Dans `installer/packages/manifest.py`, sous la constante `PLATFORM_KEYS`:

```python
# Les seules clés admises sous `requires:`. La liste est CLOSE : une clé
# inconnue est refusée plutôt qu'ignorée, parce qu'un `package:` au singulier
# silencieusement abandonné ferait installer un satellite avant son socle.
REQUIRES_KEYS = ("capabilities", "features", "packages")
```

- [ ] **Step 5: Valider `requires:` dans `parse_manifest`**

Dans `installer/packages/manifest.py`, remplacer le bloc :

```python
    requires = data.get("requires") or {}
    if not isinstance(requires, dict):
        raise ManifestError(f"{what}: 'requires' must be a mapping")
```

par :

```python
    requires = data.get("requires") or {}
    if not isinstance(requires, dict):
        raise ManifestError(f"{what}: 'requires' must be a mapping")
    unknown = [k for k in requires if k not in REQUIRES_KEYS]
    if unknown:
        raise ManifestError(
            f"{what}: unknown key(s) under 'requires': {sorted(unknown)}; "
            f"expected {list(REQUIRES_KEYS)} - a misspelt 'package' would "
            "silently drop a dependency and install this package before the "
            "one it needs")

    required_packages = _str_list(requires, "packages", what)
    for dependency in required_packages:
        if not NAME_RE.match(dependency):
            raise ManifestError(
                f"{what}: required package {dependency!r} must match "
                f"{NAME_RE.pattern} - it is the name of another package")
        if dependency == name:
            raise ManifestError(
                f"{what}: le package {name!r} ne peut pas dépendre de "
                "lui-même")
```

- [ ] **Step 6: Passer le champ au constructeur**

Dans `installer/packages/manifest.py`, dans le `return Manifest(...)`, après `features=...` :

```python
        features=_str_list(requires, "features", what),
        packages=required_packages,
```

- [ ] **Step 7: Lancer les tests pour vérifier qu'ils passent**

Run: `python3 scripts/tests/test_packages_manifest.py`
Expected: PASS — `OK - all manifest tests passed`

- [ ] **Step 8: Vérifier la non-régression sur les manifestes réels**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'installer')
from packages.manifest import load_manifest
import os
for p in ('console/nivuus-package.yaml',
          os.path.expanduser('~/Projects/Nivuus/packages/media-manager/nivuus-package.yaml')):
    m = load_manifest(p)
    print(f'{m.name}: requires.packages={m.packages}')
"
```
Expected: `console: requires.packages=()` et `media-manager: requires.packages=()`, sans exception.

- [ ] **Step 9: Lancer les autres suites du moteur**

Run: `python3 scripts/tests/test_packages_discovery.py && python3 scripts/tests/test_packages_conflicts.py`
Expected: les deux affichent `OK`.

- [ ] **Step 10: Commit**

```bash
git add installer/packages/manifest.py scripts/tests/test_packages_manifest.py
git commit -m "feat(packages): requires.packages dans le contrat nivuus.dev/v1"
```

---

### Task 2 : le module `dependencies.py`

Validation des dépendances et ordre d'installation, isolés du moteur pour être testables seuls — comme `conflicts.py` l'est pour les claims.

**Files:**
- Create: `installer/packages/dependencies.py`
- Test: `scripts/tests/test_packages_dependencies.py`

**Interfaces:**
- Consumes: `Manifest.packages` et `Manifest.name` (Task 1).
- Produces:
  - `class DependencyError(RuntimeError)` — levée par `install_order` sur cycle.
  - `@dataclass(frozen=True) class MissingDependency` avec `package: str`, `requires: str`, `available: bool`, et la méthode `message() -> str`.
  - `missing_dependencies(chosen, catalog) -> list[MissingDependency]` — `chosen` et `catalog` sont des itérables de `Manifest`. Trié par `(package, requires)`.
  - `install_order(chosen) -> list[Manifest]` — tri topologique stable. Les dépendances hors de `chosen` sont ignorées (c'est `missing_dependencies` qui les signale, pas ce tri).
  - Consommés par la Task 3.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_dependencies.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/dependencies.py.

Un satellite doit s'installer APRÈS son socle. Le cas réel qui a motivé ce
module : `home-desk` et `home-stock` dépendent de `home-manager`, et
plan_packages() ordonne par `sorted(selected)` - or `home-desk` trie AVANT
`home-manager`. Sans tri topologique, un satellite déposerait son
custom_component dans un répertoire que le socle n'a pas encore créé.

Run: python3 scripts/tests/test_packages_dependencies.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.dependencies import (  # noqa: E402
    DependencyError, install_order, missing_dependencies,
)
from packages.manifest import API_VERSION, parse_manifest  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def pkg(name, requires=()):
    return parse_manifest({
        "apiVersion": API_VERSION, "name": name, "version": "1.0.0",
        "label": name, "tier": "userspace",
        "requires": {"packages": list(requires)},
    }, f"/pkg/{name}")


socle = pkg("home-manager")
desk = pkg("home-desk", ["home-manager"])
stock = pkg("home-stock", ["home-manager"])
seul = pkg("media-manager")

# --- ordre d'installation ------------------------------------------------
# LE test qui compte : l'ordre correct est l'INVERSE de l'ordre alphabétique.
# Un tri stable qui se contenterait de trier par nom passerait tous les autres
# tests et échouerait sur celui-ci.
order = [m.name for m in install_order([desk, socle])]
check("le socle passe avant le satellite, malgré l'alphabet",
      order, ["home-manager", "home-desk"])

check("l'ordre est déterministe entre pairs indépendants",
      [m.name for m in install_order([stock, desk, socle])],
      ["home-manager", "home-desk", "home-stock"])

check("l'ordre ne dépend pas de l'ordre d'entrée",
      [m.name for m in install_order([socle, stock, desk])],
      ["home-manager", "home-desk", "home-stock"])

check("un package sans dépendance est rendu tel quel",
      [m.name for m in install_order([seul])], ["media-manager"])

check("liste vide", install_order([]), [])

# Une dépendance non sélectionnée n'est pas l'affaire du tri : c'est
# missing_dependencies qui la signale. Le tri doit rendre un ordre utilisable
# plutôt que planter, sinon la même erreur serait rapportée deux fois.
check("une dépendance absente de la sélection n'empêche pas le tri",
      [m.name for m in install_order([desk])], ["home-desk"])

# Une chaîne : le transitif doit être respecté.
a = pkg("aaa", ["bbb"])
b = pkg("bbb", ["ccc"])
c = pkg("ccc")
check("chaîne transitive", [m.name for m in install_order([a, b, c])],
      ["ccc", "bbb", "aaa"])


def check_raises(label, fn, needle):
    try:
        fn()
    except DependencyError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected DependencyError, none raised")


# Un cycle n'a pas d'ordre. Le message doit nommer les packages, faute de quoi
# l'opérateur n'a aucun moyen de savoir lesquels démêler.
x = pkg("xxx", ["yyy"])
y = pkg("yyy", ["xxx"])
check_raises("cycle refusé", lambda: install_order([x, y]), "xxx")
check_raises("cycle nomme les deux", lambda: install_order([x, y]), "yyy")

# --- dépendances manquantes ---------------------------------------------
catalog = [socle, desk, stock, seul]

check("rien ne manque quand le socle est sélectionné",
      missing_dependencies([socle, desk], catalog), [])

# Cas courant : le package existe sur le support, l'opérateur a juste oublié
# de le cocher. Le message doit le dire, pas prétendre qu'il est introuvable.
manque = missing_dependencies([desk], catalog)
check("le socle non coché est signalé", len(manque), 1)
check("le demandeur est nommé", manque[0].package, "home-desk")
check("le pré-requis est nommé", manque[0].requires, "home-manager")
check("il est marqué disponible", manque[0].available, True)
check("le message dit qu'il faut le cocher",
      "cocher" in manque[0].message(), True)

# Cas distinct : le package n'est pas sur ce support du tout. Dire « cochez-le »
# enverrait l'opérateur chercher une case qui n'existe pas.
absent = missing_dependencies([desk], [desk])
check("le socle absent du support est signalé", len(absent), 1)
check("il est marqué indisponible", absent[0].available, False)
check("le message dit qu'il est absent",
      "absent" in absent[0].message(), True)

# Plusieurs manques : triés, pour que le portail rende une liste stable.
deux = missing_dependencies([stock, desk], [desk, stock])
check("deux manques rapportés", len(deux), 2)
check("triés par demandeur",
      [m.package for m in deux], ["home-desk", "home-stock"])

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all dependency tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_packages_dependencies.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'packages.dependencies'`

- [ ] **Step 3: Écrire le module**

Créer `installer/packages/dependencies.py` :

```python
"""Dépendances entre packages, et l'ordre d'installation qu'elles imposent.

Un package peut être le socle d'une famille : `home-manager` installe Home
Assistant et ses radios, et les satellites (tablettes, stocks) déposent leur
custom_component dans le répertoire de configuration qu'il a créé. Un
satellite installé avant son socle écrit donc dans un répertoire inexistant.

Ce n'est pas une hypothèse : `plan_packages()` ordonne par `sorted(selected)`,
et `home-desk` trie AVANT `home-manager`. L'ordre alphabétique est exactement
le mauvais.

Deux vérifications distinctes vivent ici, et les confondre serait une erreur
d'ergonomie : un pré-requis ABSENT du support et un pré-requis PRÉSENT mais
non coché appellent des gestes opposés de la part de l'opérateur - graver un
autre support, ou cocher une case qu'il a sous les yeux.
"""
from __future__ import annotations

from dataclasses import dataclass


class DependencyError(RuntimeError):
    """Levée quand les dépendances n'admettent aucun ordre d'installation."""


@dataclass(frozen=True)
class MissingDependency:
    package: str      # celui qui réclame
    requires: str     # celui qui manque
    available: bool   # présent sur le support, mais non sélectionné

    def message(self) -> str:
        if self.available:
            return (f"le package « {self.package} » nécessite "
                    f"« {self.requires} » : cochez-le également")
        return (f"le package « {self.package} » nécessite "
                f"« {self.requires} », absent de ce support")


def missing_dependencies(chosen, catalog) -> list[MissingDependency]:
    """Les dépendances non satisfaites de `chosen`, triées.

    `catalog` est l'ensemble des manifestes découverts sur le support ; il sert
    uniquement à distinguer « non coché » de « introuvable ». Le tri rend une
    liste stable pour le portail, qui l'affiche telle quelle.
    """
    selected = {m.name for m in chosen}
    available = {m.name for m in catalog}
    missing = [
        MissingDependency(package=manifest.name, requires=dependency,
                          available=dependency in available)
        for manifest in chosen
        for dependency in manifest.packages
        if dependency not in selected
    ]
    return sorted(missing, key=lambda m: (m.package, m.requires))


def install_order(chosen) -> list:
    """`chosen` réordonné pour qu'un package suive toujours ses dépendances.

    Kahn, avec une file triée par nom : deux packages qui ne dépendent pas
    l'un de l'autre sortent toujours dans le même ordre, quel que soit l'ordre
    d'entrée. Un installateur qui produirait un ordre différent d'un lancement
    à l'autre serait indébogable.

    Les dépendances hors de `chosen` sont IGNORÉES ici. C'est
    `missing_dependencies` qui les signale, et rapporter la même erreur deux
    fois avec deux formulations est pire que la rapporter une fois.
    """
    by_name = {m.name: m for m in chosen}
    # Dépendances internes à la sélection uniquement (cf. docstring).
    deps = {name: {d for d in m.packages if d in by_name}
            for name, m in by_name.items()}

    ordered: list = []
    ready = sorted(name for name, need in deps.items() if not need)
    while ready:
        name = ready.pop(0)
        ordered.append(by_name[name])
        freed = []
        for other, need in deps.items():
            if name in need:
                need.discard(name)
                if not need and other not in {m.name for m in ordered}:
                    freed.append(other)
        del deps[name]
        ready = sorted(set(ready) | set(freed))

    if deps:
        cycle = ", ".join(sorted(deps))
        raise DependencyError(
            f"cycle de dépendances entre packages : {cycle} - aucun ordre "
            "d'installation n'est possible")
    return ordered
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_packages_dependencies.py`
Expected: PASS — `OK - all dependency tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/packages/dependencies.py scripts/tests/test_packages_dependencies.py
git commit -m "feat(packages): tri topologique et validation des dependances"
```

---

### Task 3 : branchement dans le moteur d'installation

Le module devient effectif : refus au wizard, ordre d'installation réel.

**Files:**
- Modify: `installer/install-engine/steps/packages.py` (imports, puis le bloc `chosen = ...` autour de la ligne 158)
- Test: `scripts/tests/test_packages_plan_order.py`

**Interfaces:**
- Consumes: `missing_dependencies`, `install_order`, `DependencyError` (Task 2).
- Produces: `plan_packages()` rend son `plan` dans l'ordre topologique ; `StepError` sur dépendance manquante ou cycle.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_plan_order.py` :

```python
#!/usr/bin/env python3
"""Tests de l'ordre d'installation rendu par plan_packages().

Task 2 garantit que install_order() trie juste. Ce test garantit que
plan_packages() l'APPELLE - un module correct mais jamais branché laisserait
le bug intact, et c'est le genre d'oubli qu'aucun test unitaire ne voit.

Le test travaille sur de vrais manifestes écrits dans un répertoire temporaire
et découverts par NIVUUS_PACKAGES_DIR, donc sans toucher /opt/nivuus-packages.

Run: python3 scripts/tests/test_packages_plan_order.py
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(REPO / "installer" / "install-engine"))

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def write_pkg(root, name, requires=()):
    directory = pathlib.Path(root) / name
    directory.mkdir()
    lines = ["apiVersion: nivuus.dev/v1", f"name: {name}", "version: 1.0.0",
             f"label: {name}", "tier: userspace"]
    if requires:
        lines.append("requires:")
        lines.append("  packages: [" + ", ".join(requires) + "]")
    (directory / "nivuus-package.yaml").write_text("\n".join(lines) + "\n")


class Emit:
    def info(self, *a, **k):
        pass

    def warn(self, *a, **k):
        pass


with tempfile.TemporaryDirectory() as tmp:
    write_pkg(tmp, "home-manager")
    write_pkg(tmp, "home-desk", ["home-manager"])
    os.environ["NIVUUS_PACKAGES_DIR"] = tmp

    # L'import vient APRÈS la variable d'environnement, et c'est structurel :
    # `discover(root=PACKAGES_DIR)` évalue son défaut À LA DÉFINITION du
    # module. Réassigner discovery.PACKAGES_DIR après coup ne changerait donc
    # rien, et le test lirait le vrai /opt/nivuus-packages de la machine.
    from steps.packages import plan_packages  # noqa: E402
    from steps.util import StepError  # noqa: E402

    config = {"packages": {"home-desk": {}, "home-manager": {}},
              "features": [], "disk": {"path": ""}}
    plan, _ = plan_packages(config, {}, Emit())
    check("le socle est planifié avant le satellite",
          [m.name for m, _, _ in plan], ["home-manager", "home-desk"])

    # Le satellite seul : le socle existe sur le support mais n'est pas coché.
    orphan = {"packages": {"home-desk": {}}, "features": [],
              "disk": {"path": ""}}
    try:
        plan_packages(orphan, {}, Emit())
        failures.append("satellite sans socle: expected StepError, none raised")
    except StepError as exc:
        check("l'erreur nomme le pré-requis", "home-manager" in str(exc), True)
        check("l'erreur dit de le cocher", "cocher" in str(exc), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - plan order tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_packages_plan_order.py`
Expected: FAIL — `le socle est planifié avant le satellite: got ['home-desk', 'home-manager'], want ['home-manager', 'home-desk']`. C'est le bug de l'ordre alphabétique, reproduit.

- [ ] **Step 3: Importer le module dans le moteur**

Dans `installer/install-engine/steps/packages.py`, après `from packages.conflicts import check_conflicts` :

```python
from packages.conflicts import check_conflicts
from packages.dependencies import (DependencyError, install_order,
                                   missing_dependencies)
```

- [ ] **Step 4: Brancher la validation et le tri**

Dans `installer/install-engine/steps/packages.py`, remplacer :

```python
    chosen = [by_name[name] for name in sorted(selected)]
```

par :

```python
    chosen = [by_name[name] for name in sorted(selected)]

    # Avant check_conflicts() et avant tout hook resolve, donc avant que
    # partition() ait touché le disque : un pré-requis oublié doit se corriger
    # dans le wizard, pas se découvrir sur une machine déjà effacée.
    missing = missing_dependencies(chosen, manifests)
    if missing:
        raise StepError(" ; ".join(m.message() for m in missing))
    try:
        chosen = install_order(chosen)
    except DependencyError as exc:
        raise StepError(str(exc)) from exc
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_packages_plan_order.py`
Expected: PASS — `OK - plan order tests passed`

- [ ] **Step 6: Vérifier qu'aucune suite du moteur ne régresse**

Run:
```bash
for t in manifest discovery conflicts dependencies plan_order; do
  echo "--- $t"; python3 scripts/tests/test_packages_$t.py || break
done
```
Expected: cinq `OK` consécutifs.

- [ ] **Step 7: Commit**

```bash
git add installer/install-engine/steps/packages.py scripts/tests/test_packages_plan_order.py
git commit -m "feat(install-engine): ordre topologique et refus des dependances manquantes"
```

---

### Task 4 : exposition dans le portail

Le portail affiche la dépendance, pour que l'opérateur coche le socle **avant** de lancer l'installation plutôt que de lire une erreur après coup.

**Files:**
- Modify: `installer/webapp/main.py:79-94` (fonction `describe`)
- Test: `scripts/tests/test_packages_dependencies.py` (complément)

**Interfaces:**
- Consumes: `Manifest.packages` (Task 1).
- Produces: la clé `"requires_packages": list[str]` dans chaque entrée de `eligible` et `ineligible` de la réponse JSON.

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à `scripts/tests/test_packages_dependencies.py`, avant le bloc final `if failures:` :

```python
# --- exposition au portail ----------------------------------------------
# Le portail doit pouvoir écrire « nécessite : home-manager » à côté de la
# case. Pas d'auto-cochage : cocher à la place de l'opérateur sur une machine
# sans écran est un comportement magique, hors périmètre v1.
sys.path.insert(0, str(REPO / "installer" / "webapp"))
import inspect  # noqa: E402
import main as webapp_main  # noqa: E402

source = inspect.getsource(webapp_main)
check("describe() expose requires_packages",
      "requires_packages" in source, True)
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_packages_dependencies.py`
Expected: FAIL — `describe() expose requires_packages: got False, want True`

- [ ] **Step 3: Ajouter la clé au payload**

Dans `installer/webapp/main.py`, dans `describe()` :

```python
        payload = {
            "name": manifest.name, "label": manifest.label,
            "version": manifest.version, "tier": manifest.tier,
            "claims": [r for r, _ in manifest.claims],
            # Les packages à cocher AVANT celui-ci. Le portail les affiche ;
            # il ne les coche pas à la place de l'opérateur.
            "requires_packages": list(manifest.packages),
            "questions": [],
        }
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_packages_dependencies.py`
Expected: PASS — `OK - all dependency tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/webapp/main.py scripts/tests/test_packages_dependencies.py
git commit -m "feat(webapp): le portail annonce les packages pre-requis"
```

---

### Task 5 : documenter le contrat

Le contrat est ce que lisent les auteurs de packages tiers. Un champ non documenté n'existe pas pour eux.

**Files:**
- Modify: `installer/README.md` (section du contrat `nivuus.dev/v1`)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de consommé par du code.

- [ ] **Step 1: Repérer la section du contrat**

Run: `grep -n "requires\|capabilities\|nivuus.dev/v1" installer/README.md | head -20`
Expected: les lignes décrivant le manifeste. Insérer la documentation ci-dessous à leur suite ; s'il n'existe aucune section de ce genre, la créer sous le titre `### requires`.

- [ ] **Step 2: Documenter le champ**

```markdown
#### `requires.packages`

Les packages qui doivent être installés **avant** celui-ci :

```yaml
requires:
  packages: [home-manager]
```

Le moteur ordonne les installations topologiquement — un package suit toujours
ses dépendances, quel que soit l'ordre alphabétique de leurs noms. Un
pré-requis non coché dans le wizard, absent du support, ou un cycle, sont
refusés **avant** le partitionnement du disque.

Les seules clés admises sous `requires:` sont `capabilities`, `features` et
`packages`. Toute autre clé est une erreur de manifeste : un `package:` au
singulier abandonné en silence ferait installer un package avant celui dont il
dépend.
```

- [ ] **Step 3: Ajouter l'entrée au changelog**

Sous la section non publiée de `CHANGELOG.md` :

```markdown
### Added
- Contrat `nivuus.dev/v1` : `requires.packages` déclare les packages
  pré-requis. Les installations sont ordonnées topologiquement, et un
  pré-requis manquant est refusé au wizard plutôt qu'en cours d'installation.

### Changed
- Une clé inconnue sous `requires:` est désormais une erreur de manifeste.
  Aucun manifeste existant n'est concerné.
```

- [ ] **Step 4: Lancer la totalité des suites du moteur**

Run:
```bash
for t in manifest discovery conflicts dependencies plan_order; do
  echo "--- $t"; python3 scripts/tests/test_packages_$t.py || break
done
```
Expected: cinq `OK` consécutifs.

- [ ] **Step 5: Commit**

```bash
git add installer/README.md CHANGELOG.md
git commit -m "docs: requires.packages dans le contrat nivuus.dev/v1"
```

---

## Vérification finale

- [ ] Les cinq suites passent : `manifest`, `discovery`, `conflicts`, `dependencies`, `plan_order`.
- [ ] `console/nivuus-package.yaml` et `media-manager/nivuus-package.yaml` valident toujours (Task 1, Step 8).
- [ ] `install_order([home-desk, home-manager])` rend `[home-manager, home-desk]` — l'inverse de l'alphabet, qui est le bug d'origine.
- [ ] Aucun fichier hors `installer/packages/`, `installer/install-engine/steps/packages.py`, `installer/webapp/main.py`, `scripts/tests/` et la documentation n'a été modifié.
