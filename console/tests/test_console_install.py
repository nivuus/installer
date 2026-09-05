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

Run: python3 console/tests/test_console_install.py
"""
import configparser
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
HOOK = CONSOLE / "hooks" / "install.py"

# Only for deriving the on-target copy path the SAME way install.py does -
# never to call guest_steps functions directly. That distinction is the
# whole point of this suite (see the module docstring): it asserts what the
# hook subprocess LEAVES on the filesystem, never mocks its internals.
sys.path.insert(0, str(CONSOLE))
import guest_steps  # noqa: E402

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

    Recopied from console/tests/test_console_wake_units.py rather than
    imported: suites in this repo are standalone scripts.
    """
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


# Stands in for the wizard's LIVE-MEDIUM Windows ISO: a small file (never
# the real ~4.8 GB medium - see the plan's own ban on copying that) that
# install.py is asked to copy onto the target. Not context-managed: it must
# outlive every `with tempfile.TemporaryDirectory() as tmp:` block below,
# which each stand in for a fresh install target. Cleaned up at the bottom.
FIXTURES = pathlib.Path(tempfile.mkdtemp(prefix="nivuus-console-install-test-"))
SOURCE_ISO = FIXTURES / "live-medium.iso"
SOURCE_ISO.write_bytes(b"NIVUUS-FAKE-WINDOWS-MEDIUM" * 200)  # a few KB, not GB

# Where install.py is expected to place the copy, under an install root:
# derived from guest_steps' own convention (DEFAULT_GUEST_WORKDIR +
# windows_media_path), never hardcoded, so a change to either stays caught
# here rather than silently drifting between the hook and this suite.
COPY_REL = str(guest_steps.windows_media_path(
    guest_steps.DEFAULT_GUEST_WORKDIR)).lstrip("/")

CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": str(CONSOLE)},
    "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
    "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": True,
                "admin_password": "hunter2hunter2",
                "windows_iso": str(SOURCE_ISO)},
})

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=CTX, capture_output=True, text=True, cwd=str(CONSOLE))
    check("le hook sort 0", proc.returncode, 0)

    # THE POINT OF THIS TASK: the Windows medium must survive the reboot.
    # install is the only phase that ever sees both the live medium and the
    # target - see console/hooks/install.py's own module docstring - so it
    # is the only place this copy can happen; a copy that never lands here
    # means activate, after the reboot, looks for a file that no longer
    # exists anywhere.
    copy = root / COPY_REL
    check("le media Windows est copie sous la cible", copy.is_file(), True)
    check("le contenu copie est identique a la source",
          copy.read_bytes(), SOURCE_ISO.read_bytes())

    partition = root / "etc/libvirt/hooks/vm-cpu-partition.sh"
    check("le script de partitionnement est sous /etc/libvirt/hooks",
          partition.is_file(), True)
    check("il est executable", os.access(partition, os.X_OK), True)
    check("il n est PAS sous /usr/local/sbin (piege AppArmor)",
          (root / "usr/local/sbin/vm-cpu-partition.sh").exists(), False)

    # The two CPU wrappers are copied from the repository, not generated:
    # the heredocs they replace called the partition script and stopped
    # there, dropping `nivuus-cpu-mode@{gaming,idle}.service` - named a
    # PUBLIC CONTRACT of this repository in CLAUDE.md, deployed on the host
    # side by install-engine/steps/features.py, and honoured by no code at
    # all while the heredocs were what landed. Asserting the unit name here
    # is what makes the contract two-sided; byte identity with the source is
    # asserted with the rest of the table further down.
    for phase, name, mode in (("prepare/begin", "10-cpu-confine.sh", "gaming"),
                              ("release/end", "10-cpu-release.sh", "idle")):
        w = root / f"etc/libvirt/hooks/qemu.d/Windows/{phase}/{name}"
        check(f"wrapper {name} depose", w.is_file(), True)
        check(f"wrapper {name} executable", os.access(w, os.X_OK), True)
        body = w.read_text()
        check(f"wrapper {name} appelle /etc/libvirt/hooks",
              "/etc/libvirt/hooks/vm-cpu-partition.sh" in body, True)
        check(f"wrapper {name} honore nivuus-cpu-mode@{mode}",
              f"nivuus-cpu-mode@{mode}.service" in body, True)

    for rel in ("usr/local/sbin/vm-wake-gate.py",
                "usr/local/sbin/handle-vm-start.sh",
                "usr/local/bin/winvm",
                "usr/local/sbin/guest-ready-watch.py"):
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
                      ("libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh"),
                      ("libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh",
                       "etc/libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh"),
                      ("libvirt/hooks/qemu", "etc/libvirt/hooks/qemu"),
                      ("vm-idle-shutdown.sh", "usr/local/sbin/vm-idle-shutdown.sh"),
                      ("guest-ready-watch.py",
                       "usr/local/sbin/guest-ready-watch.py")):
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
        "etc/systemd/system/nivuus-guest-ready.service",
        "etc/systemd/system/nivuus-guest-ready.timer",
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

