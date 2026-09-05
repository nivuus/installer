#!/usr/bin/env python3
"""Tests for the offline payload staged onto the unattend ISO (payload.py).

Run: python3 console/tests/test_windows_guest_payload.py
"""
import pathlib
import shutil
import sys
import inspect
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import payload  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def make_tree(root: pathlib.Path) -> "payload.PayloadSources":
    """Build a complete offline payload tree (all REQUIRED_BINARIES present)."""
    (root / "provision").mkdir(parents=True)
    (root / "provision" / "run-all.ps1").write_text("# run-all\n")
    (root / "provision" / "00-bootstrap.ps1").write_text("# bootstrap\n")
    (root / "provision" / "99-marker.ps1").write_text("# marker\n")
    (root / "provision" / "assets").mkdir()
    (root / "provision" / "assets" / "run-agent.ps1").write_text("# run-agent\n")
    (root / "provision" / "assets" / "steam-session.ps1").write_text("# steam-session\n")
    (root / "provision" / "assets" / "steam-launch.ps1").write_text("# steam-launch\n")
    (root / "provision" / "assets" / "steam-cursor.ps1").write_text("# steam-cursor\n")
    (root / "provision" / "assets" / "apollo-junction.ps1").write_text("# junction\n")
    # Depuis le 2026-08-30 explorer.exe est le shell : le kiosque a laisse la
    # place a deux taches AtLogOn, et la pile Xbox a son propre asset.
    (root / "provision" / "assets" / "steam-hold-notice.ps1").write_text("# hold notice\n")
    (root / "provision" / "assets" / "desktop-chrome.ps1").write_text("# desktop chrome\n")
    (root / "provision" / "assets" / "xbox-stack.ps1").write_text("# xbox stack\n")
    (root / "provision" / "assets" / "winget-path.ps1").write_text("# winget path\n")
    (root / "provision" / "assets" / "gaming-services.ps1").write_text("# gaming\n")
    (root / "provision" / "assets" / "retro-status.ps1").write_text("# retro-status\n")
    (root / "provision" / "assets" / "retro-7zr.ps1").write_text("# retro-7zr\n")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "wallpaper.png").write_bytes(b"\x89PNG\r\n")
    (root / "probe").mkdir()
    (root / "probe" / "advanced-color.ps1").write_text("# probe\n")
    drivers = root / "drivers"
    (drivers / "nvidia").mkdir(parents=True)
    (drivers / "nvidia" / "580.00-desktop-win11-64bit.exe").write_bytes(b"MZ")
    (drivers / "apollo").mkdir()
    (drivers / "apollo" / "Apollo-0.4.6.exe").write_bytes(b"MZ")
    (drivers / "steam").mkdir()
    (drivers / "steam" / "SteamSetup.exe").write_bytes(b"MZ")
    (drivers / "virtio" / "netkvm").mkdir(parents=True)
    (drivers / "virtio" / "netkvm" / "netkvm.inf").write_text("[Version]\n")
    (drivers / "winfsp").mkdir()
    (drivers / "winfsp" / "winfsp-2.0.msi").write_bytes(b"MSI")
    (drivers / "agent").mkdir()
    (drivers / "agent" / "agent.exe").write_bytes(b"MZ")
    (drivers / "winget" / "deps").mkdir(parents=True)
    (drivers / "winget" / "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle").write_bytes(b"PK")
    (drivers / "winget" / "License1.xml").write_text("<License/>\n")
    (drivers / "winget" / "deps" / "Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx").write_bytes(b"PK")
    (drivers / "winget" / "deps" / "Microsoft.WindowsAppRuntime.1.8_8000.616.304.0_x64.appx").write_bytes(b"PK")
    config = root / "config"
    config.mkdir()
    for name in ["sunshine.conf", "apps.json", "secrets.psd1"]:
        (config / name).write_text(f"# {name}\n")
    # retro.psd1 must carry real "Enabled = $true/$false" content:
    # payload.retro_enabled() parses it and stage_payload() requires the
    # promised binaries to actually be present, so a placeholder comment
    # (unlike the other config/* stubs above) is refused, not ignored.
    (config / "retro.psd1").write_text("@{\n    Enabled = $false\n}\n")
    return payload.PayloadSources(provision_dir=root / "provision",
                                  probe_dir=root / "probe",
                                  drivers_dir=drivers,
                                  config_dir=config,
                                  assets_dir=root / "assets")


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")

    check("nothing missing on a complete tree",
          payload.missing_binaries(sources.drivers_dir), [])

    plan = dict(payload.plan_payload(sources))
    dests = sorted(plan.values())
    check("provision scripts are staged",
          "provision/run-all.ps1" in dests, True)
    check("probe is staged", "probe/advanced-color.ps1" in dests, True)
    check("nvidia driver is staged",
          "drivers/nvidia/580.00-desktop-win11-64bit.exe" in dests, True)
    check("apollo installer is staged",
          "drivers/apollo/Apollo-0.4.6.exe" in dests, True)
    check("no absolute destination",
          any(d.startswith("/") for d in dests), False)

    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11 IoT Enterprise LTSC 2024",
                                "20260822-1200")
    payload.stage_payload(dest, sources, marker)
    payload.verify_staged(dest)
    check("marker written", (dest / payload.MARKER_NAME).exists(), True)

    parsed = payload.parse_marker((dest / payload.MARKER_NAME).read_text())
    check("marker target build", parsed["target_build"], "26100")
    check("marker provision version",
          parsed["provision_version"], payload.PROVISION_VERSION)
    check("marker image name",
          parsed["image_name"], "Windows 11 IoT Enterprise LTSC 2024")

    # A staged tree missing its marker is a broken payload, not a warning.
    (dest / payload.MARKER_NAME).unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted a payload with no marker")
    except payload.PayloadError:
        pass

