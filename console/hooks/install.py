#!/usr/bin/env python3
"""Install phase for the console package: what the manifest cannot declare.

The engine already handles the declarative half - apt packages, kernel
modules, hugepages, and the kernel command line resolve() returned. What is
left is placing files on the target, and one of those placements is not a
matter of taste.

THE APPARMOR TRAP. vm-cpu-partition.sh MUST live under /etc/libvirt/hooks/.
The libvirtd profile grants "/etc/libvirt/hooks/** rmix": hooks run
INHERITING that profile, which allows exec of /bin, /sbin, /usr/bin and
/usr/sbin - but NOT /usr/local/sbin. A partition script installed there dies
at VM start with "/bin/bash: bad interpreter: Permission denied" and NO
AppArmor DENIED line in dmesg. It cost a full VM-start cycle to find once;
it is encoded here so it cannot be rediscovered.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VM_NAME = "Windows"

# The wrappers stay thin so the logic lives in the repo, not in a heredoc.
# They exit 0 unconditionally: a hook that fails must never block a VM start.
CONFINE_WRAPPER = """#!/bin/bash
# Confine the host cgroups to the CPUs the VM does not pin, while it runs.
/etc/libvirt/hooks/vm-cpu-partition.sh confine "$1" \\
    >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
"""

RELEASE_WRAPPER = """#!/bin/bash
# Hand every CPU back once the VM is gone (shutdown or hibernation).
/etc/libvirt/hooks/vm-cpu-partition.sh release "$1" \\
    >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
"""


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def place(src: str, dest: str, mode: int = 0o755) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    os.chmod(dest, mode)


def write(dest: str, content: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        fh.write(content)
    os.chmod(dest, mode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    ctx = json.load(sys.stdin)
    answers = ctx.get("answers") or {}
    root = args.root.rstrip("/") or "/"

    def under(rel: str) -> str:
        return os.path.join(root, rel.lstrip("/"))

    emit({"event": "progress", "pct": 20,
          "msg": "Deploiement des hooks libvirt"})

    # See the module docstring: this path is load-bearing, not stylistic.
    place(os.path.join(HERE, "host", "vm-cpu-partition.sh"),
          under("etc/libvirt/hooks/vm-cpu-partition.sh"))
    base = f"etc/libvirt/hooks/qemu.d/{VM_NAME}"
    write(under(f"{base}/prepare/begin/10-cpu-confine.sh"),
          CONFINE_WRAPPER, mode=0o755)
    write(under(f"{base}/release/end/10-cpu-release.sh"),
          RELEASE_WRAPPER, mode=0o755)

    emit({"event": "progress", "pct": 50, "msg": "Deploiement des scripts hote"})
    place(os.path.join(HERE, "host", "vm-wake-gate.py"),
          under("usr/local/sbin/vm-wake-gate.py"))
    place(os.path.join(HERE, "host", "handle-vm-start.sh"),
          under("usr/local/sbin/handle-vm-start.sh"))
    place(os.path.join(HERE, "host", "winvm"), under("usr/local/bin/winvm"))

    # The operator's retrogaming choice, recorded durably on the target.
    # windows-guest/build.py reads it much later - possibly by hand, possibly
    # on this very host once it has booted - so it must outlive the installer.
    # An UNCHECKED box writes `false` rather than nothing: "absent" and
    # "declined" would otherwise be indistinguishable to the reader.
    emit({"event": "progress", "pct": 80, "msg": "Choix retrogaming enregistre"})
    write(under("etc/nivuus/retro.json"),
          json.dumps({"enabled": bool(answers.get("retro"))}, indent=2) + "\n")

    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
