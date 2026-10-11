#!/usr/bin/env python3
"""Hand Orange's delegated IPv6 prefix to the LAN bridges.

Reads the prefix the dhclient hook records, puts one /64 of it on each LAN
listed in the configuration, and has radvd announce exactly those. Nothing is
handed out without a prefix and a default route on the WAN.
Design: docs/superpowers/specs/2026-10-11-wan-orange-lan6-design.md.
"""
import argparse
import ipaddress
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

# NIVUUS_TEST_PATH lets the test suite put fakes ahead of the real tools.
os.environ["PATH"] = ":".join(filter(None, [os.environ.get("NIVUUS_TEST_PATH"),
                                            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]))
WAN = os.environ.get("NIVUUS_WAN_IF", "enp5s0.832")
STATE = pathlib.Path(os.environ.get("NIVUUS_WAN_STATE_DIR", "/run/nivuus"))
CONF = pathlib.Path(os.environ.get("NIVUUS_LAN6_CONF", "/etc/nivuus/wan-orange-lan6.conf"))
PROC = pathlib.Path(os.environ.get("NIVUUS_PROC", "/proc/sys/net/ipv6/conf"))
INTERVAL_S = int(os.environ.get("NIVUUS_LAN6_INTERVAL_S", "15"))
RADVD_UNIT = "nivuus-lan6-radvd.service"
DHCP6_UNIT = "nivuus-wan-orange-dhcp6.service"
# Forwarding is on, so only accept_ra=2 lets the kernel learn the default
# route. Orange's RA carries no prefix and an MTU above the link's.
ROUTER_SYSCTLS = {"accept_ra": "2", "accept_ra_defrtr": "1", "accept_ra_pinfo": "0",
                  "autoconf": "0", "accept_ra_mtu": "0"}
SILENT_LIMIT = 4


def parse_lans(text):
    """Lines of '<interface> <subnet index, hexadecimal>'; '#' starts a comment."""
    lans = {}
    for raw in text.splitlines():
        fields = raw.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) != 2:
            raise ValueError("expected '<interface> <subnet index>': %r" % raw)
        name, index = fields[0], int(fields[1], 16)
        if name in lans or index in lans.values():
            raise ValueError("duplicate interface or subnet index: %r" % raw)
        lans[name] = index
    return lans


def subnet(prefix, index):
    """The index-th /64 of a delegated prefix."""
    if prefix.prefixlen > 64 or not 0 <= index < 2 ** (64 - prefix.prefixlen):
        raise ValueError("subnet %x does not fit in %s" % (index, prefix))
    return ipaddress.IPv6Network((int(prefix.network_address) + (index << 64), 64))


def render_radvd(nets):
    out = ["# Rendered by nivuus-wan-orange-lan6.py. Do not edit."]
    for name, net in sorted(nets.items()):
        out += ["interface %s {" % name,
                "    AdvSendAdvert on;",
                "    MaxRtrAdvInterval 300;",
                "    AdvDefaultLifetime 900;",
                "    prefix %s {" % net,
                "        AdvValidLifetime 7200;",
                "        AdvPreferredLifetime 3600;",
                "        DeprecatePrefix on;",
                "    };",
                "};"]
    return "\n".join(out) + "\n"


def tool(*argv, check=False):
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(argv), result.stderr.strip()))
    return result


def sysctl(values):
    for key, value in values.items():
        (PROC / WAN / key).write_text(value + "\n")


def delegated_prefix():
    try:
        return ipaddress.IPv6Network((STATE / "wan-orange.prefix").read_text().strip())
    except FileNotFoundError:
        return None


def gateway():
    routes = json.loads(tool("ip", "-j", "-6", "route", "show", "default", "dev", WAN).stdout or "[]")
    return routes[0].get("gateway") if routes else None


def applied():
    try:
        return json.loads((STATE / "wan-orange.lan6").read_text())
    except FileNotFoundError:
        return {}