# Test stage_payload rejects dest_root equal to source directory
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    try:
        payload.stage_payload(sources.provision_dir, sources, "marker")
        failures.append("stage_payload: accepted dest_root == source dir")
    except payload.PayloadError:
        pass
    check("source dir still exists after rejected stage",
          sources.provision_dir.exists(), True)

# Test staging twice removes stray file
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "stray.txt").write_text("stray")
    check("stray file exists", (dest / "stray.txt").exists(), True)
    payload.stage_payload(dest, sources, marker)
    check("stray file removed after restage",
          (dest / "stray.txt").exists(), False)

# verify_staged must catch a required binary removed AFTER staging, not just
# a source tree missing one - this is the tamper-detection path, and the
# only test exercising it was removed for the SudoVDA rewrite (fix round 1).
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "drivers" / "agent" / "agent.exe").unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted a staged tree missing agent.exe")
    except payload.PayloadError as e:
        if "agent.exe" not in str(e):
            failures.append(f"verify_staged error doesn't name agent.exe: {e}")

# Same tamper-detection path for the Apollo configuration added in task 9:
# verify_staged must fail if a config/* file is removed from an already
# staged tree, not just when config_dir was never supplied in the first
# place - otherwise a future refactor could make the requirement conditional
# on config_dir without any test noticing.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "config" / "sunshine.conf").unlink()
    try:
        payload.verify_staged(dest)
        failures.append(
            "verify_staged: accepted a staged tree missing config/sunshine.conf")
    except payload.PayloadError as e:
        if "config/sunshine.conf" not in str(e):
            failures.append(
                f"verify_staged error doesn't name config/sunshine.conf: {e}")

# retro.psd1 must be required exactly like the other config/* files: it must
# be present ALWAYS, whether or not retro was checked, so a missing file can
# never be mistaken for "the option is off" (see apollo.render_retro()).
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11", "20260822")
    payload.stage_payload(dest, sources, marker)
    (dest / "config" / "retro.psd1").unlink()
    try:
        payload.verify_staged(dest)
        failures.append(
            "verify_staged: accepted a staged tree missing config/retro.psd1")
    except payload.PayloadError as e:
        if "config/retro.psd1" not in str(e):
            failures.append(
                f"verify_staged error doesn't name config/retro.psd1: {e}")

