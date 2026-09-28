"""Laying a newer release of an installed package on the running machine.

The updater adds no phase to the nivuus.dev/v1 contract: it replays the
package's own `install` with root="/" then its `activate`, exactly as the
engine and the first boot do. What it adds is the decision around them, taken
BEFORE anything is written:

  1. find the latest release of each package's `source:` (releases.py);
  2. download and verify it - fail closed, nothing written on a mismatch;
  3. extract it beside the installed copy and load its manifest with the real
     parser; the release must be the package asked for, at the version its
     tag names;
  4. the recorded wizard answers must satisfy the NEW questions, and every
     required package must be installed and healthy;
  5. only then swap the directory, replay install and activate, and record
     the new version - on success only.

A failure after the swap is recorded as `failed` with its target version and
raised: no automatic restore (the hook may already have written outside its
directory, and restoring the directory alone would describe a machine that
does not exist), and no automatic retry - `update` without names skips failed
packages, naming one retries it.

Three triggers will end up calling this (timer, CLI, Home Assistant), so every
run holds an exclusive, non-blocking lock: a second concurrent run refuses at
once rather than interleaving two lays of the same directory.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone

from . import releases, state as state_mod
from .archive import ArchiveError, extract
from .dependencies import DependencyError, install_order
from .discovery import PACKAGES_DIR, discover
from .facts import STATE_KEY as FACTS_STATE_KEY
from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest
from .runner import HookError, run_activate, run_install
from .wizard import WizardError, load_questions, validate_answers

CACHE_DIR = os.environ.get("NIVUUS_CACHE_DIR", "/var/cache/nivuus/packages")
# Releases are extracted, and the previous copy set aside, OUTSIDE the
# directory discover() scans - a copy of a manifest left there by an
# interrupted run would collide with the installed one by name, and discovery
# drops both. A sibling keeps it on the same filesystem, so the swap is a
# rename.
STAGING_DIR = os.environ.get(
    "NIVUUS_STAGING_DIR",
    os.path.join(os.path.dirname(PACKAGES_DIR.rstrip("/")), ".nivuus-packages-staging"))
AVAILABLE_NAME = "available.json"
LOCK_NAME = ".lock"


class UpdateError(RuntimeError):
    """Raised when an update is refused or a lay fails."""


@dataclass(frozen=True)
class Pending:
    name: str
    installed: str
    available: str
    notes: str


@dataclass(frozen=True)
class CheckResult:
    pending: list[Pending]
    current: list[str]
    # Packages the updater does not follow (no manifest, no source:).
    skipped: list[tuple[str, str]]
    # Packages it follows but could not check this time (network, API).
    unreachable: list[tuple[str, str]]


class _NullEmit:
    def info(self, step, pct, msg):
        pass

    warn = error = info


@contextlib.contextmanager
def lock():
    """Hold the updater's exclusive lock, or refuse at once."""
    os.makedirs(state_mod.STAMP_DIR, exist_ok=True)
    path = os.path.join(state_mod.STAMP_DIR, LOCK_NAME)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError(
                f"another nivuus run holds {path}; try again once it is done"
            ) from exc
        yield
    finally:
        os.close(fd)


def _installed_manifests() -> tuple[dict[str, Manifest], list[tuple[str, str]]]:
    manifests, errors = discover(PACKAGES_DIR)
    return {m.name: m for m in manifests}, errors


def _latest(name: str, manifests: dict, fetch):
    """(release, "", False), or (None, reason, reason_is_a_fetch_failure)."""
    manifest = manifests.get(name)
    if manifest is None:
        return None, f"no manifest under {PACKAGES_DIR}/{name}", False
    if manifest.source is None:
        return None, ("its manifest declares no 'source:', so it has no "
                      "releases to follow"), False
    try:
        return fetch(manifest.source.github), "", False
    except releases.ReleaseError as exc:
        return None, str(exc), True


def _is_newer(release, record: dict) -> bool:
    return (releases.version_key(release.version)
            > releases.version_key(record.get("version", "0.0.0")))


def check(fetch=releases.latest_release) -> CheckResult:
    """Compare every installed package with its latest release. Lays nothing.

    The result is also written to STAMP_DIR/available.json, the file the
    Home Assistant surface is meant to read, so a check made by the timer is
    visible without a network call of its own.
    """
    with lock():
        current_state = state_mod.load()
        manifests, errors = _installed_manifests()
        pending, current, skipped, unreachable = [], [], list(errors), []
        for name in sorted(current_state):
            release, reason, fetch_failed = _latest(name, manifests, fetch)
            if release is None:
                (unreachable if fetch_failed else skipped).append((name, reason))
            elif _is_newer(release, current_state[name]):
                pending.append(Pending(name, current_state[name].get("version", ""),
                                       release.version, release.notes))
            else:
                current.append(name)
        _write_available(current_state, pending, skipped + unreachable)
        return CheckResult(pending, current, skipped, unreachable)


