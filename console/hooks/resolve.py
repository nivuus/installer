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

# The guest gets half of host RAM, clamped: enough for a gaming guest, never
# so much that the host starts swapping. Hugepages are reserved at boot and
# never handed back, so over-asking costs the host permanently.
GUEST_MIB_MIN = 8192
GUEST_MIB_MAX = 32768


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def guest_memory_mib(hw: dict):
    """Half of host RAM, clamped to [GUEST_MIB_MIN, GUEST_MIB_MAX].

    Returns (mib, reason). On success `reason` is None. On failure `mib` is
    None and `reason` names precisely what disqualifies this host - main()
    turns that straight into a `refuse` event instead of doing arithmetic
    that can raise.

    Two distinct failure classes, refused for two distinct reasons:

    - The figure itself is unusable (not a number, or negative) - a
      malformed snapshot, the same class of problem passthrough_nvme() and
      isolation_plan() already refuse rather than guess through.
    - The figure is a real number but the host is too small: hugepages are
      reserved at BOOT and NEVER handed back (see CLAUDE.md), so
      unconditionally applying GUEST_MIB_MIN regardless of host size would
      let a 4 or 8 GiB host reserve all - or more than all - of its RAM for
      a guest, leaving nothing for the host itself. That is the same
      mistake "PCI passthrough only" already exists to prevent: a console
      that boots but performs unusably is worse than one that explains why
      it cannot be installed on this machine. So this refuses rather than
      shrinking the floor.
    """
    total = hw.get("memory_mib")
    if total is None or total == 0:
        # No RAM figure at all: there is nothing to strand-check against,
        # so fall back to the floor as a best effort rather than refuse on
        # missing data the detection layer should have supplied.
        return GUEST_MIB_MIN, None
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None, f"quantite de memoire hote illisible : {total!r}"
    if total < 0:
        return None, f"quantite de memoire hote negative : {total!r}"
    half = total // 2
    if half < GUEST_MIB_MIN:
        return None, (
            f"cet hote n'a que {int(total)} MiB de RAM ; il en faut au moins "
            f"{GUEST_MIB_MIN * 2} MiB pour heberger la console sans priver "
            "l'hote de toute sa memoire (les hugepages sont reserves au "
            "demarrage et ne sont jamais rendus)")
    return max(GUEST_MIB_MIN, min(GUEST_MIB_MAX, int(half))), None


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
    # passthrough_nvme() RAISES HardwareError on every failure path - no NVMe,
    # ambiguous candidate, disk owned by the host - and never returns empty.
    # Catching it is what turns "this machine will not do" into a sentence the
    # operator reads before their disk is touched, instead of a traceback and
    # a non-zero exit they cannot act on.
    wanted = (answers.get("dedicated_nvme") or "").strip()
    try:
        nvme = hardware.passthrough_nvme()
    except hardware.HardwareError as exc:
        emit({"event": "refuse",
              "reason": f"aucun NVMe dedie utilisable en passthrough PCI : {exc}"})
        return 0

    # The dict is a PCI FUNCTION - {address, id, function, bus, slot, domain}.
    # There is no `device` key, so the operator's /dev/... answer has to be
    # translated before it can be compared. Skipping this check would let the
    # install hand over a disk the operator never chose.
    if wanted:
        chosen = hardware.pci_address_for_device(wanted)
        if chosen is None:
            emit({"event": "refuse",
                  "reason": f"impossible de resoudre {wanted} vers une adresse PCI ; "
                            "ce disque ne peut pas etre passe a la VM"})
            return 0
        if chosen != nvme["address"]:
            emit({"event": "refuse",
                  "reason": f"le disque demande ({wanted}, {chosen}) n'est pas "
                            f"celui qui peut etre detache ({nvme['address']})"})
            return 0

    ids.append(nvme["id"])

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
    emit({"event": "platform",
          "kernel-cmdline": cmdline,
          "modules": [],
          "hugepages-mib": guest_mib})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
