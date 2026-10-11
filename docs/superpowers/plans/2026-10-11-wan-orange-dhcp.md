# WAN Orange over DHCP (VLAN 832), plan 1 of 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the reference host reaches the Internet through Orange's DHCP service on
VLAN 832 instead of PPPoE on VLAN 835, holds a delegated IPv6 /56, and falls
back to PPPoE by itself if the move fails.

**Architecture:** NetworkManager leaves `enp5s0.832` alone. One oneshot unit
prepares it (VLAN, 802.1p marking by `tc`, firewalld zone, CAKE); two units run
ISC `dhclient` in the foreground with options rendered from the PPPoE
credentials at each start. A detached cutover script, run by the operator,
switches modes and rolls back unaided. Plan 2 hands the /56 to the LAN.

**Tech Stack:** POSIX sh, Python 3 stdlib, `iproute2` (`tc` clsact, flower,
skbedit, cake), ISC dhclient 4.4.3-P1 (installed; chosen over systemd-networkd,
see spec decision 1), firewalld, systemd over D-Bus (`busctl`). No new package.

**Spec:** `docs/superpowers/specs/2026-10-11-wan-orange-dhcp-design.md`

## Global Constraints

- English in code, comments, log lines, commit messages. No file over 500 lines.
- 802.1p priority 6 on DHCPv4, DHCPv6, ARP, ICMPv6 RS/NS/NA; 0 on everything else.
- Option 90 and option 11 carry identical bytes; the salt is drawn at every start.
- Never restart NetworkManager, never `firewall-cmd --reload` (it drops the
  cloud-gaming runtime forward-ports): add rules `--permanent` and at runtime.
- Never reconnect PPPoE less than 300 s after a PADT; retries 360 s apart.
- No IPv6 default route and no IPv6 forwarding to Orange in this plan.
- `systemctl` does not work from a Claude session or from `!`: drive systemd
  with `busctl` there. Inside unit files `systemctl` is fine.
- The cutover is launched by the operator, with the phone tether up.
- No secret on a command line or in a log; rendered files are mode 0600.

## Review Focus

1. A forwarded packet whose TOS maps to socket priority 6 (`IPTOS_LOWDELAY`,
   SSH, gaming) must leave with 802.1p 0, not 6. Pinned in Task 3.
2. The PPPoE password may contain `$`, `%`, `\` or non-ASCII bytes; the
   rendered configuration must carry the right hash and the template engine
   must not expand it. Pinned in Task 1.
3. The delegated prefix can change on renewal; the old `unreachable` route must
   go and the state file must hold the new prefix. Pinned in Task 2.
4. A cutover started without a working tether would strand the operator and
   the session. It must refuse before touching anything. Pinned in Task 5.
5. `dhclient-script` rewrites `/etc/resolv.conf`; on this host that file is
   static and feeds three dnsmasq instances. It must be left alone, even though
   `resolved-enter` is sourced after most hooks. Pinned in Task 2.

---

### Task 1: Option generator

**Files:**
- Create: `scripts/orange_dhcp_options.py`
- Create: `configs/network/orange/dhclient-orange-v4.conf.template`, `configs/network/orange/dhclient-orange-v6.conf.template`
- Test: `scripts/tests/test_orange_dhcp_options.py`
- Modify: `installer/Makefile` (append `test_orange_dhcp_options` to the Python suite loop that ends with `test_install_engine_base`)

**Interfaces:**
- Produces: `authentication(login: bytes, password: bytes, salt: bytes | None) -> bytes`,
  `values(login, password, livebox: int, salt=None) -> dict[str, str]`,
  CLI `orange_dhcp_options.py render --family {4,6} --templates DIR --out DIR [--profile FILE] [--livebox N]`
  writing `DIR/dhclient-orange-v{4,6}.conf` (mode 0600; default `/etc/dhcp/nivuus-orange`, the only tree AppArmor lets dhclient read). Deployed as `/usr/local/sbin/nivuus-orange-dhcp-options.py`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Tests for scripts/orange_dhcp_options.py. Run: python3 scripts/tests/test_orange_dhcp_options.py"""
import hashlib, os, pathlib, stat, subprocess, sys, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import orange_dhcp_options as o

SALT = b"0123456789abcdef"

class Authentication(unittest.TestCase):
    def test_layout_matches_the_answered_probe(self):
        a = o.authentication(b"fti/abcdefg", b"secret", SALT)
        self.assertEqual(len(a), 70)                     # 0x46, as a Livebox sends
        self.assertEqual(a[:20].hex(), "00" * 11 + "1a0900000558010341")
        self.assertEqual(a[20:22], b"\x01\x0d")
        self.assertEqual(a[22:33], b"fti/abcdefg")
        self.assertEqual(a[33:35], b"\x3c\x12")
        self.assertEqual(a[35:51], SALT)
        self.assertEqual(a[51:54], b"\x03\x13" + SALT[:1])
        self.assertEqual(a[54:], hashlib.md5(SALT[:1] + b"secret" + SALT).digest())

    def test_salt_is_fresh_by_default(self):
        self.assertNotEqual(o.authentication(b"fti/a", b"p"), o.authentication(b"fti/a", b"p"))

    def test_rejects_a_salt_of_the_wrong_length(self):
        with self.assertRaises(ValueError):
            o.authentication(b"fti/a", b"p", b"short")

class Values(unittest.TestCase):
    def test_v4_and_v6_carry_the_same_authentication(self):
        v = o.values(b"fti/abcdefg", b"secret", 5, SALT)
        self.assertEqual(v["AUTHENTICATION"].count(":"), 69)
        self.assertTrue(v["USER_CLASS_4"].startswith("2b:46:53:56"))   # length 43, then "FSV"
        self.assertTrue(v["USER_CLASS_6"].startswith("00:2b:46:53:56"))
        self.assertEqual(v["VENDOR_CLASS_6"], "00:00:04:0e:00:05:73:61:67:65:6d")

class Render(unittest.TestCase):
    def test_hostile_password_is_hashed_not_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "p.nmconnection").write_bytes(
                "[pppoe]\nusername=fti/abcdefg\npassword=p$HOME%s\\é\n".encode())
            rc = subprocess.run([sys.executable, str(ROOT / "scripts/orange_dhcp_options.py"),
                "render", "--family", "4", "--profile", str(tmp / "p.nmconnection"),
                "--templates", str(ROOT / "configs/network/orange"), "--out", str(tmp)],
                capture_output=True, text=True)
            self.assertEqual(rc.returncode, 0, rc.stderr)
            out = tmp / "dhclient-orange-v4.conf"
            text = out.read_text()
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertNotIn("$", text)
            self.assertNotIn("HOME", text)
            self.assertNotIn(os.environ.get("HOME", "/nonexistent-home"), text)
            self.assertIn("send authentication 00:00:00", text)
            self.assertNotIn("secret", rc.stdout + rc.stderr)

    def test_missing_credentials_fail_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "p.nmconnection").write_text("[pppoe]\nusername=fti/abcdefg\n")
            rc = subprocess.run([sys.executable, str(ROOT / "scripts/orange_dhcp_options.py"),
                "render", "--family", "6", "--profile", str(tmp / "p.nmconnection"),
                "--templates", str(ROOT / "configs/network/orange"), "--out", str(tmp)],
                capture_output=True, text=True)
            self.assertNotEqual(rc.returncode, 0)
            self.assertFalse((tmp / "dhclient-orange-v6.conf").exists())

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it** — `python3 scripts/tests/test_orange_dhcp_options.py` — expected: `ModuleNotFoundError: No module named 'orange_dhcp_options'`.

- [ ] **Step 3: Write the templates**

`configs/network/orange/dhclient-orange-v4.conf.template`:

```
# Rendered by nivuus-orange-dhcp-options.py at every start. Do not edit.
option user-class     code 77 = string;
option authentication code 90 = string;

