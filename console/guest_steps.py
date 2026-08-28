"""The five steps that build the console's Windows guest, and what lets them skip.

This module DECIDES: what to launch, in which order, and what may be skipped.
It launches nothing. Every step carries the argv it would run, an
`already_done()` predicate that is a pure observation of the filesystem and of
`virsh`, and a `run()` that performs it. That separation is what makes the
whole phase testable without building a gigabyte ISO and without starting the
production domain - starting it detaches the GPU from the host, which stops
ollama, nvidia-persistenced and the Tdarr nodes.

Two rules govern the predicates, and they are not symmetric:

  * WHEN IN DOUBT, REBUILD. Any read or parse error on the fingerprint file
    means "rebuild", never "skip". Rebuilding by mistake costs twenty minutes;
    skipping by mistake ships a console that does not match the answers given.
  * A REFUSAL NAMES ITS CAUSE. A disk too small, a medium that is not there,
    an empty secret: each raises GuestBuildError with a sentence an operator
    can read, never a raw traceback.

Nothing here imports from installer/: the package must run on a Debian that
has never seen this installer. The few literals shared with console/guest/ are
copied, with a pointer to their source, rather than imported - guest/build.py,
guest/autounattend.py and guest/domain.py all import jinja2 at module scope,
and the activate phase only guarantees python3 and python3-yaml on the target.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

HERE = Path(__file__).resolve().parent
GUEST_DIR = HERE / "guest"

GIB = 1024 ** 3

# --- the partition derivation ------------------------------------------- #
# The GAMES partition is the one with a fixed size; Windows takes what is
# left. guest/templates/autounattend.xml.j2 creates the data partition FIRST
# so Windows Setup cannot displace it, and <Extend> only applies to the LAST
# partition created - so the derivation SUBTRACTS what Windows needs instead
# of reserving what the games want. Reversed, a small disk yields a tiny C:,
# a console that installs and then suffocates on its first Windows update.
#
# Windows settles around 70-80 GiB in service (the OS, a ~12 GiB hibernation
# file - the host's whole energy strategy depends on S4 - a ~16 GiB page file,
# the NVIDIA driver, Apollo and the agent); the rest absorbs years of
# cumulative updates and WinSxS growth.
WINDOWS_MIN_GIB = 120
# EFI (260 MiB), MSR (16 MiB) and the Recovery partition Setup carves out for
# itself, rounded up: about 1.3 GiB on the production NVMe.
PARTITION_OVERHEAD_GIB = 2
# Mirrors guest/autounattend.py's MIN_DATA_PARTITION_MB (102400 MiB), which
# refuses the answer file outright below it. Copied, not imported: see the
# module docstring.
MIN_GAMES_GIB = 100

# guest/domain.py's DOMAIN_NAME. Same copy-not-import reason.
DOMAIN_NAME = "Windows"
# The states in which the guest is already up, listed positively rather than
# excluding "shut off". `in shutdown` and `crashed` are states to get OUT of,
# not successes: read as "already started" they would leave a crashed guest
# untouched and the console dark. virsh emits them in English under LC_ALL=C,
# which default_virsh forces.
DOMAIN_UP_STATES = frozenset({"running", "idle", "paused", "pmsuspended"})

# --- what actually identifies the domain: its hostdevs' HOST pci addresses -#
# Matches the host address libvirt keeps verbatim inside <hostdev><source>
# (measured on the running production domain, 2026-08-28: `virsh dumpxml
# Windows` shows `<source><address domain='0x0000' bus='0x03' slot='0x00'
# function='0x0'/></source>` for the NVMe hostdev, exactly the fields
# domain.xml.j2 renders, in the same order, no extra attribute). This is
# NOT the same element as the GUEST-side bus position libvirt also stamps as
# a sibling <address type='pci' .../> right after <alias> - that one only
# says where the device sits on the VIRTUAL bus, so it changes across
# defines even when the PHYSICAL device passed through does not, and must
# never be read as identity. Restricting the search to the text inside
# <hostdev>...</hostdev> keeps the two apart without depending on attribute
# order elsewhere in the document.
_HOSTDEV_BLOCK_RE = re.compile(r"<hostdev\b.*?</hostdev>", re.DOTALL)
_HOSTDEV_SOURCE_ADDR_RE = re.compile(
    r"<source>\s*<address\s+domain=['\"]0x([0-9a-fA-F]+)['\"]\s+"
    r"bus=['\"]0x([0-9a-fA-F]+)['\"]\s+slot=['\"]0x([0-9a-fA-F]+)['\"]\s+"
    r"function=['\"]0x([0-9a-fA-F]+)['\"]", re.DOTALL)
_PCI_ADDRESS_RE = re.compile(
    r"([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9a-fA-F]+)\.([0-9a-fA-F]+)")


def _normalize_pci_address(groups: tuple[str, str, str, str]) -> str:
    """(domain, bus, slot, function) hex strings -> 'dddd:bb:ss.f', lower
    case, canonical width - so a '0x01' from the XML and a '1' from sysfs
    compare equal instead of failing on formatting alone."""
    domain, bus, slot, func = (int(part, 16) for part in groups)
    return f"{domain:04x}:{bus:02x}:{slot:02x}.{func:x}"


def hostdev_source_addresses(xml: str) -> set[str]:
    """Every HOST pci address a <hostdev> in `xml` passes through.

    Pure string parsing, no libvirt call: `xml` is whatever defined_xml()
    already read via `virsh dumpxml`. Restricted to <hostdev> blocks (see
    the comment above _HOSTDEV_BLOCK_RE) so the guest-side bus position
    libvirt also stamps on the same element is never mistaken for the
    physical device identity.
    """
    out = set()
    for block in _HOSTDEV_BLOCK_RE.findall(xml):
        match = _HOSTDEV_SOURCE_ADDR_RE.search(block)
        if match:
            out.add(_normalize_pci_address(match.groups()))
    return out


def domain_matches_disk(xml: str, disk: str, *,
                        pci_address_of: Callable[[str], str | None] | None = None
                        ) -> bool:
    """Is `disk` the SAME physical device `xml` actually passes through?

    ISO paths alone cannot answer this - see domain_defined()'s own
    docstring for why: they are FIXED paths under the workdir, unchanged by
    which physical disk was selected, so a domain built for a PREVIOUS
    'dedicated_nvme' answer would satisfy the media check forever while
    still wiring up the OLD disk to the guest.

    `pci_address_of` defaults to console.hardware.pci_address_for_device (a
    pure /sys/block read, imported lazily - see _sysfs_size below for the
    same convention and the same reason). ANY resolution failure - an
    unrecognised device path, a symlink sysfs cannot walk - reads as "no
    match", never as "cannot tell so assume yes": the module's own WHEN IN
    DOUBT rule (see the module docstring) applies here exactly as it does to
    the build fingerprint.
    """
    resolver = pci_address_of or _disk_pci_address
    address = resolver(disk)
    if not address:
        return False
    match = _PCI_ADDRESS_RE.fullmatch(address)
    if not match:
        return False
    return _normalize_pci_address(match.groups()) in hostdev_source_addresses(xml)


def _disk_pci_address(disk: str) -> str | None:
    """console.hardware.pci_address_for_device, imported only when needed -
    same lazy-import convention as _sysfs_size below (pure /sys/block read,
    no subprocess, no dependency this module cannot promise a target has)."""
    sys.path.insert(0, str(HERE))
    from hardware import pci_address_for_device  # noqa: PLC0415

    return pci_address_for_device(disk)


# guest/build.py's own defaults for the two answers the wizard does not ask.
DEFAULT_APOLLO_USER = "nivuus"
DEFAULT_HOSTNAME = "NIVUUS-WIN"

# The three secrets, mapped to the file guest/build.py expects to read them
# from. build.py takes them as FILES and never on argv, so that they cannot
# leak into ps output or shell history; this module honours that by pointing
# --key-file & co. at files it wrote in mode 0600.
SECRET_FILES = {
    "ltsc_key": "windows-ltsc.key",
    "admin_password": "windows-admin.pass",
    "apollo_password": "apollo-ui.pass",
}

# Bumped whenever the meaning of the fingerprint changes, so that stamps
# written by an older version read as "rebuild" rather than as a match.
FINGERPRINT_VERSION = 1


class GuestBuildError(RuntimeError):
    """A refusal an operator can act on. Never a bare traceback."""


# --- derivations, fingerprint, stamp ------------------------------------ #

def data_partition_gib(disk_bytes: int) -> int:
    """Size of the GAMES partition, in GiB, for a disk of `disk_bytes`.

    Subtracts what Windows needs from the whole disk - see the module's
    partition comment for why that is the right direction. Refuses a disk
    that cannot leave a usable games partition rather than emitting an
    absurd one that guest/autounattend.py would reject much later.
    """
    disk_gib = int(disk_bytes) // GIB
    games = disk_gib - WINDOWS_MIN_GIB - PARTITION_OVERHEAD_GIB
    if games < MIN_GAMES_GIB:
        raise GuestBuildError(
            f"the dedicated disk holds {disk_gib} GiB: after the "
            f"{WINDOWS_MIN_GIB} GiB Windows needs and "
            f"{PARTITION_OVERHEAD_GIB} GiB of EFI/MSR/Recovery there would be "
            f"{games} GiB left for games, under the {MIN_GAMES_GIB} GiB "
            "minimum. Use a larger disk for the console.")
    return games


def _secret_digest(key: str, value: object) -> str:
    """Irreversible stand-in for a secret, for the fingerprint material.

    The fingerprint file sits next to the ISO. Putting a password in it in
    clear would undo the care taken to keep secrets off argv, so what enters
    the material is a digest, domain-separated by the answer's own name so
    two answers holding the same string still read as different inputs.
    """
    return hashlib.sha256(f"{key}\x00{value}".encode("utf-8")).hexdigest()


def build_fingerprint(iso: str, payload_files: Mapping[str, str],
                      answers: Mapping[str, object], data_gib: int,
                      build_inputs: Mapping[str, str] | None = None) -> str:
    """Identity of what ENTERS the image, as a hex digest.

    Dates are deliberately not part of it: the payload gets touched without
    the ISO needing a rebuild, and the reverse happens too. What counts is
    the source medium's identity, the payload tree, the answers that shape
    the image, the partition size derived from the disk, and the package's
    own build inputs.

    THE SECRETS COUNT TOO, and that is not obvious: each of the three is
    baked into the image (the product key and the administrator password
    into the answer file, the Apollo password into secrets.psd1). Leaving
    them out let `secrets` report "not done" while `build` reported "done",
    so a changed administrator password shipped an ISO still carrying the
    old one - exactly the failure this mechanism exists to prevent. They
    enter as digests, never in clear: see _secret_digest.

    `build_inputs` is the package's own code and data that shape the image -
    build.py, the answer-file template, provision/, assets/. Without it, a
    package upgrade would happily reuse an ISO built by the previous
    version. It is a parameter rather than a read, so this function stays
    pure; plan_steps always supplies it (see package_inputs).
    """
    material = {
        "version": FINGERPRINT_VERSION,
        "iso": iso,
        "payload": {name: payload_files[name] for name in sorted(payload_files)},
        "answers": {key: answers[key] for key in sorted(answers)
                    if key not in SECRET_FILES},
        "secrets": {key: _secret_digest(key, answers[key])
                    for key in sorted(SECRET_FILES) if key in answers},
        "inputs": {name: build_inputs[name] for name in sorted(build_inputs or {})},
        "data_gib": data_gib,
    }
    blob = json.dumps(material, sort_keys=True, default=repr, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_stamp_path(iso_path: str) -> str:
    """Where the fingerprint lives: right next to the ISO it describes."""
    return f"{iso_path}.fingerprint"


def write_build_stamp(stamp_path: str, fingerprint: str) -> None:
    """Record a fingerprint. Only ever called after a build SUCCEEDS."""
    path = Path(stamp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": FINGERPRINT_VERSION,
                                "fingerprint": fingerprint}) + "\n")


def build_is_current(stamp_path: str, expected: str) -> bool:
    """True only when the stamp positively says the ISO matches `expected`.

    FALSE on every error: absent, unreadable, not JSON, JSON of the wrong
    shape, no fingerprint field, a fingerprint that is not a string. All of
    them mean "we do not know", and not knowing means rebuild.
    """
    try:
        data = json.loads(Path(stamp_path).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    recorded = data.get("fingerprint")
    if not isinstance(recorded, str):
        return False
    return recorded == expected


def payload_tree(directory: str) -> dict[str, str]:
    """Map every payload file to the sha256 of its content.

    Content, not size or mtime: fetch_payload.py replaces a binary in place
    when a pinned URL moves, and a same-sized replacement must still count as
    a different image.
    """
    root = Path(directory)
    tree: dict[str, str] = {}
    if not root.is_dir():
        return tree
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        tree[path.relative_to(root).as_posix()] = digest.hexdigest()
    return tree


# The package's own files that shape the image: the build script and every
# module it renders from, the templates, and everything staged into the ISO
# from inside the package. About 200 KiB in total - reading them is the same
# trade-off already made for media_identity, on the cheap side of it: a few
# code files are affordable to hash, several gigabytes of medium are not.
# fetch_payload.py is in this list, and the reason is worth stating because
# the obvious one is WRONG: it is not "covered by payload_tree() anyway".
# payload_done() is deliberately shallow - a non-empty payload directory is
# enough for it - so a changed downloader never re-runs, the tree never moves,
# and its change would reach the fingerprint through nothing at all. Hashing
# the file itself is the only signal that the downloader changed. It buys the
# ISO rebuild, not a re-fetch: the payload on disk still has to be removed by
# hand for new pins to be downloaded. That gap belongs to payload_done(), not
# here, and is left named rather than papered over.
BUILD_INPUT_FILES = ("build.py", "unattend_iso.py", "autounattend.py",
                     "apollo.py", "media.py", "payload.py",
                     "fetch_payload.py")
BUILD_INPUT_DIRS = ("templates", "provision", "probe", "assets")

# The modules under guest/ that deliberately do NOT enter the fingerprint,
# each with the reason it does not shape the image. This list is not
# decoration: undeclared_guest_modules() refuses any .py under guest/ that
# appears in neither it nor BUILD_INPUT_FILES, because a new module slipping
# into the image unfingerprinted would let a package upgrade reuse a stale
# ISO - silently, which is the whole failure class this mechanism exists to
# prevent. Adding a module here is a DECISION; make it explicitly.
BUILD_INPUT_EXCLUDED = {
    # Builds the libvirt domain, not the image. Listing it would force a
    # twenty-minute ISO rebuild every time the XML changes, which is the
    # opposite of what the fingerprint is for.
    "domain.py": "shapes the domain, not the image",
    # Same, for the throwaway HDR bench domain: never part of the console.
    "testdomain.py": "throwaway bench domain, never shipped",
    # Host-side, post-install and optional: replays `retro install` against a
    # guest that already exists. Nothing of it is staged into the ISO.
    "retro_sync.py": "runs on the host after the install, not in the image",
    # Operator tool for running one command in a live guest over WinRM.
    "winrm_exec.py": "operator tool, not staged into the image",
}


def package_inputs(guest_dir: str | Path = GUEST_DIR,
                   console_dir: str | Path = HERE) -> dict[str, str]:
    """Hash the package files that shape the image. Missing ones are recorded.

    A missing input is a sentinel, not an exception: the build will fail on
    it anyway with a better message, and a predicate that raises here would
    have to be caught somewhere to mean "rebuild" - simpler to let the
    fingerprint change.
    """
    guest = Path(guest_dir)
    inputs: dict[str, str] = {}
    for name in BUILD_INPUT_FILES:
        inputs[f"guest/{name}"] = _file_digest(guest / name)
    for name in BUILD_INPUT_DIRS:
        for key, digest in payload_tree(str(guest / name)).items():
            inputs[f"guest/{name}/{key}"] = digest
    # build.py imports it for the retro marker path, and a change there moves
    # where the retrogaming choice is read from.
    inputs["retro.py"] = _file_digest(Path(console_dir) / "retro.py")
    return inputs


def undeclared_guest_modules(guest_dir: str | Path = GUEST_DIR) -> list[str]:
    """Modules under guest/ that are in neither the inputs nor the exclusions.

    BUILD_INPUT_FILES is an explicit list, and an explicit list is exactly
    what someone adding a module forgets to extend. The omission is invisible
    - nothing errors, the ISO simply keeps its old fingerprint and a package
    upgrade reuses an image built from the previous code. This repository has
    already paid that failure once, on a placement table.

    So every `.py` directly under guest/ must be classified, one way or the
    other, and the caller reports the ones that are not BY NAME: the person
    reading that message six months from now has just added a file and needs
    to be told which one, not that "something" is missing.
    """
    guest = Path(guest_dir)
    if not guest.is_dir():
        return []
    known = set(BUILD_INPUT_FILES) | set(BUILD_INPUT_EXCLUDED)
    return sorted(p.name for p in guest.glob("*.py") if p.name not in known)


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


# console/wizard.yaml's own default for the 'guest_workdir' answer.
# Copied here for the same reason console/hooks/activate.py copies its own
# literal of it (see that file's DEFAULT_GUEST_WORKDIR): a plain string from
# a YAML file the packages engine reads, not something either hook can
# import. install.py needs this SAME default to know where to place the
# copy this module then expects to find under it.
DEFAULT_GUEST_WORKDIR = "/var/lib/nivuus/guest"

# The on-target copy's name, fixed rather than derived from the wizard's
# answer: one convention both install.py (which creates it) and plan_steps
# below (which points every step at it) share without exchanging anything
# beyond the workdir they already agree on.
WINDOWS_MEDIA_FILENAME = "windows-source.iso"


def windows_media_path(workdir: str | Path) -> Path:
    """Where the copied Windows medium lives, given the guest workdir.

    Same directory as the other guest-build artefacts (secrets/, payload/,
    the answer-file ISO) - see plan_steps.
    """
    return Path(workdir) / WINDOWS_MEDIA_FILENAME


def require_windows_iso_answer(answers: Mapping[str, object]) -> str:
    """The wizard's raw 'windows_iso' answer, non-empty or refused by name.

    Shared between install.py (which needs it to know what to copy) and
    plan_steps below (which validates the same answer before deriving the
    on-target copy path with windows_media_path) - one message, one place,
    rather than two hooks each inventing their own wording for the same
    refusal.
    """
    value = str(answers.get("windows_iso") or "").strip()
    if not value:
        raise GuestBuildError(
            "the answer 'windows_iso' is empty; the Windows LTSC medium is "
            "what the guest is built from.")
    return value


def copy_windows_medium(source: str, dest: str, *,
                        free_bytes: Callable[[str], int] | None = None
                        ) -> None:
    """Place a full copy of the Windows medium at `dest`. Refuses first.

    console/hooks/install.py is the only phase that ever calls this: it is
    the only moment the live medium the wizard's 'windows_iso' answer names
    and the install target coexist (see that file's module docstring - the
    live medium is unmounted, along with the boot medium it lived on, by
    the time activate runs after the reboot). Once install has run,
    everything downstream in this module (source_iso in plan_steps, in
    particular) reads `dest`, never the raw answer.

    Two refusals, both BEFORE a single byte lands at `dest`, both naming
    their cause via GuestBuildError: the source cannot be read (missing,
    a directory, permission denied - anything os.stat or the copy itself
    raises), or the target filesystem does not have `source`'s size free.
    A half-copied ~5 GB file is a worse failure than a clean refusal while
    the operator is still at the wizard, watching.

    Skipped entirely - no read, no write, no free-space check even run -
    when `dest` already exists with the exact SAME SIZE as `source`. A
    content hash would be the more careful completeness check, and would
    catch a same-sized truncation or swap; it is deliberately not used
    here, for the same reason media_identity() below already made this
    exact call for this exact medium (see its own docstring): the file is
    several gigabytes, so hashing it costs real minutes on every install
    that runs this step more than once (an interrupted engine, a re-run
    `make test-packages`, a reinstalled console) - and it would be a
    STRONGER guarantee than the rest of this module ever relies on, since
    media_identity() itself only ever compares name+size to decide whether
    a BUILD is stale. A hash here would claim more confidence than the
    pipeline downstream can act on, for a cost that scales with exactly the
    case (repeated installer runs) this skip exists to make cheap. The
    trade-off this leaves, spelled out rather than hidden: an operator who
    replaces the medium in place with a byte-identical-SIZE but different
    file must remove the copy (or its stamp - see media_identity) by hand,
    exactly as media_identity() already documents for the fingerprint.
    """
    try:
        size = os.stat(source).st_size
    except OSError as exc:
        raise GuestBuildError(
            f"the Windows medium {source} is not readable: {exc.strerror}. "
            "Check the path given to the wizard before retrying the "
            "install.") from None

    dest_path = Path(dest)
    try:
        if dest_path.stat().st_size == size:
            return  # already copied in full; several GB not worth redoing
    except OSError:
        pass  # absent, or otherwise unreadable: (re)copy it below

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    disk_usage = free_bytes or (lambda path: shutil.disk_usage(path).free)
    free = disk_usage(str(dest_path.parent))
    if free < size:
        raise GuestBuildError(
            f"not enough free space to copy the Windows medium: {source} "
            f"is {size} bytes, only {free} bytes are free under "
            f"{dest_path.parent}. Free some space on the target disk and "
            "retry the install.")

    # A temp name until the copy fully lands: an interrupted install must
    # never leave a file at `dest` that is anything BUT a complete copy -
    # the size check above is what a later run trusts to skip this step.
    tmp_path = dest_path.with_name(dest_path.name + ".partial")
    try:
        shutil.copy2(source, tmp_path)
    except OSError as exc:
        raise GuestBuildError(
            f"the Windows medium {source} could not be copied to "
            f"{dest_path.parent}: {exc.strerror or exc}.") from None
    os.replace(tmp_path, dest_path)


def media_identity(iso_path: str) -> str:
    """Identity of the Windows medium AT THIS PATH: its path and its size.

    Not a content hash: the medium is several gigabytes and gets read in
    full by the build itself anyway. Two different LTSC media never share a
    size; an operator who replaces one in place with a same-sized variant
    must remove the stamp file.

    `iso_path` is now the ON-TARGET COPY console/hooks/install.py placed
    under the guest workdir via copy_windows_medium() above - never the
    wizard's raw 'windows_iso' answer, which may point at live installer
    media long gone by the time this runs (activate happens after the
    reboot; install is the only phase that ever sees both roots - see that
    file's module docstring). A missing file here therefore usually means
    install never copied it (or the copy was removed by hand); that is
    what the refusal below says.
    """
    path = Path(iso_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GuestBuildError(
            f"the Windows medium {iso_path} is not readable: {exc.strerror}. "
            "This should be the copy console install placed under the "
            "guest workdir - re-run the install phase, or place the "
            "medium at this exact path by hand.") from None
    return f"{path.name}:{size}"


# --- the steps ----------------------------------------------------------- #

@dataclass
class Step:
    """One step: its name, whether it is already done, and what it would run.

    `command` is the argv this step WOULD launch, built here and never
    launched at plan time. `fingerprint` is only set on the build step.
    """
    name: str
    already_done: Callable[[], bool]
    run: Callable[[], None]
    command: list[str] | None = None
    fingerprint: Callable[[], str] | None = None
    description: str = ""


def default_virsh(*args: str) -> subprocess.CompletedProcess:
    """Run virsh with a C locale - `shut off` is a localized string."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    return subprocess.run(["virsh", *args], capture_output=True, text=True,
                          env=env, check=False)


