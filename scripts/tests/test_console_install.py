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
import configparser
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


def load_unit(path):
    """systemd units are INI. Parsing them - rather than searching the raw
    text - is what makes an assertion about a DIRECTIVE rather than about a
    string that may well be commented out.

    strict=False: systemd tolerates a repeated key (later wins), configparser
    raises on it by default.
    optionxform=str: configparser lowercases keys, and systemd directives are
    case-sensitive - StartLimitIntervalSec would silently become
    startlimitintervalsec.

    Recopied from scripts/tests/test_console_wake_units.py rather than
    imported: suites in this repo are standalone scripts.
    """
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


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

    # The dispatcher is the load-bearing one: without it libvirt runs no
    # hook at all, so the two CPU wrappers install DOES write are never
    # executed. It was missing for the whole of phase 2a.
    executables = [
        "etc/libvirt/hooks/qemu",
        "etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh",
        "etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh",
        "etc/libvirt/hooks/qemu.d/Windows/started/begin/rules.sh",
        "etc/libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh",
        "usr/local/sbin/vm-idle-shutdown.sh",
    ]
    for rel in executables:
        check(f"{rel} depose", (root / rel).is_file(), True)
        check(f"{rel} executable", os.access(root / rel, os.X_OK), True)

    # Presence and the execute bit say nothing about WHICH file landed where.
    # Swapping the two rules.sh entries in HOOK_FILES would start the VM
    # without its forward-ports and leave the wake socket exposed with no
    # DNAT in front of it - and every check above would still pass. Comparing
    # bytes closes that for the whole table at once, not just for this pair.
    for src, dest in (("libvirt/hooks/qemu.d/Windows/started/begin/rules.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/started/begin/rules.sh"),
                      ("libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh"),
                      ("libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh"),
                      ("libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh"),
                      ("libvirt/hooks/qemu", "etc/libvirt/hooks/qemu"),
                      ("vm-idle-shutdown.sh", "usr/local/sbin/vm-idle-shutdown.sh")):
        origin = CONSOLE / "host" / src
        target = root / dest
        try:
            same = target.read_bytes() == origin.read_bytes()
        except FileNotFoundError as exc:
            check(f"{dest} est bien la copie de host/{src}",
                  f"fichier absent: {exc.filename}", "fichiers presents")
            continue
        check(f"{dest} est bien la copie de host/{src}", same, True)

    # Units are data, not programs. Mode is not asserted - only presence -
    # because a unit with the execute bit still works; what must not happen
    # is a unit missing while the package claims the cycle is deployed.
    units = [
        "etc/systemd/system/vm-trigger-47984.socket",
        "etc/systemd/system/vm-trigger-47984.service",
        "etc/systemd/system/vm-trigger-47989.socket",
        "etc/systemd/system/vm-trigger-47989.service",
        "etc/systemd/system/vm-idle-shutdown.service",
        "etc/systemd/system/vm-idle-shutdown.timer",
        "etc/systemd/system/vm-trigger-47984.service.d/no-start-limit.conf",
        "etc/systemd/system/vm-trigger-47989.service.d/no-start-limit.conf",
    ]
    for rel in units:
        check(f"{rel} depose", (root / rel).is_file(), True)

    # The drop-in must reach BOTH services: systemd reads it from each
    # unit's own .d/ directory, so one copy enables the limit on the other.
    # Parsed as INI, not searched as text: a substring check would also pass
    # on a commented-out line. The section/key lookup is guarded: its
    # absence (missing file, or a file present but empty/malformed) must
    # surface as a named failure here, not as an uncaught KeyError that
    # would abort the script before the retro blocks below ever run - the
    # file's own presence is already asserted above by the "units" loop.
    for port in ("47984", "47989"):
        dropin = root / ("etc/systemd/system/vm-trigger-"
                         f"{port}.service.d/no-start-limit.conf")
        parser = load_unit(dropin)
        try:
            value = parser["Unit"]["StartLimitIntervalSec"]
        except KeyError:
            check(f"le drop-in {port} desarme la limite de demarrage",
                  "[Unit] StartLimitIntervalSec absent", "0")
            continue
        check(f"le drop-in {port} desarme la limite de demarrage",
              value, "0")

    # install WRITES; activate ARMS. A wake socket armed here would listen
    # on 0.0.0.0 for a VM that does not exist yet - and the stopped/end
    # rules.sh hook removes the forward-ports precisely then, so the DNAT
    # that would otherwise shadow it is gone.
    for wants in ("sockets.target.wants", "timers.target.wants"):
        check(f"install ne cree aucun lien dans {wants}",
              (root / "etc/systemd/system" / wants).exists(), False)

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

# retro absente du contexte : meme regle - le temoin dit non, pas rien
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    del ctx["answers"]["retro"]
    subprocess.run([sys.executable, str(HOOK), "--phase", "install",
                    "--root", str(root)],
                   input=json.dumps(ctx), capture_output=True, text=True,
                   cwd=str(CONSOLE))
    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit non quand retro est absente", marker["enabled"],
          False)

# bool("false") est True en Python - le meme piege de coercion deja corrige
# une fois sur 'required' dans packages/wizard.py. Une chaine, quel que soit
# son sens de lecture, doit etre refusee, jamais interpretee ; un nombre de
# meme. La refuser signifie : sortir non-zero, ne rien ecrire, et nommer la
# cle en cause.
for bad_value in ("false", "true", 1):
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        ctx = json.loads(CTX)
        ctx["answers"]["retro"] = bad_value
        proc = subprocess.run(
            [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
            input=json.dumps(ctx), capture_output=True, text=True,
            cwd=str(CONSOLE))
        check(f"retro={bad_value!r} : le hook sort non-zero",
              proc.returncode != 0, True)
        check(f"retro={bad_value!r} : erreur nomme 'retro'",
              "retro" in proc.stderr, True)
        check(f"retro={bad_value!r} : aucun temoin ecrit",
              (root / "etc/nivuus/retro.json").exists(), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console install tests passed")