send dhcp-parameter-request-list 1, 3, 6, 15, 28, 51, 58, 59, 90, 119, 120, 125;
send vendor-class-identifier "${VENDOR_CLASS_4}";
send dhcp-client-identifier = hardware;
send user-class ${USER_CLASS_4};
send authentication ${AUTHENTICATION};
```

`configs/network/orange/dhclient-orange-v6.conf.template`:

```
# Rendered by nivuus-orange-dhcp-options.py at every start. Do not edit.
option dhcp6.vendorclass code 16 = string;
option dhcp6.userclass   code 15 = string;
option dhcp6.auth        code 11 = string;

send dhcp6.vendorclass ${VENDOR_CLASS_6};
send dhcp6.userclass ${USER_CLASS_6};
send dhcp6.auth ${AUTHENTICATION};

request dhcp6.auth, dhcp6.vendorclass, dhcp6.userclass;
```

- [ ] **Step 4: Write the implementation** — `scripts/orange_dhcp_options.py`:

```python
#!/usr/bin/env python3
"""Render the dhclient configuration Orange expects on VLAN 832.

The DHCP credentials are the PPPoE ones; they are read from the NetworkManager
profile and never printed. Layout: docs/superpowers/specs/2026-10-11-wan-orange-dhcp-design.md.
"""
import argparse, configparser, hashlib, os, pathlib, secrets, string, sys

ORANGE_PREFIX = bytes(11) + bytes.fromhex("1a0900000558010341")
VENDOR = b"sagem"
DEFAULT_PROFILE = "/etc/NetworkManager/system-connections/pppoe-enp6s0.835.nmconnection"


def authentication(login, password, salt=None):
    salt = secrets.token_hex(8).encode() if salt is None else salt
    if len(salt) != 16:
        raise ValueError("salt must be 16 bytes")
    ident = salt[:1]
    digest = hashlib.md5(ident + password + salt).digest()
    return (ORANGE_PREFIX + bytes([0x01, len(login) + 2]) + login
            + bytes([0x3C, len(salt) + 2]) + salt
            + bytes([0x03, len(ident) + len(digest) + 2]) + ident + digest)


def colon_hex(data):
    return ":".join("%02x" % byte for byte in data)


def values(login, password, livebox, salt=None):
    name = ("FSVDSL_livebox.Internet.softathome.Livebox%d" % livebox).encode()
    return {
        "AUTHENTICATION": colon_hex(authentication(login, password, salt)),
        "VENDOR_CLASS_4": VENDOR.decode(),
        "USER_CLASS_4": colon_hex(bytes([len(name)]) + name),
        "VENDOR_CLASS_6": colon_hex(bytes.fromhex("0000040e") + len(VENDOR).to_bytes(2, "big") + VENDOR),
        "USER_CLASS_6": colon_hex(len(name).to_bytes(2, "big") + name),
    }


def credentials(profile):
    cfg = configparser.RawConfigParser()
    with open(profile, encoding="utf-8") as handle:
        cfg.read_file(handle)
    return cfg["pppoe"]["username"].encode(), cfg["pppoe"]["password"].encode()


