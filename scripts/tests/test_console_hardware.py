#!/usr/bin/env python3
"""Tests for the PCI/NVMe/CPU-isolation helpers used by the console package.

The parsers are pure: they take captured `lspci` text. `_whole_disk_name` is
exercised against a fake sysfs tree built in a temp dir. So these tests run
anywhere and do not depend on the machine they execute on.

Run: python3 scripts/tests/test_console_hardware.py
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console"))

import hardware  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: raised {type(exc).__name__}, want {exc_type.__name__}")
        return
    failures.append(f"{label}: did not raise {exc_type.__name__}")


# Captured from the Nivuus host on 2026-08-22 (`lspci -nn -D`).
LSPCI = """\
0000:00:02.0 VGA compatible controller [0300]: Intel Corporation AlderLake-S GT1 [8086:4680] (rev 0c)
0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786] (rev a1)
0000:01:00.1 Audio device [0403]: NVIDIA Corporation AD104 High Definition Audio Controller [10de:22bc] (rev a1)
0000:02:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller 980 (DRAM-less) [144d:a809]
0000:03:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller SM981/PM981/PM983 [144d:a808]
"""

fns = hardware.parse_pci_functions(LSPCI, "01:00.0")
check("two functions in the GPU slot", len(fns), 2)
check("first function address", fns[0]["address"], "0000:01:00.0")
check("first function id", fns[0]["id"], "10de:2786")
check("first function number", fns[0]["function"], "0x0")
check("second function number", fns[1]["function"], "0x1")
check("bus is hex-prefixed", fns[0]["bus"], "0x01")
check("slot is hex-prefixed", fns[0]["slot"], "0x00")
check("domain is hex-prefixed", fns[0]["domain"], "0x0000")

# A fully-qualified slot must behave identically to a short one: callers get
# the address from either form depending on which lspci flags they used.
check(
    "qualified slot matches short slot",
    hardware.parse_pci_functions(LSPCI, "0000:01:00.0"),
    fns,
)

check("unknown slot yields nothing", hardware.parse_pci_functions(LSPCI, "09:00.0"), [])
check("empty input yields nothing", hardware.parse_pci_functions("", "01:00.0"), [])

# --- list_gpus/cpu_topology : ce que domain.py appelle apres le reboot ---- #
# domain.py calls these two directly, and it runs after the reboot on a host
# where installer/ may never have existed. They are copied, not imported:
# console/ importing installer/ is what this whole split exists to prevent.
check("console.hardware expose list_gpus",
      callable(getattr(hardware, "list_gpus", None)), True)
check("console.hardware expose cpu_topology",
      callable(getattr(hardware, "cpu_topology", None)), True)

# Parsing is what can be tested without hardware; detection itself cannot.
# Feed the parser the same shape `lspci -nn` produces. The first two lines
# are captured verbatim from the Nivuus host on 2026-08-27
# (`lspci -nn | grep -iE 'vga|3d|display'`) - it has exactly the targeted
# layout: an Intel iGPU plus a discrete NVIDIA. This host has no class-[0302]
# "3D controller" card (a compute/second GPU, exactly what a passthrough
# console can meet), so the third line is not captured here - it is built in
# the real `lspci -nn` shape around the well-known real device id 10de:102d
# (NVIDIA GK210GL "Tesla K80", confirmed present in this machine's own
# /usr/share/misc/pci.ids), not invented text.
SAMPLE_LSPCI_VGA = """\
00:02.0 VGA compatible controller [0300]: Intel Corporation AlderLake-S GT1 [8086:4680] (rev 0c)
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786] (rev a1)
02:00.0 3D controller [0302]: NVIDIA Corporation GK210GL [Tesla K80] [10de:102d] (rev a1)
"""

gpus = hardware.parse_gpus(SAMPLE_LSPCI_VGA)
check("un GPU discret est reconnu", [g["slot"] for g in gpus if g["discrete"]],
      ["01:00.0", "02:00.0"])
check("l iGPU n est pas discret", [g["discrete"] for g in gpus if g["slot"] == "00:02.0"], [False])
threed = [g for g in gpus if g["slot"] == "02:00.0"]
check("la ligne « 3D controller » (classe 0302) est reconnue comme un GPU",
      len(threed), 1)
check("la carte 3D est classee nvidia et discrete",
      [(g["vendor"], g["discrete"]) for g in threed], [("nvidia", True)])

# cpu_topology() actually runs against this machine's real sysfs - there is
# no captured-text form to feed it (unlike parse_gpus), so only its shape and
# internal consistency can be checked without hardcoding a value specific to
# whatever CPU happens to run the test suite.
topo = hardware.cpu_topology()
check("cpu_topology rend les cles attendues",
      {"model", "total_cpus", "hybrid", "performance_cpus"} <= set(topo.keys()), True)

total = topo["total_cpus"]
performance = topo["performance_cpus"]
check("performance_cpus ne contient pas de doublon",
      len(performance), len(set(performance)))
check("performance_cpus tient dans le nombre total de cpus en ligne",
      all(0 <= c < total for c in performance), True)
efficiency = set(range(total)) - set(performance)
check("performance et le reste des coeurs ne se recoupent pas",
      set(performance) & efficiency, set())
check("leur reunion couvre tous les cpus en ligne",
      set(performance) | efficiency, set(range(total)))
check("le drapeau hybride s accorde avec l existence de deux classes de coeurs",
      topo["hybrid"], 0 < len(performance) < total)

# Test the whole-disk name extractor against a fake sysfs tree (same approach
# as scripts/tests/test_pcie_wifi_link_guard.sh), so this does not depend on
# what block devices happen to exist on the machine running the test.
with tempfile.TemporaryDirectory() as fake_root:
    def _make_partition(disk, part):
        disk_dir = os.path.join(fake_root, disk)
        part_dir = os.path.join(disk_dir, part)
        os.makedirs(part_dir, exist_ok=True)
        with open(os.path.join(part_dir, "partition"), "w") as fh:
            fh.write("3\n")
        os.symlink(os.path.join(disk, part), os.path.join(fake_root, part))

    def _make_whole_disk(disk):
        os.makedirs(os.path.join(fake_root, disk), exist_ok=True)

    _make_partition("nvme0n1", "nvme0n1p3")
    _make_whole_disk("sda")
    _make_partition("mmcblk0", "mmcblk0p1")

    check(
        "partition name resolves to parent disk",
        hardware._whole_disk_name("nvme0n1p3", sysfs_root=fake_root),
        "nvme0n1",
    )
    check(
        "whole-disk name unchanged",
        hardware._whole_disk_name("sda", sysfs_root=fake_root),
        "sda",
    )
    check(
        "mmcblk-style partition resolves to parent disk",
        hardware._whole_disk_name("mmcblk0p1", sysfs_root=fake_root),
        "mmcblk0",
    )
    check(
        "missing node falls back to the name unchanged",
        hardware._whole_disk_name("nonexistent0", sysfs_root=fake_root),
        "nonexistent0",
    )

ctrls = hardware.parse_nvme_controllers(LSPCI)
check("two NVMe controllers", len(ctrls), 2)
check("host controller listed", ctrls[0]["address"], "0000:02:00.0")
check("guest controller listed", ctrls[1]["address"], "0000:03:00.0")

picked = hardware.select_passthrough_nvme(ctrls, {"0000:02:00.0"})
check("picks the controller that is not the host root", picked["address"], "0000:03:00.0")
check("picked id", picked["id"], "144d:a808")

# Refusing to guess is the whole point: this disk gets wiped.
check_raises(
    "host unknown is never a guess",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(ctrls[:1], set()),
)
check_raises(
    "no candidate left",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(
        ctrls, {"0000:02:00.0", "0000:03:00.0"}
    ),
)
check_raises(
    "no controller at all",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme([], {"0000:02:00.0"}),
)

# --- le cas ISO LIVE : la racine hote n est PAS identifiable -------------- #
# C est le chemin PRINCIPAL du package, et l ancien selecteur y refusait sur
# TOUTES les machines : sur un support live la racine est l image live, aucun
# disque PCI ne la porte, donc host_root_pci_addresses() rend None et un
# selecteur qui en derive n a jamais de quoi decider. La reponse de
# l operateur (dedicated_nvme) est la seule information presente des deux
# cotes : elle CHOISIT, et l exclusion de la racine hote devient une
# assertion qui, faute de racine identifiable, ne s applique simplement pas.
picked_live = hardware.select_passthrough_nvme(
    ctrls, None, wanted_address="0000:03:00.0")
check("ISO live : la reponse de l operateur choisit malgre une racine "
      "inconnue", picked_live["address"], "0000:03:00.0")
check("ISO live : et l id suit", picked_live["id"], "144d:a808")

# Meme chose vue depuis la fonction pure qui decompose l adresse.
resolved_live = hardware.resolve_passthrough_nvme(
    LSPCI, None, wanted_address="0000:03:00.0")
check("ISO live : l adresse est decomposee", resolved_live["bus"], "0x03")

# Sans reponse ET sans racine identifiable, il ne reste rien pour decider :
# le refus d origine tient, c est le seul cas ou il etait justifie.
check_raises(
    "ISO live sans reponse : plus rien pour decider, refus",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(ctrls, None),
)

# Une reponse qui ne correspond a AUCUN controleur NVMe doit etre refusee, et
# le refus doit nommer ce qui est attendu - sinon l operateur ne peut pas
# corriger sa reponse.
try:
    hardware.select_passthrough_nvme(ctrls, None,
                                     wanted_address="0000:00:02.0")
    failures.append("une adresse qui n est pas un NVMe aurait du lever")
except hardware.HardwareError as exc:
    check("le refus nomme l adresse demandee", "0000:00:02.0" in str(exc), True)
    check("le refus nomme les controleurs connus",
          "0000:03:00.0" in str(exc), True)

# L assertion de securite reste vraie quand la racine EST identifiable : une
# reponse qui designe le disque de l hote est refusee, elle ne devient pas
# une autorisation parce que l operateur l a ecrite.
try:
    hardware.select_passthrough_nvme(ctrls, {"0000:02:00.0"},
                                     wanted_address="0000:02:00.0")
    failures.append("le disque de la racine hote aurait du etre refuse")
except hardware.HardwareError as exc:
    check("le refus dit que le disque porte la racine hote",
          "host root" in str(exc), True)

# The template consumes nvme.bus / nvme.slot / nvme.function, so the resolved
# record must be decomposed, not just {address, id}.
resolved = hardware.resolve_passthrough_nvme(LSPCI, {"0000:02:00.0"})
check("resolved address", resolved["address"], "0000:03:00.0")
check("resolved bus", resolved["bus"], "0x03")
check("resolved slot", resolved["slot"], "0x00")
check("resolved function", resolved["function"], "0x0")
check("resolved id", resolved["id"], "144d:a808")

# --- isolation_plan: la derivation que le moteur ne fait plus ------------- #
# Regression guard: les deux chemins heureux du brief doivent rester verts.
check("hybrid: les P-cores sont isoles",
      hardware.isolation_plan({"hybrid": True, "performance_cpus": [0, 1, 2, 3],
                               "total_cpus": 8}),
      {"isolcpus": "0-3", "nohz_full": "0-3"})
check("uniforme: tout sauf cpu0",
      hardware.isolation_plan({"hybrid": False, "performance_cpus": [],
                               "total_cpus": 4}),
      {"isolcpus": "1-3", "nohz_full": "1-3"})
check("snapshot vide ne leve pas",
      hardware.isolation_plan({}), {"isolcpus": "", "nohz_full": ""})
check("un seul cpu: rien a isoler",
      hardware.isolation_plan({"hybrid": False, "performance_cpus": [],
                               "total_cpus": 1}),
      {"isolcpus": "", "nohz_full": ""})
check("cpu_ranges compresse", hardware.cpu_ranges([0, 1, 2, 3, 8]), "0-3,8")
check("cpu_ranges vide", hardware.cpu_ranges([]), "")

# ABSENT (rien a isoler) et MALFORME (snapshot qui ment) sont distingues.
# isolation_plan() finit sur la ligne de commande noyau (nohz_full=...) et
# la liste blanche GRUB en aval ne garde que l injection shell, pas la
# validite semantique - donc filtrer les entrees fautives laisserait passer
# une plage plausible tiree d un snapshot dont on vient de prouver qu il
# est faux. Refuser est le seul comportement sur qui compter.
check_raises(
    "performance_cpus avec une chaine",
    hardware.HardwareError,
    lambda: hardware.isolation_plan(
        {"hybrid": True, "performance_cpus": [0, "x"], "total_cpus": 8}
    ),
)
check_raises(
    "performance_cpus avec True (piege bool-est-un-int)",
    hardware.HardwareError,
    lambda: hardware.isolation_plan(
        {"hybrid": True, "performance_cpus": [0, True], "total_cpus": 8}
    ),
)
check_raises(
    "performance_cpus avec un numero de cpu negatif",
    hardware.HardwareError,
    lambda: hardware.isolation_plan(
        {"hybrid": True, "performance_cpus": [-1, 0, 1], "total_cpus": 8}
    ),
)
check_raises(
    "performance_cpus avec un numero de cpu >= total_cpus",
    hardware.HardwareError,
    lambda: hardware.isolation_plan(
        {"hybrid": True, "performance_cpus": [0, 1, 8], "total_cpus": 8}
    ),
)

# --- pci_address_for_device : le pont entre /dev/... et une adresse PCI --- #
check("un chemin introuvable rend None",
      hardware.pci_address_for_device("/dev/nexistepas0n1"), None)

# Sur un arbre sysfs factice, pour que la traduction /dev/... -> adresse PCI
# soit verifiee sans dependre des disques de la machine qui execute le test.
with tempfile.TemporaryDirectory() as fake_block:
    os.makedirs(os.path.join(fake_block, "nvme9n1", "device"))
    os.makedirs(os.path.join(fake_block, "pci", "0000:03:00.0"))
    os.symlink(os.path.join(fake_block, "pci", "0000:03:00.0"),
               os.path.join(fake_block, "nvme9n1", "device", "device"))
    check("un disque connu se traduit en adresse PCI",
          hardware.pci_address_for_device("/dev/nvme9n1",
                                          sysfs_root=fake_block),
          "0000:03:00.0")
    check("un disque absent de l arbre rend None",
          hardware.pci_address_for_device("/dev/nvme8n1",
                                          sysfs_root=fake_block),
          None)

# --- la selection du NVMe, de facon DETERMINISTE ------------------------- #
# Sur du texte lspci capture, pas sur le materiel de cette machine : c est
# ce qui rend ce test vrai partout. LSPCI est deja defini plus haut dans ce
# fichier (repris de test_windows_guest_hardware.py).
try:
    hardware.select_passthrough_nvme(hardware.parse_nvme_controllers(LSPCI),
                                     {"0000:02:00.0"})
    picked = True
except hardware.HardwareError:
    picked = False
check("un NVMe non possede par l hote est selectionnable", picked, True)

try:
    # Les deux controleurs appartiennent a l hote : plus aucun candidat.
    hardware.select_passthrough_nvme(hardware.parse_nvme_controllers(LSPCI),
                                     {"0000:02:00.0", "0000:03:00.0"})
    failures.append("aucun candidat libre aurait du lever HardwareError")
except hardware.HardwareError as exc:
    check("le refus nomme la raison", bool(str(exc).strip()), True)

try:
    hardware.select_passthrough_nvme([], set())
    failures.append("aucun controleur NVMe aurait du lever HardwareError")
except hardware.HardwareError:
    pass

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - hardware detection checks passed")
