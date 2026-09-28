#!/usr/bin/env python3
"""Tests for installer/packages/updater.py and adopt.py.

Every package here is real enough to run: a manifest the real parser loads
and hooks the real runner executes, published by a local fake GitHub. The
hooks append one line per call to a witness file, so the order and the count
of install/activate runs are measured, not assumed.

Run: python3 scripts/tests/test_packages_updater.py
"""
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = tempfile.mkdtemp(prefix="nivuus-updater-")
WITNESS = os.path.join(ROOT, "witness.log")
for key, rel in (("NIVUUS_PACKAGES_DIR", "opt/nivuus-packages"),
                 ("NIVUUS_STATE_FILE", "etc/nivuus/packages.json"),
                 ("NIVUUS_STAMP_DIR", "var/lib/nivuus/packages"),
                 ("NIVUUS_CACHE_DIR", "var/cache/nivuus")):
    os.environ[key] = os.path.join(ROOT, rel)
os.environ["UPDATER_WITNESS"] = WITNESS
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402

fake = fake_github.start()

from packages import state  # noqa: E402
from packages.adopt import adopt  # noqa: E402
from packages.updater import UpdateError, check, lock, update  # noqa: E402

PACKAGES_DIR = os.environ["NIVUUS_PACKAGES_DIR"]
failures = []

HOOK = """import json, os, sys
phase = sys.argv[sys.argv.index("--phase") + 1]
ctx = json.load(sys.stdin)
with open(os.environ["UPDATER_WITNESS"], "a") as fh:
    fh.write(f"{ctx['package']['name']} {phase} {ctx['package']['version']} "
             f"{json.dumps(ctx['answers'], sort_keys=True)}\\n")
sys.exit(%d)
"""