def render(family, profile, templates, out, livebox):
    login, password = credentials(profile)
    name = "dhclient-orange-v%s.conf" % family
    template = (pathlib.Path(templates) / (name + ".template")).read_text(encoding="utf-8")
    text = string.Template(template).substitute(values(login, password, livebox))
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(target, 0o600)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("render")
    cmd.add_argument("--family", choices=["4", "6"], required=True)
    cmd.add_argument("--profile", default=DEFAULT_PROFILE)
    cmd.add_argument("--templates", default="/usr/local/share/nivuus/orange")
    cmd.add_argument("--out", default="/etc/dhcp/nivuus-orange")
    cmd.add_argument("--livebox", type=int, default=5)
    args = parser.parse_args()
    try:
        render(args.family, args.profile, args.templates, args.out, args.livebox)
    except (OSError, KeyError) as error:
        print("orange_dhcp_options: cannot render: %s" % type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests** — expected: `OK`, 6 tests. Then add the suite name to `installer/Makefile`.
- [ ] **Step 6: Commit** — `git commit -m "feat(host): render the dhclient options Orange expects on VLAN 832"`

---

### Task 2: dhclient hooks

**Files:**
- Create: `configs/network/orange/dhclient-enter-hook`, `configs/network/orange/dhclient-exit-hook`
- Test: `scripts/tests/test_orange_dhclient_hooks.sh`
- Modify: `installer/Makefile` (append `test_orange_dhclient_hooks` to the shell loop holding `test_net_rps_ecores`)

**Interfaces:**
- Consumes: dhclient-script variables `interface`, `reason`, `new_ip6_prefix`, `old_ip6_prefix`.
- Produces: `/run/nivuus/wan-orange.prefix` (one line, the /56), read by plan 2.
  Deployed as `/etc/dhcp/dhclient-enter-hooks.d/zz-nivuus-wan-orange` (the `zz-`
  prefix sources it after `resolved-enter`) and `/etc/dhcp/dhclient-exit-hooks.d/nivuus-wan-orange`.

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_orange_dhclient_hooks.sh`:

```bash
#!/bin/bash
# Tests for the Orange dhclient hooks, against a fake `ip` and a temp state dir.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/../../configs/network/orange" && pwd)"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
mkdir -p "$TMP/bin"; printf '#!/bin/sh\necho "$*" >> "%s/ip.log"\n' "$TMP" > "$TMP/bin/ip"; chmod +x "$TMP/bin/ip"
run_exit() { # reason new old interface
    ( PATH="$TMP/bin:$PATH"; export NIVUUS_WAN_STATE_DIR="$TMP/state"
      reason=$1 new_ip6_prefix=$2 old_ip6_prefix=$3 interface=${4:-enp5s0.832}
      . "$DIR/dhclient-exit-hook" ); }

echo "== a bound prefix is parked and recorded =="
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:1:100::/56 ""
grep -q "route replace unreachable 2a01:cb00:1:100::/56" "$TMP/ip.log" && ok "unreachable route" || bad "unreachable route" "$(cat "$TMP/ip.log")"
[ "$(cat "$TMP/state/wan-orange.prefix")" = "2a01:cb00:1:100::/56" ] && ok "state file" || bad "state file" "$(cat "$TMP/state/wan-orange.prefix" 2>&1)"

echo "== a changed prefix replaces the old one =="
: > "$TMP/ip.log"; run_exit RENEW6 2a01:cb00:2:200::/56 2a01:cb00:1:100::/56
grep -q "route del unreachable 2a01:cb00:1:100::/56" "$TMP/ip.log" && ok "old route removed" || bad "old route removed" "$(cat "$TMP/ip.log")"
[ "$(cat "$TMP/state/wan-orange.prefix")" = "2a01:cb00:2:200::/56" ] && ok "state follows" || bad "state follows" "$(cat "$TMP/state/wan-orange.prefix")"

echo "== an unchanged renewal deletes nothing =="
: > "$TMP/ip.log"; run_exit RENEW6 2a01:cb00:2:200::/56 2a01:cb00:2:200::/56
grep -q "route del" "$TMP/ip.log" && bad "no deletion" "$(cat "$TMP/ip.log")" || ok "no deletion"

echo "== release clears everything =="
: > "$TMP/ip.log"; run_exit RELEASE6 "" 2a01:cb00:2:200::/56
[ ! -e "$TMP/state/wan-orange.prefix" ] && ok "state removed" || bad "state removed" "still there"

echo "== another interface is ignored =="
: > "$TMP/ip.log"; run_exit BOUND6 2a01:cb00:9:900::/56 "" eth9
[ ! -s "$TMP/ip.log" ] && ok "no route touched" || bad "no route touched" "$(cat "$TMP/ip.log")"

echo "== resolv.conf is left alone on the WAN only =="
out=$( make_resolv_conf() { echo REWRITTEN; }; interface=enp5s0.832; . "$DIR/dhclient-enter-hook"; make_resolv_conf )
[ -z "$out" ] && ok "neutralised on enp5s0.832" || bad "neutralised on enp5s0.832" "$out"
out=$( make_resolv_conf() { echo REWRITTEN; }; interface=eth9; . "$DIR/dhclient-enter-hook"; make_resolv_conf )
[ "$out" = REWRITTEN ] && ok "untouched elsewhere" || bad "untouched elsewhere" "$out"

echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run it** — `bash scripts/tests/test_orange_dhclient_hooks.sh` — expected: failures, the hook files do not exist.

- [ ] **Step 3: Write the hooks**

`configs/network/orange/dhclient-enter-hook`:

```sh
# Sourced by dhclient-script. /etc/resolv.conf is static on this host and feeds
# the three LAN dnsmasq instances: the WAN lease must not rewrite it.
if [ "$interface" = "${NIVUUS_WAN_IF:-enp5s0.832}" ]; then
    make_resolv_conf() { :; }
fi
```

`configs/network/orange/dhclient-exit-hook`:

```sh
# Sourced by dhclient-script. Parks the delegated /56 behind an unreachable
# route, so nothing inside it is ever routed back to Orange, and records it
# for the LAN side (plan 2).
nivuus_wan_prefix() {
    [ "$interface" = "${NIVUUS_WAN_IF:-enp5s0.832}" ] || return 0
    state=${NIVUUS_WAN_STATE_DIR:-/run/nivuus}
    case "$reason" in
        BOUND6|RENEW6|REBIND6|REBOOT6)
            [ -n "$new_ip6_prefix" ] || return 0
            if [ -n "$old_ip6_prefix" ] && [ "$old_ip6_prefix" != "$new_ip6_prefix" ]; then
                ip -6 route del unreachable "$old_ip6_prefix" 2>/dev/null
            fi
            ip -6 route replace unreachable "$new_ip6_prefix"
            mkdir -p "$state"
            printf '%s\n' "$new_ip6_prefix" > "$state/wan-orange.prefix"
            ;;
        EXPIRE6|RELEASE6|STOP6)
            [ -n "$old_ip6_prefix" ] && ip -6 route del unreachable "$old_ip6_prefix" 2>/dev/null
            rm -f "$state/wan-orange.prefix"
            ;;
    esac
}
nivuus_wan_prefix
```

- [ ] **Step 4: Run the tests** — expected: `9 passed, 0 failed`. Add the suite to the Makefile shell loop.
- [ ] **Step 5: Commit** — `git commit -m "feat(host): park the delegated prefix and keep resolv.conf out of the WAN lease"`

---

### Task 3: Interface preparation and 802.1p marking

**Files:**
- Create: `scripts/wan-orange.sh`
- Test: `scripts/tests/test_wan_orange_cos.sh` (needs root and `tcpdump`; prints `SKIP` otherwise and exits 0)

**Interfaces:**
- Produces: `wan-orange.sh {prepare|teardown|status|check}`; environment
  `NIVUUS_WAN_PARENT` (default `enp5s0`), `NIVUUS_WAN_IF` (default `$PARENT.832`),
  `NIVUUS_WAN_RATE` (default `780mbit`, or `RATE` from `/etc/default/nivuus-wan-cake`),
  `NIVUUS_WAN_NO_FIREWALL=1` (tests). Deployed as `/usr/local/sbin/nivuus-wan-orange.sh`.

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_wan_orange_cos.sh`:

```bash
#!/bin/bash
# Proves the 802.1p marking on a veth pair in a network namespace: what leaves
# the VLAN is captured on its parent, the only place the tag is visible.
set -uo pipefail
[ "$(id -u)" = 0 ] && command -v tcpdump >/dev/null || { echo "SKIP: needs root and tcpdump"; exit 0; }
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-orange.sh"
NS=nvcos$$; TMP=$(mktemp -d)
trap 'ip netns del $NS 2>/dev/null; rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
ip netns add $NS
ip -n $NS link add wan0 type veth peer name peer0
ip -n $NS link set wan0 up; ip -n $NS link set peer0 up
n() { ip netns exec $NS env NIVUUS_WAN_PARENT=wan0 NIVUUS_WAN_NO_FIREWALL=1 "$@"; }
n "$SCRIPT" prepare || { echo "prepare failed"; exit 1; }
n ip addr add 192.0.2.2/24 dev wan0.832
ip netns exec $NS tcpdump -i peer0 -e -n -l -c 40 vlan 832 > "$TMP/cap" 2>/dev/null &
CAP=$!; sleep 1
n ping -c 1 -W 1 192.0.2.1 >/dev/null 2>&1                         # triggers ARP
n python3 -c "
import socket
def sock(port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wan0.832')
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('0.0.0.0', port))
    return s
plain = sock()
plain.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x10)           # IPTOS_LOWDELAY -> priority 6
plain.sendto(b'x', ('192.0.2.255', 9999))                          # ordinary traffic
sock(68).sendto(b'x', ('255.255.255.255', 67))                     # DHCPv4 shape
"
n ping -6 -c 1 -W 1 -I wan0.832 ff02::2 >/dev/null 2>&1            # ordinary ICMPv6 echo
n ping -6 -c 1 -W 1 fe80::1%wan0.832 >/dev/null 2>&1               # unknown neighbour: triggers an NS
sleep 2; kill $CAP 2>/dev/null; wait $CAP 2>/dev/null
pcp() { grep -E "$1" "$TMP/cap" | head -1 | grep -oE "vlan 832, p [0-9]" | grep -oE "[0-9]$"; }
[ "$(pcp 'ethertype ARP')" = 6 ] && ok "ARP leaves with priority 6" || bad "ARP priority" "$(pcp 'ethertype ARP')"
[ "$(pcp '\.68 > .*\.67')" = 6 ] && ok "DHCPv4 leaves with priority 6" || bad "DHCPv4 priority" "$(pcp '\.68 > .*\.67')"
[ "$(pcp '\.9999')" = 0 ] && ok "low-delay traffic leaves with priority 0" || bad "ordinary traffic priority" "$(pcp '\.9999')"
[ "$(pcp 'echo request')" = 0 ] && ok "ICMPv6 echo leaves with priority 0" || bad "ICMPv6 echo priority" "$(pcp 'echo request')"
[ "$(pcp 'neighbor solicitation|router solicitation')" = 6 ] && ok "ND leaves with priority 6" || bad "ND priority" "$(pcp 'neighbor solicitation|router solicitation')"
n "$SCRIPT" teardown; n ip link show wan0.832 >/dev/null 2>&1 && bad "teardown" "interface still there" || ok "teardown removes the interface"
echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run it** — `sudo bash scripts/tests/test_wan_orange_cos.sh` — expected: `prepare failed`.

