"""UPS state for the hardware black box (scripts/hw-blackbox.py).

Since 2026-11-07 the host sits behind an APC Back-UPS read by NUT. Mains
quality was the one input to the platform freezes nothing could see: with
these columns, the row before a freeze says whether the mains sagged, the
UPS switched to battery, or nothing happened on that side at all.

Read through NUT's own Python client (Debian `python3-nut`, module PyNUT)
on a daemon thread every NIVUUS_BLACKBOX_UPS_PERIOD seconds; the sampler
only reads the cache, so a hung upsd cannot stall the per-second record. A
value older than three periods is written blank. Every change of ups.status
(OL -> OB, LB, ...) is also written to stderr, i.e. the journal, because a
transfer shorter than the poll period would otherwise leave no trace.

The columns exist whenever PyNUT is importable, whatever upsd's state at
start-up: the black box starts early at boot, before upsd may be up, and a
column set fixed at start must not lose the UPS for the whole session.
"""

import os
import sys
import threading
import time

PERIOD = float(os.environ.get("NIVUUS_BLACKBOX_UPS_PERIOD", "2"))
HOST = os.environ.get("NIVUUS_UPS_HOST", "127.0.0.1")
PORT = int(os.environ.get("NIVUUS_UPS_PORT", "3493"))
UPS_NAME = os.environ.get("NIVUUS_UPS", "")   # empty: the first UPS upsd lists
# 0 turns the UPS columns off: the test suites set it so they never query
# the upsd of the machine running them.
ENABLED = os.environ.get("NIVUUS_BLACKBOX_UPS", "1") != "0"

# (column, NUT variable)
FIELDS = (("ups_status", "ups.status"), ("ups_input_v", "input.voltage"),
          ("ups_load_pct", "ups.load"), ("ups_charge_pct", "battery.charge"))


def _decode(value):
    return value.decode() if isinstance(value, bytes) else str(value)


class UpsPoller:
    """One upsd query per period, shared by every UPS column."""

    def __init__(self, client_factory, name=UPS_NAME, period=PERIOD, warn=None):
        self.client_factory, self.name, self.period = client_factory, name, period
        self.warn = warn or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.values, self.at, self.status = {}, None, None
        self.first = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _poll(self):
        client = self.client_factory()
        name = self.name or _decode(next(iter(client.GetUPSList())))
        raw = {_decode(k): _decode(v) for k, v in client.GetUPSVars(name).items()}
        return {column: raw.get(var, "") for column, var in FIELDS}

    def _loop(self):
        reachable = None
        while True:
            try:
                values = self._poll()
            except Exception as exc:  # any upsd failure is reported, then retried
                if reachable is not False:
                    self.warn("blackbox: UPS unreadable through upsd: %s" % exc)
                reachable = False
            else:
                if reachable is False:
                    self.warn("blackbox: UPS readable again")
                reachable = True
                self.values, self.at = values, time.monotonic()
                status = values["ups_status"]
                if self.status is not None and status != self.status:
                    self.warn("blackbox: UPS status %s -> %s (input %s V)"
                              % (self.status, status, values["ups_input_v"]))
                self.status = status
            self.first.set()
            time.sleep(self.period)

    def value(self, column):
        if self.at is None or time.monotonic() - self.at > 3 * self.period:
            return ""
        return self.values.get(column, "")


def ups_sensors():
    """([(column, reader)], poller); ([], None) when PyNUT is missing."""
    if not ENABLED:
        return [], None
    try:
        import PyNUT
    except ImportError:
        print("blackbox: PyNUT (python3-nut) missing, no UPS columns", file=sys.stderr, flush=True)
        return [], None
    poller = UpsPoller(lambda: PyNUT.PyNUTClient(host=HOST, port=PORT, timeout=3))
    return [(column, lambda c=column: poller.value(c)) for column, _ in FIELDS], poller
