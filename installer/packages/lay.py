"""Laying one staged release over the installed copy, and undoing it.

updater.py decides WHAT to lay and refuses before anything is written. This
module is what happens after that decision: swap the directory, replay the
package's `install` then `activate`, and - when one of them fails - put the
previous version back.

The previous copy is set aside, never deleted, until the new version's hooks
have both succeeded. Until 2026-11-16 it was deleted right after the swap,
before any hook ran, so a failing hook left the new tree in place with
nothing to go back to.

WHAT A ROLLBACK DOES. The previous copy returns to PACKAGES_DIR, its own
`install` is replayed with root "/" and then its `activate`, with the answers
and facts recorded for it - the same replay an update is, pointed at the
version that worked. The state then says `installed` at that version, and
remembers the release that failed (`rolled_back_from`, `error`) so a bare
`nivuus update` does not walk into it again; naming the package retries it,
and a later release is laid as usual.

WHAT IT DOES NOT DO, because the nivuus.dev/v1 contract has no hook to undo
anything. A package's `install` rewrites what that version writes - the
idempotence harness proves it, and `--rollback-from` measures it across two
versions - so files both versions write come back. Everything else the failed
release's hooks did stays:
  - files only the new version writes (no version knows how to remove them);
  - data it migrated in place - a database schema, a Home Assistant config
    entry, a compose volume;
  - container images pulled, system packages installed, anything on another
    machine.
A release whose hooks migrate data forward is not made reversible by this.

WHEN THE ROLLBACK ITSELF FAILS - the previous version's hooks do not pass
either, or the copy cannot be moved back - the package is recorded `failed`
with both errors, exactly as before this module existed, and is left to the
operator. A lay that is INTERRUPTED (signal, power loss) is not rolled back
either: running more hooks from a process being killed is not a recovery. Its
record already says `failed ... interrupted`, the previous copy stays under
PREVIOUS_DIR, and the next named update uses it: it lays the release again
and, should that fail, rolls back to the copy that was kept.
"""
from __future__ import annotations

import os
import shutil

from . import state as state_mod
from .discovery import PACKAGES_DIR
from .facts import STATE_KEY as FACTS_STATE_KEY
from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest
from .runner import HookError, run_activate, run_install

_BESIDE_PACKAGES = os.path.dirname(PACKAGES_DIR.rstrip("/"))
# Releases are extracted OUTSIDE the directory discover() scans - a copy of a
# manifest left there by an interrupted run would collide with the installed
# one by name, and discovery drops both. A sibling keeps it on the same
# filesystem, so the swap is a rename.
STAGING_DIR = os.environ.get(
    "NIVUUS_STAGING_DIR", os.path.join(_BESIDE_PACKAGES, ".nivuus-packages-staging"))
# The copy a lay replaced, kept until that lay's hooks have succeeded. Apart
# from STAGING_DIR on purpose: staging is wiped at the start of every run,
# and this must survive the run that was interrupted.
PREVIOUS_DIR = os.environ.get(
    "NIVUUS_PREVIOUS_DIR", os.path.join(_BESIDE_PACKAGES, ".nivuus-packages-previous"))

# What a lay can fail on once the decision to lay has been taken.
LAY_ERRORS = (HookError, ManifestError, OSError)


class UpdateError(RuntimeError):
    """Raised when an update is refused or a lay fails."""


def _paths(name: str) -> tuple[str, str]:
    return os.path.join(PACKAGES_DIR, name), os.path.join(PREVIOUS_DIR, name)


def _stamp(name: str) -> str:
    return os.path.join(state_mod.STAMP_DIR, f"{name}.activated")


def recover() -> list[str]:
    """Put back the copies an interrupted swap left out of PACKAGES_DIR.

    A swap is two renames. A run killed between them leaves the package with
    no directory at all and its previous copy under PREVIOUS_DIR; no hook has
    run on the new one. Returns the names put back.
    """
    if not os.path.isdir(PREVIOUS_DIR):
        return []
    recovered = []
    for name in sorted(os.listdir(PREVIOUS_DIR)):
        dest, kept = _paths(name)
        if not os.path.lexists(dest):
            os.rename(kept, dest)
            recovered.append(name)
    _drop_if_empty(PREVIOUS_DIR)
    return recovered


def _drop_if_empty(directory: str) -> None:
    if os.path.isdir(directory) and not os.listdir(directory):
        os.rmdir(directory)


def _swap(name: str, root: str, incomplete: bool) -> Manifest:
    """Set the installed copy aside and put the staged release in its place.

    `incomplete` says the record was `failed` when this lay began. A copy
    found under PREVIOUS_DIR then belongs to a lay that never completed: it
    is the last one whose hooks succeeded, so it stays the way back, and
    what sits in PACKAGES_DIR - the release that did not complete - is the
    one discarded. On a healthy record such a copy is only a leftover that a
    completed lay could not remove, older than what runs: it is dropped.
    """
    dest, kept = _paths(name)
    if os.path.lexists(kept) and not incomplete:
        shutil.rmtree(kept)
    if os.path.lexists(kept):
        if os.path.lexists(dest):
            shutil.rmtree(dest)
    elif os.path.lexists(dest):
        os.makedirs(PREVIOUS_DIR, exist_ok=True)
        os.rename(dest, kept)
    os.rename(root, dest)
    return load_manifest(os.path.join(dest, MANIFEST_NAME))


