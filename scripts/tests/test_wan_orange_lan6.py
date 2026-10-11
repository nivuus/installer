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


if __name__ == "__main__":
    unittest.main()
