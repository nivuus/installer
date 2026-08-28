"""Precise hardware detection for the console package.

The ENGINE detects capabilities - coarse, generic, enough to decide whether
this package may be offered at all: is there an IOMMU, a discrete GPU, a
spare NVMe. It cannot delegate that, because it must answer
`requires.capabilities` before running any of this package's code.

What lives here is the precise half: which PCI functions share the GPU's
slot, which NVMe controller is free of the host's own root filesystem, which
CPUs to hand to the guest. The package refines what the engine handed it; it
does not redo it.

`_run` and `_read_int` are copied from installer/common/hardware.py rather
than imported. That duplication is deliberate: this package must run on a
Debian that has never seen the Nivuus engine, so it may not import from it.
"""
from __future__ import annotations

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


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class HardwareError(RuntimeError):
    """Raised when detection cannot answer safely and must not guess."""


# --------------------------------------------------------------------------- #
# GPU discovery (coarse)                                                      #
# --------------------------------------------------------------------------- #
def parse_gpus(raw: str) -> list[dict]:
    """Discrete GPUs, coarsely - enough for the `gpu-discrete` capability.

    Deliberately WITHOUT the slot's vendor:device ids: those are what
    vfio-pci.ids needs, and pci_slot_ids()/vfio_ids_for_slot() below derive
    them from the slot this returns, in this package's own resolve phase.

    This mirrors installer/common/hardware.py::list_gpus(), copied rather
    than imported per the module docstring - domain.py calls list_gpus()
    directly, after the reboot, on a host that may never have had the
    installer on it. Parsing is split from detection (unlike the engine's
    copy) the same way parse_pci_functions/parse_nvme_controllers already are
    in this file: it is the only way to test GPU classification without the
    right hardware attached. Reuses _clean_lspci_desc_from_nn instead of a
    second quoted-field parser, so list_gpus() below reads `lspci -nn`
    output - already this module's convention - rather than `-nnmm`.

    NOT identical output to the engine's copy: `slot`, `vendor` and
    `discrete` carry the same values (verified on real hardware), but
    `description` does not - `-nn` + _clean_lspci_desc_from_nn drops the
    bracketed [vendor:device] id text that `-nnmm` keeps (e.g. here
    "Intel Corporation AlderLake-S GT1" against the engine's
    "Intel Corporation [8086] AlderLake-S GT1 [4680]"). Harmless today: no
    caller of list_gpus() (domain.py, resolve.py, capabilities.py, the
    wizard, the portal) reads a GPU's `description`. If one starts to, this
    divergence is exactly what to check first.
    """
    gpus = []
    for line in raw.splitlines():
        # Match VGA/3D/Display controller class entries.
        if not re.search(r"(VGA compatible|3D|Display) controller", line):
            continue
        parts = line.split()
        if not parts:
            continue
        slot = parts[0]
        desc = _clean_lspci_desc_from_nn(line)
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


def list_gpus() -> list[dict]:
    """parse_gpus applied to this machine."""
    return parse_gpus(_run(["lspci", "-nn"]))


# --------------------------------------------------------------------------- #
# GPU / PCI slot functions (for VFIO passthrough)                            #
# --------------------------------------------------------------------------- #
def pci_slot_ids(slot: str) -> list[str]:
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

    `pci_slot_ids` returns vendor:device pairs, which is what vfio-pci.ids
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


