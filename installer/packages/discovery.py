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

    Returns (valid manifests sorted by name, [(path, error message)]).
    A directory with no manifest is not a package and is skipped in silence;
    a directory WITH a manifest that does not parse is an error worth showing.
    """
    manifests: list[Manifest] = []
    errors: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return [], []

    for entry in entries:
        path = os.path.join(root, entry, MANIFEST_NAME)
        if not os.path.isfile(path):
            continue
        try:
            manifests.append(load_manifest(path))
        except ManifestError as exc:
            errors.append((path, str(exc)))

    manifests.sort(key=lambda m: m.name)
    return manifests, errors


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
