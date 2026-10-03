"""A minimal ISO 9660 image for the suites: system area, one primary volume
descriptor claiming `blocks` logical blocks of 2048 bytes, then the volume.

Never the real ~4.8 GB medium - a few sectors are enough for what the hooks
read: the descriptor's identifier, its volume space size and its logical
block size (guest_steps.iso_volume_size), nothing past that.
"""
from __future__ import annotations

SECTOR = 2048
SYSTEM_AREA = 16 * SECTOR


def fake_iso(blocks: int = 20, fill: bytes = b"\xaa", *,
             boot_record_first: bool = False) -> bytes:
    """`blocks` * 2048 bytes carrying a valid primary volume descriptor.

    `boot_record_first` puts an El Torito style boot record (type 0) ahead
    of the primary descriptor: the standard does not make the primary the
    first of the set, and a reader that only looks at sector 16 misses it.
    """
    if blocks < 19:
        raise ValueError("an image needs the system area, a descriptor, a "
                         "possible boot record and a terminator: at least "
                         "19 blocks")
    boot = bytearray(SECTOR)
    boot[0] = 0
    boot[1:6] = b"CD001"
    boot[6] = 1
    boot[7:30] = b"EL TORITO SPECIFICATION".ljust(23, b"\x00")
    pvd = bytearray(SECTOR)
    pvd[0] = 1                                   # type: primary
    pvd[1:6] = b"CD001"                          # identifier
    pvd[6] = 1                                   # version
    pvd[80:84] = blocks.to_bytes(4, "little")    # volume space size, LE
    pvd[84:88] = blocks.to_bytes(4, "big")       # ... and BE
    pvd[128:130] = SECTOR.to_bytes(2, "little")  # logical block size, LE
    pvd[130:132] = SECTOR.to_bytes(2, "big")     # ... and BE
    terminator = bytearray(SECTOR)
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1
    body = (fill * (SECTOR // len(fill) + 1))[:SECTOR] * (blocks - 19)
    descriptors = (bytes(boot) + bytes(pvd)) if boot_record_first \
        else (bytes(pvd) + bytes(boot))
    return bytes(SYSTEM_AREA * b"\x00") + descriptors + bytes(terminator) + body
