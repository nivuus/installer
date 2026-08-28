"""Facts: what `resolve` measured and the reboot then makes unmeasurable.

THE GAP THIS CLOSES. The nivuus.dev/v1 contract names its three phases
against the reboot: `resolve` runs before anything is written, `install`
writes into a root, `activate` runs after the reboot with a network. Between
the first and the third there was no channel at all - `activate` rebuilds its
`hw` by detecting the machine afresh, and anything the installation itself
destroyed is simply gone by then. A platform package routinely needs exactly
that: a disk it is about to hand to vfio-pci, a device the new kernel command
line will capture, any transient state the install consumes. `resolve` is the
only phase that can still see those, and it had nowhere to put what it saw.

So: `resolve` RETURNS facts, the engine PERSISTS them, `activate` gets them
back merged into its `hw`. Nothing here lets resolve write anything - that
property is what keeps `bootloader` where it is in run.py, and inverting it
(a hook writing its own facts file) would trade a read-only phase for a
phase that writes before partition() has even run.

Facts are NOT gated on tier. `kernel-cmdline`, `modules` and `hugepages-mib`
are refused to a `userspace` package because they reach the boot chain and
the wizard has to show them for a separate confirmation. A fact reaches
nothing: it is inert data, handed back to the same package that produced it,
at its own activate phase. A userspace package measuring something the
install destroys has the same problem and gets the same channel.

PRECEDENCE - the rule this module exists to make explicit. A fact describes
the world BEFORE the reboot; `hw` describes it after. When both speak about
the same key, THE FRESH SNAPSHOT WINS and the fact is dropped: "now" beats
"then" whenever "now" can still be observed at all. A fact earns its keep
precisely and only where the fresh snapshot is silent - which is the case it
was designed for, a thing that no longer exists to be detected. The
consequence is deliberate: a stale fact can never mask a live measurement,
and a package that wants the pre-reboot value of something still observable
must say so by naming its fact distinctly (the console names its own
`dedicated_nvme_size_bytes`, not `size_bytes`). Silent shadowing would be
the worst of both - hence shadowed_facts(), so the caller can say out loud
which facts it ignored and why.
"""
from __future__ import annotations

# The jsonl event a resolve hook emits, and the key under which the engine
# records what it emitted in etc/nivuus/packages.json. Both are part of the
# contract: manifest.py and wizard.py expose their constants for the same
# reason - a package's CI asserts them, so a drift breaks a test instead of
# leaving a document lying.
FACTS_EVENT = "facts"
STATE_KEY = "facts"


class FactsError(ValueError):
    """A `facts` event that cannot be understood. Callers add the context.

    Deliberately not HookError: runner.py owns that exception and imports
    this module, not the other way round. run_resolve() catches this and
    re-raises with the package and phase named.
    """


def parse_facts_event(event: dict) -> dict:
    """Validate one `{"event":"facts","facts":{...}}` payload.

    Refuses rather than coerces, exactly like the `platform` event's own
    fields: a hook that speaks the protocol wrongly is a broken hook, and the
    operator needs to learn which package it was rather than silently get an
    empty `hw` addition that only surfaces as a puzzling refusal one reboot
    later.

    Values are not type-checked beyond being JSON: they arrived through
    json.loads and go back out through json.dumps into the state file, so
    they round-trip by construction. What IS checked is the shape the engine
    depends on - a mapping, with non-empty string keys, since those keys
    become keys of the `hw` dict that activate reads.
    """
    payload = event.get("facts")
    if payload is None:
        raise FactsError("a 'facts' event carries no 'facts' mapping")
    if not isinstance(payload, dict):
        raise FactsError(
            f"a 'facts' event's 'facts' must be a mapping ({payload!r})")
    for key in payload:
        # json.loads only ever produces string keys, so this is a guard
        # against a caller handing us a hand-built dict, not against JSON.
        if not isinstance(key, str) or not key.strip():
            raise FactsError(
                f"a 'facts' event has an unusable key ({key!r}); a fact key "
                "becomes a key of the hw dict activate reads")
    return dict(payload)


def merge_into_hw(hw: dict, facts: dict) -> dict:
    """`hw` plus every fact whose key the fresh snapshot did not produce.

    See the module docstring for why the fresh snapshot wins: presence in
    `hw` is what decides, not the value - a detector that emitted the key at
    all had something to say about it, and a fact measured before the reboot
    must not override it.
    """
    merged = dict(hw or {})
    for key, value in (facts or {}).items():
        if key in merged:
            continue
        merged[key] = value
    return merged


def shadowed_facts(hw: dict, facts: dict) -> list[str]:
    """The fact keys merge_into_hw() dropped, so a caller can say so aloud."""
    present = hw or {}
    return sorted(key for key in (facts or {}) if key in present)