def check_eq(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except UpdateError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UpdateError, none raised")


def manifest_text(name, version, *, source=True, requires=(), questions=False):
    lines = ["apiVersion: nivuus.dev/v1", f"name: {name}",
             f"version: {version}", f"label: {name}", "tier: userspace",
             "hooks:", "  install: hooks/install.py",
             "  activate: hooks/activate.py"]
    if source:
        lines += ["source:", f"  github: nivuus/{name}"]
    if requires:
        lines += ["requires:", f"  packages: [{', '.join(requires)}]"]
    if questions:
        lines += ["wizard:", "  questions: wizard.yaml"]
    return "\n".join(lines) + "\n"


def package_files(name, version, *, fail=False, **kwargs):
    files = {"nivuus-package.yaml": manifest_text(name, version, **kwargs),
             "hooks/install.py": HOOK % (1 if fail else 0),
             "hooks/activate.py": HOOK % 0}
    if kwargs.get("questions"):
        files["wizard.yaml"] = ("- key: zone\n  label: Zone\n  type: texte\n"
                                "  required: true\n")
    return files


def install_locally(name, version, answers=None, **kwargs):
    """What the engine leaves behind: a directory and a state record."""
    dest = os.path.join(PACKAGES_DIR, name)
    for rel, content in package_files(name, version, **kwargs).items():
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    current = state.load()
    current[name] = {"version": version}
    if answers is not None:
        current[name]["answers"] = answers
    state.save(current)


def publish(name, version, **kwargs):
    archive_name = kwargs.pop("archive_name", name)
    files = package_files(archive_name, kwargs.pop("manifest_version", version),
                          **kwargs)
    fake.publish(f"nivuus/{name}", version, fake_github.build_archive(files))


def witness():
    if not os.path.exists(WITNESS):
        return []
    with open(WITNESS) as fh:
        return fh.read().splitlines()


def installed_version(name):
    with open(os.path.join(PACKAGES_DIR, name, "nivuus-package.yaml")) as fh:
        return [line.split(": ")[1] for line in fh.read().splitlines()
                if line.startswith("version:")][0]


def hw():
    return {}


try:
    # --- the ordinary update ---------------------------------------------
    install_locally("demo", "1.0.0", answers={})
    publish("demo", "1.1.0")
    result = check()
    check_eq("check sees the pending release",
             [(p.name, p.installed, p.available) for p in result.pending],
             [("demo", "1.0.0", "1.1.0")])
    with open(os.path.join(os.environ["NIVUUS_STAMP_DIR"], "available.json")) as fh:
        available = json.load(fh)
    check_eq("check publishes what is available",
             available["packages"]["demo"]["available"], "1.1.0")
    check_eq("check lays nothing", (witness(), installed_version("demo")),
             ([], "1.0.0"))

    check_eq("update lays the package", update(hw_detect=hw), ["demo"])
    check_eq("install then activate, at the new version",
             witness(), ["demo install 1.1.0 {}", "demo activate 1.1.0 {}"])
    record = state.load()["demo"]
    check_eq("state records the new version",
             (record["version"], state.status(record)), ("1.1.0", "installed"))
    check_eq("directory holds the new release", installed_version("demo"), "1.1.0")
    check_eq("activation stamped", os.path.exists(os.path.join(
        os.environ["NIVUUS_STAMP_DIR"], "demo.activated")), True)
    check_eq("no staging left behind",
             sorted(os.listdir(PACKAGES_DIR)), ["demo"])

    check_eq("a second update is a no-op", update(hw_detect=hw), [])
    check_eq("and runs no hook", len(witness()), 2)

    # --- a failing hook ----------------------------------------------------
    publish("demo", "1.2.0", fail=True)
    check_refused("failing install is raised", lambda: update(hw_detect=hw),
                  "update to 1.2.0 failed")
    record = state.load()["demo"]
    check_eq("failure recorded with its target",
             (state.status(record), record["version"], record["target_version"]),
             ("failed", "1.1.0", "1.2.0"))
    runs = len(witness())
    check_eq("update without names does not retry a failed package",
             update(hw_detect=hw), [])
    check_eq("so no hook ran", len(witness()), runs)
    check_refused("naming it retries it",
                  lambda: update(["demo"], hw_detect=hw), "failed")
    check_eq("the retry did run", len(witness()), runs + 1)

    publish("demo", "1.2.1")
    check_eq("a fixed release clears the failure",
             update(["demo"], hw_detect=hw), ["demo"])
    record = state.load()["demo"]
    check_eq("state is healthy again",
             (state.status(record), "target_version" in record), ("installed", False))

    # --- refusals before anything is replaced -------------------------------
    publish("demo", "1.3.0", archive_name="other")
    check_refused("release of another package", lambda: update(hw_detect=hw),
                  "contains package 'other'")
    publish("demo", "1.3.0", manifest_version="1.2.9")
    check_refused("manifest version differs from the tag",
                  lambda: update(hw_detect=hw), "at version 1.2.9")
    evil = tarfile.TarInfo("../escaped.txt")
    evil.size = 4
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.addfile(evil, io.BytesIO(b"evil"))
    fake.publish("nivuus/demo", "1.3.0", buffer.getvalue())
    check_refused("archive escaping its directory", lambda: update(hw_detect=hw),
                  "cannot be extracted")
    check_eq("nothing escaped", os.path.exists(
        os.path.join(os.path.dirname(PACKAGES_DIR), "escaped.txt")), False)
    check_eq("refusals left the installed copy alone",
             (installed_version("demo"), sorted(os.listdir(PACKAGES_DIR))),
             ("1.2.1", ["demo"]))
    publish("demo", "1.2.1")

    # --- requirements and ordering -----------------------------------------
    install_locally("sat", "1.0.0", answers={}, requires=["base"])
    publish("sat", "1.1.0", requires=["base"])
    check_refused("required package not installed",
                  lambda: update(["sat"], hw_detect=hw), "'base', which is not installed")
    install_locally("base", "1.0.0", answers={})
    publish("base", "1.1.0")
    del_lines = len(witness())
    check_eq("base laid before its satellite, despite the alphabet",
             update(["sat", "base"], hw_detect=hw), ["base", "sat"])
    check_eq("hooks ran in that order",
             [line.split()[:2] for line in witness()[del_lines:]],
             [["base", "install"], ["base", "activate"],
              ["sat", "install"], ["sat", "activate"]])

    current = state.load()
    state.mark_failed(current, "base", "1.2.0", "hook exited 1")
    state.save(current)
    publish("sat", "1.2.0", requires=["base"])
    check_refused("required package whose last update failed",
                  lambda: update(["sat"], hw_detect=hw), "whose last update failed")
    current = state.load()
    state.mark_installed(current, "base", "1.1.0")
    state.save(current)
    publish("sat", "1.1.0", requires=["base"])

    # --- answers ------------------------------------------------------------
    install_locally("asker", "1.0.0", questions=True)
    publish("asker", "1.1.0", questions=True)
    check_refused("questions without recorded answers",
                  lambda: update(["asker"], hw_detect=hw), "no answers are recorded")
    current = state.load()
    current["asker"]["answers"] = {"zone": "north", "dropped": "x"}
    state.save(current)
    lines = len(witness())
    check_eq("recorded answers are replayed", update(["asker"], hw_detect=hw),
             ["asker"])
    check_eq("hooks received them, minus the question no longer asked",
             witness()[lines], 'asker install 1.1.0 {"zone": "north"}')

    # --- no source, and the lock -------------------------------------------
    install_locally("nosrc", "1.0.0", answers={}, source=False)
    check_refused("naming a package without source",
                  lambda: update(["nosrc"], hw_detect=hw), "declares no 'source:'")
    check_eq("without names it is skipped, not fatal", update(hw_detect=hw), [])
    with lock():
        check_refused("a concurrent run refuses", lambda: update(hw_detect=hw),
                      "another nivuus run holds")
    check_refused("unknown package", lambda: update(["ghost"], hw_detect=hw),
                  "not installed")

    # --- interruptions and leftovers --------------------------------------------
    import packages.updater as updater_mod
    staging_dir = updater_mod.STAGING_DIR
    check_eq("staging lives outside the scanned directory",
             os.path.dirname(staging_dir) == os.path.dirname(PACKAGES_DIR), True)
    os.makedirs(os.path.join(staging_dir, "demo"))
    with open(os.path.join(staging_dir, "demo", "nivuus-package.yaml"), "w") as fh:
        fh.write(manifest_text("demo", "9.9.9"))
    publish("demo", "1.4.0")
    check_eq("a leftover from an interrupted run does not block the package",
             update(["demo"], hw_detect=hw), ["demo"])
    check_eq("and it is cleared", os.path.exists(staging_dir)
             and os.listdir(staging_dir), [])

    real_install = updater_mod.run_install

    def killed(*args, **kwargs):
        raise KeyboardInterrupt

    updater_mod.run_install = killed
    publish("demo", "1.5.0")
    try:
        update(["demo"], hw_detect=hw)
        failures.append("interrupted lay: expected KeyboardInterrupt")
    except KeyboardInterrupt:
        pass
    finally:
        updater_mod.run_install = real_install
    record = state.load()["demo"]
    check_eq("an interrupted lay is recorded as such, not as the old version",
             (state.status(record), record["target_version"],
              "interrupted" in record["error"]), ("failed", "1.5.0", True))
    check_eq("a bare update leaves it for the operator", update(hw_detect=hw), [])
    check_eq("naming it completes the lay", update(["demo"], hw_detect=hw), ["demo"])

    install_locally("ping", "1.0.0", answers={}, requires=["pong"])
    install_locally("pong", "1.0.0", answers={}, requires=["ping"])
    publish("ping", "1.1.0", requires=["pong"])
    publish("pong", "1.1.0", requires=["ping"])
    check_refused("a dependency cycle is refused, in English",
                  lambda: update(["ping", "pong"], hw_detect=hw), "dependency cycle")
    for name in ("ping", "pong"):
        current = state.load()
        del current[name]
        state.save(current)
        import shutil
        shutil.rmtree(os.path.join(PACKAGES_DIR, name))

    fake.releases.pop("nivuus/base")
    result = check()
    check_eq("an unreachable release is reported apart from unfollowed packages",
             ([n for n, _ in result.unreachable], [n for n, _ in result.skipped]),
             (["base"], ["nosrc"]))
    publish("base", "1.1.0")

    # --- adoption -------------------------------------------------------------
    with tempfile.TemporaryDirectory() as clone:
        for rel, content in package_files("handmade", "0.0.0").items():
            path = os.path.join(clone, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(content)
        lines = len(witness())
        check_eq("adopt returns the name", adopt(clone), "handmade")
        record = state.load()["handmade"]
        check_eq("adopted at the clone's version",
                 (record["version"], record["answers"]), ("0.0.0", {}))
        check_eq("adoption runs no hook", len(witness()), lines)
        check_refused("adopting twice", lambda: adopt(clone), "already recorded")
        publish("handmade", "1.0.0")
        check_eq("an adopted package updates to the release",
                 update(["handmade"], hw_detect=hw), ["handmade"])
finally:
    fake.close()


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all updater tests passed")
