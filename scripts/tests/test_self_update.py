#!/usr/bin/env python3
"""Tests for installer/packages/self_update.py - `nivuus update --self`.

The payload is laid into a scratch directory standing for /opt/nivuus, from
archives a local fake GitHub publishes.

Run: python3 scripts/tests/test_self_update.py
"""
import json
import os
import pathlib
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = tempfile.mkdtemp(prefix="nivuus-self-")
for key, rel in (("NIVUUS_STAMP_DIR", "var/lib/nivuus/packages"),
                 ("NIVUUS_CACHE_DIR", "var/cache/nivuus")):
    os.environ[key] = os.path.join(ROOT, rel)
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402

fake = fake_github.start()

from packages import self_update  # noqa: E402
from packages.updater import UpdateError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except UpdateError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UpdateError, none raised")


def payload(version, *, drop=(), installer_link=False):
    """A release archive built from THIS repository's real installer/ tree.

    The smoke check runs the staged CLI, so a stand-in file would prove
    nothing: the payload must be the code that actually ships.
    """
    files = {"README.md": "top level, not laid", "installer/VERSION": version,
             "configs/systemd/nivuus-check.timer": f"timer {version}\n"}
    for sub in ("packages", "common"):
        for path in (REPO / "installer" / sub).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "__pycache__" not in rel and rel not in drop:
                files[rel] = path.read_bytes()
    if installer_link:
        files = {k.replace("installer/", "real/", 1): v for k, v in files.items()}
        link = tarfile.TarInfo("installer")
        link.type = tarfile.SYMTYPE
        link.linkname = "real"
        return fake_github.build_archive(files, extra=[link])
    return fake_github.build_archive(files)


def laid_version():
    with open(os.path.join(root, "VERSION")) as fh:
        return fh.read()


unit_dir = os.path.join(ROOT, "etc", "systemd", "system")
os.makedirs(unit_dir)
with open(os.path.join(unit_dir, "nivuus-check.timer"), "w") as fh:
    fh.write("timer laid by the install engine\n")
applied = []


def update(target=None):
    return self_update.update_self(target or root, unit_dir=unit_dir,
                                   apply=applied.append)


def timer():
    with open(os.path.join(unit_dir, "nivuus-check.timer")) as fh:
        return fh.read()


opt = os.path.join(ROOT, "opt", "nivuus")
root = os.path.join(opt, "installer")
os.makedirs(os.path.join(root, "packages"))
with open(os.path.join(root, "packages", "nivuus_cli.py"), "w") as fh:
    fh.write("# laid by hand\n")

try:
    check("no record reads as 0.0.0", self_update.installed_version(), "0.0.0")
    fake.publish("nivuus/installer", "1.5.0", payload("1.5.0"))
    check("the release is laid, with its units",
          update(), ("1.5.0", ["nivuus-check.timer"]))
    check("the unit carries the release's content", timer(), "timer 1.5.0\n")
    check("and systemd was told", applied, [["nivuus-check.timer"]])
    check("with the release's files", laid_version(), "1.5.0")
    check("the CLI is executable",
          os.access(os.path.join(root, "packages", "nivuus_cli.py"), os.X_OK), True)
    check("only the installer subtree is laid",
          sorted(os.listdir(opt)), ["installer"])
    check("the version is recorded", self_update.installed_version(), "1.5.0")
    with open(os.path.join(os.environ["NIVUUS_STAMP_DIR"], "installer.json")) as fh:
        check("in its own record, not the package state",
              "updated_at" in json.load(fh), True)
    check("a second run lays nothing", update(), (None, []))
    with open(os.path.join(unit_dir, "nivuus-check.timer"), "w") as fh:
        fh.write("timer from an older updater\n")
    check("a current installer still catches its units up",
          update(), (None, ["nivuus-check.timer"]))
    check("from the current release", timer(), "timer 1.5.0\n")

    fake.publish("nivuus/installer", "1.6.0",
                 payload("1.6.0", drop=("installer/packages/answers.py",)))
    check_refused("a payload whose CLI cannot run is refused before the swap",
                  lambda: update(), "does not run from staging")
    check("and the laid copy is untouched", laid_version(), "1.5.0")
    check("nor are its units", timer(), "timer 1.5.0\n")
    check("no leftover beside it", sorted(os.listdir(opt)), ["installer"])

    fake.publish("nivuus/installer", "1.6.0", payload("1.6.0", installer_link=True))
    check_refused("an installer/ that is a link is refused",
                  lambda: update(), "not a plain directory")
    check("the laid copy is still a real directory",
          (os.path.islink(root), laid_version()), (False, "1.5.0"))

    fake.publish("nivuus/installer", "1.6.0", payload("1.6.0"))
    check("a sound release is laid over the previous one",
          (update(), laid_version(), timer()),
          (("1.6.0", ["nivuus-check.timer"]), "1.6.0", "timer 1.6.0\n"))
    check("the previous copy is gone, nothing beside it",
          sorted(os.listdir(opt)), ["installer"])

    check_refused("a git checkout is never overwritten",
                  lambda: self_update.update_self(str(REPO / "installer")),
                  "update it with git")
finally:
    fake.close()


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all self-update tests passed")
