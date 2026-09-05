"""Exclusive hardware claims, and the conflicts they produce.

A claim is how a package says "this piece of hardware is mine alone". The
case this exists for is real and lives on the reference host: the gaming
console claims the GPU, and so would a transcoding or local-inference
package. They cannot both have it. Catching that at wizard time is the whole
point - the alternative is two sets of libvirt hooks fighting over the same
card at runtime, which is a class of failure that only shows up when someone
starts a game.

v1 knows exactly one mode, `exclusive`, and the manifest parser refuses any
other. Shared and counted claims are deliberately absent: nothing needs them
yet, and a resource model is easier to widen than to narrow.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Conflict:
    resource: str
    packages: tuple[str, ...]

    def message(self) -> str:
        names = ", ".join(self.packages)
        return (f"{len(self.packages)} packages réclament « {self.resource} » "
                f"de façon exclusive : {names}. Un seul peut être installé.")


def check_conflicts(manifests) -> list[Conflict]:
    """Every resource claimed exclusively by more than one of `manifests`.

    One conflict per contested resource, naming every claimant - not one per
    pair, which would report the same problem three times for three packages.
    Ordered by resource so the portal renders a stable list.

    Claimants are collected in a set keyed by name, not a list: this function
    is public and other callers may reach it without going through
    `discover()` first, so it must not rely on the caller having already
    de-duplicated by name. Without that, the same manifest object passed
    twice, or two distinct manifest objects that happen to share a `name`,
    would read as two claimants and produce a conflict against itself.
    """
    claimants: dict[str, set[str]] = {}
    for manifest in manifests:
        for resource, mode in manifest.claims:
            if mode == "exclusive":
                claimants.setdefault(resource, set()).add(manifest.name)

    return [
        Conflict(resource=resource, packages=tuple(sorted(names)))
        for resource, names in sorted(claimants.items())
        if len(names) > 1
    ]
