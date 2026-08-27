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

# /etc/default/grub is sourced as shell by grub-mkconfig: a parameter from a
# third-party package is shell text evaluated as root at install time. A
# denylist (just '"') cannot be exhaustive against that - only an allowlist
# can. Each of these is shell-meaningful and must be refused.
for bad, needle in [
    ("saut\nligne", "saut"),
    ("inject$(id)", "inject"),
    ("inject`id`", "inject"),
    ("inject;id", "inject"),
    ("a b", "a b"),
]:
    try:
        grub_defaults((bad,))
        failures.append(f"shell-unsafe parameter {bad!r} was accepted")
    except ValueError as exc:
        check(f"the error names {bad!r}", needle in str(exc), True)

# A non-string element must fail cleanly (ValueError), not crash on .strip().
try:
    grub_defaults((42,))
    failures.append("a non-string parameter (int) was accepted")
except ValueError as exc:
    check("the error names the non-string parameter", "42" in str(exc), True)

# Positive case: every parameter this project actually emits still passes.
check("every real-world parameter this project emits still passes",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt nohz_full=0-15 '
      'vfio-pci.ids=10de:2786,10de:22bc"'
      in grub_defaults(("intel_iommu=on", "iommu=pt", "nohz_full=0-15",
                        "vfio-pci.ids=10de:2786,10de:22bc")), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all bootloader cmdline tests passed")
