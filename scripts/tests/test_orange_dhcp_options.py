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

def render_cli(tmp, profile_bytes, *extra):
    (tmp / "p.nmconnection").write_bytes(profile_bytes)
    return subprocess.run([sys.executable, str(ROOT / "scripts/orange_dhcp_options.py"), "render",
                           "--profile", str(tmp / "p.nmconnection"),
                           "--templates", str(ROOT / "configs/network/orange"), "--out", str(tmp), *extra],
                          capture_output=True, text=True)


def sent(tmp, family, statement):
    text = (tmp / ("dhclient-orange-v%s.conf" % family)).read_text()
    line = [l for l in text.splitlines() if l.startswith("send %s " % statement)][0]
    return bytes.fromhex(line.split()[-1].rstrip(";").replace(":", ""))


class Coherence(unittest.TestCase):
    """Orange wants option 90 and option 11 to carry the same bytes, and parks
    or disconnects a client whose two protocols disagree."""

    def test_one_render_gives_both_families_the_same_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            rc = render_cli(tmp, b"[pppoe]\nusername=fti/abcdefg\npassword=secret\n")
            self.assertEqual(rc.returncode, 0, rc.stderr)
            self.assertEqual(sent(tmp, "4", "authentication"), sent(tmp, "6", "dhcp6.auth"))

    def test_the_units_render_once_for_both_clients(self):
        units = ROOT / "configs/systemd"
        options = (units / "nivuus-wan-orange-options.service").read_text()
        self.assertIn("nivuus-orange-dhcp-options.py render\n", options)
        for family in ("4", "6"):
            unit = (units / ("nivuus-wan-orange-dhcp%s.service" % family)).read_text()
            self.assertNotIn("ExecStartPre", unit)
            self.assertIn("Requires=nivuus-wan-orange.service nivuus-wan-orange-options.service", unit)


class KeyfileCredentials(unittest.TestCase):
    """NetworkManager writes a GKeyFile: backslash and leading-space escapes,
    no trailing-space trimming. The hash must be computed on the real bytes."""

    def digest_of(self, stored, real):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            rc = render_cli(tmp, b"[pppoe]\nusername=fti/abcdefg\npassword=" + stored + b"\n", "--family", "4")
            self.assertEqual(rc.returncode, 0, rc.stderr)
            auth = sent(tmp, "4", "authentication")
        salt, ident, digest = auth[35:51], auth[53:54], auth[54:]
        self.assertEqual(digest, hashlib.md5(ident + real + salt).digest())

    def test_plain(self):
        self.digest_of(b"secret", b"secret")

    def test_backslash_is_unescaped(self):
        self.digest_of(rb"a\\b", rb"a\b")

    def test_leading_space_escape(self):
        self.digest_of(rb"\sa", b" a")

    def test_trailing_space_is_kept(self):
        self.digest_of(b"a ", b"a ")

    def test_hash_percent_dollar_semicolon_and_utf8_are_literal(self):
        self.digest_of("p#1;%s$xé".encode(), "p#1;%s$xé".encode())


class Units(unittest.TestCase):
    def test_route_metric_reaches_dhclient_script(self):
        # dhclient builds its script's environment itself: only -e gets through.
        unit = (ROOT / "configs/systemd/nivuus-wan-orange-dhcp4.service").read_text()
        self.assertIn(" -e IF_METRIC=50 ", unit)
        self.assertNotIn("Environment=IF_METRIC", unit)

    def test_a_failed_preparation_is_retried(self):
        unit = (ROOT / "configs/systemd/nivuus-wan-orange.service").read_text()
        self.assertIn("Restart=on-failure", unit)


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
