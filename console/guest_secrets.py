"""Single source of truth for where the guest secrets live on the host.

console/guest_steps.py's `secrets` step writes the three secrets the guest
build needs (0600, never on argv) under the guest workdir. Everything that
later READS one of them must name the same file:

  * console/guest/winrm_exec.py, whose default password file this is;
  * console/guest/retro_sync.py, which runs winrm_exec.py as a child;
  * console/guest/build.py, whose manual-run defaults these are.

Before this module, winrm_exec.py and build.py defaulted to
/root/.config/nivuus/windows-admin.pass, a hand-made file the activate
pipeline never writes: retro_sync.py failed with "password file not found"
on a console installed through activate, or silently used a stale password
on a host that still had the old file.

The guest workdir is a wizard answer (`guest_workdir`); the paths below are
those of its default. A console installed with another workdir must pass
GUEST_PASS_FILE (or build.py's --*-file options) explicitly.
"""
from __future__ import annotations

# console/wizard.yaml's default for the 'guest_workdir' answer.
DEFAULT_GUEST_WORKDIR = "/var/lib/nivuus/guest"

# The three secrets, mapped to the file name the `secrets` step writes and
# guest/build.py reads.
SECRET_FILES = {
    "ltsc_key": "windows-ltsc.key",
    "admin_password": "windows-admin.pass",
    "apollo_password": "apollo-ui.pass",
}


def secret_path(key: str, workdir: str = DEFAULT_GUEST_WORKDIR) -> str:
    """Where the `secrets` step writes `key` for a given guest workdir."""
    return f"{workdir}/secrets/{SECRET_FILES[key]}"


# The guest's administrator password, as written by activate.
ADMIN_PASSWORD_FILE = secret_path("admin_password")
