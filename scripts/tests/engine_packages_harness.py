"""Shared ground for the install-engine package-step suites.

test_install_engine_packages.py (planning), _apply.py (what reaches the
target), _facts.py (the resolve -> activate channel) and _failures.py (what
must stop the install) all drive steps/packages.py against the fixture
packages under scripts/tests/fixtures/packages, with chroot_run faked.
"""
import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"
os.environ["NIVUUS_PACKAGES_DIR"] = str(FIXTURES)

from steps import packages as steps_packages  # noqa: E402,F401
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

config = {
    "disk": {"path": "/dev/nvme0n1"},
    "features": ["os-base"],
    "packages": {"demo": {"greeting": "salut"}},
}

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
    shutil.copyfile(REPO / "installer/packages/nivuus_cli.py",
                    payload / "installer/packages/nivuus_cli.py")
    for unit in ("nivuus-check.service", "nivuus-check.timer"):
        shutil.copyfile(REPO / "configs/systemd" / unit,
                        payload / "configs/systemd" / unit)
    return "/opt/nivuus"


def report(ok_line):
    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print(ok_line)
