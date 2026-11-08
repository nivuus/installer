"""Read, back up, write and verify UEFI variables through efivarfs.

An efivarfs file is the variable's 4-byte attribute word followed by its
data. Writing replaces the whole variable and must be ONE write() of the
full content (Documentation/filesystems/efivarfs.rst). The kernel marks
every non-whitelisted variable immutable, so the flag has to be lifted for
the write and put back after it.
"""

import fcntl
import os
import shutil
import struct
import time

EFIVARS = "/sys/firmware/efi/efivars"
FS_IOC_GETFLAGS, FS_IOC_SETFLAGS, FS_IMMUTABLE_FL = 0x80086601, 0x40086602, 0x10


class EfiVarError(RuntimeError):
    pass


def set_immutable(path, immutable):
    """chattr +i / -i on one file, through the same ioctl chattr uses."""
    fd = os.open(path, os.O_RDONLY)
    try:
        flags = struct.unpack("i", fcntl.ioctl(fd, FS_IOC_GETFLAGS, struct.pack("i", 0)))[0]
        flags = flags | FS_IMMUTABLE_FL if immutable else flags & ~FS_IMMUTABLE_FL
        fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i", flags))
    finally:
        os.close(fd)


class EfiVars:
    def __init__(self, root=EFIVARS, immutable=set_immutable):
        self.root = root
        self.immutable = immutable

    def path(self, name):
        return os.path.join(self.root, name)

    def read(self, name):
        """(attributes, data), or None when the variable does not exist."""
        try:
            with open(self.path(name), "rb") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return None
        if len(raw) < 4:
            raise EfiVarError("%s: %d bytes, shorter than its attribute word" % (name, len(raw)))
        return struct.unpack("<I", raw[:4])[0], raw[4:]

    def write(self, name, attributes, data):
        """Replace a variable, then read it back; raise if it does not match."""
        path = self.path(name)
        if not os.path.exists(path):
            raise EfiVarError("%s does not exist; this tool never creates variables" % name)
        content = struct.pack("<I", attributes) + bytes(data)
        self.immutable(path, False)
        try:
            fd = os.open(path, os.O_WRONLY)
            try:
                written = os.write(fd, content)
            finally:
                os.close(fd)
        finally:
            self.immutable(path, True)
        if written != len(content):
            raise EfiVarError("%s: short write, %d of %d bytes" % (name, written, len(content)))
        back = self.read(name)
        if back != (attributes, bytes(data)):
            raise EfiVarError("%s: the firmware did not keep the written value" % name)

    def backup(self, names, directory):
        """Copy the raw files (attributes included) of these variables."""
        os.makedirs(directory, exist_ok=False)
        for name in names:
            shutil.copyfile(self.path(name), os.path.join(directory, name))
        return directory

    def restore(self, directory):
        """Write every variable of a backup back, return the names changed."""
        changed = []
        for name in sorted(os.listdir(directory)):
            with open(os.path.join(directory, name), "rb") as fh:
                raw = fh.read()
            attributes, data = struct.unpack("<I", raw[:4])[0], raw[4:]
            if self.read(name) != (attributes, data):
                self.write(name, attributes, data)
                changed.append(name)
        return changed


def backup_dir(base):
    return os.path.join(base, time.strftime("%Y%m%d-%H%M%S"))
