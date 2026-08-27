#!/usr/bin/env python3
"""Demo install hook: writes one marker under --root, proving it got the root."""
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

ctx = json.load(sys.stdin)
marker = os.path.join(args.root, "etc/nivuus-demo.json")
os.makedirs(os.path.dirname(marker), exist_ok=True)
with open(marker, "w") as fh:
    json.dump({"phase": args.phase, "answers": ctx["answers"]}, fh)

print(json.dumps({"event": "progress", "pct": 90, "msg": "marker written"}))
print(json.dumps({"event": "done"}))
