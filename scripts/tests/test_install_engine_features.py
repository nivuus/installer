#!/usr/bin/env python3
"""Tests for the retro (retrogaming) handling in the install engine's
features step (install-engine/steps/features.py), task 3 of the
console-provisioning sub-project.

retro installs nothing inside the Debian chroot - it is entirely a Windows
guest VM concern, provisioned later and separately by
windows-guest/build.py. This step's only job is to record the operator's
choice durably on the target, at the path common/retro.py defines once
(RETRO_STATE_REL_PATH) for both this writer and build.py, its reader -
see test_retro_marker_bridge.py for the test proving the two agree. No
chroot, no root privileges and no real target
filesystem are needed: these tests exercise apply_features() against a
plain temporary directory, and none of the paths reached with the default
feature list ("os-base" only) or with "retro" alone invoke chroot/apt at
all.

Run: python3 scripts/tests/test_install_engine_features.py
"""
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
# installer/ root, for `common` - features.py imports common.retro.
sys.path.insert(0, str(REPO / "installer"))

from steps import features  # noqa: E402
from common.retro import retro_state_path  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    """Collects progress messages; apply_features/_retro call .info/.warn."""

    def __init__(self):
        self.info_messages = []
        self.warn_messages = []

    def info(self, stage, pct, msg):
        self.info_messages.append((stage, pct, msg))

    def warn(self, stage, pct, msg):
        self.warn_messages.append((stage, pct, msg))


def retro_state(target: pathlib.Path) -> dict:
    return json.loads(pathlib.Path(retro_state_path(str(target))).read_text())


# --- _retro() directly -------------------------------------------------- #

# Selected, alongside its VM dependency: enabled, no warning.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features._retro(str(target), {"os-base", "kvm-vfio", "retro"}, emit)
    check("retro state is enabled when checked with the VM",
          retro_state(target), {"enabled": True})
    check("no warning when the VM dependency is met",
          emit.warn_messages, [])

# Selected WITHOUT its VM dependency: this must NOT abort an
# otherwise-complete install (disk partitioned, base system installed,
# bootloader written) over a file nothing reads yet - warn and record
# retro as disabled instead. The wizard's own guard (webapp/models.py) is
# what actually stops this combination from reaching here in practice.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features._retro(str(target), {"os-base", "retro"}, emit)
    check("retro is recorded as disabled without its VM dependency",
          retro_state(target), {"enabled": False})
    check("a warning is emitted, not an exception",
          len(emit.warn_messages) >= 1, True)
    check("the warning names kvm-vfio",
          any("kvm-vfio" in m for _, _, m in emit.warn_messages), True)

# --- apply_features(), the real entry point ------------------------------ #

# The wizard's own default ("os-base" alone, nothing else checked) is
# exactly what an install looked like before "retro" existed. apply_features
# must behave identically: retro is gated by "if 'retro' in features" like
# every other feature in this file, so nothing about retro runs at all -
# no marker, no progress line, no state left behind. This is the property
# the task cares about most: an unchecked install must be indistinguishable
# from one built before this option existed.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features.apply_features({"features": ["os-base"]}, str(target), "/nivuus",
                            {}, emit)
    check("no retro marker is written when retro was not selected",
          pathlib.Path(retro_state_path(str(target))).exists(), False)
    check("no retro progress line either",
          any("retro" in msg.lower()
              for _, _, msg in emit.info_messages + emit.warn_messages),
          False)
    # Nothing else about a plain os-base install should exist: no bridges,
    # no hostapd, no firewall sysctl file.
    other_paths = [
        "etc/NetworkManager/system-connections",
        "etc/hostapd",
        "etc/sysctl.d/99-nivuus-forward.conf",
    ]
    for rel in other_paths:
        check(f"unrelated feature output stays absent: {rel}",
              (target / rel).exists(), False)

# retro alone (no kvm-vfio): apply_features must still complete - no chroot
# call happens for "retro" itself, so this exercises the full function
# without needing a real chroot, and pins that the warn-and-disable path
# (not an exception) is what apply_features actually reaches too.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features.apply_features({"features": ["os-base", "retro"]}, str(target),
                            "/nivuus", {}, emit)
    check("apply_features completes for retro without kvm-vfio",
          retro_state(target), {"enabled": False})

# retro + kvm-vfio together would also try to run install.sh inside a real
# chroot for kvm-vfio, which this sandbox cannot provide - not exercised
# end-to-end here; _retro() alone already pins the "enabled" case above.

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all install-engine retro feature tests passed")
