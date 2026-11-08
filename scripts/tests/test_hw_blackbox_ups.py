#!/usr/bin/env python3
"""Tests for scripts/hw_blackbox_ups.py. Run: python3 scripts/tests/test_hw_blackbox_ups.py"""
import pathlib
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hw_blackbox_ups as ups  # noqa: E402


class FakeUpsd:
    """Answers like PyNUTClient: bytes keys and values."""

    def __init__(self):
        self.vars = {b"ups.status": b"OL", b"input.voltage": b"239.0", b"ups.load": b"15",
                     b"battery.charge": b"100", b"ups.model": b"Back-UPS"}
        self.down = False
        self.lock = threading.Lock()

    def client(self):
        if self.down:
            raise ConnectionRefusedError("upsd down")
        return self

    def GetUPSList(self):
        return {b"myups": b"APC UPS"}

    def GetUPSVars(self, name):
        assert name == "myups"
        with self.lock:
            return dict(self.vars)


def wait_until(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Poller(unittest.TestCase):
    def make(self, upsd, period=0.02):
        self.log = []
        return ups.UpsPoller(upsd.client, name="", period=period, warn=self.log.append)

    def test_reads_the_four_columns_from_the_first_ups(self):
        p = self.make(FakeUpsd())
        self.assertTrue(p.first.wait(2))
        self.assertEqual([p.value(c) for c, _ in ups.FIELDS], ["OL", "239.0", "15", "100"])

    def test_status_change_is_written_to_the_journal(self):
        upsd = FakeUpsd()
        p = self.make(upsd)
        p.first.wait(2)
        with upsd.lock:
            upsd.vars[b"ups.status"], upsd.vars[b"input.voltage"] = b"OB DISCHRG", b"0.0"
        self.assertTrue(wait_until(lambda: any("OL -> OB DISCHRG" in m for m in self.log)), self.log)
        self.assertIn("input 0.0 V", next(m for m in self.log if "->" in m))

    def test_unreachable_upsd_warns_once_then_blanks(self):
        upsd = FakeUpsd()
        p = self.make(upsd)
        p.first.wait(2)
        upsd.down = True
        self.assertTrue(wait_until(lambda: p.value("ups_status") == ""), "stale value kept")
        time.sleep(0.1)
        self.assertEqual(sum("unreadable" in m for m in self.log), 1, self.log)
        upsd.down = False
        self.assertTrue(wait_until(lambda: p.value("ups_status") == "OL"))
        self.assertTrue(any("readable again" in m for m in self.log))

    def test_no_value_before_the_first_poll(self):
        p = ups.UpsPoller(lambda: (_ for _ in ()).throw(OSError("no upsd")), period=60,
                          warn=lambda m: None)
        self.assertEqual(p.value("ups_input_v"), "")

    def test_switch_off_gives_no_columns(self):
        ups.ENABLED, saved = False, ups.ENABLED
        try:
            self.assertEqual(ups.ups_sensors(), ([], None))
        finally:
            ups.ENABLED = saved


if __name__ == "__main__":
    unittest.main()
