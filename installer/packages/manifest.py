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

# The only keys admitted under `requires:`. The list is CLOSED: an unknown key
# is refused rather than ignored, because a silently dropped `package:` in the
# singular would install a satellite before the package it needs.
REQUIRES_KEYS = ("capabilities", "features", "packages")


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
    # Names of packages that must be installed BEFORE this one. The engine
    # orders installs alphabetically by default, which puts `home-desk` before
    # `home-manager`: a satellite would drop its custom_component into a
    # directory its base package has not created yet.
    packages: tuple[str, ...] = ()
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


def _str_pairs(raw: Any, what: str, kind: str) -> list[tuple[str, str]]:
    """Sorted (key, value) pairs from a mapping, both sides required to be strings.

    PyYAML implements YAML 1.1, where a bare `on`, `off`, `yes` or `no` key
    parses as a boolean - so `claims: {on: exclusive}` is a plausible authoring
    slip that yields a bool key. Sorting mixed-type keys raises TypeError, which
    would leave this module as an unhandled crash instead of the ManifestError
    every caller is written to expect. So keys and values are checked, and
    rejected with a specific message, before anything sorts or coerces them.
    """
    if not isinstance(raw, dict):
        raise ManifestError(f"{what}: '{kind}' must be a mapping")
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ManifestError(
                f"{what}: {kind} key {key!r} must be a string - quote it if it "
                "looks like on/off/yes/no, which YAML parses as a boolean")
        if not isinstance(value, str):
            raise ManifestError(
                f"{what}: {kind} value {value!r} for {key!r} must be a string")
    return sorted(raw.items())


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
    unknown = [k for k in requires if k not in REQUIRES_KEYS]
    if unknown:
        raise ManifestError(
            f"{what}: unknown key(s) under 'requires': {sorted(unknown)}; "
            f"expected {list(REQUIRES_KEYS)} - a misspelt 'package' would "
            "silently drop a dependency and install this package before the "
            "one it needs")

    required_packages = _str_list(requires, "packages", what)
    for dependency in required_packages:
        if not NAME_RE.match(dependency):
            raise ManifestError(
                f"{what}: required package {dependency!r} must match "
                f"{NAME_RE.pattern} - it is the name of another package")
        if dependency == name:
            raise ManifestError(
                f"{what}: le package {name!r} ne peut pas dépendre de "
                "lui-même")

    claims_raw = data.get("claims") or {}
    claims = []
    for resource, mode in _str_pairs(claims_raw, what, "claims"):
        if mode not in CLAIM_MODES:
            raise ManifestError(
                f"{what}: claim {resource!r} mode must be one of {CLAIM_MODES}, "
                f"got {mode!r}")
        claims.append((resource, mode))

    wizard = data.get("wizard") or {}
    if not isinstance(wizard, dict):
        raise ManifestError(f"{what}: 'wizard' must be a mapping")
    questions_raw = wizard.get("questions")
    if questions_raw is not None and not isinstance(questions_raw, str):
        raise ManifestError(
            f"{what}: wizard 'questions' must be a string, got {questions_raw!r}")
    questions_file = _safe_relpath(questions_raw, what) if questions_raw else ""

    hooks_raw = data.get("hooks") or {}
    hooks = []
    for phase, rel in _str_pairs(hooks_raw, what, "hooks"):
        if phase not in HOOK_PHASES:
            raise ManifestError(
                f"{what}: unknown hook phase {phase!r}; expected {HOOK_PHASES}")
        hooks.append((phase, _safe_relpath(rel, what)))

    return Manifest(
        name=name, version=version, label=label, tier=tier, root=root,
        capabilities=_str_list(requires, "capabilities", what),
        features=_str_list(requires, "features", what),
        packages=required_packages,
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
