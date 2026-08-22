#!/usr/bin/env python3
"""Build-time acquisition of the payload binaries that are not already local.

Networking is allowed HERE and nowhere else: the guest provisions offline, so
a URL that rots must break the build, never an install. Nothing in this module
is imported by the guest-facing code paths.

Usage:
    sudo python3 fetch_payload.py --drivers-dir /media/data/nivuus-win-payload
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class FetchError(RuntimeError):
    """Raised when a payload binary cannot be obtained."""


@dataclass(frozen=True)
class Download:
    name: str
    url: str
    dest: Path


# virtio-win is fetched as a whole ISO and mined for two drivers: the stable
# repository publishes no per-driver artifact.
VIRTIO_ISO_URL = ("https://fedorapeople.org/groups/virt/virtio-win/"
                  "direct-downloads/stable-virtio/virtio-win.iso")
STEAM_URL = "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe"
WINFSP_URL = ("https://github.com/winfsp/winfsp/releases/download/"
              "v2.0/winfsp-2.0.23075.msi")


def plan_downloads(drivers_dir: Path) -> list[Download]:
    """Pure: what would be fetched, and where each file would land."""
    return [
        Download("steam", STEAM_URL, drivers_dir / "steam" / "SteamSetup.exe"),
        Download("winfsp", WINFSP_URL,
                 drivers_dir / "winfsp" / "winfsp-2.0.23075.msi"),
        Download("virtio-iso", VIRTIO_ISO_URL,
                 drivers_dir / "virtio" / "virtio-win.iso"),
    ]


def fetch(item: Download) -> str:
    """Download one item unless it is already there. Returns its sha256."""
    item.dest.parent.mkdir(parents=True, exist_ok=True)
    if not item.dest.exists():
        print(f"fetching {item.name} <- {item.url}")
        try:
            with urllib.request.urlopen(item.url, timeout=120) as resp, \
                 open(item.dest, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except OSError as exc:
            item.dest.unlink(missing_ok=True)
            raise FetchError(f"cannot fetch {item.name}: {exc}") from exc
    else:
        print(f"keeping existing {item.dest}")
    digest = hashlib.sha256()
    with open(item.dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# w11/amd64 is the 24H2 driver set; the guest is build 26100.
VIRTIO_MEMBERS = {"netkvm": "NetKVM/w11/amd64", "viofs": "viofs/w11/amd64"}


def extract_virtio(iso: Path, drivers_dir: Path) -> None:
    """Pull NetKVM and viofs out of the virtio-win ISO with 7z."""
    for name, member in VIRTIO_MEMBERS.items():
        dest = drivers_dir / "virtio" / name
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["7z", "x", "-y", f"-o{dest}", str(iso), f"{member}/*"],
            text=True, capture_output=True)
        if proc.returncode != 0:
            raise FetchError(f"cannot extract {member} from {iso}: "
                             f"{proc.stderr.strip()}")
        # 7z recreates the archive tree; flatten it so the guest step can point
        # at one directory instead of guessing the vendor's layout.
        for path in (dest / member.split("/")[0]).rglob("*"):
            if path.is_file():
                path.replace(dest / path.name)
    print(f"extracted {', '.join(VIRTIO_MEMBERS)} from {iso.name}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the B payload binaries")
    ap.add_argument("--drivers-dir", required=True)
    args = ap.parse_args(argv)
    drivers = Path(args.drivers_dir)
    try:
        for item in plan_downloads(drivers):
            print(f"  {item.name} sha256 {fetch(item)}")
        extract_virtio(drivers / "virtio" / "virtio-win.iso", drivers)
    except FetchError as exc:
        raise SystemExit(str(exc))
    print("\nNot fetched, and never fetchable: agent/agent.exe must be "
          "extracted from the current Windows VM before it is wiped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
