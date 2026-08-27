"""Step 9: plan and apply the selected Nivuus packages.

The split between the two functions here IS the contract. plan_packages()
decides and never writes: it discovers, filters on capabilities, validates
answers, detects conflicts and runs every resolve hook. Each of those can
refuse, and all of them happen before partition() has touched the disk. What
it returns is the kernel command line the bootloader step will write.

apply_packages() writes, and only then: modules, hugepages, apt, the install
hook, and the activation unit that carries the package into first boot.

The activate phase is armed HERE, and arming it means putting three separate
things on the target - the unit, the CLI it runs, and the package directories
the CLI rediscovers at first boot. None of them get there by any other route:
copy_payload() copies the repo to /opt/nivuus, and the packages live outside
the repo (they are sibling repositories embedded in the live medium at
/opt/nivuus-packages, which is the LIVE root, not the target). Every one of
those copies is therefore load-bearing, and every one of them fails loudly:
an install that reports success while first-boot activation cannot possibly
run is the exact failure this file exists to prevent.
"""
from __future__ import annotations

import json
import os
import shutil

from packages.capabilities import detect_capabilities
from packages.conflicts import check_conflicts
from packages.discovery import discover, eligibility
from packages.manifest import MANIFEST_NAME, ManifestError
from packages.runner import HookError, run_install, run_resolve
from packages.wizard import WizardError, load_questions, validate_answers

from .bootloader import grub_defaults
from .util import StepError, chroot_run, write_file

STATE_REL_PATH = "etc/nivuus/packages.json"
SYSCTL_REL_PATH = "etc/sysctl.d/60-nivuus-packages.conf"
MODULES_REL_PATH = "etc/modules"
UNIT_NAME = "nivuus-package-activate@.service"
UNIT_REL_DIR = "etc/systemd/system"
WANTS_REL_DIR = "etc/systemd/system/multi-user.target.wants"
PACKAGES_REL_DIR = "opt/nivuus-packages"
CLI_REL_PATH = "installer/packages/activate_cli.py"
UNIT_SRC_REL_PATH = "configs/systemd/" + UNIT_NAME
# activate_cli.py parses the manifest again at first boot, and manifest.py
# imports PyYAML. Nothing else in the target's package list pulls it in, so a
# package with an activate phase would fail on every boot with ImportError.
ACTIVATE_APT = ["python3-yaml"]
# A 2 MiB hugepage is the x86-64 default; the manifest speaks MiB because
# "how much memory does the guest need" is the question an author can answer.
HUGEPAGE_MIB = 2


