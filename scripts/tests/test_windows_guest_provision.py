#!/usr/bin/env python3
"""Static checks on the guest provisioning scripts.

PowerShell cannot run here, so these are the invariants a Linux host can still
enforce: ordering, no hardcoded drive letters, and the session-1 contract.
Run: python3 scripts/tests/test_windows_guest_provision.py
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GUEST = REPO / "installer" / "windows-guest"
PROVISION = GUEST / "provision"
PROBE = GUEST / "probe"

STAGES = ["00-bootstrap.ps1", "10-nvidia.ps1", "20-sudovda.ps1", "99-marker.ps1"]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


for name in STAGES + ["run-all.ps1"]:
    check(f"{name} exists", (PROVISION / name).is_file(), True)
for name in ["AdvancedColor.cs", "advanced-color.ps1"]:
    check(f"{name} exists", (PROBE / name).is_file(), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)

texts = {p.name: p.read_text(encoding="utf-8")
         for p in list(PROVISION.iterdir()) + list(PROBE.iterdir()) if p.is_file()}

for name, text in texts.items():
    check(f"{name} under 200 lines", len(text.splitlines()) <= 200, True)
    # Every drive letter must come from the marker scan, never be assumed.
    check(f"{name} has no hardcoded payload drive",
          bool(re.search(r"[D-Z]:\\nivuus", text)), False)
    # Session 0 is blind to the display; nothing may migrate back to it.
    check(f"{name} never mentions SetupComplete", "SetupComplete" in text, False)

runall = texts["run-all.ps1"]
positions = [runall.find(s) for s in STAGES]
check("run-all lists every stage", all(p >= 0 for p in positions), True)
check("run-all keeps the stages ordered", positions, sorted(positions))
check("run-all skips stages already done", ".done" in runall, True)
check("run-all takes PayloadRoot", "$PayloadRoot" in runall, True)
check("run-all mentions reboot.requested", "reboot.requested" in runall, True)
check("run-all mentions Restart-Computer", "Restart-Computer" in runall, True)
# The .done file must be written before the reboot sentinel is consumed, or a
# stage that needs a reboot would rerun from scratch on every resume.
check("run-all writes .done before consuming the reboot sentinel",
      runall.find("Set-Content -Path $done") < runall.find("reboot.requested"), True)

boot = texts["00-bootstrap.ps1"]
check("bootstrap enables PSRemoting", "Enable-PSRemoting" in boot, True)
# WinRM is configured early for debugging but stays firewalled until the end,
# so "5985 reachable" means "provisioning finished".
check("bootstrap keeps 5985 closed", "Disable-NetFirewallRule" in boot, True)
check("bootstrap writes the resume script", "resume.cmd" in boot, True)
check("bootstrap registers the resume entry", "CurrentVersion\\Run" in boot, True)
check("resume script rescans drives", "%%d" in boot, True)

marker = texts["99-marker.ps1"]
# The version lives in two languages; a silent drift would make the host accept
# a guest provisioned by an older payload.
sys.path.insert(0, str(GUEST))
import payload  # noqa: E402
check("marker version matches payload.PROVISION_VERSION",
      f"provision_version={payload.PROVISION_VERSION}" in marker, True)
check("marker opens 5985", "Enable-NetFirewallRule" in marker, True)
check("marker disables autologon", "AutoAdminLogon" in marker, True)
check("marker clears the resume entry", "Remove-ItemProperty" in marker, True)
check("marker writes PROVISION.done", "PROVISION.done" in marker, True)
# The marker is what makes "5985 reachable" mean "guest is provisioned", so it
# must be written strictly before the firewall rule that opens 5985.
check("marker writes PROVISION.done before opening 5985",
      marker.find("PROVISION.done") < marker.find("Enable-NetFirewallRule"), True)
# 10-nvidia.ps1 may have deferred its device check to survive a driver reboot;
# this is where that deferred verification must land, before port 5985 opens.
check("marker verifies the NVIDIA device", "Get-PnpDevice" in marker, True)
check("marker verifies the NVIDIA device before opening 5985",
      marker.find("Get-PnpDevice") < marker.find("Enable-NetFirewallRule"), True)

nvidia = texts["10-nvidia.ps1"]
check("nvidia installs silently", "-noreboot" in nvidia, True)
check("nvidia verifies the device afterwards", "Get-PnpDevice" in nvidia, True)
check("nvidia writes reboot.requested", "reboot.requested" in nvidia, True)
# The sentinel must only be written on the "success, reboot required" path
# (ExitCode 1), never unconditionally.
check("nvidia requests reboot only on the ExitCode -eq 1 path",
      nvidia.find("-eq 1") < nvidia.find("reboot.requested"), True)

sudovda = texts["20-sudovda.ps1"]
check("sudovda trusts the publisher certificate", "TrustedPublisher" in sudovda, True)
check("sudovda verifies the device afterwards", "ROOT\\DISPLAY" in sudovda, True)

cs = texts["AdvancedColor.cs"]
for symbol in ("GetDisplayConfigBufferSizes", "QueryDisplayConfig",
               "DisplayConfigGetDeviceInfo", "QDC_ONLY_ACTIVE_PATHS"):
    check(f"probe uses {symbol}", symbol in cs, True)
check("probe reports bits per colour", "bitsPerColorChannel" in cs, True)
check("probe output matches the reference format",
      "target={0} rc={1} supported={2} enabled={3} bpc={4}" in cs, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all provisioning script checks passed")
