# Contrat de release Nivuus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doter les dix dépôts Nivuus d'un workflow de release partagé qui dérive
la version des commits conventionnels, publie archive + sommes + attestation, et
refuse de publier un package dont le hook `install` n'est pas idempotent.

**Architecture:** Un workflow réutilisable unique dans `nivuus/.github`, appelé
par un stub de quelques lignes dans chaque dépôt, sur le modèle déjà en place
pour `policy.yml` et les quatre `ci-*.yml`. La dérivation de version est un
script bash testé en bats, comme `check-commits.sh`. La preuve d'idempotence est
un harnais Python qui vit dans `installer` — parce que c'est le dépôt qui possède
déjà `runner.py` et qu'un harnais séparé divergerait de lui — et que `.github`
se contente d'appeler.

**Tech Stack:** GitHub Actions (`workflow_call`), bash + bats, Python 3 (stdlib
seule + PyYAML, déjà exigé par `manifest.py`), `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-09-08-releases-et-mise-a-jour-locale-design.md`

Ce plan est le **plan A** d'un découpage en quatre. Les plans B (updater et CLI
`nivuus`), C (migration des quatre packages hors moteur) et D (surface MQTT, ISO,
ouverture des dépôts privés) le suivent et en dépendent.

## Global Constraints

- **Deux dépôts sont touchés** : `nivuus/.github` (tâches 1, 4, 5, 6) et
  `nivuus/installer` (tâches 2, 3). Les tâches 7 et 8 touchent les dix dépôts.
  Chaque tâche indique son dépôt ; ne jamais mélanger deux dépôts dans un commit.
- **Titres de PR conventionnels EN ANGLAIS** — `policy / Coding rules` lance
  `check-commits.sh --subject "$PR_TITLE"` et le refuse sinon. Les messages de
  commit de `installer` sont en français, les titres de PR en anglais.
- **Commentaires de code en anglais** dans `installer` (`CLAUDE.md`), sauf
  marqueur `# policy: allow-fr-file` daté et justifié.
- **`main` est protégée avec `enforce_admins: true`** sur `installer`, `mqtt`,
  `marketplace` et `shell` : passage par PR, historique linéaire, fusion en
  **squash** obligatoire sur `installer`. `--admin` ne contourne rien.
- **Remotes en HTTPS** : `git push` en SSH échoue depuis cette session (root sans
  clé). Cloner avec `git clone https://github.com/nivuus/<dépôt>`.
- **`gh api repos/nivuus/<dépôt>/actions/runs/<id>/jobs`** pour diagnostiquer :
  `gh run view --log` rend vide avec exit 0 sur un workflow réutilisable.
- **`self-test.yml` de `nivuus/.github` lance `shellcheck -S warning
  scripts/*.sh scripts/lib/*.sh` puis `bats tests/`** : tout nouveau script y
  passe automatiquement et doit être shellcheck-propre.
- **Version initiale : `1.0.0`**, surchargeable par `INITIAL_VERSION`. C'est le
  numéro que les cinq manifestes existants déclarent déjà.
- **`manifest.py:186` impose `MAJOR.MINOR.PATCH` strict** — pas de suffixe.
- **Fait mesuré le 2026-09-08 :** les six hooks `install` de la suite (`desk`,
  `home-desk`, `home-manager`, `home-stock`, `media-manager`, `console`) ne font
  **aucun appel `subprocess`** — ni apt, ni docker, ni systemctl. Ce sont de purs
  écrivains de fichiers sous `root`. C'est ce qui rend la tâche 2 possible sans
  privilège ni réseau.

---

### Task 1: Dérivation de version depuis les commits conventionnels

**Dépôt :** `nivuus/.github`

**Files:**
- Create: `scripts/derive-version.sh`
- Test: `tests/test_derive_version.bats`

**Interfaces:**
- Consumes: rien.
- Produces: `derive-version.sh` — lit `git log` dans le répertoire courant,
  écrit la version suivante sur stdout et sort **0** ; sort **3** sans rien
  écrire quand aucun commit ne justifie de release. `INITIAL_VERSION` (défaut
  `1.0.0`) est la sortie quand aucun tag `v*` n'existe. Consommé par
  `release.yml` (tâche 6).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_derive_version.bats` :

```bash
#!/usr/bin/env bats

load helpers/repo

setup() {
    SCRIPTS="${BATS_TEST_DIRNAME}/../scripts"
    make_repo
}

@test "prints the initial version when no tag exists" {
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.0.0" ]
}

@test "honours an overridden initial version" {
    INITIAL_VERSION=0.1.0 run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "0.1.0" ]
}

@test "bumps the minor version for a feat" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "feat: add the stock endpoint"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.3.0" ]
}

@test "bumps the patch version for a fix" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "fix: stop dropping the retained topic"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.2.4" ]
}

@test "treats perf and refactor as patches" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "perf: shrink the discovery payload"
    commit_file "b.txt" "x" "refactor: split the coordinator"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.2.4" ]
}

@test "bumps the major version for a bang marker" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "feat(cli)!: rename nivuus to nivuus-shell"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "2.0.0" ]
}

@test "bumps the major version for a BREAKING CHANGE footer" {
    git tag -a v1.2.3 -m "release"
    printf 'x\n' > a.txt
    git add a.txt
    git commit -q -m "feat: rework the manifest" -m "BREAKING CHANGE: source is required"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "2.0.0" ]
}

@test "a feat outranks a fix whatever the order" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "fix: correct the unit path"
    commit_file "b.txt" "x" "feat: add the update entity"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.3.0" ]
}

@test "exits 3 when nothing is releasable" {
    git tag -a v1.2.3 -m "release"
    commit_file "a.txt" "x" "docs: explain the release contract"
    commit_file "b.txt" "x" "chore: bump the linter"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 3 ]
    [ -z "$output" ]
}

@test "ignores tags that are not versions" {
    git tag -a nightly -m "not a version"
    commit_file "a.txt" "x" "feat: add a thing"
    run "$SCRIPTS/derive-version.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "1.0.0" ]
}
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

```bash
bats tests/test_derive_version.bats
```

Attendu : ÉCHEC — `derive-version.sh` n'existe pas (`No such file or directory`).

- [ ] **Step 3: Écrire l'implémentation minimale**

Créer `scripts/derive-version.sh` :

