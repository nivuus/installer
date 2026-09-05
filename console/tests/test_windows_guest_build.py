#!/usr/bin/env python3
"""Tests for the ISO build orchestration CLI (build.py).

Covers argument parsing and the destructive-rebuild refusal
(`enforce_disk_mode_guard`) - the only real gate against a rebuild
reformatting the wrong disk, since Windows Setup repartitions in the
windowsPE pass long before any guest-side script runs. Does not build an
ISO, read a real secret file, or touch a real Windows medium: every
assertion here runs against nonexistent paths on purpose.

Run: python3 console/tests/test_windows_guest_build.py
"""
import ast
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import apollo  # noqa: E402
import build  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# "rebuild" without the operator's explicit sign-off must be refused, and the
# refusal must name what is at stake (partition 4 = the games partition) -
# a refusal without a test is one a future refactor deletes with nobody
# noticing.
args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
    "--disk-mode", "rebuild",
])
check("rebuild defaults to unverified", args.target_disk_verified, False)
try:
    build.enforce_disk_mode_guard(args.disk_mode, args.target_disk_verified)
    failures.append("enforce_disk_mode_guard: accepted rebuild with no "
                     "--target-disk-verified")
except SystemExit as e:
    if "partition 4" not in str(e):
        failures.append(f"disk-mode guard message doesn't name partition 4: {e}")

# The same rebuild request, once the operator passes --target-disk-verified,
# must NOT be refused.
args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
    "--disk-mode", "rebuild",
    "--target-disk-verified",
])
try:
    build.enforce_disk_mode_guard(args.disk_mode, args.target_disk_verified)
except SystemExit as e:
    failures.append(f"disk-mode guard refused a verified rebuild: {e}")

# wipe (the default) never needs --target-disk-verified.
args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
])
check("default disk mode is wipe", args.disk_mode, "wipe")
try:
    build.enforce_disk_mode_guard(args.disk_mode, args.target_disk_verified)
except SystemExit as e:
    failures.append(f"disk-mode guard refused the default wipe mode: {e}")

# --retro is UNSET by default (None), not False: build_retro_psd1() must
# fall back to the host marker in that case (see the resolve_retro/
# read_retro_marker/build_retro_psd1 block below) - a bare False here would
# defeat a box the wizard's owner DID check, silently.
args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
])
check("retro is unset by default (deferred to the host marker)",
      args.retro, None)

args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
    "--retro",
])
check("--retro forces it on", args.retro, True)

args = build.parse_args([
    "--windows-iso", "/nonexistent.iso",
    "--drivers-dir", "/nonexistent-drivers",
    "--no-retro",
])
check("--no-retro forces it off", args.retro, False)

# --- read_retro_marker(): what the wizard left behind on this host ------ #

