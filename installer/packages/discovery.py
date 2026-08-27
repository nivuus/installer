"""Finding package manifests, and deciding which ones may be offered.

Discovery is fail-SOFT: one broken third-party manifest must not make the
whole wizard unusable. Broken manifests are returned alongside the valid ones
so the portal can say "this package is broken, here is why" - an installer
that refuses to start because a stranger shipped bad YAML would be worse than
useless on a machine with no screen.

Eligibility is the opposite: strict, and it always explains itself. A package
that is silently absent from the wizard is indistinguishable from one that was
never installed, so an ineligible package travels with the reason it is
ineligible.
"""
from __future__ import annotations

import os

from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest

# Overridable so tests and dev runs never touch the real payload directory,
# exactly like NIVUUS_PROGRESS_DIR does for the progress protocol.
PACKAGES_DIR = os.environ.get("NIVUUS_PACKAGES_DIR", "/opt/nivuus-packages")


def discover(root: str = PACKAGES_DIR) -> tuple[list[Manifest], list[tuple[str, str]]]:
    """Load every manifest under `root`.

    Returns (valid manifests sorted by name, [(source, error message)]).
    `source` is NOT always a path: for a manifest that failed to parse it is
    that manifest's path, but for a name collision (see below) it is the
    colliding package name, because a collision has no single path to point
    at - it involves several, and those are listed in the message itself.
    A directory with no manifest is not a package and is skipped in silence;
    a directory WITH a manifest that does not parse is an error worth showing.
    A `nivuus-package.yaml` that is a DIRECTORY rather than a file is reported
    as an error too, not skipped: an entry named exactly like the manifest is
    an attempt at being a package, and "present but broken is always worth
    reporting" is the whole thesis of this module - silently treating it as
    "not a package" is the one failure mode discovery is meant to avoid.

    `name` becomes a systemd unit instance name and a key in the installed
    target's package-state file, so two manifests declaring the same name is
    load-bearing, not cosmetic - they would overwrite each other's state and
    each other's activation unit at first boot. When that happens, EVERY
    manifest sharing the name is excluded from the valid list, not just the
    losers of some directory-sort order: silently installing whichever one
    happened to sort first would be worse than installing none. The operator
    gets one error naming the collision and every colliding path.
    """
    manifests: list[Manifest] = []
    errors: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return [], []

    for entry in entries:
        source = os.path.join(root, entry, MANIFEST_NAME)
        if os.path.isdir(source):
            errors.append((source, f"{MANIFEST_NAME} est un répertoire, "
                                   "pas un fichier"))
            continue
        if not os.path.isfile(source):
            continue
        try:
            manifests.append(load_manifest(source))
        except ManifestError as exc:
            errors.append((source, str(exc)))

    by_name: dict[str, list[Manifest]] = {}
    for manifest in manifests:
        by_name.setdefault(manifest.name, []).append(manifest)

    valid: list[Manifest] = []
    for name, group in sorted(by_name.items()):
        if len(group) > 1:
            paths = ", ".join(os.path.join(m.root, MANIFEST_NAME) for m in group)
            errors.append((
                name,  # source: the collision has no single path, only a name
                f"deux packages ou plus déclarent le nom {name!r} : {paths} "
                "— aucun n'est proposé, renommez-en un",
            ))
        else:
            valid.extend(group)

    valid.sort(key=lambda m: m.name)
    return valid, errors


def eligibility(manifest: Manifest, capabilities: set[str],
                 features: set[str]) -> str:
    """"" when the package may be offered, otherwise the reason it may not."""
    missing_caps = [c for c in manifest.capabilities if c not in capabilities]
    if missing_caps:
        return ("matériel requis absent : " + ", ".join(sorted(missing_caps)))
    missing_features = [f for f in manifest.features if f not in features]
    if missing_features:
        return ("fonctionnalité requise non sélectionnée : "
                 + ", ".join(sorted(missing_features)))
    return ""


def partition(manifests, capabilities: set[str], features: set[str]):
    """Split manifests into (eligible, [(manifest, reason)])."""
    eligible: list[Manifest] = []
    rejected: list[tuple[Manifest, str]] = []
    for manifest in manifests:
        reason = eligibility(manifest, capabilities, features)
        (rejected.append((manifest, reason)) if reason
         else eligible.append(manifest))
    return eligible, rejected
