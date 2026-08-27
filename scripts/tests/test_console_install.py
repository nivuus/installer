#!/usr/bin/env python3
"""Tests for the console package's install hook.

It asserts ARTEFACTS under a temporary root, never calls: the whole point of
this hook is what it leaves on the target filesystem, and a test that mocked
the copying would prove nothing about it.

The AppArmor constraint is the one that matters most here and is invisible
from the code: the libvirtd profile grants "/etc/libvirt/hooks/** rmix", so
a hook runs INHERITING that profile, which allows exec of /bin, /sbin,
/usr/bin and /usr/sbin - but NOT /usr/local/sbin. A partition script
installed there dies at VM start with a misleading "bad interpreter:
Permission denied" and no DENIED line in dmesg.

Run: python3 scripts/tests/test_console_install.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
HOOK = CONSOLE / "hooks" / "install.py"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": str(CONSOLE)},
    "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
    "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": True,
                "admin_password": "hunter2hunter2"},
})

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=CTX, capture_output=True, text=True, cwd=str(CONSOLE))
    check("le hook sort 0", proc.returncode, 0)

    partition = root / "etc/libvirt/hooks/vm-cpu-partition.sh"
    check("le script de partitionnement est sous /etc/libvirt/hooks",
          partition.is_file(), True)
    check("il est executable", os.access(partition, os.X_OK), True)
    check("il n est PAS sous /usr/local/sbin (piege AppArmor)",
          (root / "usr/local/sbin/vm-cpu-partition.sh").exists(), False)

    for phase, name in (("prepare/begin", "10-cpu-confine.sh"),
                        ("release/end", "10-cpu-release.sh")):
        w = root / f"etc/libvirt/hooks/qemu.d/Windows/{phase}/{name}"
        check(f"wrapper {name} depose", w.is_file(), True)
        check(f"wrapper {name} executable", os.access(w, os.X_OK), True)
        check(f"wrapper {name} appelle /etc/libvirt/hooks",
              "/etc/libvirt/hooks/vm-cpu-partition.sh" in w.read_text(), True)

    for rel in ("usr/local/sbin/vm-wake-gate.py",
                "usr/local/sbin/handle-vm-start.sh",
                "usr/local/bin/winvm"):
        check(f"{rel} depose", (root / rel).is_file(), True)
        check(f"{rel} executable", os.access(root / rel, os.X_OK), True)

    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit oui", marker["enabled"], True)

# retro decoche : le temoin doit dire non, pas disparaitre
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    ctx["answers"]["retro"] = False
    subprocess.run([sys.executable, str(HOOK), "--phase", "install",
                    "--root", str(root)],
                   input=json.dumps(ctx), capture_output=True, text=True,
                   cwd=str(CONSOLE))
    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit non", marker["enabled"], False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console install tests passed")
