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
