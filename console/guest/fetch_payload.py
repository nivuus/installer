#!/usr/bin/env python3
"""Build-time acquisition of the payload binaries that are not already local.

Networking is allowed HERE, and - with TWO named exceptions - nowhere else:
the guest provisions offline, so a URL that rots must break the build, never
an install. Nothing in this module is imported by the guest-facing code paths.

The first exception is retrogaming, and only when it is enabled: 32-retro.ps1
runs `retro install` on the guest, which downloads the emulators themselves.
They weigh about a gigabyte, they move on their own schedule, and freezing
them into the image would mean rebuilding it to refresh one - while that
install is idempotent and replayable. So this module still fetches,
offline-first, everything that install NEEDS (the interpreter, 7zr.exe, the
whole wheel closure), and the emulator archives it does not carry are pinned
by sha256 in the package's own manifest: a URL rotting there is a named
failure on the guest, recorded on the persistent volume, never a silent
substitution.

The second is Gaming Services (34-gaming-services.ps1), and it is not a
trade-off but a fact about the artefact: it is a Microsoft Store package, the
Store publishes no offline file, and its whole point is to be CURRENT - a
version frozen into the image is the "Ensure GamingServices is up to date"
error that stops a game launching, months later, on a console nobody wants to
reimage. The same arbitration as retrogaming applies to what CAN be frozen:
winget, the only client measured to reach the Store from a SKU that ships
none, travels offline and pinned (WINGET_VERSION below).

Usage:
    sudo python3 fetch_payload.py --drivers-dir /media/data/nivuus-win-payload
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
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

# App Installer, i.e. winget. IoT Enterprise LTSC ships NO Microsoft Store,
# and Gaming Services only exists as a Store package: winget's `msstore`
# source is the one path measured to reach it from this SKU (2026-08-30, on
# the production guest, build 26100 - `winget install --id 9MWPM2CQNLHN
# --source msstore` returned "Installe correctement" and left
# Microsoft.GamingServices 38.116.6003.0 with both its services Running).
#
# PINNED to one release, unlike Steam and virtio-win which are deliberately
# moving pointers. Two reasons, and neither is taste: the license file's name
# carries a per-release hash (so "latest" would rename the file under the
# build), and winget is the machinery that installs everything else - a
# machinery that changes on its own is the one that fails on a screenless
# guest. Bumping the version means editing WINGET_LICENSE_NAME in the same
# gesture: they are one release, not two knobs.
WINGET_VERSION = "v1.29.290"
WINGET_BUNDLE_NAME = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
WINGET_LICENSE_NAME = "e53e159d00e04f729cc2180cffd1c02e_License1.xml"
WINGET_DEPS_NAME = "DesktopAppInstaller_Dependencies.zip"
WINGET_BASE_URL = ("https://github.com/microsoft/winget-cli/releases/download/"
                   f"{WINGET_VERSION}/")
WINGET_DIRNAME = "winget"
WINGET_DEPS_DIRNAME = "deps"
# Le temoin d epinglage, DANS drivers/winget/ a cote de ce qu il date.
# Sans lui, relever WINGET_VERSION ne relevait rien du tout : fetch() saute le
# telechargement des que le fichier existe, et les trois artefacts atterrissent
# sur des chemins qui ne portent PAS la version (nom fixe du bundle, licence
# renommee License1.xml, zip de dependances au nom fixe). Sur un hote de
# construction dont drivers/ est deja peuple, un relevement ne changeait donc
# que des URL : l ANCIEN winget repartait dans l image, en silence, et le
# manifeste TOFU ne pouvait rien voir puisque le chemin et le condensat etaient
# les memes. Meme forme de defaut que les installateurs Python du retrogaming,
# et meme reponse (prune_stale_retro).
WINGET_STAMP_NAME = ".winget-version"
# The dependency zip carries every architecture (93 MB); the guest is x64 and
# needs three of them, 31.6 MB in total (measured 2026-08-30). Mining the zip
# for x64 mirrors extract_virtio: the vendor publishes no per-architecture
# artefact, and shipping the whole zip would send arm64 and x86 frameworks to
# a machine that can never load them.
WINGET_DEPS_MEMBER_DIR = "x64"
# Without these two the bundle installs but winget.exe never appears, and it
# does so SILENTLY - the failure mode Microsoft's own IoT documentation warns
# about. Named here so a vendor who reorganises the zip breaks the build
# rather than the guest.
WINGET_DEPS_REQUIRED = ("Microsoft.VCLibs.140.00.UWPDesktop",
                        "Microsoft.WindowsAppRuntime.")

# agent.exe is the ONE offline payload binary that is never downloaded: it is
# a compiled artefact of the sibling repository nivuus/desk, vendored
# straight into this package (see payload/agent/README.md and
# console/README.md for the reserve behind that decision - no source
# checkout of nivuus/desk exists on a build machine, so it cannot be built
# here, and it used to have to be extracted from the one production Windows
# VM that happened to be running it, which left a fresh machine with nothing
# to extract at all).
PACKAGED_AGENT_EXE = Path(__file__).resolve().parent / "payload" / "agent" / "agent.exe"

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
# below and the interpreter installed on the guest must agree. The wheel tag
# is DERIVED from this string at each use (py_tag) rather than written twice -
# the two drifting apart would produce wheels no guest Python can install, and
# pip would only say so at provisioning time, offline, on a screenless box.
RETRO_PYTHON_VERSION = "3.12.10"
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


def py_tag(version: str = RETRO_PYTHON_VERSION) -> str:
    """The cpXY tag matching an x.y.z Python version: 3.12.10 -> "312".

    A function, called at each use, rather than a constant computed once at
    import: a constant frozen to today's literal ("312") happens to equal the
    derivation as long as the pinned version does not move, which is exactly
    the case where nobody notices it stopped deriving anything.
    """
    return "".join(version.split(".")[:2])


def prune_stale_retro(drivers_dir: Path) -> list[str]:
    """Drop Python installers pinned to a version this build no longer uses.

    drivers/ is a working tree that persists between builds, so a version
    bump would otherwise leave the OLD installer beside the new one - and the
    guest, taking the first by name, would install the OLD interpreter and
    then the NEW version's wheels. That breaks on the guest, loudly but very
    late. 32-retro.ps1 refuses the ambiguity; this removes it at the source.
    """
    retro_dir = drivers_dir / RETRO_DIRNAME
    wanted = f"python-{RETRO_PYTHON_VERSION}-amd64.exe"
    removed = []
    for stale in sorted(retro_dir.glob("python-*-amd64.exe")):
        if stale.name != wanted:
            stale.unlink()
            removed.append(stale.name)
    return removed


def resolve_retro(cli_value: bool | None, marker_path: str | None = None) -> bool:
    """Decide whether this fetch includes retrogaming.

    Same decision, same source and same precedence as build.py: an explicit
    --retro/--no-retro wins, otherwise the wizard's marker on this host
    decides. Fetching by a different rule than the build renders would be the
    one divergence that only shows up an hour into provisioning - a payload
    whose config/retro.psd1 says Enabled = $true with no drivers/retro/ in it.
    """
    if cli_value is not None:
        return cli_value
    # Local import on purpose: build.py pulls jinja2, and this fetcher must
    # stay runnable with the standard library alone. Only the "no flag given"
    # path needs it, and if it cannot be had, the remedy is named.
    try:
        import build
    except ImportError as exc:
        raise FetchError(
            f"cannot read the retro marker recorded by the install wizard: "
            f"importing build.py failed ({exc}). Pass --retro or --no-retro "
            "explicitly to say what this payload must carry.") from exc
    return build.read_retro_marker(marker_path or build.DEFAULT_RETRO_MARKER)


def plan_downloads(drivers_dir: Path, retro: bool = False) -> list[Download]:
    """Pure: what would be fetched, and where each file would land.

    The retro artefacts are appended ONLY when the operator asked for
    retrogaming: an installation without it has no reason to grow by an
    interpreter, an extractor and a wheelhouse it will never open.
    """
    winget_dir = drivers_dir / WINGET_DIRNAME
    items = [
        Download("steam", STEAM_URL, drivers_dir / "steam" / "SteamSetup.exe"),
        Download("winfsp", WINFSP_URL,
                 drivers_dir / "winfsp" / "winfsp-2.0.23075.msi"),
        Download("virtio-iso", VIRTIO_ISO_URL,
                 drivers_dir / BUILD_CACHE_DIRNAME / "virtio-win.iso"),
        # winget is NOT behind a flag: the owner asked for Gaming Services by
        # default, and an appliance that silently lacks the only client able
        # to install it would fail at the one moment nobody is watching - the
        # first time a game asks for it.
        Download("winget-bundle", WINGET_BASE_URL + WINGET_BUNDLE_NAME,
                 winget_dir / WINGET_BUNDLE_NAME),
        # Renamed on landing: the source name carries a per-release hash, and
        # 33-winget.ps1 must point at ONE path that does not move with the
        # pin. The hash stays in WINGET_LICENSE_NAME, where a bump reads it.
        Download("winget-license", WINGET_BASE_URL + WINGET_LICENSE_NAME,
                 winget_dir / "License1.xml"),
        Download("winget-deps", WINGET_BASE_URL + WINGET_DEPS_NAME,
                 drivers_dir / BUILD_CACHE_DIRNAME / WINGET_DEPS_NAME),
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_packaged_agent(drivers_dir: Path) -> str:
    """Copy the package's own agent.exe into the offline payload tree.

    Unlike fetch(), there is no network and no manifest here: the source of
    truth is the file tracked in this repository's git history
    (PACKAGED_AGENT_EXE), not a URL that can rot. What still matters is
    verifying the COPY, not just making it - an 11 MB file that gets
    silently truncated by a full disk or an interrupted process would
    otherwise surface only deep inside provisioning, on a screenless guest,
    as an agent.exe that will not run. Comparing sha256 at both ends catches
    that immediately, at build time.
    """
    if not PACKAGED_AGENT_EXE.is_file():
        raise FetchError(
            f"the package does not carry agent.exe: {PACKAGED_AGENT_EXE} is "
            "missing. See console/guest/payload/agent/README.md - the "
            "binary must be committed there before this console can be "
            "built at all."
        )
    dest = drivers_dir / "agent" / "agent.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGED_AGENT_EXE, dest)
    src_digest = _sha256(PACKAGED_AGENT_EXE)
    dest_digest = _sha256(dest)
    if src_digest != dest_digest:
        raise FetchError(
            f"copying agent.exe from the package to {dest} produced a "
            f"different sha256 ({dest_digest}) than the package's own copy "
            f"({src_digest}) - the copy is corrupt, most likely truncated. "
            f"Delete {dest} and re-run."
        )
    return dest_digest


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


def extract_winget_deps(archive: Path, drivers_dir: Path) -> list[str]:
    """Pull the x64 framework packages winget needs out of its dependency zip.

    Returns the names extracted. Refuses (raises FetchError) when the zip
    carries no x64 directory, or when one of WINGET_DEPS_REQUIRED is not
    among what came out: an App Installer bundle whose frameworks are missing
    installs WITHOUT ERROR and then has no winget.exe at all - Microsoft's own
    IoT guidance calls that failure out as silent, and a silent failure at
    build time becomes an unexplainable one an hour into provisioning, on a
    guest with no screen.

    zipfile, not `7z`: unlike the virtio ISO this is a plain archive, and the
    standard library keeps this module runnable without the extra binary.
    """
    dest = drivers_dir / WINGET_DIRNAME / WINGET_DEPS_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if WINGET_DEPS_MEMBER_DIR not in parts:
                continue
            if not info.filename.lower().endswith(".appx"):
                continue
            name = Path(info.filename).name
            # Flattened like extract_virtio, and for the same reason: the
            # guest step points at one directory rather than guessing a
            # vendor layout that can be reorganised between releases.
            with zf.open(info) as src, open(dest / name, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(name)
    if not extracted:
        raise FetchError(
            f"{archive.name} carries no {WINGET_DEPS_MEMBER_DIR}/*.appx: the "
            f"winget {WINGET_VERSION} dependency zip was reorganised, and "
            "shipping the bundle without its frameworks would leave the guest "
            "with no winget.exe and no error to explain it")
    for needed in WINGET_DEPS_REQUIRED:
        if not any(name.startswith(needed) for name in extracted):
            raise FetchError(
                f"no {needed}* among the x64 frameworks extracted from "
                f"{archive.name} (got {', '.join(sorted(extracted))}): winget "
                "would install and then not exist. Check what winget "
                f"{WINGET_VERSION} now depends on before bumping the pin.")
    print(f"extracted {len(extracted)} x64 framework(s) from {archive.name}: "
          f"{', '.join(sorted(extracted))}")
    return extracted


def prune_stale_winget(drivers_dir: Path) -> list[str]:
    """Drop a winget payload staged for a different WINGET_VERSION.

    Returns what was removed. Nothing staged, or a witness that already
    carries this pin, means nothing to do - so this is safe to call on every
    build and on a drivers/ that has never seen winget.

    The dependency zip in the build cache goes too: extract_winget_deps mines
    it into deps/, so leaving yesterday's zip beside today's bundle is the
    same trap one directory up.
    """
    winget_dir = drivers_dir / WINGET_DIRNAME
    stamp = winget_dir / WINGET_STAMP_NAME
    try:
        current = stamp.read_text().strip()
    except OSError:
        current = ""
    if current == WINGET_VERSION:
        return []
    removed: list[str] = []
    if winget_dir.is_dir():
        removed.append(f"{WINGET_DIRNAME}/")
        shutil.rmtree(winget_dir)
    zip_cache = drivers_dir / BUILD_CACHE_DIRNAME / WINGET_DEPS_NAME
    if zip_cache.exists():
        zip_cache.unlink()
        removed.append(f"{BUILD_CACHE_DIRNAME}/{WINGET_DEPS_NAME}")
    return removed


def stamp_winget(drivers_dir: Path) -> None:
    """Record the pin the staged winget payload was built from.

    Written only AFTER the frameworks are extracted, so an interrupted fetch
    leaves no witness and the next build prunes and starts over rather than
    shipping a half-staged winget.
    """
    winget_dir = drivers_dir / WINGET_DIRNAME
    winget_dir.mkdir(parents=True, exist_ok=True)
    (winget_dir / WINGET_STAMP_NAME).write_text(f"{WINGET_VERSION}\n")


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
    # --platform/--python-version are load-bearing, not decoration: without
    # them pip resolves for THIS host, and a Linux build machine would stage
    # manylinux .so wheels that no guest can install.
    tag = py_tag(RETRO_PYTHON_VERSION)
    _pip(["download", "--only-binary=:all:", "--platform", "win_amd64",
          "--python-version", tag, "--dest", str(wheels), str(built[0])],
         f"downloading the retro dependencies for cp{tag} win_amd64")
    return wheels


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the B payload binaries")
    ap.add_argument("--drivers-dir", required=True)
    # Same switch, same default and same source as build.py --retro: None,
    # not False, so that omitting it means "do what the wizard recorded on
    # this host" and never "off" - fetching off a box whose owner DID check
    # the option would fail the build later, at build.py, with the payload
    # already claiming Enabled = $true.
    ap.add_argument(
        "--retro", action=argparse.BooleanOptionalAction, default=None,
        help="force fetching what retrogaming needs (7zr.exe, the Python "
             "installer and the retro wheelhouse, ~30 MB) on or off; without "
             "this flag, defaults to whatever the install wizard recorded on "
             "this host, exactly like build.py")
    ap.add_argument("--retro-src", default=str(RETRO_SRC),
                    help="checkout of the retro package to build the wheel "
                         f"from (default: {RETRO_SRC})")
    args = ap.parse_args(argv)
    drivers = Path(args.drivers_dir)
    try:
        retro = resolve_retro(args.retro)
        print("retrogaming: " + ("enabled" if retro else "disabled") + (
            " (--retro/--no-retro given explicitly)" if args.retro is not None
            else " (from the install wizard's marker, no --retro/--no-retro "
                 "given)"))
        print(f"  agent sha256 {install_packaged_agent(drivers)} "
              "(bundled in the package, not downloaded)")
        # AVANT les telechargements : fetch() garde ce qui existe deja, donc
        # un epinglage releve ne change rien tant que l ancien est encore la.
        for gone in prune_stale_winget(drivers):
            print(f"  winget: removed the stale {gone}")
        for item in plan_downloads(drivers, retro=retro):
            print(f"  {item.name} sha256 {fetch(item, drivers)}")
        extract_virtio(drivers / BUILD_CACHE_DIRNAME / "virtio-win.iso", drivers)
        extract_winget_deps(
            drivers / BUILD_CACHE_DIRNAME / WINGET_DEPS_NAME, drivers)
        # Le temoin en dernier : une extraction interrompue ne doit pas laisser
        # croire que cet epinglage est completement pose.
        stamp_winget(drivers)
        if retro:
            for gone in prune_stale_retro(drivers):
                print(f"  retro: removed the stale {gone}")
            wheels = build_retro_wheels(Path(args.retro_src), drivers)
            names = sorted(w.name for w in wheels.glob("*.whl"))
            print(f"  retro: {len(names)} wheels in {wheels}")
            print("    " + ", ".join(names))
    except FetchError as exc:
        raise SystemExit(str(exc))
    if not retro:
        print("\nRetrogaming is off for this payload: 7zr.exe, the Python "
              "installer and the retro wheels were NOT fetched, and "
              "32-retro.ps1 will say on the guest that the option is off.")
    print(f"\nagent/agent.exe came from the package itself "
          f"({PACKAGED_AGENT_EXE}), not from a live VM: see "
          "console/guest/payload/agent/README.md for the reserve that "
          "decision carries.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
