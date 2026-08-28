#!/usr/bin/env python3
"""Tests for the console package: manifest, questions, and the resolve hook.

resolve is READ-ONLY and runs before partition(), so what it returns is the
kernel command line the engine writes before the target disk is touched.
It is also where "PCI passthrough only" is enforced: a machine with no
properly isolated NVMe must be refused with a sentence, not silently
downgraded to a disk image.

Run: python3 console/tests/test_console_resolve.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

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


def call_hook(hw=None, answers=None, env=None):
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
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(RESOLVE_HOOK), "--phase", "resolve"],
        input=json.dumps(ctx), capture_output=True, text=True, timeout=30,
        env=child_env)
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
check("sept questions", len(qs), 7)
check("le disque est un selecteur materiel",
      [q.type for q in qs if q.key == "dedicated_nvme"], ["disque"])
check("le mot de passe est un secret",
      [q.type for q in qs if q.key == "admin_password"], ["secret"])
check("le secret n expose pas de defaut",
      "default" in [q for q in qs if q.key == "admin_password"][0].to_dict(),
      False)
answers = validate_answers(qs, {"dedicated_nvme": "/dev/nvme1n1",
                                "admin_password": "hunter2hunter2",
                                "windows_iso": "/media/data/win-ltsc.iso",
                                "ltsc_key": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX",
                                "apollo_password": "another-secret"})
check("retro prend son defaut", answers["retro"], False)

# Four answers now reach the guest build. The two secrets must be typed as
# such: a `texte` would come back in the portal's payload with its default,
# and land in a log. The engine's own vocabulary check is the other half.
by_key = {q.key: q.to_dict() for q in qs}
check("le media Windows est demande", by_key["windows_iso"]["type"], "texte")
check("le media est requis", by_key["windows_iso"].get("required"), True)
check("la cle produit est un secret", by_key["ltsc_key"]["type"], "secret")
check("la cle produit est requise", by_key["ltsc_key"].get("required"), True)
check("le mot de passe Apollo est un secret",
      by_key["apollo_password"]["type"], "secret")
check("le mot de passe Apollo est requis",
      by_key["apollo_password"].get("required"), True)
check("le repertoire de travail est facultatif",
      by_key["guest_workdir"].get("required", False), False)

# No default on the medium: the only automatic download available serves an
# Evaluation edition whose MAK conversion is unmeasured, and a console that
# installs then expires is worse than one that refuses with a reason.
check("le media n a pas de valeur par defaut",
      by_key["windows_iso"].get("default"), None)

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

# --- LE CHEMIN ISO LIVE, de bout en bout et de facon DETERMINISTE -------- #
# C est le chemin PRINCIPAL du package - on l installe depuis l ISO - et
# c est celui qui etait casse : le selecteur derivait le NVMe a passer en
# EXCLUANT ceux qui portent la racine hote, or sur un support live la racine
# est l image live, aucun disque PCI ne la porte, et findmnt rend « overlay ».
# host_root_pci_addresses() rendait donc None sur TOUTES les machines et le
# selecteur refusait partout. Mesure avant correctif :
#
#   hote deja installe, racine sur A  -> choisit B
#   ISO live, racine = overlay        -> REFUS : « host root not identified »
#
# Les verifications materielles precedentes passaient parce qu elles
# s executaient sur un hote installe : le mauvais chemin etait exerce.
#
# Le scenario est monte de toutes pieces - un faux `lspci` et un faux
# `findmnt` sur le PATH, un faux arbre sysfs via NIVUUS_SYSFS_BLOCK - donc il
# est vrai sur n importe quelle machine, y compris une sans NVMe du tout.
LSPCI_LIVE = """\
0000:00:02.0 VGA compatible controller [0300]: Intel Corporation AlderLake-S GT1 [8086:4680] (rev 0c)
0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786] (rev a1)
0000:01:00.1 Audio device [0403]: NVIDIA Corporation AD104 High Definition Audio Controller [10de:22bc] (rev a1)
0000:02:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller 980 [144d:a809]
0000:03:00.0 Non-Volatile memory controller [0108]: Samsung Electronics Co Ltd NVMe SSD Controller SM981 [144d:a808]
"""


def live_iso_env(root, disk_to_address):
    """A fake live medium: no traceable host root, a captured lspci, a fake
    sysfs block tree mapping device names to PCI addresses.

    Returns the env overlay to hand call_hook().
    """
    binaries = os.path.join(root, "bin")
    os.makedirs(binaries, exist_ok=True)
    # A live ISO's root really is reported as `overlay` - a name that is not
    # a block device, so nothing PCI can be derived from it.
    with open(os.path.join(binaries, "findmnt"), "w") as fh:
        fh.write("#!/bin/sh\necho overlay\n")
    with open(os.path.join(binaries, "lspci"), "w") as fh:
        fh.write("#!/bin/sh\ncat <<'EOF'\n" + LSPCI_LIVE + "EOF\n")
    for name in ("findmnt", "lspci"):
        os.chmod(os.path.join(binaries, name), 0o755)

    block = os.path.join(root, "sysfs-block")
    pci = os.path.join(root, "pci")
    for device, address in disk_to_address.items():
        os.makedirs(os.path.join(block, device, "device"), exist_ok=True)
        os.makedirs(os.path.join(pci, address), exist_ok=True)
        os.symlink(os.path.join(pci, address),
                   os.path.join(block, device, "device", "device"))

    return {"PATH": binaries + os.pathsep + os.environ.get("PATH", ""),
            "NIVUUS_SYSFS_BLOCK": block}


HW_LIVE = {"gpus": [GPU_OK], "cpu": CPU_OK, "memory_mib": 65536}

with tempfile.TemporaryDirectory() as tmp:
    env = live_iso_env(tmp, {"nvme9n1": "0000:03:00.0",
                             "nvme8n1": "0000:00:02.0"})

    # Le cas qui refusait sur toutes les machines et doit maintenant reussir.
    rc, events = call_hook(hw=HW_LIVE, answers={"dedicated_nvme": "/dev/nvme9n1"},
                           env=env)
    check("ISO live : code de sortie 0", rc, 0)
    check("ISO live : AUCUN refus", refusal_reason(events), None)
    plat = platform_event(events)
    check("ISO live : un plan platform est emis", plat is not None, True)
    if plat:
        check("ISO live : le NVMe choisi est celui repondu par l operateur",
              "vfio-pci.ids=10de:2786,10de:22bc,144d:a808"
              in plat.get("kernel-cmdline", []), True)

    # Une reponse qui ne designe aucun controleur NVMe : refus motive, pas un
    # choix de repli silencieux sur un autre disque.
    rc, events = call_hook(hw=HW_LIVE, answers={"dedicated_nvme": "/dev/nvme8n1"},
                           env=env)
    check("ISO live, reponse non-NVMe : code de sortie 0", rc, 0)
    reason = refusal_reason(events)
    check("ISO live, reponse non-NVMe : un refus est emis",
          reason is not None, True)
    check("ISO live, reponse non-NVMe : le refus nomme l adresse trouvee",
          bool(reason) and "0000:00:02.0" in reason, True)
    check("ISO live, reponse non-NVMe : le refus nomme ce qui etait attendu",
          bool(reason) and "0000:03:00.0" in reason, True)

    # Une reponse qui ne se resout vers aucune adresse PCI du tout.
    rc, events = call_hook(hw=HW_LIVE,
                           answers={"dedicated_nvme": "/dev/nexistepas0n1"},
                           env=env)
    check("ISO live, disque inconnu : code de sortie 0", rc, 0)
    reason = refusal_reason(events)
    check("ISO live, disque inconnu : un refus est emis", reason is not None, True)
    check("ISO live, disque inconnu : le refus nomme le disque demande",
          bool(reason) and "/dev/nexistepas0n1" in reason, True)

    # Sans reponse, sur un live ou la racine n est pas tracable, il ne reste
    # rien pour decider : le refus est le bon comportement, et il le dit.
    rc, events = call_hook(hw=HW_LIVE, answers={"dedicated_nvme": ""}, env=env)
    check("ISO live sans reponse : code de sortie 0", rc, 0)
    reason = refusal_reason(events)
    check("ISO live sans reponse : un refus est emis", reason is not None, True)
    check("ISO live sans reponse : le refus dit qu il manque le disque dedie",
          bool(reason) and "dedicated NVMe" in reason, True)

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
