"""Recording a package that was laid by hand, so the updater can follow it.

A machine the install engine never installed has no /opt/nivuus-packages and
no state file, yet it may run the very packages the updater knows how to
follow - laid by hand, from a clone. Adoption declares that fact: it copies
the package's tracked files where the engine would have put them and records
the manifest's version. It runs NO hook: it describes what is already there,
it does not install anything.

A clone's manifest carries version 0.0.0 - the marker for "not a release" -
so the first `nivuus update` after an adoption always lays the latest release
over it, which is exactly the point.

Wizard answers are NOT invented. A package that asks questions is recorded
without an `answers` key, and the updater refuses to replay its hooks until
answers are recorded - replaying them with an empty answer set would be the
silent failure this refusal exists to prevent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile

from . import state as state_mod
from .discovery import PACKAGES_DIR
from .manifest import MANIFEST_NAME, ManifestError, load_manifest
from .updater import UpdateError, lock


def _copy_tracked(source: str, dest: str) -> None:
    """Tracked files only when `source` is a git work tree, like the releases.

    A clone carries a .git directory, virtualenvs, caches and possibly a
    .env; `git archive HEAD` is what the release workflow publishes, so it is
    what an adopted copy should hold too.
    """
    inside = subprocess.run(
        ["git", "-C", source, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True)
    try:
        if inside.returncode != 0:
            shutil.copytree(source, dest, symlinks=True)
            return
        with tempfile.TemporaryFile() as archive:
            proc = subprocess.run(
                ["git", "-C", source, "archive", "--format=tar", "HEAD"],
                stdout=archive, stderr=subprocess.PIPE, text=False)
            if proc.returncode != 0:
                raise UpdateError(f"git archive HEAD failed in {source}: "
                                  f"{proc.stderr.decode(errors='replace').strip()}")
            archive.seek(0)
            os.makedirs(dest)
            with tarfile.open(fileobj=archive) as tar:
                tar.extractall(dest, filter="data")
    except (OSError, tarfile.TarError, UpdateError) as exc:
        # A partial copy would make every later adopt refuse "already exists".
        if os.path.lexists(dest):
            shutil.rmtree(dest)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"cannot copy {source} to {dest}: {exc}") from exc


def adopt(package_dir: str) -> str:
    """Record the package at `package_dir` as installed. Returns its name."""
    package_dir = os.path.abspath(package_dir)
    try:
        manifest = load_manifest(os.path.join(package_dir, MANIFEST_NAME))
    except ManifestError as exc:
        raise UpdateError(f"cannot adopt {package_dir}: {exc}") from exc
    if manifest.source is None:
        raise UpdateError(
            f"cannot adopt {manifest.name}: its manifest declares no 'source:', "
            "so the updater would have nothing to follow")

    with lock():
        current_state = state_mod.load()
        if manifest.name in current_state:
            raise UpdateError(f"{manifest.name} is already recorded in "
                              f"{state_mod.STATE_FILE}")
        dest = os.path.join(PACKAGES_DIR, manifest.name)
        if os.path.lexists(dest):
            raise UpdateError(f"{dest} already exists and is not recorded; "
                              "remove it or record it before adopting")
        os.makedirs(PACKAGES_DIR, exist_ok=True)
        _copy_tracked(package_dir, dest)
        record = current_state.setdefault(manifest.name, {})
        record["version"] = manifest.version
        record["state"] = state_mod.INSTALLED
        record["adopted_from"] = package_dir
        if not manifest.questions_file:
            record["answers"] = {}
        state_mod.save(current_state)
    return manifest.name
