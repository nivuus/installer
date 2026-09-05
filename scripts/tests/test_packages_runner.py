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


def _resolve_only_pkg(tmp, name, resolve_body):
    """A minimal userspace package with only a resolve hook, for protocol tests."""
    pkg = pathlib.Path(tmp) / name
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "nivuus-package.yaml").write_text(
        f"apiVersion: {manifest_mod.API_VERSION}\nname: {name}\nversion: 1.0.0\n"
        f'label: "{name}"\ntier: userspace\nhooks:\n  resolve: hooks/resolve.py\n')
    (pkg / "hooks" / "resolve.py").write_text(resolve_body)
    return load_manifest(str(pkg / "nivuus-package.yaml"))


RESOLVE_BOOTSTRAP = "import json, sys\njson.load(sys.stdin)\n"

# --- a 'platform' event that lies about its own field types must be refused,
# not coerced: a bare string iterates into single-character fragments, and
# those fragments would otherwise reach the kernel command line. ------------ #
with tempfile.TemporaryDirectory() as tmp:
    bad_cmdline = _resolve_only_pkg(
        tmp, "bad-cmdline",
        RESOLVE_BOOTSTRAP +
        "print(json.dumps({'event': 'platform', "
        "'kernel-cmdline': 'intel_iommu=on', 'modules': [], "
        "'hugepages-mib': 0}))\n")
    try:
        run_resolve(bad_cmdline, HW, {})
        failures.append("a string kernel-cmdline did not raise HookError")
    except HookError as exc:
        check("the error names the offending field 'kernel-cmdline'",
              "kernel-cmdline" in str(exc), True)
        check("the error names the package", "bad-cmdline" in str(exc), True)

with tempfile.TemporaryDirectory() as tmp:
    bad_modules = _resolve_only_pkg(
        tmp, "bad-modules",
        RESOLVE_BOOTSTRAP +
        "print(json.dumps({'event': 'platform', "
        "'kernel-cmdline': [], 'modules': 'vfio_pci', "
        "'hugepages-mib': 0}))\n")
    try:
        run_resolve(bad_modules, HW, {})
        failures.append("a string modules did not raise HookError")
    except HookError as exc:
        check("the error names the offending field 'modules'",
              "modules" in str(exc), True)

# --- hugepages-mib from a hook must obey the same rule manifest.py already
# applies to the static declaration. ----------------------------------------- #
with tempfile.TemporaryDirectory() as tmp:
    bad_neg = _resolve_only_pkg(
        tmp, "bad-neg-hugepages",
        RESOLVE_BOOTSTRAP +
        "print(json.dumps({'event': 'platform', "
        "'kernel-cmdline': [], 'modules': [], "
        "'hugepages-mib': -5}))\n")
    try:
        run_resolve(bad_neg, HW, {})
        failures.append("a negative hugepages-mib did not raise HookError")
    except HookError as exc:
        check("the error names hugepages-mib for the negative case",
              "hugepages-mib" in str(exc), True)

with tempfile.TemporaryDirectory() as tmp:
    bad_bool = _resolve_only_pkg(
        tmp, "bad-bool-hugepages",
        RESOLVE_BOOTSTRAP +
        "print(json.dumps({'event': 'platform', "
        "'kernel-cmdline': [], 'modules': [], "
        "'hugepages-mib': True}))\n")
    try:
        run_resolve(bad_bool, HW, {})
        failures.append("a boolean hugepages-mib did not raise HookError")
    except HookError as exc:
        check("the error names hugepages-mib for the boolean case",
              "hugepages-mib" in str(exc), True)

# --- MAILLON 1/3 du canal resolve -> activate : run_resolve doit RAPPORTER
# les faits qu'un hook emet. Le maillon 2 (persistance dans
# etc/nivuus/packages.json) et le maillon 3 (fusion dans le hw de activate)
# sont eprouves dans test_install_engine_packages.py. --------------------- #
mesureur = load_manifest(str(FIXTURES / "mesureur" / "nivuus-package.yaml"))
res_facts = run_resolve(mesureur, HW, {})
check("MAILLON 1 (runner) : run_resolve rapporte le fait emis par resolve — "
      "sans lui rien ne quitte la phase resolve",
      res_facts.facts.get("pre_reboot_measure"), 4242)
check("MAILLON 1 (runner) : tous les faits emis sont rapportes, pas le premier",
      res_facts.facts.get("total_cpus"), 999)
check("un package userspace a droit aux faits : ils n'atteignent aucune "
      "chaine d'amorcage, contrairement au bloc platform",
      mesureur.tier, "userspace")

# Un package qui n'emet aucun fait continue de fonctionner a l'identique : la
# collection de faits ne doit rien changer pour lui, et .facts doit etre un
# dict vide - jamais None, que les appelants auraient a tester.
check("un package sans fait resout toujours", res.ok, True)
check("et ses faits sont un dict vide, pas None", res.facts, {})
check("un package sans hook resolve du tout a lui aussi des faits vides",
      res_static.facts, {})
check("un refus ne rapporte aucun fait : il n'y aura pas d'activate",
      refused.facts, {})

