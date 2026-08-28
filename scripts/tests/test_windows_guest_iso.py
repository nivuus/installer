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
GUEST = REPO / "console" / "guest"
sys.path.insert(0, str(GUEST))

import autounattend as ua  # noqa: E402
import payload  # noqa: E402
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

    # Test that list_iso correctly handles paths with embedded single quotes.
    quote_stage = root / "quote_stage"
    (quote_stage / "nivuus" / "provision").mkdir(parents=True)
    (quote_stage / "autounattend.xml").write_text("<unattend/>\n")
    (quote_stage / "nivuus" / "PAYLOAD.id").write_text("nivuus_payload=1\n")
    (quote_stage / "nivuus" / "provision" / "run-all.ps1").write_text("# run-all\n")
    (quote_stage / "nivuus" / "provision" / "o'brien.txt").write_text("driver\n")
    quote_iso = root / "quote.iso"
    ui.build_iso(quote_stage, quote_iso)
    quote_listing = ui.list_iso(quote_iso)
    check("path with quote parsed correctly",
          "/nivuus/provision/o'brien.txt" in quote_listing, True)
    check("no escape garbage in listing",
          any("'\"'" in p for p in quote_listing), False)

    # Test that build_iso fails when staging directory doesn't exist.
    try:
        ui.build_iso(root / "nonexistent", root / "fail.iso")
        failures.append("build_iso: accepted nonexistent staging directory")
    except ui.IsoError:
        pass

# Five places encode "the payload root is called nivuus, its marker is
# PAYLOAD.id" independently, and until now only agreed by luck: pin them
# against each other so a drift in any one fails loudly instead of producing
# an ISO the guest bootstrap silently never finds its payload on.
parts = ua.PAYLOAD_MARKER.split("\\")
check("autounattend.PAYLOAD_MARKER filename matches payload.MARKER_NAME",
      parts[-1], payload.MARKER_NAME)
root = parts[1]
check("payload root name is non-empty", bool(root), True)

unattend_iso_src = (GUEST / "unattend_iso.py").read_text()
build_src = (GUEST / "build.py").read_text()
bootstrap_src = (GUEST / "provision" / "00-bootstrap.ps1").read_text()

check("unattend_iso.verify_iso requires the same marker path",
      f"/{root}/{payload.MARKER_NAME}" in unattend_iso_src, True)
check("unattend_iso.verify_iso requires the same run-all.ps1 path",
      f"/{root}/provision/run-all.ps1" in unattend_iso_src, True)
check("build.py stages the payload into the same root directory name",
      f'stage / "{root}"' in build_src, True)
check("00-bootstrap.ps1 resume loop scans for the same marker path",
      ua.PAYLOAD_MARKER in bootstrap_src, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all unattend ISO tests passed")
