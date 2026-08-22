#!/usr/bin/env python3
"""Tests for the throwaway LTSC test domain XML.

Run: python3 scripts/tests/test_windows_guest_domain.py
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import testdomain  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


xml_text = testdomain.domain_xml(
    disk_path="/media/data/vm/windows-ltsc-test.qcow2",
    windows_iso="/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso",
    unattend_iso="/media/data/iso/nivuus-unattend.iso",
)
root = ET.fromstring(xml_text)

check("domain name", root.findtext("name"), testdomain.DOMAIN_NAME)
check("kvm domain", root.get("type"), "kvm")

os_el = root.find("os")
check("q35 machine", os_el.find("type").get("machine"), "pc-q35-9.2")
# Explicit firmware paths: automatic selection already broke S4 on this host.
check("no firmware autoselection", os_el.get("firmware"), None)
check("secure boot loader", os_el.findtext("loader"),
      "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd")
check("loader is secure-boot capable",
      os_el.find("loader").get("secure"), "yes")
check("nvram template with Microsoft keys",
      os_el.find("nvram").get("template"), "/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
# Secure Boot needs SMM, and SMM needs q35.
check("smm on", root.find("features/smm").get("state"), "on")

check("tpm 2.0 emulated",
      root.find("devices/tpm/backend").get("version"), "2.0")
check("tpm backend is swtpm",
      root.find("devices/tpm/backend").get("type"), "emulator")
check("tpm model", root.find("devices/tpm").get("model"), "tpm-crb")

disks = root.findall("devices/disk")
system = [d for d in disks if d.get("device") == "disk"]
check("one system disk", len(system), 1)
# The LTSC medium carries no virtio driver: a virtio disk would be invisible.
check("system disk on sata", system[0].find("target").get("bus"), "sata")
check("system disk is qcow2", system[0].find("driver").get("type"), "qcow2")
check("system disk lives on /media/data",
      system[0].find("source").get("file").startswith("/media/data/"), True)

cdroms = [d for d in disks if d.get("device") == "cdrom"]
check("two cdroms", len(cdroms), 2)
sources = sorted(c.find("source").get("file") for c in cdroms)
check("both media attached", sources,
      [
       "/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso",
       "/media/data/iso/nivuus-unattend.iso"])
booting = [c for c in cdroms if c.find("boot") is not None]
check("only the windows medium boots", len(booting), 1)
check("windows medium boots first",
      booting[0].find("source").get("file"),
      "/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso")

nic = root.find("devices/interface/model")
check("e1000e nic (no inbox virtio driver)", nic.get("type"), "e1000e")

addrs = [h.find("source/address") for h in root.findall("devices/hostdev")]
slots = sorted((a.get("bus"), a.get("slot"), a.get("function")) for a in addrs)
check("gpu and its audio function are passed", slots,
      [("0x01", "0x00", "0x0"), ("0x01", "0x00", "0x1")])
# The Samsung NVMe stays with the production VM: Server 2022 is the rollback.
check("nvme is never passed", "0x03" in [a.get("bus") for a in addrs], False)

check("an emulated console exists", root.find("devices/graphics") is not None, True)
check("hugepages are not claimed", root.find("memoryBacking"), None)

# Test assert_gpu_free() guard with monkeypatched _virsh.
class MockProc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

original_virsh = testdomain._virsh
original_gpu_holders = testdomain.gpu_holders
# Every case below is about the domstate guard, not the holder scan: keep the
# holder scan a no-op unless a case says otherwise, so no real `find` runs.
testdomain.gpu_holders = lambda: []
try:
    # Case 1: VM shut off, no holders, safe to proceed.
    testdomain._virsh = lambda *a: MockProc(0, "shut off", "")
    testdomain.assert_gpu_free()

    # Case 2: VM running, must raise.
    testdomain._virsh = lambda *a: MockProc(0, "running", "")
    try:
        testdomain.assert_gpu_free()
        failures.append("guard: running VM did not raise")
    except testdomain.DomainError:
        pass

    # Case 3: virsh error, must raise with stderr in message.
    err_msg = "libvirtd unreachable"
    testdomain._virsh = lambda *a: MockProc(1, "", err_msg)
    try:
        testdomain.assert_gpu_free()
        failures.append("guard: virsh error did not raise")
    except testdomain.DomainError as e:
        if err_msg not in str(e):
            failures.append(f"guard: error message missing stderr: {e}")

    # Case 4: virsh succeeds but empty stdout, must raise.
    testdomain._virsh = lambda *a: MockProc(0, "", "")
    try:
        testdomain.assert_gpu_free()
        failures.append("guard: empty stdout did not raise")
    except testdomain.DomainError:
        pass

    # Case 5: VM shut off but a process still holds /dev/nvidia* - must raise
    # and name the PID, since this domain has no hooks to stop it for you.
    testdomain._virsh = lambda *a: MockProc(0, "shut off", "")
    testdomain.gpu_holders = lambda: ["4242"]
    try:
        testdomain.assert_gpu_free()
        failures.append("guard: GPU holder did not raise")
    except testdomain.DomainError as e:
        if "4242" not in str(e):
            failures.append(f"guard: error message missing holder PID: {e}")
    testdomain.gpu_holders = lambda: []

    # Case 6: the holder scan itself errors - fail closed, never treat a
    # broken scan as "no holders".
    def _broken_find(*a, **kw):
        return MockProc(2, "", "find: /proc: some races")
    original_run = testdomain.subprocess.run
    testdomain.subprocess.run = _broken_find
    try:
        try:
            # The real function, not the case-5 stub still installed above.
            original_gpu_holders()
            failures.append("gpu_holders: broken scan did not raise")
        except testdomain.DomainError:
            pass
    finally:
        testdomain.subprocess.run = original_run
finally:
    testdomain._virsh = original_virsh
    testdomain.gpu_holders = original_gpu_holders

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all test domain XML checks passed")
