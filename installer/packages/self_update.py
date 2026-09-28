"""`nivuus update --self`: laying a newer release of the installer payload.

The installer is not a package - nothing installs it but itself - yet it
publishes releases like the rest of the suite, and the updater, the CLI and
the first-boot activation all live in it. This module lays the release's
`installer/` subtree over the directory this code runs from (the payload's
`installer/`, /opt/nivuus/installer on a target), with the updater's own
rules: verified archive and safe extraction, then two things specific to
replacing the code that is running:

  - the staged tree must PROVE it works before it replaces anything: its own
    CLI is run from staging, so a release missing a module the CLI imports is
    refused, rather than discovered by the next invocation;
  - the swap is one atomic renameat2(RENAME_EXCHANGE): there is no instant at
    which the directory is missing, so a kill mid-update cannot leave
    /usr/local/sbin/nivuus pointing into nothing - which would leave no
    command to recover with.

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
import ctypes
import subprocess
import sys
from datetime import datetime, timezone

from . import releases, state as state_mod
from .archive import ArchiveError, extract
from .updater import CACHE_DIR, UpdateError, lock

INSTALLER_REPO = os.environ.get("NIVUUS_INSTALLER_REPO", "nivuus/installer")
RECORD_NAME = "installer.json"
SUBTREE = "installer"
CLI_REL = "packages/nivuus_cli.py"
EXECUTABLES = (CLI_REL, "packages/activate_cli.py")
SMOKE_TIMEOUT = 60
# linux/fs.h: swap two paths atomically.
RENAME_EXCHANGE = 2
AT_FDCWD = -100

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


def _exchange(a: str, b: str) -> None:
    """Swap directories `a` and `b` in one syscall (Linux >= 3.15, ext4)."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(AT_FDCWD, a.encode(), AT_FDCWD, b.encode(),
                      RENAME_EXCHANGE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"atomic exchange of {a} and {b} refused: "
                           f"{os.strerror(err)}")


def _smoke(new: str, release) -> None:
    """Run the staged CLI; a payload that cannot even print its usage is refused."""
    cli = os.path.join(new, CLI_REL)
    if not os.path.isfile(cli):
        raise UpdateError(f"{release.repo} {release.tag} has no {SUBTREE}/{CLI_REL}: "
                          "not an installer payload")
    proc = subprocess.run([sys.executable, cli, "help"], capture_output=True,
                          text=True, timeout=SMOKE_TIMEOUT)
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise UpdateError(f"{release.repo} {release.tag}: its nivuus command does "
                          f"not run from staging ({tail})")


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
        root = os.path.realpath(root)
        staging = os.path.join(os.path.dirname(root), ".nivuus-installer-staging")
        if os.path.lexists(staging):
            shutil.rmtree(staging)
        try:
            with tarfile.open(archive) as tar:
                extract(tar, staging)
            new = os.path.join(staging, SUBTREE)
            # A link here would be moved as a link, and its relative target
            # would mean something else once in place.
            if os.path.islink(new) or not os.path.isdir(new):
                raise UpdateError(f"{release.repo} {release.tag}: {SUBTREE}/ is "
                                  "not a plain directory in the archive")
            _smoke(new, release)
            for rel in EXECUTABLES:
                os.chmod(os.path.join(new, rel), 0o755)
            # After this, `new` holds the previous payload, removed below.
            _exchange(new, root)
        except (ArchiveError, tarfile.TarError, OSError,
                subprocess.TimeoutExpired) as exc:
            raise UpdateError(f"installer {release.version} not laid: {exc}") from exc
        finally:
            if os.path.lexists(staging):
                shutil.rmtree(staging)
        _record(release.version)
        return release.version
