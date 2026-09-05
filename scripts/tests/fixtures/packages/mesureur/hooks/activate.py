#!/usr/bin/env python3
"""Activate hook that records the `hw` it was handed, verbatim.

Written to the path in MESUREUR_HW_OUT, so the test asserts on what the hook
REALLY received through the pipe - not on what the caller believed it passed.
"""
import json
import os
import sys

ctx = json.load(sys.stdin)
out = os.environ.get("MESUREUR_HW_OUT")
if out:
    with open(out, "w") as fh:
        json.dump(ctx.get("hw") or {}, fh)

print(json.dumps({"event": "done"}))
