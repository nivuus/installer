#!/usr/bin/env python3
"""The NVIDIA driver and Apollo are fetched, pinned by version and digest.

payload.REQUIRED_BINARIES demanded both while fetch_payload.py fetched
neither, so build.py refused every payload the package produced itself.
These tests stay offline: fetch() keeps a file already at its destination,
so pre-populating it exercises the digest check without a download.

Run: python3 console/tests/test_windows_guest_pinned_installers.py
"""
import hashlib
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import fetch_payload  # noqa: E402
import payload  # noqa: E402
import pinned_installers  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


drivers = pathlib.Path("/tmp/x")
plan = {d.name: d for d in fetch_payload.plan_downloads(drivers)}

# --- every installer payload.py requires is now something we fetch ------- #
required_subdirs = {sub for sub, _glob, _desc in payload.REQUIRED_BINARIES}
for pin in pinned_installers.PINNED:
    item = plan.get(pin.name)
    check(f"{pin.name} is planned", item is not None, True)
    if item is None:
        continue
    check(f"{pin.name} lands where payload.py looks for it",
          item.dest.parent, drivers / pin.subdir)
    check(f"{pin.name} is a subdir payload.py requires",
          pin.subdir in required_subdirs, True)
    check(f"{pin.name} carries its pinned digest", item.sha256, pin.sha256)
    check(f"{pin.name}'s URL names its pinned file",
          item.url.endswith("/" + pin.filename), True)
check("the moving pointers stay unpinned", plan["steam"].sha256, None)

# --- a file that is not the pinned release is refused -------------------- #
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    pin = pinned_installers.PINNED[0]
    wrong = fetch_payload.Download(pin.name, pin.url,
                                   root / pin.subdir / pin.filename, pin.sha256)
    wrong.dest.parent.mkdir(parents=True)
    wrong.dest.write_bytes(b"not the driver")
    try:
        fetch_payload.fetch(wrong, root)
        failures.append("a wrong digest was accepted")
    except fetch_payload.FetchError as exc:
        check("the refusal names the pinned digest", pin.sha256 in str(exc), True)

    body = b"the pinned release"
    right = fetch_payload.Download(
        "probe", "https://example.invalid/probe.exe", root / "probe" / "probe.exe",
        hashlib.sha256(body).hexdigest())
    right.dest.parent.mkdir(parents=True)
    right.dest.write_bytes(body)
    check("the pinned digest is accepted", fetch_payload.fetch(right, root),
          hashlib.sha256(body).hexdigest())

# --- a previous pin never sits beside the current one -------------------- #
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    for pin in pinned_installers.PINNED:
        (root / pin.subdir).mkdir()
        (root / pin.subdir / pin.filename).write_bytes(b"current")
        (root / pin.subdir / "old-version.exe").write_bytes(b"stale")
    removed = pinned_installers.prune_stale(root)
    check("every stale installer is removed",
          sorted(removed), sorted(f"{p.subdir}/old-version.exe"
                                  for p in pinned_installers.PINNED))
    check("the pinned ones are kept",
          all((root / p.subdir / p.filename).is_file()
              for p in pinned_installers.PINNED), True)

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the NVIDIA driver and Apollo are fetched, pinned by version and "
      "digest, and a previous pin never rides along")
