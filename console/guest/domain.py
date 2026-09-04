#!/usr/bin/env python3
"""Generate the production Windows guest domain from detected hardware.

The existing production XML is NOT the source: it carries hypervisor masking,
a fabricated SMBIOS and a vBIOS override that measurements on 2026-08-22
showed unnecessary. This module builds from the requirement instead.

Usage:
    python3 domain.py xml
    sudo python3 domain.py define [--replace]
"""
from __future__ import annotations

import argparse
import re
import os
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"

DOMAIN_NAME = "Windows"
# dhcp-host pins this MAC to 192.168.3.2 in
# /etc/NetworkManager/dnsmasq-shared.d/domain.conf. Changing it silently breaks
# game.allanic.me, the firewalld stream forwards and wake-on-demand.
MAC = "52:54:00:48:e0:3e"
BRIDGE = "internalBridge"
NVRAM_PATH = "/var/lib/libvirt/qemu/nvram/Windows_VARS.fd"
# Quatre partages CIBLES, jamais la racine. La production exposait
# /media/data en entier en lecture/ecriture : un incident dans un invite ouvert
# au streaming atteignait 17 To - Movies, TV Shows, Projects, Seed compris - et
# virtiofsd tourne en root, donc sans le moindre filet de permissions. Restreindre
# a des sous-dossiers supprime cette surface sans rien retirer d utile.
#
# « Console » est le vocabulaire de cette machine : il decrit ce qu elle est pour
# celui qui l utilise, pas ce qui tourne dedans. Il reste juste si l invite change
# d OS, et ne se confond pas avec l hote, qui s appelle Nivuus.
#
# Les lettres commencent a E: - C: est le systeme jetable, D: la partition de jeux.
SHARES = (
    {"source": "/media/data/Downloads", "tag": "Downloads",
     "letter": "E", "label": "Telechargements"},
    {"source": "/media/data/Games", "tag": "Games",
     "letter": "F", "label": "Jeux"},
    {"source": "/media/data/Console", "tag": "Console",
     "letter": "G", "label": "Console"},
    {"source": "/media/backup/Console", "tag": "ConsoleSave",
     "letter": "H", "label": "Sauvegardes Console"},
)
# NOT detected: hardcoded to 16 GiB (16777216 KiB), matching the static
# `vm.nr_hugepages = 8448` pool in /etc/sysctl.d/50-virsh.conf on this host.
# Hugepage sizing is deferred -- see the spec's detection table.
MEMORY_KIB = 16777216


class DomainError(RuntimeError):
    """Raised when the domain cannot be built safely."""


def vcpu_plan(pool: list[int], reserve: int = 2) -> dict:
    """Split isolated host CPUs between the guest and QEMU's own threads.

    `reserve` host CPUs are kept for the emulator and iothreads: they carry the
    vhost and virtiofsd work, and leaving that on a vcpu makes frame pacing
    jitter. The guest gets whole SMT pairs — an odd remainder drops a thread
    rather than hand Windows a lone sibling, which is the mistake the 14x1
    topology made before 2026-07-17.

    Assumes SMT siblings are adjacent in the sorted pool (e.g., (0,1), (2,3)).
    The pool must be contiguous after deduplication and sorting. A machine whose
    SMT pairs are not value-adjacent (e.g., real pairs (0,8),(1,9)) requires a
    code change here, not a differently-ordered argument.
    """
    # Validate contiguity: sorted unique pool must be [min..max] with no gaps.
    # This prevents emulator_cpuset from silently naming CPUs not in the pool.
    # Size check is done on the unique set to reject degenerate inputs.
    unique_sorted = sorted(set(pool))
    if len(unique_sorted) < reserve + 2:
        raise DomainError(
            f"need at least {reserve + 2} isolated CPUs, got {len(unique_sorted)}"
        )
    expected_range = list(range(unique_sorted[0], unique_sorted[-1] + 1))
    if unique_sorted != expected_range:
        gaps = set(expected_range) - set(unique_sorted)
        raise DomainError(
            f"pool is not contiguous; missing CPUs: {sorted(gaps)}"
        )

    ordered = unique_sorted
    guest = ordered[: len(ordered) - reserve]
    if len(guest) % 2:
        guest = guest[:-1]
    emulator = ordered[len(guest):]
    return {
        "vcpus": len(guest),
        "cores": len(guest) // 2,
        "threads": 2,
        "vcpupin": [(i, cpu) for i, cpu in enumerate(guest)],
        "emulator_cpuset": f"{emulator[0]}-{emulator[-1]}",
    }


