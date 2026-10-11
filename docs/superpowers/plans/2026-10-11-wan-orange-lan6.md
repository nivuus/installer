# Orange IPv6 Prefix on the LANs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** devices on `localBridge` and `publicBridge`, and the router itself, reach
the IPv6 Internet through the `/56` Orange delegates on VLAN 832.

**Architecture:** the DHCPv6 client Solicits on every start, because Orange only
answers neighbour discovery after a full exchange. One long-running unit,
`nivuus-wan-orange-lan6.service`, reconciles every 15 s: Router Advertisements
accepted on the WAN for the default route, one `/64` of the prefix on each LAN
bridge, and a radvd configuration rendered from that. No prefix, no route or a
silent gateway means nothing is handed out. The firewall is not changed.

**Tech Stack:** Python 3 stdlib (`ipaddress`), POSIX sh, `iproute2`, `radvd`,
`ndisc6` (`rdisc6`, `ndisc6`), systemd.

**Spec:** `docs/superpowers/specs/2026-10-11-wan-orange-lan6-design.md`

## Global Constraints

- Code, comments, commit messages and log lines in English. No file over 200 lines.
- IPv4 must not be touched: no change to `nivuus-wan-orange-dhcp4.service`, to
  the IPv4 addresses, routes or NAT. The host's Internet access carries the
  session that operates it.
- No firewalld rule, zone or policy is added or changed.
- `systemctl` does not work from an agent session on the host: use the D-Bus
  calls of `docs/claude/host-shell-gotchas.md`. Inside units and scripts run by
  systemd, `systemctl` is fine.
- Tests never act on the real network: every external tool is a fake found
  through `NIVUUS_TEST_PATH`, sysctls go to `NIVUUS_PROC`, state to a temp dir.
- Subnet numbering follows IPv4: `localBridge` = `00`, `publicBridge` = `02`.
- radvd lifetimes: router 900 s, prefix valid 7200 s, preferred 3600 s.
- Host packages `radvd` and `ndisc6` are installed in Task 5 only, by the owner's
  decision recorded in the spec; the packaged `radvd.service` stays disabled.

## Review Focus

- A reboot or crash of the DHCPv6 client: it must Solicit, not Confirm. Pinned
  by Task 1 (`ExecStartPre` removes the lease) and proven on the host in Task 5.
- The hook runs with no `old_ip6_prefix` after a lease removal: the route of a
  previous prefix must still be cleaned. Pinned by Task 1.
- A delegated prefix shorter or longer than `/56`, or a subnet index that does
  not fit: refused with a message, never a wrong `/64`. Pinned by Task 2.
- NetworkManager reactivating a bridge and dropping the address: the next loop
  puts it back. Pinned by Task 3 (`ip addr replace` on every run).
- A LAN interface that does not exist (`publicBridge` down at boot): the unit
  fails loudly and withdraws, it does not hand out half a configuration.
  Pinned by Task 3.

---

### Task 1: The DHCPv6 client always Solicits

**Files:**
- Modify: `configs/systemd/nivuus-wan-orange-dhcp6.service`
- Modify: `configs/network/orange/dhclient-exit-hook`
- Test: `scripts/tests/test_orange_dhclient_hooks.sh`, `scripts/tests/test_orange_dhcp_options.py`

**Interfaces:**
- Produces: `$STATE/wan-orange.prefix` (one line, e.g. `2a01:cb14:8380:c000::/56`)
  is the only record of the delegated prefix; it exists only while a lease is bound.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_orange_dhclient_hooks.sh`, before its summary lines:

```bash
echo "== a fresh Solicit has no old prefix: the recorded one is cleaned =="
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:2:200::/56 ""
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:3:300::/56 ""
grep -q "route del unreachable 2a01:cb00:2:200::/56" "$TMP/ip.log" && ok "recorded route removed" || bad "recorded route removed" "$(cat "$TMP/ip.log")"
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:3:300::/56 ""
grep -q "route del" "$TMP/ip.log" && bad "same prefix deletes nothing" "$(cat "$TMP/ip.log")" || ok "same prefix deletes nothing"
```

Add to the unit-file test class of `scripts/tests/test_orange_dhcp_options.py`:

```python
    def test_dhcp6_unit_forgets_its_lease_before_starting(self):
        unit = (ROOT / "configs/systemd/nivuus-wan-orange-dhcp6.service").read_text()
        self.assertIn("ExecStartPre=/bin/rm -f /var/lib/dhcp/dhclient-orange-v6.lease", unit)
        self.assertNotIn("rm -f /var/lib/dhcp/dhclient-orange-v6.duid", unit)
