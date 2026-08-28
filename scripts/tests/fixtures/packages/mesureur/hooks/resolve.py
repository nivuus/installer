#!/usr/bin/env python3
"""Resolve hook that measures something the reboot will make unmeasurable.

Stands in for the console's real case (the size of a disk about to be bound
to vfio-pci) with two facts chosen to pin both halves of the precedence rule
in packages/facts.py:

  - `pre_reboot_measure` is a key no hardware detection ever produces, so it
    must survive all the way into the hw activate receives;
  - `total_cpus` collides with a key the fresh snapshot DOES produce, so it
    must be dropped there - a fact describes the world before the reboot and
    never overrides a measurement that can still be taken.
"""
import json
import sys

json.load(sys.stdin)

print(json.dumps({"event": "progress", "pct": 50, "msg": "measuring"}))
print(json.dumps({"event": "facts", "facts": {
    "pre_reboot_measure": 4242,
    "total_cpus": 999,
}}))
print(json.dumps({"event": "done"}))
