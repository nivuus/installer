#!/usr/bin/env python3
"""Tests for installer/install-engine/steps/firewalld_zone.py.

Every test works on a temporary copy of a zones tree: nothing here may read
or write /etc, and no command may run (the step must never reload firewalld).

Run: python3 scripts/tests/test_firewalld_zone.py
"""
import ast
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
STEP = ROOT / "installer/install-engine/steps/firewalld_zone.py"
sys.path.insert(0, str(STEP.parent))
import firewalld_zone as fz  # noqa: E402

# The reference host's external.xml as of 2026-11-09, trimmed: an XML
# declaration, a target attribute, a nested rule and an existing interface.
HOST_EXTERNAL = b"""<?xml version="1.0" encoding="utf-8"?>
<zone target="%%REJECT%%">
  <short>External</short>
  <description>For use on external networks.</description>
  <service name="ssh"/>
  <masquerade/>
  <forward-port port="47984" protocol="tcp" to-port="47984" to-addr="192.168.3.2"/>
  <rule family="ipv4">
    <port port="3389" protocol="tcp"/>
    <log prefix="rdp_scan_DROP:" level="info">
      <limit value="1/m" burst="0"/>
    </log>
    <drop/>
  </rule>
  <!-- a comment the step must keep -->
  <interface name="enp5s0.832"/>
</zone>
"""

VENDOR_EXTERNAL = b"""<?xml version="1.0" encoding="utf-8"?>
<zone>
  <short>External</short>
  <description>For use on external networks.</description>
  <service name="ssh"/>
  <masquerade/>
  <forward/>
</zone>
"""

LINE = b'  <interface name="nvguest0"/>\n'


def _no_commands(*args, **kwargs):
    raise AssertionError(f"the step ran a command: {args!r}")


class Tree:
    """A fake root with /etc/firewalld/zones and /usr/lib/firewalld/zones."""

    def __init__(self, tmp):
        self.root = pathlib.Path(tmp)
        self.etc = self.root / "etc/firewalld/zones"
        self.vendor = self.root / "usr/lib/firewalld/zones"
        self.etc.mkdir(parents=True)
        self.vendor.mkdir(parents=True)
        (self.vendor / "external.xml").write_bytes(VENDOR_EXTERNAL)

    def bind(self):
        with mock.patch.object(subprocess, "run", _no_commands), \
                mock.patch.object(subprocess, "Popen", _no_commands), \
                mock.patch.object(os, "system", _no_commands):
            return fz.bind_interface(str(self.root), "external", "nvguest0")