```

- [ ] **Step 2: Run them to see them fail**

Run: `bash scripts/tests/test_orange_dhclient_hooks.sh; python3 scripts/tests/test_orange_dhcp_options.py`
Expected: `FAIL — recorded route removed`; `AssertionError` on `ExecStartPre`.

- [ ] **Step 3: Implement**

In `configs/systemd/nivuus-wan-orange-dhcp6.service`, add above `ExecStart=`:

```ini
# Orange answers Router and Neighbor Solicitations only after a full exchange.
# A start that finds a lease only Confirms it and the gateway stays silent, so
# the lease is forgotten; the DUID is kept and Orange hands the same prefix back.
ExecStartPre=/bin/rm -f /var/lib/dhcp/dhclient-orange-v6.lease
```

In `configs/network/orange/dhclient-exit-hook`, replace the `BOUND6|RENEW6|REBIND6|REBOOT6)` branch body with:

```sh
            [ -n "$new_ip6_prefix" ] || return 0
            # After a lease removal dhclient has no old prefix to report: the
            # state file is the record of what was parked.
            for old in "$old_ip6_prefix" "$(cat "$state/wan-orange.prefix" 2>/dev/null)"; do
                [ -n "$old" ] && [ "$old" != "$new_ip6_prefix" ] && ip -6 route del unreachable "$old" 2>/dev/null
            done
            ip -6 route replace unreachable "$new_ip6_prefix"
            mkdir -p "$state"
            printf '%s\n' "$new_ip6_prefix" > "$state/wan-orange.prefix"
```

- [ ] **Step 4: Run the tests**

Run: `sh -n configs/network/orange/dhclient-exit-hook && bash scripts/tests/test_orange_dhclient_hooks.sh && python3 scripts/tests/test_orange_dhcp_options.py`
Expected: every hook assertion `ok`, `0 failed`; Python `OK`.

- [ ] **Step 5: Commit**

```bash
git add configs scripts/tests
git commit -m "fix(host): make the Orange DHCPv6 client Solicit on every start"
```

### Task 2: Prefix arithmetic and radvd rendering

**Files:**
- Create: `scripts/wan_orange_lan6.py`
- Create: `scripts/tests/test_wan_orange_lan6.py`

**Interfaces:**
- Produces: `parse_lans(text: str) -> dict[str, int]`;
  `subnet(prefix: ipaddress.IPv6Network, index: int) -> ipaddress.IPv6Network`;
  `render_radvd(nets: dict[str, ipaddress.IPv6Network]) -> str`.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_wan_orange_lan6.py`:

