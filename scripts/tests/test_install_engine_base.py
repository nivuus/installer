#!/usr/bin/env python3
"""Tests for the memory guard laid by the base step.

On 2026-09-27 the host hung for 45 minutes: 21 orphaned test workers took
~28 GB, swap was already full, and the kernel thrashed its page cache instead
of killing anything. earlyoom is the guard against that, so every installed
host must get it, whatever the wizard selected - which is why it lives in the
base step and not behind a feature.

Run: python3 scripts/tests/test_install_engine_base.py
"""
import os
import pathlib
import re
import shlex
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(REPO / "scripts" / "tests"))

from steps import chroot_base  # noqa: E402
import regional_fixtures as fx  # noqa: E402

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

# --- the account password never travels on argv ---------------------------- #
# argv is world-readable in /proc, and a failing command's argv lands in the
# StepError the portal streams to the unauthenticated /ws/progress.
calls.clear()
with tempfile.TemporaryDirectory() as tmp:
    pw_hash = "$6$salt$" + "h" * 86
    chroot_base._user({"user": {"username": "alice", "password_hash": pw_hash}},
                      tmp, FakeEmit())
    on_argv = [cmd for cmd, kw in calls if any(pw_hash in part for part in cmd)]
    check("no command carries the password hash on argv", on_argv, [])
    fed = [kw.get("input_text") for cmd, kw in calls if cmd[0] == "chpasswd"]
    check("chpasswd receives user:hash on stdin", fed, [f"alice:{pw_hash}\n"])
    chpasswd = [cmd for cmd, kw in calls if cmd[0] == "chpasswd"]
    check("chpasswd is told the password is already hashed",
          chpasswd, [["chpasswd", "--encrypted"]])

# A clear password goes to chpasswd on stdin and the TARGET hashes it (its PAM
# policy: yescrypt on Debian); the engine imports no hashing module.
calls.clear()
with tempfile.TemporaryDirectory() as tmp:
    secret = "çé 密码 كلمة:pass"
    chroot_base._user({"user": {"username": "alice", "password": secret}},
                      tmp, FakeEmit())
    check("no command carries the clear password on argv",
          [cmd for cmd, kw in calls if any(secret in part for part in cmd)], [])
    check("chpasswd receives user:password on stdin, any script",
          [(cmd, kw.get("input_text")) for cmd, kw in calls if cmd[0] == "chpasswd"],
          [(["chpasswd"], f"alice:{secret}\n")])
check("the engine no longer depends on the crypt module",
      "crypt" in sys.modules, False)

# chpasswd reads one user per line: a line break in the password would set a
# second account's password. Refused before anything runs.
for bad in ("a\nroot:x", "a\rb", "a\x00b"):
    calls.clear()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            chroot_base._user({"user": {"username": "alice", "password": bad}},
                              tmp, FakeEmit())
            failures.append(f"password {bad!r}: no StepError")
        except chroot_base.StepError as exc:
            check(f"password {bad!r}: the error does not quote it", bad in str(exc), False)
        check(f"password {bad!r}: chpasswd never runs",
              [cmd for cmd, kw in calls if cmd[0] == "chpasswd"], [])


# --- sshd and NetworkManager must really be enabled --------------------------- #
def enable_fails(unit):
    """util.chroot_run's contract: a failure raises unless check=False."""
    def fake(target, cmd, **kwargs):
        if cmd == ["systemctl", "enable", unit] and kwargs.get("check", True):
            raise chroot_base.StepError(f"command failed (1): {' '.join(cmd)}")
        return type("R", (), {"returncode": 1 if cmd[-1] == unit else 0})()
    return fake


for unit in ("ssh", "NetworkManager"):
    chroot_base.chroot_run = enable_fails(unit)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            chroot_base._sshd({"user": {"username": "alice"}}, tmp, FakeEmit())
            failures.append(f"systemctl enable {unit} failing: no StepError")
        except chroot_base.StepError as exc:
            check(f"the failure names {unit}", f"enable {unit}" in str(exc), True)
chroot_base.chroot_run = fake_chroot_run

# The suite defaults to trixie when the wizard names none; a named one is kept.
with tempfile.TemporaryDirectory() as tmp:
    chroot_base._apt_sources({}, tmp)
    check("default suite in sources.list",
          (pathlib.Path(tmp) / "etc/apt/sources.list").read_text().split()[2], "trixie")
    chroot_base._apt_sources({"suite": "bookworm"}, tmp)
    check("named suite in sources.list",
          (pathlib.Path(tmp) / "etc/apt/sources.list").read_text().split()[2], "bookworm")

# --- locale, keyboard and time zone: any the target supports ---------------- #
def regional_target(tmp):
    fx.seed(tmp)
    return pathlib.Path(tmp)


calls.clear()
with tempfile.TemporaryDirectory() as tmp:
    target = regional_target(tmp)
    chroot_base._locale_and_time({"locale": "ja_JP.UTF-8", "timezone": "Asia/Tokyo",
                                  "keyboard": {"layout": "jp", "variant": "kana"}},
                                 tmp, FakeEmit())
    check("locale.gen names the locale and its charset",
          (target / "etc/locale.gen").read_text(), "ja_JP.UTF-8 UTF-8\n")
    check("default locale", (target / "etc/default/locale").read_text(),
          "LANG=ja_JP.UTF-8\n")
    keyboard = (target / "etc/default/keyboard").read_text()
    check("keyboard layout", 'XKBLAYOUT="jp"' in keyboard.splitlines(), True)
    check("keyboard variant", 'XKBVARIANT="kana"' in keyboard.splitlines(), True)
    check("time zone link", os.readlink(target / "etc/localtime"),
          "/usr/share/zoneinfo/Asia/Tokyo")
    check("locale-gen is not lenient",
          [kw.get("check", True) for cmd, kw in calls if cmd == ["locale-gen"]], [True])

with tempfile.TemporaryDirectory() as tmp:
    target = regional_target(tmp)
    chroot_base._locale_and_time({}, tmp, FakeEmit())
    check("defaults: en_US.UTF-8, us, Etc/UTC",
          ((target / "etc/default/locale").read_text(),
           'XKBLAYOUT="us"' in (target / "etc/default/keyboard").read_text(),
           os.readlink(target / "etc/localtime")),
          ("LANG=en_US.UTF-8\n", True, "/usr/share/zoneinfo/Etc/UTC"))

for label, cfg, needle in (
        ("an unsupported locale", {"locale": "xx_YY.UTF-8"}, "xx_YY.UTF-8"),
        ("a non-UTF-8 locale", {"locale": "ja_JP.EUC-JP"}, "ja_JP.EUC-JP"),
        ("an unknown layout", {"keyboard": {"layout": "qq"}}, "'qq'"),
        ("a variant of another layout", {"keyboard": {"layout": "us", "variant": "bepo"}},
         "'bepo'"),
        ("a missing time zone", {"timezone": "Mars/Olympus"}, "Mars/Olympus")):
    with tempfile.TemporaryDirectory() as tmp:
        regional_target(tmp)
        try:
            chroot_base._locale_and_time(cfg, tmp, FakeEmit())
            failures.append(f"{label}: no StepError")
        except chroot_base.StepError as exc:
            check(f"{label} is refused by name", needle in str(exc), True)

check("the keyboard packages are core packages",
      {"keyboard-configuration", "console-setup"} <= set(chroot_base.CORE_PACKAGES), True)

if failures:
    print("FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("OK")
