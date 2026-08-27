# `console` devient un package — côté hôte (phase 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire de la console de jeu un **package ordinaire** du moteur livré en phase 1, et dissoudre `install.sh` — sans toucher à git ni au répertoire `windows-guest/`.

**Architecture:** Un répertoire `console/` à la racine du dépôt devient un package `nivuus.dev/v1` autonome : manifeste, questions, et trois hooks. Sa phase `resolve` calcule `vfio-pci.ids`, `nohz_full` et les hugepages depuis le matériel ; sa phase `install` déploie les hooks libvirt et les scripts hôte. Le moteur ne connaît plus « la VM » : il connaît un package qui réclame le GPU et un NVMe.

**Tech Stack:** Python 3.11+, PyYAML, systemd, libvirt, VFIO.

**Spec:** [`docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`](../specs/2026-08-27-decoupage-installer-console-design.md) — phase 2, moitié hôte.

## Global Constraints

- **Le package doit être un répertoire AUTONOME.** `apply_packages` fait un `copytree` de `manifest.root` vers `{target}/opt/nivuus-packages/{name}/` ; tout ce dont un hook a besoin à l'exécution doit être **dans** ce répertoire. Un hook qui importe depuis `installer/` casserait au premier boot. C'est aussi ce qui rendra la phase 3 (`git filter-repo --path console`) mécanique.
- **Contrat `nivuus.dev/v1`** : `apiVersion`, `tier` (`userspace`|`platform`), `requires.capabilities`, `claims`, `platform.{kernel-cmdline,modules,hugepages-mib}`, `apt`, `wizard.questions`, `hooks.{resolve,install,activate}`. Le manifeste normatif est `installer/packages/manifest.py` ; `installer/README.md` le documente.
- **Un hook parle jsonl sur stdout**, reçoit son contexte JSON sur **stdin**, et `--phase` (plus `--root` en phase install) en argv. Événements : `progress`, `platform`, `refuse`, `done` (advisory). Sortie non nulle = échec.
- **`resolve` est en lecture seule** — convention, pas bac à sable. Il tourne **avant `partition`**, donc un hook qui écrit touche le système de fichiers **vivant** de l'installateur.
- **`kernel-cmdline` passe une liste blanche** `^[A-Za-z0-9_.,:=/@+-]+$` (validée dans `plan_packages`, avant tout partitionnement) : `/etc/default/grub` est sourcé comme du shell.
- **`hugepages-mib` doit être un entier ≥ 0**, `bool` refusé. `kernel-cmdline` et `modules` doivent être de vraies **listes** de chaînes — une chaîne s'égrainerait en caractères.
- **PyYAML implémente YAML 1.1** : une clé nue `on`/`off`/`yes`/`no` est un **booléen**. Les manifestes et les fichiers de questions refusent ces clés ; citer entre guillemets.
- **Passthrough PCI uniquement** pour le disque dédié — refus motivé en phase `resolve` si aucun NVMe correctement isolé, jamais un repli silencieux.
- **Tests : scripts autonomes, pas pytest** (`python3 scripts/tests/test_x.py`, liste `failures`, `check(label, got, want)`, `sys.exit(1)`, `OK - …`). Ni `pytest.ini` ni `conftest.py` dans ce dépôt.
- **La CI ne lance aucun test Python** (`ci.yml`, `test-paths: ""`). `cd installer && make test-packages PYTHON=<python>` est le seul agrégateur ; ajoutez-y tout nouveau fichier de test.
- **`shellcheck` passe sur tout le dépôt.** `SC2012` sur `build.sh:104` est pré-existant sur une ligne intouchée.
- **`pydantic`/`fastapi` ne sont pas installés** : `make test-packages PYTHON=/chemin/venv/bin/python3` pour les trois suites qui en dépendent.
- Commentaires de code en **anglais** ; messages de commit **sans accents**.
- **Un module, une responsabilité.** La règle « ≤ 200 lignes » citée par `CLAUDE.md` vient de `.github/copilot-instructions.md`, **absent de ce dépôt**, et 18 fichiers suivis la dépassent — elle ne contraint pas.

---

## Carte des fichiers

### Ce que le dépôt gagne

| Chemin | Responsabilité |
| --- | --- |
| `console/nivuus-package.yaml` | Le manifeste : tier `platform`, réclame `gpu` et `nvme`, exige `iommu`/`gpu-discrete`/`nvme-dedicated` |
| `console/wizard.yaml` | Trois questions : disque dédié, retrogaming, mot de passe administrateur |
| `console/hardware.py` | La moitié précise extraite de `installer/common/hardware.py` : fonctions PCI, sélection du NVMe de passthrough, dérivation `isolcpus`/`nohz_full` |
| `console/hooks/resolve.py` | Lecture seule : rend `vfio-pci.ids`, `nohz_full`, `hugepages-mib` — ou **refuse** avec sa raison |
| `console/hooks/install.py` | Déploie les hooks libvirt, les scripts hôte, les unités de réveil, le témoin retro |
| `console/hooks/activate.py` | **Squelette en 2a** : journalise et sort 0. La construction de l'invité arrive en 2b |
| `console/host/` | Les scripts et configs hôte déplacés (voir tâche 3) |
| `console/README.md` | Ce qu'est ce package, et ce qui n'y est pas encore |

### Ce que le dépôt perd

| Chemin | Sort |
| --- | --- |
| `install.sh` | **Supprimé.** Cinq de ses sept blocs sont de la mise en place VM et partent dans le package ; la thermique devient une feature de 15 lignes |
| `installer/install-engine/steps/features.py::_kvm_vfio_thermal` | Scindé : `_thermal` reste, le reste part |
| `installer/install-engine/steps/features.py::_retro` | Part dans le hook install du package |
| `scripts/vm-cpu-partition.sh`, `vm-wake-gate.py`, `handle-vm-start.sh`, `winvm`, `install-winrm-cli.sh`, `gpu-rebind-debug/` | Déplacés vers `console/host/` |
| `configs/libvirt/`, `configs/setup-winrm.ps1` | Déplacés vers `console/host/` |
| `KNOWN_FEATURES` : `kvm-vfio`, `gpu-passthrough`, `retro` | Retirés — ce sont le package et ses questions |

### Ce qui NE bouge pas en 2a

`installer/windows-guest/`, `installer/common/retro.py`, `scripts/optimize-cpu-thermal.sh`, `scripts/nivuus-cpu-latency`, `configs/systemd/nivuus-cpu-*`, les 13 suites `test_windows_guest_*`. Tout cela est le périmètre de **2b**.

---

### Task 1: couper `hardware.py` en deux

Le moteur doit répondre à `requires.capabilities` **avant** d'exécuter le moindre hook : il garde donc une détection grossière. Le précis part au package. La ligne est vérifiée, pas supposée : dans tout le moteur, seuls `capabilities.py:30` (`gpu.get("discrete")`) et `capabilities.py:41` (`hw["cpu"]["hybrid"]`) lisent ces champs. `ids`, `isolcpus` et `performance_cpus` ne sont consommés que par `_kvm_vfio_thermal`, qui disparaît en tâche 5.

**Files:**
- Create: `console/hardware.py`
- Modify: `installer/common/hardware.py` (retirer la moitié précise, alléger `list_gpus` et `cpu_topology`)
- Create: `scripts/tests/test_console_hardware.py`
- Modify: `scripts/tests/test_windows_guest_hardware.py` (repointer les imports)

