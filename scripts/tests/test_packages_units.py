#!/usr/bin/env python3
"""Tests for installer/packages/units.py - the installer's own systemd units.

The units are laid into a scratch directory standing for /etc/systemd/system,
from archives built like a real release (members under ./configs/systemd/).
systemd itself is a fake `busctl` on PATH that logs its arguments, so the
production apply path is driven, not a double of it.

Run: python3 scripts/tests/test_packages_units.py
"""
import os
import pathlib
import stat
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402
from packages import units  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except units.UnitError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UnitError, none raised")


scratch = tempfile.mkdtemp(prefix="nivuus-units-")


def archive(files, extra=None):
    path = os.path.join(scratch, f"release-{len(os.listdir(scratch))}.tar.gz")
    members = {f"./configs/systemd/{k}": v for k, v in files.items()}
    with open(path, "wb") as fh:
        fh.write(fake_github.build_archive(members, extra=extra))
    return path


def machine(present):
    unit_dir = tempfile.mkdtemp(prefix="unit-dir-", dir=scratch)
    for name, content in present.items():
        with open(os.path.join(unit_dir, name), "w") as fh:
            fh.write(content)
    return unit_dir


def read(unit_dir, name):
    with open(os.path.join(unit_dir, name)) as fh:
        return fh.read()


applied = []
release = archive({"nivuus-check.service": "service v2\n",
                   "nivuus-check.timer": "timer v2\n",
                   "nivuus-package-activate@.service": "activate v1\n"})

# Only what the machine runs is laid, only what differs is rewritten.
unit_dir = machine({"nivuus-check.service": "service v1\n",
                    "nivuus-check.timer": "timer v2\n"})
changed = units.refresh(release, unit_dir, applied.append)
check("the stale unit is rewritten", changed, ["nivuus-check.service"])
check("with the release's content", read(unit_dir, "nivuus-check.service"),
      "service v2\n")
check("a unit the machine does not run is not created",
      os.path.exists(os.path.join(unit_dir, "nivuus-package-activate@.service")),
      False)
check("systemd is told once, with what changed", applied,
      [["nivuus-check.service"]])
check("the rewritten unit is 0644",
      stat.S_IMODE(os.stat(os.path.join(unit_dir, "nivuus-check.service")).st_mode),
      0o644)
check("no temporary file is left", sorted(os.listdir(unit_dir)),
      ["nivuus-check.service", "nivuus-check.timer"])

applied.clear()
check("a second run changes nothing",
      units.refresh(release, unit_dir, applied.append), [])
check("and does not disturb systemd", applied, [])

# A unit this machine runs but the release dropped must be seen, not skipped.
unit_dir = machine({"nivuus-check.timer": "timer v1\n"})
check_refused("a unit missing from the release is refused",
              lambda: units.refresh(archive({"nivuus-check.service": "x"}),
                                    unit_dir, applied.append),
              "the release has no configs/systemd/nivuus-check.timer")

# A link in the archive is never followed.
link = tarfile.TarInfo("./configs/systemd/nivuus-check.timer")
link.type = tarfile.SYMTYPE
link.linkname = "/etc/shadow"
check_refused("a unit that is a link in the release is refused",
              lambda: units.refresh(archive({}, extra=[link]), unit_dir,
                                    applied.append),
              "not a regular file")
check("and the laid unit is untouched", read(unit_dir, "nivuus-check.timer"),
      "timer v1\n")

# The production apply path, against a fake busctl.
bindir = tempfile.mkdtemp(prefix="bin-", dir=scratch)
log = os.path.join(scratch, "busctl.log")
busctl = os.path.join(bindir, "busctl")
with open(busctl, "w") as fh:
    fh.write(f"""#!/bin/sh
echo "$*" >> {log}
case "$*" in
  *GetUnitFileState*) echo "s \\"${{FAKE_STATE:-enabled}}\\"" ;;
esac
[ -z "$FAKE_FAIL" ] || {{ echo "Access denied" >&2; exit 1; }}
""")
os.chmod(busctl, 0o755)
os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"


def calls():
    with open(log) as fh:
        lines = [line.split("org.freedesktop.systemd1.Manager ")[1].strip()
                 for line in fh]
    os.unlink(log)
    return lines


units.systemd_apply(["nivuus-check.service", "nivuus-check.timer"])
check("reload, then restart the enabled timer only", calls(),
      ["Reload", "GetUnitFileState s nivuus-check.timer",
       "RestartUnit ss nivuus-check.timer replace"])
os.environ["FAKE_STATE"] = "disabled"
units.systemd_apply(["nivuus-check.timer"])
check("a disabled timer is not started", calls(),
      ["Reload", "GetUnitFileState s nivuus-check.timer"])
del os.environ["FAKE_STATE"]
os.environ["FAKE_FAIL"] = "1"
check_refused("a systemd refusal is reported",
              lambda: units.systemd_apply(["nivuus-check.service"]),
              "systemd Reload failed: Access denied")
del os.environ["FAKE_FAIL"]


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all systemd unit tests passed")
