#!/usr/bin/env python3
"""Read-only resolve phase for the console package.

Returns what the static manifest cannot know: which vendor:device ids to
hand vfio-pci, which CPUs to leave tickless, how many hugepages the guest
needs. Or it REFUSES, with a sentence - and that refusal reaches the
operator before a single byte is written to their disk, because the engine
runs this before partition().

A REFUSAL IS DATA, NOT AN EXCEPTION. Every path through this hook that can
fail - a GPU snapshot missing its slot, a memory figure that is not a
number, a host too small to host a guest - must end in a `refuse` event,
never an uncaught exception. An uncaught exception gives the operator a
non-zero exit and a traceback they cannot act on; a `refuse` event gives
them a sentence, before their disk is touched.

READ-ONLY IS A CONVENTION HERE, NOT A SANDBOX. Nothing stops this process
from writing; what the pipeline depends on is that it does not, and that the
engine never uses anything it might write. Since this runs before
partition(), an accidental write lands on the installer's LIVE filesystem.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import hardware  # noqa: E402

# Guest memory budget. NOT "half the host" - this project already made and
# corrected that exact mistake: CLAUDE.md's "Hugepages pool halved" finding
# records a 16584-page pool (double the VM's real need) that left the host
# swapping, cut down to 8448 pages (~16896 MiB; the VM itself uses ~8205
# MiB). GUEST_MIB_DEFAULT is pinned to that measured, settled figure -
# rounded to 16384 MiB (16 GiB) - not derived from host size. Hugepages are
# reserved at BOOT and NEVER handed back, so over-asking costs the host
# permanently; that is exactly what made it swap the first time. Do NOT
# "improve" this back to host_mib // 2 - that IS the mistake, not a
# conservative default.
GUEST_MIB_DEFAULT = 16384
# Below this, the guest is not a usable gaming console - refuse rather than
# shrink further (see guest_memory_mib).
GUEST_MIB_MIN = 8192


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def guest_memory_mib(hw: dict):
    """GUEST_MIB_DEFAULT, clamped DOWN if the host cannot spare it.

    Returns (mib, reason). On success `reason` is None. On failure `mib` is
    None and `reason` names precisely what disqualifies this host - main()
    turns that straight into a `refuse` event instead of doing arithmetic
    that can raise.

    The guest never gets more than GUEST_MIB_DEFAULT (see its docstring for
    why that is a fixed, measured figure rather than a fraction of host
    RAM), and never more than half the host either - `min(DEFAULT, total //
    2)`. Below GUEST_MIB_MIN even after that clamp, the machine is refused
    rather than squeezed further:

    Two distinct failure classes, refused for two distinct reasons:

    - The figure itself is unusable (not a number, or negative) - a
      malformed snapshot, the same class of problem passthrough_nvme() and
      isolation_plan() already refuse rather than guess through.
    - The figure is a real number but the host is too small even for the
      floor: a console that boots but performs unusably is worse than one
      that explains why it cannot be installed on this machine - the same
      reasoning "PCI passthrough only" already applies elsewhere in this
      hook.
    """
    total = hw.get("memory_mib")
    if total is None or total == 0:
        # No RAM figure at all: there is nothing to strand-check or clamp
        # against, so fall back to the floor - the smallest budget a usable
        # guest needs - rather than assume the generous default on a host
        # we know nothing about.
        return GUEST_MIB_MIN, None
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None, f"quantite de memoire hote illisible : {total!r}"
    if total < 0:
        return None, f"quantite de memoire hote negative : {total!r}"
    budget = min(GUEST_MIB_DEFAULT, total // 2)
    if budget < GUEST_MIB_MIN:
        return None, (
            f"cet hote n'a que {int(total)} MiB de RAM ; il en faut au moins "
            f"{GUEST_MIB_MIN * 2} MiB pour heberger la console sans priver "
            "l'hote de toute sa memoire (les hugepages sont reserves au "
            "demarrage et ne sont jamais rendus)")
    return int(budget), None


def dedicated_nvme_size_bytes(nvme: dict, wanted: str) -> int | None:
    """Best-effort size, in bytes, of the NVMe chosen for passthrough.

    Read HERE, and only here: resolve runs before partition() and before the
    disk is bound to vfio-pci (see the module docstring), so this is the one
    phase where the kernel still exposes a block device for it at all.
    guest_steps.py's own fallback - hardware.block_device_size_bytes()
    against /sys/block, at ACTIVATE time - has nothing left to read once the
    target's kernel command line takes vfio-pci ownership of the device from
    boot onward: measured on this very host, `lspci -nnk -d ::0108` shows the
    dedicated NVMe bound to vfio-pci while `ls /sys/block` lists only the
    host's own disk. See guest_steps.py::_disk_bytes for the consuming side.

    `wanted` is the raw /dev/... answer when the operator named a disk - the
    live-ISO path this package favours (see hardware.select_passthrough_nvme
    for why). Without one, `nvme["address"]` is all resolve has, so the
    block device is found by reversing the same sysfs lookup
    pci_address_for_device() uses.

    Never raises and never turns into a `refuse` on its own: an unreadable
    size here must not withhold GPU/NVMe passthrough from an otherwise valid
    machine. guest_steps.py falls back to sysfs by itself later, and refuses
    BY NAME only if that also fails - this function returning None is
    exactly the "at default" case its docstring describes.
    """
    device = wanted or hardware.block_device_for_pci_address(nvme.get("address") or "")
    if not device:
        return None
    try:
        return hardware.block_device_size_bytes(device)
    except hardware.HardwareError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.parse_args()
    ctx = json.load(sys.stdin)
    hw = ctx.get("hw") or {}
    answers = ctx.get("answers") or {}

    emit({"event": "progress", "pct": 10, "msg": "Analyse du materiel"})

    # Checked first and independently of the GPU/NVMe/CPU detection below: a
    # malformed or insufficient memory figure disqualifies the machine on
    # its own, and failing fast here avoids emitting GPU-detection progress
    # for a machine that is going to be refused anyway.
    guest_mib, mem_reason = guest_memory_mib(hw)
    if mem_reason:
        emit({"event": "refuse", "reason": mem_reason})
        return 0

    discrete = [g for g in hw.get("gpus") or [] if g.get("discrete")]
    if not discrete:
        emit({"event": "refuse",
              "reason": "aucun GPU dedie detecte : la console a besoin d'une "
                        "carte graphique a passer entierement a la VM"})
        return 0

    # A discrete GPU entry with no 'slot' key is a malformed snapshot, not a
    # machine to crash on: .get() rather than [...] so this refuses like any
    # other bad-detection path instead of raising KeyError.
    slot = discrete[0].get("slot") or ""
    if not slot:
        emit({"event": "refuse",
              "reason": "le GPU discret detecte n'a pas d'emplacement PCI "
                        "(slot) connu"})
        return 0

    ids = hardware.vfio_ids_for_slot(slot)
    if not ids:
        emit({"event": "refuse",
              "reason": f"impossible de lire les identifiants PCI du GPU {slot}"})
        return 0

    emit({"event": "progress", "pct": 40, "msg": f"GPU {slot} : {','.join(ids)}"})

    # PCI passthrough only, by decision: a SATA disk or an NVMe sharing its
    # IOMMU group with the host cannot be handed over, and falling back to a
    # disk image would silently deliver something slower than what was asked.
    #
    # THE OPERATOR'S ANSWER DRIVES THE SELECTION - it is not a cross-check on
    # a choice made without it. Deriving the device from "the NVMe that does
    # not back the host root" works only on an already-installed host: on the
    # installer ISO the root is the live image, no PCI disk backs it, and
    # that selector refuses on every machine. The answer is the one piece of
    # information that exists on both paths, so it is the input; the
    # host-root exclusion becomes a safety assertion on the result.
    #
    # The passthrough dict is a PCI FUNCTION - {address, id, function, bus,
    # slot, domain}. There is no `device` key, so the operator's /dev/...
    # answer must be translated to a PCI address before anything can be done
    # with it.
    wanted = (answers.get("dedicated_nvme") or "").strip()
    wanted_address = None
    if wanted:
        wanted_address = hardware.pci_address_for_device(wanted)
        if wanted_address is None:
            emit({"event": "refuse",
                  "reason": f"impossible de resoudre {wanted} vers une adresse PCI ; "
                            "ce disque ne peut pas etre passe a la VM"})
            return 0

    # passthrough_nvme() RAISES HardwareError on every failure path - no NVMe,
    # an answer that is not an NVMe controller, a device that backs the host
    # root, an ambiguous auto-selection - and never returns empty. Catching it
    # is what turns "this machine will not do" into a sentence the operator
    # reads before their disk is touched, instead of a traceback and a
    # non-zero exit they cannot act on.
    try:
        nvme = hardware.passthrough_nvme(wanted_address=wanted_address)
    except hardware.HardwareError as exc:
        emit({"event": "refuse",
              "reason": f"aucun NVMe dedie utilisable en passthrough PCI : {exc}"})
        return 0

    ids.append(nvme["id"])

    # Best-effort: never gates the refuse/accept decision above (see the
    # function's own docstring for why). Computed now, while resolve still
    # has `wanted` and `nvme` in scope, so it can flow into the same
    # `platform` event below rather than needing a second hardware pass.
    nvme_size_bytes = dedicated_nvme_size_bytes(nvme, wanted)

    # isolation_plan RAISES on a malformed CPU snapshot rather than translating
    # it: what it returns lands on the kernel command line, and the allowlist
    # downstream guards shell metacharacters only - it would happily pass a
    # semantically nonsensical range like "-1-1". Same contract as
    # passthrough_nvme above, so the same treatment: a refusal, not a traceback.
    try:
        plan = hardware.isolation_plan(hw.get("cpu") or {})
    except hardware.HardwareError as exc:
        emit({"event": "refuse",
              "reason": f"topologie CPU inexploitable : {exc}"})
        return 0

    cmdline = [f"vfio-pci.ids={','.join(ids)}"]
    if plan["nohz_full"]:
        cmdline.append(f"nohz_full={plan['nohz_full']}")

    emit({"event": "progress", "pct": 80, "msg": "Plan materiel resolu"})
    platform_event = {"event": "platform",
                      "kernel-cmdline": cmdline,
                      "modules": [],
                      "hugepages-mib": guest_mib}
    if nvme_size_bytes is not None:
        # snake_case, unlike the three kernel-facing keys above: this one is
        # never going to end up on the command line, it is meant for the
        # `hw` dict guest_steps.plan_steps() reads (see _disk_bytes() there),
        # which speaks the same snake_case as the rest of a hw snapshot
        # (memory_mib, total_cpus, ...).
        platform_event["dedicated_nvme_size_bytes"] = nvme_size_bytes
    emit(platform_event)
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
