#!/usr/bin/env python3
"""The repository carries the whole VM lifecycle, and carries no dead code.

Two failures this guards against, both already observed in this project:
a script that exists only as a deployed file on one host and is lost the
day that host is reinstalled (handle-vm-start.sh, until 2026-08-24), and
placeholder hooks kept around long enough that documentation starts
promising them (the three hugepage stubs).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONSOLE = os.path.join(ROOT, "console")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# The idle half of wake-on-demand must be versioned, not merely deployed.
idle = os.path.join(CONSOLE, "host", "vm-idle-shutdown.sh")
check("vm-idle-shutdown.sh is versioned", os.path.isfile(idle))
if os.path.isfile(idle):
    head = open(idle).readline()
    check("vm-idle-shutdown.sh starts with a shebang", head.startswith("#!"))

for unit in ("vm-idle-shutdown.service", "vm-idle-shutdown.timer"):
    check(f"{unit} is versioned",
          os.path.isfile(os.path.join(CONSOLE, "host", "systemd", unit)))

# The three hugepage hooks were two-line no-ops that documentation kept
# promising. Naming them explicitly is the only non-arbitrary guard: the
# generic form of this check - "does this file do something useful" - has
# no computable definition, since `exit 0` is syntactically code.
removed = [
    "started/begin/00-set-hugepages.sh",
    "stopped/end/00-hugepages-fix.sh",
    "stopped/end/hugepages-reset.sh",
]
hooks_dir = os.path.join(CONSOLE, "host", "libvirt", "hooks", "qemu.d")
for rel in removed:
    path = os.path.join(hooks_dir, "Windows", rel)
    check(f"the removed stub {rel} has not come back", not os.path.exists(path))

# No placeholder hooks: a two-line no-op is worse than an absent file,
# because documentation reads the filename and promises behaviour.
for dirpath, _dirnames, filenames in os.walk(hooks_dir):
    for name in filenames:
        path = os.path.join(dirpath, name)
        body = [l for l in open(path).read().splitlines()
                if l.strip() and not l.strip().startswith("#")]
        rel = os.path.relpath(path, ROOT)
        check(f"{rel} does something", bool(body))

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the repository carries the full lifecycle and no placeholder hooks")
