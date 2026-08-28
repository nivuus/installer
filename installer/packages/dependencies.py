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
    placed: set[str] = set()
    ready = sorted(name for name, need in deps.items() if not need)
    while ready:
        name = ready.pop(0)
        ordered.append(by_name[name])
        placed.add(name)
        freed = []
        for other, need in deps.items():
            if name in need:
                need.discard(name)
                if not need and other not in placed:
                    freed.append(other)
        del deps[name]
        ready = sorted(set(ready) | set(freed))

    if deps:
        cycle = ", ".join(sorted(deps))
        raise DependencyError(
            f"cycle de dépendances entre packages : {cycle} - aucun ordre "
            "d'installation n'est possible")
    return ordered
