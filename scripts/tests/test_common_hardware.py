#!/usr/bin/env python3
"""Tests for installer/common/hardware.py's memory_total_mib().

Host RAM belongs on the ENGINE side (installer/common/hardware.py), not
inside one package: it is coarse and generic, useful to any package, the
same reasoning that already puts iommu_support() and cpu_topology() here
rather than in console/hardware.py. memory_total_mib() must fail OPEN like
every other function in this module - an unreadable or malformed
/proc/meminfo yields 0, never an exception, because one undetectable figure
must not break the wizard.

Run: python3 scripts/tests/test_common_hardware.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

import common.hardware as hardware  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def with_meminfo(content: str, fn):
    """Write `content` to a temp file, call fn(path), return the result."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".meminfo",
                                      delete=False) as fh:
        fh.write(content)
        path = fh.name
    try:
        return fn(path)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


# --- the normal case: a real-shaped /proc/meminfo ------------------------ #
REAL_SHAPED = """\
MemTotal:       65614252 kB
MemFree:         1234567 kB
MemAvailable:   23456789 kB
Buffers:          123456 kB
"""
check("MemTotal is read and converted kB->MiB (floor)",
      with_meminfo(REAL_SHAPED, hardware.memory_total_mib), 65614252 // 1024)

# --- absent file: fail open, never raise ---------------------------------- #
check("a missing /proc/meminfo path yields 0, not an exception",
      hardware.memory_total_mib("/nonexistent/path/does-not-exist/meminfo"), 0)

# --- present but unreadable (a directory, not a file): fail open --------- #
with tempfile.TemporaryDirectory() as tmpdir:
    check("a path that is a directory yields 0, not an exception",
          hardware.memory_total_mib(tmpdir), 0)

# --- present but missing the MemTotal line -------------------------------- #
NO_MEMTOTAL = """\
MemFree:         1234567 kB
Buffers:          123456 kB
"""
check("a meminfo with no MemTotal line yields 0",
      with_meminfo(NO_MEMTOTAL, hardware.memory_total_mib), 0)

# --- present but MemTotal is not a number --------------------------------- #
MALFORMED = "MemTotal:       lots kB\n"
check("a non-numeric MemTotal value yields 0, not an exception",
      with_meminfo(MALFORMED, hardware.memory_total_mib), 0)

# --- present but completely empty ----------------------------------------- #
check("an empty meminfo file yields 0",
      with_meminfo("", hardware.memory_total_mib), 0)

# --- MemTotal with no value at all (truncated line) ------------------------ #
TRUNCATED = "MemTotal:\n"
check("a MemTotal line with no value yields 0, not an exception",
      with_meminfo(TRUNCATED, hardware.memory_total_mib), 0)

# --- detect_all() carries memory_mib on the real machine ------------------ #
# Sanity/integration check, not a unit test of the parser (already covered,
# deterministically, above): confirms the key exists and is wired in, on
# whatever machine actually runs this suite.
snapshot = hardware.detect_all()
check("detect_all() carries a 'memory_mib' key", "memory_mib" in snapshot, True)
check("detect_all()['memory_mib'] is a non-negative int",
      isinstance(snapshot["memory_mib"], int) and snapshot["memory_mib"] >= 0,
      True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all common hardware (memory_total_mib) tests passed")
