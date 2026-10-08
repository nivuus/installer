"""Recording a package's facts by hand, for a machine `resolve` never saw.

Facts are what a package's `resolve` measured before the install made it
unmeasurable (see facts.py): the console's dedicated NVMe has neither a size
nor an address the host can read once vfio-pci owns it. The engine records
them at install time. A package that was ADOPTED never went through
`resolve`, so it has none, and an activate that needs them refuses - the
reference host's console stops at `cannot read the size of /dev/nvme1n1`.
Re-running `resolve` cannot help: by then the disk is invisible to it too.

So the operator states them, measured some other way (from inside the guest,
from the domain XML), and this module validates them with the very parser
the engine applies to a `facts` event before writing them to the state file.
A fact recorded here is indistinguishable from one `resolve` emitted, which
is the point: the activate phase reads one channel, not two.

Values are JSON, so a number stays a number and a mapping stays a mapping:
`dedicated_nvme_size_bytes=2000398934016`,
`dedicated_nvme_pci='{"device": "/dev/nvme1n1", "address": "0000:03:00.0"}'`.
A bare word that is not JSON is refused rather than guessed into a string.
"""
from __future__ import annotations

import json

from . import state as state_mod
from .facts import FACTS_EVENT, STATE_KEY, FactsError, parse_facts_event
from .updater import UpdateError, lock


def parse_assignments(assignments: list[str]) -> dict:
    """`key=<json>` strings, each value decoded as JSON."""
    parsed = {}
    for assignment in assignments:
        key, sep, raw = assignment.partition("=")
        if not sep:
            raise UpdateError(f"expected key=<json value>, got {assignment!r}")
        try:
            parsed[key] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpdateError(
                f"{key!r}: the value is not JSON ({exc.msg}); quote a string "
                f"as JSON, e.g. {key}='\"text\"'") from None
    try:
        return parse_facts_event({"event": FACTS_EVENT, "facts": parsed})
    except FactsError as exc:
        raise UpdateError(str(exc)) from None


def record(name: str, assignments: list[str]) -> dict:
    """Merge `assignments` into `name`'s recorded facts and save.

    Returns every fact now recorded for `name`. With no assignment it only
    reads them.
    """
    given = parse_assignments(assignments)
    with lock():
        current = state_mod.load()
        if name not in current:
            raise UpdateError(f"{name}: not installed on this machine")
        recorded = current[name].get(STATE_KEY) or {}
        if not isinstance(recorded, dict):
            raise UpdateError(
                f"{name}: the recorded {STATE_KEY!r} block is a "
                f"{type(recorded).__name__}, not a mapping; refusing to "
                "merge into it")
        if not given:
            return dict(recorded)
        merged = {**recorded, **given}
        current[name][STATE_KEY] = merged
        state_mod.save(current)
        return merged