```bash
#!/usr/bin/env bash
# Derive the next semantic version from the conventional commits since the
# last version tag.
#
# The suite already enforces conventional English subjects on every pull
# request title (check-commits.sh), and protected branches merge by squash,
# so the merge commit subject IS a conventional commit. Deriving the version
# from them turns a rule that was pure overhead into the release engine.
#
# Usage: derive-version.sh
# Exit 0: the next version is on stdout.
# Exit 3: nothing releasable since the last tag; stdout is empty.
set -uo pipefail

readonly SCOPE='(\([a-z0-9._/-]+\))?'
readonly FEAT_RE="^feat${SCOPE}: "
readonly PATCH_RE="^(fix|perf|refactor)${SCOPE}: "
readonly BREAKING_RE="^[a-z]+${SCOPE}!: "

main() {
    local last
    last="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"

    if [ -z "$last" ]; then
        printf '%s\n' "${INITIAL_VERSION:-1.0.0}"
        return 0
    fi

    local major minor patch
    IFS=. read -r major minor patch <<< "${last#v}"

    local bump=none subject
    while IFS= read -r subject; do
        [ -n "$subject" ] || continue
        if [[ "$subject" =~ $BREAKING_RE ]]; then
            bump=major
            break
        fi
        if [[ "$subject" =~ $FEAT_RE ]] && [ "$bump" != major ]; then
            bump=minor
        fi
        if [[ "$subject" =~ $PATCH_RE ]] && [ "$bump" = none ]; then
            bump=patch
        fi
    done < <(git log --no-merges --format='%s' "${last}..HEAD")

    # A breaking change may also be declared in the body rather than with the
    # bang marker; the footer is the form the Conventional Commits spec makes
    # normative, so it cannot be treated as a lesser signal.
    if [ "$bump" != major ] \
        && git log --no-merges --format='%b' "${last}..HEAD" \
            | grep -q '^BREAKING CHANGE:'; then
        bump=major
    fi

    case "$bump" in
        major) printf '%d.0.0\n' "$((major + 1))" ;;
        minor) printf '%d.%d.0\n' "$major" "$((minor + 1))" ;;
        patch) printf '%d.%d.%d\n' "$major" "$minor" "$((patch + 1))" ;;
        *)     return 3 ;;
    esac
}

main "$@"
```

Rendre exécutable : `chmod +x scripts/derive-version.sh`

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

```bash
bats tests/test_derive_version.bats
shellcheck -S warning scripts/derive-version.sh
```

Attendu : 10 tests OK, shellcheck silencieux.

- [ ] **Step 5: Commit**

```bash
git add scripts/derive-version.sh tests/test_derive_version.bats
git commit -m "feat: derive the next version from conventional commits"
```

---

### Task 2: Harnais de preuve d'idempotence

**Dépôt :** `nivuus/installer`

**Files:**
- Create: `scripts/idempotence_harness.py`
- Create: `scripts/tests/test_idempotence_harness.py`
- Create: `scripts/tests/fixtures/packages/idempotent/nivuus-package.yaml`
- Create: `scripts/tests/fixtures/packages/idempotent/hooks/install.py`
- Create: `scripts/tests/fixtures/packages/appender/nivuus-package.yaml`
- Create: `scripts/tests/fixtures/packages/appender/hooks/install.py`
- Modify: `installer/Makefile` (ajouter `test_idempotence_harness` à la boucle
  `test-packages`)

**Interfaces:**
- Consumes: `packages.manifest.load_manifest(path) -> Manifest` ;
  `Manifest.name`, `Manifest.hook_path(phase) -> str` ;
  `packages.runner.run_install(manifest, hw, answers, root, emit=None) -> None`
  (lève `HookError`) ; `packages.manifest.MANIFEST_NAME`.
- Produces: `scripts/idempotence_harness.py`, invocable en
  `python3 scripts/idempotence_harness.py --package <dir>`. Sortie 0 = idempotent,
  1 = non idempotent (rapport sur stdout), 2 = manifeste illisible. Consommé par
  `check-idempotence.sh` (tâche 4).

- [ ] **Step 1: Écrire les fixtures**

Créer `scripts/tests/fixtures/packages/idempotent/nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: idempotent
version: 1.0.0
label: "Fixture : hook install rejouable"
tier: userspace

hooks:
  install: hooks/install.py
```

Créer `scripts/tests/fixtures/packages/idempotent/hooks/install.py` :

```python
#!/usr/bin/env python3
"""Fixture install hook that rewrites its output, so replaying changes nothing."""
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

json.load(sys.stdin)
target = os.path.join(args.root, "etc/nivuus-idempotent.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write("state=ready\n")

print(json.dumps({"event": "done"}))
```

Créer `scripts/tests/fixtures/packages/appender/nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: appender
version: 1.0.0
label: "Fixture : hook install qui ajoute au lieu de réécrire"
tier: userspace

hooks:
  install: hooks/install.py
```

Créer `scripts/tests/fixtures/packages/appender/hooks/install.py` :

```python
#!/usr/bin/env python3
"""Fixture install hook that APPENDS, so replaying it corrupts its own output."""
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True)
parser.add_argument("--root", default="/")
args = parser.parse_args()

json.load(sys.stdin)
target = os.path.join(args.root, "etc/nivuus-appender.conf")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "a") as handle:
    handle.write("entry\n")

print(json.dumps({"event": "done"}))
```

- [ ] **Step 2: Écrire le test qui échoue**

Créer `scripts/tests/test_idempotence_harness.py` :

