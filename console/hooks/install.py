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

# The engine launches this file by absolute path (cwd=console/), so Python
# only puts this script's OWN directory (console/hooks/) on sys.path - not
# HERE (console/), where retro.py lives. Without this, `import retro` below
# fails with ModuleNotFoundError the moment the engine (not a developer
# running the file from console/) invokes the hook.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from retro import retro_state_path  # noqa: E402

VM_NAME = "Windows"

HOOK_BASE = f"etc/libvirt/hooks/qemu.d/{VM_NAME}"

# (source under console/, destination under the target root).
# Every entry lands executable; see place()'s default mode.
HOOK_FILES = [
    ("host/libvirt/hooks/qemu", "etc/libvirt/hooks/qemu"),
    # THE APPARMOR TRAP (see module docstring): this one path is not a
    # matter of taste.
    ("host/vm-cpu-partition.sh", "etc/libvirt/hooks/vm-cpu-partition.sh"),
    # The two CPU wrappers are VERSIONED files, not heredocs. Each does three
    # things, and only the first survived the heredocs they replace: the cpuset
    # partitioning, `systemctl start nivuus-cpu-mode@{gaming,idle}.service` -
    # a PUBLIC CONTRACT of this repository, honoured by nobody while the
    # heredocs were what landed - and the Tdarr CPU node stop/start.
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/prepare/begin/10-cpu-confine.sh",
     f"{HOOK_BASE}/prepare/begin/10-cpu-confine.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/release/end/10-cpu-release.sh",
     f"{HOOK_BASE}/release/end/10-cpu-release.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/prepare/begin/bind-vfio-gpu.sh",
     f"{HOOK_BASE}/prepare/begin/bind-vfio-gpu.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/release/end/rebind-host-gpu.sh",
     f"{HOOK_BASE}/release/end/rebind-host-gpu.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/started/begin/rules.sh",
     f"{HOOK_BASE}/started/begin/rules.sh"),
    (f"host/libvirt/hooks/qemu.d/{VM_NAME}/stopped/end/rules.sh",
     f"{HOOK_BASE}/stopped/end/rules.sh"),
]

HOST_SCRIPTS = [
    ("host/vm-wake-gate.py", "usr/local/sbin/vm-wake-gate.py"),
    ("host/handle-vm-start.sh", "usr/local/sbin/handle-vm-start.sh"),
    ("host/vm-idle-shutdown.sh", "usr/local/sbin/vm-idle-shutdown.sh"),
    ("host/winvm", "usr/local/bin/winvm"),
    ("host/guest-ready-watch.py", "usr/local/sbin/guest-ready-watch.py"),
]

# Units are DATA, not programs: mode 0644. A unit file with the execute bit
# still works, but the difference is how systemd's own packages ship them.
UNITS = [
    "vm-trigger-47984.socket", "vm-trigger-47984.service",
    "vm-trigger-47989.socket", "vm-trigger-47989.service",
    "vm-idle-shutdown.service", "vm-idle-shutdown.timer",
    "nivuus-guest-ready.service", "nivuus-guest-ready.timer",
]

# The same drop-in serves both wake services; systemd reads it from each
# unit's own .d/ directory, so it is copied twice under its canonical name.
DROPIN_SRC = "host/systemd/vm-trigger-no-start-limit.conf"
DROPIN_TARGETS = [
    "etc/systemd/system/vm-trigger-47984.service.d/no-start-limit.conf",
    "etc/systemd/system/vm-trigger-47989.service.d/no-start-limit.conf",
]


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


def retro_choice(answers: dict) -> bool:
    """The retrogaming checkbox, accepted only as a genuine boolean.

    `bool("false")` is True in Python - the same coercion trap this project
    already hit once, on `required: bool(item.get("required", False))` in
    packages/wizard.py, and fixed there with an explicit isinstance check.
    The wizard's own validator (_check_value in packages/wizard.py) already
    refuses a non-boolean 'retro' answer - but this hook reads its context
    from stdin, and a hand-written config.json driving the engine outside
    the portal (the standalone path phase 2b exists to enable) never passes
    through that validator. So the same rule is enforced again here,
    independently: a value this hook cannot interpret means the caller and
    the package disagree about the contract, and silently picking a reading
    is exactly how the original bug happened. Raises ValueError naming the
    key and the offending value; never coerces.
    """
    value = answers.get("retro", False)
    if not isinstance(value, bool):
        raise ValueError(f"answer 'retro' expects true/false, got {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    ctx = json.load(sys.stdin)
    answers = ctx.get("answers") or {}
    root = args.root.rstrip("/") or "/"

    # Fail fast, before a single byte lands on the target: a caller and the
    # package disagreeing about 'retro' is a contract error, not something
    # to work around mid-placement.
    try:
        retro_enabled = retro_choice(answers)
    except ValueError as exc:
        print(f"console install: {exc}", file=sys.stderr)
        return 1

    def under(rel: str) -> str:
        return os.path.join(root, rel.lstrip("/"))

    emit({"event": "progress", "pct": 20,
          "msg": "Deploiement des hooks libvirt"})
    for src, dest in HOOK_FILES:
        place(os.path.join(HERE, src), under(dest))

    emit({"event": "progress", "pct": 50, "msg": "Deploiement des scripts hote"})
    for src, dest in HOST_SCRIPTS:
        place(os.path.join(HERE, src), under(dest))

    # Placed, deliberately NOT enabled: arming a 0.0.0.0 wake socket for a
    # VM that does not exist yet would be exposure with no counterpart. The
    # activate phase arms them, once there is something to wake.
    emit({"event": "progress", "pct": 65, "msg": "Unites systemd posees"})
    for unit in UNITS:
        place(os.path.join(HERE, "host", "systemd", unit),
              under(f"etc/systemd/system/{unit}"), mode=0o644)
    for dest in DROPIN_TARGETS:
        place(os.path.join(HERE, DROPIN_SRC), under(dest), mode=0o644)

    # The operator's retrogaming choice, recorded durably on the target.
    # console/guest/build.py reads it much later - possibly by hand, possibly
    # on this very host once it has booted - so it must outlive the installer.
    # An UNCHECKED box writes `false` rather than nothing: "absent" and
    # "declined" would otherwise be indistinguishable to the reader. The path
    # comes from retro.py (imported above), the single source both this
    # writer and build.py's reader agree on.
    emit({"event": "progress", "pct": 80, "msg": "Choix retrogaming enregistre"})
    write(retro_state_path(root),
          json.dumps({"enabled": retro_enabled}, indent=2) + "\n")

    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
