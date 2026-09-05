#!/usr/bin/env python3
"""Tests de l'ordre d'installation rendu par plan_packages().

test_packages_dependencies garantit que install_order() trie juste. Ce test
garantit que plan_packages() l'APPELLE - un module correct mais jamais branché
laisserait le bug intact, et c'est le genre d'oubli qu'aucun test unitaire ne
voit.

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
        check("l'erreur dit de le cocher", "cochez-le" in str(exc), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - plan order tests passed")
