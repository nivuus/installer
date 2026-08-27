#!/usr/bin/env python3
"""Tests that install-engine/steps/features.py and windows-guest/build.py
agree on WHERE the retrogaming toggle marker lives on disk.

Both sides used to carry their own independent string literal for this
path (features.py's old RETRO_STATE_PATH, build.py's old
DEFAULT_RETRO_MARKER). Renaming either one, alone, silently reintroduced
exactly the bug task 3 exists to fix: the operator checks retro in the
wizard, and the guest build never sees it - with all fourteen other test
files in this repo still green, because none of them exercised the
connection itself.

installer/common/retro.py now defines the path once; both sides import it.
This file proves the two REAL functions still agree - features._retro()
(the writer, mid-install) and build.read_retro_marker() (the reader, on
the live host later) - by writing with one and reading with the other
against the same temporary root. A future edit that reintroduces a second,
independent literal in either module makes this round trip fail; it does
not rely on the two sides merely importing the same name today.

It also pins, with ast (not a string search a comment could satisfy), that
windows-guest/build.py's main() feeds the CLI-resolved value into
build_retro_psd1() rather than a value hardcoded at that call site - the
one path in this bridge no filesystem round trip can reach, because main()
needs a real Windows medium to run end-to-end.

Run: python3 scripts/tests/test_retro_marker_bridge.py
"""
import ast
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
sys.path.insert(0, str(REPO / "installer"))

from steps import features  # noqa: E402
import build  # noqa: E402
from common import retro as common_retro  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def info(self, stage, pct, msg):
        pass

    def warn(self, stage, pct, msg):
        pass


# --- Single source: both modules resolve through common/retro.py -------- #

check("features.py's writer IS common.retro's path builder (not a copy)",
      features.retro_state_path is common_retro.retro_state_path, True)
check("build.py's default marker equals the shared path, rooted at /",
      build.DEFAULT_RETRO_MARKER, common_retro.retro_state_path())

# --- The actual round trip: write with one side, read with the other ---- #

with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    features._retro(str(target), {"os-base", "kvm-vfio", "retro"}, FakeEmit())
    # build.py's own notion of the marker's relative location, rooted at
    # this temporary "host" instead of the real "/". If either side's path
    # were renamed independently, the marker would not be where this looks.
    read_path = target / common_retro.RETRO_STATE_REL_PATH
    check("build.py finds the marker features.py wrote (enabled)",
          build.read_retro_marker(str(read_path)), True)

with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    # Not selected: features.py writes nothing at all (see
    # test_install_engine_features.py). build.py must read that absence as
    # "off", at the very same shared path.
    read_path = target / common_retro.RETRO_STATE_REL_PATH
    check("build.py reads no marker as off, at the shared path",
          build.read_retro_marker(str(read_path)), False)

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