```python
#!/usr/bin/env python3
"""Tests for scripts/wan_orange_lan6.py. Run: python3 scripts/tests/test_wan_orange_lan6.py"""
import ipaddress
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import wan_orange_lan6 as lan6  # noqa: E402

P56 = ipaddress.IPv6Network("2a01:db8:0:c000::/56")


class Pure(unittest.TestCase):
    def test_parse_reads_interface_and_hex_index(self):
        self.assertEqual(lan6.parse_lans("# LANs\nlan0 0\nlan1 a  # guests\n\n"), {"lan0": 0, "lan1": 10})

    def test_parse_refuses_duplicates_and_garbage(self):
        for text in ("lan0 0\nlan0 1\n", "lan0 0\nlan1 0\n", "lan0\n", "lan0 zz\n", "lan0 0 extra\n"):
            with self.assertRaises(ValueError, msg=text):
                lan6.parse_lans(text)

    def test_subnet_numbers_the_64s_of_the_prefix(self):
        self.assertEqual(str(lan6.subnet(P56, 0)), "2a01:db8:0:c000::/64")
        self.assertEqual(str(lan6.subnet(P56, 2)), "2a01:db8:0:c002::/64")
        self.assertEqual(str(lan6.subnet(P56, 0xFF)), "2a01:db8:0:c0ff::/64")
        self.assertEqual(str(lan6.subnet(ipaddress.IPv6Network("2a01:db8:0:c005::/64"), 0)), "2a01:db8:0:c005::/64")

    def test_subnet_refuses_what_does_not_fit(self):
        for prefix, index in ((P56, 0x100), (P56, -1), (ipaddress.IPv6Network("2a01:db8:0:c005::/64"), 1),
                              (ipaddress.IPv6Network("2a01:db8::/80"), 0)):
            with self.assertRaises(ValueError, msg="%s %s" % (prefix, index)):
                lan6.subnet(prefix, index)

    def test_radvd_announces_exactly_the_given_prefixes(self):
        text = lan6.render_radvd({"lan1": lan6.subnet(P56, 2), "lan0": lan6.subnet(P56, 0)})
        self.assertLess(text.index("interface lan0"), text.index("interface lan1"))
        self.assertEqual(text.count("prefix "), 2)
        self.assertIn("prefix 2a01:db8:0:c000::/64", text)
        self.assertIn("prefix 2a01:db8:0:c002::/64", text)
        for line in ("AdvSendAdvert on;", "AdvDefaultLifetime 900;", "AdvValidLifetime 7200;",
                     "AdvPreferredLifetime 3600;", "DeprecatePrefix on;"):
            self.assertEqual(text.count(line), 2, line)
        self.assertNotIn("RDNSS", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 scripts/tests/test_wan_orange_lan6.py`
Expected: `ModuleNotFoundError: No module named 'wan_orange_lan6'`.

- [ ] **Step 3: Implement**

`scripts/wan_orange_lan6.py` (mode 755):

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 scripts/tests/test_wan_orange_lan6.py && ruff check scripts/wan_orange_lan6.py scripts/tests/test_wan_orange_lan6.py`
Expected: `Ran 5 tests`, `OK`; `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add scripts/wan_orange_lan6.py scripts/tests/test_wan_orange_lan6.py
git commit -m "feat(host): number the LAN /64s of the Orange prefix and render radvd"
```

### Task 3: Apply, withdraw and the reconcile loop

**Files:**
- Modify: `scripts/wan_orange_lan6.py`
- Modify: `scripts/tests/test_wan_orange_lan6.py`

**Interfaces:**
- Consumes: `parse_lans`, `subnet`, `render_radvd` (Task 2); `$STATE/wan-orange.prefix` (Task 1).
- Produces: CLI `wan_orange_lan6.py apply [--no-ra] | withdraw | run | status`.
  Environment: `NIVUUS_TEST_PATH`, `NIVUUS_WAN_IF` (default `enp5s0.832`),
  `NIVUUS_WAN_STATE_DIR` (default `/run/nivuus`), `NIVUUS_LAN6_CONF` (default
  `/etc/nivuus/wan-orange-lan6.conf`), `NIVUUS_PROC` (default
  `/proc/sys/net/ipv6/conf`), `NIVUUS_LAN6_INTERVAL_S` (default `15`).
  Files written: `$STATE/radvd.conf`, `$STATE/wan-orange.lan6` (JSON, interface →
  address), `$STATE/wan-orange.lan6-silent` (counter).
  Units driven: `nivuus-lan6-radvd.service`, `nivuus-wan-orange-dhcp6.service`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_wan_orange_lan6.py`, above the `__main__` guard
(add `import json, os, signal, subprocess, tempfile, time` as separate import lines at the top):