def command_label(argv: list[str]) -> str:
    """Name a command the way an operator would: the thing that ran.

    argv[1] is the script only when argv[0] is an interpreter; for
    `virsh start Windows` it is the SUBCOMMAND, and naming it would report
    that "start exited with status 1".
    """
    head = Path(argv[0]).name
    if len(argv) > 1 and head.startswith("python"):
        return Path(argv[1]).name
    return head


def default_runner(argv: list[str], *,
                   env: Mapping[str, str] | None = None) -> None:
    """Launch a step's command, refusing loudly on a non-zero exit.

    `env` is optional and additive in spirit even though `subprocess.run`
    treats it as a full replacement: every caller that needs it (only the
    build step, for TMPDIR - see build_run) passes `dict(os.environ, ...)`
    itself, so PATH and the rest of the parent environment stay intact.
    Every other step passes None, which means "inherit unchanged", exactly
    the behaviour before this parameter existed.
    """
    proc = subprocess.run(argv, check=False, env=env)
    if proc.returncode != 0:
        raise GuestBuildError(
            f"{command_label(argv)} exited with status {proc.returncode}: "
            f"{' '.join(argv)}")


# libvirtd's own per-domain runtime-state directory. The libvirt-daemon
# package chowns it, at install time, to the EXACT user/group its qemu
# processes run as - libvirt-qemu on Debian/Arch, qemu on Fedora/RHEL, and
# no two distributions agree on the literal name (measured on this host,
# 2026-08-28: libvirt-qemu, uid 64055). It exists on any libvirt install
# before a single domain has ever run, which is why resolve_qemu_owner()
# below reads IT rather than a hardcoded name or a commented-out default in
# qemu.conf (this host's own /etc/libvirt/qemu.conf has `#user =
# "libvirt-qemu"` - commented, i.e. "use the packaged default" - so parsing
# that file would find nothing on the very host this was written against).
QEMU_STATE_DIR = "/var/lib/libvirt/qemu"