**Interfaces:**
- Consumes: rien
- Produces:
  - `installer/common/hardware.py` garde : `list_disks`, `list_ethernet`, `list_wifi`, `first_ap_interface`, `first_wired_with_carrier`, `iommu_support`, `list_gpus` (**sans le champ `ids`**), `cpu_topology` (**sans `isolcpus` ni `nohz_full`**), `memory_total_mib(meminfo_path="/proc/meminfo") -> int` (RAM hôte en MiB depuis `MemTotal`, fail-open à `0`), `detect_all` — dont le dict rendu porte désormais une clé **`memory_mib`** (ajoutée en fix round 2 de la tâche 2 : `detect_all()` ne la portait pas, donc `resolve` tombait systématiquement sur son repli plancher)
  - `console/hardware.py` expose : `HardwareError`, `pci_slot_ids(slot) -> list[str]`, `parse_pci_functions(raw, slot) -> list[dict]`, `pci_slot_functions(slot) -> list[dict]`, `parse_nvme_controllers(raw) -> list[dict]`, `select_passthrough_nvme(controllers, host_addresses) -> dict`, `host_root_pci_addresses() -> set[str] | None`, `resolve_passthrough_nvme(raw, host_addresses) -> dict`, `passthrough_nvme() -> dict`, `_whole_disk_name(name, sysfs_root=...) -> str`, `pci_address_for_device(path) -> str | None`, `vfio_ids_for_slot(slot) -> list[str]`, `cpu_ranges(nums) -> str`, `isolation_plan(cpu: dict) -> dict` → `{"isolcpus": str, "nohz_full": str}`

⚠️ **Deux faits sur ces fonctions que le code appelant DOIT respecter, vérifiés dans la source :**
  - `passthrough_nvme()` et `select_passthrough_nvme()` **lèvent `HardwareError`** sur tous leurs chemins d'échec (aucun contrôleur NVMe, candidat ambigu, disque appartenant à l'hôte). Elles ne rendent **jamais** un dict vide. Un appelant qui teste `if not nvme:` ne détectera jamais rien — l'exception sera déjà partie.
  - Le dict rendu est une **fonction PCI**, avec les clés `{address, id, function, bus, slot, domain}`. Il n'y a **pas** de clé `device` : comparer un chemin `/dev/…` à ce dict exige de passer par `pci_address_for_device()`.

- [ ] **Step 1: Constater la frontière avant de couper**

```bash
grep -rn 'get("discrete")\|\["hybrid"\]\|"ids"\|isolcpus\|performance_cpus' \
  installer/packages/*.py installer/install-engine/steps/*.py installer/webapp/*.py
```

Attendu : `capabilities.py` ne lit que `discrete` et `hybrid` ; toutes les autres occurrences sont dans `features.py::_kvm_vfio_thermal` ou `models.py::CpuConfig`. **Si ce n'est pas le cas, arrêtez et signalez-le** — la découpe reposerait sur une hypothèse fausse.

- [ ] **Step 2: Écrire le test du nouveau module, qui échoue**

Créer `scripts/tests/test_console_hardware.py`. Reprendre **verbatim** les cas de `scripts/tests/test_windows_guest_hardware.py` qui portent sur `parse_pci_functions`, `_whole_disk_name`, `parse_nvme_controllers`, `select_passthrough_nvme` et `resolve_passthrough_nvme` (ils sont déjà écrits et déjà verts — ne les réinventez pas), en changeant l'en-tête d'import pour :

```python
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console"))

import hardware  # noqa: E402
```

Ajouter les cas qui n'existaient pas, pour les deux fonctions nouvelles :

```python
# --- isolation_plan: la derivation que le moteur ne fait plus ------------- #
# Regression guard: les deux chemins heureux ci-dessous doivent rester verts.
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

# ABSENT (rien a isoler) et MALFORME (snapshot qui ment) sont distingues,
# comme passthrough_nvme() ailleurs dans ce module refuse de deviner plutot
# que de rendre un dict vide. isolation_plan() finit sur la ligne de
# commande noyau (nohz_full=...) et la liste blanche GRUB en aval ne garde
# que l injection shell, pas la validite semantique - filtrer les entrees
# fautives laisserait donc passer une plage plausible tiree d un snapshot
# dont on vient de prouver qu il est faux. Refuser est le seul comportement
# sur qui compter.
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
```

⚠️ Les adresses PCI ci-dessus (`0000:02:00.0`, `0000:03:00.0`) doivent correspondre à celles du bloc `LSPCI` que vous reprenez de `test_windows_guest_hardware.py`. **Lisez-le et adaptez-les** — ce test existant contient déjà `check("host controller listed", ctrls[0]["address"], "0000:02:00.0")`, donc au moins la première est bonne. Si la seconde diffère, corrigez-la plutôt que de deviner.

- [ ] **Step 3: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_console_hardware.py
```

Attendu : `ModuleNotFoundError: No module named 'hardware'`

- [ ] **Step 4: Créer `console/hardware.py`**

Déplacer depuis `installer/common/hardware.py` (les numéros de ligne sont indicatifs, repérez par nom) : `_pci_slot_ids` (renommé **`pci_slot_ids`**, il devient public), `parse_pci_functions`, `_clean_lspci_desc_from_nn`, `pci_slot_functions`, `parse_nvme_controllers`, `_whole_disk_name`, `_device_to_pci_address`, `select_passthrough_nvme`, `host_root_pci_addresses`, `resolve_passthrough_nvme`, `passthrough_nvme`, et `_ranges` (renommé **`cpu_ranges`**).

Le module a besoin de `_run` et `_read_int`. **Copiez-les**, ne les importez pas : ce fichier doit rester autonome (contrainte globale). En-tête du module :

```python
"""Precise hardware detection for the console package.

The ENGINE detects capabilities - coarse, generic, enough to decide whether
this package may be offered at all: is there an IOMMU, a discrete GPU, a
spare NVMe. It cannot delegate that, because it must answer
`requires.capabilities` before running any of this package's code.

What lives here is the precise half: which PCI functions share the GPU's
slot, which NVMe controller is free of the host's own root filesystem, which
CPUs to hand to the guest. The package refines what the engine handed it; it
does not redo it.

`_run` and `_read_int` are copied from installer/common/hardware.py rather
than imported. That duplication is deliberate: this package must run on a
Debian that has never seen the Nivuus engine, so it may not import from it.
"""
```

Copier aussi **`HardwareError`** (les fonctions déplacées la lèvent) et ajouter les fonctions nouvelles :

```python
def pci_address_for_device(path: str) -> str | None:
    """PCI address of the controller behind a /dev/... block device, or None.

    The wizard hands back a device PATH; passthrough needs a PCI ADDRESS, and
    nothing in the returned controller dict bridges the two. A partition is
    resolved to its whole disk first, because /sys/block only knows the latter.
    """
    name = _whole_disk_name(os.path.basename(path.rstrip("/")))
    return _device_to_pci_address(name)


def vfio_ids_for_slot(slot: str) -> list[str]:
    """Every vendor:device id sharing `slot`, for vfio-pci.ids.

    Passthrough binds the WHOLE slot: a GPU and its HDMI-audio function are
    separate PCI functions in the same IOMMU group, and leaving one on the
    host driver makes the group unassignable.
    """
    return pci_slot_ids(slot)


def _validate_performance_cpus(performance: list, total: int) -> None:
    """Reject a `performance_cpus` list this module cannot trust.

    isolation_plan's output is emitted onto the kernel command line
    (nohz_full=...). The GRUB allowlist downstream
    (`^[A-Za-z0-9_.,:=/@+-]+$`) exists to stop shell injection, not to judge
    semantic validity - a range like "-1-1" passes it untouched. So this is
    the only place that can catch a malformed CPU snapshot before it reaches
    the boot chain of an installed machine, and it must refuse rather than
    silently filter: dropping the bad entries would still emit a
    plausible-looking range derived from a snapshot just proven untrustworthy,
    and the operator would never learn their machine was mis-detected.
    """
    for value in performance:
        # bool is an int subclass in Python - isinstance(True, int) is True -
        # so True must be rejected explicitly, or it would silently mean CPU 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise HardwareError(
                f"performance_cpus contains a non-integer entry: {value!r}"
            )
        if value < 0:
            raise HardwareError(
                f"performance_cpus contains a negative CPU number: {value}"
            )
        if total and value >= total:
            raise HardwareError(
                f"performance_cpus contains CPU {value} but total_cpus is {total}"
            )


