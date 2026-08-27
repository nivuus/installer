#!/usr/bin/env python3
"""Tests for installer/packages/capabilities.py and hardware.iommu_support().

The engine must answer `requires.capabilities` BEFORE running any hook, so
this detection is the one piece of hardware knowledge that cannot move to a
package. It stays deliberately coarse: the precise work (which PCI functions,
which IOMMU group, which vfio-pci.ids) belongs to the package's resolve phase.

The IOMMU check is the subtle one. The live ISO boots WITHOUT intel_iommu=on -
adding it is exactly what a platform package asks for - so "iommu" must mean
"the firmware advertises it", read from the ACPI tables, not "it is on now".

Run: python3 scripts/tests/test_packages_capabilities.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from common import hardware  # noqa: E402
from packages.capabilities import (  # noqa: E402
    KNOWN_CAPABILITIES, detect_capabilities,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- hardware.iommu_support: read the firmware, not the cmdline ------------ #
with tempfile.TemporaryDirectory() as tmp:
    tables = pathlib.Path(tmp)
    check("no ACPI table means unsupported",
          hardware.iommu_support(str(tables))["supported"], False)

    (tables / "DMAR").write_bytes(b"DMAR")
    intel = hardware.iommu_support(str(tables))
    check("DMAR means supported", intel["supported"], True)
    check("DMAR means intel", intel["vendor"], "intel")

with tempfile.TemporaryDirectory() as tmp:
    tables = pathlib.Path(tmp)
    (tables / "IVRS").write_bytes(b"IVRS")
    amd = hardware.iommu_support(str(tables))
    check("IVRS means supported", amd["supported"], True)
    check("IVRS means amd", amd["vendor"], "amd")

# A missing directory must not raise - detection is fail-open everywhere else.
check("missing ACPI dir is not fatal",
      hardware.iommu_support("/nonexistent/acpi")["supported"], False)

# --- detect_capabilities --------------------------------------------------- #
EMPTY = {"disks": [], "gpus": [], "cpu": {}, "iommu": {"supported": False}}
check("nothing detected yields nothing", detect_capabilities(EMPTY), set())

hw = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [
        {"slot": "00:02.0", "vendor": "intel", "discrete": False, "ids": []},
        {"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
         "ids": ["10de:2786", "10de:22bc"]},
    ],
    "disks": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "transport": "nvme"},
        {"name": "nvme1n1", "path": "/dev/nvme1n1", "transport": "nvme"},
        {"name": "sda", "path": "/dev/sda", "transport": "sata"},
    ],
    "cpu": {"hybrid": True, "performance_cpus": [0, 1, 2, 3]},
}

caps = detect_capabilities(hw, target_disk="/dev/nvme0n1")
check("iommu supported even though inactive", "iommu" in caps, True)
check("discrete gpu detected", "gpu-discrete" in caps, True)
check("hybrid cpu detected", "cpu-hybrid" in caps, True)
check("the second nvme is free for a package", "nvme-dedicated" in caps, True)

# With only one NVMe and it being the install target, nothing is left over.
one_nvme = {**hw, "disks": [{"name": "nvme0n1", "path": "/dev/nvme0n1",
                             "transport": "nvme"}]}
check("the install target does not count as dedicated",
      "nvme-dedicated" in detect_capabilities(one_nvme, "/dev/nvme0n1"), False)
check("without a target every nvme counts",
      "nvme-dedicated" in detect_capabilities(one_nvme), True)

# An iGPU alone is not a passthrough candidate.
igpu_only = {**hw, "gpus": [{"slot": "00:02.0", "vendor": "intel",
                             "discrete": False, "ids": []}]}
check("integrated gpu is not gpu-discrete",
      "gpu-discrete" in detect_capabilities(igpu_only), False)

check("every emitted capability is a known one",
      caps - set(KNOWN_CAPABILITIES), set())

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all capability detection tests passed")