def resolve_qemu_owner(state_dir: str = QEMU_STATE_DIR, *,
                       stat_fn: Callable[[str], os.stat_result] | None = None,
                       getpwuid: Callable[[int], object] | None = None
                       ) -> tuple[int, int]:
    """(uid, gid) qemu itself runs as - READ from the live system, never a
    literal guessed here. See QEMU_STATE_DIR's own comment for why this
    directory, specifically, is the thing to read.

    Two distinct refusals, both naming their cause rather than raising a
    bare OSError/KeyError: the state directory itself is missing or
    unreadable (libvirt is not installed as expected), or it IS readable but
    owned by a uid with no account in the passwd database (an orphaned
    ownership, e.g. after the qemu account was removed). Either way the
    caller gets a sentence naming what was looked at, not a traceback - the
    same rule the whole module follows (see the module docstring).

    `stat_fn`/`getpwuid` are injectable so tests never touch the real
    /var/lib/libvirt/qemu directory or the real passwd database.
    """
    stat_fn = stat_fn or os.stat
    getpwuid = getpwuid or pwd.getpwuid
    try:
        info = stat_fn(state_dir)
    except OSError as exc:
        raise GuestBuildError(
            f"cannot determine the user qemu runs as: {state_dir} is not "
            f"readable ({exc.strerror or exc}) - is libvirt-daemon "
            "installed on this host?") from None
    try:
        getpwuid(info.st_uid)
    except KeyError:
        raise GuestBuildError(
            f"cannot determine the user qemu runs as: {state_dir} is owned "
            f"by uid {info.st_uid}, which has no account on this system. "
            "The qemu user account may have been removed after libvirt "
            "was installed.") from None
    return info.st_uid, info.st_gid


