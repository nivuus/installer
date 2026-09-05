#!/usr/bin/env python3
"""Tests for the build-time offline-payload fetcher (fetch_payload.py).

Networking is never exercised here: every test either stays on the pure
`plan_downloads()` path or pre-populates the destination file so `fetch()`
takes its "already there" branch.

Run: python3 console/tests/test_windows_guest_fetch_payload.py
"""
import hashlib
import pathlib
import sys
import tempfile
import zipfile

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


# --- Tache 4 (chaine reelle) : agent.exe voyage desormais DANS le paquet,
# et fetch_payload.py ne pretend plus qu il est "jamais recuperable" - sur
# une machine neuve, sans VM Windows a extraire de quoi que ce soit, cette
# ancienne phrase rendait l etape 40 du provisionnement impossible a
# satisfaire des le depart.
check("le paquet embarque bien console/guest/payload/agent/agent.exe",
      fetch_payload.PACKAGED_AGENT_EXE.is_file(), True)
# Une taille plancher plutot que la valeur exacte du jour : ce fichier sera
# remplace a chaque nouvelle version de l agent (voir la reserve dans
# payload/agent/README.md), la valeur exacte n a donc pas vocation a rester
# fixe - seule une troncature grossiere doit faire echouer ce controle.
if fetch_payload.PACKAGED_AGENT_EXE.is_file():
    check("agent.exe embarque n est pas tronque (>5 Mo)",
          fetch_payload.PACKAGED_AGENT_EXE.stat().st_size > 5_000_000, True)

# install_packaged_agent() doit copier le binaire du paquet vers drivers/ ET
# verifier la somme de controle a destination - une copie de 11 Mo tronquee
# en silence ne se decouvrirait sinon qu a l etape 40, sur un invite sans
# ecran.
with tempfile.TemporaryDirectory() as tmp:
    drivers = pathlib.Path(tmp) / "drivers"
    digest = fetch_payload.install_packaged_agent(drivers)
    dest = drivers / "agent" / "agent.exe"
    check("install_packaged_agent copie bien le binaire dans drivers/agent/",
          dest.is_file(), True)
    check("le sha256 retourne correspond au fichier copie a destination",
          hashlib.sha256(dest.read_bytes()).hexdigest(), digest)
    check("le sha256 correspond au binaire du paquet",
          digest,
          hashlib.sha256(fetch_payload.PACKAGED_AGENT_EXE.read_bytes()).hexdigest())

# Un paquet SANS agent.exe (le binaire retire ou jamais commite) doit faire
# echouer le controle en le NOMMANT - c est la preuve, dans le sens de la
# tache 4, que ce test detecte vraiment sa disparition.
_real_agent = fetch_payload.PACKAGED_AGENT_EXE
fetch_payload.PACKAGED_AGENT_EXE = pathlib.Path(tempfile.gettempdir()) / "agent-absent-du-paquet.exe"
try:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fetch_payload.install_packaged_agent(pathlib.Path(tmp) / "drivers")
            failures.append(
                "install_packaged_agent: a accepte un paquet sans agent.exe")
        except fetch_payload.FetchError as e:
            if "agent.exe" not in str(e):
                failures.append(
                    f"install_packaged_agent ne nomme pas agent.exe manquant: {e}")
finally:
    fetch_payload.PACKAGED_AGENT_EXE = _real_agent

# fetch_payload.py ne doit plus annoncer agent.exe comme "jamais
# recuperable" - la phrase devient fausse des que le paquet le porte - et
# doit dire d ou il vient reellement a la place.
_src = pathlib.Path(fetch_payload.__file__).read_text()
check("fetch_payload n annonce plus agent.exe comme jamais recuperable",
      "never fetchable" in _src, False)
check("... et dit desormais d ou il vient vraiment",
      "payload/agent/README.md" in _src and "package itself" in _src, True)


# --- winget : la part de « Services de jeu » que la construction PEUT figer.
#
# Le paquet du Store lui-meme se telecharge sur l invite (etape 34) parce
# qu il n existe nulle part sous forme de fichier et que le figer serait de
# toute facon l erreur - les jeux refusent une copie perimee. Son client, lui,
# voyage hors ligne et epingle : c est la contrepartie, et ces controles
# gardent qu elle est bien payee.
_wg = {d.name: d for d in fetch_payload.plan_downloads(pathlib.Path("/tmp/x"))}
for _name in ("winget-bundle", "winget-license", "winget-deps"):
    check(f"{_name} est telecharge sans qu aucune option soit cochee",
          _name in _wg, True)
check("le bundle atterrit dans drivers/winget/",
      _wg["winget-bundle"].dest.parent, pathlib.Path("/tmp/x/winget"))
# Renomme en atterrissant : le nom publie porte un condensat propre a la
# livraison, et 33-winget.ps1 doit pointer un chemin qui ne bouge pas avec
# l epinglage.
check("la licence atterrit sous un nom stable",
      _wg["winget-license"].dest, pathlib.Path("/tmp/x/winget/License1.xml"))
check("... alors que la source, elle, porte le condensat de la livraison",
      _wg["winget-license"].url.endswith(fetch_payload.WINGET_LICENSE_NAME), True)
# Le zip de dependances est un intermediaire de construction, comme l ISO
# virtio : mine puis laisse dans le cache, jamais expedie a l invite.
check("le zip de dependances reste dans le cache de construction",
      fetch_payload.BUILD_CACHE_DIRNAME in _wg["winget-deps"].dest.parts, True)
