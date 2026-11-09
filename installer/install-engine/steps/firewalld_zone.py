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

On a live host, firewalld must be restarted or reloaded, in a controlled
window, BEFORE anything makes a permanent change to that zone. firewalld keeps
its permanent model in memory: a later `firewall-cmd --zone=<zone>
--permanent ...` (nivuus/mqtt's FirewallManager issues these from Home
Assistant) rewrites the zone's XML from that stale model and silently drops a
line written behind its back. After the reload, check the line is still there
with `grep nvguest0 /etc/firewalld/zones/<zone>.xml`.

It refuses, writing nothing, when the binding would contradict another
source of zones:
- another zone file listing the interface (two zones claiming it);
- a NetworkManager keyfile in etc/NetworkManager/system-connections whose
  `interface-name` or `[vpn] dev` is the interface and whose zone is a
  different zone. A profile without `zone=` is in firewalld's DefaultZone,
  read from etc/firewalld/firewalld.conf (`public` when unset). That is the
  2026-11-09 failure: every reload on the reference host failed because
  internal.xml listed enp15s0 while NM's connection.zone said home. The
  keyfiles are read; nmcli is never called.
A zone file or keyfile that cannot be parsed also refuses the bind, named by
its path only: keyfiles hold secrets, so the error gives the exception class
and line number, never the line. A leading UTF-8 BOM is accepted in both.

Standard library only, so the same file serves the install engine (on the
chroot) and the host, run by path:
    python3 firewalld_zone.py [--root /] external nvguest0
"""
from __future__ import annotations

import argparse
import configparser
import os
import pathlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

ETC_ZONES = "etc/firewalld/zones"
VENDOR_ZONES = "usr/lib/firewalld/zones"
NM_CONNECTIONS = "etc/NetworkManager/system-connections"
FIREWALLD_CONF = "etc/firewalld/firewalld.conf"
# firewalld.conf without DefaultZone: firewalld falls back to this one.
FALLBACK_ZONE = "public"
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
    """Names of the other zone files that list `interface`."""
    if not etc_dir.is_dir():
        return []
    claimed = []
    for path in sorted(etc_dir.glob("*.xml")):
        if path == zone_file:
            continue
        try:
            root = _parse_zone(path.read_bytes(), path)
        except ZoneFileError as exc:
            raise ZoneFileError(f"refusing to bind: cannot check {path} for a conflicting "
                                f"binding ({exc})") from exc
        if _lists(root, interface):
            claimed.append(path.name)
    return claimed


def _default_zone(root: str) -> str:
    """firewalld's DefaultZone, read from firewalld.conf; `public` when unset."""
    conf = pathlib.Path(root, FIREWALLD_CONF)
    zone = FALLBACK_ZONE
    if conf.is_file():
        for line in conf.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "DefaultZone" and value.strip():
                zone = value.strip()
    return zone


def _unreadable(path: pathlib.Path, exc: Exception) -> ZoneFileError:
    """A refusal naming the keyfile, never its content.

    Keyfiles hold Wi-Fi and PPPoE secrets, and configparser quotes the
    offending line in its messages: only the class and line number go out.
    """
    lineno = getattr(exc, "lineno", None)
    if lineno is None and getattr(exc, "errors", None):
        lineno = exc.errors[0][0]
    where = f" at line {lineno}" if isinstance(lineno, int) else ""
    return ZoneFileError(f"refusing to bind: cannot check {path} for a conflicting zone "
                         f"({type(exc).__name__}{where})")


def _nm_disagreeing(root: str, zone: str, interface: str) -> list[str]:
    """NM keyfiles that put `interface` in a zone other than `zone`.

    A profile without zone= puts its interface in firewalld's DefaultZone.
    """
    nm_dir = pathlib.Path(root, NM_CONNECTIONS)
    if not nm_dir.is_dir():
        return []
    found = []
    for path in sorted(nm_dir.glob("*.nmconnection")):
        keyfile = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            # utf-8-sig drops a leading BOM, which GKeyFile accepts too.
            keyfile.read_string(path.read_text(encoding="utf-8-sig"), source=path.name)
        except (configparser.Error, UnicodeDecodeError) as exc:
            # "from None": the original exception would carry the line.
            raise _unreadable(path, exc) from None
        names = {keyfile.get("connection", "interface-name", fallback=""),
                 keyfile.get("vpn", "dev", fallback="")}
        if interface not in names:
            continue
        other = keyfile.get("connection", "zone", fallback="").strip() or _default_zone(root)
        if other != zone:
            found.append(f"{path.name} (zone={other})")
    return found


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
    # Make the rename itself durable, not only the new file's content.
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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

    others = _claimed_elsewhere(etc_dir, target, interface)
    if others:
        raise ZoneFileError(f"{interface} is already bound in {', '.join(others)}; "
                            f"refusing to bind it to {zone} as well")
    profiles = _nm_disagreeing(root, zone, interface)
    if profiles:
        raise ZoneFileError(f"NetworkManager puts {interface} elsewhere: {', '.join(profiles)}; "
                            f"refusing to bind it to {zone}")
    data = source.read_bytes()
    if _lists(_parse_zone(data, source), interface):
        return False

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
        print(f"added interface {args.interface} to zone {args.zone} in {where}; it takes effect "
              "at the next firewalld start or reload, and a --permanent change to this zone made "
              "before that reload drops it: reload in a controlled window, then grep the file")
    else:
        print(f"interface {args.interface} already listed in zone {args.zone}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