- [ ] **Step 3: Write the implementation** — `scripts/wan-orange.sh`:

```sh
#!/bin/sh
# Owns the Orange DHCP interface (VLAN 832): creation, 802.1p marking, firewalld
# zone, shaper, liveness. NetworkManager does not manage this interface.
#
# Orange wants priority 6 on DHCPv4, DHCPv6, ARP and ICMPv6 RS/NS/NA, and 0 on
# everything else. The marking is done here, on the VLAN, by tc: it then holds
# for any DHCP client, for the kernel's own ARP and ND, and for forwarded
# traffic, whose TOS would otherwise map to priority 6 (IPTOS_LOWDELAY).
set -u
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PARENT=${NIVUUS_WAN_PARENT:-enp5s0}
IF=${NIVUUS_WAN_IF:-$PARENT.832}
[ -r /etc/default/nivuus-wan-cake ] && . /etc/default/nivuus-wan-cake
RATE=${NIVUUS_WAN_RATE:-${RATE:-780mbit}}
STATE=${NIVUUS_WAN_STATE_DIR:-/run/nivuus}

mark() {
    f="tc filter add dev $IF egress"
    tc qdisc replace dev "$IF" clsact || return 1
    tc filter del dev "$IF" egress 2>/dev/null
    $f pref 10 protocol arp  matchall action skbedit priority 6 pass &&
    $f pref 20 protocol ip   flower ip_proto udp src_port 68 dst_port 67 action skbedit priority 6 pass &&
    $f pref 30 protocol ipv6 flower ip_proto udp src_port 546 dst_port 547 action skbedit priority 6 pass &&
    $f pref 40 protocol ipv6 flower ip_proto icmpv6 type 133 action skbedit priority 6 pass &&
    $f pref 41 protocol ipv6 flower ip_proto icmpv6 type 135 action skbedit priority 6 pass &&
    $f pref 42 protocol ipv6 flower ip_proto icmpv6 type 136 action skbedit priority 6 pass &&
    $f pref 100 protocol all matchall action skbedit priority 0 pass
}

prepare() {
    ip link show "$IF" >/dev/null 2>&1 ||
        ip link add link "$PARENT" name "$IF" type vlan id 832 egress-qos-map 0:0 6:6 || return 1
    echo 0 > "/proc/sys/net/ipv6/conf/$IF/accept_ra"
    echo 0 > "/proc/sys/net/ipv6/conf/$IF/disable_ipv6"
    [ "${NIVUUS_WAN_NO_FIREWALL:-0}" = 1 ] || firewall-cmd --zone=external --add-interface="$IF" || return 1
    mark || return 1
    tc qdisc replace dev "$IF" root cake bandwidth "$RATE" besteffort nat dual-srchost ethernet ether-vlan || return 1
    ip link set "$IF" up
}

teardown() {
    [ "${NIVUUS_WAN_NO_FIREWALL:-0}" = 1 ] || firewall-cmd --zone=external --remove-interface="$IF" >/dev/null 2>&1
    ip link del "$IF" 2>/dev/null
    rm -f "$STATE/wan-orange.failures"
    return 0
}

status() {
    ip -d -br addr show "$IF" 2>&1
    ip route show default dev "$IF" 2>/dev/null
    tc -s filter show dev "$IF" egress 2>/dev/null | grep -E "pref|Sent"
    cat "$STATE/wan-orange.prefix" 2>/dev/null
}

# Orange asks for a restart from DISCOVER once the gateway has been silent for
# 150 s: three consecutive failures of a check run every 50 s.
check() {
    mkdir -p "$STATE"
    gw=$(ip route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')
    if [ -n "$gw" ] && ping -I "$IF" -c 2 -W 3 -q "$gw" >/dev/null 2>&1; then
        rm -f "$STATE/wan-orange.failures"; return 0
    fi
    n=$(( $(cat "$STATE/wan-orange.failures" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$STATE/wan-orange.failures"
    echo "wan-orange: gateway ${gw:-unknown} silent, failure $n" >&2
    [ "$n" -lt 3 ] && return 0
    rm -f "$STATE/wan-orange.failures"
    echo "wan-orange: restarting both DHCP clients" >&2
    systemctl restart nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service
}

case "${1:-}" in
    prepare|teardown|status|check) "$1" ;;
    *) echo "usage: ${0##*/} prepare|teardown|status|check" >&2; exit 2 ;;
esac
```

