"""`nivuus update --self`: laying a newer release of the installer payload.

The installer is not a package - nothing installs it but itself - yet it
publishes releases like the rest of the suite, and the updater, the CLI and
the first-boot activation all live in it. This module lays the release's
`installer/` subtree over the directory this code runs from (the payload's
`installer/`, /opt/nivuus/installer on a target), with the updater's own
rules: verified archive, safe extraction, a swap that puts the previous copy
back if it fails, the updater's lock.

Three limits, stated rather than hidden:
  - it refuses to touch a git checkout: a developer's clone is updated with
    git, and overwriting it would lose work;
  - only the `installer/` subtree is laid. The systemd units under
    configs/systemd/ (nivuus-check.*, nivuus-package-activate@) are not
    refreshed by it;
  - the laid version is recorded in STAMP_DIR/installer.json, not in the
    package state file, which is a namespace of packages only. A payload
    with no record (a fresh ISO install) reads as 0.0.0, so the first run
    always lays the latest release.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
from datetime import datetime, timezone

from . import releases, state as state_mod
from .archive import ArchiveError, extract
from .updater import CACHE_DIR, UpdateError, lock

INSTALLER_REPO = os.environ.get("NIVUUS_INSTALLER_REPO", "nivuus/installer")
RECORD_NAME = "installer.json"
SUBTREE = "installer"
# Files whose presence proves the staged tree is really the payload.
EXPECTED = ("packages/nivuus_cli.py", "packages/updater.py",
            "packages/activate_cli.py")
EXECUTABLES = ("packages/nivuus_cli.py", "packages/activate_cli.py")

# The directory holding packages/ and common/ - where this code runs from.
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def installed_version() -> str:
    path = os.path.join(state_mod.STAMP_DIR, RECORD_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh)["version"])
    except FileNotFoundError:
        return "0.0.0"
    except (OSError, ValueError, KeyError) as exc:
        raise UpdateError(f"{path}: cannot be read ({exc})") from exc


def _record(version: str) -> None:
    os.makedirs(state_mod.STAMP_DIR, exist_ok=True)
    path = os.path.join(state_mod.STAMP_DIR, RECORD_NAME)
    tmp = path + ".tmp"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": version, "updated_at": now}, fh)
        fh.write("\n")
    os.replace(tmp, path)


def _git_checkout(root: str) -> str:
    """The directory holding .git above `root`, or ""."""
    path = os.path.realpath(root)
    while True:
        if os.path.lexists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent


def check_self(fetch=releases.latest_release):
    """(installed version, latest release). Raises ReleaseError."""
    return installed_version(), fetch(INSTALLER_REPO)


def update_self(root: str = DEFAULT_ROOT, fetch=releases.latest_release):
    """Lay the latest installer release over `root`. Returns the version laid,
    or None when already current."""
    checkout = _git_checkout(root)
    if checkout:
        raise UpdateError(f"{root} is inside the git checkout {checkout}; "
                          "update it with git, not with nivuus update --self")
    with lock():
        release = fetch(INSTALLER_REPO)
        if (releases.version_key(release.version)
                <= releases.version_key(installed_version())):
            return None
        archive = releases.download(release, CACHE_DIR)
        parent = os.path.dirname(os.path.realpath(root))
        staging = os.path.join(parent, ".nivuus-installer-staging")
        aside = os.path.join(parent, ".nivuus-installer-previous")
        for leftover in (staging, aside):
            if os.path.lexists(leftover):
                shutil.rmtree(leftover)
        try:
            with tarfile.open(archive) as tar:
                extract(tar, staging)
            new = os.path.join(staging, SUBTREE)
            missing = [rel for rel in EXPECTED
                       if not os.path.isfile(os.path.join(new, rel))]
            if missing:
                raise UpdateError(f"{release.repo} {release.tag} has no "
                                  f"{SUBTREE}/{missing[0]}: not an installer payload")
            for rel in EXECUTABLES:
                os.chmod(os.path.join(new, rel), 0o755)
            os.rename(root, aside)
            try:
                os.rename(new, root)
            except OSError:
                os.rename(aside, root)
                raise
        except (ArchiveError, tarfile.TarError, OSError) as exc:
            raise UpdateError(f"installer {release.version} not laid: {exc}") from exc
        finally:
            for leftover in (staging, aside):
                if os.path.lexists(leftover):
                    shutil.rmtree(leftover)
        _record(release.version)
        return release.version
