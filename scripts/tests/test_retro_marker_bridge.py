#!/usr/bin/env python3
"""Tests that the console package's install hook and windows-guest/build.py
agree on WHERE the retrogaming toggle marker lives on disk.

Before task 5, install-engine/steps/features.py wrote this marker through
common.retro.retro_state_path() - the very function build.py's reader used,
so a single shared Python object WAS the guarantee. Task 5 moved retro
entirely into the console package's install hook, and console/ must not
import anything from installer/ (that self-containment is what lets the
package run standalone, later, on an already-installed target that has
never seen this repo's install-engine). So the writer
(console/hooks/install.py) now carries the marker's relative path as a
literal ("etc/nivuus/retro.json"), while common/retro.py still carries it
as a constant for build.py's benefit. The two agree today only because
whoever typed them was careful - exactly the divergence this test exists
to catch.

The guard therefore changes shape: it can no longer compare two Python
objects, since only one importer of retro_state_path is left. Instead it
RUNS the real hook - as a subprocess, the way the engine actually invokes
package hooks - against a temporary root, and asserts the artefact it
leaves lands exactly at common.retro.retro_state_path(root): the path
build.py will read on the live host later. That is a STRONGER guarantee
than the old identity check: it verifies the artefact, not the import, and
it keeps holding even if the two sides come to share no symbol at all.

Run: python3 scripts/tests/test_retro_marker_bridge.py
"""
import ast
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
HOOK = CONSOLE / "hooks" / "install.py"

sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
sys.path.insert(0, str(REPO / "installer"))

import build  # noqa: E402
from common import retro as common_retro  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- build.py's default marker still resolves through common/retro.py --- #

check("build.py's default marker equals the shared path, rooted at /",
      build.DEFAULT_RETRO_MARKER, common_retro.retro_state_path())

CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": str(CONSOLE)},
    "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
    "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": True,
                "admin_password": "hunter2hunter2"},
})


def run_hook(ctx_json: str, root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=ctx_json, capture_output=True, text=True, cwd=str(CONSOLE))


# --- The actual round trip: run the REAL hook, read with the REAL reader - #
# If either side's path literal/constant were renamed independently, the
# marker would not be where the other side looks - exactly the bug this
# bridge exists to catch.

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    proc = run_hook(CTX, root)
    check("le hook install sort 0 (retro coche)", proc.returncode, 0)

    expected = pathlib.Path(common_retro.retro_state_path(str(root)))
    check("le temoin atterrit exactement la ou common.retro l'attend",
          expected.is_file(), True)
    check("build.py lit le temoin depose par le hook (enabled)",
          build.read_retro_marker(str(expected)), True)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    ctx["answers"]["retro"] = False
    proc = run_hook(json.dumps(ctx), root)
    check("le hook install sort 0 (retro decoche)", proc.returncode, 0)

    expected = pathlib.Path(common_retro.retro_state_path(str(root)))
    check("le temoin atterrit exactement la ou common.retro l'attend, meme decoche",
          expected.is_file(), True)
    check("build.py lit le temoin depose par le hook (disabled)",
          build.read_retro_marker(str(expected)), False)

# --- main() must feed the resolved CLI value, not a hardcoded literal --- #
# Static, but exact: ast.parse means a same-looking comment, or a variable
# merely NAMED args.retro elsewhere, can't satisfy this - it inspects the
# real argument node of the real call inside main()'s own function body.
_build_source = (REPO / "installer" / "windows-guest" / "build.py").read_text()
_tree = ast.parse(_build_source)
_main_fn = next((n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
check("build.py still defines main()", _main_fn is not None, True)

_call = None
if _main_fn is not None:
    _call = next((n for n in ast.walk(_main_fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "build_retro_psd1"), None)
check("main() calls build_retro_psd1", _call is not None, True)

if _call is not None:
    _arg = _call.args[0] if _call.args else None
    _is_args_retro = (
        isinstance(_arg, ast.Attribute) and _arg.attr == "retro"
        and isinstance(_arg.value, ast.Name) and _arg.value.id == "args"
    )
    check("main() passes args.retro (not a hardcoded literal) to build_retro_psd1",
          _is_args_retro, True)

# --- The write itself must be UNCONDITIONAL ------------------------------ #
# Everything above proves the two sides agree on WHERE and on WHAT value
# main() feeds in - none of it proves main() always writes the file. Putting
# that write under a condition ("if retro_enabled:", say) would leave every
# check above green: build_retro_psd1 is still called with args.retro, the
# round trip above still passes because it never reaches main() at all. An
# absent retro.psd1 must stay indistinguishable from "a payload built before
# this option existed" ONLY, never reachable by unchecking the box - that
# distinction is the entire point of always rendering an explicit
# Enabled = $false. payload.verify_staged() is not the guard here either: it
# runs later, for a different reason (catching a build that forgot the file
# entirely), not specifically a write skipped because retro was off.
class _RetroWriteFinder(ast.NodeVisitor):
    """Walks main()'s body for (config / "retro.psd1").write_text(...) and
    tracks whether any ast.If wraps it - by the real syntax tree, so neither
    a comment claiming the write is unconditional nor an unrelated variable
    that happens to be named `config` can satisfy this."""

    def __init__(self):
        self.if_depth = 0
        self.found = False
        self.found_under_if = False

    def visit_If(self, node):
        self.if_depth += 1
        self.generic_visit(node)
        self.if_depth -= 1

    def visit_Call(self, node):
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "write_text"
                and isinstance(func.value, ast.BinOp)
                and isinstance(func.value.op, ast.Div)
                and isinstance(func.value.right, ast.Constant)
                and func.value.right.value == "retro.psd1"):
            self.found = True
            if self.if_depth > 0:
                self.found_under_if = True
        self.generic_visit(node)


if _main_fn is not None:
    _finder = _RetroWriteFinder()
    _finder.visit(_main_fn)
    check("main() writes config/retro.psd1 at all", _finder.found, True)
    check("... and does so UNCONDITIONALLY, retro on or off",
          _finder.found_under_if, False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - the retro marker path is a single source of truth, "
      "and main() feeds it correctly")
