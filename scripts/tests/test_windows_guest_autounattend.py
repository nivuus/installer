#!/usr/bin/env python3
"""Tests for the Windows unattended answer file renderer.

Run: python3 scripts/tests/test_windows_guest_autounattend.py
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import autounattend as ua  # noqa: E402

NS = {"u": "urn:schemas-microsoft-com:unattend"}
IMAGE = "Windows 11 IoT Enterprise LTSC"
GOOD = dict(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
            admin_password="p4ssw0rd!", image_name=IMAGE)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def rejects(label, **overrides):
    params = ua.UnattendParams(**{**GOOD, **overrides})
    try:
        ua.validate(params)
    except ua.UnattendError:
        return
    failures.append(f"{label}: accepted what it must reject")


rejects("malformed key", product_key="AAAAA-BBBBB")
rejects("lowercase key", product_key="aaaaa-bbbbb-ccccc-ddddd-eeeee")
rejects("empty password", admin_password="")
rejects("hostname too long", hostname="NIVUUS-WINDOWS-2024")
rejects("hostname with space", hostname="NIVUUS WIN")
rejects("empty image name", image_name="   ")
rejects("autologon count too low", autologon_count=1)

# Verify that malformed key errors do not leak the key value
try:
    bad_key = "NOTA-VALID-KEY"
    params = ua.UnattendParams(**{**GOOD, "product_key": bad_key})
    ua.validate(params)
except ua.UnattendError as exc:
    error_str = str(exc)
    if any(c in error_str for c in bad_key.split("-")):
        failures.append(f"malformed key error contains key characters: {error_str}")
else:
    failures.append("malformed key: accepted what it must reject")

xml_text = ua.render(ua.UnattendParams(**GOOD))
root = ET.fromstring(xml_text)

passes = [s.get("pass") for s in root.findall("u:settings", NS)]
check("settings passes", passes, ["windowsPE", "specialize", "oobeSystem"])

check("image name is injected", IMAGE in xml_text, True)
check("product key is injected", GOOD["product_key"] in xml_text, True)
check("EULA accepted", "<AcceptEula>true</AcceptEula>" in xml_text, True)
check("wipes disk 0", "<WillWipeDisk>true</WillWipeDisk>" in xml_text, True)
squished = xml_text.replace("\n", "").replace(" ", "")
check("EFI partition sized 260 MB",
      "<Type>EFI</Type><Size>260</Size>" in squished, True)
check("MSR partition sized 16 MB",
      "<Type>MSR</Type><Size>16</Size>" in squished, True)
check("data partition extends",
      "<Type>Primary</Type><Extend>true</Extend>" in squished, True)

# SetupComplete.cmd runs as SYSTEM in session 0, blind to the display.
check("never uses SetupComplete", "SetupComplete" in xml_text, False)

cmds = root.findall(".//u:FirstLogonCommands/u:SynchronousCommand", NS)
check("two first-logon commands", len(cmds), 2)
check("orders", [c.find("u:Order", NS).text for c in cmds], ["1", "2"])

launcher = cmds[0].find("u:CommandLine", NS).text
check("scans drives for the marker", "\\nivuus\\PAYLOAD.id" in launcher, True)
check("no hardcoded payload drive", "D:\\nivuus\\provision" in launcher, False)
check("launches run-all.ps1", "run-all.ps1" in launcher, True)

guard = cmds[1].find("u:CommandLine", NS).text
check("guard writes a loud failure marker",
      "NIVUUS-PAYLOAD-NOT-FOUND" in guard, True)

check("autologon enabled", "<AutoLogon>" in xml_text, True)
check("autologon count", "<LogonCount>5</LogonCount>" in xml_text, True)
# The medium is en-US: the built-in account is Administrator. Targeting
# "Administrateur" would make the automatic logon fail silently.
check("autologon targets the en-US built-in account",
      "<Username>Administrator</Username>" in xml_text, True)
check("setup stays en-US", "<UILanguage>en-US</UILanguage>" in xml_text, True)
check("regional formats are French",
      "<UserLocale>fr-FR</UserLocale>" in xml_text, True)
check("keyboard is French", "<InputLocale>fr-FR</InputLocale>" in xml_text, True)

# XML-special characters in a password must not corrupt the document.
amp = ua.render(ua.UnattendParams(**{**GOOD, "admin_password": "a&b<c>"}))
ET.fromstring(amp)
check("password is XML-escaped", "a&amp;b&lt;c&gt;" in amp, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all autounattend rendering tests passed")
