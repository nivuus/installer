#!/usr/bin/env python3
"""Demo refusal: this is how a package says no BEFORE anything is written."""
import json
import sys

json.load(sys.stdin)
print(json.dumps({"event": "refuse",
                  "reason": "aucun NVMe dédié correctement isolé"}))