with tempfile.TemporaryDirectory() as tmp:
    missing = pathlib.Path(tmp) / "retro.json"
    check("a missing marker reads as off",
          build.read_retro_marker(str(missing)), False)

    enabled_marker = pathlib.Path(tmp) / "enabled.json"
    enabled_marker.write_text('{"enabled": true}')
    check("an enabled marker reads as on",
          build.read_retro_marker(str(enabled_marker)), True)

    disabled_marker = pathlib.Path(tmp) / "disabled.json"
    disabled_marker.write_text('{"enabled": false}')
    check("a disabled marker reads as off",
          build.read_retro_marker(str(disabled_marker)), False)

    garbage_marker = pathlib.Path(tmp) / "garbage.json"
    garbage_marker.write_text("not json")
    check("an unreadable marker reads as off, not a crash",
          build.read_retro_marker(str(garbage_marker)), False)

    # A truthy-but-not-boolean "enabled" must NOT activate retro: the
    # owner's constraint is that nothing turns on without them having
    # explicitly meant it, and bool("false") == True / bool(1) == True
    # would silently violate that from a hand-edited or malformed marker.
    string_false_marker = pathlib.Path(tmp) / "string-false.json"
    string_false_marker.write_text('{"enabled": "false"}')
    check('a string "false" for enabled reads as off, not on',
          build.read_retro_marker(str(string_false_marker)), False)

    int_one_marker = pathlib.Path(tmp) / "int-one.json"
    int_one_marker.write_text('{"enabled": 1}')
    check("an integer 1 for enabled reads as off, not on",
          build.read_retro_marker(str(int_one_marker)), False)

    string_true_marker = pathlib.Path(tmp) / "string-true.json"
    string_true_marker.write_text('{"enabled": "true"}')
    check('a string "true" for enabled reads as off, not on',
          build.read_retro_marker(str(string_true_marker)), False)

    missing_key_marker = pathlib.Path(tmp) / "missing-key.json"
    missing_key_marker.write_text("{}")
    check("a JSON object with no 'enabled' key reads as off",
          build.read_retro_marker(str(missing_key_marker)), False)

    # A document that is valid JSON but not an object must not crash the
    # whole build with an AttributeError from calling .get() on it - it
    # must resolve to "off", the same as every other shape with no
    # evidence of an explicit "enabled": true.
    for _label, _content in [
        ("a bare JSON null", "null"),
        ("a JSON list", '["enabled", true]'),
        ("a bare JSON string", '"true"'),
        ("a bare JSON number", "1"),
    ]:
        non_object_marker = pathlib.Path(tmp) / f"non-object-{_content[:1]}.json"
        non_object_marker.write_text(_content)
        check(f"{_label} reads as off, not a crash",
              build.read_retro_marker(str(non_object_marker)), False)

# --- resolve_retro(): the explicit flag always wins over the marker ----- #

with tempfile.TemporaryDirectory() as tmp:
    enabled_marker = pathlib.Path(tmp) / "enabled.json"
    enabled_marker.write_text('{"enabled": true}')
    disabled_marker = pathlib.Path(tmp) / "disabled.json"
    disabled_marker.write_text('{"enabled": false}')

    check("no CLI value: the enabled marker decides",
          build.resolve_retro(None, str(enabled_marker)), True)
    check("no CLI value: the disabled marker decides",
          build.resolve_retro(None, str(disabled_marker)), False)
    check("--no-retro overrides an enabled marker",
          build.resolve_retro(False, str(enabled_marker)), False)
    check("--retro overrides a disabled marker",
          build.resolve_retro(True, str(disabled_marker)), True)

# --- build_retro_psd1(): the exact content main() writes into the payload
# ------------------------------------------------------------------------ #
# This is the maillon a mutation could silently flip (e.g. main() calling
# apollo.render_retro(True) unconditionally): pin BOTH directions through
# the function main() actually calls, not just the boolean it resolves to.
# That main() really is the caller, unconditionally, with args.retro - not
# assumed here - is verified below by AST inspection of main() itself.

with tempfile.TemporaryDirectory() as tmp:
    enabled_marker = pathlib.Path(tmp) / "enabled.json"
    enabled_marker.write_text('{"enabled": true}')
    disabled_marker = pathlib.Path(tmp) / "disabled.json"
    disabled_marker.write_text('{"enabled": false}')
    absent_marker = pathlib.Path(tmp) / "absent.json"

    content, enabled = build.build_retro_psd1(None, str(enabled_marker))
    check("marker enabled, no CLI: resolved True", enabled, True)
    check("marker enabled, no CLI: content matches render_retro(True)",
          content, apollo.render_retro(True))
    check("marker enabled, no CLI: content says $true",
          "Enabled = $true" in content, True)
    check("marker enabled, no CLI: content does NOT say $false",
          "Enabled = $false" in content, False)

    content, enabled = build.build_retro_psd1(None, str(disabled_marker))
    check("marker disabled, no CLI: resolved False", enabled, False)
    check("marker disabled, no CLI: content matches render_retro(False)",
          content, apollo.render_retro(False))
    check("marker disabled, no CLI: content says $false",
          "Enabled = $false" in content, True)

    content, enabled = build.build_retro_psd1(None, str(absent_marker))
    check("no marker at all, no CLI: resolved False (off)", enabled, False)

    content, enabled = build.build_retro_psd1(False, str(enabled_marker))
    check("--no-retro overrides an enabled marker in the rendered content",
          content, apollo.render_retro(False))

    content, enabled = build.build_retro_psd1(True, str(disabled_marker))
    check("--retro overrides a disabled marker in the rendered content",
          content, apollo.render_retro(True))

