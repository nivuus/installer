"""Inspection of the Windows installation medium.

The edition is read from install.wim's own metadata, never assumed: the LTSC
2024 medium carries three editions - Enterprise LTSC, IoT Enterprise LTSC and
IoT Enterprise Subscription LTSC - and only IoTEnterpriseS is the target. A
medium that does not carry it is a hard error, never a silent fallback.

The WIM header points at an uncompressed UTF-16LE XML blob, so the whole thing
is readable with the standard library: no wimtools, no new apt dependency, and
a parser that is testable without a medium.
"""
from __future__ import annotations

import os
import struct
import subprocess
import xml.etree.ElementTree as ET

TARGET_BUILD = "26100"
TARGET_EDITION_ID = "IoTEnterpriseS"
MOUNT_DIR = "/run/nivuus-winmedia"

WIM_MAGIC = b"MSWIM\x00\x00\x00"
# rhXmlData in the WIM header: 7-byte size, 1 flag byte, then a u64 offset.
XML_RESHDR_OFFSET = 0x48


class MediaError(RuntimeError):
    """Raised when the Windows medium is not the expected LTSC release."""


def read_wim_xml(wim_path: str) -> str:
    """Return the XML metadata blob stored at the end of a WIM archive."""
    with open(wim_path, "rb") as fh:
        header = fh.read(0x60)
        if header[:8] != WIM_MAGIC:
            raise MediaError(f"{wim_path} is not a WIM archive")
        reshdr = header[XML_RESHDR_OFFSET:XML_RESHDR_OFFSET + 24]
        size = int.from_bytes(reshdr[0:7], "little")
        offset = struct.unpack_from("<Q", reshdr, 8)[0]
        fh.seek(offset)
        blob = fh.read(size)
    if len(blob) != size:
        raise MediaError(f"{wim_path} is truncated: XML metadata unreadable")
    return blob.decode("utf-16-le")


def parse_wim_xml(xml_text: str) -> list[dict]:
    """Parse the WIM metadata into one record per image."""
    try:
        root = ET.fromstring(xml_text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise MediaError(f"unreadable WIM metadata: {exc}") from exc
    images = []
    for img in root.findall("IMAGE"):
        images.append({
            "index": int(img.get("INDEX", "0")),
            "name": (img.findtext("NAME") or "").strip(),
            "edition_id": (img.findtext("WINDOWS/EDITIONID") or "").strip(),
            "build": (img.findtext("WINDOWS/VERSION/BUILD") or "").strip(),
            "languages": [e.text for e in img.findall("WINDOWS/LANGUAGES/LANGUAGE")],
        })
    return images


def _describe(images: list[dict]) -> str:
    return ", ".join(
        f"#{i['index']} {i.get('name', '?')} ({i.get('edition_id', '?')})"
        for i in images
    )


def select_ltsc_image(images: list[dict], image_name: str | None = None) -> dict:
    """Return the IoT Enterprise LTSC image, or raise naming what was found."""
    if not images:
        raise MediaError("no image found in install.wim - is this a Windows medium?")
    if image_name is not None:
        candidates = [i for i in images if i.get("name") == image_name]
        if not candidates:
            raise MediaError(
                f"no image named {image_name!r} on this medium; found: "
                + _describe(images)
            )
    else:
        # Edition ID, not the display name: "IoT Enterprise Subscription LTSC"
        # reads like the target but is IoTEnterpriseSK, which needs a
        # subscription the purchased key does not carry.
        candidates = [i for i in images
                      if i.get("edition_id") == TARGET_EDITION_ID]
        if not candidates:
            raise MediaError(
                f"no {TARGET_EDITION_ID} image on this medium; found: "
                + _describe(images)
            )
    if len(candidates) > 1:
        raise MediaError(
            "several matching images, pick one with --image-name: "
            + _describe(candidates)
        )
    chosen = candidates[0]
    build = chosen.get("build", "?")
    if build != TARGET_BUILD:
        raise MediaError(
            f"image {chosen.get('name')!r} is build {build}, expected "
            f"{TARGET_BUILD} (Windows 11 24H2) - HDR needs the 24H2 base"
        )
    return chosen


def inspect_iso(iso_path: str, mount_dir: str = MOUNT_DIR,
                image_name: str | None = None) -> dict:
    """Loop-mount the ISO read-only and return its target image record."""
    if os.geteuid() != 0:
        raise MediaError("inspecting the medium requires root (loop mount)")
    os.makedirs(mount_dir, exist_ok=True)
    subprocess.run(["mount", "-o", "loop,ro", iso_path, mount_dir], check=True)
    try:
        wim = os.path.join(mount_dir, "sources", "install.wim")
        if not os.path.exists(wim):
            esd = os.path.join(mount_dir, "sources", "install.esd")
            if os.path.exists(esd):
                raise MediaError(
                    "this medium ships sources/install.esd (retail image); the "
                    "volume LTSC medium ships sources/install.wim"
                )
            raise MediaError(f"no sources/install.wim in {iso_path}")
        return select_ltsc_image(parse_wim_xml(read_wim_xml(wim)), image_name)
    finally:
        subprocess.run(["umount", mount_dir], check=False)
