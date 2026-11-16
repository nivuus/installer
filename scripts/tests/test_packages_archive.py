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


class RecordingTar(tarfile.TarFile):
    """A TarFile that records the ownership extraction asks for.

    `TarFile.chown` is where tarfile hands ownership to the kernel, and it
    does nothing unless the process is root - so on an ordinary runner the
    files on disk belong to the runner whatever the archive said, and looking
    at them proves nothing. The updater runs as root, where that call is
    real. Recording it here measures what a root extraction would be asked
    to do, without needing root.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chowned = {}

    def chown(self, tarinfo, targetpath, numeric_owner):
        self.chowned[tarinfo.name] = (tarinfo.uid, tarinfo.gid, tarinfo.uname,
                                      tarinfo.gname, numeric_owner)
        super().chown(tarinfo, targetpath, numeric_owner)


def archive(*entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for info, data in entries:
            tar.addfile(info, data)
    buffer.seek(0)
    return RecordingTar.open(fileobj=buffer)


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
    regular = archive(entry("./", tarfile.DIRTYPE, mode=0o775),
                      entry("hooks/", tarfile.DIRTYPE, mode=0o777),
                      entry("hooks/install.py", mode=0o4777),
                      entry("link", tarfile.SYMTYPE, linkname="hooks/install.py"),
                      entry("hard", tarfile.LNKTYPE, linkname="hooks/install.py",
                            mode=0o4777))
    extract(regular, dest)
    info = os.stat(os.path.join(dest, "hooks/install.py"))
    check("a regular archive extracts", os.path.isfile(os.path.join(dest, "link")),
          True)
    check("setuid and group/other write are cleared",
          stat.S_IMODE(info.st_mode), 0o755)
    check("ownership is the extracting user's, not the archive's",
          (info.st_uid, info.st_gid), (os.getuid(), os.getgid()))
    # The check above cannot fail unless the suite runs as root. This one
    # can, anywhere: every member, directories and links included, must be
    # handed to the kernel with the extracting user's numeric ids and no
    # account name left to resolve - never the archive's uid 4242.
    mine = (os.getuid(), os.getgid(), "", "", True)
    check("every member is chowned to the extracting user, by number",
          regular.chowned,
          {name: mine for name in (".", "hooks", "hooks/install.py",
                                   "link", "hard")})

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


# This repository's own release is `git archive HEAD`, and `nivuus update
# --self` extracts it with extract(). Tracked links into live-build's install
# tree once made it unextractable, which only a real run on the reference
# host revealed: this proves the archive the release will publish passes.
import subprocess  # noqa: E402

with tempfile.TemporaryDirectory() as root:
    tarball = os.path.join(root, "release.tar")
    subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar",
                    "-o", tarball, "HEAD"], check=True)
    try:
        with tarfile.open(tarball) as tar:
            extract(tar, os.path.join(root, "dest"))
    except ArchiveError as exc:
        failures.append(f"this repository's release archive is refused: {exc}")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all archive extraction tests passed")
