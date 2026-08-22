#!/usr/bin/env python3
"""Tests for the generated production domain (sub-project C).

Run: python3 scripts/tests/test_windows_guest_production_domain.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
sys.path.insert(0, str(REPO / "installer"))

import domain  # noqa: E402

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


def parse_cpuset(cpuset_str: str) -> set:
    """Parse a cpuset string like '14-15' or '0,2,4-6' into a set of CPU indices."""
    result = set()
    for part in cpuset_str.split(","):
        if "-" in part:
            start, end = part.split("-")
            result.update(range(int(start), int(end) + 1))
        else:
            result.add(int(part))
    return result


# The i9-12900K's 8 P-cores expose 16 threads, 0..15.
plan = domain.vcpu_plan(list(range(16)))
check("vcpu count", plan["vcpus"], 14)
check("cores", plan["cores"], 7)
check("threads", plan["threads"], 2)
check("emulator cpuset", plan["emulator_cpuset"], "14-15")
check("first pin", plan["vcpupin"][0], (0, 0))
check("last pin", plan["vcpupin"][-1], (13, 13))
check("pin count matches vcpus", len(plan["vcpupin"]), 14)
# Invariant: union of all pinned CPUs and emulator CPUs equals the input pool.
# Parse emulator_cpuset from the returned string to verify it's correct.
pinned_cpus = set(cpu for _, cpu in plan["vcpupin"])
emulator_cpus = parse_cpuset(plan["emulator_cpuset"])
check("16-CPU invariant: all CPUs accounted for",
      pinned_cpus | emulator_cpus, set(range(16)))

# An odd remainder must drop a thread rather than break SMT pairing, and the
# CPU it frees goes to the emulator rather than being left idle.
odd = domain.vcpu_plan(list(range(11)))
check("odd pool keeps pairs", odd["vcpus"], 8)
check("odd pool cores", odd["cores"], 4)
check("odd pool emulator takes every leftover", odd["emulator_cpuset"], "8-10")
# Invariant: union of all pinned CPUs and emulator CPUs equals the input pool.
# Parse emulator_cpuset from the returned string to verify it's correct.
pinned_odd = set(cpu for _, cpu in odd["vcpupin"])
emulator_odd = parse_cpuset(odd["emulator_cpuset"])
check("11-CPU invariant: all CPUs accounted for",
      pinned_odd | emulator_odd, set(range(11)))

check_raises("pool too small", domain.DomainError, lambda: domain.vcpu_plan([0, 1, 2]))

# Non-contiguous pool must raise DomainError to prevent silent CPU loss
check_raises("non-contiguous pool is refused", domain.DomainError,
             lambda: domain.vcpu_plan([0, 1, 2, 3, 4, 5, 10, 15]))

# All-duplicate pool must raise DomainError to prevent degenerate zero-vcpu plans
check_raises("all-duplicate pool is refused", domain.DomainError,
             lambda: domain.vcpu_plan([5, 5, 5, 5]))

import xml.etree.ElementTree as ET  # noqa: E402

GPU = [
    {"address": "0000:01:00.0", "domain": "0x0000", "bus": "0x01",
     "slot": "0x00", "function": "0x0", "id": "10de:2786", "description": "GPU"},
    {"address": "0000:01:00.1", "domain": "0x0000", "bus": "0x01",
     "slot": "0x00", "function": "0x1", "id": "10de:22bc", "description": "audio"},
]
NVME = {"address": "0000:03:00.0", "domain": "0x0000", "bus": "0x03",
        "slot": "0x00", "function": "0x0", "id": "144d:a808", "description": "nvme"}

xml_text = domain.domain_xml(gpu_functions=GPU, nvme=NVME, plan=plan)
root = ET.fromstring(xml_text)

check("domain name", root.findtext("name"), "Windows")
check("kvm domain", root.get("type"), "kvm")
check("vcpu count", root.findtext("vcpu"), "14")

topo = root.find("cpu/topology")
check("cores", topo.get("cores"), "7")
check("threads", topo.get("threads"), "2")

check("emulatorpin", root.find("cputune/emulatorpin").get("cpuset"), "14-15")
check("vcpupin entries", len(root.findall("cputune/vcpupin")), 14)

loader = root.find("os/loader")
check("secure boot loader", loader.text,
      "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd")
check("loader is secure", loader.get("secure"), "yes")
check("nvram template", root.find("os/nvram").get("template"),
      "/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
check("firmware autoselect absent", root.find("os").get("firmware"), None)
check("smm on", root.find("features/smm").get("state"), "on")

check("tpm model", root.find("devices/tpm").get("model"), "tpm-crb")
check("tpm version", root.find("devices/tpm/backend").get("version"), "2.0")

check("mac address", root.find("devices/interface/mac").get("address"),
      "52:54:00:48:e0:3e")
check("nic model", root.find("devices/interface/model").get("type"), "virtio")

hostdevs = root.findall("devices/hostdev")
check("three hostdevs", len(hostdevs), 3)
for hd in hostdevs:
    check("hostdev managed", hd.get("managed"), "yes")
    check("hostdev vfio driver", hd.find("driver").get("name"), "vfio")

check("s4 enabled", root.find("pm/suspend-to-disk").get("enabled"), "yes")
check("s3 disabled", root.find("pm/suspend-to-mem").get("enabled"), "no")

check("hugepages", root.find("memoryBacking/hugepages") is not None, True)
check("locked", root.find("memoryBacking/locked") is not None, True)
check("shared access", root.find("memoryBacking/access").get("mode"), "shared")

check("emulated video present", root.find("devices/video/model").get("type"), "vga")
check("vnc listens locally", root.find("devices/graphics").get("listen"), "127.0.0.1")

check("virtiofs target", root.find("devices/filesystem/target").get("dir"), "Data")

# Everything the spec forbids must be absent, checked individually so a
# failure names the offender.
check("no kvm hidden", root.find("features/kvm") is None, True)
check("no vendor_id", root.find("features/hyperv/vendor_id") is None, True)
check("no sysinfo", root.find("sysinfo") is None, True)
check("no smbios mode", root.find("os/smbios") is None, True)
check("no vBIOS override", root.find("devices/hostdev/rom") is None, True)
check("no i6300esb watchdog",
      [w.get("model") for w in root.findall("devices/watchdog")], ["itco"])

# domain_in_listing() must correctly parse virsh list --all --name output
# and reject partial matches (a domain named "Windows-LTSC-test" must not
# satisfy a query for "Windows").
LISTING = "Windows\nWindows-LTSC-test\n\n"
check("existing domain is found", domain.domain_in_listing(LISTING, "Windows"), True)
check("absent domain is not found", domain.domain_in_listing(LISTING, "Absent"), False)
check("prefix is not a match", domain.domain_in_listing("Windows-LTSC-test\n", "Windows"), False)
check("empty listing", domain.domain_in_listing("", "Windows"), False)

# `define` must refuse an existing domain unless explicitly told to replace it:
# until the cutover, "Windows" is the owner's production VM.
check_raises(
    "define refuses an existing domain",
    domain.DomainError,
    lambda: domain.guard_replace(exists=True, replace=False),
)
check("define proceeds when replacing", domain.guard_replace(exists=True, replace=True), None)
check("define proceeds when absent", domain.guard_replace(exists=False, replace=False), None)

# vm-cpu-partition.sh derives the HOST cpuset from cputune, reading the domain
# XML libvirt feeds it on stdin. It parses cputune/{vcpupin,emulatorpin,
# iothreadpin}@cpuset with ElementTree. Replicate that parse here: if the
# generated XML ever stops matching it, the CPU partitioning breaks with no
# error message at all — the hook exits 0 by design and only
# /var/log/libvirt-cpu-hook.log would show it.
pinned = set()
for tune in root.findall("cputune"):
    for tag in ("vcpupin", "emulatorpin", "iothreadpin"):
        for el in tune.findall(tag):
            for part in el.get("cpuset", "").split(","):
                if "-" in part:
                    lo, hi = part.split("-")
                    pinned |= set(range(int(lo), int(hi) + 1))
                elif part:
                    pinned.add(int(part))

check("hook sees every pinned CPU", pinned, set(range(16)))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - production domain checks passed")
