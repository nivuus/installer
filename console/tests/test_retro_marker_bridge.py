#!/usr/bin/env python3
"""Both ends of the retrogaming marker still agree on one path.

The spec expected this guard to disappear with the split, on the premise
that `retro` would be answered in the package's own config with no file in
between. It is not: phase 2a made it a DURABLE MARKER on disk, because
build.py runs much later - possibly by hand, possibly on this very host
once it has booted. So the bridge still exists; it is merely shorter, and
entirely inside console/. The failure it prevents - box ticked in the
wizard, nothing installed on the guest, no test saying so - is exactly as
silent as before.

It asserts the PATH, by driving the real install hook and reading the
result with build.py's own default, rather than comparing two literals.

Run: python3 console/tests/test_retro_marker_bridge.py
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
HOOK = CONSOLE / "hooks" / "install.py"

sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(CONSOLE / "guest"))

import build  # noqa: E402
import retro as console_retro  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- build.py's default marker still resolves through console/retro.py -- #

check("build.py's default marker equals the shared path, rooted at /",
      build.DEFAULT_RETRO_MARKER, console_retro.retro_state_path())

# A small stand-in for the wizard's live-medium 'windows_iso' answer:
# install.py now refuses outright when it is missing (see console/guest_steps.py
# copy_windows_medium / require_windows_iso_answer), and this suite is about
# the retro marker, not the medium - so it just needs SOME readable file.
_fixtures = pathlib.Path(tempfile.mkdtemp(prefix="nivuus-retro-bridge-test-"))
_SOURCE_ISO = _fixtures / "live-medium.iso"
_SOURCE_ISO.write_bytes(b"NIVUUS-FAKE-WINDOWS-MEDIUM")

CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": str(CONSOLE)},
    "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
    "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": True,
                "admin_password": "hunter2hunter2",
                "windows_iso": str(_SOURCE_ISO)},
})


def run_hook(ctx_json: str, root: pathlib.Path) -> subprocess.CompletedProcess:
    # The engine launches package hooks by absolute path with cwd=<package
    # root> - exercise the exact same invocation, not a developer-friendly
    # shortcut, since it is precisely that context that broke the import
    # (see console/hooks/install.py's HERE/sys.path handling).
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=ctx_json, capture_output=True, text=True, cwd=str(CONSOLE))


# --- The actual round trip: run the REAL hook, read with the REAL reader - #
# If either side's path literal/constant were renamed independently, the
# marker would not be where the other side looks - exactly the bug this
# bridge exists to catch.

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    proc = run_hook(CTX, root)
    check("le hook install sort 0 (retro coche)", proc.returncode, 0)

    expected = pathlib.Path(console_retro.retro_state_path(str(root)))
    check("le temoin atterrit exactement la ou console.retro l'attend",
          expected.is_file(), True)
    check("build.py lit le temoin depose par le hook (enabled)",
          build.read_retro_marker(str(expected)), True)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    ctx["answers"]["retro"] = False
    proc = run_hook(json.dumps(ctx), root)
    check("le hook install sort 0 (retro decoche)", proc.returncode, 0)

    expected = pathlib.Path(console_retro.retro_state_path(str(root)))
    check("le temoin atterrit exactement la ou console.retro l'attend, meme decoche",
          expected.is_file(), True)
    check("build.py lit le temoin depose par le hook (disabled)",
          build.read_retro_marker(str(expected)), False)

shutil.rmtree(_fixtures, ignore_errors=True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - the retro marker path is a single source of truth inside console/, "
      "and the install hook writes exactly where build.py reads")
