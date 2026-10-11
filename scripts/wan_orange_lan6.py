#!/usr/bin/env python3
"""Hand Orange's delegated IPv6 prefix to the LAN bridges.

Reads the prefix the dhclient hook records, puts one /64 of it on each LAN
listed in the configuration, and has radvd announce exactly those. Nothing is
handed out without a prefix and a default route on the WAN.
Design: docs/superpowers/specs/2026-10-11-wan-orange-lan6-design.md.
"""
import ipaddress


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