def parse_nvme_controllers(raw: str) -> list[dict]:
    """NVMe controllers (PCI class 0108) from `lspci -nn -D` output.

    lsblk cannot see the passthrough disk: it is bound to vfio-pci and has no
    block device. PCI class is the only view that shows both.
    """
    out = []
    for line in raw.splitlines():
        if "[0108]" not in line:
            continue
        parts = line.split()
        if not parts or parts[0].count(":") != 2:
            continue
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]\s*(?:\(rev|$)", line)
        out.append(
            {
                "address": parts[0],
                "id": f"{m.group(1).lower()}:{m.group(2).lower()}" if m else "",
                "description": _clean_lspci_desc_from_nn(line),
            }
        )
    return sorted(out, key=lambda c: c["address"])


def _whole_disk_name(name: str, sysfs_root: str = "/sys/class/block") -> str:
    """Get the whole-disk name from a device name (partition or disk).

    Uses sysfs to detect whether the device is a partition:
    - If <sysfs_root>/<name>/partition exists, extract parent disk name
    - Otherwise, the name already is the whole disk

    `sysfs_root` defaults to the real sysfs location; tests point it at a
    fake tree so this stays testable without depending on the machine it
    runs on. Falls back to returning the name unchanged on any sysfs error
    or if the node does not exist at all.
    """
    try:
        partition_file = os.path.join(sysfs_root, name, "partition")
        if os.path.exists(partition_file):
            # It's a partition; find the parent disk
            # Resolve the symlink to get the sysfs path, then go up one level
            sysfs_path = os.path.realpath(os.path.join(sysfs_root, name))
            parent_path = os.path.dirname(sysfs_path)
            return os.path.basename(parent_path)
        else:
            # No partition file; this is already the whole disk (or the node
            # does not exist at all)
            return name
    except OSError:
        # Fall back to returning the name unchanged on any error
        return name


SYSFS_BLOCK_ENV = "NIVUUS_SYSFS_BLOCK"


def _sysfs_block_root() -> str:
    """Root of the sysfs block tree, overridable for tests.

    The tests must be able to describe a machine that is not the one running
    them - a live ISO, a host whose root cannot be traced - and a block tree
    is the one input that cannot be captured as text the way `lspci` output
    can. NIVUUS_SYSFS_BLOCK is the same kind of seam
    installer/common/progress.py already exposes through NIVUUS_PROGRESS_DIR.
    Production never sets it.
    """
    return os.environ.get(SYSFS_BLOCK_ENV) or "/sys/block"


def _device_to_pci_address(device: str,
                           sysfs_root: Optional[str] = None) -> Optional[str]:
    """Resolve a block device name to its PCI address, or None."""
    root = sysfs_root or _sysfs_block_root()
    try:
        target = os.path.realpath(os.path.join(root, device, "device", "device"))
    except OSError:
        return None
    tail = os.path.basename(target)
    return tail if tail.count(":") == 2 else None


def select_passthrough_nvme(controllers: list[dict],
                            host_addresses: Optional[set[str]],
                            wanted_address: Optional[str] = None) -> dict:
    """The NVMe controller to hand over, chosen by the OPERATOR when possible.

    `wanted_address` is the PCI address behind the `dedicated_nvme` answer.
    It DRIVES the choice; the host-root exclusion is then an assertion on
    that choice, not the way the choice is made. The inversion is not
    cosmetic - it is what makes this usable from the installer ISO, which is
    the package's primary path. On a live medium the host root is the live
    image, not a PCI disk, so `host_addresses` is None on EVERY machine and
    a selector that derives from it can only ever refuse. The operator's
    answer is the one piece of information that exists on both paths.

    `host_addresses` is therefore allowed to be None (root not traceable):
    the safety assertion simply does not apply, and must not become a
    refusal on its own.

    With no answer to go on, the old behaviour stands as a fallback:
    auto-select when exactly one candidate is unambiguous, refuse otherwise.
    The selected disk is wiped by the Windows installer, so an ambiguous
    answer must be an error, never a best effort.
    """
    if not controllers:
        raise HardwareError("no NVMe controller found (PCI class 0108)")

    known = [c["address"] for c in controllers]
    if wanted_address:
        chosen = next((c for c in controllers
                       if c["address"] == wanted_address), None)
        if chosen is None:
            raise HardwareError(
                f"the dedicated device resolves to PCI address "
                f"{wanted_address}, which is not an NVMe controller; the "
                f"NVMe controllers on this machine are: {known}")
        if host_addresses and chosen["address"] in host_addresses:
            raise HardwareError(
                f"the dedicated device ({wanted_address}) backs the host root "
                "filesystem; it cannot be handed to the guest")
        return chosen

    if not host_addresses:
        raise HardwareError(
            "host root not identified and no dedicated NVMe was named; "
            "cannot safely select a passthrough device")
    candidates = [c for c in controllers if c["address"] not in host_addresses]
    if not candidates:
        raise HardwareError(
            "every NVMe controller backs the host root; none can be passed "
            f"through: {known}"
        )
    if len(candidates) > 1:
        raise HardwareError(
            "cannot decide which NVMe to pass through, several candidates: "
            f"{[c['address'] for c in candidates]}"
        )
    return candidates[0]


def host_root_pci_addresses() -> Optional[set[str]]:
    """PCI addresses of all controllers backing the host's root filesystem.

    Returns a set of PCI addresses (e.g., {'0000:02:00.0'}), or None if the
    root device cannot be traced.

    Handles LVM by walking /sys/block/dm-X/slaves/ to find all backing devices,
    then resolves each to its PCI address. Returns None if no backing devices
    are found or if any cannot be resolved to a PCI address.
    """
    src = _run(["findmnt", "-no", "SOURCE", "/"])
    if not src:
        return None

    backing_devices = set()

    # Direct block device: try to extract the disk name from the source.
    if src.startswith("/dev/") and not src.startswith("/dev/mapper/"):
        # Handle block devices directly (e.g., /dev/nvme0n1p3, /dev/sda1)
        # Get the whole disk name (handles both partition and whole-disk names)
        disk = os.path.basename(src)
        disk = _whole_disk_name(disk)
        addr = _device_to_pci_address(disk)
        if addr:
            backing_devices.add(addr)
        else:
            return None
    else:
        # Device mapper (LVM): walk /sys/block/dm-X/slaves/ to find all backing devices
        # Extract the dm device number from the major:minor of the mapped device
        try:
            device_stat = os.stat(src)
            major = os.major(device_stat.st_rdev)
            minor = os.minor(device_stat.st_rdev)
            # Device mapper devices have major 254
            if major != 254:
                return None
            dm_path = f"/sys/block/dm-{minor}/slaves"
            if not os.path.isdir(dm_path):
                return None
            # Walk all slaves to find backing devices
            for slave in os.listdir(dm_path):
                # Each slave is a symlink to the backing device
                # E.g., nvme0n1p3, sda1, etc.
                disk = _whole_disk_name(slave)
                addr = _device_to_pci_address(disk)
                if addr:
                    backing_devices.add(addr)
                else:
                    # If any backing device cannot be resolved, fail safely
                    return None
        except (OSError, TypeError):
            return None

    return backing_devices if backing_devices else None


def resolve_passthrough_nvme(raw: str, host_addresses: Optional[set[str]],
                             wanted_address: Optional[str] = None) -> dict:
    """The passthrough NVMe, decomposed the way a <hostdev> address needs.

    Pure, so it is testable on captured lspci text.
    """
    controller = select_passthrough_nvme(parse_nvme_controllers(raw),
                                         host_addresses, wanted_address)
    functions = parse_pci_functions(raw, controller["address"])
    if not functions:
        raise HardwareError(f"cannot decompose address {controller['address']}")
    # Return the function matching the controller's address (usually function 0, but be explicit)
    matching = next((f for f in functions if f["address"] == controller["address"]), None)
    if matching is None:
        raise HardwareError(f"cannot find function for address {controller['address']}")
    return matching


def passthrough_nvme(wanted_address: Optional[str] = None) -> dict:
    """The NVMe controller to hand to the Windows guest, on this machine.

    An untraceable host root is NOT an error here any more: it is the normal
    state of a live installer medium, where the root is the live image rather
    than a PCI disk. It is passed through as None so select_passthrough_nvme
    can treat the host-root exclusion as an assertion that does not apply,
    instead of a refusal that would fire on every ISO install.
    """
    return resolve_passthrough_nvme(
        _run(["lspci", "-nn", "-D"]), host_root_pci_addresses(), wanted_address
    )


def pci_address_for_device(path: str,
                           sysfs_root: Optional[str] = None) -> str | None:
    """PCI address of the controller behind a /dev/... block device, or None.

    The wizard hands back a device PATH; passthrough needs a PCI ADDRESS, and
    nothing in the returned controller dict bridges the two. A partition is
    resolved to its whole disk first, because /sys/block only knows the latter.
    """
    name = _whole_disk_name(os.path.basename(path.rstrip("/")))
    return _device_to_pci_address(name, sysfs_root)


def vfio_ids_for_slot(slot: str) -> list[str]:
    """Every vendor:device id sharing `slot`, for vfio-pci.ids.

    Passthrough binds the WHOLE slot: a GPU and its HDMI-audio function are
    separate PCI functions in the same IOMMU group, and leaving one on the
    host driver makes the group unassignable.
    """
    return pci_slot_ids(slot)


# --------------------------------------------------------------------------- #
# CPU topology (coarse)                                                       #
# --------------------------------------------------------------------------- #
def cpu_topology() -> dict:
    """CPU topology, coarsely - enough for the `cpu-hybrid` capability.

    Deliberately WITHOUT isolcpus/nohz_full: which CPUs to isolate is only
    knowable by a package that pins vCPUs, and isolation_plan() below derives
    them from `performance_cpus` here.

    Returns {model, total_cpus, hybrid, performance_cpus}.

    Strategy (no hardcoded i9-12900K assumptions):
      * Detect hybrid (Intel P+E) layout from max CPU frequency per core.
      * performance_cpus lists the top-frequency-tier CPUs on a hybrid layout;
        empty on a uniform CPU.

    This mirrors installer/common/hardware.py::cpu_topology(), copied rather
    than imported per the module docstring - domain.py calls it directly,
    after the reboot, on a host that may never have had the installer on it.
    Not split into a separate parser: unlike list_gpus() it has no
    subprocess-text input to isolate, it reads /proc/cpuinfo and sysfs
    directly.
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


# --------------------------------------------------------------------------- #
# CPU isolation plan (for isolcpus / nohz_full)                              #
# --------------------------------------------------------------------------- #
def cpu_ranges(nums: list[int]) -> str:
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


def _validate_performance_cpus(performance: list, total: int) -> None:
    """Reject a `performance_cpus` list this module cannot trust.

    isolation_plan's output is emitted onto the kernel command line
    (nohz_full=...). The GRUB allowlist downstream
    (`^[A-Za-z0-9_.,:=/@+-]+$`) exists to stop shell injection, not to judge
    semantic validity - a range like "-1-1" passes it untouched. So this is
    the only place that can catch a malformed CPU snapshot before it reaches
    the boot chain of an installed machine, and it must refuse rather than
    silently filter: dropping the bad entries would still emit a
    plausible-looking range derived from a snapshot just proven untrustworthy,
    and the operator would never learn their machine was mis-detected.
    """
    for value in performance:
        # bool is an int subclass in Python - isinstance(True, int) is True -
        # so True must be rejected explicitly, or it would silently mean CPU 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise HardwareError(
                f"performance_cpus contains a non-integer entry: {value!r}"
            )
        if value < 0:
            raise HardwareError(
                f"performance_cpus contains a negative CPU number: {value}"
            )
        if total and value >= total:
            raise HardwareError(
                f"performance_cpus contains CPU {value} but total_cpus is {total}"
            )


def isolation_plan(cpu: dict) -> dict:
    """CPUs handed to the guest, as kernel range strings.

    Derived here rather than by the engine because only a package that pins
    vCPUs knows which cores it wants. Emitted as `nohz_full=` only, never
    `isolcpus=`: isolcpus is a BOOT-time parameter, so it would keep those
    CPUs out of the scheduler for the whole uptime and leave the host on the
    remaining cores even while the guest is shut off. The host/guest split is
    dynamic, done by vm-cpu-partition.sh from the libvirt hooks.

    ABSENT versus MALFORMED are deliberately distinguished, the way
    passthrough_nvme() already refuses to guess rather than return an empty
    dict. An absent snapshot ({}, no performance_cpus, no/zero total_cpus) is
    not an error - there is simply nothing to isolate, so this returns an
    empty plan. A PRESENT but invalid performance_cpus (a non-int entry, a
    negative CPU number, or a CPU number >= total_cpus) raises HardwareError:
    see _validate_performance_cpus for why this must refuse rather than
    filter, given where this string ends up.
    """
    performance = cpu.get("performance_cpus") or []
    total = cpu.get("total_cpus") or 0
    if performance:
        _validate_performance_cpus(performance, total)
    if cpu.get("hybrid") and performance:
        isolated = sorted(performance)
    elif total:
        isolated = [c for c in range(total) if c != 0]
    else:
        isolated = []
    ranges = cpu_ranges(isolated)
    return {"isolcpus": ranges, "nohz_full": ranges}
