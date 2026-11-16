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
  4. the recorded wizard answers must satisfy the NEW questions, and the
     machine must meet what the release requires (requirements.py): its
     required packages installed and healthy, no capability or feature added
     that the machine cannot be shown to have;
  5. only then swap the directory, replay install and activate, and record
     the new version - on success only (lay.py).

A hook that fails after the swap is rolled back: the previous copy, kept
until then, is put back and its own install and activate are replayed.
lay.py says what that covers and what it cannot. There is no automatic
retry: `update` without names skips a release that was rolled back and a
package left `failed`; naming the package retries it.

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

from . import releases, requirements, state as state_mod
from .archive import ArchiveError, extract
from .dependencies import DependencyError, install_order
from .discovery import PACKAGES_DIR, DiscoveryError, discover
from .lay import STAGING_DIR, UpdateError, lay, recover
from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest
from .wizard import WizardError, load_questions, validate_answers

CACHE_DIR = os.environ.get("NIVUUS_CACHE_DIR", "/var/cache/nivuus/packages")
AVAILABLE_NAME = "available.json"
LOCK_NAME = ".lock"


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


def _installed_manifests(current_state: dict
                         ) -> tuple[dict[str, Manifest], list[tuple[str, str]]]:
    """The manifests of what is installed, or a refusal when they cannot be read.

    A packages directory that is missing or unreadable while the state file
    records packages is a broken machine (a failed mount, wrong permissions),
    not a set of packages "without a manifest": answering that made
    `nivuus check` file every package under "not followed" and exit 0.
    """
    if current_state and not os.path.isdir(PACKAGES_DIR):
        raise UpdateError(
            f"{PACKAGES_DIR} does not exist, yet {len(current_state)} "
            f"package(s) are recorded as installed in {state_mod.STATE_FILE}")
    try:
        manifests, errors = discover(PACKAGES_DIR, strict=True)
    except DiscoveryError as exc:
        raise UpdateError(str(exc)) from exc
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
        manifests, errors = _installed_manifests(current_state)
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
        elif record.get("rolled_back_from"):
            entry["rolled_back_from"] = record["rolled_back_from"]
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


def _check_requirements(staged: list[Manifest], manifests: dict,
                        current_state: dict, hw: dict, apt_query, emit) -> None:
    """Refuse the batch when the machine does not meet what a release requires."""
    try:
        requirements.check_packages(staged, current_state)
        for manifest in staged:
            requirements.check_machine(manifest, manifests[manifest.name], hw)
    except requirements.RequirementError as exc:
        raise UpdateError(str(exc)) from exc
    for manifest in staged:
        # apt is lenient at install (a failed apt install is a warning there)
        # and the update installs nothing: the gap is named, never refused.
        try:
            missing = requirements.missing_apt(
                manifest, manifests[manifest.name], apt_query)
        except requirements.RequirementError as exc:
            emit.warn("packages", 0, f"[{manifest.name}] {exc}")
            continue
        if missing:
            emit.warn("packages", 0,
                      f"[{manifest.name}] release {manifest.version} adds apt "
                      f"package(s) that are not installed: {', '.join(missing)} "
                      "- nivuus update installs none, its hooks will run "
                      "without them")


def _default_hw() -> dict:
    from common import hardware
    return hardware.detect_all()


def update(names=None, fetch=releases.latest_release, hw_detect=_default_hw,
           emit=None, apt_query=requirements.dpkg_installed) -> list[str]:
    """Lay the latest release of `names`, or of every healthy package behind.

    Returns the names laid, in the order they were laid. Raises UpdateError
    on the first refusal or failure; packages laid before it stay laid, and
    the one that failed is rolled back to the version it had (lay.py).
    """
    emit = emit or _NullEmit()
    with lock():
        # Anything left here was left by an interrupted run: only the updater
        # writes here, and it holds the lock.
        if os.path.lexists(STAGING_DIR):
            shutil.rmtree(STAGING_DIR)
        for name in recover():
            emit.warn("packages", 0, f"[{name}] an interrupted update had left "
                                     "it without a directory; its previous "
                                     "copy was put back")
        current_state = state_mod.load()
        manifests, _ = _installed_manifests(current_state)
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
            elif not _is_newer(release, current_state[name]):
                emit.info("packages", 0, f"[{name}] already at {release.version}")
            elif (not explicit and release.version
                  == current_state[name].get("rolled_back_from")):
                emit.warn("packages", 0,
                          f"[{name}] skipped: {release.version} failed and was "
                          "rolled back; name the package to try it again")
            else:
                chosen.append((name, release))

        if not chosen:
            return []
        prepared = {}
        laid = []
        try:
            for name, release in chosen:
                prepared[name] = _prepare(name, release, current_state[name], emit)
            staged = [manifest for _, manifest, _ in prepared.values()]
            # Measured once, before any directory is swapped: a detection
            # failure must refuse the update, not strand a half-laid package.
            hw = hw_detect()
            _check_requirements(staged, manifests, current_state, hw,
                                apt_query, emit)
            try:
                order = install_order(staged)
            except DependencyError as exc:
                raise UpdateError(str(exc)) from exc
            for manifest in order:
                _, _, answers = prepared[manifest.name]
                lay(manifest, answers, current_state, hw, emit)
                laid.append(manifest.name)
        finally:
            # Releases staged but never laid - a refusal, or a failure of an
            # earlier package in the batch - leave no directory behind.
            for staging, _, _ in prepared.values():
                if os.path.lexists(staging):
                    shutil.rmtree(staging)
            if os.path.isdir(STAGING_DIR) and not os.listdir(STAGING_DIR):
                os.rmdir(STAGING_DIR)
        return laid
