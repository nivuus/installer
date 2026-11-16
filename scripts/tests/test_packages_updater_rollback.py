#!/usr/bin/env python3
"""Tests for what a failed update leaves behind, and what a release may require.

Two contracts of installer/packages/updater.py, both about not stranding a
machine:
  - lay.py: a release whose hook fails is rolled back - the previous copy is
    put back and its own hooks replayed - and the record says which version
    runs. The hooks here write a file outside the package directory, so the
    rollback is judged on the machine, not only on the directory;
  - requirements.py: a release that asks for more than the machine can be
    shown to have is refused before anything is swapped.

Run: python3 scripts/tests/test_packages_updater_rollback.py
"""
import json
import os

# updater_bench first: it points every NIVUUS_* directory at a scratch root,
# which `packages` reads when it is imported.
from updater_bench import (PACKAGES_DIR, check_eq, check_refused, fake,  # isort: skip
                           finish, hw, install_locally, installed_version,
                           publish, witness, written_by_hooks)

from packages import discovery, lay as lay_mod, state  # noqa: E402
from packages.updater import check, update  # noqa: E402

STAMP_DIR = os.environ["NIVUUS_STAMP_DIR"]


class Recorder:
    """An emit that keeps the warnings, the only channel a lenient check has."""

    def __init__(self):
        self.warnings = []

    def info(self, step, pct, msg):
        pass

    def warn(self, step, pct, msg):
        self.warnings.append(msg)

    error = warn


def hooks_since(mark):
    return [" ".join(line.split()[:3]) for line in witness()[mark:]]


def nothing_set_aside():
    return (os.path.exists(lay_mod.PREVIOUS_DIR),
            os.path.exists(lay_mod.STAGING_DIR))


def no_apt(names):
    return set()


