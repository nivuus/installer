#!/usr/bin/env python3
"""Throwaway libvirt domain that answers the HDR question on the real GPU.

Server 2022 stays untouched on the NVMe: this domain only ever writes a qcow2
on /media/data, and never receives the Samsung NVMe hostdev.

OPERATIONAL TRAPS:
- assert_gpu_free() guards the `define` action only, not manual `virsh start`;
  protection at start time relies on libvirt/vfio refusing a second GPU attach.
- Domain name is Windows-LTSC-test, so none of
  /etc/libvirt/hooks/qemu.d/Windows-LTSC-test/** runs: unlike production,
  nothing here stops nvidia-persistenced/ollama/Tdarr for you. assert_gpu_free()
  refuses the define instead when gpu_holders() finds one; a manual
  `virsh start` is not covered by that guard.
- Only domain_xml(), assert_gpu_free() and gpu_holders() are tested.

Usage:
    python3 testdomain.py xml
    sudo python3 testdomain.py define --windows-iso ... --unattend-iso ...
    python3 testdomain.py wait-ready
    sudo python3 testdomain.py teardown
"""
from __future__ import annotations

import argparse
import glob
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"

DOMAIN_NAME = "Windows-LTSC-test"
DISK_PATH = "/media/data/vm/windows-ltsc-test.qcow2"
NVRAM_PATH = "/var/lib/libvirt/qemu/nvram/Windows-LTSC-test_VARS.fd"
BRIDGE = "internalBridge"
# Locally administered MAC, distinct from the production VM's.
MAC = "52:54:00:4c:54:53"
WINRM_PORT = 5985


class DomainError(RuntimeError):
    """Raised when the test domain cannot be created or does not come up."""


def _virsh(*args: str) -> subprocess.CompletedProcess:
    # virsh output is localized; LC_ALL=C keeps state strings parseable.
    return subprocess.run(["virsh", *args], text=True, capture_output=True,
                          env={**os.environ, "LC_ALL": "C"})


def domain_xml(*, disk_path: str = DISK_PATH, windows_iso: str,
               unattend_iso: str, name: str = DOMAIN_NAME,
               nvram_path: str = NVRAM_PATH, bridge: str = BRIDGE,
               mac: str = MAC, memory_gib: int = 16, vcpus: int = 8) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)),
                      autoescape=select_autoescape(enabled_extensions=("j2",),
                                                   default=True),
                      keep_trailing_newline=True)
    return env.get_template("domain-test.xml.j2").render(
        name=name, disk_path=disk_path, windows_iso=windows_iso,
        unattend_iso=unattend_iso, nvram_path=nvram_path, bridge=bridge,
        mac=mac, memory_gib=memory_gib, vcpus=vcpus,
    )


def gpu_holders() -> list[str]:
    """PIDs with an open fd on /dev/nvidia*, enumerated in pure Python: a
    find(1) subprocess has no reliable exit-code signal on this host (it was
    measured exiting 1 on every run regardless of any real failure - see
    CLAUDE.md). A per-entry race/permission error is normal, swallowed, never
    raised; only /proc itself being unlistable is a genuine scan failure."""
    try:
        os.listdir("/proc")
    except OSError as exc:
        raise DomainError(f"GPU holder scan failed: /proc unreadable: {exc}")
    pids = set()
    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            target = os.readlink(fd_path)
        except OSError:
            continue
        if target.startswith("/dev/nvidia"):
            pids.add(fd_path.split("/")[2])
    return sorted(pids, key=int)


def assert_gpu_free() -> None:
    """The production VM owns the GPU while it runs; refuse rather than fight."""
    proc = _virsh("domstate", "Windows")
    if proc.returncode != 0:
        raise DomainError(f"could not determine Windows domain state: {proc.stderr.strip()}")
    state = proc.stdout.strip()
    if not state:
        raise DomainError("could not determine Windows domain state: domstate returned empty")
    if state != "shut off":
        raise DomainError(f"the Windows domain is {state!r}: shut it down first, "
                          "the GPU cannot be assigned to two domains")
    holders = gpu_holders()
    if holders:
        raise DomainError("process(es) still hold /dev/nvidia*: PID " + ", ".join(holders) +
                          " - no libvirt hooks stop them for you here (nvidia-persistenced, "
                          "nivuus-ollama, Tdarr are the usual suspects); stop them by hand")


def create_disk(path: str = DISK_PATH, size_gib: int = 120) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if Path(path).exists():
        raise DomainError(f"{path} already exists; run teardown first")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", path, f"{size_gib}G"],
                   check=True, capture_output=True)


def define(xml: str) -> None:
    path = Path("/run") / f"{DOMAIN_NAME}.xml"
    path.write_text(xml)
    proc = _virsh("define", str(path))
    if proc.returncode != 0:
        raise DomainError(f"virsh define failed: {proc.stderr.strip()}")


def guest_ip(domain: str = DOMAIN_NAME) -> str | None:
    proc = _virsh("domifaddr", domain, "--source", "arp")
    for line in proc.stdout.splitlines():
        for field in line.split():
            if "/" in field and field.count(".") == 3:
                return field.split("/")[0]
    return None


def wait_ready(domain: str = DOMAIN_NAME, timeout_s: int = 5400) -> str:
    """Wait for provisioning to finish: 99-marker.ps1 opens 5985 last.

    A reachable 5985 IS the readiness signal - a successful TCP connect, and
    nothing else. It does NOT read C:\\nivuus\\state\\PROVISION.done: that
    marker is diagnostic only, read by hand over WinRM once this returns,
    compared against payload.PROVISION_VERSION (Task 8 runbook).
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ip = guest_ip(domain)
        if ip:
            with socket.socket() as sock:
                sock.settimeout(2)
                if sock.connect_ex((ip, WINRM_PORT)) == 0:
                    return ip
        time.sleep(15)
    raise DomainError(
        f"{domain} did not open {WINRM_PORT} within {timeout_s}s; connect to "
        "the VNC console and read C:\\nivuus\\provision.log"
    )


def teardown(domain: str = DOMAIN_NAME, disk_path: str = DISK_PATH) -> None:
    _virsh("destroy", domain)
    _virsh("undefine", domain, "--nvram")
    Path(disk_path).unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Throwaway LTSC test domain")
    ap.add_argument("action", choices=["xml", "define", "wait-ready", "teardown"])
    ap.add_argument("--windows-iso", default="/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso")
    ap.add_argument("--unattend-iso", default="/media/data/iso/nivuus-unattend.iso")
    ap.add_argument("--disk-size", type=int, default=120)
    args = ap.parse_args(argv)

    if args.action == "xml":
        print(domain_xml(windows_iso=args.windows_iso,
                         unattend_iso=args.unattend_iso))
        return 0
    if args.action == "define":
        assert_gpu_free()
        create_disk(size_gib=args.disk_size)
        define(domain_xml(windows_iso=args.windows_iso,
                          unattend_iso=args.unattend_iso))
        print(f"defined {DOMAIN_NAME}; start it with: virsh start {DOMAIN_NAME}")
        return 0
    if args.action == "wait-ready":
        print(f"guest ready at {wait_ready()}")
        return 0
    teardown()
    print(f"{DOMAIN_NAME} removed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
