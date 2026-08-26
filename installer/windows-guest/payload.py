"""Assembly and verification of the offline /nivuus payload.

Everything the guest will ever need must be here: provisioning runs offline.
A missing binary fails the build, never the install.

ONE deliberate exception, and only when retrogaming is enabled: 32-retro.ps1
runs `retro install`, which downloads the emulators from their vendors. They
are gigabytes, they change on their own schedule, and freezing them into the
image would mean rebuilding it to refresh one of them - while the install
itself is idempotent and replayable. What still travels offline is everything
that install NEEDS: the interpreter, 7zr.exe and the whole wheel closure. The
emulator archives are pinned by sha256 in the package's manifest, so the worst
case there is a named failure, never a silent substitution - and 32-retro.ps1
records that failure on the persistent volume rather than only in the log.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = "PAYLOAD.id"
PROVISION_VERSION = "B2"
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
    # Branding shipped to the guest: the shell paints it itself, because
    # without explorer.exe there is no desktop and therefore no wallpaper.
    assets_dir: Path | None = None


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


# The one subdirectory of drivers/ that exists only for retrogaming. Named
# once, because plan_payload() drops it BY THAT NAME when the option is off.
RETRO_DIRNAME = "retro"
_WHEELS = f"{RETRO_DIRNAME}/wheels"

# Retrogaming rides in drivers/retro/, fetched only by `fetch_payload.py
# --retro`. The wheels are named one by one rather than checked as "the
# directory is not empty": a `pip download` that silently resolved no
# dependency would leave a wheels/ holding the retro wheel alone, and the
# guest would then fail offline, deep inside provisioning, on an import.
RETRO_BINARIES = [
    (RETRO_DIRNAME, "7zr.exe",
     "7zr.exe (RetroArch's archives use the BCJ2 filter, which py7zr cannot "
     "read, and no .zip variant exists - without it no retro emulator installs)"),
    (RETRO_DIRNAME, "python-*-amd64.exe",
     "Python installer (LTSC ships none, and the retro package is Python)"),
    (_WHEELS, "retro-*.whl", "retro package wheel"),
    (_WHEELS, "py7zr-*.whl", "py7zr wheel (retro dependency)"),
    (_WHEELS, "vdf-*.whl", "vdf wheel (retro dependency)"),
    (_WHEELS, "requests-*.whl", "requests wheel (retro dependency)"),
]


def missing_binaries(drivers_dir: Path, retro: bool = False) -> list[str]:
    """Return a human-readable list of the offline binaries not provided.

    `retro` comes from the rendered toggle (see retro_enabled), never from
    the directory's own presence: asking "is drivers/retro/ there?" would
    read a fetch that failed halfway as "retrogaming was not wanted", the one
    confusion this whole option is written to avoid.
    """
    missing = []
    required = REQUIRED_BINARIES + (RETRO_BINARIES if retro else [])
    for subdir, pattern, what in required:
        where = drivers_dir.joinpath(*subdir.split("/"))
        if not list(where.glob(pattern)):
            missing.append(f"{what} ({pattern}) in {where}")
    return missing


def retro_enabled(config_dir: Path) -> bool:
    """Read the retrogaming toggle this build rendered for the guest.

    What the payload must CARRY follows the very file the guest will READ:
    32-retro.ps1 decides what to install from config/retro.psd1, so the
    build requires exactly what that same file promises. Threading a second
    flag down from the command line would create two places to keep in sync,
    and their divergence would only surface an hour into provisioning, on a
    machine with no screen - a payload claiming Enabled = $true with no
    drivers/retro/ in it.

    A toggle that says neither is a hard error, never a silent "off": that
    is the same confusion (absent vs. explicitly disabled) the file exists
    to prevent.
    """
    path = config_dir / "retro.psd1"
    if not path.is_file():
        raise PayloadError(f"missing the retrogaming toggle: {path}")
    text = path.read_text()
    if re.search(r"Enabled\s*=\s*\$true", text):
        return True
    if re.search(r"Enabled\s*=\s*\$false", text):
        return False
    raise PayloadError(
        f"{path} carries no 'Enabled = $true' nor 'Enabled = $false': the "
        "retrogaming state cannot be guessed, and guessing 'off' would "
        "silently drop the feature from a build that asked for it")


def _walk(src_dir: Path, prefix: str) -> list[tuple[Path, str]]:
    entries = []
    for path in sorted(src_dir.rglob("*")):
        # Any dot-directory (e.g. drivers/virtio/.build-cache/, where
        # fetch_payload.py keeps the source virtio-win.iso and its manifest)
        # is host-side bookkeeping, not something the guest needs - one rule
        # keeps the payload exactly "what the guest receives".
        if any(part.startswith(".") for part in path.relative_to(src_dir).parts[:-1]):
            continue
        if path.is_file():
            entries.append((path, f"{prefix}/{path.relative_to(src_dir).as_posix()}"))
    return entries


def plan_payload(sources: PayloadSources) -> list[tuple[Path, str]]:
    """Map each source file to its destination path relative to /nivuus.

    drivers/retro/ is dropped when the toggle says retrogaming is off: the
    drivers directory is a persistent working tree, so an earlier
    `fetch_payload.py --retro` leaves ~30 MB behind that a later build with
    the option unchecked would otherwise still ship - the very cost the
    option exists to avoid, and a guest carrying an interpreter and a
    wheelhouse it is told never to open.
    """
    retro = (retro_enabled(sources.config_dir)
             if sources.config_dir is not None else False)
    drivers = [(src, rel) for src, rel in _walk(sources.drivers_dir, "drivers")
               if retro or not rel.startswith(f"drivers/{RETRO_DIRNAME}/")]
    entries = (_walk(sources.provision_dir, "provision")
               + _walk(sources.probe_dir, "probe")
               + drivers)
    if sources.config_dir is not None:
        entries += _walk(sources.config_dir, "config")
    if sources.assets_dir is not None:
        entries += _walk(sources.assets_dir, "assets")
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
    if sources.assets_dir is not None:
        src_paths.add(sources.assets_dir.resolve())
    if dest_resolved in src_paths:
        raise PayloadError(f"dest_root cannot be a source directory: {dest_root}")
    if dest_resolved.parent == dest_resolved or not dest_resolved.name:
        raise PayloadError(f"dest_root cannot be filesystem root: {dest_root}")
    # The rendered toggle decides what this payload must contain (see
    # retro_enabled). No config_dir at all is a caller that renders no
    # configuration, therefore no retrogaming either.
    retro = (retro_enabled(sources.config_dir)
             if sources.config_dir is not None else False)
    missing = missing_binaries(sources.drivers_dir, retro=retro)
    if missing:
        error_msg = ("offline payload incomplete, refusing to build:\n  - "
                     + "\n  - ".join(missing))
        if any("agent.exe" in item for item in missing):
            error_msg += (
                "\n\nagent.exe must be extracted from the current Windows VM "
                "BEFORE it is wiped - no machine can rebuild it afterwards."
            )
        if retro:
            # Name the command, the way every guest-side message here does: a
            # build that stops at "7zr.exe is missing" without saying how to
            # get it sends the owner reading source at the one moment they
            # just wanted their image.
            error_msg += (
                "\n\nconfig/retro.psd1 says Enabled = $true, so the retro "
                "artefacts above are required. Fetch them with:\n"
                f"  python3 fetch_payload.py --drivers-dir {sources.drivers_dir}"
                " --retro"
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
        "provision/assets/run-agent.ps1",
        "provision/assets/steam-session.ps1",
        "provision/assets/steam-launch.ps1",
        "provision/assets/apollo-junction.ps1",
        "provision/assets/steam-shell.ps1",
        # Dot-sources par 32-retro.ps1 AVANT qu'elle lise le basculement, donc
        # requis meme sans retrogaming: absents, l'etape meurt au lieu de dire
        # posement que l'option n'est pas cochee.
        "provision/assets/retro-status.ps1",
        "provision/assets/retro-7zr.ps1",
        "assets/wallpaper.png",
        "probe/advanced-color.ps1",
        "config/sunshine.conf",
        "config/apps.json",
        "config/secrets.psd1",
        "config/retro.psd1",
    ]
    for rel in required:
        path = dest_root / rel
        if not path.is_file() or path.stat().st_size == 0:
            raise PayloadError(f"staged payload is missing or empty: {rel}")
    # Required above, so it is there: the staged toggle is the same one the
    # guest will read, and it alone says whether drivers/retro/ is required.
    missing = missing_binaries(dest_root / "drivers",
                               retro=retro_enabled(dest_root / "config"))
    if missing:
        raise PayloadError(
            "staged payload is incomplete:\n  - "
            + "\n  - ".join(missing)
        )
