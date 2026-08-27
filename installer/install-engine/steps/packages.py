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
