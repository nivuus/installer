"""Putting the package updater on the target: the `nivuus` command and its timer.

The updater itself is code of the payload (installer/packages/updater.py);
what the target needs is the way in to it:

  1. /usr/local/sbin/nivuus, a SYMLINK to installer/packages/nivuus_cli.py in
     the payload - the CLI resolves its own real path to import `packages`
     and `common`, like activate_cli.py, so a copy would not work;
  2. nivuus-check.service + .timer, copied from configs/systemd/, the single
     source of truth for both units;
  3. the timer enabled by a direct symlink into timers.target.wants/, which
     is what `systemctl enable` does and which, unlike systemctl in a chroot,
     either exists or raises.

The timer only checks; it never lays a release. Every copy is mandatory: a
missing file here would leave a machine that believes it follows its
packages and never does.
"""
from __future__ import annotations

import os
import shutil

from .util import StepError

CLI_REL_PATH = "installer/packages/nivuus_cli.py"
CLI_LINK_REL = "usr/local/sbin/nivuus"
UNITS = ("nivuus-check.service", "nivuus-check.timer")
UNIT_SRC_REL_DIR = "configs/systemd"
UNIT_REL_DIR = "etc/systemd/system"
TIMER_WANTS_REL_DIR = "etc/systemd/system/timers.target.wants"


def deploy_updater(target: str, nivuus_dir: str) -> None:
    """Lay the `nivuus` command and arm its daily check on `target`."""
    payload = os.path.join(target, nivuus_dir.lstrip("/"))

    cli = os.path.join(payload, CLI_REL_PATH)
    if not os.path.isfile(cli):
        raise StepError(f"nivuus_cli.py missing from the payload: {cli} - the "
                        "nivuus command would point at nothing")
    os.chmod(cli, 0o755)
    link = os.path.join(target, CLI_LINK_REL)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(os.path.join(nivuus_dir, CLI_REL_PATH), link)

    for unit in UNITS:
        source = os.path.join(payload, UNIT_SRC_REL_DIR, unit)
        if not os.path.isfile(source):
            raise StepError(f"{unit} missing from the payload: {source}")
        dest = os.path.join(target, UNIT_REL_DIR, unit)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(source, dest)
        os.chmod(dest, 0o644)

    wants = os.path.join(target, TIMER_WANTS_REL_DIR)
    os.makedirs(wants, exist_ok=True)
    timer_link = os.path.join(wants, "nivuus-check.timer")
    if os.path.lexists(timer_link):
        os.unlink(timer_link)
    os.symlink("/" + UNIT_REL_DIR + "/nivuus-check.timer", timer_link)
