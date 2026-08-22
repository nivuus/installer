#!/usr/bin/env python3
"""Rendering of the Apollo configuration shipped in the offline payload.

Every key asserted here was verified present in the Apollo 0.4.6 binary on
2026-08-22; a typo in a key name is silently ignored by Apollo, so the test is
the only thing standing between a rendered file and a stream that never gets
HDR. Run: python3 scripts/tests/test_windows_guest_apollo.py
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import apollo  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {exc_type.__name__}")
        return
    failures.append(f"{label}: raised nothing, want {exc_type.__name__}")


params = apollo.ApolloParams(ui_username="nivuus", ui_password="p4ssw0rd")
conf = apollo.render_conf(params)
conf_map = {}
for raw in conf.splitlines():
    raw = raw.strip()
    if raw and not raw.startswith("#") and "=" in raw:
        k, _, v = raw.partition("=")
        conf_map[k.strip()] = v.strip()

# Measured 2026-08-22: with ensure_only_display the virtual display was the
# only active path (paths=1), which is what put bpc=10 on the wire.
check("only the virtual display stays active",
      conf_map.get("dd_configuration_option"), "ensure_only_display")
# The isolated option keeps the physical display alive in a corner - wrong for
# a headless box, and the cause of the 2026-07-23 "game on the dummy plug" bug.
check("no isolated corner layout",
      conf_map.get("isolated_virtual_display_option"), "disabled")
check("HDR follows the client", conf_map.get("dd_hdr_option"), "auto")
# The dummy plug is gone: pinning an output name would pin a display that no
# longer exists.
check("no output is pinned", "output_name" in conf_map, False)
check("credentials are not in sunshine.conf",
      any("p4ssw0rd" in v for v in conf_map.values()), False)

apps = json.loads(apollo.render_apps(params))
# version: 2 ensures Apollo does not migrate from v1 to v2, guaranteeing
# virtual-display is honored (critical for the HDR chain).
check("apps.json declares version 2", apps.get("version"), 2)
names = [a["name"] for a in apps["apps"]]
check("both production apps are declared", sorted(names),
      ["Desktop", "Steam Big Picture"])
desktop = next(a for a in apps["apps"] if a["name"] == "Desktop")
# 🔴 It is the app's virtual-display flag - NOT isolated_virtual_display_option
# - that makes the SudoVDA display appear (trap paid on 2026-07-23).
check("Desktop asks for a virtual display", desktop.get("virtual-display"), True)
check("Desktop launches Steam from D:", desktop.get("detached"),
      ["D:\\Steam\\steam.exe"])
bp = next(a for a in apps["apps"] if a["name"] == "Steam Big Picture")
check("Big Picture asks for a virtual display", bp.get("virtual-display"), True)

secrets = apollo.render_secrets("adminpass", "nivuus", "p4ssw0rd")
check("secrets file is a PowerShell data file", secrets.lstrip().startswith("@{"), True)
for needle in ["adminpass", "nivuus", "p4ssw0rd"]:
    check(f"secrets carry {needle}", needle in secrets, True)
# A quote in a secret would break the .psd1 and, worse, could inject.
check_raises("a quote in a secret is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("ad'min", "nivuus", "p4ssw0rd"))
check_raises("an empty UI password is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("adminpass", "nivuus", ""))
# Non-ASCII in a secret would be misdecoded by Windows PowerShell 5.1's ANSI
# codepage, silently corrupting the password.
check_raises("non-ASCII in a secret is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("adminpäss", "nivuus", "p4ssw0rd"))
# ApolloPassword crosses a Start-Process -ArgumentList boundary on the guest
# (Windows PowerShell 5.1 does not quote array elements containing a space),
# so a space or double quote there must be refused, not just single quotes.
check_raises("a space in ApolloPassword is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("adminpass", "nivuus", "p4ss w0rd"))
check_raises("a double quote in ApolloPassword is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("adminpass", "nivuus", 'p4ss"w0rd'))
# AdminPassword never reaches a command line (XML answer file, Set-ItemProperty)
# so it is deliberately NOT restricted the same way - pin the asymmetry.
admin_with_space = apollo.render_secrets("admin pass", "nivuus", "p4ssw0rd")
check("a space in AdminPassword is still accepted",
      "admin pass" in admin_with_space, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK")
