#!/usr/bin/env python3
"""Tests for scripts/idempotence_harness.py - the proof that install replays.

The update path replays `install` in place rather than introducing a new
phase, so idempotence is a hard contract and not a courtesy. A control that
cannot fail would be worse than none: this suite therefore checks that the
harness ACCEPTS a hook that rewrites and REFUSES one that appends, rather
than only checking that it runs.

Run: python3 scripts/tests/test_idempotence_harness.py
"""

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"

from idempotence_harness import check_package, snapshot  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- a hook that rewrites is idempotent ------------------------------------ #
status, report = check_package(str(FIXTURES / "idempotent"))
check("a rewriting hook is accepted", status, 0)
check("the report names the package", "idempotent" in report, True)

# --- a hook that appends is not -------------------------------------------- #
status, report = check_package(str(FIXTURES / "appender"))
check("an appending hook is refused", status, 1)
check("the report names the offending path", "etc/nivuus-appender.conf" in report, True)
check("the report says what changed", "changed" in report, True)

# --- a package with no install hook proves nothing, and says so ------------ #
status, report = check_package(str(FIXTURES / "refuser"))
check("a package with no install hook is accepted", status, 0)
check("the report says there was nothing to prove", "no install hook" in report, True)

# --- an unreadable manifest is an error, not a pass ------------------------ #
status, report = check_package(str(FIXTURES / "does-not-exist"))
check("a missing manifest exits 2", status, 2)

# --- a package needing a prerequisite fails without --prereq, and succeeds
# once install_order() has installed it first --------------------------- #
status, report = check_package(str(FIXTURES / "dependent"))
check("a package needing its prerequisite fails without --prereq", status, 1)

status, report = check_package(str(FIXTURES / "dependent"), prereqs=(str(FIXTURES / "idempotent"),))
check("the same package succeeds once its prerequisite is installed", status, 0)
check("the report names the dependent package", "dependent" in report, True)

# install_order(), not argument order, decides the sequence: 'dependent' is
# listed BEFORE its own prerequisite 'idempotent' here, and the two-hop chain
# 'idempotent' -> 'dependent' -> 'grandchild' must still be installed in the
# right order for 'grandchild' (the package under test) to succeed. If a
# naive implementation installed prereqs in argument order instead, this
# would fail with "prerequisite 'dependent' failed to install".
status, report = check_package(
    str(FIXTURES / "grandchild"),
    prereqs=(str(FIXTURES / "dependent"), str(FIXTURES / "idempotent")),
)
check("prerequisite order comes from install_order(), not argument order", status, 0)
check("the report names the grandchild package", "grandchild" in report, True)

# --- the snapshot itself must distinguish content, mode, kind, and
# additions/removals --------------------------------------------------- #
import os  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    with open(path, "w") as handle:
        handle.write("b")
    check("the snapshot notices a content change", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    os.chmod(path, 0o600)
    first = snapshot(tmp, ())
    os.chmod(path, 0o644)
    check("the snapshot notices a mode change", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    os.symlink("elsewhere", path)
    check("the snapshot notices a file replaced by a symlink", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    os.mkdir(path)
    check("the snapshot notices a file replaced by a directory", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    first = snapshot(tmp, ())
    with open(os.path.join(tmp, "new"), "w") as handle:
        handle.write("a")
    check("the snapshot notices an added entry", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    check("the snapshot notices a removed entry", snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    with open(os.path.join(tmp, "f"), "w") as handle:
        handle.write("a")
    check("an ignored path is left out of the snapshot", snapshot(tmp, ("f",)), {})

# --- rollback: replaying the previous version must undo the newer one ------ #
# What installer/packages/lay.py relies on when a release's hooks fail. Three
# versions of one package, built here: v1 rewrites its file, v2 rewrites the
# same file and adds another, and a v1 that only writes its file when it is
# missing - idempotent on its own, and unable to restore anything.
from idempotence_harness import check_rollback  # noqa: E402
from idempotence_harness import main as harness_main  # noqa: E402

VERSION_HOOK = """import argparse, json, os, sys
parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()
json.load(sys.stdin)
etc = os.path.join(args.root, "etc")
os.makedirs(etc, exist_ok=True)
for name in {files!r}:
    path = os.path.join(etc, name)
    if {only_if_missing!r} and os.path.exists(path):
        continue
    with open(path, "w") as handle:
        handle.write("{version}\\n")
sys.exit({status})
"""


def versioned(tmp, label, version, files, only_if_missing=False, name="app", status=0):
    pkg = pathlib.Path(tmp) / label
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "nivuus-package.yaml").write_text(
        f"apiVersion: nivuus.dev/v1\nname: {name}\nversion: {version}\n"
        f'label: "{name}"\ntier: userspace\nhooks:\n  install: hooks/install.py\n'
    )
    (pkg / "hooks" / "install.py").write_text(
        VERSION_HOOK.format(
            files=files, only_if_missing=only_if_missing, version=version, status=status
        )
    )
    return str(pkg)


with tempfile.TemporaryDirectory() as tmp:
    v1 = versioned(tmp, "v1", "1.0.0", ["app.conf"])
    v2 = versioned(tmp, "v2", "2.0.0", ["app.conf", "app-extra.conf"])
    keeper = versioned(tmp, "keeper", "1.0.0", ["app.conf"], only_if_missing=True)
    broken = versioned(tmp, "broken", "2.0.0", ["app.conf"], status=1)
    other = versioned(tmp, "other", "2.0.0", ["app.conf"], name="other")

    status, report = check_rollback(v1, v2)
    check("a version that rewrites its files undoes the newer one", status, 0)
    check(
        "what only the newer version wrote is listed as left behind",
        ("left behind by 2.0.0" in report, "etc/app-extra.conf" in report),
        (True, True),
    )

    status, report = check_package(keeper)
    check("a hook that writes only what is missing is idempotent", status, 0)
    status, report = check_rollback(keeper, v2)
    check("and still cannot roll a newer version back", status, 1)
    check(
        "the report names the file it failed to restore",
        ("does NOT restore" in report, "changed etc/app.conf" in report),
        (True, True),
    )

    status, report = check_rollback(v1, broken)
    check("a newer hook that fails part-way is where a rollback starts", status, 0)
    check("and the report says it failed", "install hook failed part-way" in report, True)

    status, report = check_rollback(v1, other)
    check("two different packages are not a rollback", status, 2)
    status, report = check_rollback(v1, str(pathlib.Path(tmp) / "absent"))
    check("an unreadable newer manifest exits 2", status, 2)

    status = harness_main(["--package", keeper, "--rollback-from", v2])
    check("the command line reaches the rollback proof", status, 1)

# --- malformed --answers/--hw exit 2, not a raw traceback ------------------ #

with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w") as handle:
        handle.write("{not valid json")

    status = harness_main(["--package", str(FIXTURES / "idempotent"), "--answers", bad])
    check("a malformed --answers file exits 2, not a traceback", status, 2)

    status = harness_main(["--package", str(FIXTURES / "idempotent"), "--hw", bad])
    check("a malformed --hw file exits 2, not a traceback", status, 2)

    missing = os.path.join(tmp, "does-not-exist.json")
    status = harness_main(["--package", str(FIXTURES / "idempotent"), "--answers", missing])
    check("an unreadable --answers file exits 2, not a traceback", status, 2)

if failures:
    print(f"FAIL ({len(failures)})")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("OK - all idempotence harness tests passed")