# FIX 6 (final review): provision/assets/*.ps1 scripts are artefacts
# consumed by 40-agent.ps1 and 25-apollo.ps1 (which also dot-sources
# apollo-junction.ps1), and must be declared in verify_staged's required
# list just like any other stage script - a rename would otherwise fail
# deep inside the offline guest instead of at build time.
# retro-status.ps1 et retro-7zr.ps1 : dot-sources par 32-retro.ps1, le premier
# AVANT meme qu elle lise le basculement - une charge utile sans eux tue une
# etape qui n avait, peut-etre, rien a faire.
# steam-cursor.ps1 : dot-source par steam-session.ps1, la commande suivie
# d Apollo. Absent, le masquage du curseur de Big Picture disparait en silence -
# exactement la facon dont la dette C4 s etait creee.
for asset in ["run-agent.ps1", "steam-session.ps1", "steam-launch.ps1",
              "steam-cursor.ps1", "apollo-junction.ps1", "retro-status.ps1",
              "retro-7zr.ps1"]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        sources = make_tree(root / "src")
        dest = root / "staging" / "nivuus"
        marker = payload.marker_text("Windows 11", "20260822")
        payload.stage_payload(dest, sources, marker)
        (dest / "provision" / "assets" / asset).unlink()
        try:
            payload.verify_staged(dest)
            failures.append(
                f"verify_staged: accepted a staged tree missing assets/{asset}")
        except payload.PayloadError as e:
            if f"assets/{asset}" not in str(e):
                failures.append(
                    f"verify_staged error doesn't name assets/{asset}: {e}")

# FIX 7 (final review): a dot-directory anywhere under a source tree (e.g.
# fetch_payload.py's .build-cache/, which holds the source virtio-win.iso
# and the build manifest) is host-side bookkeeping and must never reach the
# staged payload - previously ~700 MB of dead weight rode along in every
# built image.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    cache = sources.drivers_dir / "virtio" / ".build-cache"
    cache.mkdir(parents=True)
    (cache / "virtio-win.iso").write_bytes(b"not actually an iso")
    dests = [rel for _, rel in payload.plan_payload(sources)]
    check("dot-directory contents are excluded from the payload plan",
          any(".build-cache" in d for d in dests), False)
    dest = root / "staging" / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("Windows 11", "20260822"))
    check("dot-directory contents are not staged",
          (dest / "drivers" / "virtio" / ".build-cache").exists(), False)

# Les DEUX assets de la chaine winget que verify_staged laissait passer.
# xbox-stack.ps1 y etait, ses deux jumeaux non - et ce sont eux que les etapes
# 33 et 34 copient sur C: sous $ErrorActionPreference = 'Stop'. Absents, le
# Copy-Item leve : l etape 34, qui promet de ne JAMAIS faire echouer le
# provisionnement, le fait quand meme, et pour une raison qui appartient a la
# construction. Meme raisonnement que steam-cursor.ps1 en 2026-08-29 : ce qui
# se constate a la construction ne doit pas se decouvrir sur l invite.
_required = inspect.getsource(payload.verify_staged)
for _asset in ("winget-path.ps1", "gaming-services.ps1", "xbox-stack.ps1"):
    check(f"verify_staged exige provision/assets/{_asset}",
          f"provision/assets/{_asset}" in _required, True)

# --- Sous-projet B : la charge utile déclare ses artefacts en un seul endroit.
# ⚠️ PROVISION_VERSION n'est PAS touché ici : test_windows_guest_provision.py
# le recoupe avec la chaîne écrite par 99-marker.ps1, donc les deux doivent
# bouger ensemble. C'est la tâche 8 qui les bascule en B1.

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    missing = payload.missing_binaries(root)
    joined = "\n".join(missing)
    for needle in ["nvidia", "apollo", "steam", "virtio", "winfsp", "agent",
                   "winget"]:
        check(f"empty payload reports {needle} missing", needle in joined, True)
    # SudoVDA rides inside the Apollo installer; requiring it separately would
    # install the same IDD twice.
    check("sudovda is not required separately", "sudovda" in joined.lower(), False)