# Les trois URL viennent de la MEME livraison. Un bundle d une version et une
# licence d une autre s installent tous les deux et ne vont pas ensemble.
for _name in ("winget-bundle", "winget-license", "winget-deps"):
    check(f"{_name} vient de la livraison epinglee",
          f"/{fetch_payload.WINGET_VERSION}/" in _wg[_name].url, True)
check("les trois viennent bien de winget-cli, pas d un miroir",
      all(_wg[n].url.startswith("https://github.com/microsoft/winget-cli/releases/download/")
          for n in ("winget-bundle", "winget-license", "winget-deps")), True)

# Le dossier que les deux modules nomment : payload.py y cherche ce que
# celui-ci y depose (meme garde que pour le retrogaming plus haut).
check("payload.py cherche winget la ou fetch_payload le depose",
      any(sub.split("/")[0] == fetch_payload.WINGET_DIRNAME
          for sub, _, _ in payload.REQUIRED_BINARIES), True)

# extract_winget_deps : ne prend que x64, et REFUSE de rendre la main sur un
# zip qui ne porte pas les frameworks nommes. Un bundle pose sans eux
# s installe sans erreur et ne depose aucun winget.exe - le silence que ce
# refus transforme en echec de construction.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    archive = root / "deps.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x64/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx", "x")
        zf.writestr("x64/Microsoft.WindowsAppRuntime.1.8_8000.616.304.0_x64.appx", "x")
        zf.writestr("arm64/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_arm64.appx", "x")
        zf.writestr("x86/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x86.appx", "x")
        zf.writestr("x64/readme.txt", "x")
    got = sorted(fetch_payload.extract_winget_deps(archive, root))
    check("seuls les frameworks x64 sont extraits", got,
          ["Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx",
           "Microsoft.WindowsAppRuntime.1.8_8000.616.304.0_x64.appx"])
    check("ils sont aplatis dans drivers/winget/deps/",
          sorted(q.name for q in (root / "winget" / "deps").iterdir()), got)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    archive = root / "deps.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x64/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx", "x")
    try:
        fetch_payload.extract_winget_deps(archive, root)
        failures.append("un zip sans Windows App Runtime a ete accepte")
    except fetch_payload.FetchError as exc:
        check("un zip sans le runtime nomme est refuse a la construction",
              "WindowsAppRuntime" in str(exc), True)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    archive = root / "deps.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("arm64/Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_arm64.appx", "x")
    try:
        fetch_payload.extract_winget_deps(archive, root)
        failures.append("un zip sans x64 a ete accepte")
    except fetch_payload.FetchError as exc:
        check("un zip sans x64 est refuse plutot que vide en silence",
              "x64" in str(exc), True)


# prune_stale_winget : relever l epinglage doit VRAIMENT changer ce qui part.
#
# Le defaut sans elle : fetch() saute le telechargement des que le fichier
# existe, et les trois artefacts winget atterrissent sur des chemins qui NE
# PORTENT PAS la version (le bundle a un nom fixe, la licence est renommee
# License1.xml, le zip de dependances est un nom fixe du cache). Sur un hote de
# construction dont le drivers/ est deja peuple, relever WINGET_VERSION ne
# changeait donc que des URL : l ANCIEN winget repartait dans l image, en
# silence, et le manifeste TOFU ne pouvait rien voir - meme chemin, meme
# condensat. C est la meme forme de defaut que prune_stale_retro, et la meme
# reponse : un temoin de version a cote des fichiers, et on jette tout des que
# les deux ne concordent plus.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    wg = root / fetch_payload.WINGET_DIRNAME
    (wg / fetch_payload.WINGET_DEPS_DIRNAME).mkdir(parents=True)
    (wg / "vieux.msixbundle").write_text("ancien bundle")
    (wg / "License1.xml").write_text("ancienne licence")
    (wg / fetch_payload.WINGET_DEPS_DIRNAME / "vieux.appx").write_text("ancien framework")
    cache = root / fetch_payload.BUILD_CACHE_DIRNAME
    cache.mkdir()
    (cache / fetch_payload.WINGET_DEPS_NAME).write_text("ancien zip")
    (wg / fetch_payload.WINGET_STAMP_NAME).write_text("v1.2.3\n")

    gone = fetch_payload.prune_stale_winget(root)
    check("un epinglage different jette le winget deja pose", bool(gone), True)
    check("... y compris le zip de dependances du cache",
          (cache / fetch_payload.WINGET_DEPS_NAME).exists(), False)
    check("... et les frameworks deja extraits",
          (wg / fetch_payload.WINGET_DEPS_DIRNAME / "vieux.appx").exists(), False)

    # Rejoue : le temoin porte maintenant la version voulue, plus rien ne part.
    (wg / fetch_payload.WINGET_DEPS_DIRNAME).mkdir(parents=True, exist_ok=True)
    (wg / "a-garder.msixbundle").write_text("bundle a jour")
    fetch_payload.stamp_winget(root)
    check("le temoin porte l epinglage courant",
          (wg / fetch_payload.WINGET_STAMP_NAME).read_text().strip(),
          fetch_payload.WINGET_VERSION)
    check("un epinglage inchange ne jette rien",
          fetch_payload.prune_stale_winget(root), [])
    check("... et laisse le bundle en place",
          (wg / "a-garder.msixbundle").exists(), True)

# Un drivers/ vierge n est pas une erreur : il n y a rien a jeter.
with tempfile.TemporaryDirectory() as tmp:
    check("un drivers/ vierge ne fait rien jeter",
          fetch_payload.prune_stale_winget(pathlib.Path(tmp)), [])


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all fetch_payload tests passed")
