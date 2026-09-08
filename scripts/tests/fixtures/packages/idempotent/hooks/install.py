#!/usr/bin/env python3
"""Fixture install hook that rewrites its output, so replaying changes nothing."""

import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

json.load(sys.stdin)
target = os.path.join(args.root, "etc/nivuus-idempotent.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write("state=ready\n")

print(json.dumps({"event": "done"}))