def _write_available(current_state: dict, pending, skipped) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    by_name = {p.name: p for p in pending}
    payload = {"checked_at": now, "packages": {}}
    for name, record in sorted(current_state.items()):
        entry = {"installed": record.get("version", ""),
                 "state": state_mod.status(record)}
        if name in by_name:
            entry["available"] = by_name[name].available
            entry["notes"] = by_name[name].notes
        if state_mod.status(record) == state_mod.FAILED:
            entry["target_version"] = record.get("target_version", "")
            entry["error"] = record.get("error", "")
        payload["packages"][name] = entry
    payload["skipped"] = {name: reason for name, reason in skipped}
    path = os.path.join(state_mod.STAMP_DIR, AVAILABLE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _stage(name: str, archive: str, subpath: str) -> tuple[str, str]:
    """Extract `archive` beside the installed copy. Returns (staging, root).

    archive.extract() refuses absolute paths, `..` and links leaving the
    extraction directory: a hostile or broken archive is refused here, before
    anything installed is touched.
    """
    staging = os.path.join(STAGING_DIR, name)
    if os.path.lexists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    try:
        with tarfile.open(archive) as tar:
            extract(tar, staging)
    except (ArchiveError, tarfile.TarError, OSError) as exc:
        shutil.rmtree(staging)
        raise UpdateError(f"{name}: {os.path.basename(archive)} cannot be "
                          f"extracted ({exc})") from exc
    root = os.path.join(staging, subpath) if subpath else staging
    return staging, root


def _prepare(name: str, release, record: dict, emit) -> tuple[str, Manifest, dict]:
    """Download, verify, stage and validate one release. Writes nothing installed."""
    archive = releases.download(release, CACHE_DIR)
    manifest = None
    staging = ""
    try:
        installed = load_manifest(os.path.join(PACKAGES_DIR, name, MANIFEST_NAME))
        staging, root = _stage(name, archive, installed.source.path)
        manifest = load_manifest(os.path.join(root, MANIFEST_NAME))
        if manifest.name != name:
            raise UpdateError(f"{name}: release {release.tag} of "
                              f"{release.repo} contains package {manifest.name!r}")
        if manifest.version != release.version:
            raise UpdateError(f"{name}: release {release.tag} carries a manifest "
                              f"at version {manifest.version}")
        return staging, manifest, _answers_for(manifest, record, emit)
    except (ManifestError, WizardError, UpdateError) as exc:
        if staging and os.path.lexists(staging):
            shutil.rmtree(staging)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"{name}: release {release.tag} refused: {exc}") from exc


def _answers_for(manifest: Manifest, record: dict, emit) -> dict:
    """The recorded answers, validated against the NEW version's questions."""
    if not manifest.questions_file:
        return record.get("answers") or {}
    if "answers" not in record:
        raise UpdateError(
            f"{manifest.name}: this package asks wizard questions and no answers "
            "are recorded for it (it was adopted, not installed by the engine); "
            "record them in the state file before updating it")
    questions = load_questions(os.path.join(manifest.root, manifest.questions_file))
    answers = dict(record["answers"])
    asked = {q.key for q in questions}
    for key in sorted(set(answers) - asked):
        emit.warn("packages", 0, f"[{manifest.name}] recorded answer {key!r} is "
                                 "no longer asked by this version and is dropped")
        del answers[key]
    return validate_answers(questions, answers)


def _check_requirements(manifests: list[Manifest], current_state: dict) -> None:
    batch = {m.name for m in manifests}
    for manifest in manifests:
        for dependency in manifest.packages:
            record = current_state.get(dependency)
            if record is None:
                raise UpdateError(f"{manifest.name}: requires package "
                                  f"{dependency!r}, which is not installed")
            if dependency not in batch and state_mod.status(record) == state_mod.FAILED:
                raise UpdateError(f"{manifest.name}: requires package "
                                  f"{dependency!r}, whose last update failed")