- [ ] **Step 4: Run the test** — expected: `6 passed, 0 failed`. If the low-delay
  line reads 6, the catch-all filter is not reached: do not go further, the
  marking is the conformity requirement.
- [ ] **Step 5: Commit** — `git commit -m "feat(host): prepare the Orange VLAN and mark 802.1p priority with tc"`

---

### Task 4: Units, firewall and inventory of `ppp0`

**Files:**
- Create: `configs/systemd/nivuus-wan-orange.service`, `nivuus-wan-orange-dhcp4.service`, `nivuus-wan-orange-dhcp6.service`, `nivuus-wan-orange-check.service`, `nivuus-wan-orange-check.timer`
- Create: `configs/network/orange/nm-unmanaged.conf`, `configs/firewall/nivuus-smb-ratelimit.nft` (copied from `/etc/nftables.d/`, then edited)

**Interfaces:**
- Consumes: Tasks 1-3 deployed under `/usr/local/sbin` and `/usr/local/share/nivuus/orange`.
- Produces: unit names used by Task 5. All units stay **disabled and stopped**.

- [ ] **Step 1: Write the units**

`nivuus-wan-orange.service`:

```ini
[Unit]
Description=Nivuus WAN: Orange VLAN 832 interface (802.1p marking, zone, shaper)
After=NetworkManager.service firewalld.service sys-subsystem-net-devices-enp5s0.device
Wants=firewalld.service
BindsTo=sys-subsystem-net-devices-enp5s0.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/nivuus-wan-orange.sh prepare
ExecStop=/usr/local/sbin/nivuus-wan-orange.sh teardown

[Install]
WantedBy=multi-user.target
```

