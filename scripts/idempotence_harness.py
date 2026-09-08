#!/usr/bin/env python3
"""Prove that a package's `install` hook can be replayed without changing anything.

The update path replays `install` in place on a live system rather than adding
a new phase, so idempotence stopped being a courtesy the day that was decided:
a hook that appends instead of rewriting, or that stamps a timestamp, corrupts
the machine on its second run. This harness is the control that can actually
fail - it compares a measured result, never a declared intention.

It runs the hook TWICE against a scratch root and compares the resulting trees
byte for byte. Every install hook in the suite is a pure file writer - no
subprocess, no apt, no docker, measured across all six on 2026-09-08 - so this
needs neither root nor network and runs on an ordinary CI runner.

Four of those six packages cannot install alone: they declare
`requires.packages` (a satellite writes into what its base package created)
or need a wizard answer only the operator supplies. `--prereq <dir>`
(repeatable) installs those prerequisites ONCE into the same scratch root,
in the order `packages.dependencies.install_order()` computes - never
argument order, so a caller cannot silently misorder a dependency chain -
before the package under test runs. Prerequisites are never part of the
idempotence comparison: the first snapshot is taken only after the package
under test has completed its own first pass.

A failure keeps the scratch root and names it, because "the second run differs"
is not actionable on its own: the reader needs the two trees to compare. A
successful run removes its scratch root - this harness runs on every pull
request across six repositories, so an unbounded pile of temp directories on
a self-hosted runner is a real, measured failure mode on this host (see
CLAUDE.md, "Disk space: audit + permanent bounds"), not a hypothetical one.

Messages are in English, unlike discovery.py's: those reach an operator in the
installer portal, these reach a developer in a CI log, and check-english.sh
judges every added line of a non-test file.

Run: python3 scripts/idempotence_harness.py --package <dir> \
    [--prereq <dir>]... [--ignore GLOB]...
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "installer"))

from packages.dependencies import DependencyError, install_order  # noqa: E402
from packages.manifest import MANIFEST_NAME, ManifestError, load_manifest  # noqa: E402
from packages.runner import HookError, run_install  # noqa: E402


def _describe(path: str) -> str:
    """A stable one-line description of one filesystem entry."""
    if os.path.islink(path):
        return "symlink:" + os.readlink(path)
    mode = oct(os.stat(path).st_mode & 0o777)
    if os.path.isdir(path):
        return "dir:" + mode
    with open(path, "rb") as handle:
        return "file:{}:{}".format(mode, hashlib.sha256(handle.read()).hexdigest())


def snapshot(root: str, ignore) -> dict:
    """Map every path under `root` to its description, skipping `ignore` globs.

    os.walk with followlinks=False rather than Path.rglob: a symlinked
    directory must be recorded as a symlink and never descended into, or a
    loop in what a hook wrote would hang the proof instead of failing it.
    """
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in dirnames + sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if any(fnmatch.fnmatch(rel, pattern) for pattern in ignore):
                continue
            entries[rel] = _describe(full)
    return entries


def _report(name: str, first: dict, second: dict, root: str) -> str:
    lines = [f"{name}: replaying install CHANGED the tree", ""]
    for rel in sorted(set(second) - set(first)):
        lines.append(f"  added   {rel}")
    for rel in sorted(set(first) - set(second)):
        lines.append(f"  removed {rel}")
    for rel in sorted(set(first) & set(second)):
        if first[rel] != second[rel]:
            lines.append(f"  changed {rel}")
            lines.append(f"      first pass:  {first[rel]}")
            lines.append(f"      second pass: {second[rel]}")
    lines += ["", f"Scratch root kept at: {root}"]
    return "\n".join(lines)


def _load_prereq_manifests(prereqs) -> tuple:
    """Manifests for `--prereq` directories, or (None, error-report)."""
    manifests = []
    for prereq_dir in prereqs:
        prereq_path = os.path.join(prereq_dir, MANIFEST_NAME)
        try:
            manifests.append(load_manifest(prereq_path))
        except (ManifestError, OSError) as exc:
            return None, f"{prereq_dir}: unreadable prerequisite manifest: {exc}"
    return manifests, ""


def check_package(package_dir: str, answers=None, hw=None, ignore=(),
                   prereqs=()) -> tuple:
    """Return (exit status, report) for one package directory.

    `prereqs` are directories installed ONCE into the same scratch root
    before the package under test - see the module docstring for why this
    exists and why prerequisite order is computed, never taken as given.
    """
    manifest_path = os.path.join(package_dir, MANIFEST_NAME)
    try:
        manifest = load_manifest(manifest_path)
    except (ManifestError, OSError) as exc:
        return 2, f"{package_dir}: unreadable manifest: {exc}"

    if not manifest.hook_path("install"):
        return 0, f"{manifest.name}: no install hook, nothing to prove"

    prereq_manifests, error = _load_prereq_manifests(prereqs)
    if error:
        return 2, error

    try:
        ordered = install_order([*prereq_manifests, manifest])
    except DependencyError as exc:
        return 2, f"{manifest.name}: cannot order prerequisites: {exc}"

    answers = {} if answers is None else answers
    hw = {} if hw is None else hw
    root = tempfile.mkdtemp(prefix="nivuus-idempotence-")

    try:
        for prereq in ordered:
            if prereq.name == manifest.name:
                continue
            try:
                run_install(prereq, hw, answers, root)
            except HookError as exc:
                return 1, (f"{manifest.name}: prerequisite '{prereq.name}' "
                           f"failed to install: {exc}")

        run_install(manifest, hw, answers, root)
        first = snapshot(root, ignore)
        run_install(manifest, hw, answers, root)
        second = snapshot(root, ignore)
    except HookError as exc:
        return 1, f"{manifest.name}: the install hook failed: {exc}"

    if first == second:
        shutil.rmtree(root, ignore_errors=True)
        return 0, (f"{manifest.name}: install is idempotent "
                   f"({len(first)} identical entries)")
    return 1, _report(manifest.name, first, second, root)


def _load_json_arg(path: str, what: str) -> tuple:
    """Parse a JSON file given on the CLI. Returns (data, error-or-empty).

    A malformed or unreadable --answers/--hw file must exit 2, the same
    class as an unreadable manifest, never surface as a raw traceback.
    """
    try:
        return json.loads(pathlib.Path(path).read_text()), ""
    except OSError as exc:
        return {}, f"{what} file {path!r} cannot be read: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"{what} file {path!r} is not valid JSON: {exc}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True,
                        help="directory holding nivuus-package.yaml")
    parser.add_argument("--prereq", action="append", default=[],
                        help="directory holding a prerequisite package, "
                             "installed once before --package; repeatable")
    parser.add_argument("--answers", default="",
                        help="JSON file holding the wizard answers")
    parser.add_argument("--hw", default="",
                        help="JSON file holding the hardware inventory")
    parser.add_argument("--ignore", action="append", default=[],
                        help="relative glob to ignore, repeatable")
    args = parser.parse_args(argv)

    answers: dict = {}
    if args.answers:
        answers, error = _load_json_arg(args.answers, "answers")
        if error:
            print(error)
            return 2

    hw: dict = {}
    if args.hw:
        hw, error = _load_json_arg(args.hw, "hw")
        if error:
            print(error)
            return 2

    status, report = check_package(args.package, answers, hw,
                                    tuple(args.ignore), tuple(args.prereq))
    print(report)
    return status


if __name__ == "__main__":
    sys.exit(main())
