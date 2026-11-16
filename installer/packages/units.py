"""Keeping the installer's own systemd units in step with its release.

The installer ships a few units under configs/systemd/ that the install
engine copies to /etc/systemd/system: the daily release check and the
first-boot package activation. `nivuus update --self` lays the code; this
module lays those units from the same verified archive.

Rules:
  - the unit files are READ from the archive (extractfile), never extracted
    to disk: only regular files at their exact expected path are taken;
  - only units ALREADY present on the machine are replaced - which units a
    machine runs is the install engine's decision, not the updater's;
  - a unit is rewritten only when its content differs, atomically, 0644;
  - when anything changed, systemd is reloaded, and a changed timer that is
    enabled is restarted so its new schedule applies now. Both go through
    the D-Bus system bus (busctl), which also works from a session whose PID
    namespace breaks systemctl's private socket;
  - that reload is owed until it has succeeded. Each unit is noted in
    STAMP_DIR/units-reload.pending BEFORE its file is replaced, and the note
    is removed only once systemd has applied it: a reload that failed (D-Bus
    away for a moment) is replayed by the next run, which would otherwise
    find the files identical, change nothing and leave systemd on the old
    definitions - the timer's old schedule included - until a reboot.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile

from . import state as state_mod

UNIT_DIR = os.environ.get("NIVUUS_UNIT_DIR", "/etc/systemd/system")
MANAGED = ("nivuus-check.service", "nivuus-check.timer",
           "nivuus-package-activate@.service")
SOURCE_DIR = "configs/systemd"

SYSTEMD = ["busctl", "--system", "call", "org.freedesktop.systemd1",
           "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager"]
# A bus that does not answer must fail the run, not hang the daily timer.
SYSTEMD_TIMEOUT = 60
PENDING_NAME = "units-reload.pending"


class UnitError(RuntimeError):
    """Raised when a unit cannot be read, written or reloaded."""


def _systemd(*args: str) -> str:
    try:
        proc = subprocess.run([*SYSTEMD, *args], capture_output=True, text=True,
                              timeout=SYSTEMD_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise UnitError(f"systemd {args[0]} did not answer within "
                        f"{SYSTEMD_TIMEOUT}s") from exc
    if proc.returncode != 0:
        raise UnitError(f"systemd {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


def systemd_apply(changed: list[str]) -> None:
    """Reload systemd, then restart each changed timer that is enabled."""
    _systemd("Reload")
    for unit in changed:
        if unit.endswith(".timer") and '"enabled"' in _systemd(
                "GetUnitFileState", "s", unit):
            _systemd("RestartUnit", "ss", unit, "replace")


def _from_archive(tar: tarfile.TarFile, unit: str) -> bytes | None:
    for name in (f"{SOURCE_DIR}/{unit}", f"./{SOURCE_DIR}/{unit}"):
        try:
            member = tar.getmember(name)
        except KeyError:
            continue
        if not member.isreg():
            raise UnitError(f"{name} in the release is not a regular file")
        return tar.extractfile(member).read()
    return None


def _pending_path() -> str:
    return os.path.join(state_mod.STAMP_DIR, PENDING_NAME)


def _read_pending(path: str) -> list[str] | None:
    """Units whose reload is still owed, or None when nothing is owed.

    One name per line. A note that exists but lists nothing usable (emptied,
    damaged) still owes a reload: it reads as [], not as None.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            names = fh.read().split()
    except FileNotFoundError:
        return None
    return [unit for unit in MANAGED if unit in names]


def _note_pending(path: str, owed: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{PENDING_NAME}.",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{unit}\n" for unit in owed))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def refresh(archive: str, unit_dir: str = UNIT_DIR,
            apply=systemd_apply, pending: str | None = None) -> list[str]:
    """Lay the managed units present on this machine from `archive`.

    Returns the units rewritten. A managed unit present here but absent from
    the release is left alone and reported by raising: the release no longer
    ships something this machine runs, which the operator must see.

    `apply` is called with every unit whose reload is owed - those rewritten
    now and those an earlier run rewrote without managing to reload - so the
    list it receives can be longer than the one returned.
    """
    pending = pending or _pending_path()
    noted = _read_pending(pending)
    owed = list(noted or [])
    changed = []
    with tarfile.open(archive) as tar:
        for unit in MANAGED:
            dest = os.path.join(unit_dir, unit)
            if not os.path.isfile(dest):
                continue
            wanted = _from_archive(tar, unit)
            if wanted is None:
                raise UnitError(f"{unit} runs on this machine but the release "
                                f"has no {SOURCE_DIR}/{unit}")
            with open(dest, "rb") as fh:
                if fh.read() == wanted:
                    continue
            if unit not in owed:
                owed.append(unit)
                _note_pending(pending, owed)
            fd, tmp = tempfile.mkstemp(prefix=f".{unit}.", dir=unit_dir)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(wanted)
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            changed.append(unit)
    if changed or noted is not None:
        apply([unit for unit in MANAGED if unit in owed])
        os.unlink(pending)
    return changed
