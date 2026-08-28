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
      "cochez-le" in manque[0].message(), True)

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
