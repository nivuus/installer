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
import datetime
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

# Retrogaming (OPTIONAL, --retro). Three artefacts the guest cannot obtain on
# its own, ~30 MB in total: a payload built WITHOUT retrogaming carries none
# of them.
#
# 7zr.exe is a REQUIREMENT, not a convenience: RetroArch's archives - and
# only those, among the manifest's - use the BCJ2 compression filter, which
# py7zr marks "Unsupported" in its own code, and the vendor publishes no .zip
# variant. Without it, the emulator covering most of the retro library does
# not install at all.
SEVENZR_URL = "https://www.7-zip.org/a/7zr.exe"
# The guest ships no Python and the `retro` package is Python. This version
# is load-bearing beyond the interpreter itself: py7zr's dependencies are
# compiled wheels, tagged for one minor version, so the wheels downloaded
# below and the interpreter installed on the guest must agree. RETRO_PY_TAG
# is DERIVED from it rather than written twice - the two drifting apart would
# produce wheels no guest Python can install, and pip would only say so at
# provisioning time, offline, on a machine with no screen.
RETRO_PYTHON_VERSION = "3.12.10"
RETRO_PY_TAG = "".join(RETRO_PYTHON_VERSION.split(".")[:2])
RETRO_PYTHON_URL = (f"https://www.python.org/ftp/python/{RETRO_PYTHON_VERSION}"
                    f"/python-{RETRO_PYTHON_VERSION}-amd64.exe")
RETRO_DIRNAME = "retro"
RETRO_WHEELS_DIRNAME = "wheels"
# packages/retro, the neighbouring repository this payload installs from.
RETRO_SRC = Path(__file__).resolve().parents[2].parent / "retro"

# Steam and the virtio-win ISO are deliberately unpinned moving pointers -
# pinning their sha256 would break the build on every upstream refresh. What
# is tracked instead is CHANGE, not a fixed value: the first digest seen for
# a path is recorded here, and any later mismatch fails loudly.
MANIFEST_NAME = "payload-manifest.txt"

# Host-side build bookkeeping that the guest never needs: the source
# virtio-win.iso (already mined for its two drivers by extract_virtio) and
# the fetch manifest. Kept under a dot-directory inside drivers_dir so
# payload._walk (which skips dot-directories) never ships either of them to
# the guest - ~700 MB of dead ISO otherwise rode along in every image.
BUILD_CACHE_DIRNAME = ".build-cache"


def plan_downloads(drivers_dir: Path, retro: bool = False) -> list[Download]:
    """Pure: what would be fetched, and where each file would land.

    The retro artefacts are appended ONLY when the operator asked for
    retrogaming: an installation without it has no reason to grow by an
    interpreter, an extractor and a wheelhouse it will never open.
    """
    items = [
        Download("steam", STEAM_URL, drivers_dir / "steam" / "SteamSetup.exe"),
        Download("winfsp", WINFSP_URL,
                 drivers_dir / "winfsp" / "winfsp-2.0.23075.msi"),
        Download("virtio-iso", VIRTIO_ISO_URL,
                 drivers_dir / BUILD_CACHE_DIRNAME / "virtio-win.iso"),
    ]
    if retro:
        retro_dir = drivers_dir / RETRO_DIRNAME
        items += [
            Download("7zr", SEVENZR_URL, retro_dir / "7zr.exe"),
            Download("retro-python", RETRO_PYTHON_URL,
                     retro_dir / f"python-{RETRO_PYTHON_VERSION}-amd64.exe"),
        ]
    return items


def load_manifest(drivers_dir: Path) -> dict[str, tuple[str, str]]:
    """Parse the trust-on-first-use manifest: relative path -> (sha256, date)."""
    path = drivers_dir / BUILD_CACHE_DIRNAME / MANIFEST_NAME
    entries: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            rel, digest, date = parts
            entries[rel] = (digest, date)
    return entries


def _check_manifest(drivers_dir: Path, item: Download, digest: str) -> None:
    """Record a first-seen digest, or fail loudly if it changed since."""
    manifest_path = drivers_dir / BUILD_CACHE_DIRNAME / MANIFEST_NAME
    rel = item.dest.relative_to(drivers_dir).as_posix()
    recorded = load_manifest(drivers_dir).get(rel)
    if recorded is None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        date = datetime.date.today().isoformat()
        with open(manifest_path, "a") as fh:
            fh.write(f"{rel}\t{digest}\t{date}\n")
        return
    recorded_digest, recorded_date = recorded
    if recorded_digest != digest:
        raise FetchError(
            f"{item.name} ({rel}) changed since it was first recorded on "
            f"{recorded_date}: manifest has {recorded_digest}, got {digest}. "
            "Confirm the change is expected, then delete its entry from "
            f"{manifest_path} and re-run to accept the new digest."
        )


