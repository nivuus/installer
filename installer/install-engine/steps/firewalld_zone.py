#!/usr/bin/env python3
"""Bind an interface to a firewalld zone in the permanent configuration only.

Inserts one `<interface name="..."/>` line into `<root>/etc/firewalld/zones/
<zone>.xml` and leaves every other byte of the file as it was. When /etc holds
no copy of the zone, the vendor file under /usr/lib/firewalld/zones is the
base, which is what firewalld itself does on its first permanent change.

Why not `firewall-offline-cmd --zone=Z --add-interface=I`: it rewrites the
whole file from its own model (comments dropped, elements reordered) and only
knows the live /etc. Why not `firewall-cmd --permanent`: it talks to the
running daemon, and for an interface NetworkManager owns it rewrites the NM
profile's zone instead. This module runs no command at all: the change takes
effect at the next firewalld start or reload, which is never this module's
business.

It refuses to bind an interface that another zone file already lists: two
zones claiming one interface is the contradiction that made every firewalld
reload on the reference host fail on 2026-11-09 (stale enp15s0 in
internal.xml against NM's connection.zone home).

Standard library only, so the same file serves the install engine (on the
chroot) and the host, run by path:
    python3 firewalld_zone.py [--root /] external nvguest0
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

ETC_ZONES = "etc/firewalld/zones"
VENDOR_ZONES = "usr/lib/firewalld/zones"
# firewalld's own rule (functions.checkInterface): at most 15 bytes, no
# whitespace, no "/".
_IFNAME = re.compile(r"[^\s/]{1,15}")
# The closing tag alone on its line, followed by nothing but whitespace.
_CLOSING = re.compile(rb"^([ \t]*)</zone\s*>[ \t\r\n]*\Z", re.MULTILINE)


class ZoneFileError(RuntimeError):
    """A zone file cannot be changed safely; nothing was written."""


def _parse_zone(data: bytes, path: pathlib.Path) -> ET.Element:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ZoneFileError(f"{path}: not well-formed XML: {exc}") from exc
    if root.tag != "zone":
        raise ZoneFileError(f"{path}: root element is <{root.tag}>, not <zone>")
    return root


def _lists(root: ET.Element, interface: str) -> int:
    return sum(1 for el in root.findall("interface") if el.get("name") == interface)


def _claimed_elsewhere(etc_dir: pathlib.Path, zone_file: pathlib.Path, interface: str) -> list[str]:
    if not etc_dir.is_dir():
        return []
    return sorted(p.name for p in etc_dir.glob("*.xml")
                  if p != zone_file and _lists(_parse_zone(p.read_bytes(), p), interface))


def _insert(data: bytes, interface: str, path: pathlib.Path) -> bytes:
    """Return `data` with the interface line placed just before </zone>."""
    match = _CLOSING.search(data)
    if match is None:
        raise ZoneFileError(f"{path}: </zone> is not alone on the last line; refusing to guess")
    start = match.start()
    head = data[:start]
    lines = head.splitlines(keepends=True)
    newline = b"\r\n" if lines and lines[-1].endswith(b"\r\n") else b"\n"
    previous = next((line for line in reversed(lines) if line.strip()), b"")
    if previous.lstrip().startswith(b"<zone"):
        indent = b"  "
    else:
        indent = previous[:len(previous) - len(previous.lstrip(b" \t"))]
    line = indent + b"<interface name=" + quoteattr(interface).encode("ascii") + b"/>" + newline
    return head + line + data[start:]


def _write_like(path: pathlib.Path, data: bytes, like: os.stat_result) -> None:
    """Atomically replace `path` with `data`, with the mode and owner of `like`."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, like.st_mode & 0o7777)
        os.chown(tmp, like.st_uid, like.st_gid)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def bind_interface(root: str, zone: str, interface: str) -> bool:
    """Make the permanent `zone` list `interface`. Returns True if a file changed."""
    if not _IFNAME.fullmatch(interface) or len(interface.encode()) > 15:
        raise ValueError(f"invalid interface name: {interface!r}")
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", zone):
        raise ValueError(f"invalid zone name: {zone!r}")
    etc_dir = pathlib.Path(root, ETC_ZONES)
    target = etc_dir / f"{zone}.xml"
    source = target if target.is_file() else pathlib.Path(root, VENDOR_ZONES, f"{zone}.xml")
    if not source.is_file():
        raise ZoneFileError(f"zone {zone!r} has no file: neither {target} nor {source} exists")

    data = source.read_bytes()
    if _lists(_parse_zone(data, source), interface):
        return False
    others = _claimed_elsewhere(etc_dir, target, interface)
    if others:
        raise ZoneFileError(f"{interface} is already bound in {', '.join(others)}; "
                            f"refusing to bind it to {zone} as well")

    new = _insert(data, interface, source)
    if _lists(_parse_zone(new, target), interface) != 1:
        raise ZoneFileError(f"{target}: the edited file does not list {interface} exactly once")
    etc_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    _write_like(target, new, source.stat())
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="/", help="filesystem root holding etc/ and usr/ (default /)")
    parser.add_argument("zone")
    parser.add_argument("interface")
    args = parser.parse_args(argv)
    try:
        changed = bind_interface(args.root, args.zone, args.interface)
    except (ZoneFileError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    where = pathlib.Path(args.root, ETC_ZONES, f"{args.zone}.xml")
    if changed:
        print(f"added interface {args.interface} to zone {args.zone} in {where}; "
              "takes effect at the next firewalld start or reload")
    else:
        print(f"interface {args.interface} already listed in zone {args.zone}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
