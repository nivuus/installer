"""Build the small secondary ISO that Windows Setup reads the answer file from.

The Windows medium itself is never modified. Setup searches the root of every
removable read-only drive for autounattend.xml, so a second CD-ROM can carry
both the answer file and the offline payload.

Why not reinject into the Windows medium, as first designed: xorriso 1.5.6 has
no UDF support (verified: `xorriso -as mkisofs -help | grep -i udf` is empty),
and a Windows 11 24H2 install.wim exceeds 4 GiB - a size only UDF carries on a
Windows medium, since Windows' CDFS driver cannot read ISO9660 multi-extent
files. Rebuilding the medium would produce an install.wim Setup cannot read.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

VOLID = "NIVUUS_UA"


class IsoError(RuntimeError):
    """Raised when the unattend ISO cannot be built or is unusable."""


def xorriso_command(staging_dir, output_iso, volid: str = VOLID) -> list[str]:
    """Build the xorriso argument list. Pure, so it can be asserted on."""
    return [
        "xorriso", "-as", "mkisofs",
        "-iso-level", "3",       # lifts the 4 GiB single-file limit
        "-J", "-joliet-long",    # Joliet is the tree Windows reads
        "-rational-rock",
        "-volid", volid,
        "-o", str(output_iso), str(staging_dir),
    ]


def build_iso(staging_dir, output_iso, volid: str = VOLID) -> None:
    cmd = xorriso_command(staging_dir, output_iso, volid)
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise IsoError(f"xorriso failed ({proc.returncode}): {proc.stderr.strip()}")


def list_iso(iso_path) -> list[str]:
    """Return the absolute path of every file in the ISO."""
    proc = subprocess.run(
        ["xorriso", "-indev", str(iso_path), "-find", "/", "-type", "f"],
        text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise IsoError(f"cannot read {iso_path}: {proc.stderr.strip()}")
    # xorriso quotes paths with POSIX shell escaping for embedded quotes.
    return shlex.split(proc.stdout)


def verify_iso(iso_path) -> None:
    """Assert the ISO carries what Setup and the guest bootstrap depend on."""
    listing = list_iso(iso_path)
    for required in ("/autounattend.xml", "/nivuus/PAYLOAD.id",
                     "/nivuus/provision/run-all.ps1"):
        if required not in listing:
            raise IsoError(
                f"{Path(iso_path).name} is missing {required} "
                f"({len(listing)} files present)"
            )
