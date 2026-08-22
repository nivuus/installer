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

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all build CLI/guard tests passed")