def _grant_qemu_access(paths: list[Path], owner: tuple[int, int],
                       chown: Callable[[str, int, int], None]) -> None:
    """chown each of `paths` to the user qemu runs as. NEVER touches mode.

    The ISO carries the product key and two passwords in clear (see
    guest/build.py's own printed warning) and is deliberately 0600 - see
    write_secrets() and build.py itself for the same rule applied to the
    secret files. Changing the OWNER is enough for the SAME-uid qemu
    process to open a file it does not have group/other access to; widening
    the mode would be a security regression dressed up as a fix.
    """
    uid, gid = owner
    for path in paths:
        try:
            chown(str(path), uid, gid)
        except OSError as exc:
            raise GuestBuildError(
                f"could not hand {path} to the qemu user (uid {uid}): "
                f"{exc.strerror or exc}. qemu would get 'Permission denied' "
                "opening it.") from None


def _secret(answers: Mapping[str, object], key: str) -> str:
    value = answers.get(key)
    text = "" if value is None else str(value).strip()
    if not text:
        raise GuestBuildError(
            f"the answer '{key}' is empty; the guest cannot be built without "
            "it. Re-run the wizard and provide it.")
    return text


def _secret_is_current(path: Path, expected: str) -> bool:
    """The file exists, is private, and holds exactly the answer given."""
    try:
        if not path.is_file():
            return False
        if path.stat().st_mode & 0o077:
            return False        # build.py refuses a group/world-readable secret
        return path.read_text().strip() == expected
    except OSError:
        return False


