#!/usr/bin/env python3
"""Tests for the PCI/NVMe detection helpers used by the Windows guest domain.

The parsers are pure: they take captured `lspci` text. `_whole_disk_name` is
exercised against a fake sysfs tree built in a temp dir. So these tests run
anywhere and do not depend on the machine they execute on.

Run: python3 scripts/tests/test_windows_guest_hardware.py
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from common import hardware  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {exc_type.__name__}")
        return
    failures.append(f"{label}: did not raise {exc_type.__name__}")


# Captured from the Nivuus host on 2026-08-22 (`lspci -nn -D`).
LSPCI = """\
0000:00:02.0 VGA compatible controller [0300]: Intel Corporation AlderLake-S GT1 [8086:4680] (rev 0c)
0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786] (rev a1)
0000:01:00.1 Audio device [0403]: NVIDIA Corporation AD104 High Definition Audio Controller [10de:22bc] (rev a1)
0000:02:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller 980 (DRAM-less) [144d:a809]
0000:03:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller SM981/PM981/PM983 [144d:a808]
"""

fns = hardware.parse_pci_functions(LSPCI, "01:00.0")
check("two functions in the GPU slot", len(fns), 2)
check("first function address", fns[0]["address"], "0000:01:00.0")
check("first function id", fns[0]["id"], "10de:2786")
check("first function number", fns[0]["function"], "0x0")
check("second function number", fns[1]["function"], "0x1")
check("bus is hex-prefixed", fns[0]["bus"], "0x01")
check("slot is hex-prefixed", fns[0]["slot"], "0x00")
check("domain is hex-prefixed", fns[0]["domain"], "0x0000")

# A fully-qualified slot must behave identically to a short one: callers get
# the address from either form depending on which lspci flags they used.
check(
    "qualified slot matches short slot",
    hardware.parse_pci_functions(LSPCI, "0000:01:00.0"),
    fns,
)

check("unknown slot yields nothing", hardware.parse_pci_functions(LSPCI, "09:00.0"), [])
check("empty input yields nothing", hardware.parse_pci_functions("", "01:00.0"), [])

# Test the whole-disk name extractor against a fake sysfs tree (same approach
# as scripts/tests/test_pcie_wifi_link_guard.sh), so this does not depend on
# what block devices happen to exist on the machine running the test.
with tempfile.TemporaryDirectory() as fake_root:
    def _make_partition(disk, part):
        disk_dir = os.path.join(fake_root, disk)
        part_dir = os.path.join(disk_dir, part)
        os.makedirs(part_dir, exist_ok=True)
        with open(os.path.join(part_dir, "partition"), "w") as fh:
            fh.write("3\n")
        os.symlink(os.path.join(disk, part), os.path.join(fake_root, part))

    def _make_whole_disk(disk):
        os.makedirs(os.path.join(fake_root, disk), exist_ok=True)

    _make_partition("nvme0n1", "nvme0n1p3")
    _make_whole_disk("sda")
    _make_partition("mmcblk0", "mmcblk0p1")

    check(
        "partition name resolves to parent disk",
        hardware._whole_disk_name("nvme0n1p3", sysfs_root=fake_root),
        "nvme0n1",
    )
    check(
        "whole-disk name unchanged",
        hardware._whole_disk_name("sda", sysfs_root=fake_root),
        "sda",
    )
    check(
        "mmcblk-style partition resolves to parent disk",
        hardware._whole_disk_name("mmcblk0p1", sysfs_root=fake_root),
        "mmcblk0",
    )
    check(
        "missing node falls back to the name unchanged",
        hardware._whole_disk_name("nonexistent0", sysfs_root=fake_root),
        "nonexistent0",
    )

ctrls = hardware.parse_nvme_controllers(LSPCI)
check("two NVMe controllers", len(ctrls), 2)
check("host controller listed", ctrls[0]["address"], "0000:02:00.0")
check("guest controller listed", ctrls[1]["address"], "0000:03:00.0")

picked = hardware.select_passthrough_nvme(ctrls, {"0000:02:00.0"})
check("picks the controller that is not the host root", picked["address"], "0000:03:00.0")
check("picked id", picked["id"], "144d:a808")

# Refusing to guess is the whole point: this disk gets wiped.
check_raises(
    "host unknown is never a guess",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(ctrls[:1], set()),
)
check_raises(
    "no candidate left",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(
        ctrls, {"0000:02:00.0", "0000:03:00.0"}
    ),
)
check_raises(
    "no controller at all",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme([], {"0000:02:00.0"}),
)

# The template consumes nvme.bus / nvme.slot / nvme.function, so the resolved
# record must be decomposed, not just {address, id}.
resolved = hardware.resolve_passthrough_nvme(LSPCI, {"0000:02:00.0"})
check("resolved address", resolved["address"], "0000:03:00.0")
check("resolved bus", resolved["bus"], "0x03")
check("resolved slot", resolved["slot"], "0x00")
check("resolved function", resolved["function"], "0x0")
check("resolved id", resolved["id"], "144d:a808")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - hardware detection checks passed")