def _make_complete_payload(root: pathlib.Path) -> None:
    (root / "nvidia").mkdir(parents=True)
    (root / "nvidia" / "610.88.exe").write_text("x")
    (root / "apollo").mkdir()
    (root / "apollo" / "Apollo-0.4.6.exe").write_text("x")
    (root / "steam").mkdir()
    (root / "steam" / "SteamSetup.exe").write_text("x")
    (root / "virtio" / "netkvm").mkdir(parents=True)
    (root / "virtio" / "netkvm" / "netkvm.inf").write_text("x")
    (root / "virtio" / "viofs").mkdir(parents=True)
    (root / "virtio" / "viofs" / "viofs.inf").write_text("x")
    (root / "winfsp").mkdir()
    (root / "winfsp" / "winfsp-2.0.msi").write_text("x")
    (root / "agent").mkdir()
    (root / "agent" / "agent.exe").write_text("x")
    (root / "winget" / "deps").mkdir(parents=True)
    (root / "winget" / "App.msixbundle").write_text("x")
    (root / "winget" / "License1.xml").write_text("x")
    (root / "winget" / "deps" / "Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64.appx").write_text("x")
    (root / "winget" / "deps" / "Microsoft.WindowsAppRuntime.1.8_8000.616.304.0_x64.appx").write_text("x")

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    check("complete payload reports nothing missing",
          payload.missing_binaries(root), [])

# viofs is a comfort, not a requirement: the guest streams fine without the
# /media/data share, so a missing viofs must NOT fail the build.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    shutil.rmtree(root / "virtio" / "viofs")
    check("missing viofs does not fail the build",
          payload.missing_binaries(root), [])

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    shutil.rmtree(root / "virtio" / "netkvm")
    check("missing netkvm fails the build",
          any("netkvm" in m for m in payload.missing_binaries(root)), True)

# The irreplaceable artefact deserves its own message: no machine can rebuild
# agent.exe once the current VM is wiped.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    (src / "provision").mkdir(parents=True)
    (src / "probe").mkdir()
    drivers = src / "drivers"
    _make_complete_payload(drivers)
    shutil.rmtree(drivers / "agent")
    sources = payload.PayloadSources(
        provision_dir=src / "provision", probe_dir=src / "probe",
        drivers_dir=drivers)
    try:
        payload.stage_payload(root / "dest", sources, "marker")
        failures.append("stage_payload: accepted a payload with no agent.exe")
    except payload.PayloadError as e:
        if "extracted from the current Windows VM" not in str(e):
            failures.append(f"stage_payload error missing the agent warning: {e}")

# The rendered configuration must ride in the payload, or 25-apollo has
# nothing to copy and 50-power has no password to set autologon with.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    (src / "provision").mkdir(parents=True)
    (src / "provision" / "run-all.ps1").write_text("x")
    (src / "provision" / "00-bootstrap.ps1").write_text("x")
    (src / "provision" / "99-marker.ps1").write_text("x")
    (src / "provision" / "assets").mkdir()
    (src / "provision" / "assets" / "run-agent.ps1").write_text("x")
    (src / "provision" / "assets" / "steam-session.ps1").write_text("x")
    (src / "provision" / "assets" / "steam-launch.ps1").write_text("x")
    (src / "provision" / "assets" / "steam-cursor.ps1").write_text("x")
    (src / "provision" / "assets" / "apollo-junction.ps1").write_text("x")
    (src / "provision" / "assets" / "steam-hold-notice.ps1").write_text("x")
    (src / "provision" / "assets" / "desktop-chrome.ps1").write_text("x")
    (src / "provision" / "assets" / "xbox-stack.ps1").write_text("x")
    (src / "provision" / "assets" / "winget-path.ps1").write_text("x")
    (src / "provision" / "assets" / "gaming-services.ps1").write_text("x")
    (src / "provision" / "assets" / "retro-status.ps1").write_text("x")
    (src / "provision" / "assets" / "retro-7zr.ps1").write_text("x")
    (src / "assets").mkdir(exist_ok=True)
    (src / "assets" / "wallpaper.png").write_bytes(b"\x89PNG\r\n")
    (src / "probe").mkdir()
    (src / "probe" / "advanced-color.ps1").write_text("x")
    drivers = src / "drivers"
    _make_complete_payload(drivers)
    cfg = src / "config"
    cfg.mkdir()
    for name in ["sunshine.conf", "apps.json", "secrets.psd1"]:
        (cfg / name).write_text("x")
    (cfg / "retro.psd1").write_text("@{\n    Enabled = $false\n}\n")
    sources = payload.PayloadSources(
        provision_dir=src / "provision", probe_dir=src / "probe",
        drivers_dir=drivers, config_dir=cfg, assets_dir=src / "assets")
    dest = root / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("img", "b1"))
    payload.verify_staged(dest)
    check("the staged payload carries the rendered config",
          (dest / "config" / "sunshine.conf").is_file(), True)
    check("the staged payload carries the secrets",
          (dest / "config" / "secrets.psd1").is_file(), True)
    check("the staged payload carries the retro toggle",
          (dest / "config" / "retro.psd1").is_file(), True)

