#!/usr/bin/env python3
"""Resolve hook returning a kernel parameter that carries shell meaning.

/etc/default/grub is sourced as shell by grub-mkconfig, so this must be
refused - and refused while the target disk is still untouched, not at the
bootloader step which runs long after partition() has wiped it.
"""
import json
import sys

json.load(sys.stdin)
print(json.dumps({
    "event": "platform",
    "kernel-cmdline": ["vfio-pci.ids=$(rm -rf /)"],
}))
