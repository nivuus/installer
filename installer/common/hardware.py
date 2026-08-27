"""Generic hardware detection for the Nivuus installer.

All detection is best-effort and degrades gracefully: every public function
returns plain dict/list structures (JSON-serialisable) and never raises on a
missing tool — it returns an empty/partial result instead. This keeps the web
wizard usable even on stripped-down live environments.

This module is deliberately COARSE: it is what the engine uses to answer a
package manifest's `requires.capabilities` before running any of that
package's code, so it cannot delegate to a package. Anything more precise
(which PCI functions share a slot, which NVMe controller is free of the
host's root filesystem, which CPUs to isolate) lives in the console
package's own console/hardware.py, resolved in that package's `resolve`
phase.

Used by both the web portal (to populate the wizard) and the install engine
(to answer `requires.capabilities`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional


def _run(cmd: list[str], timeout: int = 10) -> str:
    """Run a command, return stdout (stripped). Empty string on any failure."""
    if not shutil.which(cmd[0]):
        return ""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


# --------------------------------------------------------------------------- #
# Block devices                                                               #
# --------------------------------------------------------------------------- #
def list_disks() -> list[dict]:
    """Return whole disks (type=disk) suitable as install targets.

    Each entry: {name, path, size, size_bytes, model, rotational, removable}.
    USB sticks / loop / ram devices are flagged so the UI can warn or hide them.
    """
    raw = _run(
        [
            "lsblk", "-J", "-b", "-d", "-o",
            "NAME,SIZE,MODEL,TYPE,ROTA,RM,TRAN",
        ]
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = dev.get("name", "")
        # Skip pseudo/removable-non-target devices: loopbacks, ramdisks, optical,
        # zram, and floppies (fd0) — none are valid install targets.
        if name.startswith(("loop", "ram", "sr", "zram", "fd")):
            continue
        size_bytes = int(dev.get("size") or 0)
        disks.append(
            {
                "name": name,
                "path": f"/dev/{name}",
                "size_bytes": size_bytes,
                "size": _human_size(size_bytes),
                "model": (dev.get("model") or "").strip() or "Unknown",
                "rotational": bool(int(dev.get("rota") or 0)),
                "removable": bool(int(dev.get("rm") or 0)),
                "transport": (dev.get("tran") or "").strip(),
            }
        )
    return disks


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


# --------------------------------------------------------------------------- #
# Network interfaces                                                          #
# --------------------------------------------------------------------------- #
def list_ethernet() -> list[dict]:
    """Return wired interfaces: {name, mac, state, carrier}."""
    raw = _run(["ip", "-j", "link", "show"])
    if not raw:
        return []
    try:
        links = json.loads(raw)
    except json.JSONDecodeError:
        return []

    result = []
    for link in links:
        name = link.get("ifname", "")
        if name == "lo" or name.startswith(("veth", "docker", "br-", "vnet", "ppp")):
            continue
        # Wireless interfaces are reported separately by list_wifi().
        if _is_wireless(name):
            continue
        result.append(
            {
                "name": name,
                "mac": link.get("address", ""),
                "state": link.get("operstate", "unknown"),
                "carrier": "LOWER_UP" in (link.get("flags") or []),
            }
        )
    return result


def _is_wireless(iface: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{iface}/wireless") or os.path.exists(
        f"/sys/class/net/{iface}/phy80211"
    )


def first_wired_with_carrier() -> Optional[str]:
    """Best Ethernet interface for the fallback portal: prefer one with a cable."""
    eths = list_ethernet()
    for e in eths:
        if e["carrier"]:
            return e["name"]
    return eths[0]["name"] if eths else None


# --------------------------------------------------------------------------- #
# WiFi (AP capability)                                                        #
# --------------------------------------------------------------------------- #
def list_wifi() -> list[dict]:
    """Return WiFi interfaces with AP-capability flag.

    {name, phy, ap_capable}. ap_capable drives hotspot interface selection.
    """
    dev_out = _run(["iw", "dev"])
    if not dev_out:
        return []

    # Parse `iw dev`: blocks of "phy#N" then "Interface <name>".
    interfaces: list[dict] = []
    current_phy = None
    for line in dev_out.splitlines():
        line = line.strip()
        m = re.match(r"phy#(\d+)", line)
        if m:
            current_phy = f"phy{m.group(1)}"
            continue
        m = re.match(r"Interface\s+(\S+)", line)
        if m:
            interfaces.append({"name": m.group(1), "phy": current_phy})

    ap_phys = _ap_capable_phys()
    for iface in interfaces:
        iface["ap_capable"] = iface["phy"] in ap_phys
    return interfaces


def _ap_capable_phys() -> set[str]:
    """Set of phyN identifiers whose supported interface modes include 'AP'."""
    out = _run(["iw", "list"])
    if not out:
        return set()

    ap_phys: set[str] = set()
    current_phy = None
    in_modes = False
    for line in out.splitlines():
        stripped = line.strip()
        m = re.match(r"Wiphy\s+(phy\d+)", stripped)
        if m:
            current_phy = m.group(1)
            in_modes = False
            continue
        if "Supported interface modes" in stripped:
            in_modes = True
            continue
        if in_modes:
            if stripped.startswith("*"):
                if stripped.lstrip("* ").strip() == "AP" and current_phy:
                    ap_phys.add(current_phy)
            else:
                in_modes = False
    return ap_phys


def first_ap_interface(preferred: Optional[list[str]] = None) -> Optional[str]:
    """Pick a WiFi interface usable as an access point.

    Honours `preferred` names first (e.g. known-good adapters), then any
    AP-capable interface.
    """
    wifi = [w for w in list_wifi() if w.get("ap_capable")]
    if not wifi:
        return None
    if preferred:
        for name in preferred:
            for w in wifi:
                if w["name"] == name:
                    return name
    return wifi[0]["name"]


# --------------------------------------------------------------------------- #
# GPU (for VFIO passthrough)                                                  #
# --------------------------------------------------------------------------- #
def list_gpus() -> list[dict]:
    """Discrete GPUs, coarsely - enough for the `gpu-discrete` capability.

    Deliberately WITHOUT the slot's vendor:device ids: those are what
    vfio-pci.ids needs, and deriving them is a passthrough package's job, in
    its resolve phase. The engine only has to answer "is there a discrete GPU
    here" before it runs any package code. See console/hardware.py.
    """
    raw = _run(["lspci", "-nnmm"])
    if not raw:
        return []

    gpus = []
    for line in raw.splitlines():
        # Match VGA/3D/Display controller class entries.
        if not re.search(r'"(VGA compatible|3D|Display) controller', line):
            continue
        slot = line.split()[0]
        desc = _clean_lspci_desc(line)
        low = desc.lower()
        # Word boundaries matter: "Corporation" contains the substring "ati".
        if "nvidia" in low:
            vendor = "nvidia"
        elif re.search(r"\b(amd|ati|radeon)\b|advanced micro devices", low):
            vendor = "amd"
        elif "intel" in low:
            vendor = "intel"
        else:
            vendor = "other"
        # Treat Intel iGPU as non-discrete (usually the host display).
        discrete = vendor in ("nvidia", "amd")
        gpus.append(
            {
                "slot": slot,
                "description": desc,
                "vendor": vendor,
                "discrete": discrete,
            }
        )
    return gpus


def _clean_lspci_desc(line: str) -> str:
    # lspci -nnmm quotes fields; join vendor + device names for a readable label.
    fields = re.findall(r'"([^"]*)"', line)
    # fields ~ [class, vendor, device, ...]
    if len(fields) >= 3:
        return f"{fields[1]} {fields[2]}".strip()
    return line.strip()


# --------------------------------------------------------------------------- #
# Platform capabilities                                                       #
# --------------------------------------------------------------------------- #
# Firmware tables that advertise an IOMMU: DMAR is Intel VT-d, IVRS is AMD-Vi.
IOMMU_TABLES = {"DMAR": "intel", "IVRS": "amd"}


def iommu_support(acpi_dir: str = "/sys/firmware/acpi/tables") -> dict:
    """Report whether the PLATFORM has an IOMMU, not whether it is enabled.

    The live installer boots without intel_iommu=on - turning it on is exactly
    what a passthrough package asks the engine to add to the kernel command
    line. So a check based on /sys/kernel/iommu_groups would answer "no" on
    every machine that is in fact capable, and no such package would ever be
    offered. The firmware tables answer the right question: they are present
    whenever the chipset advertises an IOMMU, whatever the kernel was told.

    `active` is reported separately, for diagnostics only. Never gate on it.
    """
    vendor = ""
    try:
        present = set(os.listdir(acpi_dir))
    except OSError:
        present = set()
    for table, table_vendor in IOMMU_TABLES.items():
        if table in present:
            vendor = table_vendor
            break
    active = False
    try:
        active = bool(os.listdir("/sys/kernel/iommu_groups"))
    except OSError:
        pass
    return {"supported": bool(vendor), "vendor": vendor, "active": active}


# --------------------------------------------------------------------------- #
# CPU topology (for isolcpus / nohz_full)                                     #
# --------------------------------------------------------------------------- #
def cpu_topology() -> dict:
    """CPU topology, coarsely - enough for the `cpu-hybrid` capability.

    Deliberately WITHOUT isolcpus/nohz_full: which CPUs to isolate is only
    knowable by a package that pins vCPUs, and it derives them from
    `performance_cpus` here. See console/hardware.py::isolation_plan.

    Returns {model, total_cpus, hybrid, performance_cpus}.

    Strategy (no hardcoded i9-12900K assumptions):
      * Detect hybrid (Intel P+E) layout from max CPU frequency per core.
      * performance_cpus lists the top-frequency-tier CPUs on a hybrid layout;
        empty on a uniform CPU.
    """
    import glob

    # /proc/cpuinfo is locale-independent (lscpu output is translated).
    model = ""
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    cpu_dirs = sorted(
        glob.glob("/sys/devices/system/cpu/cpu[0-9]*"),
        key=lambda p: int(re.search(r"cpu(\d+)", p).group(1)),
    )
    cpus = [int(re.search(r"cpu(\d+)", p).group(1)) for p in cpu_dirs]
    total = len(cpus)

    freqs: dict[int, int] = {}
    for cpu in cpus:
        f = _read_int(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq")
        if f:
            freqs[cpu] = f

    hybrid = False
    performance_cpus: list[int] = []
    if freqs and len(set(freqs.values())) > 1:
        # Hybrid layout: performance cores are those at the top frequency tier.
        top_freq = max(freqs.values())
        # Allow a small tolerance band (within 5%) for the "performance" tier.
        threshold = top_freq * 0.95
        performance_cpus = sorted(c for c, f in freqs.items() if f >= threshold)
        hybrid = 0 < len(performance_cpus) < total

    return {
        "model": model,
        "total_cpus": total,
        "hybrid": hybrid,
        "performance_cpus": performance_cpus,
    }


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def memory_total_mib(meminfo_path: str = "/proc/meminfo") -> int:
    """Host RAM in MiB, from /proc/meminfo's `MemTotal`. 0 on any failure.

    Coarse and generic, like the rest of this module: host RAM is not
    specific to any one package (the console package uses it to size the
    guest's hugepage budget, but any package might want it), so it belongs
    on the engine side rather than being detected inside one package.

    `meminfo_path` is overridable so tests can point this at a fake file
    instead of the real `/proc/meminfo` - the same convention `iommu_support`
    uses for its ACPI table directory.

    Fail-open like every other function in this module: a missing or
    unreadable file, a missing `MemTotal` line, or a non-numeric value all
    yield 0, never an exception - one undetectable figure must not break the
    wizard. The kernel reports `MemTotal` in kB (labelled "kB" but actually
    KiB); divided by 1024 for MiB, floor rounding.
    """
    try:
        with open(meminfo_path) as fh:
            for line in fh:
                if not line.startswith("MemTotal:"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) // 1024
                return 0
    except OSError:
        pass
    return 0


# --------------------------------------------------------------------------- #
# Aggregate snapshot for the wizard                                           #
# --------------------------------------------------------------------------- #
def detect_all() -> dict:
    """One-shot hardware snapshot for the web wizard."""
    gpus = list_gpus()
    return {
        "disks": list_disks(),
        "ethernet": list_ethernet(),
        "wifi": list_wifi(),
        "gpus": gpus,
        "cpu": cpu_topology(),
        "iommu": iommu_support(),
        "memory_mib": memory_total_mib(),
        "passthrough_candidates": [g for g in gpus if g.get("discrete")],
    }


if __name__ == "__main__":
    print(json.dumps(detect_all(), indent=2))
