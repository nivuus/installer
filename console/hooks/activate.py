#!/usr/bin/env python3
"""Activate phase for the console package - a placeholder until phase 2b.

The guest image is still built by hand, exactly as it was before this
package existed (windows-guest/build.py then domain.py). Wiring that into
this hook is phase 2b's whole subject.

This exits 0 deliberately: the stamp file must be written so systemd stops
retrying a unit that has nothing to do. A hook that failed here would retry
on every boot forever and teach the operator to ignore it.
"""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.parse_args()
    json.load(sys.stdin)
    print(json.dumps({
        "event": "progress", "pct": 100,
        "msg": "console : rien a activer pour l'instant, l'invite Windows se "
               "construit encore a la main (windows-guest/build.py)"}), flush=True)
    print(json.dumps({"event": "done"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
