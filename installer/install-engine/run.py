#!/usr/bin/env python3
"""Nivuus install engine — orchestrates a scripted debootstrap install.

Reads a JSON config (produced by the web portal) and installs Debian + the
selected Nivuus features onto the target disk, emitting structured progress
events the portal relays over WebSocket.

Usage:
    sudo python3 run.py --config /run/nivuus-install/config.json \
                        [--target /mnt/target] [--nivuus-src /opt/nivuus-src]

The engine is also runnable standalone (outside the ISO) against a loopback
disk for fast iteration — point config.disk.path at the loop device.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make `common` (installer/) and `steps` (this dir) importable when run as a script.
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALLER_ROOT = os.path.dirname(ENGINE_DIR)
for path in (ENGINE_DIR, INSTALLER_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from common import hardware  # noqa: E402
from common.progress import ProgressEmitter, CONFIG_FILE  # noqa: E402
from steps import (  # noqa: E402
    partition, debootstrap, chroot_base, bootloader, features, packages,
    validate,
)
from steps.util import MountTracker, StepError  # noqa: E402


def load_config(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nivuus install engine")
    parser.add_argument("--config", default=CONFIG_FILE,
                        help="path to install config JSON")
    parser.add_argument("--target", default="/mnt/target",
                        help="mount point for the target root filesystem")
    parser.add_argument("--nivuus-src", default="/opt/nivuus-src",
                        help="path to the Nivuus repo payload on the live system")
    parser.add_argument("--stop-after", default=None,
                        choices=["partition", "debootstrap", "base", "bootloader",
                                 "features", "packages"],
                        help="stop the pipeline after this step (for testing)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("This engine must run as root.", file=sys.stderr)
        return 2

    config = load_config(args.config)
    emit = ProgressEmitter()
    mounts = MountTracker()
    target = args.target

    try:
        emit.info("start", 0, "Starting Nivuus installation…")
        hw = hardware.detect_all()

        # Decide before writing: every package refusal, conflict and kernel
        # parameter is known here, while the target disk is still untouched.
        plan, package_cmdline = packages.plan_packages(config, hw, emit)

        def stop(step: str) -> bool:
            if args.stop_after == step:
                emit.info("stop", 100, f"Stopping after '{step}' as requested.")
                mounts.unmount_all()
                os.sync()
                emit.done(f"Stopped after '{step}'.")
                return True
            return False

        fs = partition.partition_and_format(config, target, mounts, emit)
        if stop("partition"):
            return 0
        debootstrap.run_debootstrap(config, target, emit)
        debootstrap.setup_api_mounts(target, mounts, emit)
        nivuus_dir = debootstrap.copy_payload(args.nivuus_src, target, emit)
        debootstrap.generate_fstab(target, fs, emit)
        if stop("debootstrap"):
            return 0

        chroot_base.configure_base(config, target, emit)
        if stop("base"):
            return 0
        bootloader.install_bootloader(config, target, fs, emit, package_cmdline)
        if stop("bootloader"):
            return 0
        features.apply_features(config, target, nivuus_dir, hw, emit)
        if stop("features"):
            return 0
        packages.apply_packages(plan, target, hw, emit)
        if stop("packages"):
            return 0
        validate.validate(config, target, nivuus_dir, emit)

        emit.info("cleanup", 99, "Finalising and unmounting…")
        mounts.unmount_all()
        os.sync()
        emit.done()
        return 0

    except StepError as exc:
        emit.error("error", 100, f"Installation failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any crash to the portal
        emit.error("error", 100, f"Unexpected error: {exc}")
        return 1
    finally:
        mounts.unmount_all()
        emit.close()


if __name__ == "__main__":
    sys.exit(main())
