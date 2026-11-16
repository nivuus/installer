#!/usr/bin/env python3
"""Tests for the engine's --stop-after control flow (run.py).

`--stop-after partition` stops AFTER partitioning, i.e. after `wipefs -a` and
`sgdisk --zap-all` have already run on the configured disk. The docs used to
present it as a trial "without touching the disk". The non-destructive trial
is `--stop-after plan`: hardware detection and package planning run, then the
engine stops before the first step that writes to the target disk.

These tests drive the REAL `run.main()` - argument parsing, the stop closure,
the order of the steps - with every step replaced by a recorder, so a
regression that moves a write before the plan stop is caught by behaviour, not
by reading the source.

Run: python3 scripts/tests/test_install_engine_stop_points.py
(needs a Python that still ships `crypt`, i.e. <= 3.12: run.py imports
chroot_base, which imports it.)
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
RUN_PY = REPO / "installer" / "install-engine" / "run.py"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


progress_dir = tempfile.mkdtemp(prefix="nivuus-stop-points-")
# common.progress reads this at import time, so it must be set before run.py
# (which imports it) is loaded - otherwise the emitter writes to /run.
os.environ["NIVUUS_PROGRESS_DIR"] = progress_dir

spec = importlib.util.spec_from_file_location("nivuus_engine_run", RUN_PY)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

calls = []


class Sentinel(Exception):
    """Raised by a step that must never be reached in a given scenario."""


def recorder(name, result=None):
    def step(*args, **kwargs):
        calls.append(name)
        return result
    return step


def install_recorders():
    run.hardware.detect_all = recorder("detect", {})
    run.packages.plan_packages = recorder("plan", ([], ()))
    run.partition.partition_and_format = recorder(
        "partition", {"disk": "/dev/null", "esp": "x", "root": "y"})
    run.debootstrap.run_debootstrap = recorder("debootstrap")
    run.debootstrap.setup_api_mounts = recorder("api_mounts")
    run.debootstrap.copy_payload = recorder("copy_payload", "/nivuus")
    run.debootstrap.generate_fstab = recorder("fstab")
    run.chroot_base.configure_base = recorder("base")
    run.bootloader.install_bootloader = recorder("bootloader")
    run.features.apply_features = recorder("features")
    run.packages.apply_packages = recorder("apply_packages")
    run.validate.validate = recorder("validate")


def run_engine(stop_after):
    calls.clear()
    config = os.path.join(progress_dir, "cfg.json")
    with open(config, "w") as fh:
        json.dump({"disk": {"path": "/dev/does-not-matter"}}, fh)
    argv = ["run.py", "--config", config, "--target",
            os.path.join(progress_dir, "target")]
    if stop_after is not None:
        argv += ["--stop-after", stop_after]
    saved_argv, saved_euid = sys.argv, os.geteuid
    sys.argv, os.geteuid = argv, (lambda: 0)
    try:
        return run.main()
    except SystemExit as exc:  # argparse refusing the choice
        return f"SystemExit({exc.code})"
    finally:
        sys.argv, os.geteuid = saved_argv, saved_euid


def last_event():
    if not os.path.exists(os.path.join(progress_dir, "progress.jsonl")):
        return None  # the engine never started emitting
    with open(os.path.join(progress_dir, "progress.jsonl")) as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    return lines[-1] if lines else None


install_recorders()

# 1. --stop-after plan: detection and planning run, nothing that writes does.
rc = run_engine("plan")
check("plan: exit code", rc, 0)
check("plan: steps reached", list(calls), ["detect", "plan"])
check("plan: partition never called", "partition" in calls, False)
event = last_event()
check("plan: ends with a 'done' event",
      (event or {}).get("level"), "done")

# 2. The same guarantee, stated as a tripwire: a partition step that raises
#    must not be reached, so the run still succeeds (main() turns any
#    exception into exit code 1).
def tripwire(*args, **kwargs):
    raise Sentinel("partition reached under --stop-after plan")


run.partition.partition_and_format = tripwire
check("plan: tripwire not hit", run_engine("plan"), 0)
install_recorders()

# 3. --stop-after partition keeps meaning what its name says: partitioning
#    runs (it is destructive), and nothing after it does.
rc = run_engine("partition")
check("partition: exit code", rc, 0)
check("partition: steps reached", list(calls),
      ["detect", "plan", "partition"])

# 4. No --stop-after: the full pipeline, in order.
rc = run_engine(None)
check("full: exit code", rc, 0)
check("full: steps reached", list(calls),
      ["detect", "plan", "partition", "debootstrap", "api_mounts",
       "copy_payload", "fstab", "base", "bootloader", "features",
       "apply_packages", "validate"])

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - stop point tests passed")