`nivuus-wan-orange-dhcp4.service`:

```ini
[Unit]
Description=Nivuus WAN: Orange DHCPv4 client
Requires=nivuus-wan-orange.service
After=nivuus-wan-orange.service
PartOf=nivuus-wan-orange.service

[Service]
Environment=IF_METRIC=50
ExecStartPre=/usr/local/sbin/nivuus-orange-dhcp-options.py render --family 4
ExecStart=/sbin/dhclient -4 -d -v -cf /etc/dhcp/nivuus-orange/dhclient-orange-v4.conf -lf /var/lib/dhcp/dhclient-orange-v4.lease -pf /run/dhclient-orange-v4.pid enp5s0.832
ExecStop=/sbin/dhclient -4 -r -cf /etc/dhcp/nivuus-orange/dhclient-orange-v4.conf -lf /var/lib/dhcp/dhclient-orange-v4.lease -pf /run/dhclient-orange-v4.pid enp5s0.832
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

`nivuus-wan-orange-dhcp6.service`: same shape, with `After=nivuus-wan-orange-dhcp4.service`
added, no `IF_METRIC`, `--family 6`, and the command
`/sbin/dhclient -6 -P -D LL -d -v -cf /etc/dhcp/nivuus-orange/dhclient-orange-v6.conf -lf /var/lib/dhcp/dhclient-orange-v6.lease -df /var/lib/dhcp/dhclient-orange-v6.duid -pf /run/dhclient-orange-v6.pid enp5s0.832`
(`ExecStop` is that command with `-r` in place of `-d -v`).

`nivuus-wan-orange-check.service`: `Type=oneshot`, `ExecStart=/usr/local/sbin/nivuus-wan-orange.sh check`.
`nivuus-wan-orange-check.timer`: `OnBootSec=120`, `OnUnitActiveSec=50`, `WantedBy=timers.target`.

`configs/network/orange/nm-unmanaged.conf` (a `[device-*]` section, **not**
`unmanaged-devices=`: a second file redefining that key would replace the list
in `base.conf` and hand the Wi-Fi interfaces back to NetworkManager):

```ini
[device-nivuus-wan-orange]
match-device=interface-name:enp5s0.832
managed=0
```

- [ ] **Step 2: Deploy without starting anything**

```sh
install -m 755 scripts/wan-orange.sh /usr/local/sbin/nivuus-wan-orange.sh
install -m 755 scripts/orange_dhcp_options.py /usr/local/sbin/nivuus-orange-dhcp-options.py
install -d /usr/local/share/nivuus/orange && install -m 644 configs/network/orange/*.template /usr/local/share/nivuus/orange/
install -m 644 configs/network/orange/dhclient-enter-hook /etc/dhcp/dhclient-enter-hooks.d/zz-nivuus-wan-orange
install -m 644 configs/network/orange/dhclient-exit-hook /etc/dhcp/dhclient-exit-hooks.d/nivuus-wan-orange
install -m 644 configs/systemd/nivuus-wan-orange*.service configs/systemd/nivuus-wan-orange-check.timer /etc/systemd/system/
install -m 644 configs/network/orange/nm-unmanaged.conf /etc/NetworkManager/conf.d/nivuus-wan-orange.conf
busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager Reload
nmcli general reload conf
```

Verify: `nmcli -g connection.id con show --active` still lists the bridges and
`pppoe-enp6s0.835`; `iw dev wlp10s0 info` still shows `type AP`.

- [ ] **Step 3: Open the firewall for the new interface** (permanent and runtime, no reload)

```sh
for scope in "--permanent" ""; do
  firewall-cmd $scope --zone=external --add-service=dhcpv6-client
  firewall-cmd $scope --direct --add-rule ipv4 filter FORWARD 30 -i enp5s0.832 -m conntrack --ctstate RELATED,ESTABLISHED,DNAT -j ACCEPT
  firewall-cmd $scope --direct --add-rule ipv4 filter FORWARD 40 -i enp5s0.832 -p icmp -m conntrack --ctstate NEW -j ACCEPT
  firewall-cmd $scope --direct --add-rule ipv4 filter FORWARD 80 -i enp5s0.832 -m conntrack --ctstate INVALID -j DROP
done
```

These mirror the three IPv4 `-i ppp0` rules of `/etc/firewalld/direct.xml`. Its
IPv6 `-i ppp0` rules are deliberately **not** mirrored: plan 2 owns IPv6 forwarding.

- [ ] **Step 4: Teach the SMB rate limit the new interface** — copy
  `/etc/nftables.d/nivuus-smb-ratelimit.nft` into `configs/firewall/`, replace
  both `iifname "ppp0"` with `iifname { "ppp0", "enp5s0.832" }`, install it back,
  then `nft -c -f /etc/nftables.d/nivuus-smb-ratelimit.nft && nft -f /etc/nftables.d/nivuus-smb-ratelimit.nft`.
  Verify: `nft list table inet nivuus_smb` shows the set on both rules.

- [ ] **Step 5: Record what else names `ppp0`** in `docs/claude/host-network-rf-wan.md`:
  `/etc/mqtt-system-agent/config.yaml` (monitored interface list, follow-up in
  `nivuus/mqtt`), `/etc/suricata/*` (suricata is not running), `/etc/ppp/ip-up.d/*`
  (PPPoE only, kept for the rollback), `/usr/local/bin/net-rps-ecores.sh` (not
  needed without PPPoE: verify in Task 6 that `enp5s0`'s four queues share the load).

- [ ] **Step 6: Commit** — `git commit -m "feat(host): units, firewall rules and inventory for the Orange DHCP WAN"`

---

### Task 5: Cutover script with unaided rollback

**Files:**
- Create: `scripts/wan-cutover.sh`
- Test: `scripts/tests/test_wan_cutover.sh` (add to the Makefile shell loop)

**Interfaces:**
- Consumes: unit names from Task 4.
- Produces: `wan-cutover.sh {to-dhcp|to-pppoe} [--dry-run]`; log `/var/log/nivuus-wan-cutover.log`.

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_wan_cutover.sh`:

```bash
#!/bin/bash
# The cutover is tested on what must never go wrong: its refusals and its order.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/wan-cutover.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
out=$(NIVUUS_TETHER_OK=0 "$SCRIPT" to-dhcp --dry-run 2>&1); rc=$?
[ $rc -ne 0 ] && ok "refuses without a working tether" || bad "refuses without a working tether" "$out"
case "$out" in *"nmcli con down"*) bad "touches nothing when refusing" "$out" ;; *) ok "touches nothing when refusing" ;; esac
out=$(NIVUUS_TETHER_OK=1 "$SCRIPT" to-dhcp --dry-run 2>&1); rc=$?
[ $rc -eq 0 ] && ok "dry run succeeds with a tether" || bad "dry run succeeds with a tether" "$out"
down=$(echo "$out" | grep -n "nmcli con down" | cut -d: -f1)
start=$(echo "$out" | grep -n "StartUnit.*nivuus-wan-orange.service" | cut -d: -f1)
[ -n "$down" ] && [ -n "$start" ] && [ "$down" -lt "$start" ] && ok "PPPoE goes down before DHCP starts" || bad "order" "$out"
echo "$out" | grep -q "autoconnect no" && ok "PPPoE autoconnect is disabled first" || bad "autoconnect" "$out"
out=$("$SCRIPT" sideways 2>&1); [ $? -eq 2 ] && ok "unknown mode is a usage error" || bad "usage" "$out"
echo; echo "$PASS passed, $FAIL failed"; [ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run it** — expected: every assertion fails, the script does not exist.

- [ ] **Step 3: Write the implementation** — `scripts/wan-cutover.sh`:

```sh
#!/bin/sh
# Switches the WAN between PPPoE (VLAN 835) and Orange DHCP (VLAN 832).
#
# Run by the operator, never by an agent: it cuts the uplink. It detaches at
# once, logs to $LOG, and rolls back to PPPoE by itself if DHCP does not bring
# the Internet back. It refuses to start unless the phone tether works, because
# that link is what keeps the operator and any session reachable meanwhile.
# Orange rejects a PPPoE reconnection for minutes after a teardown: the
# rollback waits COOLDOWN_S after the PADT and retries every RETRY_S.
set -u
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOG=${NIVUUS_CUTOVER_LOG:-/var/log/nivuus-wan-cutover.log}
PROFILE=pppoe-enp6s0.835
IF=enp5s0.832
UNITS="nivuus-wan-orange.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange-dhcp6.service nivuus-wan-orange-check.timer"
PROBE=https://www.gstatic.com/generate_204
WAIT_S=150 COOLDOWN_S=300 RETRY_S=360
MODE=${1:-}; DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
SD="busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"

log() { echo "$(date '+%F %T') $*"; }
run() { if [ "$DRY" = 1 ]; then echo "would run: $*"; else log "+ $*"; "$@"; fi; }
http_ok() { [ "$(curl -4 -s -m 8 --interface "$1" -o /dev/null -w '%{http_code}' "$PROBE")" = 204 ]; }
tether() { ls /sys/class/net | grep -E '^enx' | head -1; }
tether_ok() {
    [ -n "${NIVUUS_TETHER_OK:-}" ] && { [ "$NIVUUS_TETHER_OK" = 1 ]; return; }
    t=$(tether); [ -n "$t" ] && http_ok "$t"
}
start_units() { for u in $UNITS; do run $SD StartUnit ss "$u" replace; done; }
stop_units()  { for u in nivuus-wan-orange-check.timer nivuus-wan-orange-dhcp6.service nivuus-wan-orange-dhcp4.service nivuus-wan-orange.service; do run $SD StopUnit ss "$u" replace; done; }
enable_units()  { run $SD EnableUnitFiles asbb 4 $UNITS false true; }
disable_units() { run $SD DisableUnitFiles asb 4 $UNITS false; }

pppoe_up() {
    run nmcli con modify "$PROFILE" connection.autoconnect yes
    since=$1
    wait_s=$(( COOLDOWN_S - ($(date +%s) - since) ))
    [ "$DRY" = 0 ] && [ "$wait_s" -gt 0 ] && { log "waiting ${wait_s}s for Orange's cooldown"; sleep "$wait_s"; }
    while :; do
        run nmcli con up "$PROFILE"
        [ "$DRY" = 1 ] && return 0
        sleep 15; http_ok ppp0 && { log "PPPoE is back"; return 0; }
        log "PPPoE not back, next attempt in ${RETRY_S}s"; sleep "$RETRY_S"
    done
}

to_dhcp() {
    tether_ok || { echo "refusing: no working phone tether (plug it in, enable USB tethering)" >&2; return 1; }
    run nmcli con modify "$PROFILE" connection.autoconnect no
    run nmcli con down "$PROFILE"
    down_at=$(date +%s)
    start_units
    [ "$DRY" = 1 ] && { enable_units; return 0; }
    while [ $(( $(date +%s) - down_at )) -lt "$WAIT_S" ]; do
        sleep 5
        if http_ok "$IF"; then
            log "Internet reachable through $IF: $(ip -br addr show $IF)"
            enable_units; log "DHCP mode is now the boot default"; return 0
        fi
    done
    log "no Internet through $IF after ${WAIT_S}s: rolling back to PPPoE"
    journalctl -u nivuus-wan-orange-dhcp4.service -u nivuus-wan-orange-dhcp6.service --since "-5min" --no-pager -o cat | grep -viE "auth|option 90" | tail -40
    stop_units
    pppoe_up "$down_at"
    return 3
}

to_pppoe() {
    disable_units
    stop_units
    pppoe_up "$(date +%s)"
}

case "$MODE" in
    to-dhcp|to-pppoe) ;;
    *) echo "usage: ${0##*/} to-dhcp|to-pppoe [--dry-run]" >&2; exit 2 ;;
