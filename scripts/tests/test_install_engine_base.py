#!/usr/bin/env python3
"""Tests for the memory guard laid by the base step.

On 2026-09-27 the host hung for 45 minutes: 21 orphaned test workers took
~28 GB, swap was already full, and the kernel thrashed its page cache instead
of killing anything. earlyoom is the guard against that, so every installed
host must get it, whatever the wizard selected - which is why it lives in the
base step and not behind a feature.

Run: python3 scripts/tests/test_install_engine_base.py
"""
import pathlib
import re
import shlex
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import chroot_base  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def info(self, step, pct, msg):
        pass


calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append((cmd, kwargs))

    class R:
        returncode = 0
    return R()


chroot_base.chroot_run = fake_chroot_run

# The package goes through the core apt path, which fails the install loudly.
check("earlyoom in CORE_PACKAGES", "earlyoom" in chroot_base.CORE_PACKAGES, True)

with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    chroot_base._memory_guard(str(target), FakeEmit())

    defaults = target / "etc/default/earlyoom"
    check("defaults file written", defaults.is_file(), True)
    text = defaults.read_text()

    # The file is sourced by /bin/sh and by systemd's EnvironmentFile: parse it
    # the way the shell does, then split the value the way ExecStart does.
    assigns = [line for line in text.splitlines()
               if line and not line.startswith("#")]
    check("one assignment", len(assigns), 1)
    name, _, value = assigns[0].partition("=")
    check("variable name", name, "EARLYOOM_ARGS")
    args = shlex.split(shlex.split(value)[0])

    def opt(flag):
        return args[args.index(flag) + 1] if flag in args else None

    # SIGTERM under 10 % available, SIGKILL under 5 %.
    check("-m", opt("-m"), "10,5")
    # Swap ignored for both signals: without the ",100" the SIGKILL limit
    # defaults to half the SIGTERM one, i.e. it would wait for 50 % free swap.
    check("-s", opt("-s"), "100,100")
    check("no --prefer", "--prefer" in args, False)

    avoid = opt("--avoid")
    check("--avoid present", avoid is not None, True)
    pattern = re.compile(avoid)
    for comm in ("qemu-system-x86", "dockerd", "containerd-shim", "sshd",
                 "systemd", "systemd-journal", "NetworkManager", "pppd"):
        check(f"avoid matches {comm}", bool(pattern.search(comm)), True)
    # The runaway of 2026-09-27, and a process that merely contains a name.
    for comm in ("node (vitest 1)", "python3", "sshd-session", "mysystemd"):
        check(f"avoid spares {comm}", bool(pattern.search(comm)), False)
    # earlyoom matches /proc/pid/comm, truncated to 15 bytes by the kernel: a
    # longer name in the list could never match and would protect nothing.
    names = re.fullmatch(r"\^\((.*)\)\$", avoid).group(1).split("|")
    too_long = [n for n in names if len(n.encode()) > 15]
    check("avoid names fit in comm", too_long, [])

    enabled = [cmd for cmd, kw in calls
               if cmd == ["systemctl", "enable", "earlyoom"]]
    check("earlyoom enabled", len(enabled), 1)
    lenient = [cmd for cmd, kw in calls if kw.get("check") is False]
    check("enable is not lenient", lenient, [])

if failures:
    print("FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("OK")
