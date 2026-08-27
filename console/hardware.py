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


def _device_to_pci_address(device: str) -> Optional[str]:
    """Resolve a block device name to its PCI address, or None."""
    try:
        target = os.path.realpath(f"/sys/block/{device}/device/device")
    except OSError:
        return None
    tail = os.path.basename(target)
    return tail if tail.count(":") == 2 else None


def select_passthrough_nvme(controllers: list[dict],
                            host_addresses: set[str]) -> dict:
    """The one NVMe controller that is not backing the host's own root.

    Refuses to guess: the selected disk is wiped by the Windows installer, so
    an ambiguous answer must be an error, never a best effort.
    """
    if not controllers:
        raise HardwareError("no NVMe controller found (PCI class 0108)")
    if not host_addresses:
        raise HardwareError(
            "host root not identified; cannot safely select a passthrough device"
        )
    candidates = [c for c in controllers if c["address"] not in host_addresses]
    if not candidates:
        raise HardwareError(
            "every NVMe controller backs the host root; none can be passed "
            f"through: {[c['address'] for c in controllers]}"
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


def resolve_passthrough_nvme(raw: str, host_addresses: set[str]) -> dict:
    """The passthrough NVMe, decomposed the way a <hostdev> address needs.

    Pure, so it is testable on captured lspci text.
    """
    controller = select_passthrough_nvme(parse_nvme_controllers(raw), host_addresses)
    functions = parse_pci_functions(raw, controller["address"])
    if not functions:
        raise HardwareError(f"cannot decompose address {controller['address']}")
    # Return the function matching the controller's address (usually function 0, but be explicit)
    matching = next((f for f in functions if f["address"] == controller["address"]), None)
    if matching is None:
        raise HardwareError(f"cannot find function for address {controller['address']}")
    return matching


def passthrough_nvme() -> dict:
    """The NVMe controller to hand to the Windows guest, on this machine."""
    host_addrs = host_root_pci_addresses()
    if host_addrs is None:
        raise HardwareError(
            "cannot identify host root filesystem; refusing to guess passthrough device"
        )
    return resolve_passthrough_nvme(
        _run(["lspci", "-nn", "-D"]), host_addrs
    )


def pci_address_for_device(path: str) -> str | None:
    """PCI address of the controller behind a /dev/... block device, or None.

    The wizard hands back a device PATH; passthrough needs a PCI ADDRESS, and
    nothing in the returned controller dict bridges the two. A partition is
    resolved to its whole disk first, because /sys/block only knows the latter.
    """
    name = _whole_disk_name(os.path.basename(path.rstrip("/")))
    return _device_to_pci_address(name)


def vfio_ids_for_slot(slot: str) -> list[str]:
    """Every vendor:device id sharing `slot`, for vfio-pci.ids.

    Passthrough binds the WHOLE slot: a GPU and its HDMI-audio function are
    separate PCI functions in the same IOMMU group, and leaving one on the
    host driver makes the group unassignable.
    """
    return pci_slot_ids(slot)


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
