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


def payload(version, *, complete=True):
    files = {"README.md": "top level, not laid",
             "installer/packages/nivuus_cli.py": f"# {version}\n",
             "installer/packages/updater.py": "", "installer/common/hardware.py": ""}
    if complete:
        files["installer/packages/activate_cli.py"] = ""
    return fake_github.build_archive(files)


opt = os.path.join(ROOT, "opt", "nivuus")
root = os.path.join(opt, "installer")
os.makedirs(os.path.join(root, "packages"))
with open(os.path.join(root, "packages", "nivuus_cli.py"), "w") as fh:
    fh.write("# laid by hand\n")

try:
    check("no record reads as 0.0.0", self_update.installed_version(), "0.0.0")
    fake.publish("nivuus/installer", "1.5.0", payload("1.5.0"))
    check("the release is laid", self_update.update_self(root), "1.5.0")
    with open(os.path.join(root, "packages", "nivuus_cli.py")) as fh:
        check("with the release's files", fh.read(), "# 1.5.0\n")
    check("the CLI is executable",
          os.access(os.path.join(root, "packages", "nivuus_cli.py"), os.X_OK), True)
    check("only the installer subtree is laid",
          sorted(os.listdir(opt)), ["installer"])
    check("the version is recorded", self_update.installed_version(), "1.5.0")
    with open(os.path.join(os.environ["NIVUUS_STAMP_DIR"], "installer.json")) as fh:
        check("in its own record, not the package state",
              "updated_at" in json.load(fh), True)
    check("a second run lays nothing", self_update.update_self(root), None)

    fake.publish("nivuus/installer", "1.6.0", payload("1.6.0", complete=False))
    check_refused("an archive that is not a payload is refused",
                  lambda: self_update.update_self(root), "not an installer payload")
    with open(os.path.join(root, "packages", "nivuus_cli.py")) as fh:
        check("and the laid copy is untouched", fh.read(), "# 1.5.0\n")
    check("no leftover beside it", sorted(os.listdir(opt)), ["installer"])

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