```python
#!/usr/bin/env python3
"""Tests for scripts/idempotence_harness.py - the proof that install replays.

The update path replays `install` in place rather than introducing a new
phase, so idempotence is a hard contract and not a courtesy. A control that
cannot fail would be worse than none: this suite therefore checks that the
harness ACCEPTS a hook that rewrites and REFUSES one that appends, rather
than only checking that it runs.

Run: python3 scripts/tests/test_idempotence_harness.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "packages"

from idempotence_harness import check_package, snapshot  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- a hook that rewrites is idempotent ------------------------------------ #
status, report = check_package(str(FIXTURES / "idempotent"))
check("a rewriting hook is accepted", status, 0)
check("the report names the package", "idempotent" in report, True)

# --- a hook that appends is not -------------------------------------------- #
status, report = check_package(str(FIXTURES / "appender"))
check("an appending hook is refused", status, 1)
check("the report names the offending path",
      "etc/nivuus-appender.conf" in report, True)
check("the report says what changed", "changed" in report, True)

# --- a package with no install hook proves nothing, and says so ------------ #
status, report = check_package(str(FIXTURES / "refuser"))
check("a package with no install hook is accepted", status, 0)
check("the report says there was nothing to prove",
      "no install hook" in report, True)

# --- an unreadable manifest is an error, not a pass ------------------------ #
status, report = check_package(str(FIXTURES / "does-not-exist"))
check("a missing manifest exits 2", status, 2)

# --- the snapshot itself must distinguish content, mode and kind ----------- #
import os  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    first = snapshot(tmp, ())
    with open(path, "w") as handle:
        handle.write("b")
    check("the snapshot notices a content change", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "f")
    with open(path, "w") as handle:
        handle.write("a")
    os.chmod(path, 0o600)
    first = snapshot(tmp, ())
    os.chmod(path, 0o644)
    check("the snapshot notices a mode change", snapshot(tmp, ()) == first,
          False)

with tempfile.TemporaryDirectory() as tmp:
    with open(os.path.join(tmp, "f"), "w") as handle:
        handle.write("a")
    check("an ignored path is left out of the snapshot",
          snapshot(tmp, ("f",)), {})

if failures:
    print(f"FAIL ({len(failures)})")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("OK - all idempotence harness tests passed")
```

- [ ] **Step 3: Lancer le test pour vérifier qu'il échoue**

```bash
python3 scripts/tests/test_idempotence_harness.py
```

Attendu : ÉCHEC — `ModuleNotFoundError: No module named 'idempotence_harness'`.

- [ ] **Step 4: Écrire l'implémentation minimale**

Créer `scripts/idempotence_harness.py` :

```python
#!/usr/bin/env python3
"""Prove that a package's `install` hook can be replayed without changing anything.

The update path replays `install` in place on a live system rather than adding
a new phase, so idempotence stopped being a courtesy the day that was decided:
a hook that appends instead of rewriting, or that stamps a timestamp, corrupts
the machine on its second run. This harness is the control that can actually
fail - it compares a measured result, never a declared intention.

It runs the hook TWICE against a scratch root and compares the resulting trees
byte for byte. Every install hook in the suite is a pure file writer - no
subprocess, no apt, no docker, measured across all six on 2026-09-08 - so this
needs neither root nor network and runs on an ordinary CI runner.

A failure keeps the scratch root and names it, because "the second run differs"
is not actionable on its own: the reader needs the two trees to compare.

Messages are in English, unlike discovery.py's: those reach an operator in the
installer portal, these reach a developer in a CI log, and check-english.sh
judges every added line of a non-test file.

Run: python3 scripts/idempotence_harness.py --package <dir> [--ignore GLOB]...
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "installer"))

from packages.manifest import MANIFEST_NAME, ManifestError, load_manifest  # noqa: E402
from packages.runner import HookError, run_install  # noqa: E402


def _describe(path: str) -> str:
    """A stable one-line description of one filesystem entry."""
    if os.path.islink(path):
        return "symlink:" + os.readlink(path)
    mode = oct(os.stat(path).st_mode & 0o777)
    if os.path.isdir(path):
        return "dir:" + mode
    with open(path, "rb") as handle:
        return "file:{}:{}".format(mode, hashlib.sha256(handle.read()).hexdigest())


def snapshot(root: str, ignore) -> dict:
    """Map every path under `root` to its description, skipping `ignore` globs.

    os.walk with followlinks=False rather than Path.rglob: a symlinked
    directory must be recorded as a symlink and never descended into, or a
    loop in what a hook wrote would hang the proof instead of failing it.
    """
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in dirnames + sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if any(fnmatch.fnmatch(rel, pattern) for pattern in ignore):
                continue
            entries[rel] = _describe(full)
    return entries


def _report(name: str, first: dict, second: dict, root: str) -> str:
    lines = [f"{name}: replaying install CHANGED the tree", ""]
    for rel in sorted(set(second) - set(first)):
        lines.append(f"  added   {rel}")
    for rel in sorted(set(first) - set(second)):
        lines.append(f"  removed {rel}")
    for rel in sorted(set(first) & set(second)):
        if first[rel] != second[rel]:
            lines.append(f"  changed {rel}")
            lines.append(f"      first pass:  {first[rel]}")
            lines.append(f"      second pass: {second[rel]}")
    lines += ["", f"Scratch root kept at: {root}"]
    return "\n".join(lines)


def check_package(package_dir: str, answers=None, hw=None, ignore=()) -> tuple:
    """Return (exit status, report) for one package directory."""
    manifest_path = os.path.join(package_dir, MANIFEST_NAME)
    try:
        manifest = load_manifest(manifest_path)
    except (ManifestError, OSError) as exc:
        return 2, f"{package_dir}: unreadable manifest: {exc}"

    if not manifest.hook_path("install"):
        return 0, f"{manifest.name}: no install hook, nothing to prove"

    answers = {} if answers is None else answers
    hw = {} if hw is None else hw
    root = tempfile.mkdtemp(prefix="nivuus-idempotence-")

    try:
        run_install(manifest, hw, answers, root)
        first = snapshot(root, ignore)
        run_install(manifest, hw, answers, root)
        second = snapshot(root, ignore)
    except HookError as exc:
        return 1, f"{manifest.name}: the install hook failed: {exc}"

    if first == second:
        return 0, (f"{manifest.name}: install is idempotent "
                   f"({len(first)} identical entries)")
    return 1, _report(manifest.name, first, second, root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True,
                        help="directory holding nivuus-package.yaml")
    parser.add_argument("--answers", default="",
                        help="JSON file holding the wizard answers")
    parser.add_argument("--hw", default="",
                        help="JSON file holding the hardware inventory")
    parser.add_argument("--ignore", action="append", default=[],
                        help="relative glob to ignore, repeatable")
    args = parser.parse_args(argv)

    answers = json.loads(pathlib.Path(args.answers).read_text()) if args.answers else {}
    hw = json.loads(pathlib.Path(args.hw).read_text()) if args.hw else {}

    status, report = check_package(args.package, answers, hw, tuple(args.ignore))
    print(report)
    return status


if __name__ == "__main__":
    sys.exit(main())
```

