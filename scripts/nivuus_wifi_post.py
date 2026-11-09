#!/usr/bin/python3
"""Apply the per-port Wi-Fi settings hostapd cannot carry, then prove they hold.

hostapd has no option for three things this host needs, so they are applied
here once hostapd reports every BSS as ENABLED (never after a fixed sleep):

- guest isolation: every port hostapd puts on the guest bridge is marked
  `isolated`, so guests of one radio cannot reach guests of the other one
  (ap_isolate only separates clients of the same radio);
- 2.4 GHz transmit power: the QCA9984 EEPROM says US, which would allow 30 dBm;
  the 2.4 GHz radios are held at 20 dBm (ETSI limit, and the Zigbee/Thread
  stick sits in the same chassis);
- 802.11k neighbor reports: each BSS advertises the BSS with the same SSID on
  the other band, so clients asking where else to go get a real answer.

Everything is derived from the hostapd configuration files given as arguments,
so a network added later (for example from Home Assistant) is covered without
editing this script. Any setting that does not read back as expected makes the
unit fail, visibly, instead of leaving the network silently half-configured.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

GUEST_BRIDGE = os.environ.get("NIVUUS_WIFI_GUEST_BRIDGE", "publicBridge")
TXPOWER_24GHZ_MBM = int(os.environ.get("NIVUUS_WIFI_TXPOWER_24GHZ_MBM", "2000"))
READY_TIMEOUT_S = float(os.environ.get("NIVUUS_WIFI_READY_TIMEOUT_S", "90"))
HOSTAPD_CLI = os.environ.get("NIVUUS_WIFI_HOSTAPD_CLI", "hostapd_cli")
IW = os.environ.get("NIVUUS_WIFI_IW", "iw")
SYSFS_NET = os.environ.get("NIVUUS_WIFI_SYSFS_NET", "/sys/class/net")


@dataclass
class Bss:
    ifname: str
    ssid: str
    bridge: str
    band_24ghz: bool
    primary: bool


def parse_hostapd_config(path: str) -> list[Bss]:
    """Return the BSSes of one hostapd configuration file, first one included."""
    radio_24ghz = False
    entries: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "interface" and not entries:
                entries.append({"ifname": value})
            elif key == "bss":
                entries.append({"ifname": value})
            elif key == "hw_mode" and len(entries) <= 1:
                radio_24ghz = value in ("b", "g")
            elif entries and key in ("ssid", "bridge"):
                entries[-1][key] = value
    return [
        Bss(e["ifname"], e.get("ssid", ""), e.get("bridge", ""), radio_24ghz, index == 0)
        for index, e in enumerate(entries)
    ]


def run(*argv: str) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def hostapd_state(ifname: str) -> str:
    try:
        out = run(HOSTAPD_CLI, "-i", ifname, "status")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    for line in out.splitlines():
        if line.startswith("state="):
            return line.split("=", 1)[1]
    return ""


def wait_until_enabled(ifnames: list[str]) -> None:
    """Wait for each radio's first interface: hostapd reports the state per radio."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    pending = list(ifnames)
    while pending:
        pending = [i for i in pending if hostapd_state(i) != "ENABLED"]
        if not pending:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"hostapd not ENABLED after {READY_TIMEOUT_S:.0f}s: {pending}")
        time.sleep(1)


def isolate_guest_port(ifname: str) -> None:
    flag = os.path.join(SYSFS_NET, ifname, "brport", "isolated")
    with open(flag, "w", encoding="ascii") as handle:
        handle.write("1")
    with open(flag, encoding="ascii") as handle:
        if handle.read().strip() != "1":
            raise RuntimeError(f"{ifname}: bridge port isolation did not hold")


def cap_txpower(ifname: str) -> None:
    run(IW, "dev", ifname, "set", "txpower", "fixed", str(TXPOWER_24GHZ_MBM))
    want = f"txpower {TXPOWER_24GHZ_MBM / 100:.2f} dBm"
    if want not in run(IW, "dev", ifname, "info"):
        raise RuntimeError(f"{ifname}: transmit power did not read back as {want}")


def own_neighbor_report(ifname: str) -> tuple[str, str, str]:
    """Return (bssid, ssid hex, nr hex) of the BSS's own neighbor report entry."""
    for line in run(HOSTAPD_CLI, "-i", ifname, "show_neighbor").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1].startswith("ssid=") and fields[2].startswith("nr="):
            return fields[0], fields[1][5:], fields[2][3:]
    raise RuntimeError(f"{ifname}: no own neighbor report "
                       "(does its section set ctrl_interface= and rrm_neighbor_report=1?)")


def publish_neighbors(bsses: list[Bss]) -> None:
    by_ssid: dict[str, list[Bss]] = {}
    for bss in bsses:
        by_ssid.setdefault(bss.ssid, []).append(bss)
    for group in by_ssid.values():
        if len(group) < 2:
            continue
        reports = {b.ifname: own_neighbor_report(b.ifname) for b in group}
        for bss in group:
            for other in group:
                if other.ifname == bss.ifname:
                    continue
                bssid, ssid_hex, nr_hex = reports[other.ifname]
                reply = run(HOSTAPD_CLI, "-i", bss.ifname, "set_neighbor", bssid,
                            f"ssid={ssid_hex}", f"nr={nr_hex}").strip()
                if reply != "OK":
                    raise RuntimeError(f"{bss.ifname}: set_neighbor {bssid} answered {reply!r}")


def main(config_paths: list[str]) -> int:
    bsses = [bss for path in config_paths for bss in parse_hostapd_config(path)]
    failures = []
    try:
        wait_until_enabled([b.ifname for b in bsses if b.primary])
    except RuntimeError as error:
        # Keep going: a radio that is up must still get its guest isolation.
        failures.append(f"wait_until_enabled: {error}")
    steps = [(isolate_guest_port, b.ifname) for b in bsses if b.bridge == GUEST_BRIDGE]
    steps += [(cap_txpower, b.ifname) for b in bsses if b.band_24ghz]
    for step, ifname in steps:
        try:
            step(ifname)
            print(f"{step.__name__} {ifname}: ok")
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failures.append(f"{step.__name__} {ifname}: {error}")
    try:
        publish_neighbors(bsses)
        print("publish_neighbors: ok")
    except (RuntimeError, subprocess.CalledProcessError) as error:
        failures.append(f"publish_neighbors: {error}")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: nivuus-wifi-post <hostapd.conf>...", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
