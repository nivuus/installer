#!/usr/bin/env python3
"""Every reader of the guest admin password names the file activate writes.

console/guest_steps.py's `secrets` step writes the password under the guest
workdir. winrm_exec.py and build.py used to default to a hand-made file under
/root/.config/nivuus/ that activate never writes, and retro_sync.py inherited
that default: on a console installed through activate it failed with
"password file not found", and on a host that still had the old file it used
a stale password.

The expected path is derived from guest_steps itself (its workdir default and
its secret file names), never retyped here.

Run: python3 console/tests/test_console_guest_secret_path.py
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
GUEST = CONSOLE / "guest"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(GUEST))

# The defaults are read at import time; the developer's own environment must
# not decide what this suite sees.
os.environ.pop("GUEST_PASS_FILE", None)

import guest_steps  # noqa: E402

EXPECTED = os.path.join(guest_steps.DEFAULT_GUEST_WORKDIR, "secrets",
                        guest_steps.SECRET_FILES["admin_password"])

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- retro_sync: the child winrm_exec.py is told where the password is ---- #
import retro_sync  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    record = pathlib.Path(d) / "env"
    fake = pathlib.Path(d) / "winrm_exec.py"
    fake.write_text(
        "import os, sys\n"
        f"open({str(record)!r}, 'w').write(os.environ.get('GUEST_PASS_FILE', '<unset>'))\n"
    )
    real_exec = retro_sync.WINRM_EXEC
    retro_sync.WINRM_EXEC = fake
    try:
        rc, _out, _err = retro_sync._winrm_run("ps", "hostname")
        check("the fake client ran", rc, 0)
        check("retro_sync hands its child the password file activate writes",
              record.read_text(), EXPECTED)

        os.environ["GUEST_PASS_FILE"] = "/elsewhere/admin.pass"
        try:
            retro_sync._winrm_run("ps", "hostname")
        finally:
            del os.environ["GUEST_PASS_FILE"]
        check("an explicit GUEST_PASS_FILE from the operator still wins",
              record.read_text(), "/elsewhere/admin.pass")
    finally:
        retro_sync.WINRM_EXEC = real_exec

# --- winrm_exec.py: its own default is the activate path ------------------ #
import winrm_exec  # noqa: E402

check("winrm_exec.py defaults to the password file activate writes",
      winrm_exec.PASS_FILE, EXPECTED)

# --- build.py: the manual-run defaults are the activate paths ------------- #
import build  # noqa: E402

args = build.parse_args(["--windows-iso", "x.iso", "--drivers-dir", "d"])
check("build.py --password-file default", args.password_file, EXPECTED)
check("build.py --key-file default", args.key_file,
      os.path.join(guest_steps.DEFAULT_GUEST_WORKDIR, "secrets",
                   guest_steps.SECRET_FILES["ltsc_key"]))
check("build.py --apollo-password-file default", args.apollo_password_file,
      os.path.join(guest_steps.DEFAULT_GUEST_WORKDIR, "secrets",
                   guest_steps.SECRET_FILES["apollo_password"]))

# --- no second copy of the path is left to drift --------------------------- #
for rel in ("guest/winrm_exec.py", "guest/retro_sync.py", "guest/build.py",
            "host/winvm", "host/guest-ready-watch.py"):
    text = (CONSOLE / rel).read_text()
    check(f"{rel} carries no literal of the old hand-made path",
          "/root/.config/nivuus/windows-admin.pass" in text, False)
    check(f"{rel} carries no literal copy of the activate path",
          EXPECTED in text, False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("OK - every reader of the guest admin password names the file activate writes")