Rendre exécutable : `chmod +x scripts/idempotence_harness.py`

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

```bash
python3 scripts/tests/test_idempotence_harness.py
ruff check scripts/idempotence_harness.py scripts/tests/test_idempotence_harness.py
```

Attendu : `OK - all idempotence harness tests passed`, ruff silencieux.

- [ ] **Step 6: Vérifier le harnais contre un VRAI package**

```bash
python3 scripts/idempotence_harness.py --package ../home-manager
python3 scripts/idempotence_harness.py --package ../home-stock
python3 scripts/idempotence_harness.py --package ./console
```

Attendu : sortie 0 pour chacun. **Si l'un échoue, c'est une vraie
non-idempotence et le rapport nomme le chemin fautif** : corriger le hook du
package concerné fait partie de cette étape, et le rapport dit exactement quoi
regarder. Ne pas neutraliser le harnais avec `--ignore` pour faire passer un
hook qui réécrit mal ; `--ignore` n'est là que pour un chemin dont la variation
est légitime et doit alors être justifiée en commentaire dans le stub CI.

- [ ] **Step 7: Câbler le harnais dans la boucle de tests**

Dans `installer/Makefile`, ajouter `test_idempotence_harness` à la liste de la
cible `test-packages`, après `test_packages_runner` :

```make
	@for t in test_packages_manifest test_packages_capabilities \
	          test_packages_discovery test_packages_conflicts \
	          test_packages_dependencies \
	          test_packages_wizard test_packages_runner \
	          test_idempotence_harness \
	          test_install_engine_bootloader test_install_engine_packages \
```

Puis lancer la boucle complète :

```bash
make -C installer test-packages
```

Attendu : toutes les suites passent, dont `--- test_idempotence_harness`.

- [ ] **Step 8: Commit**

```bash
git add scripts/idempotence_harness.py scripts/tests/test_idempotence_harness.py \
        scripts/tests/fixtures/packages/idempotent scripts/tests/fixtures/packages/appender \
        installer/Makefile
git commit -m "ajoute le harnais qui prouve l'idempotence des hooks install"
```

---

### Task 3: Faire tourner les suites Python d'installer en CI

**Dépôt :** `nivuus/installer`

Le `Makefile` le dit lui-même : « The CI runs no Python tests at all (ci.yml
sets test-paths: "") […] a suite no target runs is a suite the CI cannot run
either ». Le harnais de la tâche 2 est un contrôle capable d'échouer — mais son
propre test ne tournerait nulle part. Cette tâche ferme ce trou.

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: la cible `test-packages` du `installer/Makefile` (tâche 2).
- Produces: un job `packages` dans la CI d'`installer`, qui échoue si une suite
  du moteur échoue.

- [ ] **Step 1: Ajouter le job**

Dans `.github/workflows/ci.yml`, ajouter à la fin de `jobs:` :

```yaml
  packages:
    name: Package engine suites
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      # The 13 suites in `make test-packages` need PyYAML (manifest.py), plus
      # pydantic v2 for test_webapp_models and jinja2 for the console suites.
      # The Makefile documents these three, measured suite by suite.
      - name: Install what the suites import
        run: python -m pip install --upgrade pip pyyaml pydantic jinja2
      - name: Run the package engine suites
        run: make -C installer test-packages
```

- [ ] **Step 2: Lancer localement ce que fera la CI**

```bash
python3 -m venv /tmp/nivuus-ci && /tmp/nivuus-ci/bin/pip -q install pyyaml pydantic jinja2
make -C installer test-packages PYTHON=/tmp/nivuus-ci/bin/python3
```

Attendu : toutes les suites passent. **Si une suite préexistante échoue, elle
est à traiter ici** : soit corrigée, soit retirée de la boucle avec un
commentaire daté qui dit pourquoi et ce qui la remplace. Une suite laissée
rouge rendrait le job inutile dès le premier jour — c'est exactement le mode de
panne que cette tâche vient supprimer.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "fait tourner les suites du moteur de packages dans la CI"
```

---

### Task 4: Script d'appel du harnais depuis la CI partagée

**Dépôt :** `nivuus/.github`

**Files:**
- Create: `scripts/check-idempotence.sh`
- Test: `tests/test_check_idempotence.bats`

**Interfaces:**
- Consumes: `installer/scripts/idempotence_harness.py` (tâche 2), cloné depuis
  `nivuus/installer`.
- Produces: `check-idempotence.sh` — sort 0 quand le dépôt courant n'a pas de
  `nivuus-package.yaml` (rien à prouver) ou que la preuve passe, non-zéro sinon.
  `NIVUUS_INSTALLER_DIR` court-circuite le clone. Consommé par `ci-package.yml`
  (tâche 5) et `release.yml` (tâche 6).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_check_idempotence.bats` :

```bash
#!/usr/bin/env bats

load helpers/repo

setup() {
    SCRIPTS="${BATS_TEST_DIRNAME}/../scripts"
    make_repo
    FAKE_INSTALLER="$(mktemp -d)"
    mkdir -p "$FAKE_INSTALLER/scripts"
    cat > "$FAKE_INSTALLER/scripts/idempotence_harness.py" <<'PY'
import sys
print("harness ran on " + sys.argv[sys.argv.index("--package") + 1])
sys.exit(int(__import__("os").environ.get("FAKE_HARNESS_STATUS", "0")))
PY
}

teardown() {
    [ -n "${REPO:-}" ] && rm -rf "$REPO"
    [ -n "${FAKE_INSTALLER:-}" ] && rm -rf "$FAKE_INSTALLER"
}

@test "exits cleanly when the repository is not a package" {
    NIVUUS_INSTALLER_DIR="$FAKE_INSTALLER" run "$SCRIPTS/check-idempotence.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"skipping"* ]]
}

@test "runs the harness when a manifest is present" {
    commit_file "nivuus-package.yaml" "name: demo" "chore: add manifest"
    NIVUUS_INSTALLER_DIR="$FAKE_INSTALLER" run "$SCRIPTS/check-idempotence.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"harness ran on"* ]]
}

@test "fails when the harness refuses the package" {
    commit_file "nivuus-package.yaml" "name: demo" "chore: add manifest"
    FAKE_HARNESS_STATUS=1 NIVUUS_INSTALLER_DIR="$FAKE_INSTALLER" \
        run "$SCRIPTS/check-idempotence.sh"
    [ "$status" -ne 0 ]
}

@test "fails loudly when the harness is missing rather than passing" {
    commit_file "nivuus-package.yaml" "name: demo" "chore: add manifest"
    rm "$FAKE_INSTALLER/scripts/idempotence_harness.py"
    NIVUUS_INSTALLER_DIR="$FAKE_INSTALLER" run "$SCRIPTS/check-idempotence.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"harness"* ]]
}
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

```bash
bats tests/test_check_idempotence.bats
```

Attendu : ÉCHEC — le script n'existe pas.

- [ ] **Step 3: Écrire l'implémentation minimale**

Créer `scripts/check-idempotence.sh` :

```bash
#!/usr/bin/env bash
# Prove that this repository's install hook can be replayed, by handing it to
# the engine's own harness.
#
# The harness lives in nivuus/installer, next to the runner.py it drives,
# rather than being copied here: two copies of the engine would drift, and the
# home-manager Makefile already validates against the real parser through
# NIVUUS_INSTALLER_DIR for exactly that reason.
#
# Usage: check-idempotence.sh [<package directory>]
# Exit 0: nothing to prove, or the proof passed.
set -uo pipefail