# --- le media Windows : refus tot, jamais a moitie fait ------------------ #
# The wizard's 'windows_iso' answer names a file on the LIVE medium this
# hook runs from. It must be refused HERE - at install, while the operator
# is still at the wizard - if it cannot be read, never discovered only
# after the reboot when activate finds nothing at the copy's path.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    absent = str(root / "does-not-exist.iso")
    ctx["answers"]["windows_iso"] = absent
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=json.dumps(ctx), capture_output=True, text=True, cwd=str(CONSOLE))
    check("un media Windows absent fait sortir le hook non-zero",
          proc.returncode != 0, True)
    check("le refus nomme le media absent", absent in proc.stderr, True)
    check("le media n est pas copie", (root / COPY_REL).exists(), False)
    # Fail-fast means fail EARLY: nothing else landed either, so an
    # operator staring at a refused install sees no half-finished state.
    check("aucun hook libvirt n est pose quand le media est refuse",
          (root / "etc/libvirt/hooks/qemu").exists(), False)

# "Not enough free space" is proven at the guest_steps.py unit level (see
# test_console_guest_steps.py's copy_windows_medium tests), where free_bytes
# is injectable. copy_windows_medium is the exact same function either way -
# only the plumbing differs, and reproducing the refusal here would mean
# actually filling a real filesystem to capacity, which this suite does not
# do to any real disk.

# An already-complete copy (same size) must NOT be redone. Proven the same
# way guest_steps.py proves it: pre-place a same-SIZE but WRONG-CONTENT file
# at the copy's path, run the hook, and check the wrong content survives -
# a real re-copy would have replaced it with SOURCE_ISO's actual bytes.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    copy = root / COPY_REL
    copy.parent.mkdir(parents=True, exist_ok=True)
    stale = b"\x00" * len(SOURCE_ISO.read_bytes())
    copy.write_bytes(stale)
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=CTX, capture_output=True, text=True, cwd=str(CONSOLE))
    check("le hook sort 0 quand la copie est deja complete", proc.returncode, 0)
    check("une copie deja complete (meme taille) n est pas refaite",
          copy.read_bytes(), stale)

shutil.rmtree(FIXTURES, ignore_errors=True)

# The byte-for-byte comparison above only protects the TRANSPORT: a regression
# in the SOURCE reaches the target intact and every assertion still passes.
# Measured: neutralising the return-code propagation in the dispatcher
# (`if [ "$HOOK_NAME" = "prepare" ]` -> `if false`) left the whole suite
# green. That branch is the ONLY thing that lets bind-vfio-gpu.sh REFUSE a VM
# start while a process still holds /dev/nvidia*; without it the gaming VM
# boots without its GPU. So it is exercised, not read: a throwaway hook tree
# with a failing hook, run through the versioned dispatcher.
#
# The tree is synthetic and named TestVM on purpose - pointing the dispatcher
# at a deployed Windows/prepare/begin would EXECUTE the real bind-vfio-gpu.sh
# on the machine running the tests.
with tempfile.TemporaryDirectory() as tmp:
    tree = pathlib.Path(tmp)
    dispatcher = tree / "qemu"
    dispatcher.write_bytes((CONSOLE / "host/libvirt/hooks/qemu").read_bytes())
    dispatcher.chmod(0o755)

    # `logger` writes to the host syslog; a stub keeps the suite silent there.
    stub_bin = tree / "bin"
    stub_bin.mkdir()
    (stub_bin / "logger").write_text("#!/bin/sh\nexit 0\n")
    (stub_bin / "logger").chmod(0o755)
    env = dict(os.environ,
               PATH=str(stub_bin) + os.pathsep + os.environ["PATH"])

    def hook(phase, state, code):
        d = tree / "qemu.d/TestVM" / phase / state
        d.mkdir(parents=True, exist_ok=True)
        script = d / "zz-probe.sh"
        script.write_text(f"#!/bin/sh\nexit {code}\n")
        script.chmod(0o755)

    def dispatch(phase, state):
        return subprocess.run([str(dispatcher), "TestVM", phase, state, "-"],
                              capture_output=True, text=True, env=env).returncode

    hook("prepare", "begin", 3)
    check("un hook prepare en echec fait refuser le demarrage",
          dispatch("prepare", "begin"), 3)

    # Same failure on release/stopped must NOT propagate: a non-zero code
    # there only obstructs a teardown libvirt is already committed to.
    hook("release", "end", 3)
    check("un hook release en echec ne bloque pas le demontage",
          dispatch("release", "end"), 0)

    hook("prepare", "begin", 0)
    check("un hook prepare qui reussit laisse passer le demarrage",
          dispatch("prepare", "begin"), 0)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console install tests passed")
