#!/usr/bin/env python3
"""Tests for installer/packages/state.py - the installed machine's state file.

The file predates the updater: the install engine writes it and
activate_cli.py reads it. So the first property that matters is that a file
written by the current engine reads back unchanged, as installed.

Run: python3 scripts/tests/test_packages_state.py
"""
import json
import os
import pathlib
import stat
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages import state  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "etc", "packages.json")

    check("missing file is an empty state", state.load(path), {})

    engine_written = {"home-stock": {"version": "1.0.0", "answers": {}}}
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as fh:
        json.dump(engine_written, fh)
    loaded = state.load(path)
    check("engine-written record reads unchanged", loaded, engine_written)
    check("engine-written record reads installed",
          state.status(loaded["home-stock"]), "installed")

    state.mark_failed(loaded, "home-stock", "1.2.0", "hook exited 1")
    record = loaded["home-stock"]
    check("failure is recorded", state.status(record), "failed")
    check("failure keeps the installed version", record["version"], "1.0.0")
    check("failure names its target", record["target_version"], "1.2.0")
    check("failure keeps its message", record["error"], "hook exited 1")

    state.mark_installed(loaded, "home-stock", "1.2.0")
    record = loaded["home-stock"]
    check("success records the version", record["version"], "1.2.0")
    check("success clears the failure", ("target_version" in record,
                                         "error" in record), (False, False))
    check("success keeps the recorded answers", record["answers"], {})
    check("success is timestamped", "updated_at" in record, True)

    state.save(loaded, path)
    check("saved file is 0600", stat.S_IMODE(os.stat(path).st_mode), 0o600)
    check("saved file reads back", state.load(path), loaded)
    check("no temporary file left behind",
          sorted(os.listdir(os.path.dirname(path))), ["packages.json"])

    with open(path, "w") as fh:
        fh.write("{not json")
    try:
        state.load(path)
        failures.append("malformed JSON: expected StateError")
    except state.StateError:
        pass

    with open(path, "w") as fh:
        json.dump({"home-stock": "1.0.0"}, fh)
    try:
        state.load(path)
        failures.append("record that is not a mapping: expected StateError")
    except state.StateError:
        pass


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all state tests passed")
