#!/usr/bin/env python3
"""Tests for the offline payload staged onto the unattend ISO.

Run: python3 scripts/tests/test_windows_guest_payload.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import payload  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def make_tree(root: pathlib.Path, *, with_nvidia=True, with_sudovda=True):
    (root / "provision").mkdir(parents=True)
    (root / "provision" / "run-all.ps1").write_text("# run-all\n")
    (root / "provision" / "00-bootstrap.ps1").write_text("# bootstrap\n")
    (root / "probe").mkdir()
    (root / "probe" / "advanced-color.ps1").write_text("# probe\n")
    drivers = root / "drivers"
    (drivers / "nvidia").mkdir(parents=True)
    (drivers / "sudovda").mkdir(parents=True)
    if with_nvidia:
        (drivers / "nvidia" / "580.00-desktop-win11-64bit.exe").write_bytes(b"MZ")
    if with_sudovda:
        (drivers / "sudovda" / "SudoVDA.inf").write_text("[Version]\n")
        (drivers / "sudovda" / "install.bat").write_text("@echo off\n")
    return payload.PayloadSources(provision_dir=root / "provision",
                                  probe_dir=root / "probe",
                                  drivers_dir=drivers)


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")

    check("nothing missing on a complete tree",
          payload.missing_binaries(sources.drivers_dir), [])

    plan = dict(payload.plan_payload(sources))
    dests = sorted(plan.values())
    check("provision scripts are staged",
          "provision/run-all.ps1" in dests, True)
    check("probe is staged", "probe/advanced-color.ps1" in dests, True)
    check("nvidia driver is staged",
          "drivers/nvidia/580.00-desktop-win11-64bit.exe" in dests, True)
    check("sudovda inf is staged", "drivers/sudovda/SudoVDA.inf" in dests, True)
    check("no absolute destination",
          any(d.startswith("/") for d in dests), False)

    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11 IoT Enterprise LTSC 2024",
                                "20260822-1200")
    payload.stage_payload(dest, sources, marker)
    payload.verify_staged(dest)
    check("marker written", (dest / payload.MARKER_NAME).exists(), True)

    parsed = payload.parse_marker((dest / payload.MARKER_NAME).read_text())
    check("marker target build", parsed["target_build"], "26100")
    check("marker provision version",
          parsed["provision_version"], payload.PROVISION_VERSION)
    check("marker image name",
          parsed["image_name"], "Windows 11 IoT Enterprise LTSC 2024")

    # A staged tree missing its marker is a broken payload, not a warning.
    (dest / payload.MARKER_NAME).unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted a payload with no marker")
    except payload.PayloadError:
        pass

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src", with_nvidia=False, with_sudovda=False)
    missing = payload.missing_binaries(sources.drivers_dir)
    check("both binaries reported missing", len(missing), 2)
    check("nvidia named in the report",
          any("nvidia" in m.lower() for m in missing), True)
    check("sudovda named in the report",
          any("sudovda" in m.lower() for m in missing), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all payload staging tests passed")