class BindInterface(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = Tree(self._tmp.name)
        self.ext = self.tree.etc / "external.xml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_inserts_the_line_once_before_the_closing_tag(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        self.assertTrue(self.tree.bind())
        want = HOST_EXTERNAL.replace(b"</zone>\n", LINE + b"</zone>\n")
        self.assertEqual(self.ext.read_bytes(), want)

    def test_other_lines_stay_byte_identical(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        self.tree.bind()
        after = self.ext.read_bytes().splitlines(keepends=True)
        after.remove(LINE)
        self.assertEqual(b"".join(after), HOST_EXTERNAL)

    def test_second_run_changes_nothing(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        self.assertTrue(self.tree.bind())
        first = self.ext.read_bytes()
        mtime = self.ext.stat().st_mtime_ns
        self.assertFalse(self.tree.bind())
        self.assertEqual(self.ext.read_bytes(), first)
        self.assertEqual(self.ext.stat().st_mtime_ns, mtime)
        self.assertEqual(first.count(b'name="nvguest0"'), 1)

    def test_an_existing_binding_written_differently_is_recognised(self):
        text = HOST_EXTERNAL.replace(b"</zone>", b"<interface  name='nvguest0' />\n</zone>")
        self.ext.write_bytes(text)
        self.assertFalse(self.tree.bind())
        self.assertEqual(self.ext.read_bytes(), text)

    def test_mode_and_ownership_are_preserved(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        os.chmod(self.ext, 0o640)
        before = self.ext.stat()
        self.tree.bind()
        after = self.ext.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_no_temporary_file_is_left_behind(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        self.tree.bind()
        self.assertEqual(sorted(p.name for p in self.tree.etc.iterdir()), ["external.xml"])

    def test_without_an_etc_copy_the_vendor_zone_is_the_base(self):
        # firewalld reads /usr/lib when /etc has no override, and creates the
        # /etc copy itself on the first permanent change: do the same.
        self.assertTrue(self.tree.bind())
        want = VENDOR_EXTERNAL.replace(b"</zone>\n", LINE + b"</zone>\n")
        self.assertEqual(self.ext.read_bytes(), want)
        self.assertEqual(stat.S_IMODE(self.ext.stat().st_mode),
                         stat.S_IMODE((self.tree.vendor / "external.xml").stat().st_mode))
        self.assertEqual((self.tree.vendor / "external.xml").read_bytes(), VENDOR_EXTERNAL)

    def test_no_zone_file_at_all_fails_loudly(self):
        (self.tree.vendor / "external.xml").unlink()
        with self.assertRaises(fz.ZoneFileError):
            self.tree.bind()
        self.assertFalse(self.ext.exists())

    def test_interface_bound_to_another_zone_is_refused(self):
        # Two zones claiming one interface is the 12:09 contradiction:
        # firewalld must never be handed one.
        self.ext.write_bytes(HOST_EXTERNAL)
        (self.tree.etc / "internal.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <interface name="nvguest0"/>\n</zone>\n')
        with self.assertRaises(fz.ZoneFileError):
            self.tree.bind()
        self.assertEqual(self.ext.read_bytes(), HOST_EXTERNAL)

    def test_backup_files_of_other_zones_do_not_count(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        (self.tree.etc / "internal.xml.old").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <interface name="nvguest0"/>\n</zone>\n')
        self.assertTrue(self.tree.bind())

    def test_conflict_is_refused_even_when_the_target_already_lists_it(self):
        text = HOST_EXTERNAL.replace(b"</zone>", LINE + b"</zone>")
        self.ext.write_bytes(text)
        (self.tree.etc / "internal.xml").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <interface name="nvguest0"/>\n</zone>\n')
        with self.assertRaises(fz.ZoneFileError):
            self.tree.bind()
        self.assertEqual(self.ext.read_bytes(), text)

    def test_a_malformed_unrelated_zone_is_named_in_the_refusal(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        (self.tree.etc / "work.xml").write_bytes(b"<zone>\n")
        with self.assertRaises(fz.ZoneFileError) as ctx:
            self.tree.bind()
        self.assertIn("work.xml", str(ctx.exception))
        self.assertIn("refus", str(ctx.exception))
        self.assertEqual(self.ext.read_bytes(), HOST_EXTERNAL)

    def _profile(self, name, body):
        nm = self.tree.root / fz.NM_CONNECTIONS
        nm.mkdir(parents=True, exist_ok=True)
        (nm / name).write_text(body)

    def test_an_nm_profile_putting_the_interface_elsewhere_is_refused(self):
        # The 12:09 cause was a zone file against NM's connection.zone.
        self.ext.write_bytes(HOST_EXTERNAL)
        for name, body in (
                ("guest.nmconnection", "[connection]\nid=guest\ntype=vpn\nzone=internal\n\n"
                                       "[vpn]\ndev=nvguest0\nservice-type=org.freedesktop.NetworkManager.openvpn\n"),
                ("tun.nmconnection", "[connection]\nid=tun\ninterface-name=nvguest0\nzone=home\n")):
            self._profile(name, body)
            with self.assertRaises(fz.ZoneFileError, msg=name) as ctx:
                self.tree.bind()
            self.assertIn(name, str(ctx.exception))
            self.assertEqual(self.ext.read_bytes(), HOST_EXTERNAL)
            (self.tree.root / fz.NM_CONNECTIONS / name).unlink()

    def test_nm_profiles_that_agree_or_concern_others_are_accepted(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        self._profile("guest.nmconnection", "[connection]\nid=guest\ntype=vpn\nzone=external\n\n"
                                            "[vpn]\ndev=nvguest0\n")
        self._profile("lan.nmconnection", "[connection]\nid=lan\ninterface-name=enp15s0\nzone=home\n")
        self._profile("notes.txt", "[connection]\ninterface-name=nvguest0\nzone=home\n")
        self.assertTrue(self.tree.bind())

    def _default_zone(self, text):
        (self.tree.root / "etc/firewalld/firewalld.conf").write_text(text)

    def test_a_profile_without_zone_is_in_the_default_zone(self):
        # No zone= in the profile means firewalld's DefaultZone.
        self._profile("nozone.nmconnection", "[connection]\nid=x\ninterface-name=nvguest0\n")
        for conf, refused in ((None, True),  # firewalld's own default: public
                              ("# DefaultZone=external\nDefaultZone=internal\n", True),
                              ("DefaultZone = external\n", False)):
            self.ext.write_bytes(HOST_EXTERNAL)
            if conf is not None:
                self._default_zone(conf)
            if refused:
                with self.assertRaises(fz.ZoneFileError, msg=conf) as ctx:
                    self.tree.bind()
                self.assertIn("nozone.nmconnection", str(ctx.exception))
                self.assertEqual(self.ext.read_bytes(), HOST_EXTERNAL)
            else:
                self.assertTrue(self.tree.bind(), conf)

    def test_a_malformed_keyfile_never_leaks_its_content(self):
        secret = "S3CRET-MARKER-psk"
        bodies = (f"psk={secret}\n[connection]\ninterface-name=nvguest0\n",
                  f"[connection]\ninterface-name=nvguest0\n[wifi-security]\n{secret}\n",
                  f"[wifi-security]\npsk={secret}\n[connection\ninterface-name=nvguest0\n",
                  f"[wifi-security]\npsk={secret}\n".encode() + b"\xff\xfe\n")
        for body in bodies:
            self.ext.write_bytes(HOST_EXTERNAL)
            nm = self.tree.root / fz.NM_CONNECTIONS
            nm.mkdir(parents=True, exist_ok=True)
            path = nm / "wifi.nmconnection"
            path.write_bytes(body if isinstance(body, bytes) else body.encode())
            with self.assertRaises(fz.ZoneFileError, msg=body) as ctx:
                self.tree.bind()
            shown = "".join(traceback.format_exception(ctx.exception))
            self.assertNotIn(secret, shown, body)
            self.assertNotIn("psk", shown, body)
            self.assertIn("wifi.nmconnection", str(ctx.exception))
            env = {k: v for k, v in os.environ.items() if k != "PATH"}
            env["PATH"] = ""
            cli = subprocess.run([sys.executable, "-I", str(STEP), "--root", str(self.tree.root),
                                  "external", "nvguest0"], env=env, capture_output=True, text=True)
            self.assertEqual(cli.returncode, 1)
            self.assertNotIn(secret, cli.stdout + cli.stderr, body)
            self.assertIn("wifi.nmconnection", cli.stderr)
            self.assertEqual(self.ext.read_bytes(), HOST_EXTERNAL)

    def test_the_step_logs_nothing(self):
        source = STEP.read_text()
        self.assertNotIn("import logging", source)

    def test_a_utf8_bom_is_accepted_and_kept(self):
        bom = b"\xef\xbb\xbf"
        self._profile("guest.nmconnection", "﻿[connection]\nid=guest\nzone=external\n\n"
                                            "[vpn]\ndev=nvguest0\n")
        (self.tree.etc / "work.xml").write_bytes(bom + b"<zone>\n  <short>w</short>\n</zone>\n")
        self.ext.write_bytes(bom + HOST_EXTERNAL)
        self.assertTrue(self.tree.bind())
        self.assertEqual(self.ext.read_bytes(),
                         bom + HOST_EXTERNAL.replace(b"</zone>\n", LINE + b"</zone>\n"))
        self._profile("tun.nmconnection", "﻿[connection]\ninterface-name=nvguest0\nzone=home\n")
        with self.assertRaises(fz.ZoneFileError):
            self.tree.bind()

    def test_the_directory_is_synced_after_the_rename(self):
        self.ext.write_bytes(HOST_EXTERNAL)
        synced, real = [], os.fsync

        def record(fd):
            synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            real(fd)
        with mock.patch.object(fz.os, "fsync", record):
            self.tree.bind()
        self.assertEqual(synced, [False, True])

    def test_unparsable_or_foreign_xml_is_refused_untouched(self):
        for text in (b"<zone>\n  <short>x</short>\n", b"<service>\n</service>\n", b"<zone/>\n",
                     b"<zone><short>x</short></zone>\n"):
            self.ext.write_bytes(text)
            with self.assertRaises(fz.ZoneFileError, msg=text):
                self.tree.bind()
            self.assertEqual(self.ext.read_bytes(), text)

    def test_indentation_follows_the_file(self):
        text = b'<?xml version="1.0" encoding="utf-8"?>\n<zone>\n\t<short>x</short>\n</zone>\n'
        self.ext.write_bytes(text)
        self.tree.bind()
        self.assertEqual(self.ext.read_bytes(),
                         text.replace(b"</zone>", b'\t<interface name="nvguest0"/>\n</zone>'))

    def test_a_crlf_file_keeps_its_line_endings(self):
        text = HOST_EXTERNAL.replace(b"\n", b"\r\n")
        self.ext.write_bytes(text)
        self.tree.bind()
        self.assertEqual(self.ext.read_bytes(),
                         text.replace(b"</zone>\r\n", LINE.replace(b"\n", b"\r\n") + b"</zone>\r\n"))


class NoRuntime(unittest.TestCase):
    def test_the_step_can_run_no_command(self):
        # Code only, docstrings aside: no process module imported, no tool named.
        tree = ast.parse(STEP.read_text())
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names} | {node.module for node in ast.walk(tree)
                                                if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {"subprocess", "pty", "multiprocessing", "dbus", "firewall"}, imported)
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertFalse(attrs & {"system", "popen", "execv", "execvp", "spawnv", "startfile"}, attrs)
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        strings = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                           and isinstance(n.value, str) and id(n) not in docstrings)
        for tool in ("firewall-cmd", "firewall-offline-cmd", "nmcli", "systemctl", "--reload"):
            self.assertNotIn(tool, strings, tool)

    def test_command_line_binds_under_the_given_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(tmp)
            (tree.etc / "external.xml").write_bytes(HOST_EXTERNAL)
            env = {k: v for k, v in os.environ.items() if k != "PATH"}
            env["PATH"] = ""  # no tool reachable: the script must need none
            run = [sys.executable, "-I", str(STEP), "--root", tmp, "external", "nvguest0"]
            first = subprocess.run(run, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(run, env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((tree.etc / "external.xml").read_bytes().count(b'name="nvguest0"'), 1)
            self.assertIn("added", first.stdout)
            self.assertIn("already", second.stdout)


if __name__ == "__main__":
    unittest.main()
