#!/usr/bin/env python3
"""Tests for installer/packages/fact_record.py - recording an adopted
package's facts by hand.

Run: python3 scripts/tests/test_packages_fact_record.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = tempfile.mkdtemp(prefix="nivuus-facts-")
for key, rel in (("NIVUUS_PACKAGES_DIR", "opt/nivuus-packages"),
                 ("NIVUUS_STATE_FILE", "etc/nivuus/packages.json"),
                 ("NIVUUS_STAMP_DIR", "var/lib/nivuus/packages")):
    os.environ[key] = os.path.join(ROOT, rel)
sys.path.insert(0, str(REPO / "installer"))

from packages import fact_record, state  # noqa: E402
from packages.facts import STATE_KEY, merge_into_hw  # noqa: E402
from packages.updater import UpdateError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except UpdateError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UpdateError, none raised")


SIZE = "dedicated_nvme_size_bytes=2000398934016"
PCI = 'dedicated_nvme_pci={"device": "/dev/nvme1n1", "address": "0000:03:00.0"}'

state.save({"console": {"version": "1.8.0", "state": "installed"}})

# --- refusals leave the state untouched --------------------------------- #
check_refused("a value that is not JSON is refused, never guessed",
              lambda: fact_record.record("console", ["x=nvme1n1"]),
              "not JSON")
check_refused("an assignment without '=' is refused",
              lambda: fact_record.record("console", ["dedicated_nvme"]),
              "key=<json value>")
check_refused("a key the engine could not put in hw is refused",
              lambda: fact_record.record("console", [" =1"]),
              "unusable key")
check_refused("a package that is not installed is refused",
              lambda: fact_record.record("ghost", [SIZE]),
              "not installed")
check("nothing was recorded by the refusals",
      STATE_KEY in state.load()["console"], False)

# --- recording, typed by JSON ------------------------------------------- #
recorded = fact_record.record("console", [SIZE, PCI])
check("a number stays a number", recorded["dedicated_nvme_size_bytes"],
      2000398934016)
check("a mapping stays a mapping", recorded["dedicated_nvme_pci"],
      {"device": "/dev/nvme1n1", "address": "0000:03:00.0"})
check("written where the updater and activate_cli read facts",
      state.load()["console"][STATE_KEY], recorded)
check("the other fields of the record are kept",
      state.load()["console"]["version"], "1.8.0")

# --- the activate path sees them exactly like resolve's own -------------- #
hw = merge_into_hw({"memory_mib": 65536}, state.load()["console"][STATE_KEY])
check("the facts reach the hw activate receives",
      hw.get("dedicated_nvme_size_bytes"), 2000398934016)

# --- merge, read-only listing ------------------------------------------- #
fact_record.record("console", ["dedicated_nvme_size_bytes=1"])
check("a later assignment replaces only its own key",
      state.load()["console"][STATE_KEY],
      {"dedicated_nvme_size_bytes": 1,
       "dedicated_nvme_pci": {"device": "/dev/nvme1n1",
                              "address": "0000:03:00.0"}})
before = state.load()
check("no assignment only reads", fact_record.record("console", []),
      before["console"][STATE_KEY])
check("and writes nothing", state.load(), before)

state.save({"console": {"version": "1.8.0", "state": "installed",
                        STATE_KEY: ["corrupt"]}})
check_refused("a corrupt facts block is never merged into",
              lambda: fact_record.record("console", [SIZE]),
              "not a mapping")

# --- through the real command ------------------------------------------- #
state.save({"console": {"version": "1.8.0", "state": "installed"}})
proc = subprocess.run(
    [sys.executable, str(REPO / "installer" / "packages" / "nivuus_cli.py"),
     "facts", "console", SIZE], capture_output=True, text=True, env=os.environ)
check("`nivuus facts` exits 0", proc.returncode, 0)
check("`nivuus facts` recorded the fact",
      state.load()["console"].get(STATE_KEY),
      {"dedicated_nvme_size_bytes": 2000398934016})
proc = subprocess.run(
    [sys.executable, str(REPO / "installer" / "packages" / "nivuus_cli.py"),
     "facts", "console", "x=word"], capture_output=True, text=True,
    env=os.environ)
check("`nivuus facts` refuses non-JSON with exit 1", proc.returncode, 1)

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - facts recorded by hand are validated like resolve's and reach "
      "activate through the same state key")
