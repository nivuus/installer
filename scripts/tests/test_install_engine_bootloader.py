#!/usr/bin/env python3
"""Tests for the GRUB cmdline assembly in install-engine/steps/bootloader.py.

The packages engine collects kernel-cmdline from every selected package and
hands it here, so it is written ONCE, at the only sane moment - while GRUB is
being installed. That replaces install.sh's sed-after-the-fact, which could
only ever append and had to guard against appending twice.

Only the pure assembly function is exercised: install_bootloader() itself runs
apt and grub-install inside a chroot, which this sandbox has no way to provide.

Run: python3 scripts/tests/test_install_engine_bootloader.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps.bootloader import grub_defaults  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


plain = grub_defaults(())
check("without packages the default line is untouched",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet"' in plain, True)
check("the distributor stays Nivuus", 'GRUB_DISTRIBUTOR="Nivuus"' in plain, True)

withparams = grub_defaults(("intel_iommu=on", "iommu=pt", "nohz_full=0-15"))
check("package params are appended after quiet",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt nohz_full=0-15"'
      in withparams, True)

check("duplicates are collapsed",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1 b=2"'
      in grub_defaults(("a=1", "b=2", "a=1")), True)

check("blank entries are ignored",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1"'
      in grub_defaults(("", "  ", "a=1")), True)

# A parameter carrying a double quote would break the shell-ish grub file.
try:
    grub_defaults(('bad="x"',))
    failures.append("a quoted parameter was accepted")
except ValueError as exc:
    check("the error names the offending parameter", "bad=" in str(exc), True)

check("the file always ends with a newline", plain.endswith("\n"), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all bootloader cmdline tests passed")
