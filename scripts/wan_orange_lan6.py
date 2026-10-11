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
TOOL_TIMEOUT_S = int(os.environ.get("NIVUUS_LAN6_TOOL_TIMEOUT_S", "30"))
# Runs without a usable gateway before the DHCPv6 client is restarted, then
# the same count times each of these: every restart sends a RELEASE and a
# SOLICIT to Orange and makes the LANs lose and regain their prefix.
SILENT_LIMIT = 4
BACKOFF = (1, 5, 15, 60)
# The link-local address radvd advertises from. Not the bridge's own: the
# Thread border router advertises from that one with router lifetime 0, which
# makes every client drop it as default router until the next advertisement.
# It is set deprecated (preferred lifetime 0): the kernel then never picks it
# for a socket that did not ask for it, so only radvd, which names its source,
# sends from it.
ROUTER_LL = "fe80::1"


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
                "    MaxRtrAdvInterval 60;",
                "    AdvDefaultLifetime 900;",
                "    AdvRASrcAddress {",
                "        %s;" % ROUTER_LL,
                "    };",
                "    prefix %s {" % net,
                "        AdvValidLifetime 7200;",
                "        AdvPreferredLifetime 3600;",
                "        DeprecatePrefix on;",
                "    };",
                "};"]
    return "\n".join(out) + "\n"


def tool(*argv, check=False):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError("%s did not return within %d s" % (" ".join(argv), TOOL_TIMEOUT_S)) from None
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
    """[interface, address] pairs this script may have put on the LANs."""
    try:
        return [tuple(pair) for pair in json.loads((STATE / "wan-orange.lan6").read_text())]
    except FileNotFoundError:
        return []


def set_addresses(want):
    """Remove the addresses no longer wanted, (re)assert the wanted ones."""
    record = STATE / "wan-orange.lan6"
    known, wanted = applied(), sorted(want)
    # Intent first: an address added just before a failure is then still
    # known to the withdrawal that follows.
    record.write_text(json.dumps(sorted(set(known) | set(wanted))))
    for name, addr in known:
        if (name, addr) not in wanted:
            tool("ip", "-6", "addr", "del", addr, "dev", name)
    for name, addr in wanted:
        # Asserted on every run: NetworkManager drops it when it reactivates the bridge.
        deprecated = ("preferred_lft", "0") if addr.startswith(ROUTER_LL + "/") else ()
        tool("ip", "-6", "addr", "replace", addr, "dev", name, *deprecated, check=True)
    record.write_text(json.dumps(wanted))
    for name in sorted({name for name, _ in wanted}):
        settle(name)


def settle(name):
    """Wait for duplicate address detection: radvd cannot send from a tentative address."""
    for _ in range(50):
        listing = json.loads(tool("ip", "-j", "-6", "addr", "show", "dev", name).stdout or "[]")
        addrs = [addr for entry in listing for addr in entry.get("addr_info", [])]
        failed = [addr["local"] for addr in addrs if addr.get("dadfailed")]
        if failed:
            raise RuntimeError("duplicate address on %s: %s" % (name, ", ".join(failed)))
        if not any(addr.get("tentative") for addr in addrs):
            return
        time.sleep(0.1)
    raise RuntimeError("addresses on %s still tentative after 5 s" % name)


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


def unusable(reason):
    """Count a run without a usable gateway; restart the DHCPv6 client when it lasts.

    Orange closes neighbour discovery when its side forgets the DHCPv6
    exchange, and only a new full exchange reopens it.
    """
    path = STATE / "wan-orange.lan6-health"
    state = json.loads(path.read_text()) if path.exists() else {"bad": 0, "restarts": 0}
    state["bad"] += 1
    limit = SILENT_LIMIT * BACKOFF[min(state["restarts"], len(BACKOFF) - 1)]
    if state["bad"] == 1:
        print("wan-orange-lan6: %s; DHCPv6 restart after %d such runs" % (reason, limit), file=sys.stderr)
    if state["bad"] < limit:
        path.write_text(json.dumps(state))
        return
    path.write_text(json.dumps({"bad": 0, "restarts": state["restarts"] + 1}))
    print("wan-orange-lan6: %s; restarting the DHCPv6 client for a full exchange" % reason, file=sys.stderr)
    tool("systemctl", "--no-block", "restart", DHCP6_UNIT, check=True)


def apply(with_ra=True):
    STATE.mkdir(parents=True, exist_ok=True)
    sysctl(ROUTER_SYSCTLS)
    prefix = delegated_prefix()
    gw = gateway() if prefix else None
    if prefix and not gw:
        tool("rdisc6", "-1", "-q", "-r", "1", "-w", "2000", WAN)
        gw = gateway()
    if not gw:
        if prefix:
            unusable("prefix %s but no default route" % prefix)
        advertise(None)
        set_addresses([])
        return
    # Neighbor Solicitation, the IPv6 counterpart of the ARP check on IPv4.
    # Exit 2 is "no answer"; anything else is a local failure, not a verdict.
    probe = tool("ndisc6", "-1", "-q", "-r", "2", "-w", "1000", gw, WAN)
    if probe.returncode == 0:
        (STATE / "wan-orange.lan6-health").unlink(missing_ok=True)
    elif probe.returncode == 2:
        unusable("gateway %s silent" % gw)
    else:
        raise RuntimeError("ndisc6 failed (%d): %s" % (probe.returncode, probe.stderr.strip()))
    nets = {name: subnet(prefix, index) for name, index in parse_lans(CONF.read_text()).items()}
    set_addresses([(name, addr) for name, net in nets.items()
                   for addr in (ROUTER_LL + "/64", "%s/64" % (net.network_address + 1))])
    advertise(render_radvd(nets) if with_ra and nets else None)


def withdraw():
    STATE.mkdir(parents=True, exist_ok=True)
    advertise(None)
    set_addresses([])
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