# --- main() must feed the resolved CLI value, not a hardcoded literal --- #
# Static, but exact: ast.parse means a same-looking comment, or a variable
# merely NAMED args.retro elsewhere, can't satisfy this - it inspects the
# real argument node of the real call inside main()'s own function body.
# Restored here (2026-08-28) after living, misplaced, in
# test_retro_marker_bridge.py: that suite answers WHERE the marker lives,
# not whether main() calls the right function with the right argument -
# a different question, and this is its real home, next to the
# build_retro_psd1() content checks above whose only unverified premise it
# closes.
#
# A plain ast.walk()-based lookup (the original shape of this check) finds
# the call node no matter how deeply it is nested under an ast.If: walk()
# does not care about control flow, only about "does this node exist
# somewhere in the tree" - so wrapping the exact same call in
# "if args.retro:" left every assertion below green while silently making
# retro.psd1 go unwritten on the args.retro-false/None path. Measured
# directly while restoring this guard, not assumed: a bare
# "if True: retro_psd1, retro_enabled = build_retro_psd1(args.retro)"
# mutation left the walk()-based version passing. _RetroCallFinder tracks
# if_depth (the same technique this file's write-unconditional guard, a few
# lines below, already used for config/retro.psd1's write_text) so a
# conditional call is caught here too, not just a conditional write.
_build_source = (REPO / "console" / "guest" / "build.py").read_text()
_tree = ast.parse(_build_source)
_main_fn = next((n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
check("build.py still defines main()", _main_fn is not None, True)


class _RetroCallFinder(ast.NodeVisitor):
    """Walks main()'s body for build_retro_psd1(...) and tracks whether any
    ast.If wraps it, exactly as _RetroWriteFinder (below) does for the
    write - a call reachable only under a condition is indistinguishable,
    to a plain walk()+next() lookup, from an always-reached one."""

    def __init__(self):
        self.if_depth = 0
        self.call = None
        self.call_under_if = False

    def visit_If(self, node):
        self.if_depth += 1
        self.generic_visit(node)
        self.if_depth -= 1

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "build_retro_psd1":
            if self.call is None:
                self.call = node
            if self.if_depth > 0:
                self.call_under_if = True
        self.generic_visit(node)


_finder = _RetroCallFinder()
if _main_fn is not None:
    _finder.visit(_main_fn)
check("main() calls build_retro_psd1", _finder.call is not None, True)
check("... and calls it UNCONDITIONALLY, retro on or off",
      _finder.call_under_if, False)

if _finder.call is not None:
    _arg = _finder.call.args[0] if _finder.call.args else None
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
# check above green: build_retro_psd1 is still called unconditionally with
# args.retro, and the round trip further up never reaches main() at all.
# An absent retro.psd1 must stay indistinguishable from "a payload built
# before this option existed" ONLY, never reachable by unchecking the box -
# that distinction is the entire point of always rendering an explicit
# Enabled = $false. payload.verify_staged() is not the guard here either: it
# runs later, for a different reason (catching a build that forgot the file
# entirely), not specifically a write skipped because retro was off.
# Restored here (2026-08-28) from scripts/tests/test_retro_marker_bridge.py,
# where it lived on main and was dropped in the move: measured, wrapping
# build.py's `(config / "retro.psd1").write_text(...)` in `if retro_enabled:`
# left all 30 suites green without it.
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
    _write_finder = _RetroWriteFinder()
    _write_finder.visit(_main_fn)
    check("main() writes config/retro.psd1 at all", _write_finder.found, True)
    check("... and does so UNCONDITIONALLY, retro on or off",
          _write_finder.found_under_if, False)


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all build CLI/guard tests passed")