```python
SCRIPT = ROOT / "scripts/wan_orange_lan6.py"
FAKES = {
    "ip": '''case "$*" in
  "-j -6 route show default dev"*) if [ -e "$T/route" ]; then echo '[{"dst":"default","gateway":"fe80::1"}]'; else echo '[]'; fi ;;
  *) echo "ip $*" >> "$T/calls"; case "$*" in *"dev $(cat "$T/missing" 2>/dev/null)") exit 2 ;; esac ;;
esac''',
    "rdisc6": 'echo "rdisc6 $*" >> "$T/calls"; [ -e "$T/ra_answers" ] && : > "$T/route"; exit 0',
    "ndisc6": 'echo "ndisc6 $*" >> "$T/calls"; [ -e "$T/alive" ]',
    "systemctl": '''echo "systemctl $*" >> "$T/calls"
case "$1" in is-active) [ -e "$T/radvd_active" ] ;; restart|start) case "$*" in *radvd*) : > "$T/radvd_active" ;; esac ;;
  stop) rm -f "$T/radvd_active" ;; esac''',
}


class Applied(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        (self.tmp / "bin").mkdir()
        (self.tmp / "state").mkdir()
        (self.tmp / "proc/wan0").mkdir(parents=True)
        for name, body in FAKES.items():
            path = self.tmp / "bin" / name
            path.write_text("#!/bin/sh\nT=%s\n%s\n" % (self.tmp, body))
            path.chmod(0o755)
        (self.tmp / "lans").write_text("lan0 0\nlan1 2\n")
        (self.tmp / "calls").write_text("")
        self.env = dict(os.environ, NIVUUS_TEST_PATH=str(self.tmp / "bin"), NIVUUS_WAN_IF="wan0",
                        NIVUUS_WAN_STATE_DIR=str(self.tmp / "state"), NIVUUS_LAN6_CONF=str(self.tmp / "lans"),
                        NIVUUS_PROC=str(self.tmp / "proc"), NIVUUS_LAN6_INTERVAL_S="1")

    def healthy(self, prefix="2a01:db8:0:c000::/56"):
        (self.tmp / "state/wan-orange.prefix").write_text(prefix + "\n")
        for flag in ("route", "alive"):
            (self.tmp / flag).write_text("")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env, capture_output=True, text=True)

    def calls(self, reset=False):
        text = (self.tmp / "calls").read_text()
        if reset:
            (self.tmp / "calls").write_text("")
        return text

    def test_nothing_is_handed_out_without_a_prefix(self):
        self.assertEqual(self.run_cli("apply").returncode, 0)
        self.assertEqual((self.tmp / "proc/wan0/accept_ra").read_text().strip(), "2")
        self.assertEqual((self.tmp / "proc/wan0/autoconf").read_text().strip(), "0")
        self.assertNotIn("addr replace", self.calls())
        self.assertFalse((self.tmp / "state/radvd.conf").exists())

    def test_healthy_link_addresses_both_lans_and_advertises(self):
        self.healthy()
        self.assertEqual(self.run_cli("apply").returncode, 0)
        calls = self.calls()
        self.assertIn("ip -6 addr replace 2a01:db8:0:c000::1/64 dev lan0", calls)
        self.assertIn("ip -6 addr replace 2a01:db8:0:c002::1/64 dev lan1", calls)
        self.assertIn("systemctl restart nivuus-lan6-radvd.service", calls)
        self.assertIn("prefix 2a01:db8:0:c002::/64", (self.tmp / "state/radvd.conf").read_text())

    def test_second_run_does_not_restart_radvd(self):
        self.healthy()
        self.run_cli("apply")
        self.calls(reset=True)
        self.run_cli("apply")
        calls = self.calls()
        self.assertNotIn("restart nivuus-lan6-radvd", calls)
        self.assertIn("ip -6 addr replace 2a01:db8:0:c000::1/64 dev lan0", calls)

    def test_missing_route_is_solicited_and_blocks_the_lans(self):
        self.healthy()
        (self.tmp / "route").unlink()
        self.assertEqual(self.run_cli("apply").returncode, 0)
        calls = self.calls()
        self.assertIn("rdisc6", calls)
        self.assertNotIn("addr replace", calls)
        (self.tmp / "ra_answers").write_text("")
        self.run_cli("apply")
        self.assertIn("addr replace", self.calls())

    def test_a_new_prefix_replaces_the_old_addresses(self):
        self.healthy()
        self.run_cli("apply")
        self.calls(reset=True)
        self.healthy("2a01:db8:0:d000::/56")
        self.run_cli("apply")
        calls = self.calls()
        self.assertIn("ip -6 addr del 2a01:db8:0:c000::1/64 dev lan0", calls)
        self.assertIn("ip -6 addr replace 2a01:db8:0:d000::1/64 dev lan0", calls)
        self.assertIn("systemctl restart nivuus-lan6-radvd.service", calls)

    def test_a_silent_gateway_restarts_dhcp6_on_the_fourth_run_only(self):
        self.healthy()
        (self.tmp / "alive").unlink()
        for _ in range(3):
            self.run_cli("apply")
        self.assertNotIn("nivuus-wan-orange-dhcp6", self.calls())
        self.run_cli("apply")
        self.assertEqual(self.calls().count("systemctl --no-block restart nivuus-wan-orange-dhcp6.service"), 1)

    def test_an_answer_resets_the_silence_count(self):
        self.healthy()
        (self.tmp / "alive").unlink()
        for _ in range(3):
            self.run_cli("apply")
        (self.tmp / "alive").write_text("")
        self.run_cli("apply")
        (self.tmp / "alive").unlink()
        self.run_cli("apply")
        self.assertNotIn("nivuus-wan-orange-dhcp6", self.calls())

    def test_no_ra_addresses_without_advertising(self):
        self.healthy()
        self.assertEqual(self.run_cli("apply", "--no-ra").returncode, 0)
        calls = self.calls()
        self.assertIn("addr replace", calls)
        self.assertNotIn("radvd", calls)

    def test_a_missing_lan_fails_loudly(self):
        self.healthy()
        (self.tmp / "missing").write_text("lan1")
        result = self.run_cli("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lan1", result.stderr)

    def test_withdraw_removes_addresses_and_stops_advertising(self):
        self.healthy()
        self.run_cli("apply")
        self.calls(reset=True)
        self.assertEqual(self.run_cli("withdraw").returncode, 0)
        calls = self.calls()
        self.assertIn("systemctl stop nivuus-lan6-radvd.service", calls)
        self.assertIn("ip -6 addr del 2a01:db8:0:c000::1/64 dev lan0", calls)
        self.assertIn("ip -6 addr del 2a01:db8:0:c002::1/64 dev lan1", calls)
        self.assertEqual((self.tmp / "proc/wan0/accept_ra").read_text().strip(), "0")
        self.assertFalse((self.tmp / "state/radvd.conf").exists())

    def test_the_loop_withdraws_when_stopped(self):
        self.healthy()
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "run"], env=self.env)
        deadline = time.time() + 10
        while time.time() < deadline and not (self.tmp / "state/radvd.conf").exists():
            time.sleep(0.1)
        self.assertTrue((self.tmp / "state/radvd.conf").exists())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertIn("ip -6 addr del 2a01:db8:0:c000::1/64 dev lan0", self.calls())
        self.assertFalse((self.tmp / "state/radvd.conf").exists())
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 scripts/tests/test_wan_orange_lan6.py`
Expected: the 5 `Pure` tests pass; the 11 `Applied` tests fail or error (the script has no command line yet: it exits 0 having done nothing, so the first read of a file it should have written raises).

