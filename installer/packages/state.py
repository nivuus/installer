"""The installed machine's package state, /etc/nivuus/packages.json.

The install engine writes one record per package at install time:
`{"version": ..., "answers": {...}, "facts": {...}}`. activate_cli.py reads it
at first boot; the updater reads it to know what is installed and writes it
back after each lay. This module is the one place that reads and writes it
on the installed machine.

Three keys extend the record without breaking an engine-written file:
    state           "installed" | "failed" - absent reads "installed"
    target_version  the version a failed lay was aiming at
    updated_at      ISO 8601 timestamp of the last successful lay
plus `error`, the message of the failure, kept next to `target_version` so
`nivuus status` can show why without a journal. No migration is needed: a
file the current installer wrote carries none of these keys and is read as
installed.

The file is 0600 because it holds wizard answers verbatim, secrets included
(the console's Windows administrator password). It is written atomically -
a temporary file in the same directory then os.replace - because a
half-written state file would lose every package's answers at once.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

# Overridable, like NIVUUS_PACKAGES_DIR, so an end-to-end run can be pointed
# at a scratch root on a production machine without touching it.
STATE_FILE = os.environ.get("NIVUUS_STATE_FILE", "/etc/nivuus/packages.json")
STAMP_DIR = os.environ.get("NIVUUS_STAMP_DIR", "/var/lib/nivuus/packages")

INSTALLED = "installed"
FAILED = "failed"


class StateError(RuntimeError):
    """Raised when the state file exists but cannot be used."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(path: str | None = None) -> dict:
    """The state mapping; {} when no package was ever recorded."""
    path = path or STATE_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"{path}: cannot be read ({exc})") from exc
    if not isinstance(data, dict) or not all(
            isinstance(v, dict) for v in data.values()):
        raise StateError(f"{path}: expected a mapping of package records")
    return data


def save(state: dict, path: str | None = None) -> None:
    """Write `state` atomically with mode 0600."""
    path = path or STATE_FILE
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".packages.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def status(record: dict) -> str:
    """`installed` or `failed`; an engine-written record reads installed."""
    return record.get("state", INSTALLED)


def mark_installed(state: dict, name: str, version: str,
                   answers: dict | None = None) -> None:
    """Record a successful lay. Keeps answers and facts already recorded."""
    record = state.setdefault(name, {})
    record["version"] = version
    record["state"] = INSTALLED
    record["updated_at"] = _now()
    if answers is not None:
        record["answers"] = answers
    record.pop("target_version", None)
    record.pop("error", None)
    # What adoption described - a copy laid by hand from some directory - is
    # no longer what runs once a release has been laid over it.
    record.pop("adopted_from", None)


def mark_failed(state: dict, name: str, target_version: str,
                error: str) -> None:
    """Record a failed lay. `version` still names the last successful one."""
    record = state.setdefault(name, {})
    record["state"] = FAILED
    record["target_version"] = target_version
    record["error"] = error