def existing_uuid(name: str = DOMAIN_NAME) -> str | None:
    """UUID of the domain if it is already defined, else None.

    Without it, `domain.py xml | virsh define` fails with "domain already
    exists with uuid ..." - libvirt mints a fresh UUID for a UUID-less XML and
    refuses to attach it to an existing name. Paid twice on 2026-08-26: once on
    the throwaway bench, where the silent failure made a rebuild replay the
    wipe ISO, and once here, on the production domain, when dropping the
    install media after the cutover.
    """
    out = _virsh("dumpxml", "--inactive", name)
    if out.returncode != 0:
        return None
    match = re.search(r"<uuid>([0-9a-fA-F-]{36})</uuid>", out.stdout)
    return match.group(1) if match else None


def install_media(windows_iso: str | None,
                  unattend_iso: str | None) -> dict | None:
    """The two install media as ONE indivisible pair, or None for steady state.

    They are validated together rather than rendered independently because
    either one alone yields a domain that cannot install: the official medium
    without the answer ISO stops at Setup's first question (there is no
    keyboard on a headless console to answer it), and the answer ISO without
    the official medium is not bootable at all - it carries the answer file
    and the payload, nothing that a firmware can start. A partial pair is
    therefore a refusal, never a half-configured domain.
    """
    windows = (windows_iso or "").strip()
    unattend = (unattend_iso or "").strip()
    if not windows and not unattend:
        return None
    if not windows or not unattend:
        missing = "the Windows medium" if not windows else "the answer ISO"
        raise DomainError(
            f"the install media go together and {missing} is missing: an "
            "install domain needs BOTH the official Windows medium (which "
            "boots) and the ISO build.py produced (which Setup reads once "
            "booted). Pass --windows-iso and --unattend-iso, or neither.")
    if windows == unattend:
        # The two are different objects by construction, so one path in both
        # slots is a caller wiring both flags to the same variable. Left
        # unchecked it renders a domain that looks healthy - two drives, one
        # boot order - and installs nothing: whichever ISO it is, the guest
        # is missing the other half. Setup then sits on the language screen
        # forever, with no error anywhere.
        raise DomainError(
            f"both install media point at the same file ({windows}): the "
            "official Windows medium and the ISO build.py produced are two "
            "different files, and a domain carrying one of them twice can "
            "never complete Setup.")
    return {"windows_iso": windows, "unattend_iso": unattend}


def domain_xml(*, gpu_functions: list[dict], nvme: dict, plan: dict,
               memory_kib: int = MEMORY_KIB, name: str = DOMAIN_NAME,
               mac: str = MAC, bridge: str = BRIDGE,
               nvram_path: str = NVRAM_PATH,
               shares: tuple = SHARES,
               uuid: str | None = None,
               smbios: dict | None = None,
               windows_iso: str | None = None,
               unattend_iso: str | None = None) -> str:
    """Render the production domain XML.

    With both `windows_iso` and `unattend_iso` given, the domain is the
    INSTALL one: it attaches the two media and lets the Windows medium boot.
    With neither, it is the steady-state domain that boots the NVMe. The
    guest is installed with the first and then redefined with the second.
    """
    if len(gpu_functions) < 1:
        raise DomainError("no GPU function to pass through")
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        keep_trailing_newline=True,
    )
    return env.get_template("domain.xml.j2").render(
        name=name, memory_kib=memory_kib, plan=plan, mac=mac, bridge=bridge,
        nvram_path=nvram_path, gpu_functions=gpu_functions, nvme=nvme,
        shares=shares, uuid=uuid, smbios=smbios or {},
        install_media=install_media(windows_iso, unattend_iso),
    )