# L en-tete du module disait « provisioning runs with no network at all »
# quand l etape 32 telecharge les emulateurs depuis l invite. La prose n est
# gardee par rien ; ce controle-ci est le garde bon marche : restaurer le texte
# mensonger le fait tomber.
_doc = payload.__doc__
check("l en-tete de payload.py ne nie plus le reseau de l etape 32",
      "no network at all" in _doc, False)
check("... et nomme l exception, son objet et sa contrepartie",
      "exception" in _doc.lower() and "retro install" in _doc
      and "sha256" in _doc, True)

# --- Tache 4 (sous-projet C2) : ce que la charge utile doit PORTER quand le
# retrogaming est active, et surtout ne pas porter sinon.
#
# La source de verite est le basculement RENDU (config/retro.psd1), celui-la
# meme que 32-retro.ps1 lira sur l invite : un second drapeau passe en
# parallele serait un deuxieme endroit a tenir a jour, et leur divergence ne
# se verrait qu une heure plus tard, sur une machine sans ecran.

def _add_retro(drivers: pathlib.Path) -> None:
    """Ce que `fetch_payload.py --retro` depose dans drivers/retro/."""
    (drivers / "retro").mkdir(parents=True, exist_ok=True)
    (drivers / "retro" / "7zr.exe").write_bytes(b"MZ")
    (drivers / "retro" / "python-3.12.10-amd64.exe").write_bytes(b"MZ")
    wheels = drivers / "retro" / "wheels"
    wheels.mkdir(exist_ok=True)
    for name in ["retro-0.1.0-py3-none-any.whl", "py7zr-1.1.3-py3-none-any.whl",
                 "vdf-3.4-py2.py3-none-any.whl", "requests-2.34.2-py3-none-any.whl"]:
        (wheels / name).write_bytes(b"PK\x03\x04")


_ENABLED = "@{\n    Enabled = $true\n}\n"
_DISABLED = "@{\n    Enabled = $false\n}\n"

# Le drapeau seul, sur l inventaire des binaires : sans retrogaming, aucun
# des artefacts retro n est reclame - une installation qui ne l a pas coche
# n a pas a porter un interpreteur, un extracteur et des roues.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    check("sans retrogaming, la charge utile est complete sans drivers/retro",
          payload.missing_binaries(root, retro=False), [])
    _joined = "\n".join(payload.missing_binaries(root, retro=True))
    for needle in ["7zr.exe", "python-*-amd64.exe", "retro-*.whl", "py7zr-*.whl",
                   "vdf-*.whl", "requests-*.whl"]:
        check(f"avec retrogaming, {needle} est reclame", needle in _joined, True)
    # Le message doit dire POURQUOI 7zr est exige : c est le prerequis qu on
    # prend pour une commodite, et sans lui aucun emulateur retro ne s installe.
    check("le message explique pourquoi 7zr.exe est une exigence",
          "BCJ2" in _joined, True)
    _add_retro(root)
    check("les artefacts deposes satisfont l exigence",
          payload.missing_binaries(root, retro=True), [])

# Le basculement pilote la construction : Enabled = $true sans drivers/retro
# doit ECHOUER A LA CONSTRUCTION, pas une heure plus tard sur l invite.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    (sources.config_dir / "retro.psd1").write_text(_ENABLED)
    try:
        payload.stage_payload(root / "staging" / "nivuus", sources, "marker")
        failures.append("stage_payload: accepted Enabled = $true with no drivers/retro")
    except payload.PayloadError as e:
        if "7zr.exe" not in str(e) or "retro-*.whl" not in str(e):
            failures.append(f"stage_payload error doesn't name the retro artefacts: {e}")
        # Tous les messages de l etape 32, cote invite, nomment la commande a
        # relancer. Celui de la construction ne le faisait pas : le
        # proprietaire lisait « 7zr.exe manque » sans savoir comment l obtenir.
        if "fetch_payload.py" not in str(e) or "--retro" not in str(e):
            failures.append(
                f"the build failure doesn't name the command to re-run: {e}")

