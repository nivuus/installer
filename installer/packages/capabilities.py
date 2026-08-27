"""Coarse hardware capabilities, the vocabulary of `requires.capabilities`.

This is the one piece of hardware knowledge that cannot move into a package.
The engine must decide whether a package is even offered BEFORE running any of
its code, so it cannot ask the package. What it detects is therefore
deliberately coarse and generic - useful to any third party, and never enough
on its own to configure anything.

The precise work stays with the package, in its `resolve` phase: which PCI
functions share the slot, which IOMMU group they land in, which vfio-pci.ids
to emit, how many CPUs to hand over. The package refines; it does not redo.
"""
from __future__ import annotations

KNOWN_CAPABILITIES = ("iommu", "gpu-discrete", "nvme-dedicated", "cpu-hybrid")


def detect_capabilities(hw: dict, target_disk: str = "") -> set[str]:
    """Capabilities implied by a `hardware.detect_all()` snapshot.

    `target_disk` is the install target chosen in the wizard: the disk being
    installed onto can never be the dedicated disk a package claims, so it is
    excluded. An empty value means "not chosen yet" and excludes nothing.
    """
    caps: set[str] = set()

    if (hw.get("iommu") or {}).get("supported"):
        caps.add("iommu")

    if any(gpu.get("discrete") for gpu in hw.get("gpus") or []):
        caps.add("gpu-discrete")

    spare_nvme = [
        disk for disk in hw.get("disks") or []
        if (disk.get("transport") or "").lower() == "nvme"
        and disk.get("path") != target_disk
    ]
    if spare_nvme:
        caps.add("nvme-dedicated")

    if (hw.get("cpu") or {}).get("hybrid"):
        caps.add("cpu-hybrid")

    return caps
