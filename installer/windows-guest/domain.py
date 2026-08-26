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


def domain_xml(*, gpu_functions: list[dict], nvme: dict, plan: dict,
               memory_kib: int = MEMORY_KIB, name: str = DOMAIN_NAME,
               mac: str = MAC, bridge: str = BRIDGE,
               nvram_path: str = NVRAM_PATH,
               shares: tuple = SHARES,
               uuid: str | None = None) -> str:
    """Render the production domain XML."""
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
        shares=shares, uuid=uuid,
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


def guard_fresh_varstore(*, exists: bool, path: str = NVRAM_PATH) -> None:
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
    """
    if exists:
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


def build_domain_xml(*, announce: bool = False) -> str:
    """Detect this machine's hardware and render its domain.

    `announce`, when set, prints the selected passthrough NVMe controller's
    description and PCI address to stderr before rendering. That selection
    wipes a disk; on `define` it must be a decision the operator can read,
    not one baked silently into the XML.
    """
    sys.path.insert(0, str(HERE.parent))
    from common import hardware  # noqa: PLC0415

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
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Production Windows guest domain")
    parser.add_argument("action", choices=["xml", "define"])
    parser.add_argument("--replace", action="store_true",
                        help="redefine the domain even if it already exists "
                             "-- the next boot of a hibernated Windows then "
                             "resumes into changed hardware and discards the "
                             "saved session")
    args = parser.parse_args()

    # Imported here, not at module scope: the tests put only
    # installer/windows-guest on sys.path, and a top-level import of
    # common.hardware would break them.
    sys.path.insert(0, str(HERE.parent))
    from common.hardware import HardwareError  # noqa: PLC0415

    try:
        xml_text = build_domain_xml(announce=(args.action == "define"))
        if args.action == "xml":
            print(xml_text)
            return 0
        guard_replace(exists=domain_exists(), replace=args.replace)
        guard_fresh_varstore(exists=Path(NVRAM_PATH).exists())
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