# Le meme arbre, avec les artefacts : la construction passe.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    (sources.config_dir / "retro.psd1").write_text(_ENABLED)
    _add_retro(sources.drivers_dir)
    dest = root / "staging" / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("Windows 11", "b1"))
    payload.verify_staged(dest)
    check("les roues du paquet voyagent jusqu a l invite",
          (dest / "drivers" / "retro" / "wheels" / "retro-0.1.0-py3-none-any.whl").is_file(),
          True)
    check("7zr.exe voyage jusqu a l invite",
          (dest / "drivers" / "retro" / "7zr.exe").is_file(), True)
    # Detection d alteration APRES mise en place, comme pour agent.exe : une
    # roue retiree de l arbre construit doit se voir ici, pas sur l invite.
    (dest / "drivers" / "retro" / "wheels" / "py7zr-1.1.3-py3-none-any.whl").unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted a retro payload with no py7zr wheel")
    except payload.PayloadError as e:
        if "py7zr" not in str(e):
            failures.append(f"verify_staged error doesn't name the py7zr wheel: {e}")

# Sans retrogaming, la MEME construction passe sans le moindre artefact retro :
# c est la propriete que l option existe pour tenir.
#
# _add_retro() est ICI ce que le controle a de mordant : le dossier des
# pilotes est un arbre de travail qui PERSISTE entre deux constructions, donc
# un « fetch_payload.py --retro » anterieur y laisse ses 30 Mo. Sans cette
# ligne, le controle affirmait l absence d un dossier que la fixture n avait
# jamais cree - vrai, et vide de sens.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    (sources.config_dir / "retro.psd1").write_text(_DISABLED)
    _add_retro(sources.drivers_dir)
    check("la fixture porte bien un drivers/retro laisse par une recuperation "
          "anterieure", (sources.drivers_dir / "retro" / "7zr.exe").is_file(), True)
    dest = root / "staging" / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("Windows 11", "b1"))
    payload.verify_staged(dest)
    check("sans retrogaming, rien de retro n est mis en place",
          (dest / "drivers" / "retro").exists(), False)
    # Et l exclusion ne doit pas emporter le reste des pilotes avec elle.
    check("les autres pilotes voyagent quand meme",
          (dest / "drivers" / "steam" / "SteamSetup.exe").is_file()
          and (dest / "drivers" / "agent" / "agent.exe").is_file(), True)

# Un basculement qui ne dit NI l un NI l autre est une erreur franche : le lire
# comme « desactive » retirerait silencieusement la fonctionnalite d une
# construction qui l avait demandee. C est la meme confusion (absent contre
# explicitement desactive) que ce fichier existe pour empecher.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")
    (sources.config_dir / "retro.psd1").write_text("@{\n    Enabled = maybe\n}\n")
    try:
        payload.stage_payload(root / "staging" / "nivuus", sources, "marker")
        failures.append("stage_payload: accepted a toggle saying neither true nor false")
    except payload.PayloadError as e:
        if "Enabled" not in str(e):
            failures.append(f"the ambiguous-toggle error doesn't name Enabled: {e}")

with tempfile.TemporaryDirectory() as tmp:
    cfg = pathlib.Path(tmp)
    (cfg / "retro.psd1").write_text(_ENABLED)
    check("Enabled = $true est lu comme actif", payload.retro_enabled(cfg), True)
    (cfg / "retro.psd1").write_text(_DISABLED)
    check("Enabled = $false est lu comme inactif", payload.retro_enabled(cfg), False)
    (cfg / "retro.psd1").unlink()
    try:
        payload.retro_enabled(cfg)
        failures.append("retro_enabled: accepted a missing toggle as 'off'")
    except payload.PayloadError:
        pass


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all payload staging tests passed")
