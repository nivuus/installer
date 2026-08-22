"""Generic hardware detection for the Nivuus installer.

All detection is best-effort and degrades gracefully: every public function
returns plain dict/list structures (JSON-serialisable) and never raises on a
missing tool — it returns an empty/partial result instead. This keeps the web
wizard usable even on stripped-down live environments.

Used by both the web portal (to populate the wizard) and the install engine
(to compute isolcpus / vfio-pci.ids for the generic, non-hardcoded install).
"""
from __future__ import annotations

import json
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
    import os

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
    """Return discrete GPUs as passthrough candidates.

    Each entry includes the IOMMU device IDs (vendor:device) for every function
    in the PCI slot (GPU + its HDMI-audio function), which is what vfio-pci.ids
    needs. {slot, description, vendor, ids:[...], discrete}.
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
        ids = _pci_slot_ids(slot)
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
                "ids": ids,
                "discrete": discrete,
            }
        )
    return gpus


def _pci_slot_ids(slot: str) -> list[str]:
    """All vendor:device IDs sharing the GPU's slot (e.g. 10de:2786,10de:22bc).

    GPU passthrough requires binding every function in the slot to vfio-pci, so
    we gather the GPU video function plus the co-located HDMI audio function.
    """
    # slot is like "01:00.0"; the slot prefix is "01:00".
    slot_prefix = slot.rsplit(".", 1)[0]
    raw = _run(["lspci", "-nn", "-D"])
    ids: list[str] = []
    if not raw:
        return ids
    for line in raw.splitlines():
        # Lines look like: 0000:01:00.0 VGA ... [10de:2786] (rev a1)
        addr = line.split()[0]
        # Strip optional domain (0000:).
        addr_no_domain = addr.split(":", 1)[1] if addr.count(":") == 2 else addr
        if addr_no_domain.rsplit(".", 1)[0] != slot_prefix:
            continue
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
        if m:
            pair = f"{m.group(1).lower()}:{m.group(2).lower()}"
            if pair not in ids:
                ids.append(pair)
    return ids


def parse_pci_functions(raw: str, slot: str) -> list[dict]:
    """Every PCI function sharing `slot`, decomposed for libvirt <address>.

    `_pci_slot_ids` returns vendor:device pairs, which is what vfio-pci.ids
    needs. A <hostdev> needs the address instead, split into domain/bus/slot/
    function and hex-prefixed. `slot` may be "01:00.0" or "0000:01:00.0".

    Sorted by function number so generated <hostdev> entries keep a stable
    order across runs.
    """
    wanted = slot.split(":", 1)[1] if slot.count(":") == 2 else slot
    wanted_prefix = wanted.rsplit(".", 1)[0]
    found = []
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        addr = parts[0]
        if addr.count(":") != 2:
            continue
        domain, bus, rest = addr.split(":")[0], addr.split(":")[1], addr.split(":")[2]
        if f"{bus}:{rest}".rsplit(".", 1)[0] != wanted_prefix:
            continue
        dev, func = rest.split(".", 1)
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
        found.append(
            {
                "address": addr,
                "domain": f"0x{domain}",
                "bus": f"0x{bus}",
                "slot": f"0x{dev}",
                "function": f"0x{int(func, 16):x}",
                "id": f"{m.group(1).lower()}:{m.group(2).lower()}" if m else "",
                "description": _clean_lspci_desc_from_nn(line),
            }
        )
    return sorted(found, key=lambda f: f["function"])


def _clean_lspci_desc_from_nn(line: str) -> str:
    """Human label from an `lspci -nn` line: the text between ': ' and ' ['."""
    after_colon = line.split(": ", 1)[1] if ": " in line else line
    return re.sub(r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\].*$", "", after_colon).strip()


def pci_slot_functions(slot: str) -> list[dict]:
    """parse_pci_functions applied to this machine."""
    return parse_pci_functions(_run(["lspci", "-nn", "-D"]), slot)


def _clean_lspci_desc(line: str) -> str:
    # lspci -nnmm quotes fields; join vendor + device names for a readable label.
    fields = re.findall(r'"([^"]*)"', line)
    # fields ~ [class, vendor, device, ...]
    if len(fields) >= 3:
        return f"{fields[1]} {fields[2]}".strip()
    return line.strip()


# --------------------------------------------------------------------------- #
# CPU topology (for isolcpus / nohz_full)                                     #
# --------------------------------------------------------------------------- #
def cpu_topology() -> dict:
    """Detect CPU layout, computing a sensible isolation range generically.

    Returns {model, total_cpus, hybrid, performance_cpus, isolcpus, nohz_full}.

    Strategy (no hardcoded i9-12900K assumptions):
      * Detect hybrid (Intel P+E) layout from max CPU frequency per core.
      * On hybrid CPUs, isolate the performance cores (highest max freq) and
        leave efficiency cores for the host.
      * On uniform CPUs, isolate all cores except CPU 0 (kept for the host).
    """
    import os
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

    if hybrid:
        isolated = performance_cpus
    else:
        # Uniform CPU: isolate everything but CPU 0 for the host scheduler.
        isolated = [c for c in cpus if c != 0]

    isolcpus = _ranges(isolated)
    return {
        "model": model,
        "total_cpus": total,
        "hybrid": hybrid,
        "performance_cpus": performance_cpus,
        "isolcpus": isolcpus,
        "nohz_full": isolcpus,
    }


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _ranges(nums: list[int]) -> str:
    """Compress a sorted int list into a kernel range string e.g. '0-7,16'."""
    if not nums:
        return ""
    nums = sorted(set(nums))
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(parts)


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
        "passthrough_candidates": [g for g in gpus if g.get("discrete")],
    }


if __name__ == "__main__":
    print(json.dumps(detect_all(), indent=2))
