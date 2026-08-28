"""Single source of truth for the retrogaming toggle's path on disk.

Two callers must agree on exactly one location, at two different times:

  * console/hooks/install.py writes it, mid-install, under the target
    being built (e.g. /mnt/target/etc/nivuus/retro.json).
  * console/guest/build.py reads it later - and separately, possibly by
    hand, possibly on this very host once it has booted and its old
    install target has become "/" (e.g. /etc/nivuus/retro.json).

Before this module existed, each side carried its own copy of that path as
an independent string literal. Renaming either one without the other broke
the connection silently: retro checked in the wizard, nothing installed on
the guest, no test failing to say so. Defining the path here once, and
having both sides import it, makes that particular failure impossible to
reintroduce by a one-sided edit - see
scripts/tests/test_retro_marker_bridge.py, which proves the two callers
still agree by driving the real hook and reading the result with build.py's
own default.

This module used to live in installer's shared `common` package, one file
shared across the installer/ and console/ trees. The console-invite-structure
split moved both callers inside console/, so the bridge is now entirely
internal to this package: console/ no longer imports anything from installer/.
"""
from __future__ import annotations

import os

# Relative to whatever root currently applies: an install target while
# console/hooks/install.py is writing it, or "/" once that host has booted
# and console/guest/build.py is reading it.
RETRO_STATE_REL_PATH = "etc/nivuus/retro.json"


def retro_state_path(root: str = "/") -> str:
    """Join RETRO_STATE_REL_PATH under `root` (an install target, or "/")."""
    return os.path.join(root, RETRO_STATE_REL_PATH)
