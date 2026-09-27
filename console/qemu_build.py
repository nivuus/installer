"""The anti-detection QEMU the console's domain runs on: where it lives,
whether it is built, and how to build it.

The build itself is host/qemu-anti-detection/build-qemu.sh (bash: it is a
configure/make recipe, run on the target by the activate phase). This
module is the Python side of that contract - the prefix, the stamp the
script writes when it is done, and the step predicate guest_steps.py wires
before `define`. libvirt validates the <emulator> binary AT DEFINE TIME
("Cannot check QEMU binary ...: No such file or directory"), so a domain
naming this binary cannot be defined until this build has run once.

WHY THE VERSION IS READ FROM THE SCRIPT. The tarball version and its
checksum are pinned in build-qemu.sh, the only file that downloads
anything. Restating the version here would be a second place to bump, and
a stale one makes the predicate wrong in the worst direction: "already
built" against a binary of the other version. So it is parsed out of the
script's own source, the same way test_console_guest_steps.py reads
fetch_payload.py's literal rather than trusting a copy.

Importable from the activate phase: nothing here needs jinja2 or anything
outside the standard library.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE / "host" / "qemu-anti-detection"
BUILD_SCRIPT = BUILD_DIR / "build-qemu.sh"

# An isolated prefix, deliberately NOT /usr/local: the upstream README's
# `make install` there shadows Debian's qemu-system-x86_64 for every caller
# on PATH, libvirt's capability probing included. Here only the domain
# whose <emulator> names this binary ever runs it.
QEMU_PREFIX = "/opt/qemu-anti-detection"
EMULATOR = f"{QEMU_PREFIX}/bin/qemu-system-x86_64"
STAMP = f"{QEMU_PREFIX}/nivuus-build.stamp"

_VERSION_RE = re.compile(r'^QEMU_VERSION="([^"]+)"$', re.MULTILINE)


class QemuBuildError(RuntimeError):
    """The package's own build recipe is not usable as shipped."""


def qemu_version(script: Path = BUILD_SCRIPT) -> str:
    """The QEMU version build-qemu.sh pins, read from its source."""
    match = _VERSION_RE.search(script.read_text(encoding="utf-8"))
    if not match:
        raise QemuBuildError(f"{script} pins no QEMU_VERSION")
    return match.group(1)


def patch_path(script: Path = BUILD_SCRIPT) -> Path:
    return script.parent / f"qemu-{qemu_version(script)}.patch"


def expected_stamp(script: Path = BUILD_SCRIPT) -> str:
    """What build-qemu.sh writes once THIS version with THIS patch is in.

    Same shape as the script's EXPECTED_STAMP, byte for byte: the patch's
    sha256 is in it so that vendoring a new revision of the patch rebuilds,
    while a rebuild for nothing (same tarball, same patch) never happens.
    """
    patch = patch_path(script)
    if not patch.is_file():
        raise QemuBuildError(f"patch missing next to {script.name}: {patch}")
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    return f"qemu={qemu_version(script)} patch={digest}"


def qemu_built(prefix: str = QEMU_PREFIX, script: Path = BUILD_SCRIPT) -> bool:
    """Is the patched QEMU installed, at the version and patch shipped here?

    Both the stamp and the binary are required: a stamp alone is what a
    half-removed prefix leaves behind, a binary alone is one built from an
    earlier patch. A stamp that cannot be read means "build", never "skip" -
    the same rule guest_steps.py applies to its ISO fingerprint.
    """
    binary = os.path.join(prefix, "bin", "qemu-system-x86_64")
    stamp = os.path.join(prefix, "nivuus-build.stamp")
    if not os.access(binary, os.X_OK):
        return False
    try:
        with open(stamp, encoding="utf-8") as fh:
            return fh.read().strip() == expected_stamp(script)
    except OSError:
        return False


def build_command(prefix: str = QEMU_PREFIX, script: Path = BUILD_SCRIPT) -> list[str]:
    """The argv that builds and installs it. Built, never launched here."""
    return [str(script), "--prefix", prefix]
