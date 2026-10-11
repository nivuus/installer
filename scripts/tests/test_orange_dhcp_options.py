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

class AppArmorPaths(unittest.TestCase):
    """dhclient is confined (usr.sbin.dhclient): outside these globs it reads
    nothing and says nothing, then runs on its defaults. The wire test proves
    the effect as root; this pins the paths the units and the CLI use."""

    ALLOWED = {"-cf": "/etc/dhcp/*/*", "-lf": "/var/lib/dhcp/dhclient*",
               "-df": "/var/lib/dhcp/dhclient*", "-pf": "/run/dhclient*.pid"}

    def test_units_only_use_paths_the_profile_allows(self):
        import fnmatch, shlex
        for family in ("4", "6"):
            unit = (ROOT / "configs/systemd" / ("nivuus-wan-orange-dhcp%s.service" % family)).read_text()
            lines = [l.split("=", 1)[1] for l in unit.splitlines() if l.startswith(("ExecStart=", "ExecStop="))]
            self.assertEqual(len(lines), 2)
            for line in lines:
                argv = shlex.split(line)
                for flag, glob in self.ALLOWED.items():
                    if flag in argv:
                        path = argv[argv.index(flag) + 1]
                        self.assertTrue(fnmatch.fnmatchcase(path, glob), "%s %s escapes %s" % (flag, path, glob))
                self.assertIn("-cf", argv)
                self.assertIn("-pf", argv)

    def test_cli_renders_where_the_units_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = subprocess.run([sys.executable, str(ROOT / "scripts/orange_dhcp_options.py"),
                                 "render", "--help"], capture_output=True, text=True)
        self.assertIn("/etc/dhcp/nivuus-orange", rc.stdout)
        for family in ("4", "6"):
            unit = (ROOT / "configs/systemd" / ("nivuus-wan-orange-dhcp%s.service" % family)).read_text()
            self.assertIn("-cf /etc/dhcp/nivuus-orange/dhclient-orange-v%s.conf" % family, unit)


if __name__ == "__main__":
    unittest.main()