def _validate_selection(raw) -> dict:
    """Validate the shape of config['packages'] before anything else uses it.

    plan_packages is not only reached behind the portal's Pydantic-validated
    config: the engine is documented as runnable standalone against a
    loopback disk from a hand-written config.json (see run.py's docstring),
    so it cannot assume a validated caller. A non-mapping value here must not
    surface as a raw Python TypeError from deep inside a dict lookup - that
    reads as an installer bug, not something an operator can act on. It must
    be a mapping of package name -> mapping of answers (or no answers at
    all), refused by field name and shape otherwise.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StepError(
            "config « packages » doit être un mapping nom de package → "
            f"réponses ; reçu {type(raw).__name__}")
    for name, answers in raw.items():
        if not isinstance(name, str):
            raise StepError(
                f"config « packages » : la clé {name!r} doit être une chaîne "
                "(nom de package)")
        if answers is not None and not isinstance(answers, dict):
            raise StepError(
                f"config « packages » : les réponses de {name!r} doivent "
                f"être un mapping ; reçu {type(answers).__name__}")
    return raw


def plan_packages(config: dict, hw: dict, emit):
    """Decide everything, write nothing. Raises StepError on any refusal.

    Returns (plan, kernel_cmdline) where plan is a list of
    (manifest, validated answers, resolution).
    """
    selected = _validate_selection(config.get("packages"))
    if not selected:
        return [], ()

    emit.info("packages", 60, f"Planning {len(selected)} package(s)…")
    manifests, errors = discover()
    for source, message in errors:
        emit.warn("packages", 60, f"Manifeste ignoré ({source}) : {message}")

    by_name = {m.name: m for m in manifests}
    unknown = sorted(set(selected) - set(by_name))
    if unknown:
        # A name missing from by_name is either simply absent, or it is
        # exactly the name discover() just refused because two or more
        # manifests declared it - discover()'s own contract is that a
        # collision's `source` IS the package name, not a path (see its
        # docstring). Without this cross-reference the real cause is
        # stranded in the warn stream above, and an operator on a
        # screenless machine reads the failure, not the log.
        collisions = {source: message for source, message in errors
                      if source in unknown}
        details = [collisions.get(name, name) for name in unknown]
        raise StepError(
            "packages sélectionnés mais introuvables sur ce support : "
            + "; ".join(details))

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

    # The allowlist that guards /etc/default/grub lives in bootloader.py, and
    # bootloader.py runs at step 7 - after partition and debootstrap. A
    # manifest yielding `vfio-pci.ids=$(x)` would therefore blow up on an
    # already-wiped disk, which is precisely what "decide before you write"
    # forbids. Render it here purely for its validation and throw the result
    # away: the same call runs again for real in install_bootloader().
    try:
        grub_defaults(tuple(cmdline))
    except ValueError as exc:
        raise StepError(f"paramètre noyau refusé : {exc}") from exc

    return plan, tuple(cmdline)


def _deploy_activation(plan, target: str, nivuus_dir: str, emit) -> None:
    """Put on the target everything the first-boot activate phase needs.

    Three copies, all mandatory, all fatal when they fail:
      1. the template unit, from the payload rather than re-written inline so
         configs/systemd/ stays the single source of truth for it;
      2. the CLI made executable where it already lives - the unit's ExecStart
         points into /opt/nivuus, because activate_cli.py computes its own
         sys.path from __file__ and would not survive being moved;
      3. the SELECTED packages' directories under /opt/nivuus-packages. Only
         the ones that were planned: the live medium may carry packages the
         operator declined, and copying those would claim an install that did
         not happen.
    """
    payload = os.path.join(target, nivuus_dir.lstrip("/"))

    src_unit = os.path.join(payload, UNIT_SRC_REL_PATH)
    if not os.path.isfile(src_unit):
        raise StepError(
            f"unité d'activation introuvable dans la charge utile : {src_unit} "
            "— la phase activate ne pourrait pas démarrer au premier boot")
    dest_unit = os.path.join(target, UNIT_REL_DIR, UNIT_NAME)
    os.makedirs(os.path.dirname(dest_unit), exist_ok=True)
    shutil.copyfile(src_unit, dest_unit)
    os.chmod(dest_unit, 0o644)

    cli = os.path.join(payload, CLI_REL_PATH)
    if not os.path.isfile(cli):
        raise StepError(
            f"activate_cli.py introuvable dans la charge utile : {cli} — "
            "l'ExecStart de l'unité d'activation pointerait dans le vide")
    os.chmod(cli, 0o755)

    for manifest, _, _ in plan:
        dest = os.path.join(target, PACKAGES_REL_DIR, manifest.name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(manifest.root, dest, symlinks=True)
        if not os.path.isfile(os.path.join(dest, MANIFEST_NAME)):
            raise StepError(
                f"package « {manifest.label} » : copie incomplète vers {dest}")
        emit.info("packages", 91,
                  f"Package « {manifest.label} » copié vers "
                  f"/{PACKAGES_REL_DIR}/{manifest.name}")


def _enable_activation(target: str, name: str) -> None:
    """Arm nivuus-package-activate@<name> for first boot, by symlink.

    `systemctl enable` on a template unit whose [Install] says
    WantedBy=multi-user.target does exactly one thing: create
    multi-user.target.wants/<unit>@<instance>.service pointing at the template
    file. Doing it directly is both simpler and more robust than shelling into
    the chroot - systemctl needs a working D-Bus or an offline-mode guess, and
    this project has been bitten more than once by systemctl failing silently
    in a constrained environment (see CLAUDE.md). A symlink either exists or
    raises OSError; there is no third, quiet outcome.
    """
    wants = os.path.join(target, WANTS_REL_DIR)
    os.makedirs(wants, exist_ok=True)
    link = os.path.join(wants, f"nivuus-package-activate@{name}.service")
    if os.path.islink(link) or os.path.exists(link):
        os.unlink(link)
    try:
        os.symlink("/" + UNIT_REL_DIR + "/" + UNIT_NAME, link)
    except OSError as exc:
        raise StepError(
            f"package « {name} » : impossible d'armer l'activation au premier "
            f"boot ({link}) : {exc}") from exc


def apply_packages(plan, target: str, nivuus_dir: str, hw: dict, emit) -> None:
    """Write everything the plan decided, in dependency order."""
    if not plan:
        return

    modules: list[str] = []
    hugepages_mib = 0
    apt: list[str] = list(ACTIVATE_APT)
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
        # Not fatal - a package may still be usable without an optional
        # dependency, and failing the whole install here would be worse than
        # the risk. But it must never be silent: the install hook is about to
        # run against a system that does not have what it asked for, and the
        # operator has to be able to connect the two.
        proc = chroot_run(target, ["apt-get", "install", "-y", *apt],
                          check=False)
        if proc.returncode != 0:
            emit.warn("packages", 94,
                      f"apt-get install a échoué (code {proc.returncode}) pour "
                      f": {' '.join(apt)} — les hooks install vont s'exécuter "
                      "sans ces dépendances")

    _deploy_activation(plan, target, nivuus_dir, emit)

    # The answers must outlive the portal: the activate phase runs at first
    # boot, long after there is anyone left to ask. Written incrementally,
    # after each package succeeds, and not once at the end: if package 2's
    # install hook raises, package 1 has already written into the target and
    # been armed for first boot. A state file written only on full success
    # would leave that residue undescribed - the machine would activate a
    # package no file admits to having installed.
    state = {}
    state_path = os.path.join(target, STATE_REL_PATH)
    for manifest, answers, _ in plan:
        emit.info("packages", 95, f"Applying package « {manifest.label} »…")
        try:
            run_install(manifest, hw, answers, target, emit)
        except HookError as exc:
            raise StepError(str(exc)) from exc
        state[manifest.name] = {"version": manifest.version, "answers": answers}
        write_file(state_path,
                   json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        _enable_activation(target, manifest.name)