def _swap(name: str, staging: str, root: str) -> Manifest:
    dest = os.path.join(PACKAGES_DIR, name)
    aside = os.path.join(STAGING_DIR, f"{name}.previous")
    if os.path.lexists(aside):
        shutil.rmtree(aside)
    if os.path.lexists(dest):
        os.rename(dest, aside)
    try:
        os.rename(root, dest)
    except OSError:
        # Never leave the machine with no copy at all: put the previous one
        # back, then let the failure be recorded and raised.
        if os.path.lexists(aside) and not os.path.lexists(dest):
            os.rename(aside, dest)
        raise
    for leftover in (aside, staging):
        if os.path.lexists(leftover):
            shutil.rmtree(leftover)
    return load_manifest(os.path.join(dest, MANIFEST_NAME))


def _lay(staged: Manifest, staging: str, answers: dict, current_state: dict,
         hw: dict, emit) -> None:
    name = staged.name
    record = current_state[name]
    # Recorded as failed BEFORE anything is replaced, and cleared only on
    # success: a run killed mid-lay (signal, power loss) then leaves a record
    # that says so, never one claiming the old version is still what runs.
    state_mod.mark_failed(current_state, name, staged.version,
                          f"interrupted while laying {staged.version}; its "
                          "hooks may have run partially")
    state_mod.save(current_state)
    try:
        manifest = _swap(name, staging, staged.root)
        emit.info("packages", 30, f"[{name}] install {manifest.version}")
        run_install(manifest, hw, answers, "/", emit)
        stamp = os.path.join(state_mod.STAMP_DIR, f"{name}.activated")
        if os.path.exists(stamp):
            os.unlink(stamp)
        emit.info("packages", 70, f"[{name}] activate {manifest.version}")
        run_activate(manifest, hw, answers, emit,
                     facts=record.get(FACTS_STATE_KEY) or {})
        with open(stamp, "w") as fh:
            fh.write(manifest.version + "\n")
    except (HookError, ManifestError, OSError) as exc:
        state_mod.mark_failed(current_state, name, staged.version, str(exc))
        state_mod.save(current_state)
        raise UpdateError(f"{name}: update to {staged.version} failed: {exc}") from exc
    state_mod.mark_installed(current_state, name, manifest.version, answers)
    state_mod.save(current_state)


def _default_hw() -> dict:
    from common import hardware
    return hardware.detect_all()


def update(names=None, fetch=releases.latest_release, hw_detect=_default_hw,
           emit=None) -> list[str]:
    """Lay the latest release of `names`, or of every healthy package behind.

    Returns the names laid, in the order they were laid. Raises UpdateError
    on the first refusal or failure; packages laid before it stay laid.
    """
    emit = emit or _NullEmit()
    with lock():
        # Anything left here was left by an interrupted run: only the updater
        # writes here, and it holds the lock.
        if os.path.lexists(STAGING_DIR):
            shutil.rmtree(STAGING_DIR)
        current_state = state_mod.load()
        manifests, _ = _installed_manifests()
        explicit = bool(names)
        for name in names or []:
            if name not in current_state:
                raise UpdateError(f"{name}: not installed on this machine")
        targets = list(names) if explicit else [
            n for n in sorted(current_state)
            if state_mod.status(current_state[n]) != state_mod.FAILED]

        chosen = []
        for name in targets:
            release, reason, _ = _latest(name, manifests, fetch)
            if release is None:
                if explicit:
                    raise UpdateError(f"{name}: cannot be updated: {reason}")
                emit.warn("packages", 0, f"[{name}] skipped: {reason}")
            elif _is_newer(release, current_state[name]):
                chosen.append((name, release))
            else:
                emit.info("packages", 0, f"[{name}] already at {release.version}")

        if not chosen:
            return []
        prepared = {}
        laid = []
        try:
            for name, release in chosen:
                prepared[name] = _prepare(name, release, current_state[name], emit)
            staged = [manifest for _, manifest, _ in prepared.values()]
            _check_requirements(staged, current_state)
            try:
                order = install_order(staged)
            except DependencyError as exc:
                raise UpdateError(str(exc)) from exc
            # Measured once, before any directory is swapped: a detection
            # failure must refuse the update, not strand a half-laid package.
            hw = hw_detect()
            for manifest in order:
                staging, _, answers = prepared[manifest.name]
                _lay(manifest, staging, answers, current_state, hw, emit)
                laid.append(manifest.name)
        finally:
            # Releases staged but never laid - a refusal, or a failure of an
            # earlier package in the batch - leave no directory behind.
            for staging, _, _ in prepared.values():
                if os.path.lexists(staging):
                    shutil.rmtree(staging)
        return laid