readonly MANIFEST="nivuus-package.yaml"
readonly INSTALLER_URL="https://github.com/nivuus/installer"

main() {
    local package="${1:-.}"

    if [ ! -f "${package}/${MANIFEST}" ]; then
        printf 'No %s in %s, skipping the idempotence proof.\n' \
            "$MANIFEST" "$package"
        return 0
    fi

    local installer="${NIVUUS_INSTALLER_DIR:-}"
    if [ -z "$installer" ]; then
        installer="$(mktemp -d)"
        git clone -q --depth 1 "$INSTALLER_URL" "$installer" || {
            printf 'Could not clone %s to obtain the harness.\n' "$INSTALLER_URL"
            return 1
        }
    fi

    local harness="${installer}/scripts/idempotence_harness.py"
    # A missing harness must FAIL, never skip: a proof that quietly does not
    # run is worse than no proof, because the release goes out believing it ran.
    if [ ! -f "$harness" ]; then
        printf 'The idempotence harness is missing at %s\n' "$harness"
        return 1
    fi

    python3 "$harness" --package "$package"
}

main "$@"
```

Rendre exécutable : `chmod +x scripts/check-idempotence.sh`

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

```bash
bats tests/test_check_idempotence.bats
shellcheck -S warning scripts/check-idempotence.sh
```

Attendu : 4 tests OK, shellcheck silencieux.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-idempotence.sh tests/test_check_idempotence.bats
git commit -m "feat: run the package idempotence proof from the shared socle"
```

---

### Task 5: Workflow réutilisable de vérification de package

**Dépôt :** `nivuus/.github`

Ce workflow tourne sur les **pull requests**, pour qu'une régression
d'idempotence soit vue avant la fusion. Sans lui, elle ne serait découverte
qu'après le merge sur une `main` protégée, où le retour en arrière est coûteux.

**Files:**
- Create: `.github/workflows/ci-package.yml`
- Test: `tests/test_ci_package_wiring.bats`

**Interfaces:**
- Consumes: `scripts/check-idempotence.sh` (tâche 4).
- Produces: workflow appelable en
  `uses: nivuus/.github/.github/workflows/ci-package.yml@main`, avec l'entrée
  `package-dir` (défaut `.`).

- [ ] **Step 1: Écrire le test de câblage qui échoue**

Créer `tests/test_ci_package_wiring.bats` :

```bash
#!/usr/bin/env bats

setup() {
    WF="${BATS_TEST_DIRNAME}/../.github/workflows/ci-package.yml"
}

@test "ci-package workflow exists" {
    [ -f "$WF" ]
}

@test "is callable by other repositories" {
    grep -q "workflow_call:" "$WF"
}

@test "exposes an overridable package directory" {
    grep -q "package-dir:" "$WF"
}

@test "checks out the socle to reach its scripts" {
    grep -q "repository: nivuus/.github" "$WF"
    grep -q ".nivuus-socle" "$WF"
}

@test "runs the idempotence proof" {
    grep -q "check-idempotence.sh" "$WF"
}

@test "passes github expressions through env, never into run blocks" {
    run grep -nE '^ +run:.*\$\{\{' "$WF"
    [ "$status" -ne 0 ]
}

# PyYAML is what manifest.py imports; without it the harness cannot even parse
# the manifest and the job would fail for the wrong reason.
@test "installs what the harness imports" {
    grep -q "pyyaml" "$WF"
}
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

```bash
bats tests/test_ci_package_wiring.bats
```

Attendu : ÉCHEC — le workflow n'existe pas.

- [ ] **Step 3: Écrire le workflow**

Créer `.github/workflows/ci-package.yml` :

```yaml
name: CI Package

on:
  workflow_call:
    inputs:
      package-dir:
        description: Directory holding nivuus-package.yaml
        type: string
        default: "."

jobs:
  package:
    name: Package contract
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check out the shared socle
        uses: actions/checkout@v4
        with:
          repository: nivuus/.github
          ref: main
          path: .nivuus-socle

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install what the harness imports
        run: python -m pip install --upgrade pip pyyaml

      - name: Prove the install hook is idempotent
        env:
          PACKAGE_DIR: ${{ inputs.package-dir }}
        run: .nivuus-socle/scripts/check-idempotence.sh "$PACKAGE_DIR"
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

```bash
bats tests/test_ci_package_wiring.bats
```

Attendu : 7 tests OK.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci-package.yml tests/test_ci_package_wiring.bats
git commit -m "feat: add a reusable workflow proving the package contract"
```

---

### Task 6: Workflow réutilisable de release

**Dépôt :** `nivuus/.github`

**Files:**
- Create: `.github/workflows/release.yml`
- Test: `tests/test_release_wiring.bats`

**Interfaces:**
- Consumes: `scripts/derive-version.sh` (tâche 1),
  `scripts/check-idempotence.sh` (tâche 4).
- Produces: workflow appelable en
  `uses: nivuus/.github/.github/workflows/release.yml@main`, entrées
  `package-dir` (défaut `.`), `version-files` (défaut vide — détection
  automatique), `build-command` (défaut vide), `artifact-glob` (défaut vide).
  Publie `<nom>-<version>.tar.gz`, `SHA256SUMS`, les notes, et pose le tag
  `v<version>`.

- [ ] **Step 1: Écrire le test de câblage qui échoue**

Créer `tests/test_release_wiring.bats` :

```bash
#!/usr/bin/env bats

