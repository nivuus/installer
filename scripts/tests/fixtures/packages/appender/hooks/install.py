#!/usr/bin/env python3
"""Fixture install hook that APPENDS, so replaying it corrupts its own output."""

import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

json.load(sys.stdin)
target = os.path.join(args.root, "etc/nivuus-appender.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "a") as handle:
    handle.write("entry\n")

print(json.dumps({"event": "done"}))
