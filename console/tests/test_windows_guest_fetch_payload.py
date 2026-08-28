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
sys.path.insert(0, str(REPO / "console" / "guest"))

import fetch_payload  # noqa: E402
import payload  # noqa: E402

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

# L en-tete disait « networking is allowed HERE and nowhere else [...] une URL
# qui pourrit doit casser la construction, jamais une installation », ce qui
# n est plus vrai depuis l etape 32. Meme garde bon marche que du cote de
# payload.py : restaurer le texte mensonger fait tomber ce controle.
_doc = fetch_payload.__doc__
check("l en-tete de fetch_payload.py ne dit plus « nowhere else » sans reserve",
      "allowed HERE and nowhere else" in _doc, False)
check("... et nomme l exception, son objet et sa contrepartie",
      "exception" in _doc.lower() and "retro install" in _doc
      and "sha256" in _doc, True)

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
# Comparer le tag a sa propre re-derivation depuis la MEME version courante
# ne prouve rien : « 312 » ecrit a la main y satisfait tant que la version
# epinglee ne bouge pas, c est-a-dire exactement dans le cas ou la derivation
# a cesse sans que personne ne le voie. Le controle porte donc sur une AUTRE
# version que celle du jour.
check("le tag des roues derive vraiment de la version passee",
      fetch_payload.py_tag("3.13.2"), "313")
check("le tag de la version epinglee reste celui attendu",
      fetch_payload.py_tag(fetch_payload.RETRO_PYTHON_VERSION), "312")
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


# --- Les arguments qui rendent le magasin de roues INSTALLABLE sur l invite.
# Sans --platform/--python-version, pip resout pour l hote de construction :
# un hote Linux poserait des roues manylinux qu aucun invite ne peut
# installer. Aucun reseau ici - _pip est remplace, et la roue « construite »
# est deposee a la main pour que la suite de la fonction se deroule.
_pip_calls = []
_real_pip = fetch_payload._pip
_real_version = fetch_payload.RETRO_PYTHON_VERSION
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "retro-src"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname = 'retro'\n")
    drivers = root / "drivers"

    def _fake_pip(args, what):
        _pip_calls.append(list(args))
        if args[0] == "wheel":
            dest = pathlib.Path(args[args.index("--wheel-dir") + 1])
            (dest / "retro-9.9.9-py3-none-any.whl").write_bytes(b"PK\x03\x04")

    fetch_payload._pip = _fake_pip
    # Une version differente de celle du jour : un tag ecrit en dur ne suivrait
    # pas, et c est precisement la mutation qui survivait au controle d avant.
    fetch_payload.RETRO_PYTHON_VERSION = "3.13.2"
    try:
        fetch_payload.build_retro_wheels(src, drivers)
    finally:
        fetch_payload._pip = _real_pip
        fetch_payload.RETRO_PYTHON_VERSION = _real_version

_download = [c for c in _pip_calls if c and c[0] == "download"]
check("les dependances sont bien telechargees", len(_download), 1)
_args = _download[0] if _download else []
check("les roues sont resolues pour Windows, pas pour l hote de construction",
      "--platform" in _args and _args[_args.index("--platform") + 1], "win_amd64")
check("... et pour la version de Python que l invite installera",
      "--python-version" in _args and _args[_args.index("--python-version") + 1],
      "313")
check("aucune roue compilee pour une autre plateforme n est acceptee",
      "--only-binary=:all:" in _args, True)

# Un relevement de version laisserait l ANCIEN installateur a cote du nouveau
# dans un dossier de pilotes qui persiste entre deux constructions ; l invite
# prend le premier par ordre alphabetique - donc l ancien - avec les roues de
# la nouvelle version. L echec arrive sur l invite, bruyamment mais tard.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp)
    retro_dir = drivers / fetch_payload.RETRO_DIRNAME
    retro_dir.mkdir()
    pinned = f"python-{fetch_payload.RETRO_PYTHON_VERSION}-amd64.exe"
    (retro_dir / pinned).write_bytes(b"MZ")
    (retro_dir / "python-3.11.9-amd64.exe").write_bytes(b"MZ")
    (retro_dir / "7zr.exe").write_bytes(b"MZ")
    removed = fetch_payload.prune_stale_retro(drivers)
    check("l installateur perime est supprime", removed, ["python-3.11.9-amd64.exe"])
    check("l installateur epingle reste", (retro_dir / pinned).is_file(), True)
    check("le reste de drivers/retro n est pas touche",
          (retro_dir / "7zr.exe").is_file(), True)
    check("rejouer la suppression ne trouve plus rien",
          fetch_payload.prune_stale_retro(drivers), [])

# La recuperation suit le MEME marqueur que la construction. Un proprietaire
# qui a coche la case verrait sinon build.py exiger drivers/retro/ que ce
# programme-ci n aurait jamais recupere.
with tempfile.TemporaryDirectory() as tmp:
    marker = pathlib.Path(tmp) / "retro.json"
    marker.write_text('{"enabled": true}')
    check("sans drapeau, le marqueur de l assistant decide",
          fetch_payload.resolve_retro(None, str(marker)), True)
    check("--no-retro l emporte sur un marqueur qui dit oui",
          fetch_payload.resolve_retro(False, str(marker)), False)
    marker.write_text('{"enabled": false}')
    check("un marqueur qui dit non est suivi aussi",
          fetch_payload.resolve_retro(None, str(marker)), False)
    check("--retro l emporte sur un marqueur qui dit non",
          fetch_payload.resolve_retro(True, str(marker)), True)
    marker.unlink()
    check("un marqueur absent vaut « non »",
          fetch_payload.resolve_retro(None, str(marker)), False)

# Les deux modules nomment le meme dossier sous drivers/ : payload.py y
# cherche les artefacts que celui-ci y depose.
check("les deux modules nomment le meme dossier",
      fetch_payload.RETRO_DIRNAME, payload.RETRO_DIRNAME)


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all fetch_payload tests passed")
