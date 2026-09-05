#!/usr/bin/env python3
"""Tests for the Windows unattended answer file renderer.

Run: python3 console/tests/test_windows_guest_autounattend.py
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import autounattend as ua  # noqa: E402

NS = {"u": "urn:schemas-microsoft-com:unattend"}
IMAGE = "Windows 11 IoT Enterprise LTSC"
GOOD = dict(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
            admin_password="p4ssw0rd!", image_name=IMAGE)

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
    failures.append(f"{label}: did not raise {exc_type.__name__}")


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

# Verify that malformed key errors do not leak the key value. Shaped like a
# real key (five groups of five) so it fails only on case, not on structure -
# a structurally-wrong fixture like "NOTA-VALID-KEY" would pass this check
# for the wrong reason (its groups just never appear in the generic message).
try:
    bad_key = "zzzzz-yyyyy-xxxxx-wwwww-vvvvv"
    params = ua.UnattendParams(**{**GOOD, "product_key": bad_key})
    ua.validate(params)
except ua.UnattendError as exc:
    error_str = str(exc)
    if bad_key in error_str:
        failures.append(f"malformed key error contains the key: {error_str}")
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

# Windows 11 24H2 asks for country and keyboard unless International-Core is
# declared in the oobeSystem pass too; the <OOBE> Hide* options do not cover
# those pages, and a real install stalled on exactly that.
oobe = root.findall("u:settings[@pass='oobeSystem']/u:component", NS)
check("oobeSystem declares International-Core",
      any(c.get("name") == "Microsoft-Windows-International-Core" for c in oobe),
      True)
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

# Sub-project B: two partitions and a mode that preserves D:.
base = dict(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
            admin_password="s3cret", image_name="Windows 11 IoT Enterprise LTSC")

wipe = ua.render(ua.UnattendParams(**base))
check("wipe mode wipes the disk", "<WillWipeDisk>true</WillWipeDisk>" in wipe, True)
check("wipe mode creates four partitions", wipe.count("<CreatePartition "), 4)
# C n'a plus de taille : la partition de donnees est en premier, donc c'est
# elle qui est dimensionnee et Windows prend le reste (<Extend> ne vaut que
# pour la derniere partition creee).
check("wipe mode sizes the data partition from the parameter",
      f"<Size>{ua.DEFAULT_DATA_PARTITION_MB}</Size>" in wipe, True)
check("wipe mode extends the last partition", wipe.count("<Extend>true</Extend>"), 1)
check("wipe mode letters C", "<Letter>C</Letter>" in wipe, True)
# The optical drive takes D: unless the answer file claims it first.
check("wipe mode letters D", "<Letter>D</Letter>" in wipe, True)
check("wipe mode installs to partition 4",
      "<InstallTo><DiskID>0</DiskID><PartitionID>4</PartitionID></InstallTo>" in wipe,
      True)
# L'ORDRE EST L'INVARIANT DE SECURITE, pas un detail de mise en page : la
# partition de donnees doit preceder la partition Windows. En aval d'elle,
# Windows Setup retrecit la partition Windows pour y glisser sa Recovery et
# decale tout ce qui suit ; a la reconstruction il recree cette Recovery et
# emporte la partition suivante. C'est ce qui a efface la session Steam et
# 4,25 Go de jeu le 2026-08-25. En amont, Setup ne reorganise jamais rien.
_data_pos = wipe.index("<Label>Data</Label>")
_win_pos = wipe.index("<Label>Windows</Label>")
check("la partition de donnees precede la partition Windows",
      _data_pos < _win_pos, True)

rebuild = ua.render(
    ua.UnattendParams(**base, disk_mode="rebuild"))
check("rebuild never wipes the disk", "<WillWipeDisk>true</WillWipeDisk>" in rebuild, False)
check("rebuild explicitly disables disk wipe", "<WillWipeDisk>false</WillWipeDisk>" in rebuild, True)
check("rebuild creates no partition", "<CreatePartition " in rebuild, False)
check("rebuild formats exactly one partition", rebuild.count("<ModifyPartition "), 1)
check("rebuild formats partition 4", "<PartitionID>4</PartitionID>" in rebuild, True)
# La partition 1 est D:. La nommer, ne serait-ce qu'une fois, en mode rebuild
# serait un defaut.
check("rebuild never names partition 1", "<PartitionID>1</PartitionID>" in rebuild,
      False)
check("rebuild installs to partition 4",
      "<InstallTo><DiskID>0</DiskID><PartitionID>4</PartitionID></InstallTo>" in rebuild,
      True)

check_raises("unknown disk mode is refused", ua.UnattendError,
             lambda: ua.render(
                 ua.UnattendParams(**base, disk_mode="format-everything")))
check_raises("an absurdly small C is refused", ua.UnattendError,
             lambda: ua.render(
                 ua.UnattendParams(**base, data_partition_mb=1024)))

# The guest must stay logged on forever: Apollo captures an interactive desktop
# and the agent must live in session 1.
check("autologon is enabled", "<Enabled>true</Enabled>" in wipe, True)

# Le defaut vise le NVMe de production (1 To), pas le banc de 340 GiB : un
# defaut cale sur le banc donnerait silencieusement un Windows de 790 GiB et
# une bibliotheque de jeux minuscule sur la vraie machine.
check("le defaut vise le disque de production", ua.DEFAULT_DATA_PARTITION_MB, 839680)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all autounattend rendering tests passed")
