#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - planning and applying packages.

plan_packages() is the whole "decide before you write" contract in one call:
it discovers, filters on capabilities, validates answers, detects conflicts and
resolves - and every one of those can refuse, before partition() has run. What
it returns is the kernel command line the bootloader step will write.


The apply side lives in test_install_engine_packages_apply.py, the facts
channel in _facts.py, the failure modes in _failures.py; their shared
harness is engine_packages_harness.py.

Run: python3 scripts/tests/test_install_engine_packages.py
"""
import tempfile
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from engine_packages_harness import (  # noqa: E402
    HW, FakeEmit, check, check_raises, config, report, steps_packages)

# No packages selected: nothing planned, no cmdline.
plan, cmdline = steps_packages.plan_packages(
    {"disk": {"path": "/dev/nvme0n1"}, "features": [], "packages": {}},
    HW, FakeEmit())
check("nothing selected plans nothing", plan, [])
check("nothing selected contributes no cmdline", cmdline, ())

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

# --- le disque d installation ne peut pas etre reclame par un package ------ #
# Un `disque` est reclame EXCLUSIVEMENT : le package console le donne entier
# a un invite, dont l installeur l efface. Le designer aussi comme cible
# d installation detruit l installation en cours de fabrication.
#
# Seul le MOTEUR peut voir la collision : un hook ne recoit que `hw` et ses
# propres reponses, jamais la config d installation, donc il ignore par
# construction quel est le disque cible. Le package ne peut pas se defendre
# tout seul - c est pour cela que le garde vit ici, au moment de planifier,
# avant que partition() n ait touche quoi que ce soit.
check_raises("le disque d installation ne peut pas etre le disque dedie d un "
             "package",
             lambda: steps_packages.plan_packages(
                 {**config,
                  "packages": {"demo": {"dedicated_disk": "/dev/nvme0n1"}}},
                 HW, FakeEmit()),
             "/dev/nvme0n1")

check_raises("et le refus nomme la question fautive",
             lambda: steps_packages.plan_packages(
                 {**config,
                  "packages": {"demo": {"dedicated_disk": "/dev/nvme0n1"}}},
                 HW, FakeEmit()),
             "dedicated_disk")

# Un autre disque passe : le garde refuse la collision, pas la question.
plan_autre, _ = steps_packages.plan_packages(
    {**config, "packages": {"demo": {"dedicated_disk": "/dev/nvme1n1"}}},
    HW, FakeEmit())
check("un disque dedie distinct de la cible est accepte",
      plan_autre[0][1]["dedicated_disk"], "/dev/nvme1n1")

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
            "two or more packages declare the name")
    finally:
        steps_packages.discover = real_discover

# --- kernel parameters are validated before anything is written ------------- #
# The allowlist guarding /etc/default/grub lives in bootloader.py, which runs
# at step 7 - after partition() has wiped the disk. A manifest resolving to a
# parameter carrying shell meaning must therefore be refused HERE, in
# plan_packages, or the operator meets "Unexpected error" on an already
# destroyed target.
check_raises("a shell-injecting kernel parameter is refused while planning",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"injecteur": {}}}, HW, FakeEmit()),
             "vfio-pci.ids=$(rm -rf /)")

check_raises("and the refusal says it is a kernel parameter problem",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"injecteur": {}}}, HW, FakeEmit()),
             "paramètre noyau refusé")

report("OK - all package planning tests passed")
