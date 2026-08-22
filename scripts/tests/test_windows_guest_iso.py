#!/usr/bin/env python3
"""Tests for the secondary unattend ISO.

Builds a real (tiny) ISO with xorriso, which must be installed.
Run: python3 scripts/tests/test_windows_guest_iso.py
"""
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import unattend_iso as ui  # noqa: E402

if shutil.which("xorriso") is None:
    print("FAIL (1)\n  - xorriso is not installed")
    sys.exit(1)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


cmd = ui.xorriso_command("/stage", "/out.iso")
check("uses mkisofs emulation", cmd[:3], ["xorriso", "-as", "mkisofs"])
# ISO level 3 lifts the 4 GiB single-file limit; Joliet is what Windows reads.
check("iso level 3", "-iso-level" in cmd and cmd[cmd.index("-iso-level") + 1] == "3", True)
check("joliet", "-J" in cmd, True)
check("rock ridge", "-rational-rock" in cmd, True)
check("volume id", cmd[cmd.index("-volid") + 1], ui.VOLID)
check("output last-but-one", cmd[-2:], ["/out.iso", "/stage"])

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    stage = root / "stage"
    (stage / "nivuus" / "provision").mkdir(parents=True)
    (stage / "autounattend.xml").write_text("<unattend/>\n")
    (stage / "nivuus" / "PAYLOAD.id").write_text("nivuus_payload=1\n")
    (stage / "nivuus" / "provision" / "run-all.ps1").write_text("# run-all\n")

    iso = root / "nivuus-unattend.iso"
    ui.build_iso(stage, iso)
    check("iso exists", iso.is_file(), True)

    listing = ui.list_iso(iso)
    check("answer file at the root", "/autounattend.xml" in listing, True)
    check("marker present", "/nivuus/PAYLOAD.id" in listing, True)
    check("provision script present",
          "/nivuus/provision/run-all.ps1" in listing, True)
    check("paths are unquoted", any(p.startswith("'") for p in listing), False)

    ui.verify_iso(iso)

    # An ISO without the answer file at its root is useless: Setup would ask
    # every question by hand. That must fail here, not at first boot.
    bad_stage = root / "bad"
    (bad_stage / "nivuus").mkdir(parents=True)
    (bad_stage / "nivuus" / "PAYLOAD.id").write_text("nivuus_payload=1\n")
    bad_iso = root / "bad.iso"
    ui.build_iso(bad_stage, bad_iso)
    try:
        ui.verify_iso(bad_iso)
        failures.append("verify_iso: accepted an ISO with no autounattend.xml")
    except ui.IsoError:
        pass

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all unattend ISO tests passed")
