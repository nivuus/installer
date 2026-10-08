"""Which physical NVMe the console's domain passes through, and which one
the operator gave it - the two halves of "is the domain already the right one".

Split out of guest_steps.py, which only consumes these answers.

THE DISK DISAPPEARS FROM THE HOST AT THE FIRST REBOOT. The `dedicated_nvme`
answer is a block-device path (/dev/nvme1n1), and the kernel command line
resolve.py emits binds that very disk to vfio-pci - from then on no
/sys/block entry describes it, so pci_address_for_device() answers None for
the one disk that matters (measured on the production host 2026-10-08:
`lspci -nnk -d ::0108` shows 0000:03:00.0 bound to vfio-pci, /sys/block
lists only dm-0, nvme0n1, sda, sdb). Every activate after the install runs in that
state, so an identity read only from sysfs made domain_defined() answer
"not done" on every replay of a working console: `define --replace` was
always the next step.

resolve.py is the one phase that still sees the disk, so it records the
address it resolved as a FACT (installer/packages/facts.py) next to the
disk's size, and dedicated_nvme_identity() reads it back - only when sysfs
is silent ("now beats then", the facts module's own precedence rule), and
only for the device it was measured for, so a later change of answer can
never be vouched for by a stale address.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable, Mapping

HERE = Path(__file__).resolve().parent

# The fact resolve.py emits: {"device": <the answer it resolved>,
# "address": <that device's PCI address>}. The device is recorded WITH the
# address so the fact can only ever speak for the answer it was measured
# from - see recorded_identity().
FACT_KEY = "dedicated_nvme_pci"

# --- what actually identifies the domain: its hostdevs' HOST pci addresses -#
# Matches the host address libvirt keeps verbatim inside <hostdev><source>
# (measured on the running production domain, 2026-08-28: `virsh dumpxml
# Windows` shows `<source><address domain='0x0000' bus='0x03' slot='0x00'
# function='0x0'/></source>` for the NVMe hostdev, exactly the fields
# domain.xml.j2 renders, in the same order, no extra attribute). This is
# NOT the same element as the GUEST-side bus position libvirt also stamps as
# a sibling <address type='pci' .../> right after <alias> - that one only
# says where the device sits on the VIRTUAL bus, so it changes across
# defines even when the PHYSICAL device passed through does not, and must
# never be read as identity. Restricting the search to the text inside
# <hostdev>...</hostdev> keeps the two apart without depending on attribute
# order elsewhere in the document.
_HOSTDEV_BLOCK_RE = re.compile(r"<hostdev\b.*?</hostdev>", re.DOTALL)
_HOSTDEV_SOURCE_ADDR_RE = re.compile(
    r"<source>\s*<address\s+domain=['\"]0x([0-9a-fA-F]+)['\"]\s+"
    r"bus=['\"]0x([0-9a-fA-F]+)['\"]\s+slot=['\"]0x([0-9a-fA-F]+)['\"]\s+"
    r"function=['\"]0x([0-9a-fA-F]+)['\"]", re.DOTALL)
_PCI_ADDRESS_RE = re.compile(
    r"([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9a-fA-F]+)\.([0-9a-fA-F]+)")


def _normalize_pci_address(groups: tuple[str, str, str, str]) -> str:
    """(domain, bus, slot, function) hex strings -> 'dddd:bb:ss.f', lower
    case, canonical width - so a '0x01' from the XML and a '1' from sysfs
    compare equal instead of failing on formatting alone."""
    domain, bus, slot, func = (int(part, 16) for part in groups)
    return f"{domain:04x}:{bus:02x}:{slot:02x}.{func:x}"


def hostdev_source_addresses(xml: str) -> set[str]:
    """Every HOST pci address a <hostdev> in `xml` passes through.

    Pure string parsing, no libvirt call: `xml` is whatever defined_xml()
    already read via `virsh dumpxml`. Restricted to <hostdev> blocks (see
    the comment above _HOSTDEV_BLOCK_RE) so the guest-side bus position
    libvirt also stamps on the same element is never mistaken for the
    physical device identity.
    """
    out = set()
    for block in _HOSTDEV_BLOCK_RE.findall(xml):
        match = _HOSTDEV_SOURCE_ADDR_RE.search(block)
        if match:
            out.add(_normalize_pci_address(match.groups()))
    return out


def domain_matches_disk(xml: str, disk: str, *,
                        pci_address_of: Callable[[str], str | None] | None = None,
                        hw: Mapping[str, object] | None = None) -> bool:
    """Is `disk` the SAME physical device `xml` actually passes through?

    ISO paths alone cannot answer this - see domain_defined()'s own
    docstring for why: they are FIXED paths under the workdir, unchanged by
    which physical disk was selected, so a domain built for a PREVIOUS
    'dedicated_nvme' answer would satisfy the media check forever while
    still wiring up the OLD disk to the guest.

    `pci_address_of` defaults to console.hardware.pci_address_for_device (a
    pure /sys/block read, imported lazily - see guest_steps._sysfs_size for
    the same convention and the same reason). ANY resolution failure - an
    unrecognised device path, a symlink sysfs cannot walk - reads as "no
    match", never as "cannot tell so assume yes": the module's own WHEN IN
    DOUBT rule (see the module docstring) applies here exactly as it does to
    the build fingerprint.

    THAT FOLDING IS RIGHT HERE AND WRONG ELSEWHERE, which is why the
    resolution now lives in disk_pci_identity() instead of inline. This
    predicate answers "is the domain ALREADY the one we want" - for it,
    "cannot tell" and "does not match" both mean "not done", and collapsing
    them is safe. refuse_implicit_wipe() asks the OPPOSITE question, "is
    there something to lose", where the two answers are opposites: a disk
    proven to be a different one is safe to erase, a disk whose identity
    cannot be established is not. A caller that needs the distinction must
    call disk_pci_identity() and read None for itself.

    `hw` carries what resolve recorded (see dedicated_nvme_identity): on a
    host where the disk is already bound to vfio-pci - every activate after
    the install - it is the only source that can name the disk at all.
    """
    address = dedicated_nvme_identity(disk, hw, pci_address_of=pci_address_of)
    if address is None:
        return False
    return address in hostdev_source_addresses(xml)


def disk_pci_identity(disk: str, *,
                      pci_address_of: Callable[[str], str | None] | None = None
                      ) -> str | None:
    """`disk`'s host PCI address in canonical form, or None if unknowable.

    None is not "no": it is "this host cannot say", and the two must never
    be conflated by a caller for whom they differ (see domain_matches_disk
    above, and refuse_implicit_wipe below, which read the same fact in
    opposite directions).

    None IS THE NORMAL ANSWER FOR THE CONSOLE'S OWN DISK, and that is the
    whole reason this function is named and separate. The default resolver
    reads /sys/block; the dedicated NVMe is bound to vfio-pci - which is the
    POINT of the passthrough, not an accident - and a disk bound to vfio-pci
    exposes no block device to the host at all. Measured on the production
    host 2026-09-05: /sys/block lists only the host's own nvme0n1, and
    pci_address_for_device('/dev/nvme1n1') returns None while `virsh dumpxml
    Windows` shows that very disk passed through at 0000:03:00.0. So on the
    one machine this module exists to serve, this function answers None for
    the one disk that matters.
    """
    resolver = pci_address_of or _disk_pci_address
    address = resolver(disk)
    if not address:
        return None
    match = _PCI_ADDRESS_RE.fullmatch(address)
    if not match:
        return None
    return _normalize_pci_address(match.groups())


def _disk_pci_address(disk: str) -> str | None:
    """console.hardware.pci_address_for_device, imported only when needed -
    same lazy-import convention as guest_steps._sysfs_size (pure /sys/block read,
    no subprocess, no dependency this module cannot promise a target has)."""
    sys.path.insert(0, str(HERE))
    from hardware import pci_address_for_device  # noqa: PLC0415

    return pci_address_for_device(disk)



def recorded_identity(disk: str, hw: Mapping[str, object] | None) -> str | None:
    """The address resolve.py recorded for `disk`, or None.

    None when there is no fact, when it is malformed, or when it was
    measured for ANOTHER device: an answer changed after the install has no
    recorded address, and "cannot tell" is the honest reading of that.
    """
    fact = (hw or {}).get(FACT_KEY)
    if not isinstance(fact, Mapping) or fact.get("device") != disk:
        return None
    match = _PCI_ADDRESS_RE.fullmatch(str(fact.get("address") or ""))
    return _normalize_pci_address(match.groups()) if match else None


def dedicated_nvme_identity(disk: str, hw: Mapping[str, object] | None, *,
                            pci_address_of: Callable[[str], str | None] | None = None
                            ) -> str | None:
    """`disk`'s PCI address: live from sysfs, else as resolve recorded it.

    Still None when neither source knows - the caller decides what "cannot
    tell" means for its own question (see domain_matches_disk and
    guest_steps.refuse_implicit_wipe, which read it in opposite directions).
    """
    live = disk_pci_identity(disk, pci_address_of=pci_address_of)
    return live if live is not None else recorded_identity(disk, hw)
