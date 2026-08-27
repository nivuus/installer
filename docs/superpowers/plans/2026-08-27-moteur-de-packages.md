# Moteur de packages Nivuus — plan d'implémentation (phases 0 et 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doter `nivuus/installer` d'un moteur de packages — manifeste déclaratif, trois phases, résolution de conflits — sans déplacer une seule ligne du code VM existant.

**Architecture:** Un nouveau module `installer/packages/` lit des manifestes `nivuus-package.yaml` découverts dans `/opt/nivuus-packages/*/`. Le moteur évalue l'éligibilité à partir de capacités matérielles grossières qu'il détecte lui-même, exécute une phase `resolve` en lecture seule pour obtenir la ligne de commande noyau exacte avant toute écriture, applique la phase `install` dans le chroot, et arme la phase `activate` pour le premier boot. `kvm-vfio` reste intact : à la fin de ce plan le moteur existe, il est prouvé par un package factice, et il ne sert encore à aucun package réel.

**Tech Stack:** Python 3.11, PyYAML, FastAPI/Pydantic v2 (portail existant), systemd, live-build.

**Spec:** [`docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`](../specs/2026-08-27-decoupage-installer-console-design.md)

## Global Constraints

- **Contrat versionné** : `apiVersion: nivuus.dev/v1`. Un manifeste qui déclare autre chose est refusé, jamais toléré.
- **Deux tiers** : `userspace` ne peut déclarer ni `kernel-cmdline`, ni `modules`, ni `hugepages-mib` — le moteur **rejette** le manifeste, il ne supprime pas silencieusement la clé. `platform` le peut.
- **`resolve` est en lecture seule.** Il tourne avant la moindre écriture disque, et c'est ce qui permet de laisser `bootloader` à sa place actuelle dans `run.py`.
- **Fichiers ≤ 200 lignes** (`.github/copilot-instructions.md`). Chaque module a une responsabilité.
- **Commentaires en anglais**, comme tout le dépôt. Les docstrings expliquent *pourquoi*, pas *quoi*.
- **Convention de test du dépôt** : scripts autonomes, `python3 scripts/tests/test_x.py`, liste `failures` + `check(label, got, want)`, `sys.exit(1)` sur échec. **Il n'y a ni pytest.ini, ni conftest.py** — ne pas en introduire.
- **Aucune modification du comportement de `kvm-vfio`** dans ce plan. `install.sh`, `features.py::_kvm_vfio_thermal` et `common/retro.py` ne sont pas touchés.
- **La CI ne lance aucun test Python** (`.github/workflows/ci.yml`, `test-paths: ""`) : les scripts de `scripts/tests/` s'exécutent à l'import, donc pytest les déclencherait pendant la collecte. C'est une dette déjà tracée en amont. **Chaque tâche lance donc ses tests à la main**, et `make test-packages` (tâche 11) est le seul agrégateur. Ne pas conclure d'un CI vert que les tests sont passés.
- **La CI passe `shellcheck` sur tout le dépôt** (`shellcheck-paths: "."`). Les deux scripts modifiés par ce plan — `install.sh` (tâche 1) et `installer/iso-build/build.sh` (tâche 11) — doivent rester propres.
- **PyYAML doit être ajouté aux DEUX fichiers de dépendances** : `requirements.txt` à la racine (consommé par la CI) et `installer/webapp/requirements.txt` (consommé par le venv de l'ISO). Le doublon vient du socle partagé amont ; l'oublier fait échouer la CI sans toucher l'ISO, ou l'inverse.
- Chemin de découverte surchargeable par `NIVUUS_PACKAGES_DIR` (défaut `/opt/nivuus-packages`), exactement comme `NIVUUS_PROGRESS_DIR` l'est déjà.

---

### Task 1: Phase 0 — supprimer les deux fichiers morts

Deux fichiers ne sont plus lus par aucun code. `configs/vm-template.xml` est mort depuis que `windows-guest/domain.py` génère le XML depuis `templates/domain.xml.j2` ; il n'est cité que par un `echo` et de la documentation périmée. `scripts/vm-cpu-partition.sh.deployed-backup` est un détritus de déploiement.

Les supprimer maintenant évite de les déménager en phase 3.

**Files:**
- Delete: `configs/vm-template.xml`
- Delete: `scripts/vm-cpu-partition.sh.deployed-backup`
- Modify: `install.sh:242` (la ligne `echo` qui le cite)
- Modify: `QUICKSTART.md:84,92`
- Modify: `docs/vm-configuration.md:462`

**Interfaces:**
- Consumes: rien
- Produces: rien — tâche de nettoyage pur

- [ ] **Step 1: Constater l'état de départ**

```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
  | grep -v '^./CLAUDE.md'
```

Attendu : 5 lignes (`install.sh:242`, `CHANGELOG.md` ×2, `docs/vm-configuration.md:462`, `QUICKSTART.md` ×2). **`CHANGELOG.md` est un journal historique : ne pas le modifier.**

- [ ] **Step 2: Supprimer les deux fichiers**

```bash
git rm configs/vm-template.xml scripts/vm-cpu-partition.sh.deployed-backup
```

- [ ] **Step 3: Corriger la ligne morte d'`install.sh`**

Remplacer, dans le bloc « Next steps » :

```bash
echo "  2. Create Windows VM using template:"
echo "     virsh define $NIVUUS_DIR/configs/vm-template.xml"
```

par :

```bash
echo "  2. Build the Windows guest VM:"
echo "     python3 $NIVUUS_DIR/installer/windows-guest/domain.py define"
```

- [ ] **Step 4: Corriger `QUICKSTART.md`**

Remplacer le bloc citant `configs/vm-template.xml` (lignes 84 et 92) par :

```bash
# Générer et définir le domaine depuis le matériel détecté
python3 installer/windows-guest/domain.py xml     # inspecter
sudo python3 installer/windows-guest/domain.py define
```

- [ ] **Step 5: Corriger `docs/vm-configuration.md:462`**

Remplacer `**Fichier:** \`/home/mallanic/Projects/Nivuus/configs/vm-template.xml\`` par :

```markdown
**Généré par:** `installer/windows-guest/domain.py` depuis
`installer/windows-guest/templates/domain.xml.j2` — il n'existe plus de XML
de référence à recopier, le domaine est construit depuis le matériel détecté.
```

- [ ] **Step 6: Vérifier qu'il ne reste aucune référence vivante**

```bash
grep -rn "vm-template\|deployed-backup" --include='*.sh' --include='*.py' --include='*.md' . \
  | grep -v '^./CLAUDE.md' | grep -v '^./CHANGELOG.md'
```

Attendu : **aucune sortie**.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: supprimer vm-template.xml et le backup de deploiement

domain.py genere le XML depuis templates/domain.xml.j2 depuis le
sous-projet C ; plus rien ne lit configs/vm-template.xml. Les trois
references restantes pointaient vers un fichier que personne ne
maintenait plus."
```

---

### Task 2: `manifest.py` — parser et valider le contrat v1

Le manifeste est la seule chose que le moteur lit **avant** d'accepter d'exécuter du code d'un package. Tout ce qu'il doit trancher en amont — éligibilité, conflits, ligne de commande noyau — se décide ici. Le module refuse donc tout ce qu'il ne comprend pas entièrement, plutôt que de l'ignorer.

**Files:**
- Create: `installer/packages/__init__.py`
- Create: `installer/packages/manifest.py`
- Test: `scripts/tests/test_packages_manifest.py`
- Modify: `installer/webapp/requirements.txt`
- Modify: `installer/iso-build/config/hooks/normal/0500-nivuus-venv.hook.chroot`

**Interfaces:**
- Consumes: rien
- Produces:
  - `API_VERSION = "nivuus.dev/v1"`, `TIERS`, `HOOK_PHASES`, `MANIFEST_NAME`
  - `class ManifestError(RuntimeError)`
  - `@dataclass(frozen=True) Platform(kernel_cmdline: tuple[str,...], modules: tuple[str,...], hugepages_mib: int)` avec `.merge(other) -> Platform`
  - `@dataclass(frozen=True) Manifest` : `name, version, label, tier, root, capabilities, features, claims: tuple[tuple[str,str],...], platform, apt, questions_file, hooks: tuple[tuple[str,str],...]`, méthode `.hook_path(phase) -> str`
  - `parse_manifest(data: Any, root: str) -> Manifest`
  - `load_manifest(path: str) -> Manifest`

- [ ] **Step 1: Ajouter PyYAML aux dépendances — les deux fichiers**

Depuis l'adoption du socle CI partagé (`dbc63c5`) il y a **deux** manifestes de dépendances, et les oublier l'un ou l'autre casse un côté sans toucher l'autre.

Dans `requirements.txt` à la racine (consommé par la CI), en gardant l'ordre alphabétique :

```
fastapi
jinja2
pydantic
pywinrm
pyyaml
uvicorn
```

Dans `installer/webapp/requirements.txt` (consommé par le venv de l'ISO), après la ligne `jinja2>=3.1` :

```
pyyaml>=6.0
```

Dans `installer/iso-build/config/hooks/normal/0500-nivuus-venv.hook.chroot`, remplacer le bloc de vérification par :

```sh
"$VENV/bin/python3" - <<'PY'
import fastapi, uvicorn, pydantic, jinja2, yaml
assert pydantic.VERSION.startswith("2"), f"need pydantic v2, got {pydantic.VERSION}"
print("venv OK:", fastapi.__version__, pydantic.VERSION, "pyyaml", yaml.__version__)
PY
```

- [ ] **Step 2: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_manifest.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/manifest.py - the nivuus.dev/v1 contract.

The manifest is the only thing the engine reads before agreeing to run any
of a package's code, so every check here guards a decision the engine makes
BEFORE execution: eligibility, conflicts, kernel command line. A manifest it
cannot fully understand must be refused, never partially honoured.

Run: python3 scripts/tests/test_packages_manifest.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.manifest import (  # noqa: E402
    API_VERSION, ManifestError, Platform, load_manifest, parse_manifest,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, fn, needle):
    try:
        fn()
    except ManifestError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected ManifestError, none raised")


MINIMAL = {
    "apiVersion": API_VERSION,
    "name": "demo",
    "version": "1.0.0",
    "label": "Demo",
    "tier": "userspace",
}

m = parse_manifest(dict(MINIMAL), "/pkg/demo")
check("minimal name", m.name, "demo")
check("minimal tier", m.tier, "userspace")
check("minimal platform is empty", m.platform, Platform())
check("minimal has no hooks", m.hook_path("resolve"), "")

# apiVersion is the whole point of versioning the contract.
check_raises("wrong apiVersion refused",
             lambda: parse_manifest({**MINIMAL, "apiVersion": "nivuus.dev/v2"},
                                    "/pkg/demo"),
             "apiVersion")

# The name becomes a directory and a systemd instance name.
check_raises("name with a slash refused",
             lambda: parse_manifest({**MINIMAL, "name": "de/mo"}, "/pkg/demo"),
             "must match")
check_raises("version not semver refused",
             lambda: parse_manifest({**MINIMAL, "version": "1.0"}, "/pkg/demo"),
             "MAJOR.MINOR.PATCH")
check_raises("unknown tier refused",
             lambda: parse_manifest({**MINIMAL, "tier": "kernel"}, "/pkg/demo"),
             "tier must be one of")

# THE tier rule: userspace must be REFUSED, not silently stripped.
check_raises("userspace declaring kernel-cmdline refused",
             lambda: parse_manifest(
                 {**MINIMAL, "platform": {"kernel-cmdline": ["quiet"]}},
                 "/pkg/demo"),
             "cannot declare")
check_raises("userspace declaring hugepages refused",
             lambda: parse_manifest(
                 {**MINIMAL, "platform": {"hugepages-mib": 1024}}, "/pkg/demo"),
             "cannot declare")

full = parse_manifest({
    **MINIMAL,
    "tier": "platform",
    "requires": {"capabilities": ["iommu"], "features": ["networking"]},
    "claims": {"gpu": "exclusive"},
    "platform": {"kernel-cmdline": ["intel_iommu=on"],
                 "modules": ["vfio_pci"], "hugepages-mib": 16384},
    "apt": ["qemu-kvm"],
    "wizard": {"questions": "wizard.yaml"},
    "hooks": {"resolve": "hooks/resolve.py", "install": "hooks/install.py"},
}, "/pkg/demo")
check("platform tier keeps cmdline", full.platform.kernel_cmdline,
      ("intel_iommu=on",))
check("platform tier keeps hugepages", full.platform.hugepages_mib, 16384)
check("capabilities parsed", full.capabilities, ("iommu",))
check("claims parsed as pairs", full.claims, (("gpu", "exclusive"),))
check("hook path is joined under root", full.hook_path("resolve"),
      "/pkg/demo/hooks/resolve.py")
check("absent hook returns empty", full.hook_path("activate"), "")

check_raises("unknown claim mode refused",
             lambda: parse_manifest({**MINIMAL, "claims": {"gpu": "shared"}},
                                    "/pkg/demo"),
             "mode must be one of")
check_raises("unknown hook phase refused",
             lambda: parse_manifest({**MINIMAL, "hooks": {"teardown": "x.py"}},
                                    "/pkg/demo"),
             "unknown hook phase")

# A hook path escaping the package directory is an execution vector.
check_raises("hook escaping the package dir refused",
             lambda: parse_manifest(
                 {**MINIMAL, "hooks": {"install": "../../evil.py"}}, "/pkg/demo"),
             "inside the package directory")
check_raises("absolute hook path refused",
             lambda: parse_manifest(
                 {**MINIMAL, "hooks": {"install": "/tmp/evil.py"}}, "/pkg/demo"),
             "inside the package directory")

# Platform.merge: resolve completes what the static declaration cannot know.
merged = Platform(("a",), ("m1",), 0).merge(Platform(("b", "a"), (), 8192))
check("merge concatenates and dedups cmdline", merged.kernel_cmdline, ("a", "b"))
check("merge keeps static modules", merged.modules, ("m1",))
check("resolved hugepages win", merged.hugepages_mib, 8192)

# load_manifest end to end, including the YAML error path.
with tempfile.TemporaryDirectory() as tmp:
    d = pathlib.Path(tmp)
    (d / "nivuus-package.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\n")
    loaded = load_manifest(str(d / "nivuus-package.yaml"))
    check("load_manifest reads from disk", loaded.name, "demo")
    check("root is the manifest's directory", loaded.root, str(d))

    (d / "broken.yaml").write_text("apiVersion: [unclosed\n")
    check_raises("invalid YAML refused",
                 lambda: load_manifest(str(d / "broken.yaml")), "invalid YAML")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all manifest contract tests passed")
```

- [ ] **Step 3: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_manifest.py
```

Attendu : `ModuleNotFoundError: No module named 'packages'`

- [ ] **Step 4: Écrire l'implémentation**

Créer `installer/packages/__init__.py` :

```python
"""Nivuus package engine: manifest contract, discovery, conflicts, hooks."""
```

Créer `installer/packages/manifest.py` :

```python
"""Parsing and validation of nivuus-package.yaml (contract nivuus.dev/v1).

A manifest is the ONLY thing the engine reads before it agrees to run any of
a package's code. Everything the engine must decide up front - is this package
eligible, does it conflict with another, what will it add to the kernel command
line - is decided from here. So this module refuses anything it cannot fully
understand rather than ignoring it: a key it does not recognise in a position
where it matters is an error, never a silent drop.

Two tiers exist. `userspace` may only add apt packages, services and
configuration; declaring kernel-cmdline, modules or hugepages-mib at that tier
is a manifest error. `platform` may declare them, and the wizard then shows the
resolved kernel command line verbatim before asking for a separate
confirmation - which is what makes it acceptable to hand the kernel command
line to a third party at all.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import yaml

API_VERSION = "nivuus.dev/v1"
TIERS = ("userspace", "platform")
HOOK_PHASES = ("resolve", "install", "activate")
CLAIM_MODES = ("exclusive",)
MANIFEST_NAME = "nivuus-package.yaml"

# A package name becomes a directory name and a systemd unit instance name.
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Keys under `platform:` that only a platform-tier package may declare.
PLATFORM_KEYS = ("kernel-cmdline", "modules", "hugepages-mib")


class ManifestError(RuntimeError):
    """Raised when a manifest cannot be parsed or violates the contract."""


def _dedup(items) -> tuple:
    """De-duplicate while preserving first-seen order."""
    return tuple(dict.fromkeys(items))


@dataclass(frozen=True)
class Platform:
    kernel_cmdline: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    hugepages_mib: int = 0

    def merge(self, other: "Platform") -> "Platform":
        """Static declaration merged with what `resolve` returned.

        The resolved side wins on hugepages because only it knows the real RAM;
        the two lists concatenate, de-duplicated, order preserved.
        """
        return Platform(
            kernel_cmdline=_dedup(self.kernel_cmdline + other.kernel_cmdline),
            modules=_dedup(self.modules + other.modules),
            hugepages_mib=other.hugepages_mib or self.hugepages_mib,
        )


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    label: str
    tier: str
    root: str
    capabilities: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    # Pairs rather than dicts: a frozen dataclass must stay hashable.
    claims: tuple[tuple[str, str], ...] = ()
    platform: Platform = Platform()
    apt: tuple[str, ...] = ()
    questions_file: str = ""
    hooks: tuple[tuple[str, str], ...] = ()

    def hook_path(self, phase: str) -> str:
        """Absolute path of the hook for `phase`, or "" when none is declared."""
        for declared, rel in self.hooks:
            if declared == phase:
                return os.path.join(self.root, rel)
        return ""


def _require(data: dict, key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(
            f"{what}: '{key}' is required and must be a non-empty string")
    return value.strip()


def _str_list(data: dict, key: str, what: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{what}: '{key}' must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def _safe_relpath(rel: str, what: str) -> str:
    """Refuse any path that could execute code outside the package directory."""
    if os.path.isabs(rel) or os.path.normpath(rel).startswith(".."):
        raise ManifestError(
            f"{what}: {rel!r} must be a relative path inside the package directory")
    return os.path.normpath(rel)


def _parse_platform(raw: Any, tier: str, what: str) -> Platform:
    if not isinstance(raw, dict):
        raise ManifestError(f"{what}: 'platform' must be a mapping")
    declared = [k for k in PLATFORM_KEYS if raw.get(k)]
    if declared and tier != "platform":
        raise ManifestError(
            f"{what}: tier 'userspace' cannot declare {declared} under "
            "'platform:'; use tier 'platform' if the package really touches "
            "the boot chain, so the wizard asks for its own confirmation")
    hugepages = raw.get("hugepages-mib") or 0
    if isinstance(hugepages, bool) or not isinstance(hugepages, int) or hugepages < 0:
        raise ManifestError(f"{what}: 'hugepages-mib' must be a non-negative integer")
    return Platform(
        kernel_cmdline=_str_list(raw, "kernel-cmdline", what),
        modules=_str_list(raw, "modules", what),
        hugepages_mib=hugepages,
    )


def parse_manifest(data: Any, root: str) -> Manifest:
    """Validate an already-loaded manifest mapping. `root` is its directory."""
    what = os.path.join(root, MANIFEST_NAME)
    if not isinstance(data, dict):
        raise ManifestError(f"{what}: top level must be a mapping")

    api = data.get("apiVersion")
    if api != API_VERSION:
        raise ManifestError(
            f"{what}: apiVersion must be {API_VERSION!r}, got {api!r}")

    name = _require(data, "name", what)
    if not NAME_RE.match(name):
        raise ManifestError(
            f"{what}: name {name!r} must match {NAME_RE.pattern} - it becomes "
            "a directory name and a systemd unit instance name")
    version = _require(data, "version", what)
    if not VERSION_RE.match(version):
        raise ManifestError(f"{what}: version {version!r} must be MAJOR.MINOR.PATCH")
    label = _require(data, "label", what)

    tier = _require(data, "tier", what)
    if tier not in TIERS:
        raise ManifestError(f"{what}: tier must be one of {TIERS}, got {tier!r}")

    requires = data.get("requires") or {}
    if not isinstance(requires, dict):
        raise ManifestError(f"{what}: 'requires' must be a mapping")

    claims_raw = data.get("claims") or {}
    if not isinstance(claims_raw, dict):
        raise ManifestError(f"{what}: 'claims' must be a mapping")
    claims = []
    for resource, mode in sorted(claims_raw.items()):
        if mode not in CLAIM_MODES:
            raise ManifestError(
                f"{what}: claim {resource!r} mode must be one of {CLAIM_MODES}, "
                f"got {mode!r}")
        claims.append((str(resource), str(mode)))

    wizard = data.get("wizard") or {}
    if not isinstance(wizard, dict):
        raise ManifestError(f"{what}: 'wizard' must be a mapping")
    questions_file = (_safe_relpath(str(wizard["questions"]), what)
                      if wizard.get("questions") else "")

    hooks_raw = data.get("hooks") or {}
    if not isinstance(hooks_raw, dict):
        raise ManifestError(f"{what}: 'hooks' must be a mapping")
    hooks = []
    for phase, rel in sorted(hooks_raw.items()):
        if phase not in HOOK_PHASES:
            raise ManifestError(
                f"{what}: unknown hook phase {phase!r}; expected {HOOK_PHASES}")
        hooks.append((phase, _safe_relpath(str(rel), what)))

    return Manifest(
        name=name, version=version, label=label, tier=tier, root=root,
        capabilities=_str_list(requires, "capabilities", what),
        features=_str_list(requires, "features", what),
        claims=tuple(claims),
        platform=_parse_platform(data.get("platform") or {}, tier, what),
        apt=_str_list(data, "apt", what),
        questions_file=questions_file,
        hooks=tuple(hooks),
    )


def load_manifest(path: str) -> Manifest:
    """Read and validate a nivuus-package.yaml from disk."""
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ManifestError(f"{path}: cannot be read ({exc})") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML ({exc})") from exc
    return parse_manifest(data, os.path.dirname(os.path.abspath(path)))
```

- [ ] **Step 5: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_manifest.py
```

Attendu : `OK - all manifest contract tests passed`

- [ ] **Step 6: Commit**

```bash
git add installer/packages/ scripts/tests/test_packages_manifest.py \
        requirements.txt installer/webapp/requirements.txt \
        installer/iso-build/config/hooks/normal/0500-nivuus-venv.hook.chroot
git commit -m "feat(packages): contrat de manifeste nivuus.dev/v1

Le manifeste est la seule chose que le moteur lit avant d accepter
d executer du code d un package : eligibilite, conflits et ligne de
commande noyau s en deduisent. Il refuse donc tout ce qu il ne
comprend pas entierement.

La regle des tiers est un refus, pas un filtrage : un package
userspace qui declare kernel-cmdline fait echouer le manifeste,
plutot que de voir sa cle disparaitre sans bruit.

Un chemin de hook qui sort du repertoire du package est refuse -
c est un vecteur d execution."
```

---

### Task 3: `capabilities.py` — la détection grossière que le moteur garde

Le moteur doit évaluer `requires.capabilities` **avant** d'exécuter le moindre hook ; il ne peut donc pas déléguer cette réponse au package. Il détecte un vocabulaire grossier et générique, utile à n'importe quel tiers. Le précis reste au package, en phase `resolve`.

**Le piège central** : `iommu` ne peut pas signifier « l'IOMMU est actif ». L'ISO live démarre **sans** `intel_iommu=on` — c'est précisément ce que le package va demander d'ajouter. La capacité doit donc dire « la plateforme le supporte », ce qui se lit dans les tables ACPI du firmware (`DMAR` chez Intel, `IVRS` chez AMD), indépendamment de la ligne de commande courante.

**Files:**
- Create: `installer/packages/capabilities.py`
- Modify: `installer/common/hardware.py` (ajouter `iommu_support()` et l'exposer dans `detect_all()`)
- Test: `scripts/tests/test_packages_capabilities.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `KNOWN_CAPABILITIES: tuple[str, ...]` = `("iommu", "gpu-discrete", "nvme-dedicated", "cpu-hybrid")`
  - `detect_capabilities(hw: dict, target_disk: str = "") -> set[str]`
  - `hardware.iommu_support(acpi_dir: str = "/sys/firmware/acpi/tables") -> dict` → `{"supported": bool, "vendor": "intel"|"amd"|"", "active": bool}`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_capabilities.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/capabilities.py and hardware.iommu_support().

The engine must answer `requires.capabilities` BEFORE running any hook, so
this detection is the one piece of hardware knowledge that cannot move to a
package. It stays deliberately coarse: the precise work (which PCI functions,
which IOMMU group, which vfio-pci.ids) belongs to the package's resolve phase.

The IOMMU check is the subtle one. The live ISO boots WITHOUT intel_iommu=on -
adding it is exactly what a platform package asks for - so "iommu" must mean
"the firmware advertises it", read from the ACPI tables, not "it is on now".

Run: python3 scripts/tests/test_packages_capabilities.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from common import hardware  # noqa: E402
from packages.capabilities import (  # noqa: E402
    KNOWN_CAPABILITIES, detect_capabilities,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- hardware.iommu_support: read the firmware, not the cmdline ------------ #
with tempfile.TemporaryDirectory() as tmp:
    tables = pathlib.Path(tmp)
    check("no ACPI table means unsupported",
          hardware.iommu_support(str(tables))["supported"], False)

    (tables / "DMAR").write_bytes(b"DMAR")
    intel = hardware.iommu_support(str(tables))
    check("DMAR means supported", intel["supported"], True)
    check("DMAR means intel", intel["vendor"], "intel")

with tempfile.TemporaryDirectory() as tmp:
    tables = pathlib.Path(tmp)
    (tables / "IVRS").write_bytes(b"IVRS")
    amd = hardware.iommu_support(str(tables))
    check("IVRS means supported", amd["supported"], True)
    check("IVRS means amd", amd["vendor"], "amd")

# A missing directory must not raise - detection is fail-open everywhere else.
check("missing ACPI dir is not fatal",
      hardware.iommu_support("/nonexistent/acpi")["supported"], False)

# --- detect_capabilities --------------------------------------------------- #
EMPTY = {"disks": [], "gpus": [], "cpu": {}, "iommu": {"supported": False}}
check("nothing detected yields nothing", detect_capabilities(EMPTY), set())

hw = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [
        {"slot": "00:02.0", "vendor": "intel", "discrete": False, "ids": []},
        {"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
         "ids": ["10de:2786", "10de:22bc"]},
    ],
    "disks": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "transport": "nvme"},
        {"name": "nvme1n1", "path": "/dev/nvme1n1", "transport": "nvme"},
        {"name": "sda", "path": "/dev/sda", "transport": "sata"},
    ],
    "cpu": {"hybrid": True, "performance_cpus": [0, 1, 2, 3]},
}

caps = detect_capabilities(hw, target_disk="/dev/nvme0n1")
check("iommu supported even though inactive", "iommu" in caps, True)
check("discrete gpu detected", "gpu-discrete" in caps, True)
check("hybrid cpu detected", "cpu-hybrid" in caps, True)
check("the second nvme is free for a package", "nvme-dedicated" in caps, True)

# With only one NVMe and it being the install target, nothing is left over.
one_nvme = {**hw, "disks": [{"name": "nvme0n1", "path": "/dev/nvme0n1",
                             "transport": "nvme"}]}
check("the install target does not count as dedicated",
      "nvme-dedicated" in detect_capabilities(one_nvme, "/dev/nvme0n1"), False)
check("without a target every nvme counts",
      "nvme-dedicated" in detect_capabilities(one_nvme), True)

# An iGPU alone is not a passthrough candidate.
igpu_only = {**hw, "gpus": [{"slot": "00:02.0", "vendor": "intel",
                             "discrete": False, "ids": []}]}
check("integrated gpu is not gpu-discrete",
      "gpu-discrete" in detect_capabilities(igpu_only), False)

check("every emitted capability is a known one",
      caps - set(KNOWN_CAPABILITIES), set())

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all capability detection tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_capabilities.py
```

Attendu : `ImportError: cannot import name 'iommu_support' from 'common.hardware'`

- [ ] **Step 3: Ajouter `iommu_support()` à `hardware.py`**

Insérer dans `installer/common/hardware.py`, juste avant `def cpu_topology()` :

```python
# --------------------------------------------------------------------------- #
# Platform capabilities                                                       #
# --------------------------------------------------------------------------- #
# Firmware tables that advertise an IOMMU: DMAR is Intel VT-d, IVRS is AMD-Vi.
IOMMU_TABLES = {"DMAR": "intel", "IVRS": "amd"}


def iommu_support(acpi_dir: str = "/sys/firmware/acpi/tables") -> dict:
    """Report whether the PLATFORM has an IOMMU, not whether it is enabled.

    The live installer boots without intel_iommu=on - turning it on is exactly
    what a passthrough package asks the engine to add to the kernel command
    line. So a check based on /sys/kernel/iommu_groups would answer "no" on
    every machine that is in fact capable, and no such package would ever be
    offered. The firmware tables answer the right question: they are present
    whenever the chipset advertises an IOMMU, whatever the kernel was told.

    `active` is reported separately, for diagnostics only. Never gate on it.
    """
    vendor = ""
    try:
        present = set(os.listdir(acpi_dir))
    except OSError:
        present = set()
    for table, table_vendor in IOMMU_TABLES.items():
        if table in present:
            vendor = table_vendor
            break
    active = False
    try:
        active = bool(os.listdir("/sys/kernel/iommu_groups"))
    except OSError:
        pass
    return {"supported": bool(vendor), "vendor": vendor, "active": active}
```

Puis ajouter la clé dans `detect_all()` :

```python
def detect_all() -> dict:
    """One-shot hardware snapshot for the web wizard."""
    gpus = list_gpus()
    return {
        "disks": list_disks(),
        "ethernet": list_ethernet(),
        "wifi": list_wifi(),
        "gpus": gpus,
        "cpu": cpu_topology(),
        "iommu": iommu_support(),
        "passthrough_candidates": [g for g in gpus if g.get("discrete")],
    }
```

- [ ] **Step 4: Écrire `capabilities.py`**

Créer `installer/packages/capabilities.py` :

```python
"""Coarse hardware capabilities, the vocabulary of `requires.capabilities`.

This is the one piece of hardware knowledge that cannot move into a package.
The engine must decide whether a package is even offered BEFORE running any of
its code, so it cannot ask the package. What it detects is therefore
deliberately coarse and generic - useful to any third party, and never enough
on its own to configure anything.

The precise work stays with the package, in its `resolve` phase: which PCI
functions share the slot, which IOMMU group they land in, which vfio-pci.ids
to emit, how many CPUs to hand over. The package refines; it does not redo.
"""
from __future__ import annotations

KNOWN_CAPABILITIES = ("iommu", "gpu-discrete", "nvme-dedicated", "cpu-hybrid")


def detect_capabilities(hw: dict, target_disk: str = "") -> set[str]:
    """Capabilities implied by a `hardware.detect_all()` snapshot.

    `target_disk` is the install target chosen in the wizard: the disk being
    installed onto can never be the dedicated disk a package claims, so it is
    excluded. An empty value means "not chosen yet" and excludes nothing.
    """
    caps: set[str] = set()

    if (hw.get("iommu") or {}).get("supported"):
        caps.add("iommu")

    if any(gpu.get("discrete") for gpu in hw.get("gpus") or []):
        caps.add("gpu-discrete")

    spare_nvme = [
        disk for disk in hw.get("disks") or []
        if (disk.get("transport") or "").lower() == "nvme"
        and disk.get("path") != target_disk
    ]
    if spare_nvme:
        caps.add("nvme-dedicated")

    if (hw.get("cpu") or {}).get("hybrid"):
        caps.add("cpu-hybrid")

    return caps
```

- [ ] **Step 5: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_capabilities.py
```

Attendu : `OK - all capability detection tests passed`

- [ ] **Step 6: Vérifier qu'aucun test existant ne casse**

```bash
python3 scripts/tests/test_windows_guest_hardware.py
python3 scripts/tests/test_install_engine_features.py
python3 scripts/tests/test_webapp_models.py
```

Attendu : trois `OK - …`. `detect_all()` gagne une clé ; aucun test existant n'affirme la liste exacte de ses clés, mais on le vérifie plutôt que de le supposer.

- [ ] **Step 7: Commit**

```bash
git add installer/packages/capabilities.py installer/common/hardware.py \
        scripts/tests/test_packages_capabilities.py
git commit -m "feat(packages): detection de capacites, et iommu lu dans l ACPI

Le moteur doit repondre a requires.capabilities avant d executer le
moindre hook : il ne peut pas deleguer cette reponse au package. Il
detecte donc un vocabulaire grossier, generique, jamais suffisant a
lui seul pour configurer quoi que ce soit.

Le piege est iommu. L ISO live demarre SANS intel_iommu=on - c est
precisement ce qu un package de passthrough demande d ajouter - donc
un test sur /sys/kernel/iommu_groups repondrait non sur toutes les
machines capables, et aucun package de ce type ne serait jamais
propose. Les tables ACPI DMAR/IVRS repondent a la bonne question :
elles sont la des que le chipset annonce un IOMMU, quoi qu on ait dit
au noyau."
```

---

### Task 4: `discovery.py` — trouver les manifestes et filtrer l'éligibilité

**Files:**
- Create: `installer/packages/discovery.py`
- Test: `scripts/tests/test_packages_discovery.py`

**Interfaces:**
- Consumes: `manifest.load_manifest`, `manifest.Manifest`, `manifest.MANIFEST_NAME`, `manifest.ManifestError`
- Produces:
  - `PACKAGES_DIR: str` (depuis `NIVUUS_PACKAGES_DIR`, défaut `/opt/nivuus-packages`)
  - `discover(root: str = PACKAGES_DIR) -> tuple[list[Manifest], list[tuple[str, str]]]` → `(manifestes valides triés par nom, [(chemin, message d'erreur)])`
  - `eligibility(m: Manifest, capabilities: set[str], features: set[str]) -> str` → `""` si éligible, sinon la raison
  - `partition(manifests, capabilities, features) -> tuple[list[Manifest], list[tuple[Manifest, str]]]`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_discovery.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/discovery.py.

Discovery is fail-soft by design: one broken third-party manifest must not
make the whole wizard unusable. It is reported alongside the valid ones, never
raised, so the operator sees "this package is broken, here is why" instead of
an installer that refuses to start.

Eligibility, by contrast, is strict and explains itself: an ineligible package
carries the reason it is ineligible, because "not shown" with no explanation
is the worst possible answer on a machine with no screen.

Run: python3 scripts/tests/test_packages_discovery.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.discovery import discover, eligibility, partition  # noqa: E402
from packages.manifest import API_VERSION, parse_manifest  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def write_pkg(root: pathlib.Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "nivuus-package.yaml").write_text(body)


GOOD = """apiVersion: {api}
name: {name}
version: 1.0.0
label: "{name}"
tier: userspace
"""

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    write_pkg(root, "bravo", GOOD.format(api=API_VERSION, name="bravo"))
    write_pkg(root, "alpha", GOOD.format(api=API_VERSION, name="alpha"))
    write_pkg(root, "broken", "apiVersion: nivuus.dev/v99\nname: broken\n")
    # A directory with no manifest at all is simply not a package.
    (root / "notapkg").mkdir()

    found, errors = discover(str(root))
    check("valid packages found", [m.name for m in found], ["alpha", "bravo"])
    check("one broken manifest reported", len(errors), 1)
    check("the error names the offending path", "broken" in errors[0][0], True)
    check("the error explains itself", "apiVersion" in errors[0][1], True)

check("a missing packages dir is empty, not an error",
      discover("/nonexistent/packages"), ([], []))

# --- eligibility ----------------------------------------------------------- #
base = {"apiVersion": API_VERSION, "name": "demo", "version": "1.0.0",
        "label": "Demo", "tier": "platform"}

needs_iommu = parse_manifest(
    {**base, "requires": {"capabilities": ["iommu"], "features": ["networking"]}},
    "/pkg/demo")

check("eligible when everything is present",
      eligibility(needs_iommu, {"iommu"}, {"networking"}), "")

missing_cap = eligibility(needs_iommu, set(), {"networking"})
check("missing capability is refused", missing_cap != "", True)
check("the reason names the capability", "iommu" in missing_cap, True)

missing_feat = eligibility(needs_iommu, {"iommu"}, set())
check("missing feature is refused", missing_feat != "", True)
check("the reason names the feature", "networking" in missing_feat, True)

free = parse_manifest(dict(base), "/pkg/demo")
check("a package requiring nothing is always eligible",
      eligibility(free, set(), set()), "")

ok, rejected = partition([needs_iommu, free], {"iommu"}, set())
check("partition keeps the eligible one", [m.name for m in ok], ["demo"])
check("partition returns the rejected with its reason", len(rejected), 1)
check("rejected carries a non-empty reason", rejected[0][1] != "", True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all discovery tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_discovery.py
```

Attendu : `ModuleNotFoundError: No module named 'packages.discovery'`

- [ ] **Step 3: Écrire `discovery.py`**

Créer `installer/packages/discovery.py` :

```python
"""Finding package manifests, and deciding which ones may be offered.

Discovery is fail-SOFT: one broken third-party manifest must not make the
whole wizard unusable. Broken manifests are returned alongside the valid ones
so the portal can say "this package is broken, here is why" - an installer
that refuses to start because a stranger shipped bad YAML would be worse than
useless on a machine with no screen.

Eligibility is the opposite: strict, and it always explains itself. A package
that is silently absent from the wizard is indistinguishable from one that was
never installed, so an ineligible package travels with the reason it is
ineligible.
"""
from __future__ import annotations

import os

from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest

# Overridable so tests and dev runs never touch the real payload directory,
# exactly like NIVUUS_PROGRESS_DIR does for the progress protocol.
PACKAGES_DIR = os.environ.get("NIVUUS_PACKAGES_DIR", "/opt/nivuus-packages")


def discover(root: str = PACKAGES_DIR) -> tuple[list[Manifest], list[tuple[str, str]]]:
    """Load every manifest under `root`.

    Returns (valid manifests sorted by name, [(path, error message)]).
    A directory with no manifest is not a package and is skipped in silence;
    a directory WITH a manifest that does not parse is an error worth showing.
    """
    manifests: list[Manifest] = []
    errors: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return [], []

    for entry in entries:
        path = os.path.join(root, entry, MANIFEST_NAME)
        if not os.path.isfile(path):
            continue
        try:
            manifests.append(load_manifest(path))
        except ManifestError as exc:
            errors.append((path, str(exc)))

    manifests.sort(key=lambda m: m.name)
    return manifests, errors


def eligibility(manifest: Manifest, capabilities: set[str],
                features: set[str]) -> str:
    """"" when the package may be offered, otherwise the reason it may not."""
    missing_caps = [c for c in manifest.capabilities if c not in capabilities]
    if missing_caps:
        return ("matériel requis absent : " + ", ".join(sorted(missing_caps)))
    missing_features = [f for f in manifest.features if f not in features]
    if missing_features:
        return ("fonctionnalité requise non sélectionnée : "
                + ", ".join(sorted(missing_features)))
    return ""


def partition(manifests, capabilities: set[str], features: set[str]):
    """Split manifests into (eligible, [(manifest, reason)])."""
    eligible: list[Manifest] = []
    rejected: list[tuple[Manifest, str]] = []
    for manifest in manifests:
        reason = eligibility(manifest, capabilities, features)
        (rejected.append((manifest, reason)) if reason
         else eligible.append(manifest))
    return eligible, rejected
```

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_discovery.py
```

Attendu : `OK - all discovery tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/packages/discovery.py scripts/tests/test_packages_discovery.py
git commit -m "feat(packages): decouverte fail-soft et eligibilite qui s explique

Un manifeste tiers casse ne doit pas rendre le wizard inutilisable :
il est rendu a cote des valides, jamais leve, pour que le portail
puisse dire ce qui cloche.

L eligibilite fait l inverse : stricte, et elle porte toujours sa
raison. Un package absent sans explication est indistinguable d un
package jamais installe - c est la pire reponse possible sur une
machine sans ecran."
```

---

### Task 5: `conflicts.py` — deux packages ne peuvent pas réclamer le même GPU

**Files:**
- Create: `installer/packages/conflicts.py`
- Test: `scripts/tests/test_packages_conflicts.py`

**Interfaces:**
- Consumes: `manifest.Manifest`
- Produces:
  - `@dataclass(frozen=True) Conflict(resource: str, packages: tuple[str, ...])` avec `.message() -> str`
  - `check_conflicts(manifests) -> list[Conflict]`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_conflicts.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/conflicts.py.

A claim is how a package says "this piece of hardware is mine alone". The
case that matters on this very host: the gaming VM claims the GPU, and so
would a transcoding or local-inference package - they cannot both have it,
and the engine must say so BEFORE the install rather than let two sets of
libvirt hooks fight over the same card at runtime.

Run: python3 scripts/tests/test_packages_conflicts.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.conflicts import Conflict, check_conflicts  # noqa: E402
from packages.manifest import API_VERSION, parse_manifest  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def pkg(name, claims):
    return parse_manifest({
        "apiVersion": API_VERSION, "name": name, "version": "1.0.0",
        "label": name, "tier": "userspace", "claims": claims,
    }, f"/pkg/{name}")


console = pkg("console", {"gpu": "exclusive", "nvme": "exclusive"})
inference = pkg("inference", {"gpu": "exclusive"})
media = pkg("media", {})

check("no packages, no conflicts", check_conflicts([]), [])
check("a single claimer is fine", check_conflicts([console]), [])
check("claimless packages never conflict",
      check_conflicts([media, console]), [])

clash = check_conflicts([console, inference])
check("two exclusive claims on the GPU conflict", len(clash), 1)
check("the conflict names the resource", clash[0].resource, "gpu")
check("the conflict names both packages, sorted",
      clash[0].packages, ("console", "inference"))
check("the message names the resource and both packages",
      all(t in clash[0].message() for t in ("gpu", "console", "inference")),
      True)

# The nvme claim is held by console alone: it must NOT be reported.
check("only the contested resource is reported",
      [c.resource for c in clash], ["gpu"])

# Three claimers on one resource is one conflict, not three.
third = pkg("third", {"gpu": "exclusive"})
triple = check_conflicts([console, inference, third])
check("three claimers are one conflict", len(triple), 1)
check("all three are named", triple[0].packages,
      ("console", "inference", "third"))

# Conflicts are ordered by resource so the portal renders them stably.
disks = pkg("disks", {"nvme": "exclusive"})
both = check_conflicts([console, inference, disks])
check("conflicts are sorted by resource",
      [c.resource for c in both], ["gpu", "nvme"])

check("Conflict is hashable and comparable",
      Conflict("gpu", ("a", "b")) == Conflict("gpu", ("a", "b")), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all conflict detection tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_conflicts.py
```

Attendu : `ModuleNotFoundError: No module named 'packages.conflicts'`

- [ ] **Step 3: Écrire `conflicts.py`**

Créer `installer/packages/conflicts.py` :

```python
"""Exclusive hardware claims, and the conflicts they produce.

A claim is how a package says "this piece of hardware is mine alone". The
case this exists for is real and lives on the reference host: the gaming
console claims the GPU, and so would a transcoding or local-inference
package. They cannot both have it. Catching that at wizard time is the whole
point - the alternative is two sets of libvirt hooks fighting over the same
card at runtime, which is a class of failure that only shows up when someone
starts a game.

v1 knows exactly one mode, `exclusive`, and the manifest parser refuses any
other. Shared and counted claims are deliberately absent: nothing needs them
yet, and a resource model is easier to widen than to narrow.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Conflict:
    resource: str
    packages: tuple[str, ...]

    def message(self) -> str:
        names = ", ".join(self.packages)
        return (f"{len(self.packages)} packages réclament « {self.resource} » "
                f"de façon exclusive : {names}. Un seul peut être installé.")


def check_conflicts(manifests) -> list[Conflict]:
    """Every resource claimed exclusively by more than one of `manifests`.

    One conflict per contested resource, naming every claimant - not one per
    pair, which would report the same problem three times for three packages.
    Ordered by resource so the portal renders a stable list.
    """
    claimants: dict[str, list[str]] = {}
    for manifest in manifests:
        for resource, mode in manifest.claims:
            if mode == "exclusive":
                claimants.setdefault(resource, []).append(manifest.name)

    return [
        Conflict(resource=resource, packages=tuple(sorted(names)))
        for resource, names in sorted(claimants.items())
        if len(names) > 1
    ]
```

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_conflicts.py
```

Attendu : `OK - all conflict detection tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/packages/conflicts.py scripts/tests/test_packages_conflicts.py
git commit -m "feat(packages): conflits sur les ressources reclamees en exclusif

Le cas existe pour de vrai sur la machine de reference : la console
reclame le GPU, un package de transcodage ou d inference le
reclamerait aussi. Le detecter au moment du wizard evite deux jeux de
hooks libvirt qui se disputent la meme carte a l execution - une
panne qui n apparait qu au lancement d un jeu.

Un conflit par ressource contestee, nommant tous les pretendants,
plutot qu un par paire : trois packages produiraient sinon trois fois
le meme message."
```

---

### Task 6: `wizard.py` — le vocabulaire restreint de questions

Un package tiers ne doit pas pouvoir dessiner des formulaires arbitraires dans le portail. Six types suffisent, et `disque`/`gpu` ont besoin de la détection matérielle du moteur pour être rendus.

**Files:**
- Create: `installer/packages/wizard.py`
- Test: `scripts/tests/test_packages_wizard.py`

**Interfaces:**
- Consumes: `manifest.ManifestError`
- Produces:
  - `QUESTION_TYPES = ("bool", "choix", "texte", "secret", "disque", "gpu")`
  - `class WizardError(RuntimeError)`
  - `@dataclass(frozen=True) Question(key, type, label, default, choices: tuple[str,...], required: bool)` avec `.to_dict() -> dict` (le secret n'expose jamais de défaut)
  - `load_questions(path: str) -> list[Question]`
  - `validate_answers(questions, answers: dict) -> dict`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_wizard.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/wizard.py - the restricted question vocabulary.

A third-party package must not be able to draw arbitrary forms in the portal,
so the vocabulary is closed at six types rather than open at JSON Schema. Two
of them (disque, gpu) exist precisely because they need the ENGINE's hardware
detection to render at all - a package cannot draw its own disk picker.

Run: python3 scripts/tests/test_packages_wizard.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.wizard import (  # noqa: E402
    QUESTION_TYPES, Question, WizardError, load_questions, validate_answers,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, fn, needle):
    try:
        fn()
    except WizardError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected WizardError, none raised")


QUESTIONS_YAML = """- key: dedicated_disk
  type: disque
  label: "Disque dédié à la console"
  required: true
- key: admin_password
  type: secret
  label: "Mot de passe administrateur"
  required: true
- key: retro
  type: bool
  label: "Retrogaming"
  default: false
- key: edition
  type: choix
  label: "Édition"
  choices: [ltsc, pro]
  default: ltsc
"""

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "wizard.yaml"
    path.write_text(QUESTIONS_YAML)
    questions = load_questions(str(path))

check("all questions loaded", len(questions), 4)
check("keys preserved in order",
      [q.key for q in questions],
      ["dedicated_disk", "admin_password", "retro", "edition"])
check("choices parsed", questions[3].choices, ("ltsc", "pro"))
check("default parsed", questions[3].default, "ltsc")
check("required defaults to false", questions[2].required, False)
check("every type is a known one",
      {q.type for q in questions} - set(QUESTION_TYPES), set())

# A secret must never carry a default back to the browser.
secret_dict = questions[1].to_dict()
check("secret exposes no default", "default" in secret_dict, False)
check("non-secret exposes its default", questions[2].to_dict()["default"], False)

with tempfile.TemporaryDirectory() as tmp:
    bad = pathlib.Path(tmp) / "w.yaml"
    bad.write_text("- key: x\n  type: freeform\n  label: X\n")
    check_raises("unknown question type refused",
                 lambda: load_questions(str(bad)), "freeform")

    dup = pathlib.Path(tmp) / "dup.yaml"
    dup.write_text("- key: x\n  type: bool\n  label: A\n"
                   "- key: x\n  type: bool\n  label: B\n")
    check_raises("duplicate key refused", lambda: load_questions(str(dup)),
                 "duplicate")

    nochoice = pathlib.Path(tmp) / "nc.yaml"
    nochoice.write_text("- key: x\n  type: choix\n  label: X\n")
    check_raises("choix without choices refused",
                 lambda: load_questions(str(nochoice)), "choices")

# --- validate_answers ------------------------------------------------------ #
answers = validate_answers(questions, {
    "dedicated_disk": "/dev/nvme1n1",
    "admin_password": "hunter2hunter2",
    "edition": "pro",
})
check("answers pass through", answers["dedicated_disk"], "/dev/nvme1n1")
check("an unanswered optional takes its default", answers["retro"], False)
check("an answered optional wins over its default", answers["edition"], "pro")

check_raises("a missing required answer is refused",
             lambda: validate_answers(questions, {"admin_password": "x"}),
             "dedicated_disk")
check_raises("a choix outside its choices is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "edition": "home"}),
             "edition")
check_raises("a non-bool for a bool question is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "retro": "oui"}),
             "retro")
check_raises("an unknown key is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "sournois": 1}),
             "sournois")

check("a package with no questions accepts nothing",
      validate_answers([], {}), {})
check_raises("a package with no questions refuses any answer",
             lambda: validate_answers([], {"x": 1}), "x")

check("Question is frozen and comparable",
      Question("a", "bool", "A", False, (), False)
      == Question("a", "bool", "A", False, (), False), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all wizard vocabulary tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_wizard.py
```

Attendu : `ModuleNotFoundError: No module named 'packages.wizard'`

- [ ] **Step 3: Écrire `wizard.py`**

Créer `installer/packages/wizard.py` :

```python
"""The restricted question vocabulary a package may add to the wizard.

Closed at six types rather than open at JSON Schema, on purpose. A third-party
package should not be able to draw arbitrary forms in the portal that the
operator is about to trust with their disk. And two of the six - `disque` and
`gpu` - exist precisely BECAUSE they need the engine's own hardware detection
to render: a package cannot draw a usable disk picker from inside its own
directory, and should not try.

Answers are validated here, once, before any of them reach a hook. A hook that
has to re-validate its own inputs is a hook that will forget to.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

QUESTION_TYPES = ("bool", "choix", "texte", "secret", "disque", "gpu")
# Types the ENGINE fills from its hardware detection; the answer is a device
# path or a PCI slot the operator picked from a list the package never saw.
HARDWARE_TYPES = ("disque", "gpu")


class WizardError(RuntimeError):
    """Raised when a question file or a set of answers violates the contract."""


@dataclass(frozen=True)
class Question:
    key: str
    type: str
    label: str
    default: object = None
    choices: tuple[str, ...] = ()
    required: bool = False

    def to_dict(self) -> dict:
        """Shape sent to the portal. A secret never carries a default back."""
        payload = {"key": self.key, "type": self.type, "label": self.label,
                   "required": self.required}
        if self.choices:
            payload["choices"] = list(self.choices)
        if self.type != "secret" and self.default is not None:
            payload["default"] = self.default
        return payload


def load_questions(path: str) -> list[Question]:
    """Read and validate a package's question file."""
    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh) or []
    except OSError as exc:
        raise WizardError(f"{path}: cannot be read ({exc})") from exc
    except yaml.YAMLError as exc:
        raise WizardError(f"{path}: invalid YAML ({exc})") from exc

    if not isinstance(raw, list):
        raise WizardError(f"{path}: must be a list of questions")

    questions: list[Question] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise WizardError(f"{path}: question #{index + 1} must be a mapping")
        key = str(item.get("key") or "").strip()
        if not key:
            raise WizardError(f"{path}: question #{index + 1} has no 'key'")
        if key in seen:
            raise WizardError(f"{path}: duplicate question key {key!r}")
        seen.add(key)

        qtype = str(item.get("type") or "").strip()
        if qtype not in QUESTION_TYPES:
            raise WizardError(
                f"{path}: question {key!r} has type {qtype!r}; expected one of "
                f"{QUESTION_TYPES}")

        label = str(item.get("label") or "").strip()
        if not label:
            raise WizardError(f"{path}: question {key!r} has no 'label'")

        choices = tuple(str(c) for c in (item.get("choices") or []))
        if qtype == "choix" and not choices:
            raise WizardError(
                f"{path}: question {key!r} is a 'choix' but declares no 'choices'")

        questions.append(Question(
            key=key, type=qtype, label=label, default=item.get("default"),
            choices=choices, required=bool(item.get("required", False)),
        ))
    return questions


def validate_answers(questions, answers: dict) -> dict:
    """Validate `answers` against `questions`, filling in defaults.

    Unknown keys are refused rather than dropped: an answer the engine does
    not understand means the portal and the package disagree about the
    contract, and silently discarding it would hide that.
    """
    by_key = {q.key: q for q in questions}
    unknown = sorted(set(answers) - set(by_key))
    if unknown:
        raise WizardError(f"unknown answer keys: {', '.join(unknown)}")

    validated: dict = {}
    for question in questions:
        if question.key not in answers:
            if question.required:
                raise WizardError(
                    f"question {question.key!r} is required and unanswered")
            if question.default is not None:
                validated[question.key] = question.default
            continue

        value = answers[question.key]
        if question.type == "bool" and not isinstance(value, bool):
            raise WizardError(
                f"question {question.key!r} expects true/false, got {value!r}")
        if question.type == "choix" and value not in question.choices:
            raise WizardError(
                f"question {question.key!r} expects one of {question.choices}, "
                f"got {value!r}")
        if question.type in ("texte", "secret") + HARDWARE_TYPES \
                and not isinstance(value, str):
            raise WizardError(
                f"question {question.key!r} expects a string, got {value!r}")
        validated[question.key] = value
    return validated
```

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_wizard.py
```

Attendu : `OK - all wizard vocabulary tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/packages/wizard.py scripts/tests/test_packages_wizard.py
git commit -m "feat(packages): vocabulaire de questions restreint a six types

Ferme a six types plutot qu ouvert a JSON Schema, volontairement : un
package tiers ne doit pas dessiner des formulaires arbitraires dans le
portail auquel l operateur s apprete a confier son disque. Et deux des
six - disque et gpu - existent precisement PARCE QU ils ont besoin de
la detection materielle du moteur pour s afficher.

Les reponses sont validees une fois, ici, avant d atteindre le moindre
hook : un hook qui doit revalider ses entrees est un hook qui oubliera
de le faire."
```

---

### Task 7: `runner.py` — les trois phases et le protocole jsonl

C'est la pièce qui exécute du code tiers. `resolve` est en lecture seule et c'est ce qui autorise à laisser `bootloader` là où il est.

**Files:**
- Create: `installer/packages/runner.py`
- Create: `scripts/tests/fixtures/packages/demo/nivuus-package.yaml`
- Create: `scripts/tests/fixtures/packages/demo/wizard.yaml`
- Create: `scripts/tests/fixtures/packages/demo/hooks/resolve.py`
- Create: `scripts/tests/fixtures/packages/demo/hooks/install.py`
- Create: `scripts/tests/fixtures/packages/refuser/nivuus-package.yaml`
- Create: `scripts/tests/fixtures/packages/refuser/hooks/resolve.py`
- Test: `scripts/tests/test_packages_runner.py`

**Interfaces:**
- Consumes: `manifest.Manifest`, `manifest.Platform`
- Produces:
  - `class HookError(RuntimeError)`
  - `@dataclass(frozen=True) Resolution(ok: bool, reason: str, platform: Platform)`
  - `run_resolve(manifest, hw: dict, answers: dict, emit=None) -> Resolution`
  - `run_install(manifest, hw, answers, root: str, emit=None) -> None`
  - `run_activate(manifest, hw, answers, emit=None) -> None`
  - Protocole : argv `--phase <phase>` (+ `--root <path>` en phase install), contexte JSON sur **stdin**, événements jsonl sur **stdout**.
  - Événements : `{"event":"progress","pct":int,"msg":str}`, `{"event":"platform","kernel-cmdline":[...],"modules":[...],"hugepages-mib":int}`, `{"event":"refuse","reason":str}`, `{"event":"done"}`

- [ ] **Step 1: Créer le package factice qui sert de preuve**

`scripts/tests/fixtures/packages/demo/nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: demo
version: 1.0.0
label: "Package de démonstration"
tier: platform

requires:
  capabilities: [iommu]

claims:
  gpu: exclusive

platform:
  modules: [vfio_pci]
  kernel-cmdline: ["intel_iommu=on"]

apt: [cowsay]

wizard:
  questions: wizard.yaml

hooks:
  resolve: hooks/resolve.py
  install: hooks/install.py
```

`scripts/tests/fixtures/packages/demo/wizard.yaml` :

```yaml
- key: greeting
  type: texte
  label: "Message"
  default: "bonjour"
```

`scripts/tests/fixtures/packages/demo/hooks/resolve.py` :

```python
#!/usr/bin/env python3
"""Demo resolve hook: read-only, returns the platform block it computed.

Mirrors what a real package does - derive from the hardware what the static
manifest cannot know - without touching anything.
"""
import json
import sys

ctx = json.load(sys.stdin)
gpus = [g for g in ctx["hw"].get("gpus", []) if g.get("discrete")]
ids = [i for g in gpus for i in g.get("ids", [])]

print(json.dumps({"event": "progress", "pct": 50, "msg": "resolving"}))
print(json.dumps({
    "event": "platform",
    "kernel-cmdline": [f"vfio-pci.ids={','.join(ids)}"] if ids else [],
    "modules": ["vfio_iommu_type1"],
    "hugepages-mib": 1024,
}))
print(json.dumps({"event": "done"}))
```

`scripts/tests/fixtures/packages/demo/hooks/install.py` :

```python
#!/usr/bin/env python3
"""Demo install hook: writes one marker under --root, proving it got the root."""
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

ctx = json.load(sys.stdin)
marker = os.path.join(args.root, "etc/nivuus-demo.json")
os.makedirs(os.path.dirname(marker), exist_ok=True)
with open(marker, "w") as fh:
    json.dump({"phase": args.phase, "answers": ctx["answers"]}, fh)

print(json.dumps({"event": "progress", "pct": 90, "msg": "marker written"}))
print(json.dumps({"event": "done"}))
```

`scripts/tests/fixtures/packages/refuser/nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: refuser
version: 1.0.0
label: "Package qui refuse"
tier: userspace

hooks:
  resolve: hooks/resolve.py
```

`scripts/tests/fixtures/packages/refuser/hooks/resolve.py` :

```python
#!/usr/bin/env python3
"""Demo refusal: this is how a package says no BEFORE anything is written."""
import json
import sys

json.load(sys.stdin)
print(json.dumps({"event": "refuse",
                  "reason": "aucun NVMe dédié correctement isolé"}))
```

- [ ] **Step 2: Écrire le test qui échoue**

Créer `scripts/tests/test_packages_runner.py` :

```python
#!/usr/bin/env python3
"""Tests for installer/packages/runner.py - executing third-party hooks.

`resolve` being read-only is what lets bootloader stay where it is in run.py:
the engine learns the exact kernel command line BEFORE partitioning, so it
never has to reorder the pipeline or rewrite GRUB after the fact.

A refusal is a first-class outcome, not an exception: "this machine has no
dedicated NVMe" must reach the operator as a sentence, before a single byte
is written to their disk.

Run: python3 scripts/tests/test_packages_runner.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"

from packages.manifest import load_manifest  # noqa: E402
from packages.runner import (  # noqa: E402
    HookError, run_activate, run_install, run_resolve,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(("info", step, pct, msg))

    def warn(self, step, pct, msg):
        self.lines.append(("warn", step, pct, msg))

    def error(self, step, pct, msg):
        self.lines.append(("error", step, pct, msg))


HW = {"gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
                "ids": ["10de:2786", "10de:22bc"]}]}

demo = load_manifest(str(FIXTURES / "demo" / "nivuus-package.yaml"))
emit = FakeEmit()
res = run_resolve(demo, HW, {"greeting": "salut"}, emit)

check("resolve succeeds", res.ok, True)
check("resolve has no reason when it succeeds", res.reason, "")
check("the static cmdline survives",
      "intel_iommu=on" in res.platform.kernel_cmdline, True)
check("the resolved cmdline is merged in",
      "vfio-pci.ids=10de:2786,10de:22bc" in res.platform.kernel_cmdline, True)
check("static and resolved modules are both present",
      set(res.platform.modules), {"vfio_pci", "vfio_iommu_type1"})
check("resolved hugepages win over the static 0",
      res.platform.hugepages_mib, 1024)
check("progress events reached the emitter",
      any("resolving" in line[3] for line in emit.lines), True)

# A refusal is data, not an exception.
refuser = load_manifest(str(FIXTURES / "refuser" / "nivuus-package.yaml"))
refused = run_resolve(refuser, HW, {})
check("a refusing package does not raise", refused.ok, False)
check("the refusal carries its reason",
      "NVMe" in refused.reason, True)

# A package with no resolve hook resolves to its static declaration.
import packages.manifest as manifest_mod  # noqa: E402
static_only = manifest_mod.parse_manifest({
    "apiVersion": manifest_mod.API_VERSION, "name": "static",
    "version": "1.0.0", "label": "Static", "tier": "platform",
    "platform": {"kernel-cmdline": ["quiet"]},
}, str(FIXTURES / "demo"))
res_static = run_resolve(static_only, HW, {})
check("no resolve hook still resolves", res_static.ok, True)
check("the static block is returned as is",
      res_static.platform.kernel_cmdline, ("quiet",))

# --- install: the hook must receive --root and write inside it ------------- #
with tempfile.TemporaryDirectory() as tmp:
    run_install(demo, HW, {"greeting": "salut"}, tmp)
    marker = pathlib.Path(tmp) / "etc" / "nivuus-demo.json"
    check("the install hook wrote under --root", marker.is_file(), True)
    import json
    written = json.loads(marker.read_text())
    check("the hook received the phase", written["phase"], "install")
    check("the hook received the answers", written["answers"]["greeting"], "salut")

# A package with no install hook is a no-op, not a failure.
with tempfile.TemporaryDirectory() as tmp:
    run_install(refuser, HW, {}, tmp)
    check("no install hook is a no-op",
          list(pathlib.Path(tmp).iterdir()), [])

# No activate hook anywhere here: it must also be a silent no-op.
run_activate(demo, HW, {})

# --- a hook that fails must raise, loudly ---------------------------------- #
with tempfile.TemporaryDirectory() as tmp:
    pkg = pathlib.Path(tmp) / "boom"
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "nivuus-package.yaml").write_text(
        f"apiVersion: {manifest_mod.API_VERSION}\nname: boom\nversion: 1.0.0\n"
        'label: "Boom"\ntier: userspace\nhooks:\n  install: hooks/install.py\n')
    (pkg / "hooks" / "install.py").write_text(
        "import sys\nsys.stderr.write('exploded\\n')\nsys.exit(3)\n")
    boom = load_manifest(str(pkg / "nivuus-package.yaml"))
    try:
        run_install(boom, HW, {}, tmp)
        failures.append("a failing hook did not raise HookError")
    except HookError as exc:
        check("the error names the package", "boom" in str(exc), True)
        check("the error carries the exit code", "3" in str(exc), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all hook runner tests passed")
```

- [ ] **Step 3: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_packages_runner.py
```

Attendu : `ModuleNotFoundError: No module named 'packages.runner'`

- [ ] **Step 4: Écrire `runner.py`**

Créer `installer/packages/runner.py` :

```python
"""Executing a package's hooks, and the jsonl protocol they speak.

`resolve` being READ-ONLY is the load-bearing property of this module. It runs
before the engine writes anything, so the exact kernel command line is known
before partitioning - which is why `bootloader` can stay exactly where it is in
run.py instead of being reordered after the features step.

The protocol is deliberately a subprocess speaking jsonl on stdout rather than
an imported Python API. A package must be able to run on a Debian that has
never seen this engine (the standalone path), so it cannot import from here;
and a stranger's code running in the installer's own process is a worse idea
than a pipe.

Events a hook may emit, one JSON object per line:
    {"event":"progress","pct":int,"msg":str}
    {"event":"platform","kernel-cmdline":[...],"modules":[...],
     "hugepages-mib":int}          - resolve only
    {"event":"refuse","reason":str}                  - resolve only
    {"event":"done"}
Anything else on stdout is relayed as a progress line rather than dropped: a
hook that prints is easier to debug than a hook that is silently truncated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .manifest import Manifest, Platform

# A hook is third-party code; it never gets to hang the install forever.
HOOK_TIMEOUT = {"resolve": 120, "install": 1800, "activate": 7200}


class HookError(RuntimeError):
    """Raised when a hook fails, times out, or speaks an unusable protocol."""


@dataclass(frozen=True)
class Resolution:
    ok: bool
    reason: str
    platform: Platform


def _context(manifest: Manifest, hw: dict, answers: dict) -> str:
    return json.dumps({
        "package": {"name": manifest.name, "version": manifest.version,
                    "root": manifest.root},
        "hw": hw,
        "answers": answers,
    })


def _run_hook(manifest: Manifest, phase: str, hw: dict, answers: dict,
              root: str = "", emit=None) -> list[dict]:
    """Run one hook and return the events it emitted. [] when none is declared."""
    hook = manifest.hook_path(phase)
    if not hook:
        return []
    if not os.path.isfile(hook):
        raise HookError(
            f"package {manifest.name}: hook '{phase}' declared but missing at {hook}")

    cmd = [sys.executable, hook, "--phase", phase]
    if root:
        cmd += ["--root", root]

    try:
        proc = subprocess.run(
            cmd, input=_context(manifest, hw, answers), capture_output=True,
            text=True, timeout=HOOK_TIMEOUT[phase], cwd=manifest.root, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HookError(
            f"package {manifest.name}: hook '{phase}' exceeded "
            f"{HOOK_TIMEOUT[phase]}s and was killed") from exc

    events: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Not protocol: relay it rather than drop it. A hook that prints
            # is far easier to debug than one whose output vanished.
            if emit:
                emit.info("packages", 0, f"[{manifest.name}] {line[:120]}")
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        if emit and event.get("event") == "progress":
            emit.info("packages", int(event.get("pct") or 0),
                      f"[{manifest.name}] {str(event.get('msg', ''))[:120]}")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise HookError(
            f"package {manifest.name}: hook '{phase}' exited {proc.returncode} "
            f"({tail})")
    return events


def run_resolve(manifest: Manifest, hw: dict, answers: dict,
                emit=None) -> Resolution:
    """Read-only phase. Returns the merged platform block, or a refusal."""
    events = _run_hook(manifest, "resolve", hw, answers, emit=emit)

    resolved = Platform()
    for event in events:
        kind = event.get("event")
        if kind == "refuse":
            reason = str(event.get("reason") or "").strip() \
                or "le package a refusé cette machine sans en donner la raison"
            return Resolution(ok=False, reason=reason, platform=manifest.platform)
        if kind == "platform":
            hugepages = event.get("hugepages-mib") or 0
            resolved = Platform(
                kernel_cmdline=tuple(str(v) for v in
                                     (event.get("kernel-cmdline") or [])),
                modules=tuple(str(v) for v in (event.get("modules") or [])),
                hugepages_mib=int(hugepages) if isinstance(hugepages, int) else 0,
            )
    return Resolution(ok=True, reason="",
                      platform=manifest.platform.merge(resolved))


def run_install(manifest: Manifest, hw: dict, answers: dict, root: str,
                emit=None) -> None:
    """Apply the package to the filesystem at `root`. Raises HookError."""
    _run_hook(manifest, "install", hw, answers, root=root, emit=emit)


def run_activate(manifest: Manifest, hw: dict, answers: dict, emit=None) -> None:
    """Post-reboot phase, on the live system with network. Raises HookError."""
    _run_hook(manifest, "activate", hw, answers, emit=emit)
```

- [ ] **Step 5: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_packages_runner.py
```

Attendu : `OK - all hook runner tests passed`

- [ ] **Step 6: Commit**

```bash
git add installer/packages/runner.py scripts/tests/test_packages_runner.py \
        scripts/tests/fixtures/
git commit -m "feat(packages): trois phases et protocole jsonl sur stdout

resolve est en lecture seule, et c est la propriete porteuse : il
tourne avant la moindre ecriture, donc la ligne de commande noyau
exacte est connue avant le partitionnement. C est ce qui permet de
laisser bootloader exactement ou il est dans run.py.

Le protocole est un sous-processus qui parle jsonl, pas une API Python
importee : un package doit pouvoir tourner sur un Debian qui n a jamais
vu ce moteur, donc il ne peut rien importer d ici - et faire tourner le
code d un tiers dans le processus de l installateur serait pire qu un
tube.

Un refus est une donnee, pas une exception : « aucun NVMe dedie » doit
atteindre l operateur sous forme de phrase, avant qu un octet ne soit
ecrit sur son disque."
```

---

### Task 8: `bootloader.py` accepte la ligne de commande des packages

Aujourd'hui `bootloader.py` écrit `GRUB_CMDLINE_LINUX_DEFAULT="quiet"` en dur, et son commentaire dit que « le pas features ajoute les paramètres Nivuus » — ce que fait `install.sh` par un `sed`. Le moteur doit pouvoir écrire la ligne complète, une fois, au bon moment.

**Files:**
- Modify: `installer/install-engine/steps/bootloader.py`
- Test: `scripts/tests/test_install_engine_bootloader.py`

**Interfaces:**
- Consumes: rien
- Produces: `install_bootloader(config, target, fs, emit, extra_cmdline: tuple[str, ...] = ()) -> None`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_install_engine_bootloader.py` :

```python
#!/usr/bin/env python3
"""Tests for the GRUB cmdline assembly in install-engine/steps/bootloader.py.

The packages engine collects kernel-cmdline from every selected package and
hands it here, so it is written ONCE, at the only sane moment - while GRUB is
being installed. That replaces install.sh's sed-after-the-fact, which could
only ever append and had to guard against appending twice.

Only the pure assembly function is exercised: install_bootloader() itself runs
apt and grub-install inside a chroot, which this sandbox has no way to provide.

Run: python3 scripts/tests/test_install_engine_bootloader.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps.bootloader import grub_defaults  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


plain = grub_defaults(())
check("without packages the default line is untouched",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet"' in plain, True)
check("the distributor stays Nivuus", 'GRUB_DISTRIBUTOR="Nivuus"' in plain, True)

withparams = grub_defaults(("intel_iommu=on", "iommu=pt", "nohz_full=0-15"))
check("package params are appended after quiet",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt nohz_full=0-15"'
      in withparams, True)

check("duplicates are collapsed",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1 b=2"'
      in grub_defaults(("a=1", "b=2", "a=1")), True)

check("blank entries are ignored",
      'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1"'
      in grub_defaults(("", "  ", "a=1")), True)

# A parameter carrying a double quote would break the shell-ish grub file.
try:
    grub_defaults(('bad="x"',))
    failures.append("a quoted parameter was accepted")
except ValueError as exc:
    check("the error names the offending parameter", "bad=" in str(exc), True)

check("the file always ends with a newline", plain.endswith("\n"), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all bootloader cmdline tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_install_engine_bootloader.py
```

Attendu : `ImportError: cannot import name 'grub_defaults'`

- [ ] **Step 3: Modifier `bootloader.py`**

Remplacer l'en-tête du module :

```python
"""Step 7: install the kernel and GRUB (UEFI) inside the chroot.

Kernel parameters contributed by packages arrive here already resolved, and are
written ONCE while GRUB is being installed. That is the only sane moment: it is
the one point where the file is created rather than edited, so there is nothing
to append to twice. install.sh used to sed them in afterwards and needed a
guard against re-running; the guard is unnecessary when nobody edits.
"""
```

Ajouter, avant `install_bootloader` :

```python
def grub_defaults(extra_cmdline: tuple[str, ...] = ()) -> str:
    """Render /etc/default/grub with the packages' kernel parameters appended.

    Order matters and is stable: `quiet` first, then package parameters in the
    order the packages were resolved, de-duplicated. A parameter containing a
    double quote would break the file, so it is refused rather than escaped -
    no legitimate kernel parameter needs one.
    """
    params = []
    for param in extra_cmdline:
        param = (param or "").strip()
        if not param or param in params:
            continue
        if '"' in param:
            raise ValueError(
                f"kernel parameter {param!r} contains a double quote, which "
                "cannot be written to /etc/default/grub")
        params.append(param)

    cmdline = " ".join(["quiet", *params])
    return (
        "GRUB_DEFAULT=0\nGRUB_TIMEOUT=3\n"
        'GRUB_DISTRIBUTOR="Nivuus"\n'
        f'GRUB_CMDLINE_LINUX_DEFAULT="{cmdline}"\n'
        'GRUB_CMDLINE_LINUX=""\n'
    )
```

Changer la signature et le corps :

```python
def install_bootloader(config: dict, target: str, fs: dict, emit,
                       extra_cmdline: tuple[str, ...] = ()) -> None:
```

et remplacer le bloc `write_file(... "etc/default/grub" ...)` par :

```python
    if extra_cmdline:
        emit.info("bootloader", 77,
                  "Kernel parameters from packages: " + " ".join(extra_cmdline))
    write_file(os.path.join(target, "etc/default/grub"),
               grub_defaults(extra_cmdline))
```

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_install_engine_bootloader.py
```

Attendu : `OK - all bootloader cmdline tests passed`

- [ ] **Step 5: Vérifier que l'appelant existant compile toujours**

`extra_cmdline` a une valeur par défaut, donc `run.py` reste valide sans modification à ce stade.

```bash
python3 -c "
import sys; sys.path[:0] = ['installer/install-engine', 'installer']
import steps.bootloader, inspect
print(inspect.signature(steps.bootloader.install_bootloader))
"
```

Attendu : `(config: dict, target: str, fs: dict, emit, extra_cmdline: tuple[str, ...] = ()) -> None`

- [ ] **Step 6: Commit**

```bash
git add installer/install-engine/steps/bootloader.py \
        scripts/tests/test_install_engine_bootloader.py
git commit -m "feat(bootloader): accepter la ligne de commande noyau des packages

Ecrite une fois, pendant l installation de GRUB : c est le seul moment
sain, celui ou le fichier est cree plutot qu edite, donc il n y a rien
a ajouter deux fois. install.sh l inserait apres coup par sed et avait
besoin d une garde contre la re-execution ; la garde devient inutile
quand personne n edite.

Un parametre contenant un guillemet est refuse plutot qu echappe :
aucun parametre noyau legitime n en a besoin."
```

---

### Task 9: `steps/packages.py` + branchement dans `run.py` + unité d'activation

**Files:**
- Create: `installer/install-engine/steps/packages.py`
- Create: `installer/packages/activate_cli.py`
- Create: `configs/systemd/nivuus-package-activate@.service`
- Modify: `installer/install-engine/run.py`
- Test: `scripts/tests/test_install_engine_packages.py`

**Interfaces:**
- Consumes: `packages.discovery.discover/partition`, `packages.capabilities.detect_capabilities`, `packages.conflicts.check_conflicts`, `packages.wizard.load_questions/validate_answers`, `packages.runner.run_resolve/run_install`, `steps.util.chroot_run/write_file/StepError`
- Produces:
  - `plan_packages(config: dict, hw: dict, emit) -> tuple[list[tuple[Manifest, dict, Resolution]], tuple[str, ...]]` — lève `StepError` sur conflit ou refus
  - `apply_packages(plan, target: str, hw: dict, emit) -> None`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `scripts/tests/test_install_engine_packages.py` :

```python
#!/usr/bin/env python3
"""Tests for install-engine/steps/packages.py - planning and applying packages.

plan_packages() is the whole "decide before you write" contract in one call:
it discovers, filters on capabilities, validates answers, detects conflicts and
resolves - and every one of those can refuse, before partition() has run. What
it returns is the kernel command line the bootloader step will write.

apply_packages() then writes: modules, hugepages, apt, the install hook, and
the activation unit that carries the package into first boot.

Run: python3 scripts/tests/test_install_engine_packages.py
"""
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"
os.environ["NIVUUS_PACKAGES_DIR"] = str(FIXTURES)

from steps import packages as steps_packages  # noqa: E402
from steps.util import StepError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, fn, needle):
    try:
        fn()
    except StepError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected StepError, none raised")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(("info", msg))

    def warn(self, step, pct, msg):
        self.lines.append(("warn", msg))

    def error(self, step, pct, msg):
        self.lines.append(("error", msg))


HW = {
    "iommu": {"supported": True, "vendor": "intel", "active": False},
    "gpus": [{"slot": "01:00.0", "vendor": "nvidia", "discrete": True,
              "ids": ["10de:2786", "10de:22bc"]}],
    "disks": [{"name": "nvme0n1", "path": "/dev/nvme0n1", "transport": "nvme"}],
    "cpu": {"hybrid": True},
}

# No packages selected: nothing planned, no cmdline.
plan, cmdline = steps_packages.plan_packages(
    {"disk": {"path": "/dev/nvme0n1"}, "features": [], "packages": {}},
    HW, FakeEmit())
check("nothing selected plans nothing", plan, [])
check("nothing selected contributes no cmdline", cmdline, ())

config = {
    "disk": {"path": "/dev/nvme0n1"},
    "features": ["os-base"],
    "packages": {"demo": {"greeting": "salut"}},
}
plan, cmdline = steps_packages.plan_packages(config, HW, FakeEmit())
check("the selected package is planned", [m.name for m, _, _ in plan], ["demo"])
check("static cmdline collected", "intel_iommu=on" in cmdline, True)
check("resolved cmdline collected",
      "vfio-pci.ids=10de:2786,10de:22bc" in cmdline, True)
check("the validated answers travel with the plan",
      plan[0][1]["greeting"], "salut")

# An unknown package name must fail loudly, not be skipped.
check_raises("an unknown package is refused",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"fantome": {}}}, HW, FakeEmit()),
             "fantome")

# demo requires the iommu capability: without it, it must be refused.
no_iommu = {**HW, "iommu": {"supported": False, "vendor": "", "active": False}}
check_raises("a package whose capability is missing is refused",
             lambda: steps_packages.plan_packages(config, no_iommu, FakeEmit()),
             "iommu")

# A refusing package stops the plan with its own sentence.
check_raises("a package refusal stops the plan",
             lambda: steps_packages.plan_packages(
                 {**config, "packages": {"refuser": {}}}, HW, FakeEmit()),
             "NVMe")

# --- apply_packages -------------------------------------------------------- #
calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)
    class R:
        returncode = 0
    return R()


with tempfile.TemporaryDirectory() as tmp:
    steps_packages.chroot_run = fake_chroot_run
    plan, _ = steps_packages.plan_packages(config, HW, FakeEmit())
    steps_packages.apply_packages(plan, tmp, HW, FakeEmit())
    target = pathlib.Path(tmp)

    modules = (target / "etc/modules").read_text()
    check("static module written", "vfio_pci" in modules, True)
    check("resolved module written", "vfio_iommu_type1" in modules, True)

    sysctl = (target / "etc/sysctl.d/60-nivuus-packages.conf").read_text()
    check("hugepages are converted from MiB to 2 MiB pages",
          "vm.nr_hugepages = 512" in sysctl, True)

    check("apt was asked for the declared packages",
          any("cowsay" in c for c in calls), True)
    check("the activation unit was enabled",
          any("nivuus-package-activate@demo.service" in " ".join(c)
              for c in calls), True)

    marker = target / "etc" / "nivuus-demo.json"
    check("the install hook ran under the target root", marker.is_file(), True)
    check("it received its answers",
          json.loads(marker.read_text())["answers"]["greeting"], "salut")

    state = json.loads((target / "etc/nivuus/packages.json").read_text())
    check("the selection is recorded on the target",
          state["demo"]["answers"]["greeting"], "salut")
    check("the recorded version matches the manifest",
          state["demo"]["version"], "1.0.0")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all package step tests passed")
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_install_engine_packages.py
```

Attendu : `ImportError: cannot import name 'packages' from 'steps'`

- [ ] **Step 3: Écrire l'unité d'activation**

Créer `configs/systemd/nivuus-package-activate@.service` :

```ini
[Unit]
Description=Nivuus package activation: %i
# The activate phase is the one that needs the network: it downloads what
# could not travel in the ISO. Ordering after network-online is not a nicety.
After=network-online.target
Wants=network-online.target
# A stamp rather than a self-disabling unit: if activation is interrupted
# halfway, the unit must run again at the next boot rather than believe it
# succeeded. systemctl disable inside ExecStartPost cannot tell the difference.
ConditionPathExists=!/var/lib/nivuus/packages/%i.activated

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/nivuus-package-activate %i
# Third-party code on first boot must never wedge the boot itself.
TimeoutStartSec=7200

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Écrire `activate_cli.py`**

Créer `installer/packages/activate_cli.py` :

```python
#!/usr/bin/env python3
"""First-boot entry point for a package's `activate` phase.

Deployed as /usr/local/sbin/nivuus-package-activate and run once per package
by nivuus-package-activate@<name>.service. It re-reads the answers the wizard
recorded on the target at install time, because the activate phase runs long
after the portal is gone - there is nobody left to ask.

The stamp is written ONLY on success. An activation that fails is retried at
the next boot rather than silently marked done, which matters because this is
the phase that downloads things: a network that was not up yet is the ordinary
failure here, and it fixes itself.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER_ROOT = os.path.dirname(HERE)
if INSTALLER_ROOT not in sys.path:
    sys.path.insert(0, INSTALLER_ROOT)

from common import hardware  # noqa: E402
from packages.discovery import discover  # noqa: E402
from packages.runner import HookError, run_activate  # noqa: E402

STATE_FILE = "/etc/nivuus/packages.json"
STAMP_DIR = "/var/lib/nivuus/packages"


class _StderrEmit:
    """Progress to stderr, so journald syncs it as the unit produces it."""

    def _write(self, level, msg):
        print(f"[{level}] {msg}", file=sys.stderr, flush=True)

    def info(self, step, pct, msg):
        self._write("info", msg)

    def warn(self, step, pct, msg):
        self._write("warn", msg)

    def error(self, step, pct, msg):
        self._write("error", msg)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: nivuus-package-activate <package-name>", file=sys.stderr)
        return 2
    name = argv[1]

    try:
        with open(STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {STATE_FILE}: {exc}", file=sys.stderr)
        return 1
    if name not in state:
        print(f"package {name!r} is not recorded in {STATE_FILE}", file=sys.stderr)
        return 1

    manifests, _ = discover()
    match = [m for m in manifests if m.name == name]
    if not match:
        print(f"package {name!r} has no manifest on this system", file=sys.stderr)
        return 1

    try:
        run_activate(match[0], hardware.detect_all(),
                     state[name].get("answers") or {}, _StderrEmit())
    except HookError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    os.makedirs(STAMP_DIR, exist_ok=True)
    with open(os.path.join(STAMP_DIR, f"{name}.activated"), "w") as fh:
        fh.write(state[name].get("version", "") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 5: Écrire `steps/packages.py`**

Créer `installer/install-engine/steps/packages.py` :

```python
"""Step 9: plan and apply the selected Nivuus packages.

The split between the two functions here IS the contract. plan_packages()
decides and never writes: it discovers, filters on capabilities, validates
answers, detects conflicts and runs every resolve hook. Each of those can
refuse, and all of them happen before partition() has touched the disk. What
it returns is the kernel command line the bootloader step will write.

apply_packages() writes, and only then: modules, hugepages, apt, the install
hook, and the activation unit that carries the package into first boot.
"""
from __future__ import annotations

import json
import os

from packages.capabilities import detect_capabilities
from packages.conflicts import check_conflicts
from packages.discovery import discover, eligibility
from packages.manifest import ManifestError
from packages.runner import HookError, run_install, run_resolve
from packages.wizard import WizardError, load_questions, validate_answers

from .util import StepError, chroot_run, write_file

STATE_REL_PATH = "etc/nivuus/packages.json"
SYSCTL_REL_PATH = "etc/sysctl.d/60-nivuus-packages.conf"
MODULES_REL_PATH = "etc/modules"
# A 2 MiB hugepage is the x86-64 default; the manifest speaks MiB because
# "how much memory does the guest need" is the question an author can answer.
HUGEPAGE_MIB = 2


def plan_packages(config: dict, hw: dict, emit):
    """Decide everything, write nothing. Raises StepError on any refusal.

    Returns (plan, kernel_cmdline) where plan is a list of
    (manifest, validated answers, resolution).
    """
    selected = config.get("packages") or {}
    if not selected:
        return [], ()

    emit.info("packages", 60, f"Planning {len(selected)} package(s)…")
    manifests, errors = discover()
    for path, message in errors:
        emit.warn("packages", 60, f"Ignoring unreadable manifest {path}: {message}")

    by_name = {m.name: m for m in manifests}
    unknown = sorted(set(selected) - set(by_name))
    if unknown:
        raise StepError(
            "packages sélectionnés mais introuvables sur ce support : "
            + ", ".join(unknown))

    chosen = [by_name[name] for name in sorted(selected)]

    capabilities = detect_capabilities(hw, (config.get("disk") or {}).get("path", ""))
    features = set(config.get("features") or [])
    for manifest in chosen:
        reason = eligibility(manifest, capabilities, features)
        if reason:
            raise StepError(f"package « {manifest.label} » : {reason}")

    conflicts = check_conflicts(chosen)
    if conflicts:
        raise StepError(" ; ".join(c.message() for c in conflicts))

    plan = []
    cmdline: list[str] = []
    for manifest in chosen:
        try:
            questions = (load_questions(os.path.join(manifest.root,
                                                     manifest.questions_file))
                         if manifest.questions_file else [])
            answers = validate_answers(questions, selected[manifest.name] or {})
        except (WizardError, ManifestError) as exc:
            raise StepError(f"package « {manifest.label} » : {exc}") from exc

        try:
            resolution = run_resolve(manifest, hw, answers, emit)
        except HookError as exc:
            raise StepError(str(exc)) from exc
        if not resolution.ok:
            raise StepError(f"package « {manifest.label} » : {resolution.reason}")

        for param in resolution.platform.kernel_cmdline:
            if param not in cmdline:
                cmdline.append(param)
        plan.append((manifest, answers, resolution))

    return plan, tuple(cmdline)


def apply_packages(plan, target: str, hw: dict, emit) -> None:
    """Write everything the plan decided, in dependency order."""
    if not plan:
        return

    modules: list[str] = []
    hugepages_mib = 0
    apt: list[str] = []
    for manifest, _, resolution in plan:
        for module in resolution.platform.modules:
            if module not in modules:
                modules.append(module)
        hugepages_mib += resolution.platform.hugepages_mib
        for package in manifest.apt:
            if package not in apt:
                apt.append(package)

    if modules:
        emit.info("packages", 92, f"Kernel modules: {' '.join(modules)}")
        path = os.path.join(target, MODULES_REL_PATH)
        existing = ""
        if os.path.isfile(path):
            with open(path) as fh:
                existing = fh.read()
        missing = [m for m in modules if m not in existing.split()]
        if missing:
            write_file(path, existing.rstrip("\n") + "\n"
                       + "\n".join(missing) + "\n")

    if hugepages_mib:
        pages = hugepages_mib // HUGEPAGE_MIB
        emit.info("packages", 93, f"Hugepages: {pages} × {HUGEPAGE_MIB} MiB")
        write_file(os.path.join(target, SYSCTL_REL_PATH),
                   "# Written by the Nivuus package engine.\n"
                   f"vm.nr_hugepages = {pages}\n")

    if apt:
        emit.info("packages", 94, f"Installing: {' '.join(apt)}")
        chroot_run(target, ["apt-get", "install", "-y", *apt], check=False)

    state = {}
    for manifest, answers, _ in plan:
        emit.info("packages", 95, f"Applying package « {manifest.label} »…")
        try:
            run_install(manifest, hw, answers, target, emit)
        except HookError as exc:
            raise StepError(str(exc)) from exc
        state[manifest.name] = {"version": manifest.version, "answers": answers}
        chroot_run(target, ["systemctl", "enable",
                            f"nivuus-package-activate@{manifest.name}.service"],
                   check=False)

    # The answers must outlive the portal: the activate phase runs at first
    # boot, long after there is anyone left to ask.
    write_file(os.path.join(target, STATE_REL_PATH),
               json.dumps(state, indent=2, ensure_ascii=False) + "\n")
```

- [ ] **Step 6: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_install_engine_packages.py
```

Attendu : `OK - all package step tests passed`

- [ ] **Step 7: Brancher dans `run.py`**

Dans `installer/install-engine/run.py`, ajouter à l'import des steps :

```python
from steps import (  # noqa: E402
    partition, debootstrap, chroot_base, bootloader, features, packages,
    validate,
)
```

Ajouter `"packages"` aux choix de `--stop-after` :

```python
    parser.add_argument("--stop-after", default=None,
                        choices=["partition", "debootstrap", "base", "bootloader",
                                 "features", "packages"],
                        help="stop the pipeline after this step (for testing)")
```

Puis, dans `main()`, **avant** `partition.partition_and_format` :

```python
        hw = hardware.detect_all()

        # Decide before writing: every package refusal, conflict and kernel
        # parameter is known here, while the target disk is still untouched.
        plan, package_cmdline = packages.plan_packages(config, hw, emit)
```

Changer l'appel au bootloader :

```python
        bootloader.install_bootloader(config, target, fs, emit, package_cmdline)
```

Et insérer l'application après `features.apply_features` :

```python
        features.apply_features(config, target, nivuus_dir, hw, emit)
        if stop("features"):
            return 0
        packages.apply_packages(plan, target, hw, emit)
        if stop("packages"):
            return 0
```

**Attention** : `hw = hardware.detect_all()` existe déjà dans `main()` — le déplacer plutôt que le dupliquer.

- [ ] **Step 8: Vérifier le pipeline complet à vide**

```bash
python3 -c "
import sys; sys.path[:0] = ['installer/install-engine', 'installer']
import run
print('run.py imports OK')
"
python3 scripts/tests/test_install_engine_features.py
python3 scripts/tests/test_install_engine_bootloader.py
python3 scripts/tests/test_install_engine_packages.py
```

Attendu : `run.py imports OK` puis trois `OK - …`.

- [ ] **Step 9: Commit**

```bash
git add installer/install-engine/steps/packages.py installer/install-engine/run.py \
        installer/packages/activate_cli.py \
        configs/systemd/nivuus-package-activate@.service \
        scripts/tests/test_install_engine_packages.py
git commit -m "feat(packages): planifier avant d ecrire, puis appliquer

La separation entre plan_packages et apply_packages EST le contrat.
plan_packages decide et n ecrit jamais : decouverte, capacites,
validation des reponses, conflits, et chaque hook resolve. Chacun peut
refuser, et tout cela arrive avant que partition() n ait touche le
disque. Ce qu il rend est la ligne de commande noyau que bootloader
ecrira.

L unite d activation s appuie sur un temoin plutot que sur un
self-disable : une activation interrompue a mi-chemin doit repartir au
prochain boot au lieu de se croire terminee. C est la phase qui
telecharge, et un reseau pas encore la est la panne ordinaire ici -
elle se repare toute seule."
```

---

### Task 10: exposer les packages au portail

**Files:**
- Modify: `installer/webapp/models.py`
- Modify: `installer/webapp/main.py`
- Test: `scripts/tests/test_webapp_models.py` (étendre)

**Interfaces:**
- Consumes: `packages.discovery.discover/partition`, `packages.capabilities.detect_capabilities`, `packages.conflicts.check_conflicts`, `packages.wizard.load_questions`
- Produces:
  - `InstallConfig.packages: dict[str, dict]` (nom du package → réponses brutes)
  - `GET /api/packages?target_disk=<path>&features=<a,b>` → `{"eligible": [...], "ineligible": [...], "errors": [...]}`

- [ ] **Step 1: Étendre le test des modèles**

Ajouter à la fin de `scripts/tests/test_webapp_models.py`, avant le bloc `if failures:`. Le fichier importe le module sous `models` et fabrique ses configs avec le helper `base_kwargs(features)` déjà défini ligne 32 — s'en servir, ne pas en inventer un autre :

```python
# --- packages ------------------------------------------------------------- #
# The wizard carries package answers as an opaque mapping: validating them
# against each package's own question vocabulary is the engine's job - it is
# the only side that can read the manifests - so the model checks the shape
# and nothing else.
cfg_pkg = models.InstallConfig(**base_kwargs(["os-base"]),
                               packages={"console": {"retro": True}})
check("packages are carried through", cfg_pkg.packages["console"]["retro"], True)
check("packages default to empty",
      models.InstallConfig(**base_kwargs(["os-base"])).packages, {})

check_raises(
    "an invalid package name is refused",
    lambda: models.InstallConfig(**base_kwargs(["os-base"]),
                                 packages={"Console!": {}}),
)
check_raises(
    "a non-mapping answer set is refused",
    lambda: models.InstallConfig(**base_kwargs(["os-base"]),
                                 packages={"console": "oui"}),
)
```

- [ ] **Step 2: Lancer le test pour le voir échouer**

```bash
python3 scripts/tests/test_webapp_models.py
```

Attendu : `AttributeError: 'InstallConfig' object has no attribute 'packages'`. Pydantic v2 **ignore** silencieusement un kwarg inconnu (`extra='ignore'` par défaut), donc la construction réussit et c'est la lecture de l'attribut qui échoue — pas une assertion.

- [ ] **Step 3: Ajouter le champ à `InstallConfig`**

Dans `installer/webapp/models.py`, ajouter l'import du module `re` en tête, puis le champ dans `InstallConfig` après `features` :

```python
    # Package name -> raw answers. Deliberately opaque here: the answers are
    # validated against each package's own question vocabulary by the engine,
    # which is the only side that can read the manifests. Pydantic checks the
    # shape; packages/wizard.py checks the meaning.
    packages: dict[str, dict] = Field(default_factory=dict)
```

et le validateur :

```python
    @field_validator("packages")
    @classmethod
    def _packages(cls, v: dict) -> dict:
        for name, answers in v.items():
            if not re.match(r"^[a-z][a-z0-9-]{0,31}$", name):
                raise ValueError(f"invalid package name: {name!r}")
            if not isinstance(answers, dict):
                raise ValueError(f"answers for {name!r} must be a mapping")
        return v
```

- [ ] **Step 4: Lancer le test pour le voir passer**

```bash
python3 scripts/tests/test_webapp_models.py
```

Attendu : `OK - …`

- [ ] **Step 5: Ajouter l'endpoint au portail**

Dans `installer/webapp/main.py`, ajouter aux imports :

```python
from packages.capabilities import detect_capabilities  # noqa: E402
from packages.conflicts import check_conflicts  # noqa: E402
from packages.discovery import discover, partition  # noqa: E402
from packages.wizard import WizardError, load_questions  # noqa: E402
```

Puis, après `api_hardware()` :

```python
@app.get("/api/packages")
async def api_packages(target_disk: str = "", features: str = "") -> JSONResponse:
    """Packages available on this medium, with the reason for each exclusion.

    The reason matters more than the list. A package that is simply absent
    from the wizard is indistinguishable from one that was never installed,
    and this machine has no screen to explain the difference on.
    """
    hw = await asyncio.to_thread(hardware.detect_all)
    manifests, errors = await asyncio.to_thread(discover)
    capabilities = detect_capabilities(hw, target_disk)
    selected_features = {f for f in features.split(",") if f}
    eligible, rejected = partition(manifests, capabilities, selected_features)

    def describe(manifest) -> dict:
        payload = {
            "name": manifest.name, "label": manifest.label,
            "version": manifest.version, "tier": manifest.tier,
            "claims": [r for r, _ in manifest.claims],
            "questions": [],
        }
        if manifest.questions_file:
            try:
                payload["questions"] = [
                    q.to_dict() for q in load_questions(
                        os.path.join(manifest.root, manifest.questions_file))
                ]
            except WizardError as exc:
                payload["questions_error"] = str(exc)
        return payload

    return JSONResponse({
        "eligible": [describe(m) for m in eligible],
        "ineligible": [{**describe(m), "reason": reason}
                       for m, reason in rejected],
        "errors": [{"path": p, "message": msg} for p, msg in errors],
        "conflicts": [{"resource": c.resource, "packages": list(c.packages),
                       "message": c.message()}
                      for c in check_conflicts(eligible)],
    })
```

- [ ] **Step 6: Vérifier l'endpoint en conditions réelles**

```bash
NIVUUS_PACKAGES_DIR=scripts/tests/fixtures/packages \
NIVUUS_PORTAL_HOST=127.0.0.1 NIVUUS_PORTAL_PORT=8080 \
NIVUUS_PROGRESS_DIR=/tmp/nivuus-progress \
  python3 installer/webapp/main.py &
sleep 3
curl -s 'http://127.0.0.1:8080/api/packages' | python3 -m json.tool | head -40
kill %1
```

Attendu : un JSON portant `eligible`/`ineligible`/`errors`/`conflicts`. `demo` apparaît dans l'un des deux premiers selon que la machine annonce un IOMMU, **avec sa raison** s'il est écarté. `refuser` apparaît dans `eligible` (il ne requiert rien ; c'est sa phase `resolve` qui refuse, plus tard).

- [ ] **Step 7: Commit**

```bash
git add installer/webapp/models.py installer/webapp/main.py \
        scripts/tests/test_webapp_models.py
git commit -m "feat(portail): exposer les packages et la raison de chaque exclusion

La raison compte plus que la liste. Un package simplement absent du
wizard est indistinguable d un package jamais installe, et cette
machine n a pas d ecran pour expliquer la difference.

InstallConfig porte les reponses comme un mapping opaque : les valider
contre le vocabulaire de questions de chaque package est le travail du
moteur, seul cote capable de lire les manifestes. Pydantic verifie la
forme, packages/wizard.py verifie le sens."
```

---

### Task 11: documenter le contrat et embarquer les packages dans l'ISO

**Files:**
- Modify: `installer/iso-build/build.sh`
- Modify: `installer/README.md`
- Modify: `CLAUDE.md`
- Modify: `installer/Makefile`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: `PACKAGE_REPOS` (liste de chemins, séparés par des espaces) dans `build.sh`

- [ ] **Step 1: Embarquer les packages dans l'ISO**

Dans `installer/iso-build/build.sh`, après le bloc `BUILD_MQTT_DEB`, ajouter :

```bash
# Nivuus packages: sibling repositories embedded as payload, discovered at
# install time from /opt/nivuus-packages/*/nivuus-package.yaml. Same mechanism
# as the MQTT .deb above - a sibling repo, exported from its TRACKED files
# only, never the raw working tree.
PAYLOAD_PACKAGES="${INCLUDES}/opt/nivuus-packages"
rm -rf "$PAYLOAD_PACKAGES"
for pkg_repo in ${PACKAGE_REPOS:-}; do
    if [ ! -f "${pkg_repo}/nivuus-package.yaml" ]; then
        echo "W: ${pkg_repo} has no nivuus-package.yaml; skipping" >&2
        continue
    fi
    pkg_name=$(basename "$pkg_repo")
    echo "==> Exporting package ${pkg_name} -> opt/nivuus-packages/${pkg_name}"
    mkdir -p "${PAYLOAD_PACKAGES}/${pkg_name}"
    if git -C "$pkg_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$pkg_repo" archive HEAD | tar -x -C "${PAYLOAD_PACKAGES}/${pkg_name}"
    else
        rsync -a --exclude '.git' --exclude '__pycache__' \
            "${pkg_repo}/" "${PAYLOAD_PACKAGES}/${pkg_name}/"
    fi
done
```

- [ ] **Step 2: Vérifier que le script reste valide**

```bash
bash -n installer/iso-build/build.sh && echo "syntaxe OK"
# The shared CI socle runs shellcheck over the whole repo (shellcheck-paths: ".").
shellcheck installer/iso-build/build.sh install.sh && echo "shellcheck OK"
PACKAGE_REPOS="$PWD/scripts/tests/fixtures/packages/demo" bash -c '
  set -eu
  echo "PACKAGE_REPOS=$PACKAGE_REPOS"
  test -f "$PACKAGE_REPOS/nivuus-package.yaml" && echo "manifeste trouve"
'
```

Attendu : `syntaxe OK` puis `manifeste trouve`.

- [ ] **Step 3: Documenter le contrat dans `installer/README.md`**

Ajouter une section après le tableau « Layout » :

````markdown
## Packages

A **package** extends the installer with host-side features it does not ship
itself. It is a directory carrying a `nivuus-package.yaml`, discovered at
install time under `/opt/nivuus-packages/*/`, and embedded into the ISO from a
sibling repository:

```bash
PACKAGE_REPOS="$HOME/Projects/Nivuus/packages/console" sudo -E make build-iso
```

### Three phases, named relative to the reboot

| Phase | When | Receives | May |
|---|---|---|---|
| `resolve` | Before any write | `hw` + wizard answers on stdin | **Read only.** Return the resolved platform block, or refuse with a reason |
| `install` | On a target filesystem | `--root` (`/mnt/target` in the ISO, `/` standalone) | Write under that root |
| `activate` | After the reboot, network up | — | Anything |

A hook reads a JSON context on **stdin** and emits jsonl events on **stdout**:
`{"event":"progress","pct":N,"msg":"…"}`, `{"event":"platform","kernel-cmdline":[…],"modules":[…],"hugepages-mib":N}`,
`{"event":"refuse","reason":"…"}`, `{"event":"done"}`. A non-zero exit fails the install.

### Two tiers

`userspace` may declare `apt`, questions and hooks. `platform` may additionally
declare `kernel-cmdline`, `modules` and `hugepages-mib` — and the wizard then
shows the resolved kernel command line verbatim and asks for its own
confirmation. A `userspace` manifest declaring any of the three is **refused**,
not silently stripped.

See `scripts/tests/fixtures/packages/demo/` for a complete working example.
````

- [ ] **Step 4: Ajouter la cible Makefile qui exerce le moteur**

Dans `installer/Makefile`, ajouter à `.PHONY` la cible `test-packages`, puis :

```makefile
# Run the package-engine test suite (no root, no chroot, no ISO).
test-packages:
	@for t in test_packages_manifest test_packages_capabilities \
	          test_packages_discovery test_packages_conflicts \
	          test_packages_wizard test_packages_runner \
	          test_install_engine_bootloader test_install_engine_packages; do \
	    echo "--- $$t"; \
	    python3 $(dir $(INSTALLER_DIR))scripts/tests/$$t.py || exit 1; \
	done
```

- [ ] **Step 5: Lancer la suite entière**

```bash
cd installer && make test-packages && cd ..
```

Attendu : huit `OK - …`, aucun échec.

- [ ] **Step 6: Consigner dans `CLAUDE.md`**

Dans la section « Installer Architecture », après le paragraphe **Reuse, don't duplicate**, insérer :

```markdown
**Package engine (2026-08-27)**: `installer/packages/` implements the
`nivuus.dev/v1` contract — a declarative `nivuus-package.yaml` plus three hooks
(`resolve`/`install`/`activate`). Packages are sibling repositories embedded
into the ISO via `PACKAGE_REPOS=… make build-iso` and discovered at
`/opt/nivuus-packages/*/` (override with `NIVUUS_PACKAGES_DIR`).

Three properties carry the design and are easy to break by accident:
* **`resolve` is read-only and runs before `partition`.** That is the only
  reason `bootloader` can stay where it is in `run.py`: the kernel cmdline is
  known before the disk is touched. Moving a package's cmdline contribution
  later would force the whole pipeline to be reordered.
* **`iommu` is read from the ACPI tables (`DMAR`/`IVRS`), never from
  `/sys/kernel/iommu_groups`.** The live ISO boots *without* `intel_iommu=on` —
  adding it is exactly what a passthrough package asks for — so a check on the
  active state would answer "no" on every capable machine and no such package
  would ever be offered.
* **The engine detects capabilities, the package detects details.** The engine
  must answer `requires.capabilities` before running any hook, so it cannot
  delegate. It stays coarse (`iommu`, `gpu-discrete`, `nvme-dedicated`,
  `cpu-hybrid`); the precise work — PCI functions, IOMMU groups, `vfio-pci.ids`
  — belongs to `resolve`.

Activation uses a stamp (`/var/lib/nivuus/packages/<name>.activated`), not a
self-disabling unit: an interrupted activation must retry at the next boot
rather than believe it succeeded. Tests: `cd installer && make test-packages`
(8 files). Spec: `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`.
```

- [ ] **Step 7: Commit**

```bash
git add installer/iso-build/build.sh installer/README.md installer/Makefile CLAUDE.md
git commit -m "docs(packages): documenter le contrat et embarquer les packages dans l ISO

PACKAGE_REPOS suit le mecanisme deja eprouve de BUILD_MQTT_DEB : un
depot frere, exporte par git archive depuis ses fichiers SUIVIS
uniquement, jamais l arbre de travail brut - la meme garantie de
securite que pour opt/nivuus-src.

CLAUDE.md retient les trois proprietes faciles a casser par accident :
resolve avant partition, iommu lu dans l ACPI et pas dans
/sys/kernel/iommu_groups, et la frontiere capacites/details."
```

---

## Ce que ce plan ne fait PAS

Volontairement, et chacun aura son propre plan :

* **Phase 2** — `console` devient un package. `install.sh`, `features.py::_kvm_vfio_thermal`, `common/hardware.py` et `common/retro.py` ne sont **pas touchés** ici. À la fin de ce plan le moteur existe, il est prouvé par `scripts/tests/fixtures/packages/demo/`, et aucun package réel ne l'utilise.
* **Phase 3** — l'extraction git vers `nivuus/console`.
* **Phase 4** — le runner autonome sur un Debian existant.
* **Un fichier JSON Schema séparé — et c'est un écart assumé au spec.** Le spec prévoit que `nivuus/installer` possède « le JSON Schema », que `console` en vendorise une copie et que sa CI détecte la dérive. Écrire ce fichier reviendrait à maintenir une **seconde source de vérité** à côté de `manifest.py`, qui dériverait de l'implémentation exactement comme le spec redoute que les deux dépôts dérivent l'un de l'autre. Le contrat normatif est donc `installer/packages/manifest.py` — et pour rendre la détection de dérive possible, il **expose ses constantes** (`API_VERSION`, `TIERS`, `HOOK_PHASES`, `CLAIM_MODES`, `PLATFORM_KEYS`, et `QUESTION_TYPES` dans `wizard.py`). La CI de `console`, en phase 3, assertera ces valeurs plutôt qu'un document. Le spec sera mis à jour en conséquence.

* **Le rendu des questions dans le wizard HTML.** `/api/packages` sert le vocabulaire ; `static/app.js` et `templates/wizard.html` ne sont pas modifiés. Le moteur est utilisable par l'API avant que l'interface ne le montre, et découpler les deux évite de bloquer le moteur sur du CSS.

---

## Un piège de nommage à connaître avant de commencer

Il y a **deux** choses appelées `packages` dans ce plan :

| Chemin | Rôle | Comment on l'importe |
| --- | --- | --- |
| `installer/packages/` | Le moteur (manifeste, capacités, découverte, conflits, wizard, runner) | `from packages.manifest import …` — import absolu, résolu depuis `installer/` |
| `installer/install-engine/steps/packages.py` | L'**étape** du pipeline qui s'en sert | `from steps import packages` dans `run.py` |

Cela fonctionne parce que `run.py` insère `installer/` **avant** `installer/install-engine` dans `sys.path`, et que Python 3 fait des imports absolus par défaut : depuis `steps/packages.py`, `from packages.capabilities import …` désigne le moteur, jamais le module courant.

Dans un test qui importe les deux, **aliaser** : `from steps import packages as steps_packages`. C'est ce que fait `test_install_engine_packages.py`, et ce n'est pas une coquetterie.