def _replay(manifest: Manifest, record: dict, answers: dict, hw: dict,
            emit) -> None:
    """`install` with root "/", then `activate`, stamped on success."""
    name = manifest.name
    emit.info("packages", 30, f"[{name}] install {manifest.version}")
    run_install(manifest, hw, answers, "/", emit)
    stamp = _stamp(name)
    if os.path.exists(stamp):
        os.unlink(stamp)
    emit.info("packages", 70, f"[{name}] activate {manifest.version}")
    run_activate(manifest, hw, answers, emit,
                 facts=record.get(FACTS_STATE_KEY) or {})
    with open(stamp, "w") as fh:
        fh.write(manifest.version + "\n")


def _restore(name: str) -> Manifest | None:
    """Put the kept copy back in PACKAGES_DIR. None when it never left."""
    dest, kept = _paths(name)
    if not os.path.lexists(kept):
        return None
    if os.path.lexists(dest):
        # Renamed out of the scanned directory first, so there is never a
        # moment with a half-deleted copy where discovery looks.
        os.makedirs(STAGING_DIR, exist_ok=True)
        rejected = os.path.join(STAGING_DIR, f"{name}.rejected")
        if os.path.lexists(rejected):
            shutil.rmtree(rejected)
        os.rename(dest, rejected)
        os.rename(kept, dest)
        shutil.rmtree(rejected)
    else:
        os.rename(kept, dest)
    _drop_if_empty(PREVIOUS_DIR)
    return load_manifest(os.path.join(dest, MANIFEST_NAME))


def _roll_back(name: str, record: dict, hw: dict, emit,
               hooks_ran: bool) -> str:
    """Return the machine to the previous version. Returns that version.

    When no hook of the new release ran (the swap itself failed) the previous
    copy only has to be back in place: nothing outside it was touched, so
    nothing is replayed.
    """
    previous = _restore(name)
    if previous is None:
        if hooks_ran:
            raise OSError(f"no previous copy of {name} was kept under "
                          f"{PREVIOUS_DIR}")
        return record.get("version", "")
    if hooks_ran:
        emit.warn("packages", 0, f"[{name}] rolling back to {previous.version}")
        _replay(previous, record, record.get("answers") or {}, hw, emit)
    return previous.version


def lay(staged: Manifest, answers: dict, current_state: dict, hw: dict,
        emit) -> None:
    """Lay `staged` over the installed copy; roll back when its hooks fail."""
    name = staged.name
    record = current_state[name]
    incomplete = state_mod.status(record) == state_mod.FAILED
    # Recorded as failed BEFORE anything is replaced, and cleared only once
    # the outcome is known: a run killed mid-lay (signal, power loss) then
    # leaves a record that says so, never one claiming the old version is
    # still what runs.
    state_mod.mark_failed(current_state, name, staged.version,
                          f"interrupted while laying {staged.version}; its "
                          "hooks may have run partially")
    state_mod.save(current_state)
    hooks_ran = False
    try:
        manifest = _swap(name, staged.root, incomplete)
        hooks_ran = True
        _replay(manifest, record, answers, hw, emit)
    except LAY_ERRORS as exc:
        raise _failed(staged, record, current_state, hw, emit, hooks_ran,
                      exc) from exc
    # The record first: the hooks have succeeded and that must be on disk
    # before anything else can fail.
    state_mod.mark_installed(current_state, name, manifest.version, answers)
    state_mod.save(current_state)
    _, kept = _paths(name)
    try:
        if os.path.lexists(kept):
            shutil.rmtree(kept)
        _drop_if_empty(PREVIOUS_DIR)
    except OSError as exc:
        raise UpdateError(f"{name}: updated to {manifest.version}, but its "
                          f"previous copy could not be removed from {kept} "
                          f"({exc})") from exc


def _failed(staged: Manifest, record: dict, current_state: dict, hw: dict,
            emit, hooks_ran: bool, cause: Exception) -> UpdateError:
    """Roll back, record what the machine now is, return the error to raise."""
    name = staged.name
    failure = f"update to {staged.version} failed: {cause}"
    try:
        previous = _roll_back(name, record, hw, emit, hooks_ran)
    except LAY_ERRORS as exc:
        error = (f"{failure}; rolling back to {record.get('version', '?')} "
                 f"failed too: {exc}")
        state_mod.mark_failed(current_state, name, staged.version, error)
        state_mod.save(current_state)
        return UpdateError(f"{name}: {error}")
    state_mod.mark_rolled_back(current_state, name, staged.version, str(cause))
    state_mod.save(current_state)
    return UpdateError(f"{name}: {failure}; rolled back to {previous}")