def isolation_plan(cpu: dict) -> dict:
    """CPUs handed to the guest, as kernel range strings.

    Derived here rather than by the engine because only a package that pins
    vCPUs knows which cores it wants. Emitted as `nohz_full=` only, never
    `isolcpus=`: isolcpus is a BOOT-time parameter, so it would keep those
    CPUs out of the scheduler for the whole uptime and leave the host on the
    remaining cores even while the guest is shut off. The host/guest split is
    dynamic, done by vm-cpu-partition.sh from the libvirt hooks.

    ABSENT versus MALFORMED are deliberately distinguished, the way
    passthrough_nvme() already refuses to guess rather than return an empty
    dict. An absent snapshot ({}, no performance_cpus, no/zero total_cpus) is
    not an error - there is simply nothing to isolate, so this returns an
    empty plan. A PRESENT but invalid performance_cpus (a non-int entry, a
    negative CPU number, or a CPU number >= total_cpus) raises HardwareError:
    see _validate_performance_cpus for why this must refuse rather than
    filter, given where this string ends up.
    """
    performance = cpu.get("performance_cpus") or []
    total = cpu.get("total_cpus") or 0
    if performance:
        _validate_performance_cpus(performance, total)
    if cpu.get("hybrid") and performance:
        isolated = sorted(performance)
    elif total:
        isolated = [c for c in range(total) if c != 0]
    else:
        isolated = []
    ranges = cpu_ranges(isolated)
    return {"isolcpus": ranges, "nohz_full": ranges}
```

- [ ] **Step 5: Alléger `installer/common/hardware.py`**

Supprimer les fonctions déplacées. Dans `list_gpus`, retirer le champ `ids` et l'appel à `_pci_slot_ids` ; ajouter au docstring :

```python
    """Discrete GPUs, coarsely - enough for the `gpu-discrete` capability.

    Deliberately WITHOUT the slot's vendor:device ids: those are what
    vfio-pci.ids needs, and deriving them is a passthrough package's job, in
    its resolve phase. The engine only has to answer "is there a discrete GPU
    here" before it runs any package code. See console/hardware.py.
    """
```

Dans `cpu_topology`, retirer `isolcpus` et `nohz_full` du dict rendu, et ajouter :

```python
    """CPU topology, coarsely - enough for the `cpu-hybrid` capability.

    Deliberately WITHOUT isolcpus/nohz_full: which CPUs to isolate is only
    knowable by a package that pins vCPUs, and it derives them from
    `performance_cpus` here. See console/hardware.py::isolation_plan.
    """
```

Supprimer `_ranges` s'il n'a plus d'appelant dans le fichier (vérifiez avec `grep -n "_ranges" installer/common/hardware.py`).

- [ ] **Step 6: Repointer l'ancien test**

Dans `scripts/tests/test_windows_guest_hardware.py`, supprimer les cas désormais couverts par `test_console_hardware.py` et garder uniquement ce qui reste dans `installer/common/hardware.py`. Si plus rien n'y reste, **supprimez le fichier** et dites-le dans votre rapport — un test vide est pire qu'un test absent.

- [ ] **Step 7: Lancer les tests**

```bash
python3 scripts/tests/test_console_hardware.py
python3 scripts/tests/test_packages_capabilities.py
python3 scripts/tests/test_windows_guest_hardware.py 2>/dev/null || echo "(supprime)"
python3 -c "
import sys; sys.path.insert(0,'installer')
from common.hardware import detect_all
d = detect_all()
print('cles:', sorted(d))
print('un gpu porte-t-il encore ids ?', any('ids' in g for g in d['gpus']))
print('cpu porte-t-il encore isolcpus ?', 'isolcpus' in d['cpu'])"
```

Attendu : les deux suites `OK`, `ids` **False**, `isolcpus` **False**.

⚠️ `installer/windows-guest/domain.py` importe `common.hardware` et utilise la moitié précise. **Il cassera à cette étape** — c'est attendu et réparé en 2b, quand il entrera dans le package. Vérifiez-le et notez-le, ne le réparez pas :

```bash
grep -n "hardware\." installer/windows-guest/domain.py | head
```

- [ ] **Step 8: Commit**

```bash
git add console/hardware.py installer/common/hardware.py scripts/tests/
git commit -m "refactor(hardware): separer la detection grossiere de la precise

Le moteur doit repondre a requires.capabilities AVANT d executer le
moindre hook, donc il ne peut pas deleguer cette reponse au package. Il
garde le grossier - un IOMMU, un GPU dedie, un NVMe libre - et rien de
plus : verifie, seuls capabilities.py:30 et :41 lisent ces champs dans
tout le moteur.

Le precis part dans console/hardware.py : fonctions PCI d un slot,
selection du NVMe de passthrough, derivation des coeurs a isoler.
_run et _read_int y sont COPIES et non importes - le package doit
tourner sur un Debian qui n a jamais vu ce moteur.

