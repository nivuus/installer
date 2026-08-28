#!/usr/bin/env python3
"""Arming must be a symlink, must be idempotent, and must never dangle -
and on the machine being activated it must also take effect NOW.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES; systemd ignores them with "is not a symlink, ignoring".
That is the failure this asserts against: a unit that looks enabled and
is not.

The second half is the same failure one boot later: the activation unit is
WantedBy=multi-user.target, so it runs after sockets.target and
timers.target: linking alone leaves the wake sockets silent and the idle
timer stopped until a further reboot, with the stamp file already written.
Nothing here touches the machine's own systemd: the reload/start path is
driven with a STUB systemctl first on PATH, and the --root runs assert that
no systemctl is invoked at all.
"""
import importlib.util
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


def stub_systemctl(directory, exit_code=0):
    """A systemctl that records its arguments instead of driving systemd.

    Returns the path of the journal it appends to. Running the real thing
    from a test would reload and start units on the machine under test.
    """
    log = os.path.join(directory, "systemctl.log")
    script = os.path.join(directory, "systemctl")
    with open(script, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'echo "$@" >> {log}\n'
                 f"exit {exit_code}\n")
    os.chmod(script, 0o755)
    return log


def stub_env(directory):
    return dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"])


def run(root, env=None):
    return subprocess.run(
        [sys.executable, HOOK, "--phase", "activate", "--root", root],
        input=CTX, capture_output=True, text=True, cwd=ROOT, env=env)


# A target where install has run: the unit files are present.
with tempfile.TemporaryDirectory() as root:
    units = os.path.join(root, "etc/systemd/system")
    os.makedirs(units)
    for name in ("vm-trigger-47984.socket", "vm-trigger-47989.socket",
                 "vm-idle-shutdown.timer"):
        open(os.path.join(units, name), "w").write("[Unit]\n")

    bin_dir = os.path.join(root, "stub-bin")
    os.makedirs(bin_dir)
    log = stub_systemctl(bin_dir)

    proc = run(root, env=stub_env(bin_dir))
    check(f"activate succeeds (rc={proc.returncode})", proc.returncode == 0)

    # A --root that is not "/" describes a target being installed, or a
    # throwaway tree: reloading and starting there would drive the
    # INSTALLER's systemd, not the target's.
    check("a non-/ root never invokes systemctl", not os.path.exists(log))
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

# The reload/start half, driven directly: main() only takes it when --root is
# "/", and running the hook that way would arm this very machine.
spec = importlib.util.spec_from_file_location("console_activate", HOOK)
activate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activate)

check("start_now covers exactly the three armed units",
      sorted(activate.WANTS) == sorted([
          "vm-idle-shutdown.timer",
          "vm-trigger-47984.socket",
          "vm-trigger-47989.socket"]))

with tempfile.TemporaryDirectory() as bin_dir:
    log = stub_systemctl(bin_dir)
    saved = os.environ["PATH"]
    os.environ["PATH"] = bin_dir + os.pathsep + saved
    try:
        broken = activate.start_now(list(activate.WANTS))
    finally:
        os.environ["PATH"] = saved
    check("a successful systemctl reports nothing broken", broken == [])
    recorded = open(log).read().splitlines()
    check("the reload comes first", recorded[:1] == ["daemon-reload"])
    for unit in activate.WANTS:
        check(f"{unit} is started, not merely linked",
              f"start {unit}" in recorded)

with tempfile.TemporaryDirectory() as bin_dir:
    stub_systemctl(bin_dir, exit_code=1)
    saved = os.environ["PATH"]
    os.environ["PATH"] = bin_dir + os.pathsep + saved
    try:
        broken = activate.start_now(list(activate.WANTS))
    finally:
        os.environ["PATH"] = saved
    # A failing systemctl must be REPORTED, never fatal: the links already
    # make the next boot correct, and systemctl is legitimately unusable in
    # constrained environments.
    check("a failing systemctl is reported for every command",
          len(broken) == 1 + len(activate.WANTS))

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - arming is a real symlink, idempotent, never dangles, and "
      "starts the units on the machine it activates")
