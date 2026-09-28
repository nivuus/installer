"""Extracting a package archive without letting it write outside its directory.

`tarfile`'s own `filter="data"` (PEP 706) would do this, but it only exists
from Python 3.12 and the 3.11 security releases after 3.11.4 - and the
installed target is Debian bookworm, whose python3.11 is 3.11.2 and rejects
the keyword. So the same rules are applied here, member by member, on every
Python: one code path, tested wherever the suites run.

Refused, at the first offending member (callers extract into a staging
directory they discard on failure):
  - absolute names and names with a `..` component;
  - symbolic and hard links whose target leaves the extraction directory;
  - anything that is not a regular file, a directory or such a link
    (devices, FIFOs).
Neutralised: setuid/setgid/sticky bits and group/other write permission are
cleared, and ownership is set to the extracting user, so an archive cannot
hand a file to another account.
"""
from __future__ import annotations

import os
import posixpath
import tarfile


class ArchiveError(RuntimeError):
    """Raised when an archive holds a member that must not be extracted."""


def _within(root: str, path: str) -> bool:
    return os.path.commonpath([root, path]) == root


def _checked(member: tarfile.TarInfo, root: str) -> tarfile.TarInfo:
    """Validate `member` against what is ALREADY on disk under `root`.

    realpath() follows the links extracted before this member, which is what
    catches a chain such as `l2 -> .` then `l1 -> l2/..`: each target looks
    harmless on paper, and only the resolved path shows the escape. This is
    the method of CPython's own data_filter.
    """
    name = member.name
    if posixpath.isabs(name) or ".." in name.split("/"):
        raise ArchiveError(f"member {name!r} leaves the archive directory")
    if not _within(root, os.path.realpath(os.path.join(root, name))):
        raise ArchiveError(f"member {name!r} is written through a link that "
                           "leaves the archive directory")
    if member.issym():
        target = os.path.join(root, os.path.dirname(name), member.linkname)
        if (os.path.isabs(member.linkname)
                or not _within(root, os.path.realpath(target))):
            raise ArchiveError(f"symbolic link {name!r} -> "
                               f"{member.linkname!r} leaves the archive directory")
    elif member.islnk():
        target = os.path.realpath(os.path.join(root, member.linkname))
        if os.path.isabs(member.linkname) or not _within(root, target):
            raise ArchiveError(f"hard link {name!r} -> "
                               f"{member.linkname!r} leaves the archive directory")
    elif not (member.isreg() or member.isdir()):
        raise ArchiveError(f"member {name!r} is neither a file, a "
                           "directory nor a link")
    member.mode &= 0o755
    member.uid, member.gid = os.getuid(), os.getgid()
    member.uname = member.gname = ""
    return member


# Every member is validated by this module, so tarfile's own filter - where
# it exists - is told not to transform anything more: extraction then behaves
# the same on 3.11.2, where the keyword does not exist, and on 3.14, where the
# default filter changed.
_NO_FURTHER_FILTER = ({"filter": "fully_trusted"}
                      if hasattr(tarfile, "fully_trusted_filter") else {})


def extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract `tar` into `dest`, refusing at the first unsafe member.

    Members already written when a later one is refused stay on disk: the
    caller extracts into a staging directory it removes on failure, never
    into the installed copy.
    """
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    for member in tar.getmembers():
        tar.extract(_checked(member, root), dest, numeric_owner=True,
                    **_NO_FURTHER_FILTER)
