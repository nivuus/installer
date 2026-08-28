#!/usr/bin/env python3
"""First-boot entry point for a package's `activate` phase.

Run once per package by nivuus-package-activate@<name>.service, IN PLACE at
/opt/nivuus/installer/packages/activate_cli.py - the unit's ExecStart points
here rather than at a copy under /usr/local/sbin, because this file computes
its own sys.path from __file__ and a copy elsewhere could not import `common`
or `packages`. It re-reads the answers the wizard recorded on the target at
install time, because the activate phase runs long after the portal is gone -
there is nobody left to ask.

It also reads back the FACTS its own resolve phase measured before the
reboot - see packages/facts.py, and its precedence rule: those facts fill
only the keys fresh detection does not produce, because a disk handed to
vfio-pci by the kernel command line this installer wrote is no longer a block
device anybody can measure. That is the whole point of the channel; a fact
that shadowed a live measurement would be the opposite of it.

The stamp is written ONLY on success. An activation that fails is retried at
the next boot rather than silently marked done, which matters because this is
the phase that downloads things: a network that was not up yet is the ordinary
failure here, and it fixes itself.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER_ROOT = os.path.dirname(HERE)
if INSTALLER_ROOT not in sys.path:
    sys.path.insert(0, INSTALLER_ROOT)

from common import hardware  # noqa: E402
from packages.discovery import discover  # noqa: E402
from packages.facts import STATE_KEY as FACTS_STATE_KEY  # noqa: E402
from packages.facts import shadowed_facts  # noqa: E402
from packages.runner import HookError, run_activate  # noqa: E402

STATE_FILE = "/etc/nivuus/packages.json"
STAMP_DIR = "/var/lib/nivuus/packages"


class _StderrEmit:
    """Progress to stderr, so journald syncs it as the unit produces it."""

    def _write(self, level, msg):
        print(f"[{level}] {msg}", file=sys.stderr, flush=True)

    def info(self, step, pct, msg):
        self._write("info", msg)

    def warn(self, step, pct, msg):
        self._write("warn", msg)

    def error(self, step, pct, msg):
        self._write("error", msg)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: activate_cli.py <package-name>", file=sys.stderr)
        return 2
    name = argv[1]

    try:
        with open(STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {STATE_FILE}: {exc}", file=sys.stderr)
        return 1
    if name not in state:
        print(f"package {name!r} is not recorded in {STATE_FILE}", file=sys.stderr)
        return 1

    manifests, _ = discover()
    match = [m for m in manifests if m.name == name]
    if not match:
        print(f"package {name!r} has no manifest on this system", file=sys.stderr)
        return 1

    # Link 3 of 3 of the resolve -> activate channel. A malformed block here
    # is corruption of a root-owned 0600 file the engine alone writes, not an
    # authoring mistake: say so and fail, rather than activate with a hw the
    # package will silently find incomplete. No stamp is written, so the next
    # boot retries - the same policy as every other failure on this path.
    facts = state[name].get(FACTS_STATE_KEY) or {}
    if not isinstance(facts, dict):
        print(f"package {name!r}: {STATE_FILE} holds a malformed "
              f"{FACTS_STATE_KEY!r} block ({type(facts).__name__}); the facts "
              "measured before the reboot cannot be restored",
              file=sys.stderr)
        return 1

    emit = _StderrEmit()
    detected = hardware.detect_all()
    # Never silently: a fact dropped because detection also produced the key
    # is a precedence decision an operator has to be able to read.
    for key in shadowed_facts(detected, facts):
        emit.warn("packages", 0,
                  f"fact {key!r} measured before the reboot is ignored: this "
                  "machine still detects that key, and a fresh measurement "
                  "wins over a recorded one")

    try:
        run_activate(match[0], detected,
                     state[name].get("answers") or {}, emit, facts=facts)
    except HookError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    os.makedirs(STAMP_DIR, exist_ok=True)
    with open(os.path.join(STAMP_DIR, f"{name}.activated"), "w") as fh:
        fh.write(state[name].get("version", "") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