setup() {
    WF="${BATS_TEST_DIRNAME}/../.github/workflows/release.yml"
}

@test "release workflow exists" {
    [ -f "$WF" ]
}

@test "is callable by other repositories" {
    grep -q "workflow_call:" "$WF"
}

@test "exposes the per-repository knobs" {
    grep -q "package-dir:" "$WF"
    grep -q "version-files:" "$WF"
    grep -q "build-command:" "$WF"
    grep -q "artifact-glob:" "$WF"
}

# Without full history there is no tag to derive from and every release would
# look like the first one.
@test "fetches full history so the last tag is visible" {
    grep -q "fetch-depth: 0" "$WF"
}

@test "derives the version from the shared script" {
    grep -q "derive-version.sh" "$WF"
}

# Exit 3 means "nothing releasable"; treating it as a failure would paint every
# documentation-only merge red.
@test "treats an empty release as success, not failure" {
    grep -q "eq 3" "$WF"
}

@test "gates publication on the idempotence proof" {
    grep -q "check-idempotence.sh" "$WF"
}

# git archive exports TRACKED files only. Building the tarball from the working
# tree would ship .env files and logs into a public release.
@test "builds the archive from tracked files only" {
    grep -q "git archive" "$WF"
}

@test "publishes checksums alongside the archive" {
    grep -q "SHA256SUMS" "$WF"
    grep -q "sha256sum" "$WF"
}

@test "attests the build provenance" {
    grep -q "attest-build-provenance" "$WF"
}

@test "asks for the permissions the release needs" {
    grep -q "contents: write" "$WF"
    grep -q "attestations: write" "$WF"
    grep -q "id-token: write" "$WF"
}

@test "passes github expressions through env, never into run blocks" {
    run grep -nE '^ +run:.*\$\{\{' "$WF"
    [ "$status" -ne 0 ]
}

# The manifest version travels in the published archive, never in main: the
# branch is protected with enforce_admins, so a bump commit could not land.
@test "injects the version into the archive rather than committing it" {
    grep -q "0.0.0" "$WF"
}
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

```bash
bats tests/test_release_wiring.bats
```

Attendu : ÉCHEC — le workflow n'existe pas.

- [ ] **Step 3: Écrire le workflow**

Créer `.github/workflows/release.yml` :

