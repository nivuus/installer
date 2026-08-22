#!/usr/bin/env python3
"""Tests for the build-time offline-payload fetcher (fetch_payload.py).

Networking is never exercised here: every test either stays on the pure
`plan_downloads()` path or pre-populates the destination file so `fetch()`
takes its "already there" branch.

Run: python3 scripts/tests/test_windows_guest_fetch_payload.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import fetch_payload  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


plans = fetch_payload.plan_downloads(pathlib.Path("/tmp/x"))
check("every download has a url and a destination",
      all(d.url.startswith("https://") and d.dest for d in plans), True)
check("no download lands outside the drivers dir",
      all(str(d.dest).startswith("/tmp/x") for d in plans), True)
names = [d.name for d in plans]
check("downloads are uniquely named", len(names), len(set(names)))
# The source virtio-win.iso is mined for two drivers and then dead weight -
# it must land under the dot-directory build cache, never in the payload
# tree proper, or _walk() would ship it to the guest.
virtio_iso = next(d for d in plans if d.name == "virtio-iso")
check("virtio-win.iso lands under the build cache",
      fetch_payload.BUILD_CACHE_DIRNAME in virtio_iso.dest.parts, True)

# fetch() must fail loudly (never silently ship a changed artefact) when a
# path already recorded in the manifest comes back with a different digest.
# No network: the "download" is simulated by the file already being present.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp) / "drivers"
    dest = drivers / "steam" / "SteamSetup.exe"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"a newer build of the installer")
    manifest_dir = drivers / fetch_payload.BUILD_CACHE_DIRNAME
    manifest_dir.mkdir(parents=True)
    (manifest_dir / fetch_payload.MANIFEST_NAME).write_text(
        "steam/SteamSetup.exe\tdeadbeef00000000000000000000000000000000000000000000000000\t2026-01-01\n"
    )
    item = fetch_payload.Download("steam", "https://example.invalid/x", dest)
    try:
        fetch_payload.fetch(item, drivers)
        failures.append("fetch: accepted a digest mismatch against the manifest")
    except fetch_payload.FetchError as e:
        if "changed" not in str(e) or "manifest" not in str(e).lower():
            failures.append(f"fetch manifest-mismatch error is unclear: {e}")

# fetch() must record a first-seen digest rather than reject it.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp) / "drivers"
    dest = drivers / "winfsp" / "winfsp-2.0.msi"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"msi bytes")
    item = fetch_payload.Download("winfsp", "https://example.invalid/x", dest)
    fetch_payload.fetch(item, drivers)
    manifest = fetch_payload.load_manifest(drivers)
    check("first-seen digest is recorded",
          "winfsp/winfsp-2.0.msi" in manifest, True)

# extract_virtio's flatten step must refuse to silently overwrite two files
# that share a basename under different subdirectories. Fake tree, no 7z.
with tempfile.TemporaryDirectory() as tmp:
    dest = pathlib.Path(tmp) / "netkvm"
    nested = dest / "NetKVM"
    (nested / "w11" / "amd64").mkdir(parents=True)
    (nested / "w10" / "amd64").mkdir(parents=True)
    (nested / "w11" / "amd64" / "netkvm.inf").write_text("w11 driver")
    (nested / "w10" / "amd64" / "netkvm.inf").write_text("w10 driver")
    try:
        fetch_payload.flatten_extracted(nested, dest)
        failures.append("flatten_extracted: silently overwrote colliding basenames")
    except fetch_payload.FetchError as e:
        if "collide" not in str(e):
            failures.append(f"flatten_extracted collision error is unclear: {e}")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all fetch_payload tests passed")
