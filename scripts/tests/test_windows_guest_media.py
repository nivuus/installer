#!/usr/bin/env python3
"""Tests for Windows installation medium inspection.

The fixture is the real XML metadata of
en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso, trimmed of its
file counters: three LTSC editions, only one of which is the target.
Run: python3 scripts/tests/test_windows_guest_media.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import media  # noqa: E402


def image(index, name, edition, build="26100"):
    return (
        f'<IMAGE INDEX="{index}"><WINDOWS><ARCH>9</ARCH>'
        f"<EDITIONID>{edition}</EDITIONID>"
        "<INSTALLATIONTYPE>Client</INSTALLATIONTYPE>"
        "<LANGUAGES><LANGUAGE>en-US</LANGUAGE><DEFAULT>en-US</DEFAULT></LANGUAGES>"
        f"<VERSION><MAJOR>10</MAJOR><MINOR>0</MINOR><BUILD>{build}</BUILD>"
        "<SPBUILD>1742</SPBUILD></VERSION><SYSTEMROOT>WINDOWS</SYSTEMROOT>"
        f"</WINDOWS><FLAGS>{edition}</FLAGS><NAME>{name}</NAME>"
        f"<DESCRIPTION>{name}</DESCRIPTION></IMAGE>"
    )


REAL_MEDIUM = "﻿<WIM>" + "".join([
    image(1, "Windows 11 Enterprise LTSC 2024", "EnterpriseS"),
    image(2, "Windows 11 IoT Enterprise LTSC 2024", "IoTEnterpriseS"),
    image(3, "Windows 11 IoT Enterprise Subscription LTSC 2024", "IoTEnterpriseSK"),
]) + "</WIM>"

CONSUMER = "﻿<WIM>" + "".join([
    image(1, "Windows 11 Home", "Core"),
    image(2, "Windows 11 Pro", "Professional"),
]) + "</WIM>"

WRONG_BUILD = "﻿<WIM>" + image(
    1, "Windows 11 IoT Enterprise LTSC 2024", "IoTEnterpriseS", build="22631"
) + "</WIM>"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def refuses(label, images, must_mention):
    try:
        media.select_ltsc_image(images)
    except media.MediaError as exc:
        if must_mention not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} omits {must_mention!r}")
        return
    failures.append(f"{label}: accepted a medium it must refuse")


images = media.parse_wim_xml(REAL_MEDIUM)
check("three images parsed", len(images), 3)
check("index is an int", images[1]["index"], 2)
check("name", images[1]["name"], "Windows 11 IoT Enterprise LTSC 2024")
check("edition id", images[1]["edition_id"], "IoTEnterpriseS")
check("build", images[1]["build"], "26100")
check("languages", images[1]["languages"], ["en-US"])
check("BOM does not break parsing", images[0]["index"], 1)

chosen = media.select_ltsc_image(images)
check("selects IoT Enterprise LTSC, not plain Enterprise", chosen["index"], 2)
check("selects by edition id", chosen["edition_id"], "IoTEnterpriseS")

# The Subscription edition (IoTEnterpriseSK) needs subscription activation the
# purchased key does not provide: picking it would install something that
# deactivates later.
only_sub = [i for i in images if i["edition_id"] == "IoTEnterpriseSK"]
refuses("subscription edition alone", only_sub, "IoTEnterpriseSK")

refuses("consumer medium", media.parse_wim_xml(CONSUMER), "Windows 11 Pro")
refuses("wrong build", media.parse_wim_xml(WRONG_BUILD), "26100")
refuses("empty medium", [], "no image")

# --image-name is the way out if a future medium carries several IoT editions.
check("explicit name wins",
      media.select_ltsc_image(images,
                              image_name="Windows 11 IoT Enterprise LTSC 2024")["index"],
      2)
try:
    media.select_ltsc_image(images, image_name="Windows 11 Pro")
    failures.append("explicit name: accepted a name absent from the medium")
except media.MediaError:
    pass

# Synthetic WIM: valid magic, proper header, embedded UTF-16LE XML with BOM.
import tempfile  # noqa: E402

xml_str = f'﻿<WIM>{image(1, "Windows 11 IoT Enterprise LTSC 2024", "IoTEnterpriseS")}</WIM>'
xml_bytes = xml_str.encode("utf-16-le")
xml_offset = 96

with tempfile.NamedTemporaryFile(suffix=".wim") as fh:
    header = bytearray(96)
    header[0:8] = media.WIM_MAGIC
    size_bytes = len(xml_bytes).to_bytes(7, "little")
    offset_bytes = xml_offset.to_bytes(8, "little")
    header[media.XML_RESHDR_OFFSET:media.XML_RESHDR_OFFSET + 7] = size_bytes
    header[media.XML_RESHDR_OFFSET + 8:media.XML_RESHDR_OFFSET + 16] = offset_bytes
    header[media.XML_RESHDR_OFFSET + 16:media.XML_RESHDR_OFFSET + 24] = \
        len(xml_bytes).to_bytes(8, "little")
    fh.write(header)
    fh.write(xml_bytes)
    fh.flush()
    result_xml = media.read_wim_xml(fh.name)
    parsed = media.parse_wim_xml(result_xml)
    check("synthetic WIM parsed", len(parsed), 1)
    check("synthetic WIM edition", parsed[0]["edition_id"], "IoTEnterpriseS")

# Truncated header: valid magic but file cut before offset 0x60.
with tempfile.NamedTemporaryFile(suffix=".wim") as fh:
    header = bytearray(64)
    header[0:8] = media.WIM_MAGIC
    fh.write(header)
    fh.flush()
    try:
        media.read_wim_xml(fh.name)
        failures.append("read_wim_xml: accepted truncated header")
    except media.MediaError:
        pass

# A file that is not a WIM must fail on its magic, not on a stack trace.
with tempfile.NamedTemporaryFile(suffix=".wim") as fh:
    fh.write(b"not a wim at all" * 16)
    fh.flush()
    try:
        media.read_wim_xml(fh.name)
        failures.append("read_wim_xml: accepted a file that is not a WIM")
    except media.MediaError:
        pass

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all medium inspection tests passed")
