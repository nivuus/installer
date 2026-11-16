#!/usr/bin/env python3
"""Tests for installer/packages/updater.py and adopt.py.

The scratch machine, its packages and its fake GitHub live in
updater_bench.py. What a failed lay leaves behind - the rollback - and what a
release may require of the machine have their own suite,
test_packages_updater_rollback.py.

Run: python3 scripts/tests/test_packages_updater.py
"""
import io
import json
import os
import shutil
import tarfile
import tempfile

# updater_bench first: it points every NIVUUS_* directory at a scratch root,
# which `packages` reads when it is imported.
from updater_bench import (PACKAGES_DIR, check_eq, check_refused, failures,  # isort: skip
                           fake, finish, hw, install_locally, installed_version,
                           manifest_text, package_files, publish, witness)

from packages import lay as lay_mod, state  # noqa: E402
from packages.adopt import adopt  # noqa: E402
from packages.updater import check, lock, update  # noqa: E402

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

    # A failing hook, and what is put back after it, is the subject of
    # test_packages_updater_rollback.py.
    publish("demo", "1.2.1")
    check_eq("the next release is laid",
             update(["demo"], hw_detect=hw), ["demo"])

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
    staging_dir = lay_mod.STAGING_DIR
    check_eq("staging lives outside the scanned directory",
             os.path.dirname(staging_dir) == os.path.dirname(PACKAGES_DIR), True)
    os.makedirs(os.path.join(staging_dir, "demo"))
    with open(os.path.join(staging_dir, "demo", "nivuus-package.yaml"), "w") as fh:
        fh.write(manifest_text("demo", "9.9.9"))
    publish("demo", "1.4.0")
    check_eq("a leftover from an interrupted run does not block the package",
             update(["demo"], hw_detect=hw), ["demo"])
    check_eq("and it is cleared, directory included",
             os.path.exists(staging_dir), False)

    real_install = lay_mod.run_install

    def killed(*args, **kwargs):
        raise KeyboardInterrupt

    lay_mod.run_install = killed
    publish("demo", "1.5.0")
    try:
        update(["demo"], hw_detect=hw)
        failures.append("interrupted lay: expected KeyboardInterrupt")
    except KeyboardInterrupt:
        pass
    finally:
        lay_mod.run_install = real_install
    record = state.load()["demo"]
    check_eq("an interrupted lay is recorded as such, not as the old version",
             (state.status(record), record["target_version"],
              "interrupted" in record["error"]), ("failed", "1.5.0", True))
    check_eq("the copy it replaced is kept: no hook vouched for the new one",
             os.listdir(lay_mod.PREVIOUS_DIR), ["demo"])
    check_eq("a bare update leaves it for the operator", update(hw_detect=hw), [])
    check_eq("naming it completes the lay", update(["demo"], hw_detect=hw), ["demo"])
    check_eq("and the kept copy goes once the hooks have succeeded",
             os.path.exists(lay_mod.PREVIOUS_DIR), False)

    real_rename = os.rename

    def rename_refusing_the_new_copy(src, dst):
        if dst == os.path.join(PACKAGES_DIR, "demo") and src.startswith(staging_dir):
            raise OSError("simulated: rename refused")
        return real_rename(src, dst)

    publish("demo", "1.6.0")
    lines = len(witness())
    lay_mod.os.rename = rename_refusing_the_new_copy
    try:
        check_refused("a refused swap is raised",
                      lambda: update(["demo"], hw_detect=hw), "rename refused")
    finally:
        lay_mod.os.rename = real_rename
    check_eq("the previous copy is put back, never left missing",
             installed_version("demo"), "1.5.0")
    record = state.load()["demo"]
    check_eq("the record says what runs, and which release did not take",
             (state.status(record), record["version"], record["rolled_back_from"]),
             ("installed", "1.5.0", "1.6.0"))
    check_eq("no hook ran, so none is replayed", len(witness()), lines)
    check_eq("the next named update completes", update(["demo"], hw_detect=hw),
             ["demo"])

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
        os.makedirs(os.path.join(clone, ".git"))
        saved_path = os.environ["PATH"]
        os.environ["PATH"] = ""
        try:
            current = state.load()
            del current["handmade"]
            state.save(current)
            shutil.rmtree(os.path.join(PACKAGES_DIR, "handmade"))
            check_refused("a clone cannot be adopted without git",
                          lambda: adopt(clone), "git is not installed")
            check_eq("and nothing is left half-copied",
                     os.path.exists(os.path.join(PACKAGES_DIR, "handmade")), False)
            os.rmdir(os.path.join(clone, ".git"))
            check_eq("a plain directory needs no git", adopt(clone), "handmade")
        finally:
            os.environ["PATH"] = saved_path
        publish("handmade", "1.0.0")
        check_eq("an adopted package updates to the release",
                 update(["handmade"], hw_detect=hw), ["handmade"])
        check_eq("and no longer claims to come from the adopted directory",
                 "adopted_from" in state.load()["handmade"], False)
    check_eq("no empty staging directory is left beside the packages",
             os.path.exists(lay_mod.STAGING_DIR), False)
except BaseException:
    fake.close()
    raise

finish("updater")
