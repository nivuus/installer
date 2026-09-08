#!/usr/bin/env python3
"""Fixture install hook that refuses unless 'dependent' has already run.

Second link of a two-hop chain (idempotent -> dependent -> grandchild), used
to prove that prerequisite ordering comes from install_order() and not from
the order --prereq was given on the command line.
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
precondition = os.path.join(args.root, "etc/nivuus-dependent.conf")
if not os.path.isfile(precondition):
    print(
        f"grandchild install: 'dependent' has not run ({precondition} is absent)", file=sys.stderr
    )
    sys.exit(1)

target = os.path.join(args.root, "etc/nivuus-grandchild.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write("state=ready\n")

print(json.dumps({"event": "done"}))
