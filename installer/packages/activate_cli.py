#!/usr/bin/env python3
"""First-boot entry point for a package's `activate` phase.

Deployed as /usr/local/sbin/nivuus-package-activate and run once per package
by nivuus-package-activate@<name>.service. It re-reads the answers the wizard
recorded on the target at install time, because the activate phase runs long
after the portal is gone - there is nobody left to ask.

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
        print("usage: nivuus-package-activate <package-name>", file=sys.stderr)
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

    try:
        run_activate(match[0], hardware.detect_all(),
                     state[name].get("answers") or {}, _StderrEmit())
    except HookError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    os.makedirs(STAMP_DIR, exist_ok=True)
    with open(os.path.join(STAMP_DIR, f"{name}.activated"), "w") as fh:
        fh.write(state[name].get("version", "") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
