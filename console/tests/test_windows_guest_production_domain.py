#!/usr/bin/env python3
"""Tests for the generated production domain (sub-project C).

Run: python3 scripts/tests/test_windows_guest_production_domain.py
"""
import contextlib
import io
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))
sys.path.insert(0, str(REPO / "console"))

import domain  # noqa: E402
import hardware  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    # exc_type may be a single exception class or a tuple of classes (`except
    # exc_type` accepts both) -- so the failure message must not assume
    # `.__name__` exists, which a tuple does not have.
    want = " or ".join(t.__name__ for t in exc_type) if isinstance(exc_type, tuple) \
        else exc_type.__name__
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {want}")
        return
    failures.append(f"{label}: did not raise {want}")


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

# Le partage unique « Data » exposait /media/data EN ENTIER : quatre partages
# cibles l ont remplace le 2026-08-26.
check("les tags de partage",
      [t.get("dir") for t in root.findall("devices/filesystem/target")],
      ["Downloads", "Games", "Console", "ConsoleSave"])

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

# `define` must also refuse when the target varstore file already exists:
# libvirt only populates <nvram> from its Secure Boot template on first
# creation, so reusing an existing (pre-Secure-Boot) varstore would silently
# boot the guest with Secure Boot disabled.
check_raises(
    "define refuses an existing varstore",
    domain.DomainError,
    lambda: domain.guard_fresh_varstore(exists=True),
)
check("define proceeds when varstore is absent",
      domain.guard_fresh_varstore(exists=False), None)

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

# Quatre partages CIBLES, jamais la racine : virtiofsd tourne en root et le mode
# passthrough ne porte aucun filet de permissions, donc ce qui est expose l est
# sans filtre. Exposer /media/data en entier mettait 17 To a portee d un incident
# dans un invite ouvert au streaming.
import re as _re
_srcs = _re.findall(r"<source dir='([^']+)'/>", xml_text)
check("quatre partages", len(_srcs), 4)
check("aucun partage n expose une racine",
      any(s in ("/media/data", "/media/backup") for s in _srcs), False)
check("tous les partages sont des sous-dossiers",
      all(s.count("/") >= 3 for s in _srcs), True)

# Prove the test's own sys.path reaches console/hardware.py the same way
# domain.py's lazy `from hardware import HardwareError` does (Step 3) - this
# is what lets the assertion below reason about the exception domain.py
# raises for hardware mismatches without re-importing installer/.
check("HardwareError vient bien de console/hardware.py",
      hardware.HardwareError.__module__, "hardware")

# Everything above calls domain_xml() with explicit arguments and never
# touches hardware detection. This is the one assertion that actually calls
# main(), which is why phase 2a's wiring break stayed invisible for a whole
# phase: no suite reached it.
def _domain_main_xml():
    argv = sys.argv
    sys.argv = ["domain.py", "xml"]
    try:
        return domain.main()
    finally:
        sys.argv = argv


# main() must now reach hardware detection instead of dying at import. Catch
# WIDE here on purpose, then decide: a signature drift in any of the five
# console/hardware.py entry points build_domain_xml() calls (list_gpus,
# cpu_topology, passthrough_nvme, pci_slot_functions, HardwareError itself)
# raises a TypeError, not an ImportError/AttributeError - and a signature
# drift is exactly the kind of wiring break this split is most exposed to.
# A narrow except that only watches Import/AttributeError would let a
# TypeError crash this whole file with a raw traceback instead of a named
# failure line - the one thing this test exists to avoid, on the failure
# mode it is most likely to hit. So: catch broadly, then classify. A
# HardwareError (or a DomainError, if main() is ever changed to let one
# through - today it is not, both are caught inside main()'s own
# try/except and turned into return code 1, so this branch is currently
# unreachable but kept as the documented, deliberate exception for a
# legitimate refusal) means the wiring works and this particular machine's
# hardware does not match what build_domain_xml() wants - a real, expected
# outcome, not a bug. Anything else is a wiring break and must fail loud,
# named by type and message, not swallowed into a bare traceback.
_stdout = io.StringIO()
try:
    with contextlib.redirect_stdout(_stdout):
        _rc = _domain_main_xml()
except (domain.DomainError, hardware.HardwareError) as exc:
    print("main() a refuse pour une raison materielle legitime: "
          f"{type(exc).__name__}: {exc}")
except Exception as exc:  # noqa: BLE001
    failures.append(
        f"main() ne casse plus au cablage: raised {type(exc).__name__}: {exc}"
    )
else:
    check("main() ne casse plus au cablage (code de retour connu)",
          _rc in (0, 1), True)
    if _rc == 0:
        print("main() a produit du XML reel sur ce matos "
              f"({len(_stdout.getvalue())} caracteres)")
    else:
        print("main() a refuse pour une raison materielle (code 1)")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - production domain checks passed")
