"""Assembly and verification of the offline /nivuus payload.

Everything the guest will ever need must be here: provisioning runs with no
network at all. A missing binary fails the build, never the install.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = "PAYLOAD.id"
PROVISION_VERSION = "B1"
TARGET_BUILD = "26100"


class PayloadError(RuntimeError):
    """Raised when the payload is incomplete or cannot be staged."""


@dataclass(frozen=True)
class PayloadSources:
    provision_dir: Path
    probe_dir: Path
    drivers_dir: Path
    # Rendered Apollo configuration and the secrets the guest needs. Built at
    # build time into a temporary directory, never checked into the repo.
    config_dir: Path | None = None


# Each entry: (subdirectory, glob, human description). viofs is deliberately
# absent from this list: virtiofs is a comfort mount (the /media/data share),
# and a guest without it still streams. NetKVM is not optional - without it
# the guest has no network at all, so no agent, no wake-on-demand, no
# 192.168.3.2. WinFsp is required on its own even though viofs isn't: it is
# the installer the guest needs staged, independent of whether virtiofs ends
# up used.
REQUIRED_BINARIES = [
    ("nvidia", "*.exe", "NVIDIA display driver installer"),
    ("apollo", "*.exe", "Apollo installer (bundles the virtual display driver)"),
    ("steam", "SteamSetup.exe", "Steam installer"),
    ("virtio/netkvm", "*.inf", "NetKVM virtio-net driver"),
    ("winfsp", "*.msi", "WinFsp installer (virtiofs depends on it)"),
    ("agent", "agent.exe", "Guacamole agent, extracted before the wipe"),
]


def missing_binaries(drivers_dir: Path) -> list[str]:
    """Return a human-readable list of the offline binaries not provided."""
    missing = []
    for subdir, pattern, what in REQUIRED_BINARIES:
        where = drivers_dir.joinpath(*subdir.split("/"))
        if not list(where.glob(pattern)):
            missing.append(f"{what} ({pattern}) in {where}")
    return missing


def _walk(src_dir: Path, prefix: str) -> list[tuple[Path, str]]:
    entries = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            entries.append((path, f"{prefix}/{path.relative_to(src_dir).as_posix()}"))
    return entries


def plan_payload(sources: PayloadSources) -> list[tuple[Path, str]]:
    """Map each source file to its destination path relative to /nivuus."""
    entries = (_walk(sources.provision_dir, "provision")
               + _walk(sources.probe_dir, "probe")
               + _walk(sources.drivers_dir, "drivers"))
    if sources.config_dir is not None:
        entries += _walk(sources.config_dir, "config")
    return entries


def marker_text(image_name: str, build_id: str) -> str:
    return (
        "nivuus_payload=1\n"
        f"target_build={TARGET_BUILD}\n"
        f"provision_version={PROVISION_VERSION}\n"
        f"image_name={image_name}\n"
        f"build_id={build_id}\n"
    )


def parse_marker(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def stage_payload(dest_root: Path, sources: PayloadSources, marker: str) -> None:
    """Copy the payload under dest_root (the future /nivuus of the ISO)."""
    dest_resolved = dest_root.resolve()
    src_paths = {sources.provision_dir.resolve(), sources.probe_dir.resolve(),
                 sources.drivers_dir.resolve()}
    if sources.config_dir is not None:
        src_paths.add(sources.config_dir.resolve())
    if dest_resolved in src_paths:
        raise PayloadError(f"dest_root cannot be a source directory: {dest_root}")
    if dest_resolved.parent == dest_resolved or not dest_resolved.name:
        raise PayloadError(f"dest_root cannot be filesystem root: {dest_root}")
    missing = missing_binaries(sources.drivers_dir)
    if missing:
        error_msg = ("offline payload incomplete, refusing to build:\n  - "
                     + "\n  - ".join(missing))
        if any("agent.exe" in item for item in missing):
            error_msg += (
                "\n\nagent.exe must be extracted from the current Windows VM "
                "BEFORE it is wiped - no machine can rebuild it afterwards."
            )
        raise PayloadError(error_msg)
    if dest_root.exists():
        shutil.rmtree(dest_root)
    for src, rel in plan_payload(sources):
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (dest_root / MARKER_NAME).write_text(marker)


def verify_staged(dest_root: Path) -> None:
    """Fail loudly on anything the guest bootstrap depends on being there."""
    required = [
        MARKER_NAME,
        "provision/run-all.ps1",
        "provision/00-bootstrap.ps1",
        "provision/99-marker.ps1",
        "probe/advanced-color.ps1",
        "config/sunshine.conf",
        "config/apps.json",
        "config/secrets.psd1",
    ]
    for rel in required:
        path = dest_root / rel
        if not path.is_file() or path.stat().st_size == 0:
            raise PayloadError(f"staged payload is missing or empty: {rel}")
    missing = missing_binaries(dest_root / "drivers")
    if missing:
        raise PayloadError(
            "staged payload is incomplete:\n  - "
            + "\n  - ".join(missing)
        )