try:
    # --- a release whose activate fails is rolled back ----------------------
    install_locally("demo", "1.0.0", answers={})
    publish("demo", "1.1.0", fail="activate")
    mark = len(witness())
    check_refused("the failure is raised, and says the package was rolled back",
                  lambda: update(hw_detect=hw),
                  "update to 1.1.0 failed")
    check_refused("naming it tries the same release again",
                  lambda: update(["demo"], hw_detect=hw), "rolled back to 1.0.0")
    check_eq("each attempt ran the new hooks, then the previous version's",
             hooks_since(mark),
             ["demo install 1.1.0", "demo activate 1.1.0",
              "demo install 1.0.0", "demo activate 1.0.0"] * 2)
    check_eq("the previous copy is back in the packages directory",
             (installed_version("demo"), sorted(os.listdir(PACKAGES_DIR))),
             ("1.0.0", ["demo"]))
    check_eq("what the new install wrote on the machine is rewritten by the old one",
             written_by_hooks("demo"), "1.0.0")
    record = state.load()["demo"]
    check_eq("the record says the previous version runs, and is healthy",
             (state.status(record), record["version"], "target_version" in record),
             ("installed", "1.0.0", False))
    check_eq("and remembers the release that did not take, and why",
             (record["rolled_back_from"], "hook 'activate' exited 1" in record["error"]),
             ("1.1.0", True))
    with open(os.path.join(STAMP_DIR, "demo.activated")) as fh:
        check_eq("the activation stamp names the version put back",
                 fh.read().strip(), "1.0.0")
    check_eq("neither a kept copy nor a staging directory is left",
             nothing_set_aside(), (False, False))

    mark = len(witness())
    recorder = Recorder()
    check_eq("a bare update does not walk into the same release again",
             update(hw_detect=hw, emit=recorder), [])
    check_eq("it runs no hook", hooks_since(mark), [])
    check_eq("and says why it skipped it",
             ["1.1.0 failed and was rolled back" in w for w in recorder.warnings],
             [True])
    result = check()
    check_eq("check still reports the release as available",
             [(p.name, p.available) for p in result.pending], [("demo", "1.1.0")])
    with open(os.path.join(STAMP_DIR, "available.json")) as fh:
        entry = json.load(fh)["packages"]["demo"]
    check_eq("and publishes that it was rolled back",
             (entry["state"], entry["installed"], entry["rolled_back_from"]),
             ("installed", "1.0.0", "1.1.0"))

    publish("demo", "1.1.1")
    check_eq("a later release is laid by a bare update: the package was never "
             "dropped from automatic updates", update(hw_detect=hw), ["demo"])
    record = state.load()["demo"]
    check_eq("and the rollback is forgotten once a release has taken",
             (record["version"], "rolled_back_from" in record, "error" in record),
             ("1.1.1", False, False))
    check_eq("the machine carries the new version", written_by_hooks("demo"), "1.1.1")

    # --- a release whose install fails, same outcome ---------------------------
    publish("demo", "1.2.0", fail="install")
    mark = len(witness())
    check_refused("a failing install is rolled back too",
                  lambda: update(hw_detect=hw), "rolled back to 1.1.1")
    check_eq("the new activate never ran; the previous version's hooks did",
             hooks_since(mark),
             ["demo install 1.2.0", "demo install 1.1.1", "demo activate 1.1.1"])
    check_eq("directory and machine agree on the version put back",
             (installed_version("demo"), written_by_hooks("demo")),
             ("1.1.1", "1.1.1"))
    publish("demo", "1.1.1")

    # --- a rollback that fails is said to have failed -----------------------------
    # The installed version's own activate no longer passes: there is no
    # version left to vouch for, and the record must not pretend otherwise.
    install_locally("shaky", "1.0.0", answers={}, fail="activate")
    publish("shaky", "1.1.0", fail="install")
    check_refused("both failures are reported",
                  lambda: update(["shaky"], hw_detect=hw),
                  "rolling back to 1.0.0 failed too")
    record = state.load()["shaky"]
    check_eq("the package is recorded failed, with the release it aimed at",
             (state.status(record), record["version"], record["target_version"],
              "rolled_back_from" in record),
             ("failed", "1.0.0", "1.1.0", False))
    check_eq("the error keeps both causes",
             ("update to 1.1.0 failed" in record["error"],
              "hook 'activate' exited 1" in record["error"]), (True, True))
    check_eq("the previous copy is the one in place all the same",
             (installed_version("shaky"), nothing_set_aside()),
             ("1.0.0", (False, False)))
    mark = len(witness())
    check_eq("a bare update leaves a failed package to the operator",
             update(hw_detect=hw), [])
    check_eq("without running a hook", hooks_since(mark), [])
    current = state.load()
    del current["shaky"]
    state.save(current)
    fake.releases.pop("nivuus/shaky")

    # --- a swap interrupted between its two renames --------------------------------
    # The package has no directory at all and its copy sits under PREVIOUS_DIR.
    os.makedirs(lay_mod.PREVIOUS_DIR)
    os.rename(os.path.join(PACKAGES_DIR, "demo"),
              os.path.join(lay_mod.PREVIOUS_DIR, "demo"))
    publish("demo", "1.3.0")
    recorder = Recorder()
    check_eq("the copy is put back and the update goes on",
             update(["demo"], hw_detect=hw, emit=recorder), ["demo"])
    check_eq("which is said", ["its previous copy was put back" in w
                               for w in recorder.warnings], [True])
    check_eq("nothing is left set aside", nothing_set_aside(), (False, False))

    # A copy left under PREVIOUS_DIR next to a HEALTHY record is not a way
    # back: it is older than what runs. A failing lay must return to the
    # version that was running, never to that leftover.
    stale = os.path.join(lay_mod.PREVIOUS_DIR, "demo")
    os.makedirs(stale)
    with open(os.path.join(stale, "nivuus-package.yaml"), "w") as fh:
        fh.write("this is not the copy to go back to\n")
    publish("demo", "1.4.0", fail="activate")
    check_refused("a failing lay ignores a stale leftover",
                  lambda: update(["demo"], hw_detect=hw), "rolled back to 1.3.0")
    check_eq("and goes back to the version that was running",
             (installed_version("demo"), written_by_hooks("demo"),
              nothing_set_aside()), ("1.3.0", "1.3.0", (False, False)))
    publish("demo", "1.3.0")

    # --- what a release may require of the machine -----------------------------------
    install_locally("needy", "1.0.0", answers={}, features=["networking"],
                    capabilities=["nvme-dedicated"], apt=["curl"])
    publish("needy", "1.1.0", features=["networking", "docker"],
            capabilities=["nvme-dedicated"], apt=["curl"])
    mark = len(witness())
    check_refused("a feature the release adds cannot be verified and is refused",
                  lambda: update(["needy"], hw_detect=hw, apt_query=no_apt),
                  "requires installer feature(s) the installed 1.0.0 did not: docker")
    publish("needy", "1.1.0", features=["networking"],
            capabilities=["nvme-dedicated", "iommu", "gpu-discrete"], apt=["curl"])
    check_refused("a capability the release adds is measured, and named when absent",
                  lambda: update(["needy"], apt_query=no_apt, hw_detect=lambda: {
                      "iommu": {"supported": True}}),
                  "requires hardware this machine does not have: gpu-discrete")
    check_eq("both refusals came before the swap: nothing replaced, no hook run",
             (installed_version("needy"), hooks_since(mark),
              state.status(state.load()["needy"])), ("1.0.0", [], "installed"))

    # What the installed version already required is not asked again: here
    # no NVMe is detected and no apt package is installed, the very state of
    # a console whose disk went to vfio-pci, and the update must go through.
    publish("needy", "1.1.0", features=["networking"],
            capabilities=["nvme-dedicated", "iommu"], apt=["curl"])
    recorder = Recorder()
    check_eq("a release adding only what the machine has is laid",
             update(["needy"], emit=recorder, apt_query=no_apt,
                    hw_detect=lambda: {"iommu": {"supported": True}}), ["needy"])
    check_eq("and nothing it already declared is complained about",
             recorder.warnings, [])

    # apt is lenient at install, so it is lenient here: named, never refused.
    publish("needy", "1.2.0", features=["networking"],
            capabilities=["nvme-dedicated", "iommu"],
            apt=["curl", "coturn", "jq"])
    asked = []

    def only_jq(names):
        asked.append(sorted(names))
        return {"jq"}

    recorder = Recorder()
    check_eq("apt packages the release adds do not block it",
             update(["needy"], hw_detect=lambda: {"iommu": {"supported": True}},
                    emit=recorder, apt_query=only_jq), ["needy"])
    check_eq("only what the release adds is looked up", asked, [["coturn", "jq"]])
    check_eq("the one that is not installed is named, the installed one is not",
             [("coturn" in w, "jq" in w, "curl" in w) for w in recorder.warnings],
             [(True, False, False)])

    def no_dpkg(names):
        raise FileNotFoundError(2, "No such file or directory", "dpkg-query")

    publish("needy", "1.3.0", features=["networking"],
            capabilities=["nvme-dedicated", "iommu"],
            apt=["curl", "coturn", "jq", "socat"])
    recorder = Recorder()
    check_eq("a lookup that cannot run does not block the update either",
             update(["needy"], hw_detect=lambda: {"iommu": {"supported": True}},
                    emit=recorder, apt_query=no_dpkg), ["needy"])
    check_eq("but is said, with the package it could not look up",
             [("socat" in w, "dpkg-query could not say" in w)
              for w in recorder.warnings], [(True, True)])

    # --- a packages directory that cannot be read ---------------------------------------
    real_listdir = discovery.os.listdir

    def denied(path):
        if path == PACKAGES_DIR:
            raise PermissionError(13, "Permission denied", path)
        return real_listdir(path)

    discovery.os.listdir = denied
    try:
        check_refused("check refuses instead of filing every package under "
                      "'not followed'", check, "cannot be listed")
        check_refused("and so does update", lambda: update(hw_detect=hw),
                      "cannot be listed")
    finally:
        discovery.os.listdir = real_listdir
    away = PACKAGES_DIR + ".away"
    os.rename(PACKAGES_DIR, away)
    try:
        check_refused("a packages directory that is gone is refused the same way",
                      check, "does not exist, yet 2 package(s) are recorded")
    finally:
        os.rename(away, PACKAGES_DIR)
    check_eq("once it is readable again the check goes through",
             sorted(check().current), ["demo", "needy"])
except BaseException:
    fake.close()
    raise

finish("updater rollback and requirements")