def set_addresses(want):
    """Addresses this script put on the LANs: remove the stale, (re)assert the wanted."""
    for name, addr in applied().items():
        if want.get(name) != addr:
            tool("ip", "-6", "addr", "del", addr, "dev", name)
    for name, addr in want.items():
        # Asserted on every run: NetworkManager drops it when it reactivates the bridge.
        tool("ip", "-6", "addr", "replace", addr, "dev", name, check=True)
    (STATE / "wan-orange.lan6").write_text(json.dumps(want))


def advertise(text):
    """Run radvd on exactly this configuration; None stops it.

    A change restarts radvd rather than reloading it: on shutdown it announces
    the old prefixes as deprecated, which is what makes clients drop them.
    """
    path = STATE / "radvd.conf"
    if text is None:
        if path.exists():
            tool("systemctl", "stop", RADVD_UNIT)
            path.unlink()
    elif not path.exists() or path.read_text() != text:
        path.write_text(text)
        tool("systemctl", "restart", RADVD_UNIT, check=True)
    elif tool("systemctl", "is-active", "--quiet", RADVD_UNIT).returncode != 0:
        tool("systemctl", "start", RADVD_UNIT, check=True)


def gateway_answers(gw):
    """Neighbor Solicitation, the IPv6 counterpart of the ARP check on IPv4.

    Orange stops answering it when its side forgets the DHCPv6 exchange; only
    a new full exchange reopens it, so a lasting silence restarts the client.
    """
    counter = STATE / "wan-orange.lan6-silent"
    if tool("ndisc6", "-1", "-q", "-r", "2", "-w", "1000", gw, WAN).returncode == 0:
        counter.unlink(missing_ok=True)
        return
    silent = int(counter.read_text()) + 1 if counter.exists() else 1
    print("wan-orange-lan6: gateway %s silent, run %d" % (gw, silent), file=sys.stderr)
    if silent < SILENT_LIMIT:
        counter.write_text(str(silent))
        return
    counter.unlink(missing_ok=True)
    print("wan-orange-lan6: restarting the DHCPv6 client for a full exchange", file=sys.stderr)
    tool("systemctl", "--no-block", "restart", DHCP6_UNIT)


def apply(with_ra=True):
    STATE.mkdir(parents=True, exist_ok=True)
    sysctl(ROUTER_SYSCTLS)
    prefix = delegated_prefix()
    gw = gateway() if prefix else None
    if prefix and not gw:
        tool("rdisc6", "-1", "-q", "-r", "1", "-w", "2000", WAN)
        gw = gateway()
    if not gw:
        advertise(None)
        set_addresses({})
        return
    gateway_answers(gw)
    nets = {name: subnet(prefix, index) for name, index in parse_lans(CONF.read_text()).items()}
    set_addresses({name: "%s/64" % (net.network_address + 1) for name, net in nets.items()})
    advertise(render_radvd(nets) if with_ra and nets else None)


def withdraw():
    advertise(None)
    set_addresses({})
    if (PROC / WAN).is_dir():
        sysctl({"accept_ra": "0"})
        tool("ip", "-6", "route", "del", "default", "dev", WAN)


def run():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            apply()
            time.sleep(INTERVAL_S)
    finally:
        withdraw()


def status():
    print("prefix:", delegated_prefix(), "| gateway:", gateway(), "| addresses:", applied())
    print("radvd:", tool("systemctl", "is-active", RADVD_UNIT).stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("apply", "withdraw", "run", "status"))
    parser.add_argument("--no-ra", action="store_true", help="apply: address the LANs but do not advertise")
    args = parser.parse_args()
    try:
        if args.action == "apply":
            apply(with_ra=not args.no_ra)
        else:
            {"withdraw": withdraw, "run": run, "status": status}[args.action]()
    except (RuntimeError, ValueError) as error:
        sys.exit("wan-orange-lan6: %s" % error)


if __name__ == "__main__":
    main()