def guard_replace(*, exists: bool, replace: bool) -> None:
    """Refuse to redefine an existing domain unless asked explicitly.

    Until the cutover, "Windows" is the production VM, possibly hibernated with
    a live session. Silently redefining it would discard that.
    """
    if exists and not replace:
        raise DomainError(
            f"domain {DOMAIN_NAME!r} already exists; pass --replace to redefine it"
        )


def guard_fresh_varstore(*, exists: bool, keyed_varstore: bool = False,
                         path: str = NVRAM_PATH) -> None:
    """Refuse to define when the target varstore file already exists.

    libvirt only copies its <nvram template=...> into the target file the
    first time that file is created; if it already exists, libvirt reuses it
    verbatim. On this host the target is the production varstore, which was
    created long before Secure Boot templates existed and carries no
    Microsoft keys (verified: `grep -c Microsoft` returns 0 on it, 6 on the
    template). Pairing that varstore with the secure-boot loader would boot
    the guest in Setup Mode with Secure Boot effectively disabled -
    `Confirm-SecureBootUEFI` returns False - and nothing in the chain errors.
    The fix is operator action, not code: `virsh undefine Windows --nvram`
    (already the first step of the cutover spec) deletes the stale varstore
    so the next define lets libvirt populate a fresh, keyed one.

    `keyed_varstore=True` is a documented escape hatch, legitimate for
    exactly one caller: guest-ready-watch.py's redefinition of an install
    domain into the steady-state one, once WinRM answers (`domain.py define
    --replace --keyed-varstore`, no --windows-iso/--unattend-iso). At that
    point the varstore at `path` is not the stale, pre-Secure-Boot leftover
    this guard exists to catch - it is the one THIS package's own earlier
    `define --windows-iso ... --unattend-iso ...` step created from the
    Secure Boot template a short while before, already keyed, and it now
    also carries the boot entry Windows Setup itself wrote into it during
    the install this guard just watched succeed. Refusing to reuse it here
    would make the guest's two install media permanent - the very failure
    this flag exists to prevent. It must NEVER be passed on a first
    `define` of a domain whose varstore history is not already known that
    way; only the redefinition path can make that claim, because only it
    was the one that created the varstore it is now reusing.
    """
    if exists and not keyed_varstore:
        raise DomainError(
            f"varstore {path!r} already exists; libvirt will NOT repopulate "
            "it from the Secure Boot template, so the guest would silently "
            "boot with Secure Boot disabled. Run `virsh undefine Windows "
            "--nvram` first, then retry."
        )


def _virsh(*args: str) -> subprocess.CompletedProcess:
    # virsh output is localized; LC_ALL=C keeps state strings parseable.
    return subprocess.run(["virsh", *args], text=True, capture_output=True,
                          env={**os.environ, "LC_ALL": "C"})


def domain_in_listing(output: str, name: str) -> bool:
    """Check if a domain name exists in virsh list output.

    virsh list --all --name emits one name per line, possibly with blank lines.
    Match exactly: a domain named "Windows-LTSC-test" must not satisfy a query
    for "Windows".
    """
    lines = {line.strip() for line in output.split("\n") if line.strip()}
    return name in lines


def domain_exists(name: str = DOMAIN_NAME) -> bool:
    """Check if a domain exists, or raise DomainError if we cannot determine it.

    virsh dominfo returns nonzero identically for "absent", "unreachable", and
    "permission denied". We use virsh list --all --name instead, which returns
    0 if and only if libvirt answered, allowing us to distinguish "doesn't
    exist" from "cannot be determined".
    """
    proc = _virsh("list", "--all", "--name")
    if proc.returncode != 0:
        raise DomainError(
            f"could not determine if domain exists (libvirt unreachable?); "
            f"refusing to proceed: {proc.stderr.strip()}"
        )
    return domain_in_listing(proc.stdout, name)


