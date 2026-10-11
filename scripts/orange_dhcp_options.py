#!/usr/bin/env python3
"""Render the dhclient configuration Orange expects on VLAN 832.

The DHCP credentials are the PPPoE ones; they are read from the NetworkManager
profile and never printed. Layout: docs/superpowers/specs/2026-10-11-wan-orange-dhcp-design.md.
"""
import argparse
import hashlib
import os
import pathlib
import re
import secrets
import string
import sys

ORANGE_PREFIX = bytes(11) + bytes.fromhex("1a0900000558010341")
VENDOR = b"sagem"
DEFAULT_PROFILE = "/etc/NetworkManager/system-connections/pppoe-enp6s0.835.nmconnection"


def authentication(login, password, salt=None):
    salt = secrets.token_hex(8).encode() if salt is None else salt
    if len(salt) != 16:
        raise ValueError("salt must be 16 bytes")
    ident = salt[:1]
    digest = hashlib.md5(ident + password + salt).digest()
    return (ORANGE_PREFIX + bytes([0x01, len(login) + 2]) + login
            + bytes([0x3C, len(salt) + 2]) + salt
            + bytes([0x03, len(ident) + len(digest) + 2]) + ident + digest)


def colon_hex(data):
    return ":".join("%02x" % byte for byte in data)


def values(login, password, livebox, salt=None):
    name = ("FSVDSL_livebox.Internet.softathome.Livebox%d" % livebox).encode()
    return {
        "AUTHENTICATION": colon_hex(authentication(login, password, salt)),
        "VENDOR_CLASS_4": VENDOR.decode(),
        "USER_CLASS_4": colon_hex(bytes([len(name)]) + name),
        "VENDOR_CLASS_6": colon_hex(bytes.fromhex("0000040e") + len(VENDOR).to_bytes(2, "big") + VENDOR),
        "USER_CLASS_6": colon_hex(len(name).to_bytes(2, "big") + name),
    }


KEYFILE_ESCAPES = {b"s": b" ", b"n": b"\n", b"t": b"\t", b"r": b"\r", b"\\": b"\\"}


def keyfile_value(profile, section, key):
    """One value of a NetworkManager keyfile, byte-exact.

    A keyfile is a GKeyFile, not an INI file: the value starts after the first
    `=` with leading blanks dropped, keeps its trailing blanks, and escapes
    backslash, leading space, newline, tab and carriage return. configparser
    trims and does not unescape, which would hash the wrong password.
    """
    current = None
    with open(profile, "rb") as handle:
        for raw in handle.read().split(b"\n"):
            line = raw.rstrip(b"\r")
            if line.startswith(b"[") and line.rstrip().endswith(b"]"):
                current = line.strip()[1:-1]
            elif current == section and b"=" in line and not line.lstrip().startswith(b"#"):
                name, value = line.split(b"=", 1)
                if name.strip() == key:
                    return re.sub(rb"\\(.)", lambda m: KEYFILE_ESCAPES.get(m.group(1), m.group(0)),
                                  value.lstrip(b" \t"))
    raise KeyError(key.decode())


def credentials(profile):
    return keyfile_value(profile, b"pppoe", b"username"), keyfile_value(profile, b"pppoe", b"password")


def render(families, profile, templates, out, livebox):
    """Render every family from ONE salt: Orange wants option 90 and option 11
    to carry the same bytes, so the two files must come from the same draw."""
    login, password = credentials(profile)
    rendered = values(login, password, livebox)
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for family in families:
        name = "dhclient-orange-v%s.conf" % family
        template = (pathlib.Path(templates) / (name + ".template")).read_text(encoding="utf-8")
        text = string.Template(template).substitute(rendered)
        target = out / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(target, 0o600)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("render")
    cmd.add_argument("--family", choices=["4", "6"], action="append",
                     help="render only this family (default: both, from one salt)")
    cmd.add_argument("--profile", default=DEFAULT_PROFILE)
    cmd.add_argument("--templates", default="/usr/local/share/nivuus/orange")
    cmd.add_argument("--out", default="/etc/dhcp/nivuus-orange",
                     help="where dhclient may read it: AppArmor confines it to /etc/dhcp (default: %(default)s)")
    cmd.add_argument("--livebox", type=int, default=5)
    args = parser.parse_args()
    try:
        render(args.family or ["4", "6"], args.profile, args.templates, args.out, args.livebox)
    except (OSError, KeyError, ValueError) as error:
        print("orange_dhcp_options: cannot render: %s" % type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
