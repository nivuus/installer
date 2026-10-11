#!/usr/bin/env python3
"""Tests for scripts/wan_orange_lan6.py. Run: python3 scripts/tests/test_wan_orange_lan6.py"""
import ipaddress
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
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
        # The guest zone rejects Router Solicitations: a short period is what
        # gives a new guest its address without a firewall rule.
        self.assertEqual(text.count("MaxRtrAdvInterval 60;"), 2)
        # Another daemon (the Thread border router) advertises from the bridge's
        # own link-local address with router lifetime 0: radvd needs its own.
        self.assertEqual(text.count("AdvRASrcAddress {\n        fe80::1;\n    };"), 2)


SCRIPT = ROOT / "scripts/wan_orange_lan6.py"
FAKES = {
    "ip": '''case "$*" in
  "-j -6 route show default dev"*) if [ -e "$T/route" ]; then echo '[{"dst":"default","gateway":"fe80::1"}]'; else echo '[]'; fi ;;
  "-j -6 addr show dev"*) [ -e "$T/dadfailed" ] && echo '[{"addr_info":[{"local":"fe80::1","dadfailed":true}]}]' ;;
  *) echo "ip $*" >> "$T/calls"; [ -e "$T/ip_hangs" ] && sleep 30; case "$*" in *"dev $(cat "$T/missing" 2>/dev/null)") exit 2 ;; esac ;;
esac''',
    "rdisc6": 'echo "rdisc6 $*" >> "$T/calls"; [ -e "$T/ra_answers" ] && : > "$T/route"; exit 0',
    "ndisc6": 'echo "ndisc6 $*" >> "$T/calls"; [ -e "$T/alive" ] || exit "$(cat "$T/ndisc6_rc" 2>/dev/null || echo 2)"',
    "systemctl": '''echo "systemctl $*" >> "$T/calls"
[ "$1" = --no-block ] && shift
case "$1" in is-active) [ -e "$T/radvd_active" ] ;; restart|start) case "$*" in *radvd*) : > "$T/radvd_active" ;; *dhcp6*) [ ! -e "$T/dhcp6_fails" ] ;; esac ;;
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
        self.assertIn("ip -6 addr replace fe80::1/64 dev lan0", calls)
        self.assertIn("ip -6 addr replace fe80::1/64 dev lan1", calls)

    def test_a_duplicate_address_stops_before_advertising(self):
        self.healthy()
        (self.tmp / "dadfailed").write_text("")
        result = self.run_cli("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr)
        self.assertNotIn("radvd", self.calls())

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
        # lan0 was addressed before lan1 failed: the withdrawal must know it.
        self.calls(reset=True)
        self.assertEqual(self.run_cli("withdraw").returncode, 0)
        self.assertIn("ip -6 addr del 2a01:db8:0:c000::1/64 dev lan0", self.calls())

    def test_withdraw_works_before_anything_was_applied(self):
        subprocess.run(["rm", "-rf", str(self.tmp / "state")])
        self.assertEqual(self.run_cli("withdraw").returncode, 0)

    def test_a_tool_that_hangs_fails_the_run(self):
        self.healthy()
        (self.tmp / "ip_hangs").write_text("")
        started = time.time()
        result = subprocess.run([sys.executable, str(SCRIPT), "apply"], capture_output=True, text=True,
                                env=dict(self.env, NIVUUS_LAN6_TOOL_TIMEOUT_S="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.time() - started, 10)
        self.assertIn("did not return", result.stderr)

    def test_a_prefix_without_a_route_restarts_dhcp6_on_the_fourth_run(self):
        self.healthy()
        (self.tmp / "route").unlink()
        for _ in range(3):
            self.run_cli("apply")
        self.assertNotIn("nivuus-wan-orange-dhcp6", self.calls())
        self.run_cli("apply")
        self.assertEqual(self.calls().count("restart nivuus-wan-orange-dhcp6.service"), 1)

    def test_restarts_back_off(self):
        self.healthy()
        (self.tmp / "alive").unlink()
        for _ in range(4 + 19):
            self.run_cli("apply")
        self.assertEqual(self.calls().count("restart nivuus-wan-orange-dhcp6.service"), 1)
        self.run_cli("apply")
        self.assertEqual(self.calls().count("restart nivuus-wan-orange-dhcp6.service"), 2)

    def test_a_probe_error_is_not_read_as_silence(self):
        self.healthy()
        (self.tmp / "alive").unlink()
        (self.tmp / "ndisc6_rc").write_text("1")
        results = [self.run_cli("apply") for _ in range(5)]
        self.assertTrue(all(r.returncode != 0 for r in results))
        self.assertNotIn("nivuus-wan-orange-dhcp6", self.calls())

    def test_a_restart_that_fails_is_an_error(self):
        self.healthy()
        (self.tmp / "alive").unlink()
        (self.tmp / "dhcp6_fails").write_text("")
        results = [self.run_cli("apply") for _ in range(4)]
        self.assertEqual([r.returncode for r in results[:3]], [0, 0, 0])
        self.assertNotEqual(results[3].returncode, 0)

    def test_withdraw_removes_addresses_and_stops_advertising(self):
        self.healthy()
        self.run_cli("apply")
        self.calls(reset=True)
        self.assertEqual(self.run_cli("withdraw").returncode, 0)
        calls = self.calls()
        self.assertIn("systemctl stop nivuus-lan6-radvd.service", calls)
        self.assertIn("ip -6 addr del 2a01:db8:0:c000::1/64 dev lan0", calls)
        self.assertIn("ip -6 addr del 2a01:db8:0:c002::1/64 dev lan1", calls)
        self.assertIn("ip -6 addr del fe80::1/64 dev lan0", calls)
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


class Shipped(unittest.TestCase):
    def test_units_and_mapping_agree_with_the_script(self):
        lan = (ROOT / "configs/systemd/nivuus-wan-orange-lan6.service").read_text()
        self.assertIn("ExecStart=/usr/local/sbin/nivuus-wan-orange-lan6.py run", lan)
        self.assertIn("PartOf=nivuus-wan-orange-dhcp6.service", lan)
        self.assertIn("WantedBy=nivuus-wan-orange-dhcp6.service", lan)
        radvd = (ROOT / "configs/systemd/nivuus-lan6-radvd.service").read_text()
        self.assertIn("--config /run/nivuus/radvd.conf", radvd)
        # A killed loop cannot run its own withdrawal: systemd must.
        self.assertIn("ExecStopPost=/usr/local/sbin/nivuus-wan-orange-lan6.py withdraw", lan)
        self.assertIn("BindsTo=nivuus-wan-orange-lan6.service", radvd)
        self.assertIn("After=nivuus-wan-orange-lan6.service", radvd)
        self.assertNotIn("[Install]", radvd)
        mapping = lan6.parse_lans((ROOT / "configs/network/orange/wan-orange-lan6.conf").read_text())
        self.assertEqual(mapping, {"localBridge": 0, "publicBridge": 2})


if __name__ == "__main__":
    unittest.main()
