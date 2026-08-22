"""Assembly and verification of the offline /nivuus payload.

Everything the guest will ever need must be here: provisioning runs with no
network at all. A missing binary fails the build, never the install.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = "PAYLOAD.id"
PROVISION_VERSION = "A1"
TARGET_BUILD = "26100"


class PayloadError(RuntimeError):
    """Raised when the payload is incomplete or cannot be staged."""


@dataclass(frozen=True)
class PayloadSources:
    provision_dir: Path
    probe_dir: Path
    drivers_dir: Path


def missing_binaries(drivers_dir: Path) -> list[str]:
    """Return a human-readable list of the offline binaries not provided."""
    missing = []
    if not list((drivers_dir / "nvidia").glob("*.exe")):
        missing.append(
            f"NVIDIA driver installer (*.exe) in {drivers_dir / 'nvidia'}"
        )
    sudovda_dir = drivers_dir / "sudovda"
    sudovda_files = ["SudoVDA.inf", "install.bat", "sudovda.cer"]
    for fname in sudovda_files:
        if not (sudovda_dir / fname).exists():
            missing.append(
                f"SudoVDA driver file {fname} in {sudovda_dir} - "
                "copy from a machine's C:\\Program Files\\Apollo\\drivers\\sudovda"
            )
    return missing


def _walk(src_dir: Path, prefix: str) -> list[tuple[Path, str]]:
    entries = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            entries.append((path, f"{prefix}/{path.relative_to(src_dir).as_posix()}"))
    return entries


def plan_payload(sources: PayloadSources) -> list[tuple[Path, str]]:
    """Map each source file to its destination path relative to /nivuus."""
    return (_walk(sources.provision_dir, "provision")
            + _walk(sources.probe_dir, "probe")
            + _walk(sources.drivers_dir, "drivers"))


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
    if dest_resolved in src_paths:
        raise PayloadError(f"dest_root cannot be a source directory: {dest_root}")
    if dest_resolved.parent == dest_resolved or not dest_resolved.name:
        raise PayloadError(f"dest_root cannot be filesystem root: {dest_root}")
    missing = missing_binaries(sources.drivers_dir)
    if missing:
        raise PayloadError(
            "offline payload incomplete, refusing to build:\n  - "
            + "\n  - ".join(missing)
        )
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
        "probe/advanced-color.ps1",
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
