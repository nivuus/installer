#!/usr/bin/env python3
"""Tests for common/regional.py: the choices come from Debian's own files.

Run: python3 scripts/tests/test_common_regional.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(REPO / "scripts" / "tests"))

from common import regional  # noqa: E402
import regional_fixtures as fx  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


check("every UTF-8 locale and only those, any script",
      sorted(regional.utf8_locales(fx.SUPPORTED)),
      ["ar_SA.UTF-8", "en_US.UTF-8", "fr_FR.UTF-8", "ja_JP.UTF-8",
       "sr_RS@latin", "zh_CN.UTF-8"])

layouts = regional.xkb_layouts(fx.BASE_LST)
check("layouts from the layout section only", sorted(layouts), ["ara", "fr", "jp", "us"])
check("variants attached to their layout", layouts["fr"]["variants"],
      {"bepo": "French (BEPO)"})
check("a layout description", layouts["jp"]["description"], "Japanese")

with tempfile.TemporaryDirectory() as tmp:
    fx.seed(tmp)
    got = regional.choices(tmp)
    check("choices list the locales", "ja_JP.UTF-8" in got["locales"], True)
    check("choices list the layouts with variants",
          [k for k in got["keyboards"] if k["layout"] == "us"][0]["variants"],
          [{"variant": "intl", "description": "English (US, intl., with dead keys)"}])
    check("nothing missing", got["missing"], [])
    check("an existing zone", regional.timezone_exists(tmp, "Asia/Tokyo"), True)
    check("a missing zone", regional.timezone_exists(tmp, "Mars/Olympus"), False)
    check("no escaping zoneinfo", regional.timezone_exists(tmp, "../../etc/passwd"), False)

with tempfile.TemporaryDirectory() as tmp:
    got = regional.choices(tmp)
    check("a medium without the files says which ones are missing",
          got["missing"], ["/usr/share/i18n/SUPPORTED", "/usr/share/X11/xkb/rules/base.lst"])
    check("and offers no invented list", (got["locales"], got["keyboards"]), ([], []))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all regional tests passed")
