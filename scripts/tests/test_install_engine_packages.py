#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - planning and applying packages.

plan_packages() is the whole "decide before you write" contract in one call:
it discovers, filters on capabilities, validates answers, detects conflicts and
resolves - and every one of those can refuse, before partition() has run. What
it returns is the kernel command line the bootloader step will write.

apply_packages() then writes: modules, hugepages, apt, the install hook, and
the activation unit that carries the package into first boot.

Run: python3 scripts/tests/test_install_engine_packages.py
"""
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"
os.environ["NIVUUS_PACKAGES_DIR"] = str(FIXTURES)

from steps import packages as steps_packages  # noqa: E402
from steps.util import StepError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, fn, needle):
    try:
        fn()
    except StepError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected StepError, none raised")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(("info", msg))

    def warn(self, step, pct, msg):
        self.lines.append(("warn", msg))

    def error(self, step, pct, msg):
        self.lines.append(("error", msg))


HW = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
              "ids": ["10de:2786", "10de:22bc"]}],
    "disks": [{"name": "nvme0n1", "path": "/dev/nvme0n1", "transport": "nvme"}],
    "cpu": {"hybrid": True},
}

# No packages selected: nothing planned, no cmdline.
plan, cmdline = steps_packages.plan_packages(
    {"disk": {"path": "/dev/nvme0n1"}, "features": [], "packages": {}},
    HW, FakeEmit())
check("nothing selected plans nothing", plan, [])
check("nothing selected contributes no cmdline", cmdline, ())

config = {
    "disk": {"path": "/dev/nvme0n1"},
    "features": ["os-base"],
    "packages": {"demo": {"greeting": "salut"}},
}
plan, cmdline = steps_packages.plan_packages(config, HW, FakeEmit())
check("the selected package is planned", [m.name for m, _, _ in plan], ["demo"])
check("static cmdline collected", "intel_iommu=on" in cmdline, True)
check("resolved cmdline collected",
      "vfio-pci.ids=10de:2786,10de:22bc" in cmdline, True)
check("the validated answers travel with the plan",
      plan[0][1]["greeting"], "salut")

# An unknown package name must fail loudly, not be skipped.
check_raises("an unknown package is refused",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"fantome": {}}}, HW, FakeEmit()),
             "fantome")

# demo requires the iommu capability: without it, it must be refused.
no_iommu = {**HW, "iommu": {"supported": False, "vendor": "", "active": False}}
check_raises("a package whose capability is missing is refused",
             lambda: steps_packages.plan_packages(config, no_iommu, FakeEmit()),
             "iommu")

# A refusing package stops the plan with its own sentence.
check_raises("a package refusal stops the plan",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"refuser": {}}}, HW, FakeEmit()),
             "NVMe")

# --- config['packages'] shape validation ------------------------------------ #
# A plausible authoring slip in a hand-written config.json (the engine is
# documented as runnable standalone against a loopback disk - not every
# caller went through the portal's Pydantic model) must surface as a
# StepError naming the field, never as a raw Python TypeError from deep
# inside a dict lookup.
check_raises("packages as a list is refused by shape, not TypeError",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": ["demo"]}, HW, FakeEmit()),
             "packages")

check_raises("packages as a bare string is refused the same way",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": "demo"}, HW, FakeEmit()),
             "packages")

check_raises("a package's answers must themselves be a mapping",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"demo": ["not", "a", "mapping"]}},
                 HW, FakeEmit()),
             "demo")

# --- name-collision cross-referencing ---------------------------------------- #
# When discover() excludes every manifest sharing a colliding name, a
# selected-but-missing package must not be reported as merely "introuvable" -
# that reads as "you asked for something that isn't here" when the truth is
# "it's here twice and both were refused". The collision detail discover()
# put in the warn stream must also reach the StepError itself.
with tempfile.TemporaryDirectory() as collision_root:
    for variant, label in (("pkg-a", "Doublon A"), ("pkg-b", "Doublon B")):
        pkg_dir = pathlib.Path(collision_root) / variant
        pkg_dir.mkdir()
        (pkg_dir / "nivuus-package.yaml").write_text(
            "apiVersion: nivuus.dev/v1\n"
            "name: dupe\n"
            "version: 1.0.0\n"
            f"label: \"{label}\"\n"
            "tier: userspace\n")

    from packages.discovery import discover as _real_discover  # noqa: E402

    real_discover = steps_packages.discover
    steps_packages.discover = lambda: _real_discover(root=collision_root)
    try:
        check_raises(
            "a selected package excluded by a name collision names the "
            "collision, not just 'introuvable'",
            lambda: steps_packages.plan_packages(
                {**config, "packages": {"dupe": {}}}, HW, FakeEmit()),
            "deux packages ou plus déclarent le nom")
    finally:
        steps_packages.discover = real_discover

# --- apply_packages -------------------------------------------------------- #
calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)
    class R:
        returncode = 0
    return R()


with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = fake_chroot_run
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    steps_packages.apply_packages(plan, tmp, HW, FakeEmit())
    target = pathlib.Path(tmp)

    modules = (target / "etc/modules").read_text()
    check("static module written", "vfio_pci" in modules, True)
    check("resolved module written", "vfio_iommu_type1" in modules, True)

    sysctl = (target / "etc/sysctl.d/60-nivuus-packages.conf").read_text()
    check("hugepages are converted from MiB to 2 MiB pages",
          "vm.nr_hugepages = 512" in sysctl, True)

    check("apt was asked for the declared packages",
          any("cowsay" in c for c in calls), True)
    check("the activation unit was enabled",
          any("nivuus-package-activate@demo.service" in " ".join(c)
              for c in calls), True)

    marker = target / "etc" / "nivuus-demo.json"
    check("the install hook ran under the target root", marker.is_file(), True)
    check("it received its answers",
          json.loads(marker.read_text())["answers"]["greeting"], "salut")

    state = json.loads((target / "etc/nivuus/packages.json").read_text())
    check("the selection is recorded on the target",
          state["demo"]["answers"]["greeting"], "salut")
    check("the recorded version matches the manifest",
          state["demo"]["version"], "1.0.0")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all package step tests passed")
