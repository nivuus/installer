# Invité Windows LTSC — sous-projet B : provisionnement — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** amener l'invité LTSC 26100 installé par le sous-projet A à l'état d'appliance de cloud gaming — pilote NVIDIA, écran virtuel SudoVDA, Apollo configuré avec HDR, Steam sur une partition persistante, agent Guacamole en session 1, énergie et politique de mise à jour — entièrement hors-ligne.

**Architecture:** B prolonge le mécanisme éprouvé de A : des scripts `provision/NN-*.ps1` enchaînés par `run-all.ps1` avec jeton de redémarrage, alimentés par une charge utile embarquée sur l'ISO secondaire. Le disque est scindé en **C: jetable** et **D: persistant** ; Steam est *installé* sur D: et la configuration d'Apollo y est jonctionnée, si bien qu'une reconstruction de C: préserve les jeux, la session Steam et les clients appairés. Tout le rendu (fichier de réponses, `sunshine.conf`, `apps.json`) reste en Python pur, testable sans Windows.

**Tech Stack:** Python 3.11 + Jinja2 (rendu, côté hôte), PowerShell 5.1 (étapes dans l'invité), NSIS (`/S`, `/D=`), `xorriso` (ISO), tests Python autonomes sans pytest.

**Spec:** [`docs/superpowers/specs/2026-08-22-windows-guest-provisionnement-design.md`](../specs/2026-08-22-windows-guest-provisionnement-design.md)

**Dépend de :** sous-projet C, fusionné dans `main` (`0dd1500`). Le domaine libvirt et sa détection matérielle existent.

---

## ⚠️ À lire avant la première étape

🔴 **Ce plan ne touche JAMAIS la VM `Windows` de production.** Elle est en
hibernation avec une session vivante. Aucune tâche n'exécute `virsh define`,
`virsh undefine`, `virsh start`, `virsh shutdown`, ni `domain.py define`.
Toute la vérification se fait par tests statiques et par rendu ; la mise en
œuvre réelle appartient à la recette d'acceptation (tâche 10), qui exige une
fenêtre du propriétaire.

🔴 **Le partitionnement conditionnel est un piège, et c'est le risque nº 1 de
ce sous-projet.** Le fichier de réponses a deux modes exclusifs : `wipe`
(efface le disque entier, crée C: et D:) et `rebuild` (reformate C:
uniquement, ne touche pas à D:). Un mode `rebuild` lancé contre un disque qui
n'a pas été installé par `wipe` reformaterait une partition arbitraire. La
parade est un marqueur : `wipe` sème `D:\state\NIVUUS-DATA.id`, et l'étape
`20-disk.ps1` **refuse de continuer** si ce marqueur manque en mode
`rebuild`.

🔴 **Aucun secret n'entre dans le dépôt ni dans une ligne de commande de
l'hôte.** Le mot de passe administrateur, la clé produit et — nouveau en B —
le mot de passe de l'IHM Apollo sont lus depuis des fichiers en mode 600 sous
`/root/.config/nivuus/`. `build.py` les lit ; ils ne passent jamais par
`argv`.

## Global Constraints

- **Hors-ligne dans l'invité, réseau autorisé à la fabrication.** Aucune étape
  `provision/*.ps1` ne télécharge quoi que ce soit. Une charge utile
  incomplète fait échouer la *construction*, jamais l'installation.
- **Chaque étape est idempotente vis-à-vis de D:.** On sème si absent, on ne
  réécrit jamais l'état utilisateur. Exception réglée en tâche 3 :
  `sunshine.conf` et `apps.json` sont des artefacts générés, donc toujours
  réécrits ; `sunshine_state.json`, `credentials/` et la session Steam ne sont
  jamais touchés.
- **`PROVISION_VERSION = "B1"`**, dans `payload.py` **et** dans le marqueur
  écrit par `99-marker.ps1`. Une dérive silencieuse ferait accepter à l'hôte
  un invité provisionné par une charge utile périmée.
- **Le port 5985 n'est pas un signal de fin.** Seul
  `C:\nivuus\state\PROVISION.done` l'est. `99-marker.ps1` ouvre 5985 en
  dernier geste, après toutes ses vérifications.
- **Aucune lettre de lecteur codée en dur pour la charge utile.** Elle est
  toujours découverte par le balayage du marqueur `\nivuus\PAYLOAD.id`. Les
  lettres `C:` et `D:` sont, elles, des cibles fixes et légitimes.
- **L'ouverture de session automatique reste active en permanence** — l'agent
  et Apollo en dépendent tous deux. C'est l'inverse de ce que faisait A.
- **Compte administrateur : `Administrator`** (média en-US). Pas
  `Administrateur`.
- **200 lignes maximum par fichier**, commentaires en anglais, tests Python
  autonomes lancés directement (pas de pytest), dans `scripts/tests/`.
- **Domaine `Windows`, MAC `52:54:00:48:e0:3e`, IP `192.168.3.2`** — invariants
  posés par C, jamais renégociés ici.

## Décisions prises en écrivant ce plan

Chacune s'écarte de la lettre de la spec ; chacune est justifiée par une
mesure faite sur l'installeur Apollo 0.4.6 réel.

| Décision | Pourquoi | Coût si elle est fausse |
| --- | --- | --- |
| **`20-sudovda.ps1` est supprimé**, et le répertoire `sudovda/` sort de la charge utile | l'installeur Apollo embarque `drivers/sudovda` à l'identique, avec un `install.bat` qui pose lui-même le certificat dans `Root` et `TrustedPublisher` puis recrée le nœud de périphérique — donc idempotent. Deux installations du même IDD sont un risque inutile | l'étape 25 échoue bruyamment sur la vérification du périphérique ; on restaure l'étape autonome |
| **Les identifiants de l'IHM Apollo sont posés par `sunshine.exe --creds`** | l'option existe (`--creds username password | set user credentials for the Web manager`) ; dériver nous-mêmes le hachage aurait été une invention non vérifiable | rien : l'échec est immédiat et lisible |
| **Apollo s'installe à son emplacement par défaut** (`/D=` omis) | `/D=` NSIS veut un chemin non quoté en dernier argument ; `C:\Program Files\Apollo` contient une espace et PowerShell le quoterait, cassant l'analyse NSIS | l'installeur choisit un autre chemin ; l'étape 25 le lit dans le registre plutôt que de le supposer |
| **La vérification « session 1 » ne passe plus par `check-session.sh`** | ce script exige `/media/vm`, le montage CIFS que la bascule **supprime** ; il lance aussi `C:\dev\target\debug\agent.exe`, un chemin de développement qui n'existe pas sur l'appliance | aucun : la preuve est produite dans l'invité et vérifiée par `99-marker.ps1` |
| **La tâche planifiée de l'agent n'a pas de mot de passe** | `Register-ScheduledTask` avec `LogonType Interactive` + déclencheur `AtLogOn` s'exécute dans la session de l'utilisateur connecté sans identifiants stockés — strictement mieux que `schtasks /rp` | la tâche ne démarre pas ; visible dans `99-marker.ps1` |
| **Noms de fichiers en anglais** (`20-disk`, `50-power`, `55-updates`) | convention du dépôt et de A ; la spec les nomme en français dans sa prose | aucun |

---

### Task 1: Charge utile — artefacts de B

**Files:**
- Modify: `installer/windows-guest/payload.py`
- Create: `installer/windows-guest/fetch_payload.py`
- Test: `scripts/tests/test_windows_guest_payload.py` (étendre)

**Interfaces:**
- Consumes: rien (première tâche).
- Produces: `payload.PROVISION_VERSION == "B1"` ; `payload.missing_binaries(drivers_dir) -> list[str]` avec les exigences de B ; `payload.PayloadSources` gagne un champ `config_dir: Path | None` (répertoire de configuration rendu, injecté par `build.py` en tâche 9) ; `fetch_payload.plan_downloads(drivers_dir) -> list[Download]`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `scripts/tests/test_windows_guest_payload.py`, avant le bloc
de sortie :

```python
# --- Sous-projet B : la charge utile déclare ses artefacts en un seul endroit.
check("provision version is B1", payload.PROVISION_VERSION, "B1")

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    missing = payload.missing_binaries(root)
    joined = "\n".join(missing)
    for needle in ["nvidia", "apollo", "steam", "virtio", "winfsp", "agent"]:
        check(f"empty payload reports {needle} missing", needle in joined, True)
    # SudoVDA rides inside the Apollo installer; requiring it separately would
    # install the same IDD twice.
    check("sudovda is not required separately", "sudovda" in joined.lower(), False)

def _make_complete_payload(root: pathlib.Path) -> None:
    (root / "nvidia").mkdir(parents=True)
    (root / "nvidia" / "610.88.exe").write_text("x")
    (root / "apollo").mkdir()
    (root / "apollo" / "Apollo-0.4.6.exe").write_text("x")
    (root / "steam").mkdir()
    (root / "steam" / "SteamSetup.exe").write_text("x")
    (root / "virtio" / "netkvm").mkdir(parents=True)
    (root / "virtio" / "netkvm" / "netkvm.inf").write_text("x")
    (root / "virtio" / "viofs").mkdir(parents=True)
    (root / "virtio" / "viofs" / "viofs.inf").write_text("x")
    (root / "winfsp").mkdir()
    (root / "winfsp" / "winfsp-2.0.msi").write_text("x")
    (root / "agent").mkdir()
    (root / "agent" / "agent.exe").write_text("x")

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    check("complete payload reports nothing missing",
          payload.missing_binaries(root), [])

# viofs is a comfort, not a requirement: the guest streams fine without the
# /media/data share, so a missing viofs must NOT fail the build.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    shutil.rmtree(root / "virtio" / "viofs")
    check("missing viofs does not fail the build",
          payload.missing_binaries(root), [])

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    _make_complete_payload(root)
    shutil.rmtree(root / "virtio" / "netkvm")
    check("missing netkvm fails the build",
          any("netkvm" in m for m in payload.missing_binaries(root)), True)

import fetch_payload  # noqa: E402
plans = fetch_payload.plan_downloads(pathlib.Path("/tmp/x"))
check("every download has a url and a destination",
      all(d.url.startswith("https://") and d.dest for d in plans), True)
check("no download lands outside the drivers dir",
      all(str(d.dest).startswith("/tmp/x") for d in plans), True)
names = [d.name for d in plans]
check("downloads are uniquely named", len(names), len(set(names)))
```

Ajouter les imports manquants en tête du fichier (`import shutil`,
`import tempfile`, `import pathlib` s'ils n'y sont pas déjà).

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python3 scripts/tests/test_windows_guest_payload.py`
Expected: FAIL — `provision version is B1: got 'A1', want 'B1'`, puis
`ModuleNotFoundError: No module named 'fetch_payload'`.

- [ ] **Step 3: Étendre `payload.py`**

Remplacer `PROVISION_VERSION = "A1"` par `PROVISION_VERSION = "B1"`.

Remplacer entièrement `missing_binaries` par :

```python
# Each entry: (subdirectory, glob, human description). viofs and WinFsp are
# deliberately absent: virtiofs is a comfort mount, and a guest without it
# still streams. NetKVM is not - without it the guest has no network at all,
# so no agent, no wake-on-demand, no 192.168.3.2.
REQUIRED_BINARIES = [
    ("nvidia", "*.exe", "NVIDIA display driver installer"),
    ("apollo", "*.exe", "Apollo installer (it also carries SudoVDA)"),
    ("steam", "SteamSetup.exe", "Steam installer"),
    ("virtio/netkvm", "*.inf", "NetKVM virtio-net driver"),
    ("winfsp", "*.msi", "WinFsp installer (virtiofs depends on it)"),
    ("agent", "agent.exe", "Guacamole agent, extracted before the wipe"),
]


def missing_binaries(drivers_dir: Path) -> list[str]:
    """Return a human-readable list of the offline binaries not provided."""
    missing = []
    for subdir, pattern, what in REQUIRED_BINARIES:
        where = drivers_dir.joinpath(*subdir.split("/"))
        if not list(where.glob(pattern)):
            missing.append(f"{what} ({pattern}) in {where}")
    return missing
```

Retirer du message d'erreur de `stage_payload` la branche
`if any("SudoVDA" in item ...)` et la remplacer par :

```python
        if any("agent.exe" in item for item in missing):
            error_msg += (
                "\n\nagent.exe must be extracted from the current Windows VM "
                "BEFORE it is wiped - no machine can rebuild it afterwards."
            )
```

Étendre `PayloadSources` d'un champ optionnel et `plan_payload` pour le
transporter :

```python
@dataclass(frozen=True)
class PayloadSources:
    provision_dir: Path
    probe_dir: Path
    drivers_dir: Path
    # Rendered Apollo configuration and the secrets the guest needs. Built at
    # build time into a temporary directory, never checked into the repo.
    config_dir: Path | None = None
```

```python
def plan_payload(sources: PayloadSources) -> list[tuple[Path, str]]:
    """Map each source file to its destination path relative to /nivuus."""
    entries = (_walk(sources.provision_dir, "provision")
               + _walk(sources.probe_dir, "probe")
               + _walk(sources.drivers_dir, "drivers"))
    if sources.config_dir is not None:
        entries += _walk(sources.config_dir, "config")
    return entries
```

Dans `stage_payload`, ajouter `sources.config_dir` à l'ensemble `src_paths`
lorsqu'il n'est pas `None` (le garde-fou « dest_root ne peut pas être une
source » doit le couvrir aussi) :

```python
    src_paths = {sources.provision_dir.resolve(), sources.probe_dir.resolve(),
                 sources.drivers_dir.resolve()}
    if sources.config_dir is not None:
        src_paths.add(sources.config_dir.resolve())
```

Étendre la liste `required` de `verify_staged` :

```python
    required = [
        MARKER_NAME,
        "provision/run-all.ps1",
        "provision/00-bootstrap.ps1",
        "provision/99-marker.ps1",
        "probe/advanced-color.ps1",
        "config/sunshine.conf",
        "config/apps.json",
        "config/secrets.psd1",
    ]
```

- [ ] **Step 4: Écrire `fetch_payload.py`**

```python
#!/usr/bin/env python3
"""Build-time acquisition of the payload binaries that are not already local.

Networking is allowed HERE and nowhere else: the guest provisions offline, so
a URL that rots must break the build, never an install. Nothing in this module
is imported by the guest-facing code paths.

Usage:
    sudo python3 fetch_payload.py --drivers-dir /media/data/nivuus-win-payload
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class FetchError(RuntimeError):
    """Raised when a payload binary cannot be obtained."""


@dataclass(frozen=True)
class Download:
    name: str
    url: str
    dest: Path


# virtio-win is fetched as a whole ISO and mined for two drivers: the stable
# repository publishes no per-driver artifact.
VIRTIO_ISO_URL = ("https://fedorapeople.org/groups/virt/virtio-win/"
                  "direct-downloads/stable-virtio/virtio-win.iso")
STEAM_URL = "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe"
WINFSP_URL = ("https://github.com/winfsp/winfsp/releases/download/"
              "v2.0/winfsp-2.0.23075.msi")


def plan_downloads(drivers_dir: Path) -> list[Download]:
    """Pure: what would be fetched, and where each file would land."""
    return [
        Download("steam", STEAM_URL, drivers_dir / "steam" / "SteamSetup.exe"),
        Download("winfsp", WINFSP_URL,
                 drivers_dir / "winfsp" / "winfsp-2.0.23075.msi"),
        Download("virtio-iso", VIRTIO_ISO_URL,
                 drivers_dir / "virtio" / "virtio-win.iso"),
    ]


def fetch(item: Download) -> str:
    """Download one item unless it is already there. Returns its sha256."""
    item.dest.parent.mkdir(parents=True, exist_ok=True)
    if not item.dest.exists():
        print(f"fetching {item.name} <- {item.url}")
        try:
            with urllib.request.urlopen(item.url, timeout=120) as resp, \
                 open(item.dest, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except OSError as exc:
            item.dest.unlink(missing_ok=True)
            raise FetchError(f"cannot fetch {item.name}: {exc}") from exc
    else:
        print(f"keeping existing {item.dest}")
    digest = hashlib.sha256()
    with open(item.dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# w11/amd64 is the 24H2 driver set; the guest is build 26100.
VIRTIO_MEMBERS = {"netkvm": "NetKVM/w11/amd64", "viofs": "viofs/w11/amd64"}


def extract_virtio(iso: Path, drivers_dir: Path) -> None:
    """Pull NetKVM and viofs out of the virtio-win ISO with 7z."""
    for name, member in VIRTIO_MEMBERS.items():
        dest = drivers_dir / "virtio" / name
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["7z", "x", "-y", f"-o{dest}", str(iso), f"{member}/*"],
            text=True, capture_output=True)
        if proc.returncode != 0:
            raise FetchError(f"cannot extract {member} from {iso}: "
                             f"{proc.stderr.strip()}")
        # 7z recreates the archive tree; flatten it so the guest step can point
        # at one directory instead of guessing the vendor's layout.
        for path in (dest / member.split("/")[0]).rglob("*"):
            if path.is_file():
                path.replace(dest / path.name)
    print(f"extracted {', '.join(VIRTIO_MEMBERS)} from {iso.name}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the B payload binaries")
    ap.add_argument("--drivers-dir", required=True)
    args = ap.parse_args(argv)
    drivers = Path(args.drivers_dir)
    try:
        for item in plan_downloads(drivers):
            print(f"  {item.name} sha256 {fetch(item)}")
        extract_virtio(drivers / "virtio" / "virtio-win.iso", drivers)
    except FetchError as exc:
        raise SystemExit(str(exc))
    print("\nNot fetched, and never fetchable: agent/agent.exe must be "
          "extracted from the current Windows VM before it is wiped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: Lancer les tests et vérifier qu'ils passent**

Run: `python3 scripts/tests/test_windows_guest_payload.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/payload.py installer/windows-guest/fetch_payload.py scripts/tests/test_windows_guest_payload.py
git commit -m "feat(windows-guest): declare the B payload and fetch what is fetchable"
```

---

### Task 2: Fichier de réponses — C: jetable, D: persistant

**Files:**
- Modify: `installer/windows-guest/autounattend.py`
- Modify: `installer/windows-guest/templates/autounattend.xml.j2`
- Test: `scripts/tests/test_windows_guest_autounattend.py` (étendre)

**Interfaces:**
- Consumes: rien de la tâche 1.
- Produces: `UnattendParams` gagne `disk_mode: str = "wipe"` et
  `system_partition_mb: int = 204800` ; `autounattend.DISK_MODES = ("wipe", "rebuild")` ;
  `UnattendError` est levée pour un mode inconnu ou une taille hors bornes.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `scripts/tests/test_windows_guest_autounattend.py` :

```python
# --- Sous-projet B : deux partitions, et un mode qui ne détruit pas D:.
base = dict(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
            admin_password="s3cret", image_name="Windows 11 IoT Enterprise LTSC")

wipe = autounattend.render(autounattend.UnattendParams(**base))
check("wipe mode wipes the disk", "<WillWipeDisk>true</WillWipeDisk>" in wipe, True)
check("wipe mode creates four partitions", wipe.count("<CreatePartition "), 4)
check("wipe mode sizes C at 200 GiB", "<Size>204800</Size>" in wipe, True)
check("wipe mode extends the last partition", wipe.count("<Extend>true</Extend>"), 1)
check("wipe mode letters C", "<Letter>C</Letter>" in wipe, True)
# The optical drive takes D: unless the answer file claims it first.
check("wipe mode letters D", "<Letter>D</Letter>" in wipe, True)
check("wipe mode installs to partition 3",
      "<InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>" in wipe,
      True)

rebuild = autounattend.render(
    autounattend.UnattendParams(**base, disk_mode="rebuild"))
check("rebuild never wipes the disk", "WillWipeDisk" in rebuild, False)
check("rebuild creates no partition", "<CreatePartition " in rebuild, False)
check("rebuild formats exactly one partition", rebuild.count("<ModifyPartition "), 1)
check("rebuild formats partition 3", "<PartitionID>3</PartitionID>" in rebuild, True)
# Partition 4 is D:. Naming it at all in rebuild mode would be a bug.
check("rebuild never names partition 4", "<PartitionID>4</PartitionID>" in rebuild,
      False)
check("rebuild installs to partition 3",
      "<InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>" in rebuild,
      True)

check_raises("unknown disk mode is refused", autounattend.UnattendError,
             lambda: autounattend.render(
                 autounattend.UnattendParams(**base, disk_mode="format-everything")))
check_raises("an absurdly small C is refused", autounattend.UnattendError,
             lambda: autounattend.render(
                 autounattend.UnattendParams(**base, system_partition_mb=1024)))

# The guest must stay logged on forever: Apollo captures an interactive desktop
# and the agent must live in session 1.
check("autologon is enabled", "<Enabled>true</Enabled>" in wipe, True)
```

Si `check_raises` n'existe pas encore dans ce fichier, le reprendre tel quel
depuis `scripts/tests/test_windows_guest_hardware.py`.

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python3 scripts/tests/test_windows_guest_autounattend.py`
Expected: FAIL — `wipe mode creates four partitions: got 3, want 4`.

- [ ] **Step 3: Étendre `autounattend.py`**

Ajouter, sous les constantes existantes :

```python
DISK_MODES = ("wipe", "rebuild")
# 200 GiB for Windows, Apollo and the agent; everything else is D:. Below
# 60 GiB an LTSC install plus its page/hibernation files leaves no room to
# service itself, so a typo there is refused rather than discovered later.
DEFAULT_SYSTEM_PARTITION_MB = 204800
MIN_SYSTEM_PARTITION_MB = 61440
```

Ajouter les deux champs au dataclass :

```python
    # "wipe" partitions the whole disk; "rebuild" reformats C: and leaves the
    # games partition alone. See the plan header: rebuild against a disk this
    # tool did not partition would reformat an arbitrary partition, which is
    # why 20-disk.ps1 checks D:\state\NIVUUS-DATA.id before anything writes.
    disk_mode: str = "wipe"
    system_partition_mb: int = DEFAULT_SYSTEM_PARTITION_MB
```

Ajouter à `validate` :

```python
    if params.disk_mode not in DISK_MODES:
        raise UnattendError(
            f"disk_mode must be one of {DISK_MODES}, got {params.disk_mode!r}"
        )
    if params.system_partition_mb < MIN_SYSTEM_PARTITION_MB:
        raise UnattendError(
            f"system partition must be at least {MIN_SYSTEM_PARTITION_MB} MB, "
            f"got {params.system_partition_mb}"
        )
```

Et passer les deux au gabarit dans `render` :

```python
        disk_mode=params.disk_mode,
        system_partition_mb=params.system_partition_mb,
```

- [ ] **Step 4: Réécrire le bloc `<DiskConfiguration>` du gabarit**

Dans `templates/autounattend.xml.j2`, remplacer tout le bloc
`<DiskConfiguration>…</DiskConfiguration>` par :

```jinja
      <DiskConfiguration>
        <WillShowUI>OnError</WillShowUI>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
{%- if disk_mode == "wipe" %}
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order><Type>EFI</Type><Size>260</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order><Type>MSR</Type><Size>16</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order><Type>Primary</Type>
              <Size>{{ system_partition_mb }}</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>4</Order><Type>Primary</Type><Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order><PartitionID>1</PartitionID>
              <Label>System</Label><Format>FAT32</Format>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order><PartitionID>2</PartitionID>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>3</Order><PartitionID>3</PartitionID>
              <Label>Windows</Label><Letter>C</Letter><Format>NTFS</Format>
            </ModifyPartition>
            <!-- The letter is claimed here on purpose: leave it to Windows and
                 the optical drive takes D:, which would send Steam to C:. -->
            <ModifyPartition wcm:action="add">
              <Order>4</Order><PartitionID>4</PartitionID>
              <Label>Data</Label><Letter>D</Letter><Format>NTFS</Format>
            </ModifyPartition>
          </ModifyPartitions>
{%- else %}
          <!-- Rebuild: reformat the system partition and NOTHING else. The
               games partition keeps Steam, its session and Apollo's pairings.
               Valid only against a disk this tool partitioned in wipe mode -
               20-disk.ps1 refuses to continue without D:\state\NIVUUS-DATA.id. -->
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order><PartitionID>3</PartitionID>
              <Label>Windows</Label><Letter>C</Letter><Format>NTFS</Format>
            </ModifyPartition>
          </ModifyPartitions>
{%- endif %}
        </Disk>
      </DiskConfiguration>
```

- [ ] **Step 5: Lancer les tests et vérifier qu'ils passent**

Run: `python3 scripts/tests/test_windows_guest_autounattend.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/autounattend.py installer/windows-guest/templates/autounattend.xml.j2 scripts/tests/test_windows_guest_autounattend.py
git commit -m "feat(windows-guest): split the guest disk into a disposable C: and a persistent D:"
```

---

### Task 3: Rendu de la configuration d'Apollo

**Files:**
- Create: `installer/windows-guest/apollo.py`
- Create: `installer/windows-guest/templates/sunshine.conf.j2`
- Create: `installer/windows-guest/templates/apps.json.j2`
- Test: `scripts/tests/test_windows_guest_apollo.py`

**Interfaces:**
- Consumes: rien.
- Produces: `apollo.ApolloParams(ui_username, ui_password, steam_dir="D:\\Steam", state_dir="D:\\state\\apollo")` ; `apollo.render_conf(params) -> str` ; `apollo.render_apps(params) -> str` ; `apollo.render_secrets(admin_password, ui_username, ui_password) -> str` (fichier PowerShell `.psd1` lu par les étapes de l'invité) ; `apollo.ApolloError`.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_windows_guest_apollo.py` :

```python
#!/usr/bin/env python3
"""Rendering of the Apollo configuration shipped in the offline payload.

Every key asserted here was verified present in the Apollo 0.4.6 binary on
2026-08-22; a typo in a key name is silently ignored by Apollo, so the test is
the only thing standing between a rendered file and a stream that never gets
HDR. Run: python3 scripts/tests/test_windows_guest_apollo.py
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import apollo  # noqa: E402

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
    failures.append(f"{label}: raised nothing, want {exc_type.__name__}")


params = apollo.ApolloParams(ui_username="nivuus", ui_password="p4ssw0rd")
conf = apollo.render_conf(params)
conf_map = {}
for raw in conf.splitlines():
    raw = raw.strip()
    if raw and not raw.startswith("#") and "=" in raw:
        k, _, v = raw.partition("=")
        conf_map[k.strip()] = v.strip()

# Measured 2026-08-22: with ensure_only_display the virtual display was the
# only active path (paths=1), which is what put bpc=10 on the wire.
check("only the virtual display stays active",
      conf_map.get("dd_configuration_option"), "ensure_only_display")
# The isolated option keeps the physical display alive in a corner - wrong for
# a headless box, and the cause of the 2026-07-23 "game on the dummy plug" bug.
check("no isolated corner layout",
      conf_map.get("isolated_virtual_display_option"), "disabled")
check("HDR follows the client", conf_map.get("dd_hdr_option"), "auto")
# The dummy plug is gone: pinning an output name would pin a display that no
# longer exists.
check("no output is pinned", "output_name" in conf_map, False)
check("credentials are not in sunshine.conf",
      any("p4ssw0rd" in v for v in conf_map.values()), False)

apps = json.loads(apollo.render_apps(params))
names = [a["name"] for a in apps["apps"]]
check("both production apps are declared", sorted(names),
      ["Desktop", "Steam Big Picture"])
desktop = next(a for a in apps["apps"] if a["name"] == "Desktop")
# 🔴 It is the app's virtual-display flag - NOT isolated_virtual_display_option
# - that makes the SudoVDA display appear (trap paid on 2026-07-23).
check("Desktop asks for a virtual display", desktop.get("virtual-display"), True)
check("Desktop launches Steam from D:", desktop.get("detached"),
      ["D:\\Steam\\steam.exe"])
bp = next(a for a in apps["apps"] if a["name"] == "Steam Big Picture")
check("Big Picture asks for a virtual display", bp.get("virtual-display"), True)

secrets = apollo.render_secrets("adminpass", "nivuus", "p4ssw0rd")
check("secrets file is a PowerShell data file", secrets.lstrip().startswith("@{"), True)
for needle in ["adminpass", "nivuus", "p4ssw0rd"]:
    check(f"secrets carry {needle}", needle in secrets, True)
# A quote in a secret would break the .psd1 and, worse, could inject.
check_raises("a quote in a secret is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("ad'min", "nivuus", "p4ssw0rd"))
check_raises("an empty UI password is refused", apollo.ApolloError,
             lambda: apollo.render_secrets("adminpass", "nivuus", ""))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK")
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_apollo.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'apollo'`.

- [ ] **Step 3: Écrire `apollo.py`**

```python
"""Rendering of the Apollo configuration and of the guest-side secrets file.

Apollo silently ignores a key it does not know, so a typo here costs a
streaming session with no HDR and no error anywhere. Every key rendered by
this module was read out of the Apollo 0.4.6 binary on 2026-08-22.

The Web-manager credentials are NOT rendered into sunshine.conf: Apollo hashes
them itself through `sunshine.exe --creds`, which is why this module ships them
separately in a PowerShell data file the guest steps read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

STEAM_DIR = "D:\\Steam"
STATE_DIR = "D:\\state\\apollo"


class ApolloError(ValueError):
    """Raised when the Apollo configuration cannot be rendered safely."""


@dataclass(frozen=True)
class ApolloParams:
    ui_username: str
    ui_password: str
    steam_dir: str = STEAM_DIR
    state_dir: str = STATE_DIR


def _env() -> Environment:
    # No autoescape: these are a conf file and a JSON document, not HTML.
    return Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                       keep_trailing_newline=True)


def render_conf(params: ApolloParams) -> str:
    # sunshine.conf carries no path and no secret: the config directory is a
    # junction to D:, so Apollo's own relative defaults already land there.
    return _env().get_template("sunshine.conf.j2").render()


def render_apps(params: ApolloParams) -> str:
    steam_exe = params.steam_dir.rstrip("\\") + "\\steam.exe"
    return _env().get_template("apps.json.j2").render(
        steam_exe=steam_exe.replace("\\", "\\\\"))


def render_secrets(admin_password: str, ui_username: str,
                   ui_password: str) -> str:
    """Render the .psd1 the guest steps read instead of taking arguments."""
    values = {"AdminPassword": admin_password,
              "ApolloUser": ui_username,
              "ApolloPassword": ui_password}
    for name, value in values.items():
        if not value:
            raise ApolloError(f"{name} must not be empty")
        # A single quote would close the PowerShell literal early: refuse it
        # rather than escape it, so no guest step can be made to run something
        # a secret smuggled in.
        if "'" in value or "\n" in value or "\r" in value:
            raise ApolloError(
                f"{name} must not contain a quote or a newline"
            )
    body = "\n".join(f"    {k} = '{v}'" for k, v in values.items())
    return "@{\n" + body + "\n}\n"
```

- [ ] **Step 4: Écrire `templates/sunshine.conf.j2`**

```jinja
# Nivuus appliance - generated, do not edit in place.
# Every key here was verified present in Apollo 0.4.6 (2026-08-22).

# Deactivate every other display and stream the virtual one alone. Measured:
# paths=1, enabled=1, bpc=10 on the SudoVDA target. With ensure_primary the
# dummy plug stayed primary at (0,0) and fullscreen games opened on IT while
# Apollo captured the virtual display - the 2026-07-23 "only the desktop
# streams" bug.
dd_configuration_option = ensure_only_display

# The isolated option pushes the virtual display into a corner and KEEPS the
# physical ones active. Wrong for a headless box.
isolated_virtual_display_option = disabled

# Follow the client's request. On Server 2022 this was decorative - the
# Advanced Color API failed in both directions. On build 26100 it is real.
dd_hdr_option = auto

# No output_name: the HDMI dummy plug is physically removed, and pinning a
# display that does not exist would strand every session.

# Apollo restores the display layout this long after a client disconnects.
dd_config_revert_delay = 3000

min_log_level = info
```

- [ ] **Step 5: Écrire `templates/apps.json.j2`**

```jinja
{
  "env": {},
  "apps": [
    {
      "name": "Desktop",
      "image-path": "desktop.png",
      "virtual-display": true,
      "prep-cmd": [
        {
          "do": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\nivuus\\apollo\\maximize-steam.ps1",
          "undo": "",
          "elevated": false
        }
      ],
      "detached": ["{{ steam_exe }}"],
      "allow-client-commands": false
    },
    {
      "name": "Steam Big Picture",
      "image-path": "steam.png",
      "virtual-display": true,
      "prep-cmd": [
        {
          "do": "",
          "undo": "steam://close/bigpicture",
          "elevated": false
        }
      ],
      "detached": ["steam://open/bigpicture"]
    }
  ]
}
```

- [ ] **Step 6: Lancer le test et vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_apollo.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add installer/windows-guest/apollo.py installer/windows-guest/templates/sunshine.conf.j2 installer/windows-guest/templates/apps.json.j2 scripts/tests/test_windows_guest_apollo.py
git commit -m "feat(windows-guest): render the Apollo configuration for a headless HDR appliance"
```

---

### Task 4: Étapes 15-virtio et 20-disk

**Files:**
- Create: `installer/windows-guest/provision/15-virtio.ps1`
- Create: `installer/windows-guest/provision/20-disk.ps1`
- Test: `scripts/tests/test_windows_guest_provision.py` (étendu en tâche 8)

**Interfaces:**
- Consumes: la charge utile de la tâche 1 (`drivers\virtio\netkvm`, `drivers\virtio\viofs`, `drivers\winfsp\*.msi`).
- Produces: `D:` monté, `D:\state`, `D:\Steam` (répertoire créé mais pas peuplé), et le marqueur `D:\state\NIVUUS-DATA.id` que toute reconstruction vérifiera.

- [ ] **Step 1: Écrire `15-virtio.ps1`**

```powershell
<#
    Stage 15: virtio drivers.

    NetKVM is blocking: without it the guest has no network at all, so no DHCP
    lease, no 192.168.3.2, no agent and no wake-on-demand. WinFsp and viofs are
    a comfort - they mount the host's /media/data share - and this stage must
    never fail the whole provisioning over them.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$netkvm = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\virtio\netkvm') `
                        -Filter '*.inf' -ErrorAction SilentlyContinue |
          Select-Object -First 1
if (-not $netkvm) { throw "no NetKVM .inf in $PayloadRoot\drivers\virtio\netkvm" }

# /install binds the driver to devices already present; without it the NIC
# stays in "other devices" until a reboot no one triggers.
$proc = Start-Process -FilePath 'pnputil.exe' `
                      -ArgumentList '/add-driver', $netkvm.FullName, '/install' `
                      -Wait -PassThru -NoNewWindow
# pnputil returns 3010 for "installed, reboot required", which is a success.
if ($proc.ExitCode -notin @(0, 3010)) {
    throw "pnputil failed on $($netkvm.Name): exit $($proc.ExitCode)"
}

$nic = Get-NetAdapter -ErrorAction SilentlyContinue |
       Where-Object { $_.InterfaceDescription -match 'VirtIO|Red Hat' }
if (-not $nic) { throw 'no virtio network adapter after installing NetKVM' }
Write-Host "NetKVM OK: $($nic.InterfaceDescription) status $($nic.Status)"

# --- Everything below is best-effort. A failure here is logged, never fatal.
try {
    $msi = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\winfsp') `
                         -Filter '*.msi' -ErrorAction Stop | Select-Object -First 1
    if ($msi) {
        $p = Start-Process -FilePath 'msiexec.exe' `
                           -ArgumentList '/i', "`"$($msi.FullName)`"", '/qn', '/norestart' `
                           -Wait -PassThru
        Write-Host "WinFsp installer exited $($p.ExitCode)"
    }
    $viofs = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\virtio\viofs') `
                           -Filter '*.inf' -ErrorAction Stop | Select-Object -First 1
    if ($viofs) {
        Start-Process -FilePath 'pnputil.exe' `
                      -ArgumentList '/add-driver', $viofs.FullName, '/install' `
                      -Wait -PassThru -NoNewWindow | Out-Null
        Write-Host 'viofs driver submitted'
    }
}
catch {
    Write-Host "virtiofs is optional and did not install: $($_.Exception.Message)"
}
```

- [ ] **Step 2: Écrire `20-disk.ps1`**

```powershell
<#
    Stage 20: the persistent volume.

    D: is what makes this box rebuildable: Steam, its session and Apollo's
    pairings live there and survive a reinstall of C:. This stage is therefore
    the one that must refuse to continue when D: is not the volume it thinks
    it is - everything after it writes into D:, and a wrong guess would eat
    hundreds of gigabytes of games.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$MinDataGiB = 100
$DataMarker = 'D:\state\NIVUUS-DATA.id'

# The answer file assigns D: on a fresh install. On a rebuild the letter can
# drift, so repair it from the volume label rather than assume it.
if (-not (Test-Path 'D:\')) {
    $part = Get-Partition | Where-Object {
        $_.DriveLetter -eq $null -and $_.Size -gt ($MinDataGiB * 1GB)
    } | Sort-Object -Property Size -Descending | Select-Object -First 1
    if (-not $part) { throw 'no unlettered volume large enough to be D:' }
    Write-Host "assigning D: to partition $($part.PartitionNumber)"
    Set-Partition -InputObject $part -NewDriveLetter D
}

$vol = Get-Volume -DriveLetter D
if ($vol.FileSystem -ne 'NTFS') {
    throw "D: is $($vol.FileSystem), expected NTFS - wrong volume?"
}
$sizeGiB = [math]::Round($vol.Size / 1GB)
if ($sizeGiB -lt $MinDataGiB) {
    throw "D: is only $sizeGiB GiB; the games partition needs at least $MinDataGiB GiB"
}
Write-Host "D: is $sizeGiB GiB NTFS"

New-Item -ItemType Directory -Force -Path 'D:\state' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\Steam' | Out-Null

# Seed if absent, never rewrite: on a rebuild this marker is the proof that D:
# is the volume a previous run of this tooling created.
if (-not (Test-Path $DataMarker)) {
    Set-Content -Path $DataMarker -Encoding ASCII -Value @(
        'nivuus_data=1',
        "created=$(Get-Date -Format o)"
    )
    Write-Host 'D: initialised (first install)'
}
else {
    Write-Host "D: carries an existing Nivuus marker: $((Get-Content $DataMarker)[1])"
}
```

- [ ] **Step 3: Vérifier la syntaxe PowerShell côté hôte**

Aucun PowerShell ici ; le contrôle est statique. Vérifier à la main qu'aucune
lettre de charge utile n'est codée en dur :

Run: `grep -nE '[D-Z]:\\nivuus' installer/windows-guest/provision/15-virtio.ps1 installer/windows-guest/provision/20-disk.ps1`
Expected: aucune sortie.

- [ ] **Step 4: Commit**

```bash
git add installer/windows-guest/provision/15-virtio.ps1 installer/windows-guest/provision/20-disk.ps1
git commit -m "feat(windows-guest): install virtio drivers and claim the persistent volume"
```

---

### Task 5: Étape 25-apollo

**Files:**
- Create: `installer/windows-guest/provision/25-apollo.ps1`
- Create: `installer/windows-guest/provision/assets/maximize-steam.ps1`
- Delete: `installer/windows-guest/provision/20-sudovda.ps1`

**Interfaces:**
- Consumes: `drivers\apollo\*.exe`, `config\sunshine.conf`, `config\apps.json`, `config\secrets.psd1` (tâches 1 et 3) ; `D:\state` (tâche 4).
- Produces: `C:\Program Files\Apollo\config` jonctionné vers `D:\state\apollo` ; service `ApolloService` en démarrage automatique et démarré ; périphérique SudoVDA présent ; `C:\nivuus\apollo\maximize-steam.ps1` déposé.

- [ ] **Step 1: Supprimer l'étape SudoVDA autonome**

```bash
git rm installer/windows-guest/provision/20-sudovda.ps1
```

L'installeur Apollo 0.4.6 embarque `drivers\sudovda` à l'identique — même
`install.bat`, même `sudovda.cer`, même `nefconc.exe` — et ce script pose
lui-même le certificat dans `Root` et `TrustedPublisher` avant de recréer le
nœud de périphérique. Installer le même IDD deux fois est un risque gratuit ;
CLAUDE.md avertit explicitement de ne jamais avoir deux chemins d'écran
virtuel concurrents.

- [ ] **Step 2: Écrire `assets/maximize-steam.ps1`**

Reprise du script de production `C:\Apollo-scripts\maximize-steam.ps1`, dont
le comportement est décrit dans CLAUDE.md : attendre la fenêtre de premier
niveau intitulée exactement `Steam` et la maximiser en boucle pendant 30 s.
Session 0 ne peut pas le faire — d'où le `prep-cmd` d'Apollo.

```powershell
<#
    Maximize the Steam window inside the streaming session.

    Apollo runs this as a prep-cmd, which is the only place it can work: the
    session-0 tooling (WinRM) cannot touch a session-1 window. Steam restores
    its previous window geometry, which on a fresh virtual display is a small
    window in a corner.
#>
$ErrorActionPreference = 'Continue'

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Win {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    public const int SW_MAXIMIZE = 3;
}
'@

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $p = Get-Process -Name 'steam' -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -eq 'Steam' } | Select-Object -First 1
    if ($p) { [Win]::ShowWindow($p.MainWindowHandle, [Win]::SW_MAXIMIZE) | Out-Null }
    Start-Sleep -Milliseconds 500
}
```

- [ ] **Step 3: Écrire `25-apollo.ps1`**

```powershell
<#
    Stage 25: Apollo, its virtual display, and the configuration that survives
    a rebuild.

    Order is load-bearing. The installer must run before the junction, because
    it creates the config directory; the junction must exist before the
    service starts, or Apollo writes its pairings onto C: where the next
    rebuild would erase them.

    /D= is deliberately NOT passed: NSIS wants it unquoted and last, and the
    default path contains a space that PowerShell would quote. The install
    location is read back from the registry instead of assumed.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
$ApolloState = 'D:\state\apollo'

$installer = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\apollo') `
                           -Filter '*.exe' | Sort-Object Name | Select-Object -First 1
if (-not $installer) { throw "no Apollo installer in $PayloadRoot\drivers\apollo" }

Write-Host "installing $($installer.Name)"
$proc = Start-Process -FilePath $installer.FullName -ArgumentList '/S' -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "Apollo installer exited $($proc.ExitCode)" }

$root = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Apollo' `
                          -ErrorAction SilentlyContinue).InstallLocation
if (-not $root) { $root = 'C:\Program Files\Apollo' }
if (-not (Test-Path (Join-Path $root 'sunshine.exe'))) {
    throw "Apollo does not look installed under $root"
}
Write-Host "Apollo installed at $root"

# The bundled SudoVDA package. install.bat seeds its own certificate into Root
# and TrustedPublisher, then removes and recreates the device node, so running
# it is idempotent.
$vdaDir = Join-Path $root 'drivers\sudovda'
if (Test-Path (Join-Path $vdaDir 'install.bat')) {
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install.bat' `
                       -WorkingDirectory $vdaDir -Wait -PassThru
    Write-Host "SudoVDA install.bat exited $($p.ExitCode)"
}
$vda = Get-PnpDevice -InstanceId 'ROOT\DISPLAY\*' -ErrorAction SilentlyContinue |
       Where-Object { $_.Status -eq 'OK' } | Select-Object -First 1
if (-not $vda) { throw 'no working ROOT\DISPLAY device: SudoVDA did not install' }
Write-Host "SudoVDA OK: $($vda.InstanceId)"

# --- The junction. Stop the service first: it holds its config directory open.
Stop-Service -Name 'ApolloService' -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$config = Join-Path $root 'config'
New-Item -ItemType Directory -Force -Path $ApolloState | Out-Null

$item = Get-Item -Path $config -ErrorAction SilentlyContinue
$isJunction = $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
if (-not $isJunction) {
    if ($item) {
        # Seed if absent, never overwrite: on a rebuild D: already holds the
        # pairings, and the freshly installed config is the empty one.
        foreach ($f in Get-ChildItem -Path $config -Force -ErrorAction SilentlyContinue) {
            $target = Join-Path $ApolloState $f.Name
            if (-not (Test-Path $target)) { Copy-Item -Path $f.FullName -Destination $target -Recurse }
        }
        Remove-Item -Path $config -Recurse -Force
    }
    cmd.exe /c "mklink /J `"$config`" `"$ApolloState`"" | Out-Null
}
$item = Get-Item -Path $config
if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "$config is not a junction: Apollo would write its pairings onto C:"
}
Write-Host "config junctioned to $ApolloState"

# --- Generated files: always rewritten. They carry no user state, and pinning
# them to first-install would strand the appliance on an old configuration.
Copy-Item -Path (Join-Path $PayloadRoot 'config\sunshine.conf') `
          -Destination (Join-Path $ApolloState 'sunshine.conf') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'config\apps.json') `
          -Destination (Join-Path $ApolloState 'apps.json') -Force
New-Item -ItemType Directory -Force -Path 'C:\nivuus\apollo' | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\maximize-steam.ps1') `
          -Destination 'C:\nivuus\apollo\maximize-steam.ps1' -Force

# --- Web-manager credentials: seeded only when the state file is absent, so a
# rebuild keeps whatever the owner set. sunshine.exe hashes them itself.
$secrets = Import-PowerShellDataFile -Path (Join-Path $PayloadRoot 'config\secrets.psd1')
if (-not (Test-Path (Join-Path $ApolloState 'sunshine_state.json'))) {
    # The values stay in variables: Start-Transcript records the source line,
    # not the expansion, so the password never lands in provision.log.
    $p = Start-Process -FilePath (Join-Path $root 'sunshine.exe') `
                       -ArgumentList '--creds', $secrets.ApolloUser, $secrets.ApolloPassword `
                       -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -ne 0) { throw "sunshine.exe --creds exited $($p.ExitCode)" }
    Write-Host 'Apollo web credentials seeded'
}
else {
    Write-Host 'Apollo state exists: web credentials left untouched'
}

# --- Service and firewall. Apollo ships the scripts; use them rather than
# reimplement sc/netsh incantations that would drift from the vendor's.
$scripts = Join-Path $root 'scripts'
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'autostart-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
# Delete first: netsh happily creates a duplicate rule on every rebuild.
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'delete-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'add-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null

Start-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
$svc = Get-Service -Name 'ApolloService'
if ($svc.Status -ne 'Running') { throw "ApolloService is $($svc.Status), expected Running" }
Set-Content -Path (Join-Path $StateDir 'apollo.root') -Value $root -Encoding ASCII
Write-Host 'Apollo running'
```

- [ ] **Step 4: Vérifier statiquement**

Run: `grep -c 'ApolloPassword' installer/windows-guest/provision/25-apollo.ps1`
Expected: `1` — le mot de passe ne traverse qu'un seul point du script.

Run: `grep -nE '[D-Z]:\\nivuus' installer/windows-guest/provision/25-apollo.ps1`
Expected: aucune sortie (`D:\state` et `C:\nivuus` sont des cibles fixes, pas
la charge utile — le motif ne doit pas matcher).

- [ ] **Step 5: Commit**

```bash
git add -A installer/windows-guest/provision
git commit -m "feat(windows-guest): install Apollo with its virtual display and a rebuild-proof config"
```

---

### Task 6: Étapes 30-steam et 40-agent

**Files:**
- Create: `installer/windows-guest/provision/30-steam.ps1`
- Create: `installer/windows-guest/provision/40-agent.ps1`
- Create: `installer/windows-guest/provision/assets/run-agent.ps1`

**Interfaces:**
- Consumes: `drivers\steam\SteamSetup.exe`, `drivers\agent\agent.exe`, `D:\Steam` (tâche 4).
- Produces: `D:\Steam\steam.exe` ; tâche planifiée `guacamole-agent` ; `C:\nivuus\state\agent-session.txt` contenant l'identifiant de session de l'agent — la preuve que `99-marker.ps1` exige.

- [ ] **Step 1: Écrire `30-steam.ps1`**

```powershell
<#
    Stage 30: Steam, installed ON D: rather than configured to install there.

    Pre-seeding libraryfolders.vdf does not hold: Steam rewrites it and the
    default folder drifts back to C:. /D=D:\Steam makes D:\Steam\steamapps the
    default library BY CONSTRUCTION, and nothing can fall back.

    The consequence reaches past the games: wiping C: leaves the whole Steam
    install intact, config\loginusers.vdf included, so the session token
    survives. Re-running the installer on the new C: only recreates registry
    entries and shortcuts - no library to re-add, no login.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$SteamDir = 'D:\Steam'

$setup = Join-Path $PayloadRoot 'drivers\steam\SteamSetup.exe'
if (-not (Test-Path $setup)) { throw "missing $setup" }

$fresh = -not (Test-Path (Join-Path $SteamDir 'steam.exe'))
# NSIS: /D= must be the last argument and must not be quoted. D:\Steam has no
# space, so PowerShell passes it through untouched.
$proc = Start-Process -FilePath $setup -ArgumentList '/S', "/D=$SteamDir" `
                      -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "SteamSetup exited $($proc.ExitCode)" }

if (-not (Test-Path (Join-Path $SteamDir 'steam.exe'))) {
    throw "no steam.exe under $SteamDir after installing"
}
if ($fresh) { Write-Host "Steam installed into $SteamDir" }
else { Write-Host "Steam re-registered against the existing $SteamDir" }

$login = Join-Path $SteamDir 'config\loginusers.vdf'
if (Test-Path $login) { Write-Host 'existing Steam session preserved' }
```

- [ ] **Step 2: Écrire `assets/run-agent.ps1`**

```powershell
<#
    Launcher for the Guacamole agent inside the interactive session.

    Writes its own session id BEFORE starting the agent: that file is the
    appliance's replacement for check-session.sh, which cannot be used here -
    it requires the //192.168.3.2/c CIFS mount that the cutover removes, and
    it launches a C:\dev development build that does not exist on an
    appliance.
#>
$ErrorActionPreference = 'Continue'

$state = 'C:\nivuus\state'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$sid = (Get-Process -Id $PID).SessionId
Set-Content -Path (Join-Path $state 'agent-session.txt') -Value "$sid" `
            -Encoding ASCII -NoNewline

$env:SIGNALING_URL = 'ws://192.168.3.1:8080'
$env:LOCAL_IP      = '192.168.3.2'
$env:RUST_LOG      = 'info'

# UTF-8 both ways: the agent writes UTF-8 and PowerShell would otherwise decode
# it through the OEM code page, turning every accent into mojibake.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$log = New-Object System.IO.StreamWriter('C:\nivuus\agent.log', $false,
                                         (New-Object System.Text.UTF8Encoding($false)))
try {
    & 'C:\nivuus\agent\agent.exe' *>&1 |
        ForEach-Object { $log.WriteLine([string]$_); $log.Flush() }
}
finally { $log.Close() }
```

- [ ] **Step 3: Écrire `40-agent.ps1`**

```powershell
<#
    Stage 40: the Guacamole agent.

    The agent is a payload artefact with no user state: it is redeployed on
    every rebuild by construction, so nothing about it goes to D:.

    🔴 It must run in session 1, never as a service. Window capture
    (Windows.Graphics.Capture) and input injection (SendInput) do not cross the
    session boundary - the same constraint that banned SetupComplete.cmd in
    sub-project A.

    The task carries NO password: an AtLogOn trigger with an Interactive logon
    type runs in the session of whoever is logged on, which permanent autologon
    guarantees is Administrator in session 1.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$TaskName = 'guacamole-agent'
$AgentDir = 'C:\nivuus\agent'

New-Item -ItemType Directory -Force -Path $AgentDir | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'drivers\agent\agent.exe') `
          -Destination (Join-Path $AgentDir 'agent.exe') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\run-agent.ps1') `
          -Destination (Join-Path $AgentDir 'run-agent.ps1') -Force

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File C:\nivuus\agent\run-agent.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'Administrator'
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null
Write-Host "scheduled task $TaskName registered"

# Prove session 1 now rather than trust the trigger: provisioning already runs
# inside the interactive session, so starting the task here exercises exactly
# the path the next logon will take.
$sessionFile = 'C:\nivuus\state\agent-session.txt'
Remove-Item -Path $sessionFile -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
$deadline = (Get-Date).AddSeconds(60)
while (-not (Test-Path $sessionFile) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if (-not (Test-Path $sessionFile)) {
    throw "the agent task never reported a session id; check $AgentDir\..\agent.log"
}
Write-Host "agent reported session $(Get-Content $sessionFile)"
```

- [ ] **Step 4: Vérifier statiquement**

Run: `grep -nE '\-Password|/rp ' installer/windows-guest/provision/40-agent.ps1`
Expected: aucune sortie — la tâche planifiée ne stocke aucun mot de passe.

- [ ] **Step 5: Commit**

```bash
git add installer/windows-guest/provision
git commit -m "feat(windows-guest): install Steam onto D: and run the agent in session 1"
```

---

### Task 7: Étapes 50-power et 55-updates

**Files:**
- Create: `installer/windows-guest/provision/50-power.ps1`
- Create: `installer/windows-guest/provision/55-updates.ps1`

**Interfaces:**
- Consumes: `config\secrets.psd1` (le mot de passe administrateur, pour l'ouverture de session automatique permanente).
- Produces: hibernation activée, plan Performances élevées actif, écran de verrouillage neutralisé, ouverture de session automatique permanente, pilotes exclus de Windows Update.

- [ ] **Step 1: Écrire `50-power.ps1`**

```powershell
<#
    Stage 50: energy, and the permanent logon this appliance is built around.

    The host hibernates this guest after ten minutes of inactivity
    (vm-idle-shutdown.timer) and wakes it from a socket. Hibernation must
    therefore work, and the guest must never fall asleep or lock on its own -
    a locked desktop is one Apollo cannot capture, and a resume that lands on
    the secure desktop drops the stream after about ten seconds.

    ⚠️ This is the exact inverse of sub-project A, which disabled autologon as
    its last act. Two consumers need it: Apollo captures an interactive desktop
    and the agent must live in session 1.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

powercfg.exe /hibernate on
# SCHEME_MIN, the built-in High Performance plan. The guest is a gaming host;
# its power saving is the host hibernating the whole domain, not the guest
# downclocking itself.
powercfg.exe /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
foreach ($what in @('monitor-timeout-ac', 'standby-timeout-ac',
                    'disk-timeout-ac', 'hibernate-timeout-ac')) {
    powercfg.exe /change $what 0
}

# "Require a password on wakeup" - the powercfg CONSOLELOCK alias does not
# exist on this build, so the policy GUID is set directly.
$wake = 'HKLM:\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51'
New-Item -Path $wake -Force | Out-Null
Set-ItemProperty -Path $wake -Name 'ACSettingIndex' -Value 0 -Type DWord
Set-ItemProperty -Path $wake -Name 'DCSettingIndex' -Value 0 -Type DWord

$perso = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization'
New-Item -Path $perso -Force | Out-Null
Set-ItemProperty -Path $perso -Name 'NoLockScreen' -Value 1 -Type DWord

# --- Permanent autologon. The answer file's <LogonCount> counts DOWN and
# deletes AutoAdminLogon when it reaches zero; only these registry values,
# with no AutoLogonCount alongside them, survive indefinitely.
#
# The password is stored in cleartext in HKLM. That is how AutoAdminLogon
# works, and it is the posture this appliance already has: the answer file on
# the 0600 ISO carries it in cleartext too. The guest holds no other secret.
$secrets = Import-PowerShellDataFile -Path (Join-Path $PayloadRoot 'config\secrets.psd1')
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'AutoAdminLogon' -Value '1' -Type String
Set-ItemProperty -Path $winlogon -Name 'DefaultUserName' -Value 'Administrator' -Type String
Set-ItemProperty -Path $winlogon -Name 'DefaultPassword' -Value $secrets.AdminPassword -Type String
Remove-ItemProperty -Path $winlogon -Name 'AutoLogonCount' -ErrorAction SilentlyContinue
Write-Host 'permanent autologon configured'

if (-not (Test-Path 'C:\hiberfil.sys')) {
    Write-Host 'warning: hiberfil.sys is absent; hibernation may not be available'
}
```

- [ ] **Step 2: Écrire `55-updates.ps1`**

```powershell
<#
    Stage 55: Windows Update policy.

    LTSC already removed feature updates - that is why it was chosen. What
    remains is the monthly security rollup, and this guest is reachable from
    the WAN on the streaming ports: turning security updates off would trade a
    breakage risk for an intrusion risk, and a reinstall does not undo an
    intrusion.

    What breaks this configuration is not a security fix but a DRIVER pushed by
    Windows Update - it would replace the NVIDIA driver the whole HDR chain
    depends on, or SudoVDA itself. So that, and only that, is blocked.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$wu = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
New-Item -Path $wu -Force | Out-Null
Set-ItemProperty -Path $wu -Name 'ExcludeWUDriversInQualityUpdate' -Value 1 -Type DWord

$au = Join-Path $wu 'AU'
New-Item -Path $au -Force | Out-Null
# A reboot in the middle of a streaming session is the failure mode this
# prevents; the host reboots the guest on its own schedule instead.
Set-ItemProperty -Path $au -Name 'NoAutoRebootWithLoggedOnUsers' -Value 1 -Type DWord

$search = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching'
New-Item -Path $search -Force | Out-Null
Set-ItemProperty -Path $search -Name 'SearchOrderConfig' -Value 0 -Type DWord

Write-Host 'security updates on, driver updates excluded, no unattended reboot'
```

- [ ] **Step 3: Commit**

```bash
git add installer/windows-guest/provision/50-power.ps1 installer/windows-guest/provision/55-updates.ps1
git commit -m "feat(windows-guest): keep the guest awake, logged on, and off driver updates"
```

---

### Task 8: Enchaînement et clôture

**Files:**
- Modify: `installer/windows-guest/provision/run-all.ps1`
- Modify: `installer/windows-guest/provision/99-marker.ps1`
- Modify: `scripts/tests/test_windows_guest_provision.py`

**Interfaces:**
- Consumes: toutes les étapes des tâches 4 à 7.
- Produces: `C:\nivuus\state\PROVISION.done` portant `provision_version=B1` ; port 5985 ouvert en dernier.

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `scripts/tests/test_windows_guest_provision.py`, remplacer la constante
`STAGES` par :

```python
STAGES = ["00-bootstrap.ps1", "10-nvidia.ps1", "15-virtio.ps1", "20-disk.ps1",
          "25-apollo.ps1", "30-steam.ps1", "40-agent.ps1", "50-power.ps1",
          "55-updates.ps1", "99-marker.ps1"]
```

et ajouter, avant le bloc de sortie :

```python
# --- Sous-projet B.
check("the standalone SudoVDA stage is gone",
      (PROVISION / "20-sudovda.ps1").exists(), False)

marker = texts["99-marker.ps1"]
# A kept autologon is the whole point of the appliance: Apollo captures an
# interactive desktop and the agent lives in session 1. A had to disable it.
check("marker no longer disables autologon", "AutoAdminLogon" in marker, False)
check("marker verifies the agent session", "agent-session.txt" in marker, True)
check("marker verifies Apollo runs", "ApolloService" in marker, True)
check("marker verifies Steam", "steam.exe" in marker, True)
# 5985 opens last, after every check: the host reads a reachable 5985 as
# "provisioned", and a premature open already lied once.
check("marker opens 5985 last",
      marker.rfind("Enable-NetFirewallRule") > marker.rfind("throw"), True)

power = texts["50-power.ps1"]
check("power stage enables permanent autologon",
      "AutoAdminLogon" in power and "AutoLogonCount" in power, True)
check("power stage enables hibernation", "/hibernate on" in power, True)

agent = texts["40-agent.ps1"]
check("agent runs interactively", "LogonType Interactive" in agent, True)
check("agent task carries no password", "-Password" in agent, False)

steam = texts["30-steam.ps1"]
check("Steam installs onto D:", "/D=$SteamDir" in steam, True)

apollo_stage = texts["25-apollo.ps1"]
check("Apollo config is junctioned", "mklink /J" in apollo_stage, True)
check("Apollo install location is read, not assumed",
      "InstallLocation" in apollo_stage, True)

# Every stage must accept the payload root, or run-all cannot drive it.
for name in STAGES:
    check(f"{name} takes PayloadRoot", "$PayloadRoot" in texts[name], True)
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python3 scripts/tests/test_windows_guest_provision.py`
Expected: FAIL — `15-virtio.ps1 exists: got False, want True` n'apparaîtra pas
(les fichiers existent depuis les tâches 4 à 7), mais
`run-all lists every stage: got False, want True` oui.

- [ ] **Step 3: Étendre `run-all.ps1`**

Remplacer la ligne `$stages = @(...)` par :

```powershell
    $stages = @('00-bootstrap.ps1', '10-nvidia.ps1', '15-virtio.ps1',
                '20-disk.ps1', '25-apollo.ps1', '30-steam.ps1',
                '40-agent.ps1', '50-power.ps1', '55-updates.ps1',
                '99-marker.ps1')
```

Rien d'autre ne change : le jeton de redémarrage, l'ordre d'écriture du
`.done` avant la consommation du jeton, et le transcript restent tels quels.
C'est le seul mécanisme du projet éprouvé en conditions réelles.

- [ ] **Step 4: Réécrire `99-marker.ps1`**

```powershell
<#
    Stage 99: close the provisioning.

    Order matters: everything else must be true before port 5985 opens,
    because the host treats a reachable 5985 as "the guest is provisioned".
    This is also where 10-nvidia.ps1's device check lands when that stage had
    to defer it to survive a driver-install reboot.

    ⚠️ Unlike sub-project A, this stage does NOT disable the automatic logon.
    The appliance holds a session open permanently: Apollo captures an
    interactive desktop and the agent must live in session 1. With the dummy
    plug removed, that desktop is reachable only through Apollo (paired
    client), the agent (authenticated platform) or the VNC console, which
    listens on 127.0.0.1 and is therefore root-on-the-host only.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'

# Keep the probe on C: so it can be run again without the payload medium.
# Destination is the parent, not 'C:\nivuus\probe': Copy-Item nests into
# ...\probe\probe when the destination directory already exists.
Copy-Item -Path (Join-Path $PayloadRoot 'probe') -Destination 'C:\nivuus' `
          -Recurse -Force

$gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
if (-not $gpu) { throw 'no NVIDIA display device at end of provisioning' }
if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }

$vda = Get-PnpDevice -InstanceId 'ROOT\DISPLAY\*' -ErrorAction SilentlyContinue |
       Where-Object { $_.Status -eq 'OK' } | Select-Object -First 1
if (-not $vda) { throw 'no working virtual display at end of provisioning' }

$svc = Get-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
if (-not $svc -or $svc.Status -ne 'Running') {
    throw "ApolloService is $(if ($svc) { $svc.Status } else { 'absent' })"
}

if (-not (Test-Path 'D:\Steam\steam.exe')) { throw 'no steam.exe on D:' }
if (-not (Test-Path 'D:\state\NIVUUS-DATA.id')) { throw 'D: carries no Nivuus marker' }

# The session-1 proof. check-session.sh cannot be used on an appliance: it
# needs the CIFS mount the cutover removes and a C:\dev development build.
$sessionFile = Join-Path $StateDir 'agent-session.txt'
if (-not (Test-Path $sessionFile)) { throw 'the agent never reported a session id' }
$sid = (Get-Content $sessionFile -Raw).Trim()
if ($sid -ne '1') {
    throw "the agent runs in session '$sid', not 1: window capture and input injection would both fail"
}

Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                    -Name 'NivuusProvision' -ErrorAction SilentlyContinue

$marker = @(
    "provision_version=B1",
    "completed=$(Get-Date -Format o)",
    "computer=$env:COMPUTERNAME",
    "agent_session=$sid"
)
Set-Content -Path (Join-Path $StateDir 'PROVISION.done') -Value $marker -Encoding ASCII

Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Enable-NetFirewallRule
Write-Host 'provisioning marker written, WinRM reachable'
```

- [ ] **Step 5: Lancer les tests et vérifier qu'ils passent**

Run: `python3 scripts/tests/test_windows_guest_provision.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/provision scripts/tests/test_windows_guest_provision.py
git commit -m "feat(windows-guest): chain the B stages and prove the appliance before opening 5985"
```

---

### Task 9: Câblage de la construction

**Files:**
- Modify: `installer/windows-guest/build.py`
- Test: `scripts/tests/test_windows_guest_payload.py` (étendre)

**Interfaces:**
- Consumes: `payload.PayloadSources.config_dir` (tâche 1), `apollo.render_conf/render_apps/render_secrets` (tâche 3), `autounattend.UnattendParams.disk_mode` (tâche 2).
- Produces: une ISO qui porte `config/sunshine.conf`, `config/apps.json`, `config/secrets.psd1` en plus de la charge utile de A.

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à `scripts/tests/test_windows_guest_payload.py` :

```python
# The rendered configuration must ride in the payload, or 25-apollo has
# nothing to copy and 50-power has no password to set autologon with.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    src = root / "src"
    (src / "provision").mkdir(parents=True)
    (src / "provision" / "run-all.ps1").write_text("x")
    (src / "provision" / "00-bootstrap.ps1").write_text("x")
    (src / "provision" / "99-marker.ps1").write_text("x")
    (src / "probe").mkdir()
    (src / "probe" / "advanced-color.ps1").write_text("x")
    drivers = src / "drivers"
    _make_complete_payload(drivers)
    cfg = src / "config"
    cfg.mkdir()
    for name in ["sunshine.conf", "apps.json", "secrets.psd1"]:
        (cfg / name).write_text("x")
    sources = payload.PayloadSources(
        provision_dir=src / "provision", probe_dir=src / "probe",
        drivers_dir=drivers, config_dir=cfg)
    dest = root / "nivuus"
    payload.stage_payload(dest, sources, payload.marker_text("img", "b1"))
    payload.verify_staged(dest)
    check("the staged payload carries the rendered config",
          (dest / "config" / "sunshine.conf").is_file(), True)
    check("the staged payload carries the secrets",
          (dest / "config" / "secrets.psd1").is_file(), True)
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_payload.py`
Expected: FAIL — `PayloadSources.__init__() got an unexpected keyword argument
'config_dir'` si la tâche 1 n'est pas encore fusionnée ; sinon
`staged payload is missing or empty: config/sunshine.conf`.

- [ ] **Step 3: Étendre `build.py`**

Ajouter aux constantes :

```python
DEFAULT_APOLLO_PASSWORD_FILE = "/root/.config/nivuus/apollo-ui.pass"
```

Ajouter à `parse_args` :

```python
    ap.add_argument("--apollo-password-file", default=DEFAULT_APOLLO_PASSWORD_FILE)
    ap.add_argument("--apollo-user", default="nivuus")
    ap.add_argument("--disk-mode", default="wipe",
                    choices=list(autounattend.DISK_MODES),
                    help="wipe partitions the whole disk; rebuild reformats C: "
                         "and leaves the games partition alone")
    ap.add_argument("--system-partition-gb", type=int, default=200,
                    help="size of C: in GiB; the rest of the disk becomes D:")
```

Dans `main`, après la lecture des deux secrets existants :

```python
    apollo_password = read_secret(args.apollo_password_file,
                                  "Apollo web UI password file")
```

Ajouter `import apollo  # noqa: E402` aux imports locaux, passer les deux
nouveaux champs à `UnattendParams` :

```python
        params = autounattend.UnattendParams(
            product_key=key, admin_password=password,
            image_name=image["name"], hostname=args.hostname,
            disk_mode=args.disk_mode,
            system_partition_mb=args.system_partition_gb * 1024,
        )
```

et, dans le bloc `with tempfile.TemporaryDirectory(...)`, rendre la
configuration avant l'assemblage :

```python
            # Rendered into the same temporary tree as the answer file: the
            # secrets file must never exist on disk outside this build.
            config = Path(tmp) / "config"
            config.mkdir()
            ap_params = apollo.ApolloParams(ui_username=args.apollo_user,
                                            ui_password=apollo_password)
            (config / "sunshine.conf").write_text(apollo.render_conf(ap_params))
            (config / "apps.json").write_text(apollo.render_apps(ap_params))
            (config / "secrets.psd1").write_text(
                apollo.render_secrets(password, args.apollo_user, apollo_password))
            sources = payload.PayloadSources(
                provision_dir=HERE / "provision", probe_dir=HERE / "probe",
                drivers_dir=Path(args.drivers_dir), config_dir=config)
```

en déplaçant la construction de `sources` à cet endroit (elle précédait le
`with`). Ajouter `apollo.ApolloError` au tuple `except` final.

Enfin, étendre le message d'avertissement de fin :

```python
        print(f"  {out} contains the product key, the administrator password "
              "and the Apollo web password in cleartext (all three are needed "
              "for an unattended offline install) - it is mode 0600, keep it "
              "that way.")
```

- [ ] **Step 4: Lancer toute la suite**

Run:
```bash
for t in scripts/tests/test_windows_guest_*.py; do echo "== $t"; python3 "$t" || break; done
```
Expected: chaque suite affiche `OK`.

- [ ] **Step 5: Vérifier qu'aucun secret n'est entré dans le dépôt**

Run: `git diff --cached --stat; grep -rn "apollo-ui.pass" installer/ | head`
Expected: seul le *chemin* du fichier apparaît, jamais son contenu.

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/build.py scripts/tests/test_windows_guest_payload.py
git commit -m "feat(windows-guest): render the Apollo config into the offline payload at build time"
```

---

### Task 10: Recette d'acceptation

**Files:**
- Create: `docs/superpowers/plans/recette-b.md`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: un mode opératoire exécutable par le propriétaire, jamais par
  l'implémenteur.

🔴 **Cette recette n'est PAS exécutée pendant l'implémentation du plan.** Elle
démarre un domaine, prend le GPU et arrête des conteneurs de l'hôte. Elle
exige une fenêtre du propriétaire, avec la VM de production hors service. Le
document porte cette bannière en tête.

- [ ] **Step 1: Écrire la recette**

Le document couvre, dans cet ordre :

1. **Préconditions** — `agent.exe` présent dans la charge utile (sinon rien
   n'est testable) ; recette S4 du sous-projet C passée ; disque jetable
   identifié et **confirmé différent du NVMe de production** ; VM `Windows`
   arrêtée.
2. **Construction** — `python3 fetch_payload.py --drivers-dir …` puis
   `sudo python3 build.py …`, avec la vérification que l'ISO porte bien
   `config/secrets.psd1` (`xorriso -indev … -find /nivuus/config`).
3. **Installation** — définir un domaine de test (jamais `Windows`), attacher
   les deux ISO, démarrer, et **attendre le marqueur**, pas le port :
   `winrm_exec.py … "type C:\nivuus\state\PROVISION.done"`.
4. **Test 1 — HDR de bout en bout.** Un flux **depuis la TV**, pas depuis le
   Moonlight logiciel de l'hôte. Critères : `Client dynamicRange: 1` soutenu
   dans `sunshine.log`, `Display is HDR: true`, et la sonde
   `advanced-color.ps1` lancée **en session 1** (tâche planifiée `/IT`, jamais
   par WinRM qui voit zéro chemin d'affichage) rendant `enabled=1 bpc=10` sur
   la cible SudoVDA — cette fois **demandé par le client**, sans appel à
   `DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE`.
5. **Test 2 — agent en session 1.** `PROVISION.done` porte `agent_session=1`,
   et `agent.log` montre l'agent vivant. ⚠️ `check-session.sh` de Guacamole
   **ne s'applique pas** : il exige `/media/vm` et un binaire `C:\dev`.
6. **Test 3 — reconstruction préservant D:.** Reconstruire l'ISO avec
   `--disk-mode rebuild`, réinstaller, et vérifier sans aucun geste manuel :
   `D:\Steam\config\loginusers.vdf` intact, `D:\state\apollo\sunshine_state.json`
   intact, la TV toujours appairée, `D:\state\NIVUUS-DATA.id` portant sa date
   d'origine.
7. **Ce que la recette ne prouve pas** — le démarrage à froid sans aucun écran
   reste non éprouvé si le domaine de test porte une VGA émulée ; la mise à
   jour d'Apollo casse potentiellement la jonction et devra être re-vérifiée à
   chaque montée de version.
8. **Nettoyage** — supprimer le domaine de test et son varstore.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/recette-b.md
git commit -m "docs(windows-guest): acceptance recipe for the provisioned appliance"
```

---

## Ce que ce plan laisse ouvert, délibérément

| Point | Pourquoi il reste ouvert | Qui le referme |
| --- | --- | --- |
| **`agent.exe` n'existe pas encore** | il se compile aujourd'hui *dans* la VM de production, qui sera effacée. Aucune tâche de B ne peut le produire | précondition 1 de la bascule : extraire le binaire **avant** toute action destructive |
| **La divergence `Administrateur` / `Administrator`** | l'outillage Guacamole côté hôte vaut `Administrateur` par défaut ; l'appliance est en-US | hors périmètre de B : poser `WINDOWS_ADMIN_USERNAME=Administrator` côté Guacamole lors de la bascule |
| **Le poste de développement Guacamole** | conséquence du modèle appliance : `build-agent.sh` et `sync-agent.sh` visent 192.168.3.2 et le montage CIFS | un sous-projet à part, non spécifié |
| **La route Pomerium** | Apollo 0.4.6 rejette l'authentification Basic que Pomerium injecte | étape de la bascule : retirer l'en-tête injecté |
| **Le dimensionnement de C:** | la capacité du NVMe passé n'est pas lisible depuis l'hôte (il est lié à `vfio-pci`) | `--system-partition-gb`, à confirmer par la recette ; `20-disk.ps1` refuse un D: de moins de 100 GiB |