- [ ] **Step 3: Implement**

In `scripts/wan_orange_lan6.py`, replace `import ipaddress` with:

```python
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
```

and append after `render_radvd`:

```python
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
```

The fake `ip` logs `route del default` and `addr del` alike; `test_a_missing_lan_fails_loudly`
relies on `check=True` in `set_addresses` and on the message carrying the failing command.

- [ ] **Step 4: Run the tests**

Run: `python3 scripts/tests/test_wan_orange_lan6.py && ruff check scripts/wan_orange_lan6.py scripts/tests/test_wan_orange_lan6.py && wc -l scripts/wan_orange_lan6.py`
Expected: `Ran 16 tests`, `OK`; `All checks passed!`; under 200 lines.

- [ ] **Step 5: Commit**

```bash
git add scripts/wan_orange_lan6.py scripts/tests/test_wan_orange_lan6.py
git commit -m "feat(host): reconcile the LAN IPv6 addresses and radvd with the Orange prefix"
```

### Task 4: Units, configuration and test inventory

**Files:**
- Create: `configs/systemd/nivuus-wan-orange-lan6.service`, `configs/systemd/nivuus-lan6-radvd.service`
- Create: `configs/network/orange/wan-orange-lan6.conf`
- Modify: `installer/Makefile` (Python test loop), `scripts/tests/test_wan_orange_lan6.py`
- Modify: `docs/claude/host-network-rf-wan.md`

