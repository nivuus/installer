#!/usr/bin/env python3
"""Tests for installer/packages/conflicts.py.

A claim is how a package says "this piece of hardware is mine alone". The
case that matters on this very host: the gaming VM claims the GPU, and so
would a transcoding or local-inference package - they cannot both have it,
and the engine must say so BEFORE the install rather than let two sets of
libvirt hooks fight over the same card at runtime.

Run: python3 scripts/tests/test_packages_conflicts.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.conflicts import Conflict, check_conflicts  # noqa: E402
from packages.manifest import API_VERSION, parse_manifest  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def pkg(name, claims):
    return parse_manifest({
        "apiVersion": API_VERSION, "name": name, "version": "1.0.0",
        "label": name, "tier": "userspace", "claims": claims,
    }, f"/pkg/{name}")


console = pkg("console", {"gpu": "exclusive", "nvme": "exclusive"})
inference = pkg("inference", {"gpu": "exclusive"})
media = pkg("media", {})

check("no packages, no conflicts", check_conflicts([]), [])
check("a single claimer is fine", check_conflicts([console]), [])
check("claimless packages never conflict",
      check_conflicts([media, console]), [])

clash = check_conflicts([console, inference])
check("two exclusive claims on the GPU conflict", len(clash), 1)
check("the conflict names the resource", clash[0].resource, "gpu")
check("the conflict names both packages, sorted",
      clash[0].packages, ("console", "inference"))
check("the message names the resource and both packages",
      all(t in clash[0].message() for t in ("gpu", "console", "inference")),
      True)

# The nvme claim is held by console alone: it must NOT be reported.
check("only the contested resource is reported",
      [c.resource for c in clash], ["gpu"])

# Three claimers on one resource is one conflict, not three.
third = pkg("third", {"gpu": "exclusive"})
triple = check_conflicts([console, inference, third])
check("three claimers are one conflict", len(triple), 1)
check("all three are named", triple[0].packages,
      ("console", "inference", "third"))

# Conflicts are ordered by resource so the portal renders them stably.
disks = pkg("disks", {"nvme": "exclusive"})
both = check_conflicts([console, inference, disks])
check("conflicts are sorted by resource",
      [c.resource for c in both], ["gpu", "nvme"])

check("Conflict is hashable and comparable",
      Conflict("gpu", ("a", "b")) == Conflict("gpu", ("a", "b")), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all conflict detection tests passed")
