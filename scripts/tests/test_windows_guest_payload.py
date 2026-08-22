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
        (drivers / "sudovda" / "sudovda.cer").write_bytes(b"cert")
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
    check("all missing items reported", len(missing), 4)
    check("nvidia named in the report",
          any("nvidia" in m.lower() for m in missing), True)
    check("sudovda named in the report",
          any("sudovda" in m.lower() for m in missing), True)
    check("install.bat named in the report",
          any("install.bat" in m for m in missing), True)
    check("sudovda.cer named in the report",
          any("sudovda.cer" in m for m in missing), True)

# Test missing install.bat
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    sources = make_tree(src)
    (sources.drivers_dir / "sudovda" / "install.bat").unlink()
    missing = payload.missing_binaries(sources.drivers_dir)
    check("missing install.bat is reported",
          any("install.bat" in m for m in missing), True)

# Test missing sudovda.cer
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    sources = make_tree(src)
    (sources.drivers_dir / "sudovda" / "sudovda.cer").unlink()
    missing = payload.missing_binaries(sources.drivers_dir)
    check("missing sudovda.cer is reported",
          any("sudovda.cer" in m for m in missing), True)
    check("missing_binaries does not mention Apollo",
          any("Apollo" in m for m in missing), False)

# Test stage_payload error includes Apollo guidance for SudoVDA
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    sources = make_tree(src)
    (sources.drivers_dir / "sudovda" / "install.bat").unlink()
    try:
        payload.stage_payload(root / "dest", sources, "marker")
        failures.append("stage_payload: accepted incomplete SudoVDA")
    except payload.PayloadError as e:
        if "Apollo" not in str(e):
            failures.append(f"stage_payload error missing Apollo guidance: {e}")

# Test verify_staged catches missing sudovda files (verify no Apollo guidance)
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "drivers" / "sudovda" / "install.bat").unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted payload with missing install.bat")
    except payload.PayloadError as e:
        if "install.bat" not in str(e):
            failures.append(f"verify_staged error didn't mention install.bat: {e}")
        if "Apollo" in str(e):
            failures.append(f"verify_staged error mentions Apollo (should not): {e}")

# Test stage_payload rejects dest_root equal to source directory
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    try:
        payload.stage_payload(sources.provision_dir, sources, "marker")
        failures.append("stage_payload: accepted dest_root == source dir")
    except payload.PayloadError:
        pass
    check("source dir still exists after rejected stage",
          sources.provision_dir.exists(), True)

# Test staging twice removes stray file
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "stray.txt").write_text("stray")
    check("stray file exists", (dest / "stray.txt").exists(), True)
    payload.stage_payload(dest, sources, marker)
    check("stray file removed after restage",
          (dest / "stray.txt").exists(), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all payload staging tests passed")
