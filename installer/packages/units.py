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
    namespace breaks systemctl's private socket.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile

UNIT_DIR = os.environ.get("NIVUUS_UNIT_DIR", "/etc/systemd/system")
MANAGED = ("nivuus-check.service", "nivuus-check.timer",
           "nivuus-package-activate@.service")
SOURCE_DIR = "configs/systemd"

SYSTEMD = ["busctl", "--system", "call", "org.freedesktop.systemd1",
           "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager"]


class UnitError(RuntimeError):
    """Raised when a unit cannot be read, written or reloaded."""


def _systemd(*args: str) -> str:
    proc = subprocess.run([*SYSTEMD, *args], capture_output=True, text=True)
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


def refresh(archive: str, unit_dir: str = UNIT_DIR,
            apply=systemd_apply) -> list[str]:
    """Lay the managed units present on this machine from `archive`.

    Returns the units rewritten. A managed unit present here but absent from
    the release is left alone and reported by raising: the release no longer
    ships something this machine runs, which the operator must see.
    """
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
    if changed:
        apply(changed)
    return changed
