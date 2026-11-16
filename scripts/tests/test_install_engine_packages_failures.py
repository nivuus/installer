#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - what must stop the install.

Required apt, a missing activation unit or CLI, a failing install hook: each
raises StepError, and a partial apply still leaves a state file describing
what is on the target.

Run: python3 scripts/tests/test_install_engine_packages_failures.py
"""
import dataclasses
import json
import shutil
import tempfile
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from engine_packages_harness import (  # noqa: E402
    HW, REPO, FakeEmit, calls, check, check_raises, config, failures,
    fake_chroot_run, make_failing_chroot_run, report, seed_payload,
    steps_packages)

# --- the activate phase's own apt requirements are fatal, unlike the ------- #
# --- packages' own declared apt --------------------------------------------- #
# If installing python3/python3-yaml fails, the activation unit armed for
# first boot has no interpreter to run it - the install must stop here
# rather than report success over a broken chain. This is the residual
# finding this fix closes.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = make_failing_chroot_run("python3")
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    check_raises(
        "a failing activate-requirements install raises StepError and "
        "names what failed",
        lambda: steps_packages.apply_packages(plan, tmp, nivuus_dir, HW,
                                              FakeEmit()),
        "python3-yaml")
    check("the failed apt-get call did name python3 explicitly",
          any("python3" in c for c in calls[-1:]), True)

# A package's declared apt is what its hooks need: a failure is fatal, and it
# is attributed to that package alone. One apt-get call per package, because
# apt refuses a whole transaction over one unknown name - merged, a single
# package's bad name took every other package's dependencies down with it.
with tempfile.TemporaryDirectory() as tmp:
    calls.clear()

    def unknown_cowsay(target, cmd, **kwargs):
        """apt as it really fails: exit 100 and the culprit on its output."""
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        if "install" in cmd and "cowsay" in cmd:
            R.returncode = 100
            R.stderr = "E: Unable to locate package cowsay"
        return R()

    steps_packages.chroot_run = steps_packages.apt.chroot_run = unknown_cowsay
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    demo, answers, resolution = plan[0]
    neighbour = dataclasses.replace(demo, name="voisin", label="Neighbour",
                                    apt=("figlet",))
    two = [(neighbour, answers, resolution), (demo, answers, resolution)]
    hooks_run = []
    real_run_install = steps_packages.run_install
    steps_packages.run_install = lambda m, *a, **k: hooks_run.append(m.name)
    try:
        try:
            steps_packages.apply_packages(two, tmp, nivuus_dir, HW, FakeEmit())
            failures.append("a package's failing apt did not fail the step")
            message = ""
        except steps_packages.StepError as exc:
            message = str(exc)
    finally:
        steps_packages.run_install = real_run_install
    check("the failure names the package whose apt failed",
          f"« {demo.label} »" in message, True)
    check("and carries apt's own diagnosis",
          "Unable to locate package cowsay" in message, True)
    check("the other package is not blamed", "Neighbour" in message, False)
    check("the other package's apt was still installed, in its own call",
          [c for c in calls if "install" in c and "figlet" in c],
          [["apt-get", "install", "-y", "figlet"]])
    check("no install hook runs against a system missing its dependencies",
          hooks_run, [])
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run

# A payload with no activation unit must fail the install, not report success:
# an install that cannot activate anything at first boot has not done what it
# said it did.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run
    target = pathlib.Path(tmp)
    (target / "opt/nivuus/installer/packages").mkdir(parents=True)
    shutil.copyfile(REPO / "installer/packages/activate_cli.py",
                    target / "opt/nivuus/installer/packages/activate_cli.py")
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    check_raises("a payload missing the activation unit fails the install",
                 lambda: steps_packages.apply_packages(
                     plan, tmp, "/opt/nivuus", HW, FakeEmit()),
                 "nivuus-package-activate@.service")

# Same for the CLI the unit's ExecStart names.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run
    target = pathlib.Path(tmp)
    (target / "opt/nivuus/configs/systemd").mkdir(parents=True)
    shutil.copyfile(REPO / "configs/systemd/nivuus-package-activate@.service",
                    target / "opt/nivuus/configs/systemd"
                    / "nivuus-package-activate@.service")
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    check_raises("a payload missing activate_cli.py fails the install",
                 lambda: steps_packages.apply_packages(
                     plan, tmp, "/opt/nivuus", HW, FakeEmit()),
                 "activate_cli.py")

# The state file must describe the residue of a PARTIAL apply: a package whose
# install hook succeeded, then one that raised, must still leave the first one
# recorded - it is on the target and armed for first boot either way.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())

    real_run_install = steps_packages.run_install

    def exploding_run_install(manifest, hw, answers, root, emit):
        real_run_install(manifest, hw, answers, root, emit)
        raise steps_packages.HookError("hook install en échec (simulé)")

    steps_packages.run_install = exploding_run_install
    try:
        check_raises("an install hook failure fails the step",
                     lambda: steps_packages.apply_packages(
                         plan, tmp, nivuus_dir, HW, FakeEmit()),
                     "simulé")
    finally:
        steps_packages.run_install = real_run_install

    check("a package whose hook raised is not recorded",
          (target / "etc/nivuus/packages.json").exists(), False)

    # And with the failure on the SECOND package, the first one is recorded.
    two = plan + [plan[0]]
    seen = []

    def second_explodes(manifest, hw, answers, root, emit):
        seen.append(manifest.name)
        if len(seen) > 1:
            raise steps_packages.HookError("second hook en échec (simulé)")
        real_run_install(manifest, hw, answers, root, emit)

    steps_packages.run_install = second_explodes
    try:
        check_raises("the second package's failure fails the step",
                     lambda: steps_packages.apply_packages(
                         plan + [plan[0]], tmp, nivuus_dir, HW, FakeEmit()),
                     "second hook")
    finally:
        steps_packages.run_install = real_run_install

    state = json.loads((target / "etc/nivuus/packages.json").read_text())
    check("the package applied before the failure IS recorded",
          "demo" in state, True)

report("OK - all package failure tests passed")