# Un evenement 'facts' malforme est un hook casse : refuser en nommant le
# package, jamais avaler silencieusement - une cle de fait devient une cle du
# hw que activate lit.
with tempfile.TemporaryDirectory() as tmp:
    bad_facts = _resolve_only_pkg(
        tmp, "bad-facts",
        RESOLVE_BOOTSTRAP +
        "print(json.dumps({'event': 'facts', 'facts': 'taille=12'}))\n")
    try:
        run_resolve(bad_facts, HW, {})
        failures.append("a non-mapping 'facts' payload did not raise HookError")
    except HookError as exc:
        check("l'erreur nomme le package", "bad-facts" in str(exc), True)
        check("l'erreur nomme l'evenement fautif", "facts" in str(exc), True)

with tempfile.TemporaryDirectory() as tmp:
    no_payload = _resolve_only_pkg(
        tmp, "empty-facts",
        RESOLVE_BOOTSTRAP + "print(json.dumps({'event': 'facts'}))\n")
    try:
        run_resolve(no_payload, HW, {})
        failures.append("a 'facts' event with no payload did not raise HookError")
    except HookError as exc:
        check("l'erreur nomme le package pour un evenement sans charge utile",
              "empty-facts" in str(exc), True)

# --- output cap: an accidental print loop must not OOM the installer. Round
# 1 capped the *parsed events*, but capture_output=True had already read the
# whole subprocess stdout into memory before that cap ever ran - measured at
# ~612 MB RSS growth for a 150 MB hook. runner.py now streams stdout line by
# line via subprocess.Popen instead, so the cap actually bounds what is ever
# held in memory. MAX_HOOK_OUTPUT_BYTES is patched small here so the test
# stays fast without needing to actually print past the real 1 MiB default. #
import packages.runner as runner_mod  # noqa: E402

_ORIGINAL_MAX_HOOK_OUTPUT_BYTES = runner_mod.MAX_HOOK_OUTPUT_BYTES


def _chatty_hook_body(exit_code=None):
    body = (
        "import json\n"
        "for _ in range(5000):\n"
        "    print(json.dumps({'event': 'progress', 'pct': 1, 'msg': 'x' * 20}))\n"
    )
    if exit_code is None:
        body += "print(json.dumps({'event': 'done'}))\n"
    else:
        body += f"import sys\nsys.exit({exit_code})\n"
    return body


def _write_chatty_pkg(tmp, name, exit_code=None):
    pkg = pathlib.Path(tmp) / name
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "nivuus-package.yaml").write_text(
        f"apiVersion: {manifest_mod.API_VERSION}\nname: {name}\nversion: 1.0.0\n"
        f'label: "{name}"\ntier: userspace\nhooks:\n  install: hooks/install.py\n')
    (pkg / "hooks" / "install.py").write_text(_chatty_hook_body(exit_code))
    return load_manifest(str(pkg / "nivuus-package.yaml"))


runner_mod.MAX_HOOK_OUTPUT_BYTES = 1024  # 5000 lines * ~30 bytes >> 100 KB total
try:
    with tempfile.TemporaryDirectory() as tmp:
        chatty = _write_chatty_pkg(tmp, "chatty")
        chatty_emit = FakeEmit()
        run_install(chatty, HW, {}, tmp, chatty_emit)  # must not raise
        warn_lines = [line for line in chatty_emit.lines if line[0] == "warn"]
        check("a cap-exceeded warning names the package",
              any("chatty" in line[3] for line in warn_lines), True)
        check("the cap-exceeded warning fires exactly once", len(warn_lines), 1)
        info_count = sum(1 for line in chatty_emit.lines if line[0] == "info")
        check("retained progress events are bounded by the cap, not by the "
              "5000 the hook actually printed", info_count < 100, True)

    # The pipe must still be fully drained past the cap: a hook that prints
    # far more than MAX_HOOK_OUTPUT_BYTES and then exits non-zero must still
    # raise HookError with its real exit code - proving the read loop was
    # never blocked waiting on a full pipe the parent stopped reading.
    with tempfile.TemporaryDirectory() as tmp:
        chatty_fail = _write_chatty_pkg(tmp, "chattyfail", exit_code=9)
        try:
            run_install(chatty_fail, HW, {}, tmp)
            failures.append(
                "a hook exceeding the cap and exiting non-zero did not raise")
        except HookError as exc:
            check("the drained-past-cap failure names the package",
                  "chattyfail" in str(exc), True)
            check("the drained-past-cap failure carries the exit code",
                  "9" in str(exc), True)
finally:
    runner_mod.MAX_HOOK_OUTPUT_BYTES = _ORIGINAL_MAX_HOOK_OUTPUT_BYTES

# --- the timeout must still genuinely fire under the streaming rewrite ----- #
_ORIGINAL_RESOLVE_TIMEOUT = runner_mod.HOOK_TIMEOUT["resolve"]
runner_mod.HOOK_TIMEOUT["resolve"] = 1
try:
    with tempfile.TemporaryDirectory() as tmp:
        sleepy = _resolve_only_pkg(
            tmp, "sleepy", "import json, sys, time\n"
            "json.load(sys.stdin)\ntime.sleep(5)\n")
        try:
            run_resolve(sleepy, HW, {})
            failures.append("a hook exceeding its timeout did not raise HookError")
        except HookError as exc:
            check("the timeout error names the package", "sleepy" in str(exc), True)
            check("the timeout error says it was killed",
                  "killed" in str(exc), True)
finally:
    runner_mod.HOOK_TIMEOUT["resolve"] = _ORIGINAL_RESOLVE_TIMEOUT

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all hook runner tests passed")
