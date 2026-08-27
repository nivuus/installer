#!/usr/bin/env python3
"""Tests for the console package: manifest, questions, and the resolve hook.

resolve is READ-ONLY and runs before partition(), so what it returns is the
kernel command line the engine writes before the target disk is touched.
It is also where "PCI passthrough only" is enforced: a machine with no
properly isolated NVMe must be refused with a sentence, not silently
downgraded to a disk image.

Run: python3 scripts/tests/test_console_resolve.py
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
CONSOLE = REPO / "console"

from packages.manifest import load_manifest  # noqa: E402
from packages.runner import run_resolve  # noqa: E402
from packages.wizard import load_questions, validate_answers  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- le manifeste est valide au sens du contrat -------------------------- #
m = load_manifest(str(CONSOLE / "nivuus-package.yaml"))
check("nom", m.name, "console")
check("tier platform", m.tier, "platform")
check("reclame le gpu en exclusif", ("gpu", "exclusive") in m.claims, True)
check("reclame le nvme en exclusif", ("nvme", "exclusive") in m.claims, True)
check("exige un iommu", "iommu" in m.capabilities, True)
check("exige un nvme libre", "nvme-dedicated" in m.capabilities, True)
check("declare les trois hooks",
      sorted(p for p, _ in m.hooks), ["activate", "install", "resolve"])
check("iommu statique dans la cmdline",
      "intel_iommu=on" in m.platform.kernel_cmdline, True)

# --- les questions sont valides au sens du vocabulaire ------------------- #
qs = load_questions(str(CONSOLE / m.questions_file))
check("trois questions", len(qs), 3)
check("le disque est un selecteur materiel",
      [q.type for q in qs if q.key == "dedicated_nvme"], ["disque"])
check("le mot de passe est un secret",
      [q.type for q in qs if q.key == "admin_password"], ["secret"])
check("le secret n expose pas de defaut",
      "default" in [q for q in qs if q.key == "admin_password"][0].to_dict(),
      False)
answers = validate_answers(qs, {"dedicated_nvme": "/dev/nvme1n1",
                                "admin_password": "hunter2hunter2"})
check("retro prend son defaut", answers["retro"], False)

# --- resolve refuse une machine SANS GPU dedie --------------------------- #
# Deterministe : ce chemin ne depend que du snapshot injecte, jamais du
# materiel de la machine qui execute le test.
HW_SANS_GPU = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [{"slot": "00:02.0", "vendor": "intel", "discrete": False}],
    "cpu": {"hybrid": True, "performance_cpus": [0, 1, 2, 3], "total_cpus": 8},
    "disks": [], "memory_mib": 65536,
}
refus = run_resolve(m, HW_SANS_GPU, {"dedicated_nvme": "/dev/nvme1n1",
                                     "admin_password": "x", "retro": False})
check("sans GPU dedie, resolve refuse", refus.ok, False)
check("et il dit pourquoi", "GPU" in refus.reason, True)

# Une topologie CPU malformee doit devenir un refus, pas une trace
# d exception : isolation_plan leve HardwareError, et ce que resolve rend
# atterrit sur la ligne de commande NOYAU.
HW_CPU_CASSE = {
    **HW_SANS_GPU,
    "gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True}],
    "cpu": {"hybrid": True, "performance_cpus": [-1, 0], "total_cpus": 8},
}
refus_cpu = run_resolve(m, HW_CPU_CASSE, {"dedicated_nvme": "",
                                          "admin_password": "x", "retro": False})
check("une topologie CPU malformee est refusee", refus_cpu.ok, False)
check("et le refus la nomme",
      "CPU" in refus_cpu.reason or "cpu" in refus_cpu.reason, True)

# --- resolve sur un materiel plausible ----------------------------------- #
# ATTENTION : au-dela du GPU, resolve appelle hardware.passthrough_nvme(),
# qui lit le VRAI lspci de la machine qui execute ce test. Son issue depend
# donc du materiel present, et asserter « accepte » ici produirait un test
# qui passe sur une machine et ment sur une autre.
#
# On asserte donc ce qui est vrai des DEUX cotes : soit un plan bien forme,
# soit un refus motive - jamais un plantage, jamais un refus muet. La
# selection du NVMe elle-meme est testee de facon deterministe dans
# test_console_hardware.py, sur du texte lspci capture, via la fonction pure
# resolve_passthrough_nvme().
HW_OK = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True}],
    "cpu": {"hybrid": True, "performance_cpus": [0, 1, 2, 3], "total_cpus": 8},
    "disks": [{"name": "nvme1n1", "path": "/dev/nvme1n1", "transport": "nvme"}],
    "memory_mib": 65536,
}
res = run_resolve(m, HW_OK, {"dedicated_nvme": "", "admin_password": "x",
                             "retro": False})
if res.ok:
    check("la cmdline statique survit",
          "intel_iommu=on" in res.platform.kernel_cmdline, True)
    check("les ids vfio sont emis",
          any(p.startswith("vfio-pci.ids=") for p in res.platform.kernel_cmdline),
          True)
    check("nohz_full est calcule",
          any(p.startswith("nohz_full=") for p in res.platform.kernel_cmdline),
          True)
    check("isolcpus n est JAMAIS emis",
          any("isolcpus" in p for p in res.platform.kernel_cmdline), False)
    check("des hugepages sont demandes", res.platform.hugepages_mib > 0, True)
    print("  (note: cette machine convient, la branche 'accepte' a ete exercee)")
else:
    check("un refus porte toujours sa raison", bool(res.reason.strip()), True)
    check("et la raison parle du NVMe",
          "NVMe" in res.reason or "nvme" in res.reason, True)
    print(f"  (note: cette machine ne convient pas, refus exerce : {res.reason[:60]})")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console resolve tests passed")
