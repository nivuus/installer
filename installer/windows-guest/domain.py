#!/usr/bin/env python3
"""Generate the production Windows guest domain from detected hardware.

The existing production XML is NOT the source: it carries hypervisor masking,
a fabricated SMBIOS and a vBIOS override that measurements on 2026-08-22
showed unnecessary. This module builds from the requirement instead.

Usage:
    python3 domain.py xml
    sudo python3 domain.py define [--replace]
"""
from __future__ import annotations


class DomainError(RuntimeError):
    """Raised when the domain cannot be built safely."""


def vcpu_plan(pool: list[int], reserve: int = 2) -> dict:
    """Split isolated host CPUs between the guest and QEMU's own threads.

    `reserve` host CPUs are kept for the emulator and iothreads: they carry the
    vhost and virtiofsd work, and leaving that on a vcpu makes frame pacing
    jitter. The guest gets whole SMT pairs — an odd remainder drops a thread
    rather than hand Windows a lone sibling, which is the mistake the 14x1
    topology made before 2026-07-17.

    Assumes SMT siblings are adjacent in the sorted pool (e.g., (0,1), (2,3)).
    The pool must be contiguous after deduplication and sorting. A machine whose
    SMT pairs are not value-adjacent (e.g., real pairs (0,8),(1,9)) requires a
    code change here, not a differently-ordered argument.
    """
    # Validate contiguity: sorted unique pool must be [min..max] with no gaps.
    # This prevents emulator_cpuset from silently naming CPUs not in the pool.
    # Size check is done on the unique set to reject degenerate inputs.
    unique_sorted = sorted(set(pool))
    if len(unique_sorted) < reserve + 2:
        raise DomainError(
            f"need at least {reserve + 2} isolated CPUs, got {len(unique_sorted)}"
        )
    expected_range = list(range(unique_sorted[0], unique_sorted[-1] + 1))
    if unique_sorted != expected_range:
        gaps = set(expected_range) - set(unique_sorted)
        raise DomainError(
            f"pool is not contiguous; missing CPUs: {sorted(gaps)}"
        )

    ordered = unique_sorted
    guest = ordered[: len(ordered) - reserve]
    if len(guest) % 2:
        guest = guest[:-1]
    emulator = ordered[len(guest):]
    return {
        "vcpus": len(guest),
        "cores": len(guest) // 2,
        "threads": 2,
        "vcpupin": [(i, cpu) for i, cpu in enumerate(guest)],
        "emulator_cpuset": f"{emulator[0]}-{emulator[-1]}",
    }
