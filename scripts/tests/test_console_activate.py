#!/usr/bin/env python3
"""Arming must be a symlink, must be idempotent, and must never dangle.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES; systemd ignores them with "is not a symlink, ignoring".
That is the failure this asserts against: a unit that looks enabled and
is not.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "console", "hooks", "activate.py")

CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": "console"},
    "hw": {}, "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": False},
})

LINKS = {
    "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket":
        "/etc/systemd/system/vm-trigger-47984.socket",
    "etc/systemd/system/sockets.target.wants/vm-trigger-47989.socket":
        "/etc/systemd/system/vm-trigger-47989.socket",
    "etc/systemd/system/timers.target.wants/vm-idle-shutdown.timer":
        "/etc/systemd/system/vm-idle-shutdown.timer",
}

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def run(root):
    return subprocess.run(
        [sys.executable, HOOK, "--phase", "activate", "--root", root],
        input=CTX, capture_output=True, text=True, cwd=ROOT)


# A target where install has run: the unit files are present.
with tempfile.TemporaryDirectory() as root:
    units = os.path.join(root, "etc/systemd/system")
    os.makedirs(units)
    for name in ("vm-trigger-47984.socket", "vm-trigger-47989.socket",
                 "vm-idle-shutdown.timer"):
        open(os.path.join(units, name), "w").write("[Unit]\n")

    proc = run(root)
    check(f"activate succeeds (rc={proc.returncode})", proc.returncode == 0)
    for rel, target in LINKS.items():
        path = os.path.join(root, rel)
        check(f"{rel} is a symlink", os.path.islink(path))
        if os.path.islink(path):
            check(f"{rel} points at {target}", os.readlink(path) == target)

    # Idempotent: an interrupted activation retries at the next boot, and
    # the stamp file is written only on success.
    again = run(root)
    check(f"activate is idempotent (rc={again.returncode})",
          again.returncode == 0)

# A target where a unit is missing: refuse rather than dangle.
with tempfile.TemporaryDirectory() as root:
    os.makedirs(os.path.join(root, "etc/systemd/system"))
    proc = run(root)
    check("a missing unit is refused, not linked", proc.returncode != 0)
    check("the refusal names the missing unit",
          "vm-trigger-47984.socket" in (proc.stderr or ""))
    dangling = os.path.join(
        root, "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket")
    check("no dangling link is left behind", not os.path.lexists(dangling))

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - arming is a real symlink, idempotent, and never dangles")
