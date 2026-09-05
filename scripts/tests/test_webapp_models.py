#!/usr/bin/env python3
"""Tests for the wizard's Pydantic config models (webapp/models.py).

Task 6 of the console-provisioning sub-project removed the VM features
(kvm-vfio, gpu-passthrough, retro) from the wizard's vocabulary: they are the
`console` package now, and console passes through the same door a third
party would - its own manifest and wizard.yaml, resolved by the engine, not
InstallConfig. This file used to pin the "retro requires kvm-vfio" submit-
time refusal; that constraint moved with retro, into the package's resolve
hook (see console/hooks/resolve.py), so it is gone from here too.

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


# --- les features VM ont quitte le wizard -------------------------------- #
for parti in ("kvm-vfio", "gpu-passthrough", "retro"):
    check(f"{parti} n est plus une feature",
          parti in models.KNOWN_FEATURES, False)
    check_raises(
        f"{parti} est refuse comme feature inconnue",
        lambda p=parti: models.InstallConfig(**base_kwargs(["os-base", p])),
    )

# retro se coche desormais comme reponse du package console
cfg_console = models.InstallConfig(
    **base_kwargs(["os-base", "networking"]),
    packages={"console": {"retro": True, "dedicated_nvme": "/dev/nvme1n1"}})
check("retro voyage dans les reponses du package",
      cfg_console.packages["console"]["retro"], True)

# --- Requirement: an install with no VM features checked must behave
# exactly as it did before "retro" existed. A plain feature list (docker
# checked, nothing VM-related) must validate exactly like before - no new
# error, no feature silently added.
cfg_no_vm = models.InstallConfig(**base_kwargs(["docker"]))
check("features are unaffected without any VM feature",
      "retro" not in cfg_no_vm.features and "kvm-vfio" not in cfg_no_vm.features,
      True)
check("os-base is still auto-added",
      "os-base" in cfg_no_vm.features, True)

# An empty feature list (the wizard's own default) must still validate.
cfg_default = models.InstallConfig(**base_kwargs([]))
check("the default feature list carries no VM feature",
      not ({"kvm-vfio", "gpu-passthrough", "retro"} & set(cfg_default.features)),
      True)

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
