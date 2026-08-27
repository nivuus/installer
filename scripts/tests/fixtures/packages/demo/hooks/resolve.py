#!/usr/bin/env python3
"""Demo resolve hook: read-only, returns the platform block it computed.

Mirrors what a real package does - derive from the hardware what the static
manifest cannot know - without touching anything.
"""
import json
import sys

ctx = json.load(sys.stdin)
gpus = [g for g in ctx["hw"].get("gpus", []) if g.get("discrete")]
ids = [i for g in gpus for i in g.get("ids", [])]

print(json.dumps({"event": "progress", "pct": 50, "msg": "resolving"}))
print(json.dumps({
    "event": "platform",
    "kernel-cmdline": [f"vfio-pci.ids={','.join(ids)}"] if ids else [],
    "modules": ["vfio_iommu_type1"],
    "hugepages-mib": 1024,
}))
print(json.dumps({"event": "done"}))