```yaml
name: Release

on:
  workflow_call:
    inputs:
      package-dir:
        description: Directory holding nivuus-package.yaml, when this repo is a package
        type: string
        default: "."
      version-files:
        description: Space-separated files whose version field is rewritten in the archive
        type: string
        default: ""
      build-command:
        description: Optional command building a native artifact (a .deb, a wheel)
        type: string
        default: ""
      artifact-glob:
        description: Optional glob of extra assets to attach, relative to the repo
        type: string
        default: ""

jobs:
  release:
    name: Publish release
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
      attestations: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Check out the shared socle
        uses: actions/checkout@v4
        with:
          repository: nivuus/.github
          ref: main
          path: .nivuus-socle

      - name: Derive the next version
        id: version
        run: |
          set +e
          version="$(.nivuus-socle/scripts/derive-version.sh)"
          status=$?
          set -e
          # 3 means no commit since the last tag justifies a release. A
          # documentation-only merge must end green, not red.
          if [ "$status" -eq 3 ]; then
            echo "Nothing releasable since the last tag."
            echo "release=no" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          [ "$status" -eq 0 ] || exit "$status"
          echo "Next version: $version"
          echo "version=$version" >> "$GITHUB_OUTPUT"
          echo "release=yes" >> "$GITHUB_OUTPUT"

      - uses: actions/setup-python@v5
        if: steps.version.outputs.release == 'yes'
        with:
          python-version: "3.12"

      - name: Install what the harness imports
        if: steps.version.outputs.release == 'yes'
        run: python -m pip install --upgrade pip pyyaml

      - name: Prove the install hook is idempotent
        if: steps.version.outputs.release == 'yes'
        env:
          PACKAGE_DIR: ${{ inputs.package-dir }}
        run: .nivuus-socle/scripts/check-idempotence.sh "$PACKAGE_DIR"

      - name: Build the native artifact
        if: steps.version.outputs.release == 'yes' && inputs.build-command != ''
        env:
          BUILD_COMMAND: ${{ inputs.build-command }}
        run: bash -c "$BUILD_COMMAND"

      - name: Export the tracked tree and stamp the version into it
        if: steps.version.outputs.release == 'yes'
        env:
          VERSION: ${{ steps.version.outputs.version }}
          VERSION_FILES: ${{ inputs.version-files }}
        run: |
          # git archive exports TRACKED files only - never the working tree.
          # That is the guarantee that no .env, no log and no build artefact
          # reaches a public release; iso-build/build.sh relies on the same one.
          mkdir -p /tmp/export release-assets
          git archive HEAD | tar -x -C /tmp/export

          # main is protected with enforce_admins, so the version cannot be
          # committed there: it is stamped into the ARCHIVE instead, and the
          # checked-in manifest keeps 0.0.0 to declare itself a dev copy.
          files="$VERSION_FILES"
          if [ -z "$files" ]; then
            for candidate in nivuus-package.yaml package.json pyproject.toml; do
              [ -f "/tmp/export/$candidate" ] && files="$files $candidate"
            done
          fi
          for file in $files; do
            path="/tmp/export/$file"
            [ -f "$path" ] || continue
            case "$file" in
              *.yaml|*.yml)
                sed -i "s/^version: .*/version: ${VERSION}/" "$path" ;;
              *.json)
                sed -i "s/\"version\": *\"[^\"]*\"/\"version\": \"${VERSION}\"/" "$path" ;;
              *.toml)
                sed -i "s/^version = .*/version = \"${VERSION}\"/" "$path" ;;
            esac
            echo "Stamped $VERSION into $file"
          done

          name="${GITHUB_REPOSITORY##*/}"
          tar -czf "release-assets/${name}-${VERSION}.tar.gz" -C /tmp/export .

      - name: Attach the native artifact
        if: steps.version.outputs.release == 'yes' && inputs.artifact-glob != ''
        env:
          ARTIFACT_GLOB: ${{ inputs.artifact-glob }}
        run: |
          # shellcheck disable=SC2086
          cp $ARTIFACT_GLOB release-assets/ || {
            echo "The build command declared artefacts but none matched $ARTIFACT_GLOB"
            exit 1
          }

      - name: Generate checksums
        if: steps.version.outputs.release == 'yes'
        run: |
          cd release-assets
          # Never `sha256sum * > SHA256SUMS`: the redirect creates the file
          # before the glob expands, so it would hash its own empty self.
          sha256sum -- * > "${RUNNER_TEMP}/SHA256SUMS"
          mv "${RUNNER_TEMP}/SHA256SUMS" SHA256SUMS
          cat SHA256SUMS

      - name: Attest the build provenance
        if: steps.version.outputs.release == 'yes'
        uses: actions/attest-build-provenance@v2
        with:
          subject-path: release-assets/*.tar.gz

      - name: Generate the release notes
        if: steps.version.outputs.release == 'yes'
        env:
          VERSION: ${{ steps.version.outputs.version }}
        run: |
          previous="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
          {
            echo "## Changements"
            echo
            if [ -z "$previous" ]; then
              git log --no-merges --pretty=format:'- %s (%h)'
            else
              git log "${previous}..HEAD" --no-merges --pretty=format:'- %s (%h)'
            fi
            echo
            echo
            echo "## Pose"
            echo
            echo '```'
            echo "nivuus update ${GITHUB_REPOSITORY##*/}"
            echo '```'
          } > RELEASE_NOTES.md
          cat RELEASE_NOTES.md

      - name: Tag and publish
        if: steps.version.outputs.release == 'yes'
        env:
          GH_TOKEN: ${{ github.token }}
          VERSION: ${{ steps.version.outputs.version }}
        run: |
          git tag -a "v${VERSION}" -m "Release v${VERSION}"
          git push origin "v${VERSION}"
          gh release create "v${VERSION}" \
            --title "${GITHUB_REPOSITORY##*/} v${VERSION}" \
            --notes-file RELEASE_NOTES.md \
            release-assets/*
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

```bash
bats tests/test_release_wiring.bats
bats tests/
```

Attendu : 13 tests OK pour le câblage de release, et la suite complète verte.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml tests/test_release_wiring.bats
git commit -m "feat: add the shared release workflow for every Nivuus package"
```

---

### Task 7: Câbler le workflow de release dans les dix dépôts

**Dépôts :** les dix.

**Files (par dépôt) :**
- Create: `.github/workflows/release.yml` (stub) dans `desk`, `home-desk`,
  `home-manager`, `home-stock`, `installer`, `marketplace`, `media-manager`,
  `mqtt`, `retro`
- Modify: `shell/.github/workflows/release.yml` (remplace l'ancien workflow
  manuel)

**Interfaces:**
- Consumes: `nivuus/.github/.github/workflows/release.yml@main` (tâche 6).
- Produces: une release publiée par dépôt à la première fusion qualifiante.

- [ ] **Step 1: Poser le stub commun dans les sept dépôts sans artefact natif**

Dans `desk`, `home-desk`, `home-manager`, `home-stock`, `installer`,
`marketplace`, `media-manager`, créer `.github/workflows/release.yml` :

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  release:
    uses: nivuus/.github/.github/workflows/release.yml@main
    permissions:
      contents: write
      id-token: write
      attestations: write
```

- [ ] **Step 2: Poser le stub de `mqtt`, qui publie aussi son .deb**

Dans `mqtt`, créer `.github/workflows/release.yml` :

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  release:
    uses: nivuus/.github/.github/workflows/release.yml@main
    permissions:
      contents: write
      id-token: write
      attestations: write
    with:
      # The agent ships as a Debian package; the hook that will install it
      # (plan C) consumes this asset rather than rebuilding it on the target.
      build-command: "npm ci && npm run package:deb"
      artifact-glob: "*.deb"
```

- [ ] **Step 3: Poser le stub de `retro`, qui publie aussi son wheel**

Dans `retro`, créer `.github/workflows/release.yml` :

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  release:
    uses: nivuus/.github/.github/workflows/release.yml@main
    permissions:
      contents: write
      id-token: write
      attestations: write
    with:
      build-command: "python -m pip install --upgrade build && python -m build --wheel"
      artifact-glob: "dist/*.whl"
```

- [ ] **Step 4: Remplacer le workflow manuel de `shell`**

Dans `shell`, **écraser** `.github/workflows/release.yml` par le stub commun de
l'étape 1. L'ancien workflow `workflow_dispatch` avec choix de bump disparaît :
la version vient désormais des commits, et ses étapes de test sont déjà
couvertes par `ci.yml`.

- [ ] **Step 5: Basculer les manifestes sur le marqueur de dev**

Les cinq manifestes existants déclarent `version: 1.0.0`, et `console` aussi.
La spec fait du tag l'unique source de vérité : la copie versionnée doit donc
porter `0.0.0`, que le workflow remplace dans l'archive publiée.

Dans `desk`, `home-desk`, `home-manager`, `home-stock`, `media-manager` et
`installer/console`, remplacer dans `nivuus-package.yaml` :

```yaml
version: 0.0.0
```

Vérifier que le parseur l'accepte encore (`manifest.py:186` impose
`MAJOR.MINOR.PATCH` strict, et `0.0.0` s'y conforme) :

```bash
cd ~/Projects/Nivuus/packages/installer
python3 -c "
import sys; sys.path.insert(0, 'installer')
from packages.manifest import load_manifest
for p in ['../desk', '../home-desk', '../home-manager', '../home-stock',
          '../media-manager', 'console']:
    m = load_manifest(p + '/nivuus-package.yaml')
    print(m.name, m.version)
"
```

Attendu : les six noms suivis de `0.0.0`.

**Conséquence transitoire à connaître** : jusqu'au plan D, `iso-build/build.sh`
embarque encore les packages par `git archive HEAD`, donc une ISO construite
entre ce plan et le plan D enregistrera `0.0.0` dans
`/etc/nivuus/packages.json`. L'updater du plan B verra alors toute release
comme une mise à jour — ce qui est correct, mais fait qu'une machine
fraîchement installée sera immédiatement en retard. C'est précisément ce que le
plan D corrige en faisant consommer les releases par `build.sh`.

- [ ] **Step 6: Vérifier la dérivation à blanc, dépôt par dépôt**

Depuis chaque clone, avec le socle cloné à côté :

```bash
git clone -q --depth 1 https://github.com/nivuus/.github /tmp/nivuus-socle
for repo in desk home-desk home-manager home-stock installer \
            marketplace media-manager mqtt retro shell; do
  printf '%-14s ' "$repo"
  ( cd ~/Projects/Nivuus/packages/$repo && \
    /tmp/nivuus-socle/scripts/derive-version.sh || echo "(rien à publier: $?)" )
done
```

Attendu : `1.0.0` partout, aucun dépôt n'ayant de tag `v*`. Un dépôt qui
imprimerait autre chose a un tag préexistant qu'il faut examiner avant de
fusionner ce stub.

- [ ] **Step 7: Commit, un dépôt à la fois**

Dans chaque dépôt, sur une branche (jamais sur `main`, protégée sur quatre
d'entre eux) :

```bash
git checkout -b ci/release-workflow
git add .github/workflows/release.yml
git commit -m "ci: publish releases through the shared workflow"
git push -u origin ci/release-workflow
gh pr create --title "ci: publish releases through the shared workflow" \
             --body "Câble le workflow de release partagé du socle."
```

**Le titre de PR doit être en anglais conventionnel** — `policy / Coding rules`
le refuse sinon, et renommer une PR ne relance pas la CI : il faut renommer
**puis** pousser.

**Après la première fusion, vérifier que le tag a bien été poussé** :
la protection de branche couvre les branches, pas les tags, mais une règle de
protection de tag existerait sans être visible dans `gh repo view`. Si
`git push origin v<version>` échoue dans le journal du workflow, le diagnostic
passe par `gh api repos/nivuus/<dépôt>/actions/runs/<id>/jobs` — `gh run view
--log` rend vide avec exit 0 sur un workflow réutilisable.

---

### Task 8: Câbler la preuve d'idempotence dans la CI des packages

**Dépôts :** `desk`, `home-desk`, `home-manager`, `home-stock`, `installer`,
`media-manager` — les six qui portent déjà un `nivuus-package.yaml`.
`marketplace`, `mqtt`, `retro` et `shell` reçoivent ce câblage au plan C, quand
ils gagnent leur manifeste.

**Files:**
- Modify: `desk/.github/workflows/ci.yml`, `home-stock/.github/workflows/ci.yml`
- Create: `home-desk/.github/workflows/ci.yml`,
  `home-manager/.github/workflows/ci.yml`,
  `media-manager/.github/workflows/ci.yml` — ces trois dépôts n'ont **aucun**
  workflow aujourd'hui.
- Modify: `installer/.github/workflows/ci.yml` (pour `console`)

**Interfaces:**
- Consumes: `nivuus/.github/.github/workflows/ci-package.yml@main` (tâche 5).
- Produces: une PR qui casse l'idempotence d'un hook devient rouge avant fusion.

- [ ] **Step 1: Ajouter le job aux deux `ci.yml` existants**

Dans `desk/.github/workflows/ci.yml` et `home-stock/.github/workflows/ci.yml`,
ajouter à `jobs:` :

```yaml
  package:
    uses: nivuus/.github/.github/workflows/ci-package.yml@main
```

- [ ] **Step 2: Créer les trois `ci.yml` manquants**

Dans `home-desk`, `home-manager` et `media-manager`, créer
`.github/workflows/ci.yml` :

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  policy:
    uses: nivuus/.github/.github/workflows/policy.yml@main
  security:
    uses: nivuus/.github/.github/workflows/security.yml@main
  package:
    uses: nivuus/.github/.github/workflows/ci-package.yml@main
```

- [ ] **Step 3: Câbler `console` dans la CI d'`installer`**

Dans `installer/.github/workflows/ci.yml`, ajouter :

```yaml
  package:
    uses: nivuus/.github/.github/workflows/ci-package.yml@main
    with:
      # console is a package living inside this repository rather than one of
      # its own; it is the only manifest here, and the only one to prove.
      package-dir: console
```

- [ ] **Step 4: Vérifier la preuve localement avant de pousser**

```bash
git clone -q --depth 1 https://github.com/nivuus/.github /tmp/nivuus-socle
export NIVUUS_INSTALLER_DIR=~/Projects/Nivuus/packages/installer
for p in desk home-desk home-manager home-stock media-manager; do
  printf '=== %s\n' "$p"
  /tmp/nivuus-socle/scripts/check-idempotence.sh ~/Projects/Nivuus/packages/$p
done
/tmp/nivuus-socle/scripts/check-idempotence.sh \
  ~/Projects/Nivuus/packages/installer/console
```

Attendu : sortie 0 pour les six. Un échec ici est une vraie non-idempotence à
corriger dans le hook concerné **avant** de câbler la CI — sinon la première PR
de chaque dépôt part rouge pour une raison sans rapport avec elle.

- [ ] **Step 5: Commit, un dépôt à la fois**

```bash
git checkout -b ci/package-contract
git add .github/workflows/ci.yml
git commit -m "ci: prove the install hook stays idempotent"
git push -u origin ci/package-contract
gh pr create --title "ci: prove the install hook stays idempotent" \
             --body "Câble ci-package.yml du socle."
```

---

## Vérification finale du plan A

Une fois les huit tâches fusionnées :

- [ ] Une fusion `feat:` sur n'importe lequel des dix dépôts publie une release
      portant `v1.0.0`, avec `<nom>-1.0.0.tar.gz`, `SHA256SUMS`, une attestation
      de provenance et des notes.
- [ ] `tar -xzOf <archive> ./nivuus-package.yaml | grep '^version:'` donne
      `1.0.0`, alors que le fichier dans `main` porte toujours `0.0.0`.
- [ ] Une fusion `docs:` seule ne publie rien et laisse la CI verte.
- [ ] Une PR qui rend un hook `install` non idempotent devient rouge sur
      `package / Package contract`, et le rapport nomme le chemin fautif.
- [ ] `sha256sum -c SHA256SUMS` passe sur les assets téléchargés.

Ce dernier point est la précondition du **plan B** : l'updater vérifie ces
sommes avant d'écrire quoi que ce soit.
