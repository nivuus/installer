#!/usr/bin/env python3
"""Tests for the thermal feature - all that remains of install.sh.

install.sh had seven blocks; five were VM setup and now live in the console
package's install hook. What was left is fifteen lines: deploy the thermal
script and its unit. The NIVUUS_DIR / NIVUUS_IN_CHROOT / NIVUUS_ISOLCPUS /
NIVUUS_VFIO_IDS plumbing existed only to make that script runnable inside a
chroot, and it went with it.

Run: python3 scripts/tests/test_install_engine_features.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import features  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(msg)

    def warn(self, step, pct, msg):
        self.lines.append(msg)

    def error(self, step, pct, msg):
        self.lines.append(msg)


calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)

    class R:
        returncode = 0
    return R()


features.chroot_run = fake_chroot_run

# Sans la feature thermal, rien ne doit etre pose.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    features.apply_features({"features": ["os-base"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    check("aucune unite thermique sans la feature",
          (target / "etc/systemd/system/cpu-thermal-optimization.service").exists(),
          False)

# Avec la feature, l unite et le script arrivent.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    payload = target / "opt/nivuus/scripts"
    payload.mkdir(parents=True)
    (payload / "optimize-cpu-thermal.sh").write_text("#!/bin/bash\ntrue\n")

    features.apply_features({"features": ["os-base", "thermal"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    unit = target / "etc/systemd/system/cpu-thermal-optimization.service"
    check("l unite thermique est posee", unit.is_file(), True)
    check("elle pointe vers le script deploye",
          "/usr/local/bin/optimize-cpu-thermal.sh" in unit.read_text(), True)
    check("le script est deploye",
          (target / "usr/local/bin/optimize-cpu-thermal.sh").is_file(), True)
    check("l unite est activee",
          any("cpu-thermal-optimization.service" in " ".join(c) for c in calls),
          True)

# Les features VM ont quitte ce fichier.
check("plus de _kvm_vfio_thermal", hasattr(features, "_kvm_vfio_thermal"), False)
check("plus de _retro", hasattr(features, "_retro"), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all thermal feature tests passed")
