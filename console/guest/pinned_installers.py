"""The two vendor installers the guest image needs and nobody used to fetch.

payload.REQUIRED_BINARIES has always demanded an NVIDIA display driver in
drivers/nvidia/ and the Apollo installer in drivers/apollo/, and build.py
refuses to build without them - but fetch_payload.py never downloaded
either. A machine installed by the package therefore stopped at `build` with
"offline payload incomplete", and the only way past was copying them in by
hand from wherever an earlier build had left them (measured on the reference
host 2026-10-08: they sat in /media/data/nivuus-win-payload, staged by hand
on 2026-08-22, and nowhere the package looks).

Both are PINNED to one release AND one sha256, unlike Steam and virtio-win:
- they are the versions the production guest runs (NVIDIA 610.88, Apollo
  0.4.6 - the 2026-08-22 files, byte-identical to what the URLs below serve,
  same Content-Length, same digest), and the guest's display path was
  measured on exactly that pair (HDR, SudoVDA, `headless_mode`: see
  docs/claude/host-cloud-gaming-apollo.md);
- Apollo 0.4.6 changed its API authentication from Basic to a cookie, which
  broke a reverse-proxy route: a driver or streamer that moves on its own is
  a regression nobody chose.
Bumping one means changing its version, URL and digest together.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PinnedInstaller:
    name: str
    subdir: str
    filename: str
    url: str
    sha256: str


NVIDIA_VERSION = "610.88"
APOLLO_VERSION = "0.4.6"

PINNED = (
    PinnedInstaller(
        name="nvidia",
        subdir="nvidia",
        filename=(f"{NVIDIA_VERSION}-desktop-win10-win11-64bit-"
                  "international-dch-whql.exe"),
        url=(f"https://us.download.nvidia.com/Windows/{NVIDIA_VERSION}/"
             f"{NVIDIA_VERSION}-desktop-win10-win11-64bit-"
             "international-dch-whql.exe"),
        sha256="576a90c6f6eea47748db1defcbca1746c81fe4eb972f3132d30878778cee0b09",
    ),
    PinnedInstaller(
        name="apollo",
        subdir="apollo",
        filename=f"Apollo-{APOLLO_VERSION}.exe",
        url=("https://github.com/ClassicOldSong/Apollo/releases/download/"
             f"v{APOLLO_VERSION}/Apollo-{APOLLO_VERSION}.exe"),
        sha256="42b2aefaacb3474511517a56b96ee9f0517f30ac38b5dd2fda9fd5b478f5021a",
    ),
)


def prune_stale(drivers_dir: Path) -> list[str]:
    """Drop installers of a version this build no longer pins.

    10-nvidia.ps1 and 25-apollo.ps1 take the FIRST `*.exe` of their
    directory, and drivers/ persists between builds: after a bump, the old
    installer would sit beside the new one and could be the one installed.
    Same defect, same answer as fetch_payload.prune_stale_retro.
    """
    removed = []
    for pin in PINNED:
        for stale in sorted((drivers_dir / pin.subdir).glob("*.exe")):
            if stale.name != pin.filename:
                stale.unlink()
                removed.append(f"{pin.subdir}/{stale.name}")
    return removed
