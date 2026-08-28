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


def build_fingerprint(iso: str, payload_files: Mapping[str, str],
                      answers: Mapping[str, object], data_gib: int) -> str:
    """Identity of what ENTERS the image, as a hex digest.

    Dates are deliberately not part of it: the payload gets touched without
    the ISO needing a rebuild, and the reverse happens too. What counts is
    the source medium's identity, the payload tree, the answers that shape
    the image, and the partition size derived from the disk.

    The three secrets are excluded: they shape the guest but the `secrets`
    step has its own predicate, comparing each file's content to its answer.
    Every OTHER answer is included, known or not - a new answer added later
    enters the fingerprint by default, which errs towards rebuilding.
    """
    material = {
        "version": FINGERPRINT_VERSION,
        "iso": iso,
        "payload": {name: payload_files[name] for name in sorted(payload_files)},
        "answers": {key: answers[key] for key in sorted(answers)
                    if key not in SECRET_FILES},
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
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        tree[path.relative_to(root).as_posix()] = digest.hexdigest()
    return tree


def media_identity(iso_path: str) -> str:
    """Identity of the source Windows medium: its path and its size.

    Not a content hash: the medium is several gigabytes and gets read in
    full by the build itself anyway. Two different LTSC media never share a
    size; an operator who replaces one in place with a same-sized variant
    must remove the stamp file.
    """
    path = Path(iso_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GuestBuildError(
            f"the Windows medium {iso_path} is not readable: {exc.strerror}. "
            "Point the wizard's 'windows_iso' answer at an existing file, or "
            "place the downloaded medium there.") from None
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


def default_runner(argv: list[str]) -> None:
    """Launch a step's command, refusing loudly on a non-zero exit."""
    proc = subprocess.run(argv, check=False)
    if proc.returncode != 0:
        raise GuestBuildError(
            f"{Path(argv[1] if len(argv) > 1 else argv[0]).name} exited with "
            f"status {proc.returncode}: {' '.join(argv)}")


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
               runner: Callable[[list[str]], None] | None = None,
               size_of: Callable[[str], int] | None = None,
               python: str | None = None) -> list[Step]:
    """The five steps, in order, for this host's answers. Runs nothing.

    `virsh`, `runner` and `size_of` are injectable so tests can replace them:
    the real ones read - and, for `start`, drive - the production domain.
    """
    virsh = virsh or default_virsh
    runner = runner or default_runner
    python = python or sys.executable or "python3"

    secrets = {key: _secret(answers, key) for key in SECRET_FILES}
    retro_flag = _retro_flag(answers)

    disk = str(answers.get("dedicated_nvme") or "").strip()
    if not disk:
        raise GuestBuildError(
            "the answer 'dedicated_nvme' is empty; there is no disk to give "
            "the console.")
    source_iso = str(answers.get("windows_iso") or "").strip()
    if not source_iso:
        raise GuestBuildError(
            "the answer 'windows_iso' is empty; the Windows LTSC medium is "
            "what the guest is built from.")

    data_gib = data_partition_gib(_disk_bytes(disk, hw, size_of))

    root = Path(workdir)
    secret_dir = root / "secrets"
    payload_dir = root / "payload"
    iso_out = root / "nivuus-unattend.iso"
    stamp = build_stamp_path(str(iso_out))
    secret_paths = {key: secret_dir / name for key, name in SECRET_FILES.items()}

    def fingerprint() -> str:
        return build_fingerprint(
            iso=media_identity(source_iso),
            payload_files=payload_tree(str(payload_dir)),
            answers=answers, data_gib=data_gib)

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
    define_cmd = [python, str(GUEST_DIR / "domain.py"), "define"]
    start_cmd = ["virsh", "start", DOMAIN_NAME]

    def write_secrets() -> None:
        secret_dir.mkdir(parents=True, exist_ok=True)
        secret_dir.chmod(0o700)
        for key, path in secret_paths.items():
            path.write_text(secrets[key] + "\n")
            path.chmod(0o600)

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

    def build_run() -> None:
        media_identity(source_iso)      # refuse a missing medium by name
        runner(build_cmd)
        write_build_stamp(stamp, fingerprint())

    def domain_defined() -> bool:
        return getattr(virsh("dumpxml", DOMAIN_NAME), "returncode", 1) == 0

    def domain_up() -> bool:
        proc = virsh("domstate", DOMAIN_NAME)
        if getattr(proc, "returncode", 1) != 0:
            return False        # unreachable libvirtd is not "already done"
        return (proc.stdout or "").strip() != "shut off"

    return [
        Step("secrets", secrets_done, write_secrets, None, None,
             "write the three 0600 files build.py reads its secrets from"),
        Step("payload", payload_done, lambda: runner(payload_cmd), payload_cmd,
             None, "fetch the offline payload binaries"),
        Step("build", build_done, build_run, build_cmd, fingerprint,
             "build the unattended Windows ISO"),
        Step("define", domain_defined, lambda: runner(define_cmd), define_cmd,
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
