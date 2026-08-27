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

    # PyYAML implements YAML 1.1: a bare on/off/yes/no key parses as a bool,
    # not a string. Built through real YAML (not a hand-built dict), because
    # the point is that PyYAML itself produces the bool.
    (d / "bool-key-claims.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\nclaims:\n  on: exclusive\n"
        "  gpu: exclusive\n")
    check_raises("bare 'on:' claims key refused",
                 lambda: load_manifest(str(d / "bool-key-claims.yaml")),
                 "must be a string")

    (d / "bool-key-hooks.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\nhooks:\n  yes: hooks/x.py\n")
    check_raises("bare 'yes:' hooks key refused",
                 lambda: load_manifest(str(d / "bool-key-hooks.yaml")),
                 "must be a string")

    (d / "int-key-claims.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\nclaims:\n  1: exclusive\n")
    check_raises("integer claims key refused",
                 lambda: load_manifest(str(d / "int-key-claims.yaml")),
                 "must be a string")

    (d / "list-hook-value.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\nhooks:\n  install: [a, b]\n")
    check_raises("list hook value refused",
                 lambda: load_manifest(str(d / "list-hook-value.yaml")),
                 "must be a string")

    (d / "int-questions.yaml").write_text(
        f"apiVersion: {API_VERSION}\nname: demo\nversion: 1.0.0\n"
        "label: Demo\ntier: userspace\nwizard:\n  questions: 123\n")
    check_raises("integer wizard.questions value refused",
                 lambda: load_manifest(str(d / "int-questions.yaml")),
                 "must be a string")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all manifest contract tests passed")
