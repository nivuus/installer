#!/usr/bin/env python3
"""Tests for installer/packages/discovery.py.

Discovery is fail-soft by design: one broken third-party manifest must not
make the whole wizard unusable. It is reported alongside the valid ones, never
raised, so the operator sees "this package is broken, here is why" instead of
an installer that refuses to start.

Eligibility, by contrast, is strict and explains itself: an ineligible package
carries the reason it is ineligible, because "not shown" with no explanation
is the worst possible answer on a machine with no screen.

Run: python3 scripts/tests/test_packages_discovery.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.discovery import discover, eligibility, partition  # noqa: E402
from packages.manifest import API_VERSION, parse_manifest  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def write_pkg(root: pathlib.Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "nivuus-package.yaml").write_text(body)


GOOD = """apiVersion: {api}
name: {name}
version: 1.0.0
label: "{name}"
tier: userspace
"""

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    write_pkg(root, "bravo", GOOD.format(api=API_VERSION, name="bravo"))
    write_pkg(root, "alpha", GOOD.format(api=API_VERSION, name="alpha"))
    write_pkg(root, "broken", "apiVersion: nivuus.dev/v99\nname: broken\n")
    # A directory with no manifest at all is simply not a package.
    (root / "notapkg").mkdir()

    found, errors = discover(str(root))
    check("valid packages found", [m.name for m in found], ["alpha", "bravo"])
    check("one broken manifest reported", len(errors), 1)
    check("the error names the offending path", "broken" in errors[0][0], True)
    check("the error explains itself", "apiVersion" in errors[0][1], True)

check("a missing packages dir is empty, not an error",
      discover("/nonexistent/packages"), ([], []))

# --- duplicate names --------------------------------------------------------#
# The package name becomes a systemd unit instance name and a key in the
# installed target's package-state file: two directories declaring the same
# name must not both be offered, or they would overwrite each other's state
# and activation unit at first boot.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    write_pkg(root, "dup-a", GOOD.format(api=API_VERSION, name="console"))
    write_pkg(root, "dup-b", GOOD.format(api=API_VERSION, name="console"))

    found, errors = discover(str(root))
    check("colliding names are excluded from the valid list", found, [])
    check("exactly one collision error is reported", len(errors), 1)
    path_a = str(root / "dup-a" / "nivuus-package.yaml")
    path_b = str(root / "dup-b" / "nivuus-package.yaml")
    check("the collision error mentions both colliding paths",
          all(p in errors[0][1] for p in (path_a, path_b)), True)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    write_pkg(root, "trip-a", GOOD.format(api=API_VERSION, name="console"))
    write_pkg(root, "trip-b", GOOD.format(api=API_VERSION, name="console"))
    write_pkg(root, "trip-c", GOOD.format(api=API_VERSION, name="console"))
    write_pkg(root, "solo", GOOD.format(api=API_VERSION, name="solo"))

    found, errors = discover(str(root))
    check("a three-way collision is still exactly one error", len(errors), 1)
    check("the collision does not poison an unrelated package",
          [m.name for m in found], ["solo"])

# --- eligibility ----------------------------------------------------------- #
base = {"apiVersion": API_VERSION, "name": "demo", "version": "1.0.0",
        "label": "Demo", "tier": "platform"}

needs_iommu = parse_manifest(
    {**base, "requires": {"capabilities": ["iommu"], "features": ["networking"]}},
    "/pkg/demo")

check("eligible when everything is present",
      eligibility(needs_iommu, {"iommu"}, {"networking"}), "")

missing_cap = eligibility(needs_iommu, set(), {"networking"})
check("missing capability is refused", missing_cap != "", True)
check("the reason names the capability", "iommu" in missing_cap, True)

missing_feat = eligibility(needs_iommu, {"iommu"}, set())
check("missing feature is refused", missing_feat != "", True)
check("the reason names the feature", "networking" in missing_feat, True)

free = parse_manifest(dict(base), "/pkg/demo")
check("a package requiring nothing is always eligible",
      eligibility(free, set(), set()), "")

ok, rejected = partition([needs_iommu, free], {"iommu"}, set())
check("partition keeps the eligible one", [m.name for m in ok], ["demo"])
check("partition returns the rejected with its reason", len(rejected), 1)
check("rejected carries a non-empty reason", rejected[0][1] != "", True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all discovery tests passed")
