#!/usr/bin/env python3
"""Tests for the generated production domain (sub-project C).

Run: python3 scripts/tests/test_windows_guest_production_domain.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
sys.path.insert(0, str(REPO / "installer"))

import domain  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {exc_type.__name__}")
        return
    failures.append(f"{label}: did not raise {exc_type.__name__}")


def parse_cpuset(cpuset_str: str) -> set:
    """Parse a cpuset string like '14-15' or '0,2,4-6' into a set of CPU indices."""
    result = set()
    for part in cpuset_str.split(","):
        if "-" in part:
            start, end = part.split("-")
            result.update(range(int(start), int(end) + 1))
        else:
            result.add(int(part))
    return result


# The i9-12900K's 8 P-cores expose 16 threads, 0..15.
plan = domain.vcpu_plan(list(range(16)))
check("vcpu count", plan["vcpus"], 14)
check("cores", plan["cores"], 7)
check("threads", plan["threads"], 2)
check("emulator cpuset", plan["emulator_cpuset"], "14-15")
check("first pin", plan["vcpupin"][0], (0, 0))
check("last pin", plan["vcpupin"][-1], (13, 13))
check("pin count matches vcpus", len(plan["vcpupin"]), 14)
# Invariant: union of all pinned CPUs and emulator CPUs equals the input pool.
# Parse emulator_cpuset from the returned string to verify it's correct.
pinned_cpus = set(cpu for _, cpu in plan["vcpupin"])
emulator_cpus = parse_cpuset(plan["emulator_cpuset"])
check("16-CPU invariant: all CPUs accounted for",
      pinned_cpus | emulator_cpus, set(range(16)))

# An odd remainder must drop a thread rather than break SMT pairing, and the
# CPU it frees goes to the emulator rather than being left idle.
odd = domain.vcpu_plan(list(range(11)))
check("odd pool keeps pairs", odd["vcpus"], 8)
check("odd pool cores", odd["cores"], 4)
check("odd pool emulator takes every leftover", odd["emulator_cpuset"], "8-10")
# Invariant: union of all pinned CPUs and emulator CPUs equals the input pool.
# Parse emulator_cpuset from the returned string to verify it's correct.
pinned_odd = set(cpu for _, cpu in odd["vcpupin"])
emulator_odd = parse_cpuset(odd["emulator_cpuset"])
check("11-CPU invariant: all CPUs accounted for",
      pinned_odd | emulator_odd, set(range(11)))

check_raises("pool too small", domain.DomainError, lambda: domain.vcpu_plan([0, 1, 2]))

# Non-contiguous pool must raise DomainError to prevent silent CPU loss
check_raises("non-contiguous pool is refused", domain.DomainError,
             lambda: domain.vcpu_plan([0, 1, 2, 3, 4, 5, 10, 15]))

# All-duplicate pool must raise DomainError to prevent degenerate zero-vcpu plans
check_raises("all-duplicate pool is refused", domain.DomainError,
             lambda: domain.vcpu_plan([5, 5, 5, 5]))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - production domain checks passed")
