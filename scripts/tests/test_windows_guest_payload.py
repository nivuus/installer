#!/usr/bin/env python3
"""Tests for the offline payload staged onto the unattend ISO (payload.py).

Run: python3 scripts/tests/test_windows_guest_payload.py
"""
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

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
    (root / "provision" / "assets" / "maximize-steam.ps1").write_text("# maximize\n")
    (root / "provision" / "assets" / "apollo-junction.ps1").write_text("# junction\n")
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
    config = root / "config"
    config.mkdir()
    for name in ["sunshine.conf", "apps.json", "secrets.psd1"]:
        (config / name).write_text(f"# {name}\n")
    return payload.PayloadSources(provision_dir=root / "provision",
                                  probe_dir=root / "probe",
                                  drivers_dir=drivers,
                                  config_dir=config)


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

# FIX 6 (final review): provision/assets/*.ps1 scripts are artefacts
# consumed by 40-agent.ps1 and 25-apollo.ps1 (which also dot-sources
# apollo-junction.ps1), and must be declared in verify_staged's required
# list just like any other stage script - a rename would otherwise fail
# deep inside the offline guest instead of at build time.
for asset in ["run-agent.ps1", "maximize-steam.ps1", "apollo-junction.ps1"]:
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

# --- Sous-projet B : la charge utile déclare ses artefacts en un seul endroit.
# ⚠️ PROVISION_VERSION n'est PAS touché ici : test_windows_guest_provision.py
# le recoupe avec la chaîne écrite par 99-marker.ps1, donc les deux doivent
# bouger ensemble. C'est la tâche 8 qui les bascule en B1.

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    missing = payload.missing_binaries(root)
    joined = "\n".join(missing)
    for needle in ["nvidia", "apollo", "steam", "virtio", "winfsp", "agent"]:
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
    (src / "provision" / "assets" / "maximize-steam.ps1").write_text("x")
    (src / "provision" / "assets" / "apollo-junction.ps1").write_text("x")
    (src / "probe").mkdir()
    (src / "probe" / "advanced-color.ps1").write_text("x")
    drivers = src / "drivers"
    _make_complete_payload(drivers)
    cfg = src / "config"
    cfg.mkdir()
    for name in ["sunshine.conf", "apps.json", "secrets.psd1"]:
        (cfg / name).write_text("x")
    sources = payload.PayloadSources(
        provision_dir=src / "provision", probe_dir=src / "probe",
        drivers_dir=drivers, config_dir=cfg)
    dest = root / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("img", "b1"))
    payload.verify_staged(dest)
    check("the staged payload carries the rendered config",
          (dest / "config" / "sunshine.conf").is_file(), True)
    check("the staged payload carries the secrets",
          (dest / "config" / "secrets.psd1").is_file(), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all payload staging tests passed")
