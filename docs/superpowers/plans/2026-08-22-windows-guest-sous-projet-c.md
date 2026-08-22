# Sous-projet C — génération du domaine libvirt : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produire le domaine libvirt de l'invité Windows par génération depuis le matériel détecté, prêt à `virsh define`, avec Secure Boot, vTPM et passthrough — et sans le masquage d'hyperviseur du domaine actuel.

**Architecture:** Deux fonctions de détection ajoutées à `installer/common/hardware.py` (fonctions PCI d'un slot ; NVMe à passer, identifié par élimination du disque racine de l'hôte), une dérivation du plan CPU, un gabarit Jinja2, et un module `domain.py` qui rend le XML et le définit. Chaque fonction de détection est scindée en **un analyseur pur** (testable sur du texte capturé) et **une enveloppe mince** qui appelle le système.

**Tech Stack:** Python 3.13, Jinja2 (déjà utilisé par l'installeur), `lspci`, sysfs, `virsh`. Tests : scripts Python autonomes dans `scripts/tests/`, lancés directement, **sans pytest** — c'est la convention du dépôt.

**Spec:** [`docs/superpowers/specs/2026-08-22-windows-guest-domaine-design.md`](../specs/2026-08-22-windows-guest-domaine-design.md)

## Global Constraints

- Le domaine s'appelle **`Windows`** — les crochets libvirt sont indexés par nom.
- La MAC est **`52:54:00:48:e0:3e`** — `dhcp-host` l'épingle à `192.168.3.2`, dont dépendent `game.allanic.me`, les redirections firewalld et le réveil à la demande.
- Firmware **explicite**, jamais `<os firmware='efi'>` : `OVMF_CODE_4M.secboot.fd` + varstore issu de `OVMF_VARS_4M.ms.fd`, avec `<smm state='on'/>`.
- vTPM 2.0 : `<tpm model='tpm-crb'>`, backend `emulator` version `2.0`.
- **Interdits** dans le XML généré : `<kvm><hidden>`, `<vendor_id>`, `<sysinfo>`/`<smbios mode='sysinfo'>`, `<rom bar=...>`, `<watchdog model='i6300esb'>`.
- `<hostdev>` porte **toujours** `<driver name='vfio'/>` et `managed='yes'` — sans le premier, libvirt refuse le démarrage avec « host doesn't support passthrough of host PCI devices ».
- `cputune` doit rester analysable par `vm-cpu-partition.sh`, qui dérive le cpuset de l'hôte depuis `vcpupin` + `emulatorpin`.
- `<memoryBacking>` porte `<hugepages/>`, `<locked/>` et `<access mode='shared'/>` (exigé par virtiofs).
- Aucun secret n'entre dans le dépôt ni dans une ligne de commande.
- Commentaires de code en anglais (convention du dépôt) ; documentation en français.

---

## Structure de fichiers

| Fichier | Responsabilité |
| --- | --- |
| `installer/common/hardware.py` | **modifié** : ajout de `parse_pci_functions`, `pci_slot_functions`, `parse_nvme_controllers`, `select_passthrough_nvme`, `host_root_pci_address`, `passthrough_nvme` |
| `installer/windows-guest/domain.py` | **créé** : `vcpu_plan`, `domain_xml`, CLI `xml` / `define` |
| `installer/windows-guest/templates/domain.xml.j2` | **créé** : le gabarit |
| `scripts/tests/test_windows_guest_hardware.py` | **créé** : les analyseurs purs, sur du texte `lspci` capturé |
| `scripts/tests/test_windows_guest_production_domain.py` | **créé** : `vcpu_plan` et le rendu du XML |
| `docs/superpowers/plans/recette-s4.md` | **créé** : la recette d'acceptation S4, manuelle par nature |

`testdomain.py` et `domain-test.xml.j2` **restent** : ils servent de banc jetable et ne sont pas remplacés.

---

### Task 1 : fonctions PCI d'un slot

**Files:**
- Modify: `installer/common/hardware.py` (ajout après `_pci_slot_ids`)
- Test: `scripts/tests/test_windows_guest_hardware.py` (créé)

**Interfaces:**
- Consomme : rien.
- Produit : `parse_pci_functions(raw: str, slot: str) -> list[dict]` et `pci_slot_functions(slot: str) -> list[dict]`. Chaque entrée : `{"address": "0000:01:00.0", "domain": "0x0000", "bus": "0x01", "slot": "0x00", "function": "0x0", "id": "10de:2786", "description": "NVIDIA Corporation AD104 [GeForce RTX 4070]"}`.

`_pci_slot_ids` rend les identifiants `vendor:device`, dont a besoin `vfio-pci.ids`. `<hostdev>` a besoin d'autre chose : les **adresses** décomposées. D'où cette fonction.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `scripts/tests/test_windows_guest_hardware.py` :

```python
#!/usr/bin/env python3
"""Tests for the PCI/NVMe detection helpers used by the Windows guest domain.

Both parsers are pure: they take captured `lspci` text, so these tests run
anywhere and do not depend on the machine they execute on.

Run: python3 scripts/tests/test_windows_guest_hardware.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from common import hardware  # noqa: E402

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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - hardware detection checks passed")
```

- [ ] **Étape 2 : lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_hardware.py`
Expected: `AttributeError: module 'common.hardware' has no attribute 'parse_pci_functions'`

- [ ] **Étape 3 : écrire l'implémentation minimale**

Dans `installer/common/hardware.py`, après `_pci_slot_ids` :

```python
def parse_pci_functions(raw: str, slot: str) -> list[dict]:
    """Every PCI function sharing `slot`, decomposed for libvirt <address>.

    `_pci_slot_ids` returns vendor:device pairs, which is what vfio-pci.ids
    needs. A <hostdev> needs the address instead, split into domain/bus/slot/
    function and hex-prefixed. `slot` may be "01:00.0" or "0000:01:00.0".

    Sorted by function number so generated <hostdev> entries keep a stable
    order across runs.
    """
    wanted = slot.split(":", 1)[1] if slot.count(":") == 2 else slot
    wanted_prefix = wanted.rsplit(".", 1)[0]
    found = []
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        addr = parts[0]
        if addr.count(":") != 2:
            continue
        domain, bus, rest = addr.split(":")[0], addr.split(":")[1], addr.split(":")[2]
        if f"{bus}:{rest}".rsplit(".", 1)[0] != wanted_prefix:
            continue
        dev, func = rest.split(".", 1)
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
        found.append(
            {
                "address": addr,
                "domain": f"0x{domain}",
                "bus": f"0x{bus}",
                "slot": f"0x{dev}",
                "function": f"0x{int(func, 16):x}",
                "id": f"{m.group(1).lower()}:{m.group(2).lower()}" if m else "",
                "description": _clean_lspci_desc_from_nn(line),
            }
        )
    return sorted(found, key=lambda f: f["function"])


def _clean_lspci_desc_from_nn(line: str) -> str:
    """Human label from an `lspci -nn` line: the text between ': ' and ' ['."""
    after_colon = line.split(": ", 1)[1] if ": " in line else line
    return re.sub(r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\].*$", "", after_colon).strip()


def pci_slot_functions(slot: str) -> list[dict]:
    """parse_pci_functions applied to this machine."""
    return parse_pci_functions(_run(["lspci", "-nn", "-D"]), slot)
```

- [ ] **Étape 4 : lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_hardware.py`
Expected: `OK - hardware detection checks passed`

- [ ] **Étape 5 : vérifier sur la machine réelle**

Run: `python3 -c "import sys; sys.path.insert(0,'installer'); from common import hardware; print(hardware.pci_slot_functions('01:00.0'))"`
Expected: deux entrées, `0000:01:00.0` (`10de:2786`) et `0000:01:00.1` (`10de:22bc`).

- [ ] **Étape 6 : commit**

```bash
git add installer/common/hardware.py scripts/tests/test_windows_guest_hardware.py
git commit -m "feat(hardware): decompose PCI slot functions for libvirt hostdev"
```

---

### Task 2 : détection du NVMe à passer à l'invité

**Files:**
- Modify: `installer/common/hardware.py`
- Test: `scripts/tests/test_windows_guest_hardware.py` (étendu)

**Interfaces:**
- Consomme : `parse_pci_functions` (Task 1) pour décomposer l'adresse retenue.
- Produit :
  - `parse_nvme_controllers(raw: str) -> list[dict]` — `[{"address", "id", "description"}]`
  - `select_passthrough_nvme(controllers: list[dict], host_addresses: set[str]) -> dict` — lève `HardwareError`
  - `resolve_passthrough_nvme(raw: str, host_addresses: set[str]) -> dict` — **pur**, rend la forme décomposée de Task 1 (`{"address", "domain", "bus", "slot", "function", "id", "description"}`)
  - `host_root_pci_address() -> Optional[str]`
  - `passthrough_nvme() -> dict` — enveloppe mince, même forme décomposée
- Produit aussi : `class HardwareError(RuntimeError)`.

⚠️ La forme rendue est **décomposée**, comme celle de Task 1 : le gabarit de
Task 4 consomme `nvme.bus`, `nvme.slot`, `nvme.function`. Un dictionnaire
réduit à `{address, id}` rendrait un XML avec des attributs vides, que libvirt
accepterait avant d'échouer au démarrage.

🔴 **Ce disque sera effacé.** La détection doit donc refuser de deviner : elle
identifie le NVMe **par élimination** du contrôleur qui porte la racine de
l'hôte, et **lève une erreur** s'il reste plus d'un candidat ou aucun. Un
mauvais choix ici détruit le système hôte.

Le disque à passer est lié à `vfio-pci` : il n'apparaît **pas** dans
`list_disks()`, qui lit `lsblk`. La détection passe obligatoirement par la
classe PCI `0108`.

- [ ] **Étape 1 : écrire le test qui échoue**

Ajouter à `scripts/tests/test_windows_guest_hardware.py`, avant le bloc final
`if failures:` :

```python
ctrls = hardware.parse_nvme_controllers(LSPCI)
check("two NVMe controllers", len(ctrls), 2)
check("host controller listed", ctrls[0]["address"], "0000:02:00.0")
check("guest controller listed", ctrls[1]["address"], "0000:03:00.0")

picked = hardware.select_passthrough_nvme(ctrls, {"0000:02:00.0"})
check("picks the controller that is not the host root", picked["address"], "0000:03:00.0")
check("picked id", picked["id"], "144d:a808")

# Refusing to guess is the whole point: this disk gets wiped.
check_raises(
    "ambiguous when no host disk is known",
    hardware.HardwareError,
    lambda: hardware.select_passthrough_nvme(ctrls, set()),
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

# The template consumes nvme.bus / nvme.slot / nvme.function, so the resolved
# record must be decomposed, not just {address, id}.
resolved = hardware.resolve_passthrough_nvme(LSPCI, {"0000:02:00.0"})
check("resolved address", resolved["address"], "0000:03:00.0")
check("resolved bus", resolved["bus"], "0x03")
check("resolved slot", resolved["slot"], "0x00")
check("resolved function", resolved["function"], "0x0")
check("resolved id", resolved["id"], "144d:a808")
```

- [ ] **Étape 2 : lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_hardware.py`
Expected: `AttributeError: module 'common.hardware' has no attribute 'parse_nvme_controllers'`

- [ ] **Étape 3 : écrire l'implémentation minimale**

Dans `installer/common/hardware.py` :

```python
class HardwareError(RuntimeError):
    """Raised when detection cannot answer safely and must not guess."""


def parse_nvme_controllers(raw: str) -> list[dict]:
    """NVMe controllers (PCI class 0108) from `lspci -nn -D` output.

    lsblk cannot see the passthrough disk: it is bound to vfio-pci and has no
    block device. PCI class is the only view that shows both.
    """
    out = []
    for line in raw.splitlines():
        if "[0108]" not in line:
            continue
        parts = line.split()
        if not parts or parts[0].count(":") != 2:
            continue
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]\s*(?:\(rev|$)", line)
        out.append(
            {
                "address": parts[0],
                "id": f"{m.group(1).lower()}:{m.group(2).lower()}" if m else "",
                "description": _clean_lspci_desc_from_nn(line),
            }
        )
    return sorted(out, key=lambda c: c["address"])


def select_passthrough_nvme(controllers: list[dict],
                            host_addresses: set[str]) -> dict:
    """The one NVMe controller that is not backing the host's own root.

    Refuses to guess: the selected disk is wiped by the Windows installer, so
    an ambiguous answer must be an error, never a best effort.
    """
    if not controllers:
        raise HardwareError("no NVMe controller found (PCI class 0108)")
    candidates = [c for c in controllers if c["address"] not in host_addresses]
    if not candidates:
        raise HardwareError(
            "every NVMe controller backs the host root; none can be passed "
            f"through: {[c['address'] for c in controllers]}"
        )
    if len(candidates) > 1:
        raise HardwareError(
            "cannot decide which NVMe to pass through, several candidates: "
            f"{[c['address'] for c in candidates]}"
        )
    return candidates[0]


def host_root_pci_address() -> Optional[str]:
    """PCI address of the controller behind the host's root filesystem."""
    src = _run(["findmnt", "-no", "SOURCE", "/"])
    if not src:
        return None
    pk = _run(["lsblk", "-no", "PKNAME", src]).splitlines()
    disk = pk[0].strip() if pk else ""
    if not disk:
        return None
    try:
        target = os.path.realpath(f"/sys/block/{disk}/device/device")
    except OSError:
        return None
    tail = os.path.basename(target)
    return tail if tail.count(":") == 2 else None


def resolve_passthrough_nvme(raw: str, host_addresses: set[str]) -> dict:
    """The passthrough NVMe, decomposed the way a <hostdev> address needs.

    Pure, so it is testable on captured lspci text.
    """
    controller = select_passthrough_nvme(parse_nvme_controllers(raw), host_addresses)
    functions = parse_pci_functions(raw, controller["address"])
    if not functions:
        raise HardwareError(f"cannot decompose address {controller['address']}")
    return functions[0]


def passthrough_nvme() -> dict:
    """The NVMe controller to hand to the Windows guest, on this machine."""
    host = host_root_pci_address()
    return resolve_passthrough_nvme(
        _run(["lspci", "-nn", "-D"]), {host} if host else set()
    )
```

Ajouter `import os` en tête du fichier s'il n'y est pas.

⚠️ `lsblk -no PKNAME` sur un volume LVM rend le disque parent (`nvme0n1`), ce
qui est bien ce qu'on veut. Si la racine est sur un empilement que `PKNAME` ne
résout pas, `host_root_pci_address()` rend `None`, et
`select_passthrough_nvme` lève alors sur l'ambiguïté — **échec bruyant, pas
supposition**.

- [ ] **Étape 4 : lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_hardware.py`
Expected: `OK - hardware detection checks passed`

- [ ] **Étape 5 : vérifier sur la machine réelle**

Run: `python3 -c "import sys; sys.path.insert(0,'installer'); from common import hardware; print(hardware.host_root_pci_address()); print(hardware.passthrough_nvme())"`
Expected: `0000:02:00.0` puis le contrôleur `0000:03:00.0` (`144d:a808`).

🔴 Si la sortie désigne `0000:02:00.0`, **arrêter le plan et signaler** : c'est
le disque de l'hôte.

- [ ] **Étape 6 : commit**

```bash
git add installer/common/hardware.py scripts/tests/test_windows_guest_hardware.py
git commit -m "feat(hardware): identify the passthrough NVMe by eliminating the host root"
```

---

### Task 3 : dérivation du plan CPU

**Files:**
- Create: `installer/windows-guest/domain.py`
- Test: `scripts/tests/test_windows_guest_production_domain.py` (créé)

**Interfaces:**
- Consomme : `hardware.cpu_topology()` (existe), dont la clé `performance_cpus`.
- Produit : `vcpu_plan(pool: list[int], reserve: int = 2) -> dict` rendant `{"vcpus": int, "cores": int, "threads": int, "vcpupin": [(vcpu, host_cpu), ...], "emulator_cpuset": str}`. Lève `DomainError`.
- Produit aussi : `class DomainError(RuntimeError)`.

Deux CPU hôtes sont réservés à l'émulateur et aux iothreads : ils portent le
travail vhost et virtiofsd, et les laisser sur un vcpu fait broncher le rythme
d'images. Le reste va à l'invité, par paires de frères SMT.

⚠️ **Hypothèse assumée** : les frères SMT sont adjacents dans l'énumération
(`0,1` puis `2,3`…), ce qui est le cas sur cette machine et sur les Intel en
général. `vcpu_plan` est **pure** et prend la liste, donc un appelant futur
peut lui passer un ordre corrigé sans la modifier.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `scripts/tests/test_windows_guest_production_domain.py` :

```python
#!/usr/bin/env python3
"""Tests for the generated production domain (sub-project C).

Run: python3 scripts/tests/test_windows_guest_production_domain.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
sys.path.insert(0, str(REPO / "installer"))

import domain  # noqa: E402

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


# The i9-12900K's 8 P-cores expose 16 threads, 0..15.
plan = domain.vcpu_plan(list(range(16)))
check("vcpu count", plan["vcpus"], 14)
check("cores", plan["cores"], 7)
check("threads", plan["threads"], 2)
check("emulator cpuset", plan["emulator_cpuset"], "14-15")
check("first pin", plan["vcpupin"][0], (0, 0))
check("last pin", plan["vcpupin"][-1], (13, 13))
check("pin count matches vcpus", len(plan["vcpupin"]), 14)

# An odd remainder must drop a thread rather than break SMT pairing, and the
# CPU it frees goes to the emulator rather than being left idle.
odd = domain.vcpu_plan(list(range(11)))
check("odd pool keeps pairs", odd["vcpus"], 8)
check("odd pool cores", odd["cores"], 4)
check("odd pool emulator takes every leftover", odd["emulator_cpuset"], "8-10")

check_raises("pool too small", domain.DomainError, lambda: domain.vcpu_plan([0, 1, 2]))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - production domain checks passed")
```

- [ ] **Étape 2 : lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `ModuleNotFoundError: No module named 'domain'`

- [ ] **Étape 3 : écrire l'implémentation minimale**

Créer `installer/windows-guest/domain.py` :

```python
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


class DomainError(RuntimeError):
    """Raised when the domain cannot be built safely."""


def vcpu_plan(pool: list[int], reserve: int = 2) -> dict:
    """Split isolated host CPUs between the guest and QEMU's own threads.

    `reserve` host CPUs are kept for the emulator and iothreads: they carry the
    vhost and virtiofsd work, and leaving that on a vcpu makes frame pacing
    jitter. The guest gets whole SMT pairs — an odd remainder drops a thread
    rather than hand Windows a lone sibling, which is the mistake the 14x1
    topology made before 2026-07-17.
    """
    if len(pool) < reserve + 2:
        raise DomainError(
            f"need at least {reserve + 2} isolated CPUs, got {len(pool)}"
        )
    ordered = sorted(pool)
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
```

- [ ] **Étape 4 : lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `OK - production domain checks passed`

- [ ] **Étape 5 : commit**

```bash
git add installer/windows-guest/domain.py scripts/tests/test_windows_guest_production_domain.py
git commit -m "feat(windows-guest): derive the guest vcpu plan from isolated CPUs"
```

---

### Task 4 : gabarit et rendu du XML

**Files:**
- Create: `installer/windows-guest/templates/domain.xml.j2`
- Modify: `installer/windows-guest/domain.py`
- Test: `scripts/tests/test_windows_guest_production_domain.py` (étendu)

**Interfaces:**
- Consomme : `vcpu_plan` (Task 3), `hardware.pci_slot_functions` (Task 1), `hardware.passthrough_nvme` (Task 2).
- Produit : `domain_xml(*, gpu_functions, nvme, plan, memory_kib=16777216, name="Windows", mac=MAC, bridge=BRIDGE, nvram_path=NVRAM_PATH, virtiofs_source=VIRTIOFS_SOURCE, virtiofs_tag=VIRTIOFS_TAG) -> str`, et les constantes `DOMAIN_NAME`, `MAC`, `BRIDGE`, `NVRAM_PATH`, `VIRTIOFS_SOURCE`, `VIRTIOFS_TAG`.

- [ ] **Étape 1 : écrire le test qui échoue**

Ajouter à `scripts/tests/test_windows_guest_production_domain.py`, avant le
bloc final `if failures:` :

```python
import xml.etree.ElementTree as ET  # noqa: E402

GPU = [
    {"address": "0000:01:00.0", "domain": "0x0000", "bus": "0x01",
     "slot": "0x00", "function": "0x0", "id": "10de:2786", "description": "GPU"},
    {"address": "0000:01:00.1", "domain": "0x0000", "bus": "0x01",
     "slot": "0x00", "function": "0x1", "id": "10de:22bc", "description": "audio"},
]
NVME = {"address": "0000:03:00.0", "domain": "0x0000", "bus": "0x03",
        "slot": "0x00", "function": "0x0", "id": "144d:a808", "description": "nvme"}

xml_text = domain.domain_xml(gpu_functions=GPU, nvme=NVME, plan=plan)
root = ET.fromstring(xml_text)

check("domain name", root.findtext("name"), "Windows")
check("kvm domain", root.get("type"), "kvm")
check("vcpu count", root.findtext("vcpu"), "14")

topo = root.find("cpu/topology")
check("cores", topo.get("cores"), "7")
check("threads", topo.get("threads"), "2")

check("emulatorpin", root.find("cputune/emulatorpin").get("cpuset"), "14-15")
check("vcpupin entries", len(root.findall("cputune/vcpupin")), 14)

loader = root.find("os/loader")
check("secure boot loader", loader.text,
      "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd")
check("loader is secure", loader.get("secure"), "yes")
check("nvram template", root.find("os/nvram").get("template"),
      "/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
check("firmware autoselect absent", root.find("os").get("firmware"), None)
check("smm on", root.find("features/smm").get("state"), "on")

check("tpm model", root.find("devices/tpm").get("model"), "tpm-crb")
check("tpm version", root.find("devices/tpm/backend").get("version"), "2.0")

check("mac address", root.find("devices/interface/mac").get("address"),
      "52:54:00:48:e0:3e")
check("nic model", root.find("devices/interface/model").get("type"), "virtio")

hostdevs = root.findall("devices/hostdev")
check("three hostdevs", len(hostdevs), 3)
for hd in hostdevs:
    check("hostdev managed", hd.get("managed"), "yes")
    check("hostdev vfio driver", hd.find("driver").get("name"), "vfio")

check("s4 enabled", root.find("pm/suspend-to-disk").get("enabled"), "yes")
check("s3 disabled", root.find("pm/suspend-to-mem").get("enabled"), "no")

check("hugepages", root.find("memoryBacking/hugepages") is not None, True)
check("locked", root.find("memoryBacking/locked") is not None, True)
check("shared access", root.find("memoryBacking/access").get("mode"), "shared")

check("emulated video present", root.find("devices/video/model").get("type"), "vga")
check("vnc listens locally", root.find("devices/graphics").get("listen"), "127.0.0.1")

check("virtiofs target", root.find("devices/filesystem/target").get("dir"), "Data")

# Everything the spec forbids must be absent, checked individually so a
# failure names the offender.
check("no kvm hidden", root.find("features/kvm") is None, True)
check("no vendor_id", root.find("features/hyperv/vendor_id") is None, True)
check("no sysinfo", root.find("sysinfo") is None, True)
check("no smbios mode", root.find("os/smbios") is None, True)
check("no vBIOS override", root.find("devices/hostdev/rom") is None, True)
check("no i6300esb watchdog",
      [w.get("model") for w in root.findall("devices/watchdog")], ["itco"])
```

- [ ] **Étape 2 : lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `AttributeError: module 'domain' has no attribute 'domain_xml'`

- [ ] **Étape 3 : écrire le gabarit**

Créer `installer/windows-guest/templates/domain.xml.j2` :

```xml
<domain type='kvm'>
  <name>{{ name }}</name>
  <memory unit='KiB'>{{ memory_kib }}</memory>
  <currentMemory unit='KiB'>{{ memory_kib }}</currentMemory>
  <memoryBacking>
    <hugepages/>
    <locked/>
    <access mode='shared'/>
  </memoryBacking>
  <vcpu placement='static'>{{ plan.vcpus }}</vcpu>
  <iothreads>2</iothreads>
  <cputune>
{%- for vcpu, host_cpu in plan.vcpupin %}
    <vcpupin vcpu='{{ vcpu }}' cpuset='{{ host_cpu }}'/>
{%- endfor %}
    <emulatorpin cpuset='{{ plan.emulator_cpuset }}'/>
    <iothreadpin iothread='1' cpuset='{{ plan.emulator_cpuset }}'/>
    <iothreadpin iothread='2' cpuset='{{ plan.emulator_cpuset }}'/>
  </cputune>
  <os>
    <type arch='x86_64' machine='pc-q35-9.2'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='raw'>/usr/share/OVMF/OVMF_CODE_4M.secboot.fd</loader>
    <nvram template='/usr/share/OVMF/OVMF_VARS_4M.ms.fd' templateFormat='raw' format='raw'>{{ nvram_path }}</nvram>
  </os>
  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vpindex state='on'/>
      <runtime state='on'/>
      <synic state='on'/>
      <stimer state='on'><direct state='on'/></stimer>
      <reset state='on'/>
      <frequencies state='on'/>
      <tlbflush state='on'/>
      <ipi state='on'/>
      <evmcs state='on'/>
    </hyperv>
    <vmport state='off'/>
    <smm state='on'/>
    <ioapic driver='kvm'/>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='off'>
    <topology sockets='1' dies='1' clusters='1' cores='{{ plan.cores }}' threads='{{ plan.threads }}'/>
    <cache mode='passthrough'/>
    <maxphysaddr mode='emulate' bits='39'/>
    <feature policy='require' name='topoext'/>
    <feature policy='require' name='hypervisor'/>
    <feature policy='require' name='invtsc'/>
    <feature policy='disable' name='split-lock-detect'/>
  </cpu>
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='kvmclock' present='no'/>
    <timer name='hypervclock' present='yes'/>
    <timer name='tsc' present='yes' mode='native'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='yes'/>
  </pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <controller type='sata' index='0'/>
    <filesystem type='mount' accessmode='passthrough'>
      <driver type='virtiofs' queue='1024'/>
      <binary path='/usr/libexec/virtiofsd'/>
      <source dir='{{ virtiofs_source }}'/>
      <target dir='{{ virtiofs_tag }}'/>
    </filesystem>
    <interface type='bridge'>
      <mac address='{{ mac }}'/>
      <source bridge='{{ bridge }}'/>
      <model type='virtio'/>
      <driver name='vhost' queues='8'/>
      <link state='up'/>
    </interface>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <audio id='1' type='none'/>
    <graphics type='vnc' port='-1' listen='127.0.0.1'/>
    <video>
      <model type='vga' vram='16384' heads='1'/>
    </video>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <driver name='vfio'/>
      <source>
        <address domain='{{ nvme.domain }}' bus='{{ nvme.bus }}' slot='{{ nvme.slot }}' function='{{ nvme.function }}'/>
      </source>
      <boot order='1'/>
    </hostdev>
{%- for fn in gpu_functions %}
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <driver name='vfio'/>
      <source>
        <address domain='{{ fn.domain }}' bus='{{ fn.bus }}' slot='{{ fn.slot }}' function='{{ fn.function }}'/>
      </source>
    </hostdev>
{%- endfor %}
    <watchdog model='itco' action='reset'/>
    <memballoon model='none'/>
  </devices>
</domain>
```

⚠️ `<on_crash>destroy</on_crash>` remplace le `coredump-restart` du domaine
actuel : un vidage mémoire de 16 Gio à chaque plantage remplirait `/`, déjà
surveillé à 85 %.

- [ ] **Étape 4 : écrire le rendu**

Ajouter à `installer/windows-guest/domain.py` (imports en tête) :

```python
import argparse
import os
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"

DOMAIN_NAME = "Windows"
# dhcp-host pins this MAC to 192.168.3.2 in
# /etc/NetworkManager/dnsmasq-shared.d/domain.conf. Changing it silently breaks
# game.allanic.me, the firewalld stream forwards and wake-on-demand.
MAC = "52:54:00:48:e0:3e"
BRIDGE = "internalBridge"
NVRAM_PATH = "/var/lib/libvirt/qemu/nvram/Windows_VARS.fd"
VIRTIOFS_SOURCE = "/media/data"
VIRTIOFS_TAG = "Data"
MEMORY_KIB = 16777216


def domain_xml(*, gpu_functions: list[dict], nvme: dict, plan: dict,
               memory_kib: int = MEMORY_KIB, name: str = DOMAIN_NAME,
               mac: str = MAC, bridge: str = BRIDGE,
               nvram_path: str = NVRAM_PATH,
               virtiofs_source: str = VIRTIOFS_SOURCE,
               virtiofs_tag: str = VIRTIOFS_TAG) -> str:
    """Render the production domain XML."""
    if len(gpu_functions) < 1:
        raise DomainError("no GPU function to pass through")
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["xml"]),
        keep_trailing_newline=True,
    )
    return env.get_template("domain.xml.j2").render(
        name=name, memory_kib=memory_kib, plan=plan, mac=mac, bridge=bridge,
        nvram_path=nvram_path, gpu_functions=gpu_functions, nvme=nvme,
        virtiofs_source=virtiofs_source, virtiofs_tag=virtiofs_tag,
    )
```

- [ ] **Étape 5 : lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `OK - production domain checks passed`

- [ ] **Étape 6 : vérifier que libvirt accepte le XML**

```bash
python3 -c "
import sys; sys.path.insert(0,'installer/windows-guest'); sys.path.insert(0,'installer')
import domain
from common import hardware
plan = domain.vcpu_plan(hardware.cpu_topology()['performance_cpus'])
print(domain.domain_xml(gpu_functions=hardware.pci_slot_functions('01:00.0'),
                        nvme=hardware.passthrough_nvme(), plan=plan))
" > /tmp/nivuus-domain.xml
virt-xml-validate /tmp/nivuus-domain.xml domain
```
Expected: `/tmp/nivuus-domain.xml validates`

⚠️ Ne **pas** définir le domaine ici : `Windows` désigne encore la VM de
production. La définition arrive en Task 5, derrière un garde-fou.

- [ ] **Étape 7 : commit**

```bash
git add installer/windows-guest/domain.py installer/windows-guest/templates/domain.xml.j2 scripts/tests/test_windows_guest_production_domain.py
git commit -m "feat(windows-guest): render the production domain from detected hardware"
```

---

### Task 5 : interface en ligne de commande

**Files:**
- Modify: `installer/windows-guest/domain.py`
- Test: `scripts/tests/test_windows_guest_production_domain.py` (étendu)

**Interfaces:**
- Consomme : `domain_xml` (Task 4), les détections (Tasks 1-2).
- Produit : `build_domain_xml() -> str` (détection + rendu, sur cette machine), `domain_exists(name) -> bool`, et un `main()` exposant `xml` et `define [--replace]`.

🔴 **`define` refuse d'écraser un domaine existant sans `--replace`.** Tant que
la bascule n'a pas eu lieu, `Windows` est la VM de production du propriétaire,
avec sa session hibernée. Un `define` silencieux la remplacerait.

- [ ] **Étape 1 : écrire le test qui échoue**

Ajouter à `scripts/tests/test_windows_guest_production_domain.py` :

```python
# `define` must refuse an existing domain unless explicitly told to replace it:
# until the cutover, "Windows" is the owner's production VM.
check_raises(
    "define refuses an existing domain",
    domain.DomainError,
    lambda: domain.guard_replace(exists=True, replace=False),
)
check("define proceeds when replacing", domain.guard_replace(exists=True, replace=True), None)
check("define proceeds when absent", domain.guard_replace(exists=False, replace=False), None)

# vm-cpu-partition.sh derives the HOST cpuset from cputune, reading the domain
# XML libvirt feeds it on stdin. It parses cputune/{vcpupin,emulatorpin,
# iothreadpin}@cpuset with ElementTree. Replicate that parse here: if the
# generated XML ever stops matching it, the CPU partitioning breaks with no
# error message at all — the hook exits 0 by design and only
# /var/log/libvirt-cpu-hook.log would show it.
pinned = set()
for tune in root.findall("cputune"):
    for tag in ("vcpupin", "emulatorpin", "iothreadpin"):
        for el in tune.findall(tag):
            for part in el.get("cpuset", "").split(","):
                if "-" in part:
                    lo, hi = part.split("-")
                    pinned |= set(range(int(lo), int(hi) + 1))
                elif part:
                    pinned.add(int(part))

check("hook sees every pinned CPU", pinned, set(range(16)))
```

- [ ] **Étape 2 : lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `AttributeError: module 'domain' has no attribute 'guard_replace'`

- [ ] **Étape 3 : écrire l'implémentation minimale**

Ajouter à `installer/windows-guest/domain.py` :

```python
def guard_replace(*, exists: bool, replace: bool) -> None:
    """Refuse to redefine an existing domain unless asked explicitly.

    Until the cutover, "Windows" is the production VM, possibly hibernated with
    a live session. Silently redefining it would discard that.
    """
    if exists and not replace:
        raise DomainError(
            f"domain {DOMAIN_NAME!r} already exists; pass --replace to redefine it"
        )


def _virsh(*args: str) -> subprocess.CompletedProcess:
    # virsh output is localized; LC_ALL=C keeps state strings parseable.
    return subprocess.run(["virsh", *args], text=True, capture_output=True,
                          env={**os.environ, "LC_ALL": "C"})


def domain_exists(name: str = DOMAIN_NAME) -> bool:
    return _virsh("dominfo", name).returncode == 0


def build_domain_xml() -> str:
    """Detect this machine's hardware and render its domain."""
    sys.path.insert(0, str(HERE.parent))
    from common import hardware  # noqa: PLC0415

    gpus = [g for g in hardware.list_gpus() if g["discrete"]]
    if len(gpus) != 1:
        raise DomainError(
            f"expected exactly one discrete GPU, found {[g['slot'] for g in gpus]}"
        )
    topology = hardware.cpu_topology()
    pool = topology["performance_cpus"] or list(range(1, topology["total_cpus"]))
    return domain_xml(
        gpu_functions=hardware.pci_slot_functions(gpus[0]["slot"]),
        nvme=hardware.passthrough_nvme(),
        plan=vcpu_plan(pool),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Production Windows guest domain")
    parser.add_argument("action", choices=["xml", "define"])
    parser.add_argument("--replace", action="store_true",
                        help="redefine the domain even if it already exists")
    args = parser.parse_args()

    # Imported here, not at module scope: the tests put only
    # installer/windows-guest on sys.path, and a top-level import of
    # common.hardware would break them.
    sys.path.insert(0, str(HERE.parent))
    from common.hardware import HardwareError  # noqa: PLC0415

    try:
        xml_text = build_domain_xml()
        if args.action == "xml":
            print(xml_text)
            return 0
        guard_replace(exists=domain_exists(), replace=args.replace)
        path = Path("/run") / "nivuus-windows-domain.xml"
        path.write_text(xml_text)
        proc = _virsh("define", str(path))
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    except (DomainError, HardwareError) as exc:
        # Detection and build failures are operator-facing, not bugs: report
        # them plainly. Anything else keeps its traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

⚠️ **Ne pas élargir ce `except` à `Exception`.** Seules les deux erreurs de
domaine sont attendues ; tout le reste doit remonter avec sa trace.

- [ ] **Étape 4 : lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_production_domain.py`
Expected: `OK - production domain checks passed`

- [ ] **Étape 5 : vérifier le refus sur la machine réelle**

Run: `python3 installer/windows-guest/domain.py define`
Expected: `error: domain 'Windows' already exists; pass --replace to redefine it`, code de retour 1.

🔴 **Ne pas passer `--replace`** : la VM de production est hibernée.

- [ ] **Étape 6 : lancer toutes les suites du sous-projet**

```bash
for t in scripts/tests/test_windows_guest_*.py; do echo "== $t"; python3 "$t" || exit 1; done
```
Expected: chaque suite affiche sa ligne `OK`, sortie sans avertissement.

- [ ] **Étape 7 : commit**

```bash
git add installer/windows-guest/domain.py scripts/tests/test_windows_guest_production_domain.py
git commit -m "feat(windows-guest): CLI to render and define the production domain"
```

---

### Task 6 : recette d'acceptation S4

**Files:**
- Create: `installer/windows-guest/winrm_exec.py`
- Create: `docs/superpowers/plans/recette-s4.md`

**Interfaces:**
- Consomme : `domain.py` (Tasks 4-5), `testdomain.py` (existant).
- Produit : `winrm_exec.py`, client WinRM en ligne de commande, et une procédure écrite exécutée à la main.

⚠️ **`/usr/local/bin/winrm` ne convient pas** : ce client Go ne parle que
Basic, alors que l'invité n'active que Negotiate — mesuré le 2026-08-22, il
renvoie 401. D'où un petit client `pywinrm`/NTLM versionné dans le dépôt, dont
la recette et le sous-projet B se serviront tous les deux.

C ne vaut que si **l'hibernation S4 fonctionne sous Secure Boot**. La spec de A
annonçait ce risque levé ; il ne l'est pas — le domaine de test n'avait aucun
bloc `<pm>`. Toute l'économie d'énergie de la machine en dépend :
`vm-idle-shutdown.timer` hiberne au bout de dix minutes, le réveil par socket
reprend.

Cette recette est **manuelle par nature** : elle exige un invité Windows
installé, le GPU réel, et une hibernation observée. Aucun test automatisé ne
peut s'y substituer.

- [ ] **Étape 1 : écrire le client WinRM**

Créer `installer/windows-guest/winrm_exec.py` :

```python
#!/usr/bin/env python3
"""Run one command in the Windows guest over WinRM.

/usr/local/bin/winrm speaks Basic only, which the guest does not enable:
Enable-PSRemoting offers Negotiate. pywinrm with the ntlm transport negotiates
correctly (measured 2026-08-22, Basic returned 401).

The password is read from a file, never from argv, so it cannot leak into the
process table or shell history.

Usage: winrm_exec.py {cmd|ps} <command...>
Env:   GUEST_IP (default 192.168.3.2), GUEST_USER (default Administrator),
       GUEST_PASS_FILE (default /root/.config/nivuus/windows-admin.pass)
"""
import os
import sys

import winrm

IP = os.environ.get("GUEST_IP", "192.168.3.2")
USER = os.environ.get("GUEST_USER", "Administrator")
PASS_FILE = os.environ.get(
    "GUEST_PASS_FILE", "/root/.config/nivuus/windows-admin.pass"
)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("cmd", "ps"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(PASS_FILE) as fh:
            password = fh.read().strip()
    except FileNotFoundError:
        print(f"error: password file not found: {PASS_FILE}", file=sys.stderr)
        return 1
    session = winrm.Session(
        f"http://{IP}:5985/wsman",
        auth=(USER, password),
        transport="ntlm",
        server_cert_validation="ignore",
    )
    command = " ".join(sys.argv[2:])
    try:
        result = (session.run_cmd if sys.argv[1] == "cmd" else session.run_ps)(command)
    except Exception as e:
        print(f"error: cannot reach guest at {IP}:5985: {e}", file=sys.stderr)
        return 1
    out = result.std_out.decode("utf-8", "replace").strip()
    err = result.std_err.decode("utf-8", "replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]", err, file=sys.stderr)
    return result.status_code


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Étape 2 : vérifier qu'il refuse un usage incorrect**

Run: `python3 installer/windows-guest/winrm_exec.py; echo "rc=$?"`
Expected: la docstring sur stderr, `rc=2`. Aucun accès réseau, aucun mot de
passe lu.

- [ ] **Étape 3 : écrire la recette**

Créer `docs/superpowers/plans/recette-s4.md` :

````markdown
# Recette S4 — hibernation sous Secure Boot (sous-projet C)

**Ce qu'on cherche à savoir** : un invité Windows 11 démarré en Secure Boot
avec vTPM et GPU passé sait-il hiberner et reprendre, session intacte ?

**Pourquoi c'est bloquant** : `vm-idle-shutdown.timer` hiberne la VM après dix
minutes d'inactivité et le réveil par socket la reprend. Sans S4, la VM reste
allumée en permanence et tout le travail d'économie d'énergie tombe.

⚠️ **Sur disque jetable uniquement.** Le NVMe de production n'est pas touché.

## Préparation

```bash
# 0. Arrêter le domaine de production (le temps de la recette, la VM gaming est hors ligne — environ quatre-vingt-dix minutes)
virsh shutdown --mode acpi Windows
for i in $(seq 1 60); do
  [ "$(LC_ALL=C virsh domstate Windows)" = "shut off" ] && break
  sleep 1
done
```

```bash
# 1. Libérer le GPU (aucun crochet ne le fait pour un domaine jetable)
docker stop mediamanager-tdarr-node-nvenc-1 mediamanager-tdarr-node-1 \
            mediamanager-tdarr-1 nivuus-ollama
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StopUnit string:"nvidia-persistenced.service" string:"replace"

# 2. Installer un invité jetable (sous-projet A)
cd installer/windows-guest
sudo python3 testdomain.py define \
  --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
  --unattend-iso /media/data/iso/nivuus-unattend.iso
virsh start Windows-LTSC-test
```

⚠️ Le média Windows attend une frappe (« Press any key to boot from CD »).
Sans elle : `No bootable option or device was found`.

```bash
for i in $(seq 1 40); do
  virsh send-key Windows-LTSC-test --codeset linux KEY_ENTER >/dev/null 2>&1
  sleep 1
done
```

Attendre que le provisionnement finisse. Le port WinRM 5985 s'ouvre à la fin et
c'est le signal conçu (voir `testdomain.py wait_ready()`). Il existe une étroite
course dans `00-bootstrap.ps1` : entre l'activation de la communication à distance
et la désactivation de la règle pare-feu, le port peut être brièvement accessible
avant que le provisionnement soit vraiment fini. `wait-ready` sert à la fois à
attendre et à dériver l'adresse IP, et sera relancé après le redémarrage pour <pm>.

(Note : la course elle-même est un défaut du script de bootstrap du sous-projet A,
fermée en désactivant la règle *avant* d'activer PSRemoting. C'est un sujet de
suivi en dehors de ce périmètre.)

## Ajouter le bloc `<pm>` au domaine jetable

Le domaine de test de A n'en a pas. Le lui ajouter, sinon la recette ne mesure
rien :

```bash
virsh dumpxml Windows-LTSC-test > /tmp/s4-test.xml
# insérer avant </domain> :
#   <pm><suspend-to-mem enabled='no'/><suspend-to-disk enabled='yes'/></pm>
virsh destroy Windows-LTSC-test
virsh define /tmp/s4-test.xml
virsh start Windows-LTSC-test

# Redériver l'adresse IP après le redémarrage (DHCP peut avoir changé)
export GUEST_IP=$(cd installer/windows-guest && python3 testdomain.py wait-ready)
```

## La mesure

```bash
# Refusal guard: prevent targeting the production VM by mistake
: "${GUEST_IP:?GUEST_IP n'est pas défini — relancer l'étape wait-ready}"
if [ "$GUEST_IP" = "192.168.3.2" ]; then
    echo "REFUS : GUEST_IP pointe la VM de production, pas le domaine jetable" >&2
    exit 1
fi

# 1. Activer l'hibernation dans l'invité et poser un témoin de session
python3 installer/windows-guest/winrm_exec.py ps "powercfg /hibernate on"
python3 installer/windows-guest/winrm_exec.py ps "Set-Content C:\\temoin-s4.txt (Get-Date -Format o)"
python3 installer/windows-guest/winrm_exec.py ps "Start-Process notepad"    # une fenêtre ouverte = témoin visible

# 2. Hiberner
python3 installer/windows-guest/winrm_exec.py cmd "shutdown /h /f"

# 3. Constater l'arrêt (l'appel WinRM expire pendant l'endormissement :
#    son code de retour ne veut rien dire, c'est domstate qui compte)
for i in $(seq 1 30); do
  [ "$(LC_ALL=C virsh domstate Windows-LTSC-test)" = "shut off" ] && break
  sleep 5
done
LC_ALL=C virsh domstate Windows-LTSC-test

# 4. Reprendre
virsh start Windows-LTSC-test
```

## Critères

| Ce qu'on vérifie | Attendu |
| --- | --- |
| Le domaine passe à l'arrêt après `shutdown /h /f` | `shut off` en moins de 60 s |
| La reprise restitue la session | notepad toujours ouvert |
| Le GPU est réinitialisé | `nvidia-smi` répond dans l'invité |
| Secure Boot actif | `Confirm-SecureBootUEFI` → `True` |
| vTPM présent | `Get-Tpm` → `TpmPresent: True`, `TpmReady: True` |

🔴 **Si le domaine ne passe pas à l'arrêt** : la sélection automatique de
firmware est le premier suspect — vérifier que `<loader>` et `<nvram>` sont
explicites. Les descripteurs OVMF de Debian ne déclarent que `acpi-s3`, et
libvirt refuse alors S4 alors qu'OVMF le gère.

## Démontage

```bash
virsh destroy Windows-LTSC-test
cd installer/windows-guest && sudo python3 testdomain.py teardown
# Rendre le GPU à l'hôte
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StartUnit string:"nvidia-persistenced.service" string:"replace"
nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
docker start nivuus-ollama mediamanager-tdarr-1 \
             mediamanager-tdarr-node-1 mediamanager-tdarr-node-nvenc-1
```

⚠️ La régénération CDI n'est pas facultative : `nvidia_uvm` reçoit un majeur
**dynamique**, et une spécification figée fait renvoyer 999 à tout CUDA
pendant que `nvidia-smi` continue de fonctionner.
````

- [ ] **Étape 2 : exécuter la recette**

Suivre `docs/superpowers/plans/recette-s4.md` de bout en bout et consigner le
résultat de chaque critère.

- [ ] **Étape 3 : consigner le verdict**

Ajouter le résultat mesuré à la spec de C, section « Test d'acceptation », en
remplaçant la mention « jamais éprouvée à ce jour » par la mesure — date,
commandes, sortie.

Si S4 **échoue** : ne pas poursuivre vers B. Consigner l'échec, la
configuration exacte, et rouvrir la conception — la bascule dépend de ce point.

- [ ] **Étape 4 : commit**

```bash
git add docs/superpowers/plans/recette-s4.md docs/superpowers/specs/2026-08-22-windows-guest-domaine-design.md
git commit -m "test(windows-guest): S4-under-Secure-Boot acceptance runbook and verdict"
```

---

## Ce que ce plan ne fait pas

Il ne **définit pas** le domaine de production : `define` refuse tant que
`--replace` n'est pas passé, et la bascule seule y est autorisée. Il ne touche
ni au NVMe, ni à la VM en cours, ni à Pomerium, ni à `winvm`.

Le provisionnement de l'invité est le sous-projet B ; la bascule a sa propre
spec.
