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
check("the report names the offending path",
      "etc/nivuus-appender.conf" in report, True)
check("the report says what changed", "changed" in report, True)

# --- a package with no install hook proves nothing, and says so ------------ #
status, report = check_package(str(FIXTURES / "refuser"))
check("a package with no install hook is accepted", status, 0)
check("the report says there was nothing to prove",
      "no install hook" in report, True)

# --- an unreadable manifest is an error, not a pass ------------------------ #
status, report = check_package(str(FIXTURES / "does-not-exist"))
check("a missing manifest exits 2", status, 2)

# --- a package needing a prerequisite fails without --prereq, and succeeds
# once install_order() has installed it first --------------------------- #
status, report = check_package(str(FIXTURES / "dependent"))
check("a package needing its prerequisite fails without --prereq", status, 1)

status, report = check_package(str(FIXTURES / "dependent"),
                                prereqs=(str(FIXTURES / "idempotent"),))
check("the same package succeeds once its prerequisite is installed",
      status, 0)
check("the report names the dependent package", "dependent" in report, True)

# install_order(), not argument order, decides the sequence: 'dependent' is
# listed BEFORE its own prerequisite 'idempotent' here, and the two-hop chain
# 'idempotent' -> 'dependent' -> 'grandchild' must still be installed in the
# right order for 'grandchild' (the package under test) to succeed. If a
# naive implementation installed prereqs in argument order instead, this
# would fail with "prerequisite 'dependent' failed to install".
status, report = check_package(
    str(FIXTURES / "grandchild"),
    prereqs=(str(FIXTURES / "dependent"), str(FIXTURES / "idempotent")))
check("prerequisite order comes from install_order(), not argument order",
      status, 0)
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
    check("the snapshot notices a content change", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    os.chmod(path, 0o600)
    first = snapshot(tmp, ())
    os.chmod(path, 0o644)
    check("the snapshot notices a mode change", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    os.symlink("elsewhere", path)
    check("the snapshot notices a file replaced by a symlink",
          snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    os.mkdir(path)
    check("the snapshot notices a file replaced by a directory",
          snapshot(tmp, ()) == first, False)

with tempfile.TemporaryDirectory() as tmp:
    first = snapshot(tmp, ())
    with open(os.path.join(tmp, "new"), "w") as handle:
        handle.write("a")
    check("the snapshot notices an added entry", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    os.remove(path)
    check("the snapshot notices a removed entry", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    with open(os.path.join(tmp, "f"), "w") as handle:
        handle.write("a")
    check("an ignored path is left out of the snapshot",
          snapshot(tmp, ("f",)), {})

# --- malformed --answers/--hw exit 2, not a raw traceback ------------------ #
from idempotence_harness import main as harness_main  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w") as handle:
        handle.write("{not valid json")

    status = harness_main(["--package", str(FIXTURES / "idempotent"),
                            "--answers", bad])
    check("a malformed --answers file exits 2, not a traceback", status, 2)

    status = harness_main(["--package", str(FIXTURES / "idempotent"),
                            "--hw", bad])
    check("a malformed --hw file exits 2, not a traceback", status, 2)

    missing = os.path.join(tmp, "does-not-exist.json")
    status = harness_main(["--package", str(FIXTURES / "idempotent"),
                            "--answers", missing])
    check("an unreadable --answers file exits 2, not a traceback", status, 2)

if failures:
    print(f"FAIL ({len(failures)})")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("OK - all idempotence harness tests passed")
