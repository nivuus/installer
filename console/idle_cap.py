"""Idle C-state cap: the kernel parameter some platforms need to stay alive.

`intel_idle.max_cstate=3` keeps the cores in C6 and hides C8/C10. On the
reference host (i9-12900K, Alder Lake-S) removing it on 2026-07-22 started a
series of hard platform freezes the next day, and putting it back on
2026-10-26 cut their rate (5 in 75 h before, 4 in 12 days after) - see
docs/claude/host-stability-incidents.md and host-infrastructure.md. The cap
only lowers the rate, it does not remove the fault, but the history says to
never ship this board without it.

The rule is keyed on the CPUID identity (vendor, family, model), NOT on the
marketing string and NOT on "every machine": a CPU whose deep C-states have
never been seen to misbehave must keep them (idle power is 4 W lower with
C8/C10, measured 2026-07-23). Adding a CPU to the table is an owner decision
that needs the same kind of evidence.

OPEN DECISION: only the model seen to freeze is listed. Other Alder Lake-S
steppings and other Alder Lake / Raptor Lake models share the idle-state
design but no freeze has been observed on them, so they are NOT listed.
"""
from __future__ import annotations

CSTATE_CAP_PARAM = "intel_idle.max_cstate=3"

# (vendor_id, cpu family, model) as /proc/cpuinfo reports them.
# 151 == 0x97 == Alder Lake-S (desktop; the 12th-gen i9-12900K).
CAPPED_CPUS: frozenset[tuple[str, int, int]] = frozenset({
    ("GenuineIntel", 6, 151),
})


def cstate_cap_params(cpu: dict) -> list[str]:
    """Kernel parameters capping idle C-states for this CPU, or [].

    `cpu` is the `cpu` block of the hardware snapshot (installer/common/
    hardware.py::cpu_topology). An absent or unreadable identity yields no
    cap: refusing to guess is right here, because the cap costs idle power on
    every machine that does not need it.
    """
    key = (cpu.get("vendor"), cpu.get("family"), cpu.get("model_id"))
    return [CSTATE_CAP_PARAM] if key in CAPPED_CPUS else []
