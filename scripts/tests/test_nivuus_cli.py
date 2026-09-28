#!/usr/bin/env python3
"""Tests for installer/packages/nivuus_cli.py, run as the operator runs it.

Every call goes through a real subprocess, through a SYMLINK to the CLI -
which is how the engine installs it - so the path resolution that lets it
import `packages` is exercised, not assumed.

Run: python3 scripts/tests/test_nivuus_cli.py
"""
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def contains(label, text, needle):
    if needle not in text:
        failures.append(f"{label}: {needle!r} not in {text!r}")


MANIFEST = """apiVersion: nivuus.dev/v1
name: demo
version: {version}
label: demo
tier: userspace
source:
  github: nivuus/demo
hooks:
  install: hooks/install.py
"""
HOOK = "import sys; sys.stdin.read()\n"

fake = fake_github.start()
root = tempfile.mkdtemp(prefix="nivuus-cli-")
bindir = os.path.join(root, "bin")
os.makedirs(bindir)
cli = os.path.join(bindir, "nivuus")
os.symlink(REPO / "installer/packages/nivuus_cli.py", cli)
helper = os.path.join(bindir, "nivuus-hello")
with open(helper, "w") as fh:
    fh.write('#!/bin/sh\necho "hello $*"\n')
os.chmod(helper, stat.S_IRWXU)

env = dict(os.environ,
           PATH=f"{bindir}:{os.environ['PATH']}",
           NIVUUS_PACKAGES_DIR=os.path.join(root, "opt/nivuus-packages"),
           NIVUUS_STATE_FILE=os.path.join(root, "etc/nivuus/packages.json"),
           NIVUUS_STAMP_DIR=os.path.join(root, "var/lib/nivuus/packages"),
           NIVUUS_CACHE_DIR=os.path.join(root, "var/cache/nivuus"))


def run(*args):
    proc = subprocess.run([sys.executable, cli, *args], env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


try:
    code, out, _ = run("list")
    check("list on an empty machine", code, 0)
    contains("list says nothing is recorded", out, "no package recorded")

    clone = os.path.join(root, "clone")
    os.makedirs(os.path.join(clone, "hooks"))
    with open(os.path.join(clone, "nivuus-package.yaml"), "w") as fh:
        fh.write(MANIFEST.format(version="0.0.0"))
    with open(os.path.join(clone, "hooks/install.py"), "w") as fh:
        fh.write(HOOK)
    check("adopt succeeds", run("adopt", clone)[:2], (0, "demo: adopted\n"))

    fake.publish("nivuus/demo", "1.0.0", fake_github.build_archive({
        "nivuus-package.yaml": MANIFEST.format(version="1.0.0"),
        "hooks/install.py": HOOK}))
    check("check reports the pending release",
          run("check")[:2], (0, "demo: 0.0.0 -> 1.0.0\n"))
    code, out, _ = run("list")
    contains("list shows what is available", out, "demo     0.0.0      1.0.0")

    code, out, err = run("update")
    check("update lays it", (code, out), (0, "demo: updated\n"))
    code, out, _ = run("status", "demo")
    check("status shows the new version",
          json.loads(out)["demo"]["version"], "1.0.0")
    check("update again has nothing to do",
          run("update")[:2], (0, "nothing to update\n"))

    code, _, err = run("update", "ghost")
    check("an error exits 1", code, 1)
    contains("and names the package", err, "ghost: not installed")

    check("unknown word delegates to nivuus-<word>",
          run("hello", "world")[:2], (0, "hello world\n"))
    code, _, err = run("nothing-like-this")
    check("unknown word without a delegate is a usage error", code, 2)
    contains("usage is shown", err, "nivuus update")
finally:
    fake.close()


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all nivuus CLI tests passed")
