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
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
CONSOLE = REPO / "console"
RESOLVE_HOOK = CONSOLE / "hooks" / "resolve.py"

from packages.manifest import load_manifest  # noqa: E402
from packages.runner import run_resolve  # noqa: E402
from packages.wizard import load_questions, validate_answers  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def call_hook(hw=None, answers=None):
    """Invoke console/hooks/resolve.py exactly as the engine does: a
    subprocess fed the {"hw":..., "answers":...} context on stdin, jsonl
    events on stdout. This is the hook's REAL interface - going through
    run_resolve() alone would only ever exercise the happy subset of
    contexts run_resolve() itself knows how to build; a malformed hw dict
    reaching the hook as-is is exactly what a broken detection layer would
    produce.

    Returns (exit_code, events). Never raises on a hook crash: a traceback
    on stderr with a non-zero exit and zero 'refuse' events is precisely the
    defect this is here to catch, so it must be observable as data, not as
    a pytest-style exception out of this helper.
    """
    ctx = {}
    if hw is not None:
        ctx["hw"] = hw
    if answers is not None:
        ctx["answers"] = answers
    proc = subprocess.run(
        [sys.executable, str(RESOLVE_HOOK), "--phase", "resolve"],
        input=json.dumps(ctx), capture_output=True, text=True, timeout=30)
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return proc.returncode, events


def refusal_reason(events):
    for event in events:
        if event.get("event") == "refuse":
            return event.get("reason") or ""
    return None


def platform_event(events):
    for event in events:
        if event.get("event") == "platform":
            return event
    return None


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

# --- resolve refuse plutot que crasher sur un snapshot casse ------------- #
# Ces cas visent le hook directement, en subprocess, via son interface
# reelle (le JSON sur stdin) - pas run_resolve(), qui ne construit que des
# contextes bien formes. Un hook qui plante ici sortirait non-nul avec une
# trace, sans le moindre evenement 'refuse' : c'est exactement le defaut
# constate en revue, donc chaque cas verifie a la fois le code de sortie ET
# la presence d'un refus motive.

GPU_OK = {"slot": "01:00.0", "vendor": "nvidia", "discrete": True}
CPU_OK = {"hybrid": True, "performance_cpus": [0, 1, 2, 3], "total_cpus": 8}

# Un GPU discret sans cle 'slot' : ligne ~60 du hook faisait
# discrete[0]["slot"], KeyError direct.
rc, events = call_hook(
    hw={"gpus": [{"vendor": "nvidia", "discrete": True}], "memory_mib": 65536},
    answers={})
