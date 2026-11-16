"""What a release requires of the machine, checked before it is laid.

The install engine checks `requires.capabilities` and `requires.features`
once, against the wizard's choices, and installs `apt:`. An update replays
none of that, so a release that asks for more than the version it replaces
used to reach its hooks on a machine that could not run them. This module is
the update's side of the same manifest keys, and nothing beyond them.

What it can know is narrower than what the engine knew, and the rules follow
from that rather than hide it:

  - `requires.packages`: every required package must be installed, and not
    left `failed` by an earlier lay, unless it is laid in the same batch.
  - `requires.capabilities` and `requires.features`: what the INSTALLED
    version already required was granted when it was installed (or vouched
    for by the operator who adopted it) and is not asked again. It could not
    be measured again anyway: a passthrough package hides from the host the
    very hardware it required (the console's NVMe leaves /sys/block once
    vfio-pci owns it), and the machine keeps no record of the features the
    wizard selected. Only what the release ADDS is checked. An added
    capability is measured on the running machine. An added feature cannot
    be verified at all, for want of that record, and is refused by name.
  - `apt:`: the engine installs these leniently - a failed apt install is a
    warning there, by design, because a package may run without an optional
    dependency. The update installs nothing; it names the packages the
    release adds that are not installed, as a warning, and does not refuse.
"""
from __future__ import annotations

import subprocess

from . import state as state_mod
from .capabilities import detect_capabilities
from .manifest import Manifest

DPKG_TIMEOUT = 60


class RequirementError(RuntimeError):
    """Raised when the machine does not meet what a release requires."""


def _added(new: tuple, old: tuple) -> list[str]:
    return sorted(set(new) - set(old))


def check_packages(staged: list[Manifest], current_state: dict) -> None:
    """Refuse a release whose required packages are missing or failed."""
    batch = {manifest.name for manifest in staged}
    for manifest in staged:
        for dependency in manifest.packages:
            record = current_state.get(dependency)
            if record is None:
                raise RequirementError(f"{manifest.name}: requires package "
                                       f"{dependency!r}, which is not installed")
            if (dependency not in batch
                    and state_mod.status(record) == state_mod.FAILED):
                raise RequirementError(f"{manifest.name}: requires package "
                                       f"{dependency!r}, whose last update failed")


def check_machine(staged: Manifest, installed: Manifest, hw: dict) -> None:
    """Refuse a release that adds a capability or a feature the machine lacks."""
    what = f"{staged.name}: release {staged.version}"
    added = _added(staged.capabilities, installed.capabilities)
    missing = [c for c in added if c not in detect_capabilities(hw)]
    if missing:
        raise RequirementError(
            f"{what} requires hardware this machine does not have: "
            f"{', '.join(missing)} (not required by the installed "
            f"{installed.version})")
    added = _added(staged.features, installed.features)
    if added:
        raise RequirementError(
            f"{what} requires installer feature(s) the installed "
            f"{installed.version} did not: {', '.join(added)}. This machine "
            "keeps no record of the features it was installed with, so they "
            "cannot be verified and the release is not laid")


def dpkg_installed(names: list[str]) -> set[str]:
    """The subset of `names` dpkg reports as installed.

    dpkg-query exits 1 as soon as one name is unknown to it and still prints
    the others, so its exit status is not the answer; its output is.
    """
    proc = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Status}\n", *names],
        capture_output=True, text=True, timeout=DPKG_TIMEOUT)
    installed = set()
    for line in proc.stdout.splitlines():
        package, _, status = line.partition("\t")
        if status == "installed":
            installed.add(package)
    return installed


def missing_apt(staged: Manifest, installed: Manifest,
                query=dpkg_installed) -> list[str]:
    """apt packages the release adds that are not installed on this machine."""
    added = _added(staged.apt, installed.apt)
    if not added:
        return []
    try:
        present = query(added)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequirementError(
            f"{staged.name}: release {staged.version} adds apt package(s) "
            f"{', '.join(added)} and dpkg-query could not say whether they "
            f"are installed ({exc})") from exc
    return [name for name in added if name not in present]