windows-guest/domain.py casse a ce commit : il entre dans le package a
la phase suivante."
```

---

### Task 2: le manifeste, les questions, et le hook `resolve`

**Files:**
- Create: `console/nivuus-package.yaml`, `console/wizard.yaml`, `console/hooks/resolve.py`, `console/README.md`
- Test: `scripts/tests/test_console_resolve.py`

**Interfaces:**
- Consumes: `console/hardware.py` (`vfio_ids_for_slot`, `isolation_plan`, `resolve_passthrough_nvme`, `host_root_pci_addresses`)
- Produces: un package découvrable par `installer/packages/discovery.py` et résolvable par `installer/packages/runner.py`

> **Fix round 2 (revue de code) a touché deux fichiers hors de cette liste
> initiale**, tous deux côté moteur et non côté package :
> - `installer/common/hardware.py` : ajout de `memory_total_mib()` et de la
>   clé `memory_mib` dans `detect_all()` (Partie 1 — ce champ n'existait pas
>   du tout ; `resolve` tombait donc systématiquement sur son repli
>   plancher, une erreur de taille silencieuse, pas un plantage).
> - `scripts/tests/test_common_hardware.py` (nouvelle suite, créée plutôt
>   que d'étendre `test_packages_capabilities.py` : celle-ci teste la
>   conversion `hw dict -> capacités`, pas la détection elle-même — ce
>   nouveau fichier suit la même convention 1-fichier-source-1-suite que
>   `test_console_hardware.py`).
>
> Et la Partie 2 a changé la forme du budget de hugepages dans
> `console/hooks/resolve.py` : `GUEST_MIB_DEFAULT` (16384 MiB fixe, aligné
> sur ce que ce projet a mesuré et gardé après avoir vu l'hôte swapper —
> voir `CLAUDE.md`, "Hugepages pool halved") remplace "moitié de l'hôte",
> clampé vers le BAS uniquement (`min(DEFAULT, host // 2)`), avec le même
> refus qu'avant sous `GUEST_MIB_MIN`. Les deux blocs verbatim ci-dessous
> (Steps 3 et 5) sont à jour avec ce correctif.

- [ ] **Step 1: Écrire le manifeste**

`console/nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: console
version: 1.0.0
label: "Console de jeu Windows"
tier: platform

requires:
  capabilities: [iommu, gpu-discrete, nvme-dedicated]
  features: [networking]

claims:
  gpu: exclusive
  nvme: exclusive

platform:
  modules: [vfio, vfio_pci, vfio_iommu_type1]
  kernel-cmdline: ["intel_iommu=on", "iommu=pt"]

apt:
  - qemu-kvm
  - libvirt-daemon-system
  - libvirt-clients
  - ovmf
  - virtiofsd
  - bridge-utils

wizard:
  questions: wizard.yaml

hooks:
  resolve: hooks/resolve.py
  install: hooks/install.py
  activate: hooks/activate.py
```

- [ ] **Step 2: Écrire les questions**

`console/wizard.yaml` — attention, `retro` ressemble à un booléen YAML 1.1 mais c'est une **valeur de clé**, pas une clé : sans danger. Ne mettez jamais une clé nue `on`/`yes` :

```yaml
- key: dedicated_nvme
  type: disque
  label: "NVMe dédié à la console (il sera donné entièrement à la VM)"
  required: true

- key: retro
  type: bool
  label: "Installer le retrogaming (RetroArch et émulateurs)"
  default: false

- key: admin_password
  type: secret
  label: "Mot de passe administrateur Windows"
  required: true
```

- [ ] **Step 3: Écrire le test, qui échoue**

Créer `scripts/tests/test_console_resolve.py` :

```python
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
```

- [ ] **Step 4: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_console_resolve.py
```

Attendu : `ManifestError` ou `FileNotFoundError` sur `console/nivuus-package.yaml` si les étapes 1-2 ne sont pas faites ; sinon un échec sur le hook `resolve` manquant.

- [ ] **Step 5: Écrire le hook `resolve`**

`console/hooks/resolve.py` :

```python
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
```

Rendre le fichier exécutable : `chmod +x console/hooks/resolve.py`.

- [ ] **Step 6: Écrire le squelette du hook `activate`**

`console/hooks/activate.py` — en 2a il ne fait rien d'autre que dire ce qu'il fera :

```python
#!/usr/bin/env python3
"""Activate phase for the console package - a placeholder until phase 2b.

The guest image is still built by hand, exactly as it was before this
package existed (windows-guest/build.py then domain.py). Wiring that into
this hook is phase 2b's whole subject.

This exits 0 deliberately: the stamp file must be written so systemd stops
retrying a unit that has nothing to do. A hook that failed here would retry
on every boot forever and teach the operator to ignore it.
"""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.parse_args()
    json.load(sys.stdin)
    print(json.dumps({
        "event": "progress", "pct": 100,
        "msg": "console : rien a activer pour l'instant, l'invite Windows se "
               "construit encore a la main (windows-guest/build.py)"}), flush=True)
    print(json.dumps({"event": "done"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`chmod +x console/hooks/activate.py`.

- [ ] **Step 7: Écrire `console/README.md`**

```markdown
# console — la console de jeu Windows, en package Nivuus

This directory is a **Nivuus package** (`nivuus.dev/v1`): the installer
engine discovers it, offers it in the wizard, and installs it through the
same three phases any third-party package goes through. It is not special —
that is the point. If the API were not enough for this, it would not be
enough for anyone.

| Phase | What it does |
|---|---|
| `resolve` | Read-only. Derives `vfio-pci.ids` from the discrete GPU's PCI slot and the dedicated NVMe, `nohz_full` from the CPU topology, and the hugepage budget from host RAM. **Refuses**, with a reason, a machine with no discrete GPU or no properly isolated NVMe. |
| `install` | Deploys the libvirt hooks, the host-side scripts and the wake-on-demand units onto the target. |
| `activate` | **Not implemented yet** (phase 2b). The Windows guest is still built by hand with `installer/windows-guest/build.py`. |

## PCI passthrough only

The dedicated disk is handed over as a **whole PCI device**, never as a disk
image or a virtio block device. A machine whose NVMe cannot be detached from
the host is refused in `resolve` rather than silently downgraded — a console
that boots but performs like a laptop is worse than one that says why it
cannot be installed.

## What is not here yet

`installer/windows-guest/` (the unattended LTSC build, the libvirt domain
generator, the provisioning scripts) still lives outside this directory. It
moves in phase 2b, after which this package is self-contained and phase 3
is a `git filter-repo --path console` away.
```

- [ ] **Step 8: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_console_resolve.py
```

Attendu : `OK - all console resolve tests passed`

Le test imprime une note disant laquelle de ses deux branches il a exercée (« cette machine convient » ou « refus exercé »). **Reportez cette ligne dans votre rapport** : elle dit ce que le test a réellement prouvé sur cette machine-ci.

- [ ] **Step 9: Commit**

```bash
chmod +x console/hooks/resolve.py console/hooks/activate.py
git add console/ scripts/tests/test_console_resolve.py
git commit -m "feat(console): manifeste, questions et phase resolve

La console devient un package ordinaire du moteur : meme contrat, meme
decouverte, meme trois phases qu un package tiers. Si l API ne suffisait
pas pour elle, elle ne suffirait pour personne.

resolve rend ce que le manifeste statique ne peut pas savoir - les
identifiants vfio du slot GPU, les coeurs a laisser sans tic, le budget
de hugepages - ou refuse avec une phrase. Le refus atteint l operateur
AVANT le partitionnement, puisque le moteur execute cette phase avant
de toucher au disque.

Passthrough PCI uniquement : une machine dont le NVMe ne peut pas etre
detache est refusee, jamais silencieusement retrogradee vers une image
disque. Une console qui demarre mais rame est pire qu une qui explique
pourquoi elle ne s installe pas."
```

---

### Task 3: déplacer les scripts et configs hôte dans le package

Déplacement pur, sans changement de comportement. Les tests qui les couvrent doivent rester verts en ne changeant que leurs chemins.

**Files:**
- Move: `scripts/vm-cpu-partition.sh`, `scripts/vm-wake-gate.py`, `scripts/handle-vm-start.sh`, `scripts/winvm`, `scripts/install-winrm-cli.sh`, `scripts/gpu-rebind-debug/` → `console/host/`
- Move: `configs/libvirt/` → `console/host/libvirt/`
- Move: `configs/setup-winrm.ps1` → `console/host/setup-winrm.ps1`
- Modify: `scripts/tests/test_vm_wake_gate.py`, `scripts/tests/test_handle_vm_start.sh`

**Interfaces:**
- Consumes: rien
- Produces: `console/host/` peuplé ; les chemins que le hook `install` de la tâche 4 déploiera

- [ ] **Step 1: Déplacer, en préservant l'historique**

```bash
mkdir -p console/host
git mv scripts/vm-cpu-partition.sh scripts/vm-wake-gate.py \
       scripts/handle-vm-start.sh scripts/winvm scripts/install-winrm-cli.sh \
       console/host/
git mv scripts/gpu-rebind-debug console/host/gpu-rebind-debug
git mv configs/libvirt console/host/libvirt
git mv configs/setup-winrm.ps1 console/host/setup-winrm.ps1
```

- [ ] **Step 2: Repointer les deux tests**

Dans `scripts/tests/test_vm_wake_gate.py` et `scripts/tests/test_handle_vm_start.sh`, remplacer toute référence à `scripts/vm-wake-gate.py` / `scripts/handle-vm-start.sh` par `console/host/…`. Repérez-les :

```bash
grep -n "scripts/vm-wake-gate\|scripts/handle-vm-start\|vm-cpu-partition" \
  scripts/tests/test_vm_wake_gate.py scripts/tests/test_handle_vm_start.sh
```

- [ ] **Step 3: Vérifier que rien d'autre ne pointe vers les anciens chemins**

```bash
grep -rn "scripts/vm-cpu-partition\|scripts/vm-wake-gate\|scripts/handle-vm-start\|scripts/winvm\|configs/libvirt\|configs/setup-winrm" \
  --include='*.sh' --include='*.py' --include='*.md' --include='Makefile' . \
  | grep -v '^./CHANGELOG.md' | grep -v '^./docs/superpowers/'
```

Attendu, **inventorié par le contrôleur avant le dispatch** — cinq lieux, pas deux :

| Référence | Sort |
| --- | --- |
| `install.sh:106` (`cp … scripts/vm-cpu-partition.sh`) | Laisser : le fichier entier disparaît en tâche 5 |
| `CLAUDE.md` (3 passages) | Laisser : mis à jour en tâche 7 |
| `QUICKSTART.md:179` (`install -m 755 scripts/winvm …`) | **Corriger ici** → `console/host/winvm` |
| `docs/winrm-setup.md:55` | **Corriger ici** → chemin relatif `console/host/winvm`. Il porte aujourd'hui un chemin absolu `/home/mallanic/Projects/Nivuus/scripts/winvm`, vestige d'une disposition antérieure — remplacez-le par un chemin relatif au dépôt, pas par un autre absolu |
| `console/host/install-winrm-cli.sh:72` (son propre `echo`) | **Corriger ici** → le script se déplace, son message doit suivre |
| `console/host/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh:3` (commentaire) | **Corriger ici** → il dit « Real logic lives in the repo (scripts/vm-cpu-partition.sh) » |

**Toute occurrence hors de cette liste est un appelant que le déplacement casse** — signalez-la.

- [ ] **Step 4: Lancer les tests déplacés**

```bash
python3 scripts/tests/test_vm_wake_gate.py
bash scripts/tests/test_handle_vm_start.sh
shellcheck console/host/*.sh console/host/libvirt/hooks/qemu.d/Windows/*/*/*.sh
```

Attendu : les deux suites `OK`, shellcheck sans nouvelle trouvaille.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(console): deplacer les scripts et configs hote dans le package

Deplacement pur, aucun changement de comportement. git mv preserve
l historique - ces fichiers portent des mois de mesures et de pieges
documentes (l AppArmor de libvirtd qui interdit /usr/local/sbin, le
deadlock de virsh dans un hook, la porte de reveil Moonlight).

Ils vivent desormais la ou le package les deploiera, ce qui rend la
phase 3 mecanique."
```

---

### Task 4: le hook `install`

Reprend ce que faisait `install.sh` pour la VM, moins ce que le manifeste déclare déjà (modules, hugepages, apt, ligne de commande noyau — le moteur s'en charge).

**Files:**
- Create: `console/hooks/install.py`
- Test: `scripts/tests/test_console_install.py`

**Interfaces:**
- Consumes: `console/host/` (tâche 3), `installer/common/retro.py` (chemin du témoin)
- Produces: sur la cible — `/etc/libvirt/hooks/vm-cpu-partition.sh`, `/etc/libvirt/hooks/qemu.d/Windows/{prepare/begin,release/end}/`, `/usr/local/sbin/{vm-wake-gate.py,handle-vm-start.sh}`, `/usr/local/bin/winvm`, `/etc/nivuus/retro.json`

- [ ] **Step 1: Écrire le test, qui échoue**

Créer `scripts/tests/test_console_install.py` :

```python
#!/usr/bin/env python3
"""Tests for the console package's install hook.

It asserts ARTEFACTS under a temporary root, never calls: the whole point of
this hook is what it leaves on the target filesystem, and a test that mocked
the copying would prove nothing about it.

The AppArmor constraint is the one that matters most here and is invisible
from the code: the libvirtd profile grants "/etc/libvirt/hooks/** rmix", so
a hook runs INHERITING that profile, which allows exec of /bin, /sbin,
/usr/bin and /usr/sbin - but NOT /usr/local/sbin. A partition script
installed there dies at VM start with a misleading "bad interpreter:
Permission denied" and no DENIED line in dmesg.

Run: python3 scripts/tests/test_console_install.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console"
HOOK = CONSOLE / "hooks" / "install.py"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


CTX = json.dumps({
    "package": {"name": "console", "version": "1.0.0", "root": str(CONSOLE)},
    "hw": {"gpus": [{"slot": "01:00.0", "discrete": True}]},
    "answers": {"dedicated_nvme": "/dev/nvme1n1", "retro": True,
                "admin_password": "hunter2hunter2"},
})

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    proc = subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=CTX, capture_output=True, text=True, cwd=str(CONSOLE))
    check("le hook sort 0", proc.returncode, 0)

    partition = root / "etc/libvirt/hooks/vm-cpu-partition.sh"
    check("le script de partitionnement est sous /etc/libvirt/hooks",
          partition.is_file(), True)
    check("il est executable", os.access(partition, os.X_OK), True)
    check("il n est PAS sous /usr/local/sbin (piege AppArmor)",
          (root / "usr/local/sbin/vm-cpu-partition.sh").exists(), False)

    for phase, name in (("prepare/begin", "10-cpu-confine.sh"),
                        ("release/end", "10-cpu-release.sh")):
        w = root / f"etc/libvirt/hooks/qemu.d/Windows/{phase}/{name}"
        check(f"wrapper {name} depose", w.is_file(), True)
        check(f"wrapper {name} executable", os.access(w, os.X_OK), True)
        check(f"wrapper {name} appelle /etc/libvirt/hooks",
              "/etc/libvirt/hooks/vm-cpu-partition.sh" in w.read_text(), True)

    for rel in ("usr/local/sbin/vm-wake-gate.py",
                "usr/local/sbin/handle-vm-start.sh",
                "usr/local/bin/winvm"):
        check(f"{rel} depose", (root / rel).is_file(), True)
        check(f"{rel} executable", os.access(root / rel, os.X_OK), True)

    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit oui", marker["enabled"], True)

# retro decoche : le temoin doit dire non, pas disparaitre
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    ctx["answers"]["retro"] = False
    subprocess.run([sys.executable, str(HOOK), "--phase", "install",
                    "--root", str(root)],
                   input=json.dumps(ctx), capture_output=True, text=True,
                   cwd=str(CONSOLE))
    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit non", marker["enabled"], False)

# retro absente du contexte : meme regle - le temoin dit non, pas rien
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    ctx = json.loads(CTX)
    del ctx["answers"]["retro"]
    subprocess.run([sys.executable, str(HOOK), "--phase", "install",
                    "--root", str(root)],
                   input=json.dumps(ctx), capture_output=True, text=True,
                   cwd=str(CONSOLE))
    marker = json.loads((root / "etc/nivuus/retro.json").read_text())
    check("le temoin retro dit non quand retro est absente", marker["enabled"],
          False)

# bool("false") est True en Python - le meme piege de coercion deja corrige
# une fois sur 'required' dans packages/wizard.py. Une chaine, quel que soit
# son sens de lecture, doit etre refusee, jamais interpretee ; un nombre de
# meme. La refuser signifie : sortir non-zero, ne rien ecrire, et nommer la
# cle en cause.
for bad_value in ("false", "true", 1):
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        ctx = json.loads(CTX)
        ctx["answers"]["retro"] = bad_value
        proc = subprocess.run(
            [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
            input=json.dumps(ctx), capture_output=True, text=True,
            cwd=str(CONSOLE))
        check(f"retro={bad_value!r} : le hook sort non-zero",
              proc.returncode != 0, True)
        check(f"retro={bad_value!r} : erreur nomme 'retro'",
              "retro" in proc.stderr, True)
        check(f"retro={bad_value!r} : aucun temoin ecrit",
              (root / "etc/nivuus/retro.json").exists(), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all console install tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_console_install.py
```

Attendu : un échec sur `le hook sort 0` (le fichier n'existe pas).

- [ ] **Step 3: Écrire le hook**

`console/hooks/install.py` :

```python
#!/usr/bin/env python3
"""Install phase for the console package: what the manifest cannot declare.

The engine already handles the declarative half - apt packages, kernel
modules, hugepages, and the kernel command line resolve() returned. What is
left is placing files on the target, and one of those placements is not a
matter of taste.

THE APPARMOR TRAP. vm-cpu-partition.sh MUST live under /etc/libvirt/hooks/.
The libvirtd profile grants "/etc/libvirt/hooks/** rmix": hooks run
INHERITING that profile, which allows exec of /bin, /sbin, /usr/bin and
/usr/sbin - but NOT /usr/local/sbin. A partition script installed there dies
at VM start with "/bin/bash: bad interpreter: Permission denied" and NO
AppArmor DENIED line in dmesg. It cost a full VM-start cycle to find once;
it is encoded here so it cannot be rediscovered.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VM_NAME = "Windows"

# The wrappers stay thin so the logic lives in the repo, not in a heredoc.
# They exit 0 unconditionally: a hook that fails must never block a VM start.
CONFINE_WRAPPER = """#!/bin/bash
# Confine the host cgroups to the CPUs the VM does not pin, while it runs.
/etc/libvirt/hooks/vm-cpu-partition.sh confine "$1" \\
    >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
"""

RELEASE_WRAPPER = """#!/bin/bash
# Hand every CPU back once the VM is gone (shutdown or hibernation).
/etc/libvirt/hooks/vm-cpu-partition.sh release "$1" \\
    >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
"""


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def place(src: str, dest: str, mode: int = 0o755) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    os.chmod(dest, mode)


def write(dest: str, content: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        fh.write(content)
    os.chmod(dest, mode)


def retro_choice(answers: dict) -> bool:
    """The retrogaming checkbox, accepted only as a genuine boolean.

    `bool("false")` is True in Python - the same coercion trap this project
    already hit once, on `required: bool(item.get("required", False))` in
    packages/wizard.py, and fixed there with an explicit isinstance check.
    The wizard's own validator (_check_value in packages/wizard.py) already
    refuses a non-boolean 'retro' answer - but this hook reads its context
    from stdin, and a hand-written config.json driving the engine outside
    the portal (the standalone path phase 2b exists to enable) never passes
    through that validator. So the same rule is enforced again here,
    independently: a value this hook cannot interpret means the caller and
    the package disagree about the contract, and silently picking a reading
    is exactly how the original bug happened. Raises ValueError naming the
    key and the offending value; never coerces.
    """
    value = answers.get("retro", False)
    if not isinstance(value, bool):
        raise ValueError(f"answer 'retro' expects true/false, got {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    ctx = json.load(sys.stdin)
    answers = ctx.get("answers") or {}
    root = args.root.rstrip("/") or "/"

    # Fail fast, before a single byte lands on the target: a caller and the
    # package disagreeing about 'retro' is a contract error, not something
    # to work around mid-placement.
    try:
        retro_enabled = retro_choice(answers)
    except ValueError as exc:
        print(f"console install: {exc}", file=sys.stderr)
        return 1

    def under(rel: str) -> str:
        return os.path.join(root, rel.lstrip("/"))

    emit({"event": "progress", "pct": 20,
          "msg": "Deploiement des hooks libvirt"})

    # See the module docstring: this path is load-bearing, not stylistic.
    place(os.path.join(HERE, "host", "vm-cpu-partition.sh"),
          under("etc/libvirt/hooks/vm-cpu-partition.sh"))
    base = f"etc/libvirt/hooks/qemu.d/{VM_NAME}"
    write(under(f"{base}/prepare/begin/10-cpu-confine.sh"),
          CONFINE_WRAPPER, mode=0o755)
    write(under(f"{base}/release/end/10-cpu-release.sh"),
          RELEASE_WRAPPER, mode=0o755)

    emit({"event": "progress", "pct": 50, "msg": "Deploiement des scripts hote"})
    place(os.path.join(HERE, "host", "vm-wake-gate.py"),
          under("usr/local/sbin/vm-wake-gate.py"))
    place(os.path.join(HERE, "host", "handle-vm-start.sh"),
          under("usr/local/sbin/handle-vm-start.sh"))
    place(os.path.join(HERE, "host", "winvm"), under("usr/local/bin/winvm"))

    # The operator's retrogaming choice, recorded durably on the target.
    # windows-guest/build.py reads it much later - possibly by hand, possibly
    # on this very host once it has booted - so it must outlive the installer.
    # An UNCHECKED box writes `false` rather than nothing: "absent" and
    # "declined" would otherwise be indistinguishable to the reader.
    emit({"event": "progress", "pct": 80, "msg": "Choix retrogaming enregistre"})
    write(under("etc/nivuus/retro.json"),
          json.dumps({"enabled": retro_enabled}, indent=2) + "\\n")

    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`chmod +x console/hooks/install.py`.

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_console_install.py
```

Attendu : `OK - all console install tests passed`

- [ ] **Step 5: Commit**

```bash
chmod +x console/hooks/install.py
git add console/hooks/install.py scripts/tests/test_console_install.py
git commit -m "feat(console): phase install, et le piege AppArmor encode

Le moteur gere deja le declaratif - apt, modules, hugepages, ligne de
commande noyau. Restait a poser des fichiers, dont un dont l emplacement
n est pas affaire de gout.

vm-cpu-partition.sh DOIT vivre sous /etc/libvirt/hooks/ : le profil
AppArmor de libvirtd y accorde rmix, donc un hook s execute en HERITANT
ce profil, qui autorise /bin, /sbin, /usr/bin et /usr/sbin mais PAS
/usr/local/sbin. Installe ailleurs, il meurt au demarrage de la VM sur
un « bad interpreter: Permission denied » trompeur, sans la moindre
ligne DENIED dans dmesg. Le test verrouille l emplacement.

Le temoin retro ecrit `false` quand la case est decochee plutot que rien:
« absent » et « refuse » seraient sinon indistinguables pour son lecteur."
```

---

### Task 5: dissoudre `install.sh`

Sur ses sept blocs, cinq sont de la mise en place VM et vivent désormais dans le package. Reste la thermique.

**Files:**
- Delete: `install.sh`
- Modify: `installer/install-engine/steps/features.py` (`_kvm_vfio_thermal` → `_thermal`, suppression de `_retro` et de l'import `from common.retro import retro_state_path`)
- Modify: `scripts/tests/test_install_engine_features.py`
- Modify: `scripts/tests/test_retro_marker_bridge.py` — **le pont change de rive, voir ci-dessous**
- Modify: `installer/install-engine/steps/validate.py` si elle référence `install.sh`

⚠️ **Le pont retro casse à cette tâche si on n'y touche pas.** `test_retro_marker_bridge.py` teste aujourd'hui que `features.retro_state_path` **est** l'objet exporté par `common/retro.py` — or cette tâche retire cet import de `features.py`. Le test échouerait à l'import.

Il ne peut pas simplement être repointé vers le package : **`console/` ne peut rien importer de `installer/`** (contrainte d'autonomie), donc `console/hooks/install.py` porte le chemin en littéral, tandis que `common/retro.py` le porte en constante. Ils coïncident aujourd'hui par la seule vigilance de qui les a tapés — c'est exactement la divergence que ce test existait pour empêcher.

Le garde-fou doit donc changer de nature : au lieu de comparer deux objets Python, **exécuter le hook install du package sur une cible temporaire et vérifier que le fichier atterrit précisément à `common.retro.retro_state_path(cible)`**, le chemin que `windows-guest/build.py` ira lire. C'est un test plus fort que l'ancien : il vérifie l'artefact, pas l'import — et il resterait vrai même si les deux côtés cessaient de partager le moindre symbole, ce qui est justement ce vers quoi la phase 2b les emmène.

**Interfaces:**
- Consumes: rien
- Produces: `features.py::_thermal(target, nivuus_dir, emit)` — déploie `optimize-cpu-thermal.sh` et son unité, rien d'autre

- [ ] **Step 1: Constater qui appelle encore `install.sh`**

```bash
grep -rn "install\.sh" --include='*.py' --include='*.sh' --include='*.md' --include='Makefile' . \
  | grep -v '^./CHANGELOG.md' | grep -v '^./docs/superpowers/' | grep -v '^./install.sh'
```

Notez chaque appelant : ils doivent tous disparaître ou changer dans cette tâche ou la tâche 7.

- [ ] **Step 2: Réécrire le test de la feature**

`scripts/tests/test_install_engine_features.py` teste aujourd'hui `retro`, qui quitte ce fichier. Le remplacer par un test de `_thermal` :

```python
#!/usr/bin/env python3
"""Tests for the thermal feature - all that remains of install.sh.

install.sh had seven blocks; five were VM setup and now live in the console
package's install hook. What was left is fifteen lines: deploy the thermal
script and its unit. The NIVUUS_DIR / NIVUUS_IN_CHROOT / NIVUUS_ISOLCPUS /
NIVUUS_VFIO_IDS plumbing existed only to make that script runnable inside a
chroot, and it went with it.

Run: python3 scripts/tests/test_install_engine_features.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import features  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(msg)

    def warn(self, step, pct, msg):
        self.lines.append(msg)

    def error(self, step, pct, msg):
        self.lines.append(msg)


calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)

    class R:
        returncode = 0
    return R()


features.chroot_run = fake_chroot_run

# Sans la feature thermal, rien ne doit etre pose.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    features.apply_features({"features": ["os-base"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    check("aucune unite thermique sans la feature",
          (target / "etc/systemd/system/cpu-thermal-optimization.service").exists(),
          False)

# Avec la feature, l unite et le script arrivent.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    payload = target / "opt/nivuus/scripts"
    payload.mkdir(parents=True)
    (payload / "optimize-cpu-thermal.sh").write_text("#!/bin/bash\ntrue\n")

    features.apply_features({"features": ["os-base", "thermal"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    unit = target / "etc/systemd/system/cpu-thermal-optimization.service"
    check("l unite thermique est posee", unit.is_file(), True)
    check("elle pointe vers le script deploye",
          "/usr/local/bin/optimize-cpu-thermal.sh" in unit.read_text(), True)
    check("le script est deploye",
          (target / "usr/local/bin/optimize-cpu-thermal.sh").is_file(), True)
    check("l unite est activee",
          any("cpu-thermal-optimization.service" in " ".join(c) for c in calls),
          True)

# Les features VM ont quitte ce fichier.
check("plus de _kvm_vfio_thermal", hasattr(features, "_kvm_vfio_thermal"), False)
check("plus de _retro", hasattr(features, "_retro"), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all thermal feature tests passed")
```

- [ ] **Step 3: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_install_engine_features.py
```

Attendu : échec sur `plus de _kvm_vfio_thermal` (elle existe encore).

- [ ] **Step 4: Réécrire `features.py`**

Supprimer `_kvm_vfio_thermal` et `_retro`, ainsi que l'import `from common.retro import retro_state_path`. Remplacer le branchement :

```python
    if "thermal" in features:
        _thermal(target, nivuus_dir, emit)
```

et ajouter :

```python
def _thermal(target, nivuus_dir, emit) -> None:
    """Deploy the host thermal policy - all that survived install.sh.

    This is a HOST policy, not a VM one: RAPL power capping and the fan curve
    apply whether or not a guest ever runs. But its gaming/idle modes are
    driven by the console package's libvirt hooks, through
    `nivuus-cpu-mode@{gaming,idle}.service`. That unit name is therefore a
    PUBLIC CONTRACT of this repository: the package calls it if it exists and
    does nothing if it does not, which is what lets the package install on a
    Debian that has never seen this installer.
    """
    emit.info("features", 80, "Installing host thermal policy…")
    src = os.path.join(target, nivuus_dir.lstrip("/"),
                       "scripts/optimize-cpu-thermal.sh")
    if not os.path.isfile(src):
        emit.warn("features", 80,
                  f"optimize-cpu-thermal.sh absent de la charge utile ({src}) ; "
                  "politique thermique non installee")
        return
    dest = os.path.join(target, "usr/local/bin/optimize-cpu-thermal.sh")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    os.chmod(dest, 0o755)

    write_file(
        os.path.join(target, "etc/systemd/system/cpu-thermal-optimization.service"),
        "[Unit]\n"
        "Description=CPU thermal policy (RAPL caps, fan curve, core frequencies)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/bin/optimize-cpu-thermal.sh\n"
        "RemainAfterExit=yes\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n")
    chroot_run(target, ["systemctl", "enable",
                        "cpu-thermal-optimization.service"], check=False)
```

Ajouter `import shutil` en tête si absent.

⚠️ **Ne recopiez PAS le `After=multi-user.target` de l'ancienne unité.** Le `CLAUDE.md` documente un cycle d'ordonnancement causé exactement par ce motif le 2026-07-16 : systemd a cassé le cycle en supprimant un job **arbitraire**, et ce jour-là ce fut `docker.service` — 34 conteneurs absents au boot, sans trace d'échec.

- [ ] **Step 5: Supprimer `install.sh`**

```bash
git rm install.sh
```

- [ ] **Step 6: Lancer les tests**

```bash
python3 scripts/tests/test_install_engine_features.py
cd installer && make test-packages PYTHON=<python-du-venv> ; cd ..
```

Attendu : la suite thermique `OK`, l'agrégateur intégralement vert.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(installer): dissoudre install.sh

Sur ses sept blocs, cinq etaient de la mise en place VM : paquets
qemu/libvirt, hooks de partitionnement CPU, hugepages, nohz_full et
vfio-pci.ids dans GRUB, modules VFIO. Ils vivent desormais dans le
manifeste et le hook install du package console.

Restait la thermique - quinze lignes, qui deviennent une feature
ordinaire. Toute la machinerie NIVUUS_DIR / NIVUUS_IN_CHROOT /
NIVUUS_ISOLCPUS / NIVUUS_VFIO_IDS n existait que pour rendre ce script
executable dans un chroot : elle part avec lui.

L unite thermique ne porte PAS After=multi-user.target. Ce motif a
provoque un cycle d ordonnancement le 2026-07-16, que systemd a casse
en supprimant un job arbitraire - ce jour-la docker.service, donc 34
conteneurs absents au boot sans trace d echec."
```

---

### Task 6: nettoyer le vocabulaire du wizard

**Files:**
- Modify: `installer/webapp/models.py`
- Modify: `scripts/tests/test_webapp_models.py`

**Interfaces:**
- Consumes: rien
- Produces: `KNOWN_FEATURES` sans `kvm-vfio`, `gpu-passthrough`, `retro` ; `GpuPassthrough` et `CpuConfig` supprimés si plus personne ne les lit

- [ ] **Step 1: Vérifier qui lit encore les champs VM**

```bash
grep -rn "gpu_passthrough\|GpuPassthrough\|CpuConfig\|config\[.cpu.\]\|\"kvm-vfio\"\|'kvm-vfio'" \
  --include='*.py' --include='*.js' --include='*.html' installer scripts | grep -v iso-build
```

Tout appelant restant doit disparaître ici. **S'il en reste un que cette tâche ne prévoit pas, signalez-le avant de supprimer.**

- [ ] **Step 2: Adapter le test**

Dans `scripts/tests/test_webapp_models.py`, supprimer les cas portant sur `retro` + `kvm-vfio` (la contrainte n'existe plus : `retro` est une question du package `console`) et ajouter :

```python
# --- les features VM ont quitte le wizard -------------------------------- #
for parti in ("kvm-vfio", "gpu-passthrough", "retro"):
    check(f"{parti} n est plus une feature",
          parti in models.KNOWN_FEATURES, False)
    check_raises(
        f"{parti} est refuse comme feature inconnue",
        lambda p=parti: models.InstallConfig(**base_kwargs(["os-base", p])),
    )

# retro se coche desormais comme reponse du package console
cfg_console = models.InstallConfig(
    **base_kwargs(["os-base", "networking"]),
    packages={"console": {"retro": True, "dedicated_nvme": "/dev/nvme1n1"}})
check("retro voyage dans les reponses du package",
      cfg_console.packages["console"]["retro"], True)
```

- [ ] **Step 3: Lancer le test pour le voir échouer**

```bash
<python-du-venv> scripts/tests/test_webapp_models.py
```

Attendu : échec sur `kvm-vfio n est plus une feature`.

- [ ] **Step 4: Nettoyer `models.py`**

```python
# Feature keys the wizard offers; the engine gates each step on these.
#
# The VM features are gone: kvm-vfio, gpu-passthrough and retro are the
# `console` package and two of its questions. The engine no longer knows
# "the VM" - it knows packages, and console goes through the same door a
# third party would.
KNOWN_FEATURES = {
    "os-base", "thermal", "networking", "wifi-ap",
    "firewall", "docker", "home-assistant", "mqtt",
}
```

Supprimer le validateur `retro`/`kvm-vfio`. Supprimer `GpuPassthrough` et `CpuConfig` ainsi que leurs champs dans `InstallConfig` **si et seulement si** l'étape 1 a confirmé qu'ils n'ont plus de lecteur ; sinon, laissez-les et dites pourquoi.

- [ ] **Step 5: Lancer les tests**

```bash
<python-du-venv> scripts/tests/test_webapp_models.py
cd installer && make test-packages PYTHON=<python-du-venv> ; cd ..
```

- [ ] **Step 6: Commit**

```bash
git add installer/webapp/models.py scripts/tests/test_webapp_models.py
git commit -m "refactor(portail): retirer les features VM du vocabulaire du wizard

kvm-vfio, gpu-passthrough et retro ne sont plus des features : ce sont
le package console et deux de ses questions. Le moteur ne connait plus
« la VM », il connait des packages - et console passe par la meme porte
qu un tiers.

La contrainte « retro exige kvm-vfio » disparait avec eux : c est
desormais le manifeste du package qui declare ce dont il a besoin, et
son hook resolve qui refuse une machine qui ne convient pas."
```

---

### Task 7: brancher, prouver, documenter

**Files:**
- Modify: `installer/Makefile` (ajouter les trois nouvelles suites à `test-packages`)
- Modify: `installer/README.md`, `CLAUDE.md`, `QUICKSTART.md`
- Test: exécution réelle du moteur contre `console/`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: rien de nouveau — la preuve que l'ensemble tient

- [ ] **Step 1: Ajouter les suites à l'agrégateur**

Dans `installer/Makefile`, ajouter `test_console_hardware test_console_resolve test_console_install` à la boucle `test-packages`. Mettre à jour le compte annoncé (neuf → douze).

- [ ] **Step 2: Prouver que le moteur planifie et applique `console`**

```bash
NIVUUS_PACKAGES_DIR=$PWD <python-du-venv> - <<'PY'
import os, sys, json, tempfile, pathlib, shutil
sys.path[:0] = ["installer/install-engine", "installer"]
from steps import packages as sp
from common import hardware

class E:
    def info(s, *a): print("  [info]", a[-1])
    def warn(s, *a): print("  [warn]", a[-1])
    def error(s, *a): print("  [err ]", a[-1])

hw = hardware.detect_all()
hw["memory_mib"] = 65536
cfg = {"disk": {"path": "/dev/nvme0n1"}, "features": ["os-base", "networking"],
       "packages": {"console": {"dedicated_nvme": "", "admin_password": "x",
                                "retro": True}}}
try:
    plan, cmdline = sp.plan_packages(cfg, hw, E())
    print("PLAN OK — cmdline:", cmdline)
except Exception as exc:
    print(f"REFUS ({type(exc).__name__}):", exc)
PY
```

Attendu, **selon la machine** : sur un hôte avec IOMMU, GPU dédié et NVMe détachable, un plan et une ligne de commande contenant `vfio-pci.ids=…` et `nohz_full=…`. Sur une machine qui ne convient pas, **un refus avec sa phrase** — ce qui est un succès du test, pas un échec. Notez lequel des deux vous obtenez et pourquoi.

⚠️ `NIVUUS_PACKAGES_DIR` doit pointer vers le **parent** du répertoire `console/`, c'est-à-dire la racine du dépôt : `discover()` liste les sous-répertoires de ce chemin. Et il se lie **à l'import** — définissez-le avant d'importer, comme ci-dessus.

- [ ] **Step 3: Lancer l'agrégateur complet**

```bash
cd installer && make test-packages PYTHON=<python-du-venv>
```

Attendu : douze `OK`.

- [ ] **Step 4: Mettre à jour `installer/README.md`**

Dans la section `## Packages`, après l'exemple `PACKAGE_REPOS`, ajouter :

```markdown
### `console`, the first package

`console/` in this repository is the reference consumer of this API: the
Windows gaming guest, installed through exactly the three phases a
third-party package goes through. It is deliberately not privileged — if the
contract were not enough for it, it would not be enough for anyone.

```bash
PACKAGE_REPOS="$PWD/console" sudo -E make build-iso
```

Its `resolve` phase refuses, with a reason, any machine with no discrete GPU
or no properly isolated NVMe: the console is PCI-passthrough only, and a
silent fallback to a disk image would deliver something slower than what was
asked for.
```

- [ ] **Step 5: Mettre à jour `CLAUDE.md`**

Dans la section *Installer Architecture*, remplacer le paragraphe **Reuse, don't duplicate** (qui décrit `install.sh`, désormais supprimé) par :

```markdown
**`install.sh` is gone (2026-08-27).** Five of its seven blocks were VM
setup and now live in the `console` package (`console/nivuus-package.yaml`
plus `console/hooks/`); the thermal block became an ordinary
`install-engine` feature. The `NIVUUS_DIR` / `NIVUUS_IN_CHROOT` /
`NIVUUS_ISOLCPUS` / `NIVUUS_VFIO_IDS` env plumbing existed only to make that
script runnable inside a chroot and went with it.

**`console/` is the first Nivuus package, and it is deliberately ordinary.**
It declares `tier: platform`, claims `gpu` and `nvme` exclusively, and
requires the `iommu`, `gpu-discrete` and `nvme-dedicated` capabilities. Two
things about it are easy to break:

* **`vm-cpu-partition.sh` MUST be deployed under `/etc/libvirt/hooks/`**, and
  `console/hooks/install.py` does so. The libvirtd AppArmor profile grants
  `/etc/libvirt/hooks/** rmix`, so hooks run *inheriting* that profile, which
  allows exec of `/bin`, `/sbin`, `/usr/bin` and `/usr/sbin` — but **not**
  `/usr/local/sbin`. Installed there instead it dies at VM start with a
  misleading `bad interpreter: Permission denied` and no DENIED line in dmesg.
* **`nivuus-cpu-mode@{gaming,idle}.service` is a public contract of this
  repository.** The thermal policy stays here (it is a host policy), but its
  modes are driven by the package's libvirt hooks. The package calls the unit
  if it exists and does nothing if it does not — which is what lets it install
  on a Debian that has never seen this installer.

`hardware.py` is now split by that same principle: `installer/common/hardware.py`
detects **capabilities** (coarse: is there an IOMMU, a discrete GPU, a spare
NVMe — `list_gpus` no longer carries `ids`, `cpu_topology` no longer carries
`isolcpus`), and `console/hardware.py` detects the **details** in its resolve
phase. Tests: `cd installer && make test-packages` (12 files).
```

- [ ] **Step 6: Corriger `QUICKSTART.md`**

```bash
grep -n "install.sh" QUICKSTART.md
```

Remplacer toute instruction `sudo ./install.sh` par la voie réelle : construire l'ISO et passer par le wizard, ou lancer le moteur avec `--stop-after` pour un essai. Ne laissez pas une commande morte.

- [ ] **Step 7: Vérifier qu'aucune référence morte ne subsiste**

```bash
grep -rn "install\.sh\|kvm-vfio\|gpu-passthrough" \
  --include='*.py' --include='*.sh' --include='*.md' --include='Makefile' . \
  | grep -v '^./CHANGELOG.md' | grep -v '^./docs/superpowers/' | grep -v '^./console/'
```

Attendu : **aucune sortie**, hors `CLAUDE.md` là où il décrit l'histoire.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "docs(console): brancher le package et aligner la documentation

L agregateur couvre desormais douze suites. README, CLAUDE.md et
QUICKSTART decrivent ce qui existe : install.sh a disparu, console est
un package ordinaire, et hardware.py est coupe selon le meme principe
que tout le reste - le moteur detecte les capacites, le package detecte
les details."
```

---

## Ce que ce plan ne fait PAS

Périmètre de **2b**, volontairement hors sujet ici :

* **`installer/windows-guest/` ne bouge pas.** `domain.py` casse en tâche 1 (il importe la moitié précise de `hardware.py`) et reste cassé jusqu'en 2b. C'est assumé et signalé, pas ignoré.
* **`console/hooks/activate.py` ne construit rien.** Il journalise et sort 0, pour que le témoin s'écrive et que systemd cesse de réessayer une unité qui n'a rien à faire.
* **`installer/common/retro.py` reste partagé** : le hook install du package écrit le témoin, `windows-guest/build.py` le lit encore depuis `installer/`. Le pont ne disparaît qu'en 2b.
* **Les 13 suites `test_windows_guest_*` restent où elles sont**, sauf `test_windows_guest_hardware.py` que la tâche 1 ampute ou supprime.
* **La phase 3** (`git filter-repo --path console`) reste intacte, et devient mécanique une fois 2b terminée.
