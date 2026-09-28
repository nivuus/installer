#!/usr/bin/env python3
"""Tests for installer/packages/archive.py - extraction that stays inside.

Every hostile shape here is built by hand, because no honest `git archive`
produces one; each must be refused with nothing written outside the
extraction directory.

Run: python3 scripts/tests/test_packages_archive.py
"""
import io
import os
import pathlib
import stat
import sys
import tarfile
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.archive import ArchiveError, extract  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def entry(name, kind=tarfile.REGTYPE, data=b"x", linkname="", mode=0o644):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.mode = mode
    info.uid = info.gid = 4242
    if kind == tarfile.REGTYPE:
        info.size = len(data)
        return info, io.BytesIO(data)
    return info, None


def archive(*entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for info, data in entries:
            tar.addfile(info, data)
    buffer.seek(0)
    return tarfile.open(fileobj=buffer)


def refused(label, *entries):
    with tempfile.TemporaryDirectory() as root:
        dest = os.path.join(root, "dest")
        try:
            extract(archive(*entries), dest)
            failures.append(f"{label}: expected ArchiveError")
        except ArchiveError:
            pass
        outside = sorted(p for p in os.listdir(root) if p != "dest")
        check(f"{label}: nothing written outside", outside, [])


with tempfile.TemporaryDirectory() as root:
    dest = os.path.join(root, "dest")
    extract(archive(entry("./", tarfile.DIRTYPE, mode=0o775), entry("hooks/", tarfile.DIRTYPE, mode=0o777),
                    entry("hooks/install.py", mode=0o4777),
                    entry("link", tarfile.SYMTYPE, linkname="hooks/install.py"),
                    entry("hard", tarfile.LNKTYPE, linkname="hooks/install.py",
                          mode=0o4777)),
            dest)
    info = os.stat(os.path.join(dest, "hooks/install.py"))
    check("a regular archive extracts", os.path.isfile(os.path.join(dest, "link")),
          True)
    check("setuid and group/other write are cleared",
          stat.S_IMODE(info.st_mode), 0o755)
    check("ownership is the extracting user's, not the archive's",
          (info.st_uid, info.st_gid), (os.getuid(), os.getgid()))

refused("absolute name", entry("/tmp/evil"))
refused("dot-dot name", entry("../evil"))
refused("dot-dot hidden in a path", entry("a/../../evil"))
refused("symlink out", entry("out", tarfile.SYMTYPE, linkname="../.."))
refused("absolute symlink", entry("out", tarfile.SYMTYPE, linkname="/etc"))
refused("hard link out", entry("out", tarfile.LNKTYPE, linkname="../evil"))
refused("device", entry("dev", tarfile.CHRTYPE))
refused("fifo", entry("fifo", tarfile.FIFOTYPE))
# Each target looks harmless on its own; only resolving through the first
# link shows the second one escaping.
refused("chain of links", entry("l2", tarfile.SYMTYPE, linkname="."),
        entry("l1", tarfile.SYMTYPE, linkname="l2/.."),
        entry("l1/evil"))
refused("write through a link", entry("up", tarfile.SYMTYPE, linkname="."),
        entry("sub", tarfile.DIRTYPE),
        entry("sub/l", tarfile.SYMTYPE, linkname=".."),
        entry("sub/l/../evil"))


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all archive extraction tests passed")
