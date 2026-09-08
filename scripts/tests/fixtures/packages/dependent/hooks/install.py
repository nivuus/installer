#!/usr/bin/env python3
"""Fixture install hook that refuses unless 'idempotent' has already run.

Mirrors home-stock's real precondition on home-manager: a satellite package
that writes into what its prerequisite created, and refuses rather than
leaving an orphan when that prerequisite is missing.
"""
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

json.load(sys.stdin)
precondition = os.path.join(args.root, "etc/nivuus-idempotent.conf")
if not os.path.isfile(precondition):
    print("dependent install: 'idempotent' has not run "
          f"({precondition} is absent)", file=sys.stderr)
    sys.exit(1)

target = os.path.join(args.root, "etc/nivuus-dependent.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write("state=ready\n")

print(json.dumps({"event": "done"}))