def fetch(item: Download, drivers_dir: Path) -> str:
    """Download one item unless it is already there. Returns its sha256.

    dest.exists() must mean "complete": the stream lands in a sibling
    `.part` file first and is only moved onto dest once fully written, so a
    Ctrl-C (or any interruption) mid-download can never leave a truncated
    file at dest for a later run to mistake for "already fetched".
    """
    item.dest.parent.mkdir(parents=True, exist_ok=True)
    if not item.dest.exists():
        print(f"fetching {item.name} <- {item.url}")
        part = item.dest.with_name(item.dest.name + ".part")
        completed = False
        try:
            with urllib.request.urlopen(item.url, timeout=120) as resp, \
                 open(part, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
            completed = True
        except OSError as exc:
            raise FetchError(f"cannot fetch {item.name}: {exc}") from exc
        finally:
            if not completed:
                part.unlink(missing_ok=True)
        part.replace(item.dest)
    else:
        print(f"keeping existing {item.dest}")
    digest = hashlib.sha256()
    with open(item.dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _check_manifest(drivers_dir, item, hexdigest)
    return hexdigest


# w11/amd64 is the 24H2 driver set; the guest is build 26100.
VIRTIO_MEMBERS = {"netkvm": "NetKVM/w11/amd64", "viofs": "viofs/w11/amd64"}


def flatten_extracted(nested_root: Path, dest: Path) -> None:
    """Flatten a just-extracted tree from nested_root into dest.

    Refuses (raises FetchError) rather than silently overwrite when two
    files share a basename, and refuses any file that resolves outside dest
    - a defensive check against a malicious archive using a symlink to
    escape the destination during extraction.
    """
    dest_resolved = dest.resolve()
    seen: dict[str, Path] = {}
    for path in sorted(nested_root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != dest_resolved and dest_resolved not in resolved.parents:
            raise FetchError(f"extracted path escapes {dest}: {path} -> {resolved}")
        if path.name in seen:
            raise FetchError(
                f"extracted files collide on the name {path.name!r}: "
                f"{seen[path.name]} and {path}"
            )
        seen[path.name] = path
        path.replace(dest / path.name)


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
        # 7z recreates the archive tree; flatten it so the guest step can
        # point at one directory instead of guessing the vendor's layout.
        nested_root = dest / member.split("/")[0]
        if nested_root.is_dir():
            flatten_extracted(nested_root, dest)
    print(f"extracted {', '.join(VIRTIO_MEMBERS)} from {iso.name}")


def _pip(args: list[str], what: str) -> None:
    """Run pip on THIS host, and fail loudly with its own diagnostic."""
    proc = subprocess.run([sys.executable, "-m", "pip", *args],
                          text=True, capture_output=True)
    if proc.returncode != 0:
        raise FetchError(f"{what} failed (pip exited {proc.returncode}):\n"
                         f"{proc.stderr.strip()[-1200:]}")


def build_retro_wheels(retro_src: Path, drivers_dir: Path) -> Path:
    """Build the `retro` wheel and gather its WINDOWS dependencies.

    The guest installs the package with `pip install --no-index`, so the
    whole dependency closure has to be here: provisioning must not depend on
    PyPI being reachable (the emulator downloads that follow are the one
    deliberate exception, and they are hash-pinned).

    --platform win_amd64 with --python-version is what makes this correct
    from a Linux build host: py7zr's dependencies (pycryptodomex, pyzstd,
    pybcj...) are COMPILED, and resolving them for the build host would
    quietly stage Linux .so wheels that no guest can install.
    """
    if not (retro_src / "pyproject.toml").is_file():
        raise FetchError(
            f"no retro package at {retro_src}: pass --retro-src pointing at "
            "the packages/retro checkout this payload should install")
    wheels = drivers_dir / RETRO_DIRNAME / RETRO_WHEELS_DIRNAME
    wheels.mkdir(parents=True, exist_ok=True)
    # Drop any previously built retro wheel first: two versions side by side
    # would leave "which one does the guest install?" to pip's ordering.
    for stale in wheels.glob("retro-*.whl"):
        stale.unlink()
    _pip(["wheel", "--no-deps", "--wheel-dir", str(wheels), str(retro_src)],
         f"building the retro wheel from {retro_src}")
    built = list(wheels.glob("retro-*.whl"))
    if len(built) != 1:
        raise FetchError(f"expected exactly one retro wheel in {wheels}, "
                         f"got {[w.name for w in built]}")
    _pip(["download", "--only-binary=:all:", "--platform", "win_amd64",
          "--python-version", RETRO_PY_TAG, "--dest", str(wheels),
          str(built[0])],
         f"downloading the retro dependencies for cp{RETRO_PY_TAG} win_amd64")
    return wheels


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the B payload binaries")
    ap.add_argument("--drivers-dir", required=True)
    # Same switch, same default and same reason as build.py --retro: nothing
    # retro-related is fetched unless the operator asked for it.
    ap.add_argument("--retro", action="store_true",
                    help="also fetch what retrogaming needs (7zr.exe, the "
                         "Python installer and the retro wheelhouse); off by "
                         "default, and ~30 MB the payload otherwise avoids")
    ap.add_argument("--retro-src", default=str(RETRO_SRC),
                    help="checkout of the retro package to build the wheel "
                         f"from (default: {RETRO_SRC})")
    args = ap.parse_args(argv)
    drivers = Path(args.drivers_dir)
    try:
        for item in plan_downloads(drivers, retro=args.retro):
            print(f"  {item.name} sha256 {fetch(item, drivers)}")
        extract_virtio(drivers / BUILD_CACHE_DIRNAME / "virtio-win.iso", drivers)
        if args.retro:
            wheels = build_retro_wheels(Path(args.retro_src), drivers)
            names = sorted(w.name for w in wheels.glob("*.whl"))
            print(f"  retro: {len(names)} wheels in {wheels}")
            print("    " + ", ".join(names))
    except FetchError as exc:
        raise SystemExit(str(exc))
    if not args.retro:
        print("\nRetrogaming was not requested (--retro absent): 7zr.exe, the "
              "Python installer and the retro wheels were NOT fetched, and "
              "32-retro.ps1 will say on the guest that the option is off.")
    print("\nNot fetched, and never fetchable: agent/agent.exe must be "
          "extracted from the current Windows VM before it is wiped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
