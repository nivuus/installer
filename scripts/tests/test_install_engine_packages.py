#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - planning and applying packages.

plan_packages() is the whole "decide before you write" contract in one call:
it discovers, filters on capabilities, validates answers, detects conflicts and
resolves - and every one of those can refuse, before partition() has run. What
it returns is the kernel command line the bootloader step will write.

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

Run: python3 scripts/tests/test_install_engine_packages.py
"""
import json
import os
import pathlib
import shutil
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

# --- apply_packages -------------------------------------------------------- #
calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)
    class R:
        returncode = 0
    return R()


def make_failing_chroot_run(needle):
    """A fake chroot_run whose call containing `needle` reports failure.

    Mirrors real chroot_run's contract (a CompletedProcess-like object with
    a returncode) without raising itself - apply_packages is the one that
    must turn a non-zero returncode into a StepError, exactly as it has to
    do against the real subprocess wrapper.
    """
    def fake(target, cmd, **kwargs):
        calls.append(cmd)
        class R:
            returncode = 1 if any(needle in part for part in cmd) else 0
        return R()
    return fake


def seed_payload(target: pathlib.Path) -> str:
    """Reproduce what copy_payload() leaves at /opt/nivuus on the target.

    The real files, not stand-ins: the unit copied onto the target has to be
    the one in configs/systemd/, and its ExecStart has to name the CLI that
    actually exists in the payload.
    """
    payload = target / "opt" / "nivuus"
    (payload / "configs" / "systemd").mkdir(parents=True)
    shutil.copyfile(REPO / "configs/systemd/nivuus-package-activate@.service",
                    payload / "configs/systemd/nivuus-package-activate@.service")
    (payload / "installer" / "packages").mkdir(parents=True)
    shutil.copyfile(REPO / "installer/packages/activate_cli.py",
                    payload / "installer/packages/activate_cli.py")
    return "/opt/nivuus"


with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = fake_chroot_run
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

# --- the activate phase's own apt requirements are fatal, unlike the ------- #
# --- packages' own declared apt --------------------------------------------- #
# If installing python3/python3-yaml fails, the activation unit armed for
# first boot has no interpreter to run it - the install must stop here
# rather than report success over a broken chain. This is the residual
# finding this fix closes.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = make_failing_chroot_run("python3")
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

# A failing PACKAGES' apt call, by contrast, must remain a warning: a
# package may still be usable without an optional dependency, and this
# leniency is deliberate. Regression guard on that deliberate choice.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = make_failing_chroot_run("cowsay")
    target = pathlib.Path(tmp)
    nivuus_dir = seed_payload(target)
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    emit = FakeEmit()
    # Must not raise: apply_packages() completing normally IS the assertion.
    steps_packages.apply_packages(plan, tmp, nivuus_dir, HW, emit)
    warnings = [msg for level, msg in emit.lines if level == "warn"]
    check("a failing packages' apt call only warns, it does not raise",
          any("cowsay" in w for w in warnings), True)
    check("the activation unit still reached the target despite the "
          "packages' apt failure",
          (target / "etc/systemd/system"
           / "nivuus-package-activate@.service").is_file(), True)

# A payload with no activation unit must fail the install, not report success:
# an install that cannot activate anything at first boot has not done what it
# said it did.
with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = fake_chroot_run
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
    steps_packages.chroot_run = fake_chroot_run
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
    steps_packages.chroot_run = fake_chroot_run
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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all package step tests passed")