check("GPU sans slot : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("GPU sans slot : un refus est emis", reason is not None, True)
check("GPU sans slot : le refus parle du GPU",
      bool(reason) and "GPU" in reason, True)

# memory_mib non numerique : guest_memory_mib faisait total // 2 sur une str.
rc, events = call_hook(hw={"gpus": [GPU_OK], "memory_mib": "lots"}, answers={})
check("memoire non numerique : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("memoire non numerique : un refus est emis", reason is not None, True)

# memory_mib negatif : refuse (choix documente dans le rapport - un chiffre
# negatif est traite comme un snapshot malforme, pas comme "hote minuscule").
rc, events = call_hook(hw={"gpus": [GPU_OK], "memory_mib": -100}, answers={})
check("memoire negative : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("memoire negative : un refus est emis", reason is not None, True)

# --- budget invite : GUEST_MIB_DEFAULT (16384), jamais "moitie de l hote" #
# Round 2 de revue : "moitie de l hote" rejouait EXACTEMENT l erreur deja
# corrigee dans ce projet (CLAUDE.md, "Hugepages pool halved" - un pool de
# 16584 pages, double du besoin reel, faisait swapper l hote ; reduit a 8448
# pages, ~16896 MiB mesures). Le budget par defaut est donc fixe a 16384 MiB
# (16 GiB), et seulement reduit si l hote ne peut pas le fournir - jamais
# calcule comme une fraction de la RAM totale. Table mesuree :
#
#   hote   8 GiB (8192 MiB)  -> refuse (le plancher meme ne tient pas)
#   hote  16 GiB (16384 MiB) -> invite  8192 MiB (frontiere, moitie = plancher)
#   hote  24 GiB (24576 MiB) -> invite 12288 MiB (moitie, sous le defaut)
#   hote  32 GiB (32768 MiB) -> invite 16384 MiB (moitie = defaut, pile)
#   hote  64 GiB (65536 MiB) -> invite 16384 MiB (defaut, PAS la moitie - 32768)

# 8 GiB hote : la moitie (4096) est sous le plancher (8192) - refuse, et la
# raison doit nommer la RAM reelle de l hote pour que l operateur comprenne
# pourquoi (pas juste "trop petit").
rc, events = call_hook(hw={"gpus": [GPU_OK], "memory_mib": 8192}, answers={})
check("hote 8 GiB : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("hote 8 GiB : un refus est emis", reason is not None, True)
check("hote 8 GiB : le refus nomme la RAM",
      bool(reason) and "8192" in reason, True)

# 16383 MiB, juste sous la frontiere des 16 GiB : doit refuser, et nommer a
# la fois ce que l hote a et ce qu il faudrait - c est le cas qu un futur
# lecteur pourrait croire identique a 16384 s il ne regarde pas de pres.
rc, events = call_hook(hw={"gpus": [GPU_OK], "memory_mib": 16383}, answers={})
check("hote 16383 MiB (juste sous la frontiere) : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("hote 16383 MiB : un refus est emis", reason is not None, True)
check("hote 16383 MiB : le refus nomme la RAM de l hote",
      bool(reason) and "16383" in reason, True)
check("hote 16383 MiB : le refus nomme le besoin",
      bool(reason) and "16384" in reason, True)

# 16 GiB hote : la moitie (8192) egale exactement le plancher - la frontiere
# ne doit PAS refuser une machine qui fonctionne reellement.
rc, events = call_hook(
    hw={"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 16384},
    answers={"dedicated_nvme": ""})
check("hote 16 GiB : code de sortie 0", rc, 0)
plat = platform_event(events)
if plat is None:
    check("hote 16 GiB : un plan platform est emis (pas de refus)",
          refusal_reason(events), None)
else:
    check("hote 16 GiB : l invite recoit exactement le plancher",
          plat.get("hugepages-mib"), 8192)

# 24 GiB hote : la moitie (12288) est sous le defaut (16384) - l invite
# recoit la moitie, pas le defaut plein.
rc, events = call_hook(
    hw={"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 24576},
    answers={"dedicated_nvme": ""})
check("hote 24 GiB : code de sortie 0", rc, 0)
plat = platform_event(events)
if plat is None:
    check("hote 24 GiB : un plan platform est emis (pas de refus)",
          refusal_reason(events), None)
else:
    check("hote 24 GiB : l invite recoit la moitie (12288 MiB)",
          plat.get("hugepages-mib"), 12288)

# 32 GiB hote : la moitie (16384) egale exactement le defaut.
rc, events = call_hook(
    hw={"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 32768},
    answers={"dedicated_nvme": ""})
check("hote 32 GiB : code de sortie 0", rc, 0)
plat = platform_event(events)
if plat is None:
    check("hote 32 GiB : un plan platform est emis (pas de refus)",
          refusal_reason(events), None)
else:
    check("hote 32 GiB : l invite recoit le defaut (16384 MiB)",
          plat.get("hugepages-mib"), 16384)

# 64 GiB hote (cette machine) : le point que l on veut EPINGLER. La moitie
# de l hote serait 32768 MiB - c est exactement l erreur "hugepages pool
# halved" documentee dans CLAUDE.md. L invite doit recevoir le DEFAUT fixe
# (16384 MiB), jamais la moitie de l hote : c est le cas qu un futur lecteur
# sera tente de "corriger" en le remettant a host_mib // 2, donc gardez
# cette assertion telle quelle si le budget par defaut change un jour pour
# une autre raison mesuree.
rc, events = call_hook(
    hw={"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 65536},
    answers={"dedicated_nvme": ""})
check("hote 64 GiB : code de sortie 0", rc, 0)
plat = platform_event(events)
if plat is None:
    check("hote 64 GiB : un plan platform est emis (pas de refus)",
          refusal_reason(events), None)
else:
    check("hote 64 GiB : l invite recoit le defaut mesure (16384 MiB), "
          "PAS la moitie de l hote (32768 MiB)",
          plat.get("hugepages-mib"), 16384)

# --- non-regression : les refus deja verifies plus haut restent verts ---- #
rc, events = call_hook(
    hw={"gpus": [{"slot": "00:02.0", "vendor": "intel", "discrete": False}],
        "memory_mib": 65536},
    answers={})
check("regression sans GPU dedie : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("regression sans GPU dedie : le refus parle du GPU",
      bool(reason) and "GPU" in reason, True)

rc, events = call_hook(
    hw={"gpus": [GPU_OK],
        "cpu": {"hybrid": True, "performance_cpus": [-1, 0], "total_cpus": 8},
        "memory_mib": 65536},
    answers={"dedicated_nvme": ""})
check("regression CPU casse : code de sortie 0", rc, 0)
reason = refusal_reason(events)
check("regression CPU casse : le refus nomme le CPU",
      bool(reason) and ("CPU" in reason or "cpu" in reason), True)

# --- probes supplementaires, non assertees une a une : voir le rapport --- #
# hw entierement absent ; hw.gpus present mais vide ; answers absent ;
# dedicated_nvme vers un peripherique inexistant. Chacun doit refuser (ou,
# pour un hw minimal, refuser faute de GPU) - jamais planter.
for label, ctx_hw, ctx_answers in [
    ("hw absent", None, {}),
    ("gpus vide", {"gpus": [], "memory_mib": 65536}, {}),
    ("answers absent", {"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 65536},
     None),
    ("nvme demande inexistant",
     {"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 65536},
     {"dedicated_nvme": "/dev/does-not-exist-at-all"}),
]:
    rc, events = call_hook(hw=ctx_hw, answers=ctx_answers)
    check(f"probe '{label}' : code de sortie 0", rc, 0)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console resolve tests passed")