def _retro_flag(answers: Mapping[str, object]) -> list[str]:
    """--retro / --no-retro, but only when the wizard actually answered.

    Both scripts default the switch to None, and that None means "do what the
    wizard recorded on this host", never "off". Here we ARE that answer, so
    passing it explicitly is the honest form; omitting the flag when the key
    is absent leaves the marker in charge, exactly as designed.
    """
    if "retro" not in answers:
        return []
    value = answers["retro"]
    if not isinstance(value, bool):
        raise GuestBuildError(
            f"the answer 'retro' expects true or false, got {value!r}")
    return ["--retro" if value else "--no-retro"]


def plan_steps(answers: Mapping[str, object], hw: Mapping[str, object],
               workdir: str, *, virsh: Callable[..., object] | None = None,
               runner: Callable[..., None] | None = None,
               size_of: Callable[[str], int] | None = None,
               python: str | None = None,
               build_inputs: Mapping[str, str] | None = None,
               pci_address_of: Callable[[str], str | None] | None = None,
               qemu_owner: Callable[[], tuple[int, int]] | None = None,
               chown: Callable[[str, int, int], None] | None = None
               ) -> list[Step]:
    """The five steps, in order, for this host's answers. Runs nothing.

    `virsh`, `runner`, `size_of`, `pci_address_of`, `qemu_owner` and `chown`
    are injectable so tests can replace them: the real ones read - and, for
    `start`, drive - the production domain, walk the real /sys/block tree,
    read /var/lib/libvirt/qemu's owner, or actually chown a file only root
    can hand off.
    """
    virsh = virsh or default_virsh
    runner = runner or default_runner
    qemu_owner = qemu_owner or resolve_qemu_owner
    chown = chown or os.chown
    python = python or sys.executable or "python3"

    secrets = {key: _secret(answers, key) for key in SECRET_FILES}
    retro_flag = _retro_flag(answers)

    disk = str(answers.get("dedicated_nvme") or "").strip()
    if not disk:
        raise GuestBuildError(
            "the answer 'dedicated_nvme' is empty; there is no disk to give "
            "the console.")
    # Validated for its own sake only - a wizard/standalone-config answer
    # that was never given at all is a contract error worth naming here.
    # What actually gets USED from here on is windows_media_path(root)
    # below, never this raw value: see the comment on source_iso.
    require_windows_iso_answer(answers)

    data_gib = data_partition_gib(_disk_bytes(disk, hw, size_of))

    root = Path(workdir)
    secret_dir = root / "secrets"
    payload_dir = root / "payload"
    iso_out = root / "nivuus-unattend.iso"
    stamp = build_stamp_path(str(iso_out))
    secret_paths = {key: secret_dir / name for key, name in SECRET_FILES.items()}

    # NOT the raw 'windows_iso' answer validated above: that is the
    # wizard's LIVE-MEDIUM path, which console/hooks/install.py is the only
    # phase to ever see alongside the target it copies onto (see that
    # file's module docstring - by the time this function runs, at
    # activate, the live medium is long gone). Every step below - build,
    # define, the fingerprint - reads THIS copy instead.
    source_iso = str(windows_media_path(root))

    inputs_cache: dict[str, Mapping[str, str]] = {}

    def resolved_inputs() -> Mapping[str, str]:
        if build_inputs is not None:
            return build_inputs
        if "value" not in inputs_cache:
            inputs_cache["value"] = package_inputs()
        return inputs_cache["value"]

    def fingerprint() -> str:
        return build_fingerprint(
            iso=media_identity(source_iso),
            payload_files=payload_tree(str(payload_dir)),
            answers=answers, data_gib=data_gib,
            build_inputs=resolved_inputs())

    payload_cmd = [python, str(GUEST_DIR / "fetch_payload.py"),
                   "--drivers-dir", str(payload_dir)] + retro_flag
    build_cmd = [python, str(GUEST_DIR / "build.py"),
                 "--windows-iso", source_iso,
                 "--drivers-dir", str(payload_dir),
                 "--output", str(iso_out),
                 "--key-file", str(secret_paths["ltsc_key"]),
                 "--password-file", str(secret_paths["admin_password"]),
                 "--apollo-password-file", str(secret_paths["apollo_password"]),
                 "--apollo-user", str(answers.get("apollo_user",
                                                  DEFAULT_APOLLO_USER)),
                 "--hostname", str(answers.get("hostname", DEFAULT_HOSTNAME)),
                 "--data-partition-gb", str(data_gib)] + retro_flag
    # No --replace: redefining an existing domain makes the next boot of a
    # hibernated Windows resume into changed hardware and discard the session.
    #
    # BOTH media, and they are not interchangeable. The production template
    # otherwise boots the NVMe, which at this point is blank: Setup would
    # never start. The official medium is what boots; iso_out - the ISO the
    # build step just produced - is NOT bootable, it is the answer and payload
    # medium Setup reads once running. A later step redefines the domain
    # without either, once the guest is installed.
    define_cmd = [python, str(GUEST_DIR / "domain.py"), "define",
                  "--windows-iso", source_iso,
                  "--unattend-iso", str(iso_out)]
    start_cmd = ["virsh", "start", DOMAIN_NAME]

    def write_secrets() -> None:
        secret_dir.mkdir(parents=True, exist_ok=True)
        secret_dir.chmod(0o700)
        for key, path in secret_paths.items():
            # os.open with an explicit mode, not write_text: the file must
            # never exist in 0644, not even for the instant before a chmod.
            # The mode argument only applies at CREATION, so an already
            # existing file still needs the fchmod below.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, (secrets[key] + "\n").encode("utf-8"))
            finally:
                os.close(fd)

    def secrets_done() -> bool:
        return all(_secret_is_current(path, secrets[key])
                   for key, path in secret_paths.items())

    def payload_done() -> bool:
        # Shallow on purpose: fetch_payload.py is offline-first and cheap to
        # replay, so it re-verifies what is there far better than we could.
        return payload_dir.is_dir() and any(
            p.is_file() for p in payload_dir.rglob("*"))

    def build_done() -> bool:
        if not iso_out.is_file():
            return False
        try:
            expected = fingerprint()
        except (GuestBuildError, OSError):
            return False        # cannot tell => rebuild, never skip
        return build_is_current(stamp, expected)

    # build.py stages driver copies (~1.8 GiB) under tempfile's default
    # location, which is /tmp unless TMPDIR says otherwise - and on this
    # host /tmp is a 10 GiB tmpfs, already 97% full (see CLAUDE.md's
    # tmpfs/swap notes for the same class of failure). stage_dir sits on the
    # SAME filesystem as iso_out (both under the workdir, on the data disk),
    # so the fix is also cheaper than a cross-filesystem TMPDIR would be:
    # build.py's tempfile.TemporaryDirectory ends with an os.replace/rename
    # of the finished ISO, and rename() only avoids a full copy when source
    # and destination share a filesystem.
    stage_dir = root / "tmp"

    def build_run() -> None:
        media_identity(source_iso)      # refuse a missing medium by name
        stage_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TMPDIR=str(stage_dir))
        runner(build_cmd, env=env)
        write_build_stamp(stamp, fingerprint())
        # The ISO lands root:root 0600 (build.py's own deliberate choice -
        # it carries the product key and two passwords in clear). qemu runs
        # as a different, unprivileged user and can neither traverse `root`
        # nor open the ISO without this. Only the OWNER changes; see
        # _grant_qemu_access's own docstring for why the mode never does.
        _grant_qemu_access([root, iso_out], qemu_owner(), chown)

    def defined_xml() -> str | None:
        """The domain's current definition, or None when it is not defined."""
        proc = virsh("dumpxml", DOMAIN_NAME)
        if getattr(proc, "returncode", 1) != 0:
            return None         # unreachable libvirtd is not "already done"
        return proc.stdout or ""

    def domain_defined() -> bool:
        """Is the domain ALREADY what it should be - not merely "a domain
        named Windows", and not merely "carries the right ISOs".

        Two things identify the domain, and neither alone is enough:

          * WHICH MEDIA it carries - both, matching this run's own paths, or
            neither. "It exists" was a sufficient predicate only while this
            step emitted one single, unchanging XML shape; it now emits the
            INSTALL domain, so a pre-existing Windows domain without media -
            the nominal case when reinstalling a console, and the case on
            any host that has ever run one - must NOT read as done unless
            hardware also agrees (see the next point), or `start` would boot
            whatever disk that stale domain happened to carry, silently.
          * WHICH PHYSICAL DISK it hands the guest - domain_matches_disk(),
            never the ISO paths for this: they are FIXED paths under the
            workdir, unchanged by which 'dedicated_nvme' answer was given,
            so changing that answer rebuilds the ISO but, without this
            check, leaves an already-defined domain pointing at the OLD
            disk - exactly the gap round-2 review found.

        A domain carrying NEITHER medium, with the RIGHT disk wired up, is
        the STEADY-STATE (regime) domain guest-ready-watch.py's own
        redefine_steady_state() produces once the guest is provisioned - and
        that is a TERMINAL, LEGITIMATE outcome, not work still to do.
        Treating it as "not done" replays this step at every activation
        retry, without --keyed-varstore (that flag is guest-ready-watch.py's
        own escape hatch, never this step's - see domain.py's
        guard_fresh_varstore() docstring for why); domain.py's varstore
        guard then refuses FOREVER, since the varstore the earlier
        media-carrying `define` created already exists - and the guard's own
        documented remedy, `virsh undefine Windows --nvram`, would
        REINSTALL Windows over a console that already works. A domain
        carrying exactly ONE medium, or the right disk but the wrong media
        state otherwise, is never a state to accept - see the branches
        below.
        """
        xml = defined_xml()
        if xml is None:
            return False
        has_windows_iso = source_iso in xml
        has_unattend_iso = str(iso_out) in xml
        identity_ok = domain_matches_disk(xml, disk, pci_address_of=pci_address_of)
        if has_windows_iso and has_unattend_iso:
            return identity_ok
        if not has_windows_iso and not has_unattend_iso:
            return identity_ok  # the regime domain: terminal when it agrees
        return False             # exactly one medium: half-defined, never done

    def define_argv() -> list[str]:
        """The argv to define with, --replace added only when one is there.

        guard_replace() refuses to redefine an existing domain without it, so
        without this the step would fail on exactly the case domain_defined()
        just declared undone. It is never passed blindly: on a fresh host
        there is nothing to replace, and the flag stays off.

        This DOES discard a hibernated session on a host where "Windows" is
        already the owner's production VM. guard_fresh_varstore() is the
        remaining backstop - it refuses while the old varstore is there, and
        clearing it is deliberate operator action.
        """
        if defined_xml() is None:
            return define_cmd
        return define_cmd + ["--replace"]

    def domain_up() -> bool:
        proc = virsh("domstate", DOMAIN_NAME)
        if getattr(proc, "returncode", 1) != 0:
            return False        # unreachable libvirtd is not "already done"
        return (proc.stdout or "").strip() in DOMAIN_UP_STATES

    return [
        Step("secrets", secrets_done, write_secrets, None, None,
             "write the three 0600 files build.py reads its secrets from"),
        Step("payload", payload_done, lambda: runner(payload_cmd), payload_cmd,
             None, "fetch the offline payload binaries"),
        Step("build", build_done, build_run, build_cmd, fingerprint,
             "build the unattended Windows ISO"),
        # `command` is the argv for a fresh host; define_argv() appends
        # --replace at run time when a stale domain is actually there.
        Step("define", domain_defined, lambda: runner(define_argv()), define_cmd,
             None, "define the libvirt domain from detected hardware"),
        Step("start", domain_up, lambda: runner(start_cmd), start_cmd, None,
             "start the guest so Windows Setup runs unattended"),
    ]


def _disk_bytes(disk: str, hw: Mapping[str, object],
                size_of: Callable[[str], int] | None) -> int:
    """The dedicated disk's size: from `hw` if it already knows, else sysfs."""
    known = hw.get("dedicated_nvme_size_bytes") if hw else None
    if isinstance(known, int) and known > 0:
        return known
    try:
        return (size_of or _sysfs_size)(disk)
    except GuestBuildError:
        raise
    except Exception as exc:    # HardwareError, OSError - named, not raw
        raise GuestBuildError(
            f"cannot read the size of {disk}: {exc}. The console's partition "
            "layout is derived from the real disk, never assumed.") from None


def _sysfs_size(disk: str) -> int:
    """console.hardware.block_device_size_bytes, imported only when needed."""
    sys.path.insert(0, str(HERE))
    from hardware import block_device_size_bytes  # noqa: PLC0415

    return block_device_size_bytes(disk)
