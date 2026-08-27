#!/usr/bin/env python3
"""Tests for the wizard's Pydantic config models (webapp/models.py).

Focus: the "retro" feature declared in task 3 of the console-provisioning
sub-project. retro (RetroArch, via the `retro` package, on the Windows guest
VM) is OPTIONAL like every
other feature (docker, wifi-ap, home-assistant...) and depends on the guest
VM itself (the "kvm-vfio" feature): checking retro without it must be
refused at submit time, not discovered later as a failed step on a headless
machine.

Run: python3 scripts/tests/test_webapp_models.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "webapp"))

from pydantic import ValidationError  # noqa: E402

import models  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def base_kwargs(features):
    return dict(
        disk={"path": "/dev/nvme0n1"},
        user={"username": "nivuus", "password": "x"},
        features=features,
    )


def check_raises(label, fn):
    try:
        fn()
    except ValidationError:
        return
    failures.append(f"{label}: raised nothing, want ValidationError")


check("retro is a known feature (declared, like docker/wifi-ap)",
      "retro" in models.KNOWN_FEATURES, True)

# The core contract: retro cannot make sense without the VM it runs on.
check_raises(
    "retro without kvm-vfio is refused",
    lambda: models.InstallConfig(**base_kwargs(["retro"])),
)

# The same request, with kvm-vfio also checked, must be accepted.
cfg = models.InstallConfig(**base_kwargs(["kvm-vfio", "retro"]))
check("retro survives validation alongside kvm-vfio",
      set(cfg.features) >= {"kvm-vfio", "retro"}, True)

# --- Requirement: an install that does NOT check retro must behave exactly
# as it did before this option existed. A config with no "retro" at all
# (docker checked, nothing else touching retro) must validate exactly like
# before - no new error, no feature silently added.
cfg_no_retro = models.InstallConfig(**base_kwargs(["docker"]))
check("features are unaffected when retro is not requested",
      "retro" not in cfg_no_retro.features, True)
check("os-base is still auto-added, same as before retro existed",
      "os-base" in cfg_no_retro.features, True)

# An empty feature list (the wizard's own default) must still validate with
# no mention of retro anywhere - the option is invisible unless checked.
cfg_default = models.InstallConfig(**base_kwargs([]))
check("the default feature list carries no retro",
      "retro" not in cfg_default.features, True)

# --- packages ------------------------------------------------------------- #
# The wizard carries package answers as an opaque mapping: validating them
# against each package's own question vocabulary is the engine's job - it is
# the only side that can read the manifests - so the model checks the shape
# and nothing else.
cfg_pkg = models.InstallConfig(**base_kwargs(["os-base"]),
                               packages={"console": {"retro": True}})
check("packages are carried through", cfg_pkg.packages["console"]["retro"], True)
check("packages default to empty",
      models.InstallConfig(**base_kwargs(["os-base"])).packages, {})

check_raises(
    "an invalid package name is refused",
    lambda: models.InstallConfig(**base_kwargs(["os-base"]),
                                 packages={"Console!": {}}),
)
check_raises(
    "a non-mapping answer set is refused",
    lambda: models.InstallConfig(**base_kwargs(["os-base"]),
                                 packages={"console": "oui"}),
)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all webapp model tests passed")
