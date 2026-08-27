#!/usr/bin/env python3
"""Tests for the ISO build orchestration CLI (build.py).

Covers argument parsing and the destructive-rebuild refusal
(`enforce_disk_mode_guard`) - the only real gate against a rebuild
reformatting the wrong disk, since Windows Setup repartitions in the
windowsPE pass long before any guest-side script runs. Does not build an
ISO, read a real secret file, or touch a real Windows medium: every
assertion here runs against nonexistent paths on purpose.

Run: python3 scripts/tests/test_windows_guest_build.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all build CLI/guard tests passed")
