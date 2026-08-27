#!/usr/bin/env python3
"""Tests for installer/packages/runner.py - executing third-party hooks.

`resolve` being read-only is what lets bootloader stay where it is in run.py:
the engine learns the exact kernel command line BEFORE partitioning, so it
never has to reorder the pipeline or rewrite GRUB after the fact.

A refusal is a first-class outcome, not an exception: "this machine has no
dedicated NVMe" must reach the operator as a sentence, before a single byte
is written to their disk.

Run: python3 scripts/tests/test_packages_runner.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"

from packages.manifest import load_manifest  # noqa: E402
from packages.runner import (  # noqa: E402
    HookError, run_activate, run_install, run_resolve,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(("info", step, pct, msg))

    def warn(self, step, pct, msg):
        self.lines.append(("warn", step, pct, msg))

    def error(self, step, pct, msg):
        self.lines.append(("error", step, pct, msg))


HW = {"gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
                "ids": ["10de:2786", "10de:22bc"]}]}

demo = load_manifest(str(FIXTURES / "demo" / "nivuus-package.yaml"))
emit = FakeEmit()
res = run_resolve(demo, HW, {"greeting": "salut"}, emit)

check("resolve succeeds", res.ok, True)
check("resolve has no reason when it succeeds", res.reason, "")
check("the static cmdline survives",
      "intel_iommu=on" in res.platform.kernel_cmdline, True)
check("the resolved cmdline is merged in",
      "vfio-pci.ids=10de:2786,10de:22bc" in res.platform.kernel_cmdline, True)
check("static and resolved modules are both present",
      set(res.platform.modules), {"vfio_pci", "vfio_iommu_type1"})
check("resolved hugepages win over the static 0",
      res.platform.hugepages_mib, 1024)
check("progress events reached the emitter",
      any("resolving" in line[3] for line in emit.lines), True)

# A refusal is data, not an exception.
refuser = load_manifest(str(FIXTURES / "refuser" / "nivuus-package.yaml"))
refused = run_resolve(refuser, HW, {})
check("a refusing package does not raise", refused.ok, False)
check("the refusal carries its reason",
      "NVMe" in refused.reason, True)

# A package with no resolve hook resolves to its static declaration.
import packages.manifest as manifest_mod  # noqa: E402
static_only = manifest_mod.parse_manifest({
    "apiVersion": manifest_mod.API_VERSION, "name": "static",
    "version": "1.0.0", "label": "Static", "tier": "platform",
    "platform": {"kernel-cmdline": ["quiet"]},
}, str(FIXTURES / "demo"))
res_static = run_resolve(static_only, HW, {})
check("no resolve hook still resolves", res_static.ok, True)
check("the static block is returned as is",
      res_static.platform.kernel_cmdline, ("quiet",))

# --- install: the hook must receive --root and write inside it ------------- #
with tempfile.TemporaryDirectory() as tmp:
    run_install(demo, HW, {"greeting": "salut"}, tmp)
    marker = pathlib.Path(tmp) / "etc" / "nivuus-demo.json"
    check("the install hook wrote under --root", marker.is_file(), True)
    import json
    written = json.loads(marker.read_text())
    check("the hook received the phase", written["phase"], "install")
    check("the hook received the answers", written["answers"]["greeting"], "salut")

# A package with no install hook is a no-op, not a failure.
with tempfile.TemporaryDirectory() as tmp:
    run_install(refuser, HW, {}, tmp)
    check("no install hook is a no-op",
          list(pathlib.Path(tmp).iterdir()), [])

# No activate hook anywhere here: it must also be a silent no-op.
run_activate(demo, HW, {})

# --- a hook that fails must raise, loudly ---------------------------------- #
with tempfile.TemporaryDirectory() as tmp:
    pkg = pathlib.Path(tmp) / "boom"
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "nivuus-package.yaml").write_text(
        f"apiVersion: {manifest_mod.API_VERSION}\nname: boom\nversion: 1.0.0\n"
        'label: "Boom"\ntier: userspace\nhooks:\n  install: hooks/install.py\n')
    (pkg / "hooks" / "install.py").write_text(
        "import sys\nsys.stderr.write('exploded\\n')\nsys.exit(3)\n")
    boom = load_manifest(str(pkg / "nivuus-package.yaml"))
    try:
        run_install(boom, HW, {}, tmp)
        failures.append("a failing hook did not raise HookError")
    except HookError as exc:
        check("the error names the package", "boom" in str(exc), True)
        check("the error carries the exit code", "3" in str(exc), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all hook runner tests passed")