**Interfaces:**
- Consumes: CLI of Task 3; unit names `nivuus-lan6-radvd.service`, `nivuus-wan-orange-dhcp6.service`.
- Produces: enabling `nivuus-wan-orange-lan6.service` is what turns IPv6 on for the LANs.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_wan_orange_lan6.py`:

```python
class Shipped(unittest.TestCase):
    def test_units_and_mapping_agree_with_the_script(self):
        lan = (ROOT / "configs/systemd/nivuus-wan-orange-lan6.service").read_text()
        self.assertIn("ExecStart=/usr/local/sbin/nivuus-wan-orange-lan6.py run", lan)
        self.assertIn("PartOf=nivuus-wan-orange-dhcp6.service", lan)
        self.assertIn("WantedBy=nivuus-wan-orange-dhcp6.service", lan)
        radvd = (ROOT / "configs/systemd/nivuus-lan6-radvd.service").read_text()
        self.assertIn("--config /run/nivuus/radvd.conf", radvd)
        self.assertNotIn("[Install]", radvd)
        mapping = lan6.parse_lans((ROOT / "configs/network/orange/wan-orange-lan6.conf").read_text())
        self.assertEqual(mapping, {"localBridge": 0, "publicBridge": 2})
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 scripts/tests/test_wan_orange_lan6.py`
Expected: `FileNotFoundError` on `nivuus-wan-orange-lan6.service`.

- [ ] **Step 3: Create the files**

`configs/systemd/nivuus-wan-orange-lan6.service`:

```ini
[Unit]
Description=Nivuus WAN: hand the Orange IPv6 prefix to the LANs
After=nivuus-wan-orange-dhcp6.service
Requires=nivuus-wan-orange.service
# Restarted with the DHCPv6 client: a new exchange starts from a withdrawn LAN.
PartOf=nivuus-wan-orange-dhcp6.service

[Service]
# Active means IPv6 is handed out. Stopping or crashing withdraws it: the loop
# removes the addresses and stops radvd on its way out.
ExecStart=/usr/local/sbin/nivuus-wan-orange-lan6.py run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=nivuus-wan-orange-dhcp6.service
```

`configs/systemd/nivuus-lan6-radvd.service`:

```ini
[Unit]
Description=Nivuus LAN: IPv6 router advertisements for the Orange prefix
# Started and stopped by nivuus-wan-orange-lan6.py only: never enabled.
ConditionPathExists=/run/nivuus/radvd.conf

[Service]
ExecStartPre=/usr/sbin/radvd --configtest --config /run/nivuus/radvd.conf
ExecStart=/usr/sbin/radvd --nodaemon --config /run/nivuus/radvd.conf --pidfile /run/nivuus-lan6-radvd.pid
Restart=on-failure
RestartSec=10
```

`configs/network/orange/wan-orange-lan6.conf`:

```
# LANs that get a /64 of the Orange prefix: <interface> <subnet index, hex>.
# The index follows the IPv4 numbering (192.168.<index>.0/24).
localBridge 0
publicBridge 2
```

In `installer/Makefile`, add `test_wan_orange_lan6` after `test_orange_dhcp_options` in the Python suite loop.

Append to `docs/claude/host-network-rf-wan.md` one paragraph: what the unit does,
that enabling `nivuus-wan-orange-lan6.service` turns LAN IPv6 on and disabling
plus stopping it turns it off, the mapping file, and that `radvd` and `ndisc6`
are host packages installed by hand.

- [ ] **Step 4: Run the suites**

Run: `python3 scripts/tests/test_wan_orange_lan6.py && python3 scripts/tests/test_orange_dhcp_options.py && cd installer && make test-scripts`
Expected: `Ran 17 tests`, `OK`; `OK`; every shell suite `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add configs installer/Makefile scripts/tests docs/claude
git commit -m "feat(host): units and LAN mapping for the Orange IPv6 prefix"
```

### Task 5: Deployment and acceptance on the host (operator-approved)

Nothing here is automated: each step changes the live router. The agent runs
them one at a time and stops at the first unexpected result. IPv4 is checked
after every step with
`curl -4 -s -m 6 -o /dev/null -w '%{http_code}\n' --interface enp5s0.832 https://www.gstatic.com/generate_204`
(expected `204`).