esac
fn=$(echo "$MODE" | tr - _)
if [ "$DRY" = 1 ]; then $fn; exit $?; fi
if [ "${3:-}${2:-}" != "--detached" ]; then
    [ "$MODE" = to-dhcp ] && ! tether_ok && { echo "refusing: no working phone tether" >&2; exit 1; }
    setsid nohup "$0" "$MODE" --detached >> "$LOG" 2>&1 < /dev/null &
    echo "started in the background; follow it with: tail -f $LOG"; exit 0
fi
log "=== $MODE ==="; $fn; rc=$?; log "=== $MODE finished, rc=$rc ==="; exit $rc
```

- [ ] **Step 4: Run the tests** — expected: `6 passed, 0 failed`. Add the suite to the Makefile shell loop, then deploy: `install -m 755 scripts/wan-cutover.sh /usr/local/sbin/nivuus-wan-cutover.sh`.
- [ ] **Step 5: Commit** — `git commit -m "feat(host): WAN cutover between PPPoE and Orange DHCP with unaided rollback"`

---

### Task 6: Cutover and acceptance (operator-run)

No code. Each line is a gate: stop at the first one that fails.

- [ ] **Step 1: Preconditions.** `make -C installer test-scripts` green; `sudo bash scripts/tests/test_wan_orange_cos.sh` green on the host; phone plugged in with USB tethering on; `curl -4 --interface "$(ls /sys/class/net | grep ^enx)" -s -o /dev/null -w '%{http_code}\n' https://www.gstatic.com/generate_204` prints `204`; Windows VM off; nobody mid-download.
- [ ] **Step 2: Rehearse.** `sh scripts/wan-cutover.sh to-dhcp --dry-run` and read the order.
- [ ] **Step 3: Cut over.** `sh /usr/local/sbin/nivuus-wan-cutover.sh to-dhcp`, then `tail -f /var/log/nivuus-wan-cutover.log`. Expected within 150 s: `Internet reachable through enp5s0.832`. On rollback the script says so and PPPoE returns after the cooldown.
- [ ] **Step 4: Prove conformity on the wire.** `tcpdump -i enp5s0 -e -n -c 30 vlan 832` during a renewal (`busctl call … RestartUnit ss nivuus-wan-orange-dhcp4.service replace`): DHCP and ARP at `p 6`, a ping to 1.1.1.1 at `p 0`. Address must be public (not `172.16.0.0/12`), and `cat /run/nivuus/wan-orange.prefix` must show a `/56`.
- [ ] **Step 5: Prove the services.** From the LAN: browsing works, MTU is 1500 (`ping -M do -s 1472 1.1.1.1` passes). From outside (phone on mobile data): `https` and SSH answer on the new address. `nft list table inet nivuus_smb` counters move on `enp5s0.832`. `firewall-cmd --get-zone-of-interface=enp5s0.832` says `external`. No IPv6 default route: `ip -6 route show default` is empty.
- [ ] **Step 6: Measure.** Ookla against Scaleway Paris (server 61933), twice: download, upload, latency under load; `mpstat -P ALL 5 1` during the download. Record against the PPPoE baseline (925-935 down, 721 up, 10 ms, one E-core at 88 % softirq when forwarding).
- [ ] **Step 7: Survive a reboot-equivalent.** Stop then start `nivuus-wan-orange.service` over D-Bus: both clients must come back and the Internet with them, with PPPoE staying down (`autoconnect no`).
- [ ] **Step 8: Document and commit.** Rewrite the WAN section of `docs/claude/host-network-rf-wan.md` (mode, units, recovery: `nivuus-wan-cutover.sh to-pppoe`), update its index line in `CLAUDE.md`, commit `docs(host): the WAN runs on Orange DHCP over VLAN 832`.

## Known residuals

- `dhclient` is end-of-life upstream; systemd-networkd is the successor to evaluate.
- DHCP packets carry `dhclient`'s own DSCP, not CS6. Orange asks for priority 6 and calls a matching DSCP "ideal"; revisit if a lease is ever refused.
- The public address is no longer tied to a PPP session: check after the cutover what updates the `allanic.me` records, and that it follows a change of address.
- The `mqtt` agent still reports on `ppp0` only.
