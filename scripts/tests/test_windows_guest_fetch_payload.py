#!/usr/bin/env python3
"""Tests for the build-time offline-payload fetcher (fetch_payload.py).

Networking is never exercised here: every test either stays on the pure
`plan_downloads()` path or pre-populates the destination file so `fetch()`
takes its "already there" branch.

Run: python3 scripts/tests/test_windows_guest_fetch_payload.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import fetch_payload  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


plans = fetch_payload.plan_downloads(pathlib.Path("/tmp/x"))
check("every download has a url and a destination",
      all(d.url.startswith("https://") and d.dest for d in plans), True)
check("no download lands outside the drivers dir",
      all(str(d.dest).startswith("/tmp/x") for d in plans), True)
names = [d.name for d in plans]
check("downloads are uniquely named", len(names), len(set(names)))
# The source virtio-win.iso is mined for two drivers and then dead weight -
# it must land under the dot-directory build cache, never in the payload
# tree proper, or _walk() would ship it to the guest.
virtio_iso = next(d for d in plans if d.name == "virtio-iso")
check("virtio-win.iso lands under the build cache",
      fetch_payload.BUILD_CACHE_DIRNAME in virtio_iso.dest.parts, True)

# fetch() must fail loudly (never silently ship a changed artefact) when a
# path already recorded in the manifest comes back with a different digest.
# No network: the "download" is simulated by the file already being present.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp) / "drivers"
    dest = drivers / "steam" / "SteamSetup.exe"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"a newer build of the installer")
    manifest_dir = drivers / fetch_payload.BUILD_CACHE_DIRNAME
    manifest_dir.mkdir(parents=True)
    (manifest_dir / fetch_payload.MANIFEST_NAME).write_text(
        "steam/SteamSetup.exe\tdeadbeef00000000000000000000000000000000000000000000000000\t2026-01-01\n"
    )
    item = fetch_payload.Download("steam", "https://example.invalid/x", dest)
    try:
        fetch_payload.fetch(item, drivers)
        failures.append("fetch: accepted a digest mismatch against the manifest")
    except fetch_payload.FetchError as e:
        if "changed" not in str(e) or "manifest" not in str(e).lower():
            failures.append(f"fetch manifest-mismatch error is unclear: {e}")

# fetch() must record a first-seen digest rather than reject it.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp) / "drivers"
    dest = drivers / "winfsp" / "winfsp-2.0.msi"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"msi bytes")
    item = fetch_payload.Download("winfsp", "https://example.invalid/x", dest)
    fetch_payload.fetch(item, drivers)
    manifest = fetch_payload.load_manifest(drivers)
    check("first-seen digest is recorded",
          "winfsp/winfsp-2.0.msi" in manifest, True)

# extract_virtio's flatten step must refuse to silently overwrite two files
# that share a basename under different subdirectories. Fake tree, no 7z.
with tempfile.TemporaryDirectory() as tmp:
    dest = pathlib.Path(tmp) / "netkvm"
    nested = dest / "NetKVM"
    (nested / "w11" / "amd64").mkdir(parents=True)
    (nested / "w10" / "amd64").mkdir(parents=True)
    (nested / "w11" / "amd64" / "netkvm.inf").write_text("w11 driver")
    (nested / "w10" / "amd64" / "netkvm.inf").write_text("w10 driver")
    try:
        fetch_payload.flatten_extracted(nested, dest)
        failures.append("flatten_extracted: silently overwrote colliding basenames")
    except fetch_payload.FetchError as e:
        if "collide" not in str(e):
            failures.append(f"flatten_extracted collision error is unclear: {e}")

# --- Tache 4 (sous-projet C2) : les artefacts du retrogaming ne sont
# telecharges que si l option est cochee. Une installation sans retrogaming
# n a pas a payer un interpreteur, un extracteur et un magasin de roues.
_plain = [d.name for d in fetch_payload.plan_downloads(pathlib.Path("/tmp/x"))]
for name in ["7zr", "retro-python"]:
    check(f"sans --retro, {name} n est pas telecharge", name in _plain, False)

_retro = fetch_payload.plan_downloads(pathlib.Path("/tmp/x"), retro=True)
_by_name = {d.name: d for d in _retro}
check("sans --retro, rien d autre ne change",
      [d.name for d in _retro][:len(_plain)], _plain)
check("avec --retro, 7zr.exe est telecharge", "7zr" in _by_name, True)
check("avec --retro, l installateur Python est telecharge",
      "retro-python" in _by_name, True)
check("les telechargements restent uniquement nommes",
      len(_retro), len({d.name for d in _retro}))
# Ils atterrissent dans drivers/retro/, la ou payload.RETRO_BINARIES les
# cherche et d ou 32-retro.ps1 les lit sur l invite.
check("7zr.exe atterrit dans drivers/retro/",
      _by_name["7zr"].dest, pathlib.Path("/tmp/x/retro/7zr.exe"))
check("l installateur Python atterrit dans drivers/retro/",
      _by_name["retro-python"].dest.parent, pathlib.Path("/tmp/x/retro"))
# 7zr.exe vient de l editeur de 7-Zip, pas d un miroir : c est un binaire
# execute sur la console.
check("7zr.exe vient bien de 7-zip.org",
      _by_name["7zr"].url, "https://www.7-zip.org/a/7zr.exe")

# La version de Python et le tag des roues ne peuvent pas diverger : les
# dependances de py7zr sont COMPILEES, donc taguees pour une seule version
# mineure. Un interpreteur 3.13 sur l invite et des roues cp312 ne se
# rencontreraient qu au moment du pip install, hors ligne, sur une machine
# sans ecran.
check("le tag des roues derive de la version de l installateur Python",
      fetch_payload.RETRO_PY_TAG,
      "".join(fetch_payload.RETRO_PYTHON_VERSION.split(".")[:2]))
check("l URL de l installateur porte cette meme version",
      f"python-{fetch_payload.RETRO_PYTHON_VERSION}-amd64.exe"
      in fetch_payload.RETRO_PYTHON_URL, True)
check("le nom du fichier depose porte cette meme version",
      _by_name["retro-python"].dest.name,
      f"python-{fetch_payload.RETRO_PYTHON_VERSION}-amd64.exe")

# Les roues sont construites depuis le depot voisin packages/retro, jamais
# depuis PyPI : c est ce depot-ci qui decide quelle version part sur l invite.
check("la source par defaut du paquet est le depot voisin packages/retro",
      fetch_payload.RETRO_SRC.name, "retro")
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    try:
        fetch_payload.build_retro_wheels(root / "pas-un-paquet", root / "drivers")
        failures.append("build_retro_wheels: accepted a source with no pyproject.toml")
    except fetch_payload.FetchError as e:
        if "retro-src" not in str(e):
            failures.append(f"build_retro_wheels error doesn't point at --retro-src: {e}")


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all fetch_payload tests passed")