- [ ] **Step 1: Install the tools and confirm their options**

Run: `apt-get install -y radvd ndisc6`, then disable the packaged unit over D-Bus
(`DisableUnitFiles array:string:"radvd.service"`, `StopUnit`).
Run: `rdisc6 --help; ndisc6 --help; radvd --help`
Expected: `-1`, `-q`, `-r`, `-w` listed for both `ndisc6` tools; `--configtest`,
`--nodaemon`, `--config`, `--pidfile` for radvd. If an option differs, fix the
script and its test first (a Ruling in the ledger), then continue.

- [ ] **Step 2: Deploy the files**

Install `scripts/wan_orange_lan6.py` as `/usr/local/sbin/nivuus-wan-orange-lan6.py`
(755), the two units and the modified `nivuus-wan-orange-dhcp6.service` into
`/etc/systemd/system/`, the hook into `/etc/dhcp/dhclient-exit-hooks.d/`, the
mapping into `/etc/nivuus/wan-orange-lan6.conf`; `Reload` over D-Bus. Verify each
with `cmp`.

- [ ] **Step 3: Prove the Solicit path**

Restart `nivuus-wan-orange-dhcp6.service` over D-Bus, wait 25 s.
Run: `journalctl -u nivuus-wan-orange-dhcp6.service --since -40s -o cat | grep -E "PRC|RCV"`
Expected: `PRC: Soliciting for leases (INIT).` and a `RCV: Reply`; no `INIT-REBOOT`.
Run: `ip -6 route | grep unreachable`
Expected: exactly one line, the current `/56` (the stale `2a01:cb14:9009:5400::/56` is gone).

- [ ] **Step 4: Router only, no advertisement**

Run: `/usr/local/sbin/nivuus-wan-orange-lan6.py apply --no-ra && /usr/local/sbin/nivuus-wan-orange-lan6.py status`
Expected: a gateway `fe80::ba0:bab`, both bridges addressed (`…c000::1`, `…c002::1`).
Run: `curl -6 -s -m 8 -o /dev/null -w '%{http_code}\n' https://www.gstatic.com/generate_204; ping -6 -c 3 2606:4700:4700::1111`
Expected: `204`; 0 % loss. If not: `nivuus-wan-orange-lan6.py withdraw` and stop.

- [ ] **Step 5: Turn it on for the LANs**

Enable and start `nivuus-wan-orange-lan6.service` over D-Bus.
Run after 20 s: `nivuus-wan-orange-lan6.py status; radvd --configtest --config /run/nivuus/radvd.conf`
Expected: `radvd: active`; configuration valid.
Owner check from a device on each network: `https://test-ipv6.com` reports a
public IPv6 address in the expected `/64`.

- [ ] **Step 6: The firewall posture is the IPv4 one**

Run: `nft list ruleset | grep -c "2a01:cb14"` — expected `0` (no rule names the prefix; zones do the work).
Owner check from outside (phone on mobile data): `ping` and an HTTPS request to
a LAN device's IPv6 address must fail; HTTPS to the router's own address
(`…c000::1`) must answer as it does on IPv4.

- [ ] **Step 7: Withdrawal works**

Stop `nivuus-wan-orange-lan6.service` over D-Bus.
Run: `ip -6 addr show dev localBridge scope global; ip -6 route show default`
Expected: only the ULA remains on `localBridge`; no default route. A client loses
its public address' preferred state within seconds (`ip -6 addr` on a Linux
client shows `deprecated`). Start the unit again and re-run Step 5's checks.

- [ ] **Step 8: Record**

Append the measured results to `docs/claude/host-network-rf-wan.md` and commit:

```bash
git add docs/claude && git commit -m "docs(host): IPv6 reaches the LANs through the Orange prefix"
```

## Known residuals

- A reboot of the host is not exercised here: Task 5 Step 3 proves the Solicit
  path by restarting the client, which takes the same code path.
- Whether Orange sends periodic unsolicited RAs is not measured; the loop
  re-solicits whenever the default route is gone, so it does not depend on it.
- The MQTT agent's PPPoE buttons still bring PPPoE up next to DHCP (`nivuus/mqtt`).
