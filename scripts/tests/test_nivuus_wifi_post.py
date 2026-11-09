#!/usr/bin/env python3
"""Tests for scripts/nivuus_wifi_post.py. Run: python3 scripts/tests/test_nivuus_wifi_post.py

The script runs against fake hostapd_cli and iw programs and a fake /sys/class/net tree,
so these tests never touch a real radio.
"""
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nivuus_wifi_post.py"
sys.path.insert(0, str(ROOT / "scripts"))
import nivuus_wifi_post as post  # noqa: E402

RADIO_24 = textwrap.dedent("""\
    ctrl_interface=/var/run/hostapd
    interface=wlp11s0
    hw_mode=g
    ssid=home
    bridge=localBridge
    bss=wlp11s1
    ssid=guests
    bridge=publicBridge
    ctrl_interface=/var/run/hostapd
""")
RADIO_5 = RADIO_24.replace("wlp11s", "wlp10s").replace("hw_mode=g", "hw_mode=a")

FAKE_HOSTAPD_CLI = """#!/bin/sh
# hostapd_cli -i IF <command> [args]: state lives in files under $FAKE_DIR.
IF=$2; CMD=$3; shift 3
case "$CMD" in
  status) [ -e "$FAKE_DIR/$IF.enabled" ] && echo state=ENABLED || echo state=HT_SCAN ;;
  show_neighbor) [ -e "$FAKE_DIR/$IF.ctrl" ] || exit 1
                 echo "bssid-$IF ssid=$(cat "$FAKE_DIR/$IF.ssidhex") nr=nr-$IF" ;;
  set_neighbor) echo "$*" >> "$FAKE_DIR/$IF.neighbors"; echo OK ;;
esac
"""
FAKE_IW = """#!/bin/sh
# iw dev IF set txpower fixed MBM | iw dev IF info
IF=$2
if [ "$3" = set ]; then echo "$6" > "$FAKE_DIR/$IF.mbm"; exit 0; fi
[ -e "$FAKE_DIR/$IF.mbm" ] && awk '{printf "\\ttxpower %.2f dBm\\n", $1/100}' "$FAKE_DIR/$IF.mbm"
"""


class Parse(unittest.TestCase):
    def test_reads_each_bss_with_its_band_and_bridge(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as conf:
            conf.write(RADIO_24)
            conf.flush()
            bsses = post.parse_hostapd_config(conf.name)
        self.assertEqual([(b.ifname, b.ssid, b.bridge, b.band_24ghz, b.primary) for b in bsses], [
            ("wlp11s0", "home", "localBridge", True, True),
            ("wlp11s1", "guests", "publicBridge", True, False),
        ])


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.fake = base / "state"
        self.fake.mkdir()
        self.sysfs = base / "net"
        for name, body in (("hostapd_cli", FAKE_HOSTAPD_CLI), ("iw", FAKE_IW)):
            path = base / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.confs = []
        for band, text in (("24", RADIO_24), ("5", RADIO_5)):
            conf = base / f"{band}.conf"
            conf.write_text(text)
            self.confs.append(str(conf))
        for ifname, ssid in (("wlp11s0", "home"), ("wlp11s1", "guests"), ("wlp10s0", "home"), ("wlp10s1", "guests")):
            (self.sysfs / ifname / "brport").mkdir(parents=True)
            (self.sysfs / ifname / "brport" / "isolated").write_text("0")
            (self.fake / f"{ifname}.ctrl").touch()
            (self.fake / f"{ifname}.ssidhex").write_text(ssid.encode().hex())
        self.env = dict(os.environ, FAKE_DIR=str(self.fake), NIVUUS_WIFI_SYSFS_NET=str(self.sysfs),
                        NIVUUS_WIFI_HOSTAPD_CLI=str(base / "hostapd_cli"), NIVUUS_WIFI_IW=str(base / "iw"),
                        NIVUUS_WIFI_READY_TIMEOUT_S="3")

    def tearDown(self):
        self.tmp.cleanup()

    def run_post(self):
        return subprocess.run([sys.executable, str(SCRIPT), *self.confs], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def enable_radios(self):
        for ifname in ("wlp11s0", "wlp10s0"):
            (self.fake / f"{ifname}.enabled").touch()

    def test_isolates_guest_ports_caps_24ghz_and_links_the_bands(self):
        self.enable_radios()
        result = self.run_post()
        self.assertEqual(result.returncode, 0, result.stderr)
        isolated = {i: (self.sysfs / i / "brport" / "isolated").read_text() for i in ("wlp11s1", "wlp10s1", "wlp11s0")}
        self.assertEqual(isolated, {"wlp11s1": "1", "wlp10s1": "1", "wlp11s0": "0"})
        self.assertEqual((self.fake / "wlp11s0.mbm").read_text().strip(), "2000")
        self.assertFalse((self.fake / "wlp10s0.mbm").exists(), "5 GHz power must be left alone")
        self.assertIn("bssid-wlp10s1", (self.fake / "wlp11s1.neighbors").read_text())
        self.assertIn("bssid-wlp11s0", (self.fake / "wlp10s0.neighbors").read_text())
        self.assertNotIn("wlp11s1", (self.fake / "wlp10s0.neighbors").read_text(), "SSIDs must not mix")

    def test_waits_only_for_the_radios_not_for_each_bss(self):
        # Secondary BSSes report no state of their own: waiting for them used to time out.
        self.enable_radios()
        self.assertEqual(self.run_post().returncode, 0)

    def test_fails_visibly_when_a_radio_never_comes_up_but_isolates_the_other(self):
        (self.fake / "wlp11s0.enabled").touch()
        result = self.run_post()
        self.assertEqual(result.returncode, 1)
        self.assertIn("wlp10s0", result.stderr)
        self.assertEqual((self.sysfs / "wlp11s1" / "brport" / "isolated").read_text(), "1")

    def test_still_isolates_guests_when_neighbor_reports_fail(self):
        self.enable_radios()
        (self.fake / "wlp10s1.ctrl").unlink()  # a BSS without ctrl_interface
        result = self.run_post()
        self.assertEqual(result.returncode, 1)
        self.assertIn("publish_neighbors", result.stderr)
        self.assertEqual((self.sysfs / "wlp10s1" / "brport" / "isolated").read_text(), "1")


if __name__ == "__main__":
    unittest.main()
