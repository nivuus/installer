#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - what apply_packages() leaves.

apply_packages() then writes: modules, hugepages, apt, the install hook, and
the activation unit that carries the package into first boot.

The apply_packages assertions below deliberately look at ARTEFACTS UNDER THE
TARGET, not at calls. chroot_run is faked here, so asserting that a
`systemctl enable` command was issued proves only that this test's own fake
was called - it cannot tell an installed system from an empty directory, and
that is exactly how a branch shipped in which the activate phase could not
run on any installed machine. What has to exist on the target is checked as
files: the unit, the enablement symlink, the package directory, the CLI the
unit's ExecStart names.

Run: python3 scripts/tests/test_install_engine_packages_apply.py
"""
import json
import os
import stat
import tempfile
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from engine_packages_harness import (  # noqa: E402
    HW, FakeEmit, calls, check, config, fake_chroot_run, report,
    seed_payload, steps_packages)

with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = steps_packages.apt.chroot_run = fake_chroot_run
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    steps_packages.apply_packages(plan, tmp, nivuus_dir, HW, FakeEmit())

    modules = (target / "etc/modules").read_text()
    check("static module written", "vfio_pci" in modules, True)
    check("resolved module written", "vfio_iommu_type1" in modules, True)

    sysctl = (target / "etc/sysctl.d/60-nivuus-packages.conf").read_text()
    check("hugepages are converted from MiB to 2 MiB pages",
          "vm.nr_hugepages = 512" in sysctl, True)

    check("apt was asked for the declared packages",
          any("cowsay" in c for c in calls), True)
    check("apt was asked for the YAML parser the activate phase needs",
          any("python3-yaml" in c for c in calls), True)
    # python3 itself must be named explicitly, not merely pulled in as a
    # transitive dependency of python3-yaml - relying on a transitive pull
    # for the interpreter the unit's own ExecStart needs is the bug this
    # fix closes.
    check("apt was asked for python3 explicitly",
          any("python3" in c for c in calls), True)

    # The activate phase's own hard requirements must be a SEPARATE apt-get
    # invocation from the packages' declared (lenient) apt - never merged
    # into one call, or the two failure policies collapse into one.
    activate_calls = [c for c in calls
                       if "apt-get" in c and "install" in c and "python3" in c]
    package_apt_calls = [c for c in calls
                          if "apt-get" in c and "install" in c and "cowsay" in c]
    check("the activate requirements are installed in their own apt-get call",
          len(activate_calls), 1)
    check("that call does not also carry the packages' own apt",
          any("cowsay" in c for c in activate_calls), False)
    check("the packages' own apt is installed in a separate apt-get call",
          len(package_apt_calls), 1)
    check("that call does not also carry the activate requirements",
          any("python3-yaml" in c for c in package_apt_calls), False)

    # --- the activate phase must be able to run on the installed system ----- #
    unit = target / "etc/systemd/system/nivuus-package-activate@.service"
    check("the activation unit reaches the target", unit.is_file(), True)
    unit_text = unit.read_text()
    check("its ExecStart points at the CLI where the payload actually puts it",
          "ExecStart=/opt/nivuus/installer/packages/activate_cli.py %i"
          in unit_text, True)

    cli = target / "opt/nivuus/installer/packages/activate_cli.py"
    check("the CLI that ExecStart names exists on the target", cli.is_file(),
          True)
    check("and it is executable", os.access(cli, os.X_OK), True)

    # --- the updater's way in reaches the target too ------------------------ #
    nivuus = target / "usr/local/sbin/nivuus"
    check("the nivuus command is a symlink into the payload",
          (nivuus.is_symlink(), os.readlink(nivuus)),
          (True, "/opt/nivuus/installer/packages/nivuus_cli.py"))
    check("the CLI it names is executable on the target",
          os.access(target / "opt/nivuus/installer/packages/nivuus_cli.py",
                    os.X_OK), True)
    check("the daily check unit reaches the target",
          "ExecStart=/usr/local/sbin/nivuus check" in
          (target / "etc/systemd/system/nivuus-check.service").read_text(), True)
    timer_link = target / "etc/systemd/system/timers.target.wants/nivuus-check.timer"
    check("and its timer is enabled",
          (timer_link.is_symlink(), os.readlink(timer_link)),
          (True, "/etc/systemd/system/nivuus-check.timer"))

    pkg_dir = target / "opt/nivuus-packages/demo"
    check("the selected package travels to the target", pkg_dir.is_dir(), True)
    check("with its manifest, which is what discover() looks for",
          (pkg_dir / "nivuus-package.yaml").is_file(), True)
    check("and its hooks, which activate would run",
          (pkg_dir / "hooks" / "resolve.py").is_file(), True)
    check("a package that was NOT selected is not copied",
          (target / "opt/nivuus-packages/refuser").exists(), False)

    link = (target / "etc/systemd/system/multi-user.target.wants"
            / "nivuus-package-activate@demo.service")
    check("the activation is armed for first boot", link.is_symlink(), True)
    check("and the symlink points at the template unit",
          os.readlink(link),
          "/etc/systemd/system/nivuus-package-activate@.service")

    marker = target / "etc" / "nivuus-demo.json"
    check("the install hook ran under the target root", marker.is_file(), True)
    check("it received its answers",
          json.loads(marker.read_text())["answers"]["greeting"], "salut")

    state = json.loads((target / "etc/nivuus/packages.json").read_text())
    check("the selection is recorded on the target",
          state["demo"]["answers"]["greeting"], "salut")
    check("the recorded version matches the manifest",
          state["demo"]["version"], "1.0.0")

    # THE STATE FILE HOLDS SECRETS. It records each package's answers verbatim
    # so the activate phase can replay them after the reboot - and a `secret`
    # question (the console's Windows administrator password, and its product
    # key later) lands there in cleartext. 0644 would publish it to every local
    # account. activate_cli.py runs as root from a systemd oneshot, so 0600
    # costs nothing and is the only mode that can be right here.
    mode = stat.S_IMODE((target / "etc/nivuus/packages.json").stat().st_mode)
    check("the state file is not world-readable", oct(mode), oct(0o600))

    # A package emitting no fact must produce the state file it always did:
    # no `facts` key at all, not an empty one. The channel added below costs
    # nothing to a package that does not use it.
    check("un package qui n'emet aucun fait n'ajoute rien a l'etat",
          "facts" in state["demo"], False)

report("OK - all package apply tests passed")
