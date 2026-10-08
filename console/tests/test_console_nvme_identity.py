#!/usr/bin/env python3
"""The console's disk identity once vfio-pci owns the disk.

Every activate after the install runs with the dedicated NVMe bound to
vfio-pci, so /sys/block has no entry for it. These tests drive the REAL
sysfs resolver against a block tree that lacks the disk - the production
shape - instead of a `pci_address_of` double that answers regardless: a
double that satisfies a precondition production cannot satisfy is exactly
how domain_defined() stayed green in test and answered "not done" on every
real replay (see docs/claude/console-activate-lessons.md).

Run: python3 console/tests/test_console_nvme_identity.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONSOLE))
import guest_steps  # noqa: E402
import nvme_identity  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


DISK = "/dev/nvme1n1"
CONSOLE_NVME = "0000:03:00.0"
OTHER_NVME = "0000:02:00.0"
FACT = {nvme_identity.FACT_KEY: {"device": DISK, "address": CONSOLE_NVME}}


def hostdev(address):
    """The <hostdev> shape measured on the production domain: the HOST
    address inside <source>, then the guest-side bus address libvirt adds."""
    domain, bus, rest = address.split(":")
    slot, func = rest.split(".")
    return ("<hostdev mode='subsystem' type='pci' managed='yes'>"
            "<driver name='vfio'/><source>"
            f"<address domain='0x{domain}' bus='0x{bus}' slot='0x{slot}' "
            f"function='0x{func}'/></source><alias name='hostdev0'/>"
            "<address type='pci' domain='0x0000' bus='0x07' slot='0x00' "
            "function='0x0'/></hostdev>")


def regime_domain(address):
    """The steady-state domain guest-ready-watch.py leaves: no install media."""
    return f"<domain><devices>{hostdev(address)}</devices></domain>"


class FakeVirsh:
    def __init__(self, xml):
        self.xml = xml

    def __call__(self, *args):
        if args[0] == "dumpxml":
            return subprocess.CompletedProcess(list(args), 0, self.xml, "")
        if args[0] == "domstate":
            return subprocess.CompletedProcess(list(args), 0, "shut off\n", "")
        return subprocess.CompletedProcess(list(args), 1, "", "")


def vfio_bound_sysfs(root):
    """A /sys/block holding only the host's own disk, as measured on the
    production host 2026-10-08 (nvme0n1, no entry for the passthrough one)."""
    block = os.path.join(root, "sysfs-block")
    os.makedirs(os.path.join(block, "nvme0n1", "device"))
    os.makedirs(os.path.join(root, "pci", "0000:02:00.0"))
    os.symlink(os.path.join(root, "pci", "0000:02:00.0"),
               os.path.join(block, "nvme0n1", "device", "device"))
    return block


def define_done(tmp, hw, xml):
    """domain_defined() through plan_steps, with the REAL sysfs resolver."""
    answers = {"dedicated_nvme": DISK, "retro": False, "disk_mode": "rebuild",
               "target_disk_verified": True, "admin_password": "x",
               "windows_iso": "/media/ltsc.iso", "ltsc_key": "x",
               "apollo_password": "x"}
    steps = guest_steps.plan_steps(
        answers, hw, tmp, virsh=FakeVirsh(xml),
        qemu_owner=lambda: (0, 0), chown=lambda *a: None,
        definition_on_disk=lambda: True)
    return {s.name: s for s in steps}["define"].already_done()


# --- recorded_identity: the fact speaks only for the answer it came from - #
check("a fact for this very answer gives its address",
      nvme_identity.recorded_identity(DISK, FACT), CONSOLE_NVME)
check("a fact measured for ANOTHER answer vouches for nothing",
      nvme_identity.recorded_identity("/dev/nvme2n1", FACT), None)
check("no fact at all: cannot tell",
      nvme_identity.recorded_identity(DISK, {}), None)
check("a malformed address is not an identity",
      nvme_identity.recorded_identity(
          DISK, {nvme_identity.FACT_KEY: {"device": DISK, "address": "x"}}),
      None)
check("a fact that is not a mapping is ignored",
      nvme_identity.recorded_identity(DISK, {nvme_identity.FACT_KEY: "x"}),
      None)
check("the address is normalised like the XML side",
      nvme_identity.recorded_identity(
          DISK, {nvme_identity.FACT_KEY: {"device": DISK,
                                          "address": "0:3:0.0"}}),
      CONSOLE_NVME)

# --- now beats then: a live sysfs answer wins over the recorded one ------- #
check("a live measurement wins over a stale fact",
      nvme_identity.dedicated_nvme_identity(
          DISK, FACT, pci_address_of=lambda d: OTHER_NVME), OTHER_NVME)

# --- the production shape: the disk is gone from /sys/block -------------- #
with tempfile.TemporaryDirectory() as tmp:
    os.environ["NIVUUS_SYSFS_BLOCK"] = vfio_bound_sysfs(tmp)
    try:
        check("vfio-bound disk, REAL resolver, no fact: cannot tell",
              nvme_identity.dedicated_nvme_identity(DISK, {}), None)
        check("vfio-bound disk, REAL resolver, with the fact: identified",
              nvme_identity.dedicated_nvme_identity(DISK, FACT), CONSOLE_NVME)

        hw = dict(FACT, dedicated_nvme_size_bytes=2000 * 1024 ** 3)
        work = os.path.join(tmp, "work")
        check("the steady-state domain on the answered disk is DONE: no "
              "`define --replace` over a working console",
              define_done(work, hw, regime_domain(CONSOLE_NVME)), True)
        check("the steady-state domain on ANOTHER disk is still not done",
              define_done(work, hw, regime_domain(OTHER_NVME)), False)
        check("without the fact the domain cannot be recognised (the "
              "pre-fix behaviour on an adopted host)",
              define_done(work, {"dedicated_nvme_size_bytes": 2000 * 1024 ** 3},
                          regime_domain(CONSOLE_NVME)), False)
    finally:
        del os.environ["NIVUUS_SYSFS_BLOCK"]


if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the dedicated disk is identified through resolve's fact once "
      "vfio-pci owns it, and the fact never speaks for another answer")