def build_domain_xml(*, announce: bool = False,
                     windows_iso: str | None = None,
                     unattend_iso: str | None = None) -> str:
    """Detect this machine's hardware and render its domain.

    `announce`, when set, prints the selected passthrough NVMe controller's
    description and PCI address to stderr before rendering. That selection
    wipes a disk; on `define` it must be a decision the operator can read,
    not one baked silently into the XML.

    The two media are passed straight through to domain_xml(): given, they
    make the install domain; omitted, the steady-state one.
    """
    sys.path.insert(0, str(HERE.parent))
    import hardware  # noqa: PLC0415

    gpus = [g for g in hardware.list_gpus() if g["discrete"]]
    if len(gpus) != 1:
        raise DomainError(
            f"expected exactly one discrete GPU, found {[g['slot'] for g in gpus]}"
        )
    topology = hardware.cpu_topology()
    pool = topology["performance_cpus"] or list(range(1, topology["total_cpus"]))
    nvme = hardware.passthrough_nvme()
    if announce:
        print(
            f"selected passthrough NVMe: {nvme['description']} "
            f"({nvme['address']}) -- this disk will be WIPED by the Windows "
            "installer",
            file=sys.stderr,
        )
    return domain_xml(
        gpu_functions=hardware.pci_slot_functions(gpus[0]["slot"]),
        nvme=nvme,
        plan=vcpu_plan(pool),
        uuid=existing_uuid(),
        smbios=hardware.host_smbios(),
        windows_iso=windows_iso,
        unattend_iso=unattend_iso,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Extracted from main() so a caller (a test, notably) can parse an argv
    and inspect the resulting namespace without running any of main()'s own
    I/O - hardware detection, guard checks, or the `virsh define` call
    itself.
    """
    parser = argparse.ArgumentParser(description="Production Windows guest domain")
    parser.add_argument("action", choices=["xml", "define"])
    parser.add_argument("--replace", action="store_true",
                        help="redefine the domain even if it already exists "
                             "-- the next boot of a hibernated Windows then "
                             "resumes into changed hardware and discards the "
                             "saved session")
    # See guard_fresh_varstore()'s own docstring for the one caller this is
    # legitimate for (the media-less redefinition once WinRM answers) and
    # why: everywhere else, an existing varstore is exactly the stale,
    # pre-Secure-Boot leftover the guard exists to catch.
    parser.add_argument("--keyed-varstore", action="store_true",
                        help="assert that the existing NVRAM varstore is "
                             "already keyed from the Secure Boot template - "
                             "true only when THIS domain's own earlier "
                             "define created it, never for an unrelated "
                             "pre-existing domain")
    # Both or neither: see install_media(). Paths, never secrets, so argv is
    # the right place for them.
    parser.add_argument("--windows-iso",
                        help="official Windows medium to boot Setup from; "
                             "requires --unattend-iso")
    parser.add_argument("--unattend-iso",
                        help="the ISO build.py produced, holding the answer "
                             "file and the payload; requires --windows-iso")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    # Imported here, not at module scope: the tests put only
    # console/guest on sys.path, and a top-level import of hardware would
    # break them.
    sys.path.insert(0, str(HERE.parent))
    from hardware import HardwareError  # noqa: PLC0415

    try:
        xml_text = build_domain_xml(announce=(args.action == "define"),
                                    windows_iso=args.windows_iso,
                                    unattend_iso=args.unattend_iso)
        if args.action == "xml":
            print(xml_text)
            return 0
        guard_replace(exists=domain_exists(), replace=args.replace)
        guard_fresh_varstore(exists=Path(NVRAM_PATH).exists(),
                             keyed_varstore=args.keyed_varstore)
        path = Path("/run") / "nivuus-windows-domain.xml"
        # Write with mode 0600 to avoid leaving host topology world-readable
        path.write_text(xml_text)
        path.chmod(0o600)
        try:
            proc = _virsh("define", str(path))
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            return proc.returncode
        finally:
            # Clean up the temp file regardless of success or failure
            path.unlink(missing_ok=True)
    except (DomainError, HardwareError) as exc:
        # Detection and build failures are operator-facing, not bugs: report
        # them plainly. Anything else keeps its traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
