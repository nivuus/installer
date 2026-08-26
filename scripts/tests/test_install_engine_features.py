#!/usr/bin/env python3
"""Tests for the retro (retrogaming) handling in the install engine's
features step (install-engine/steps/features.py), task 3 of the
console-provisioning sub-project.

retro installs nothing inside the Debian chroot - it is entirely a Windows
guest VM concern, built later and separately by windows-guest/build.py. This
step's only job is to record the operator's choice durably on the target, so
that a missing state can never be confused with "the option is off". No
chroot, no root privileges and no real target filesystem are needed: these
tests exercise apply_features() against a plain temporary directory, and none
of the paths reached with the default feature list ("os-base" only) invoke
chroot/apt at all.

Run: python3 scripts/tests/test_install_engine_features.py
"""
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))

from steps import features  # noqa: E402
from steps.util import StepError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {exc_type.__name__}")
        return
    failures.append(f"{label}: raised nothing, want {exc_type.__name__}")


class FakeEmit:
    """Collects progress messages; apply_features/_retro only call .info()."""

    def __init__(self):
        self.messages = []

    def info(self, stage, pct, msg):
        self.messages.append((stage, pct, msg))


def retro_state(target: pathlib.Path) -> dict:
    return json.loads((target / features.RETRO_STATE_PATH).read_text())


# --- _retro() directly -------------------------------------------------- #

# Not selected: the file must still exist and say so explicitly. An absent
# file is ambiguous (option off, or a target built by an older installer
# that never had this option at all?); an explicit "enabled": false is not.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features._retro(str(target), {"os-base"}, emit)
    check("retro state file exists when unchecked",
          (target / features.RETRO_STATE_PATH).is_file(), True)
    check("retro state is explicitly disabled when unchecked",
          retro_state(target), {"enabled": False})

# Selected, alongside its VM dependency: enabled, and no error.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features._retro(str(target), {"os-base", "kvm-vfio", "retro"}, emit)
    check("retro state is enabled when checked with the VM",
          retro_state(target), {"enabled": True})

# Selected WITHOUT its VM dependency: must be refused, loudly, before any
# file is written - checking retro without the Windows guest VM cannot work,
# and failing here beats discovering it later on a screenless machine.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    check_raises("retro without kvm-vfio is refused", StepError,
                 lambda: features._retro(str(target), {"os-base", "retro"}, emit))
    check("no state file is left behind by a refused combination",
          (target / features.RETRO_STATE_PATH).exists(), False)

# --- apply_features(), the real entry point ------------------------------ #

# The wizard's own default ("os-base" alone, nothing else checked) is
# exactly what an install looked like before "retro" existed. This must
# still work with no chroot and no root available: none of the other
# feature blocks (kvm-vfio, networking, wifi-ap, firewall, docker,
# home-assistant) are selected, so apply_features must not attempt to touch
# a chroot at all - only _retro's plain file write happens.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    features.apply_features({"features": ["os-base"]}, str(target), "/nivuus",
                            {}, emit)
    check("an unchecked-retro install writes an explicit 'disabled' marker",
          retro_state(target), {"enabled": False})
    # Nothing else about a plain os-base install should exist: no bridges,
    # no hostapd, no firewall sysctl file - the marker is the ONLY new thing
    # a retro-unaware install from before this task would not have had.
    other_paths = [
        "etc/NetworkManager/system-connections",
        "etc/hostapd",
        "etc/sysctl.d/99-nivuus-forward.conf",
    ]
    for rel in other_paths:
        check(f"unrelated feature output stays absent: {rel}",
              (target / rel).exists(), False)

# retro + kvm-vfio together: apply_features would also try to run
# install.sh inside a real chroot for kvm-vfio, which this sandbox cannot
# provide - so only exercise the guard failure path here, which raises
# before any chroot command runs.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    emit = FakeEmit()
    check_raises(
        "apply_features refuses retro without kvm-vfio too", StepError,
        lambda: features.apply_features(
            {"features": ["os-base", "retro"]}, str(target), "/nivuus", {}, emit),
    )

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all install-engine retro feature tests passed")
