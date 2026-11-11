"""PCIe link health of the graphics card, for the hardware black box.

Since 2026-11-11 (a freeze under load, with every rail, temperature and
memory counter flat): the RTX 4070 sits on a riser cable, and a marginal
riser is one cause that can halt a whole platform. Its usual first symptom
is corrected link errors (bad TLPs, replays) piling up long before the link
drops, then a link retraining to a lower speed or width.

So for every display device that reports AER, and for the bridge port it
hangs off, this records the kernel's AER totals (corrected, non-fatal,
fatal), and for that port the negotiated link speed and width. These are
plain sysfs reads of kernel counters and of the port's link status, which
stays readable whichever driver holds the card (nvidia or vfio-pci). A
counter that rises is also written to stderr, i.e. the journal.

Devices are found from the bus, never hard-coded: a device without
aer_dev_* files (an integrated GPU on the root complex) gets no column.
"""

import os
import sys

SEVERITIES = (("cor", "aer_dev_correctable", "TOTAL_ERR_COR"),
              ("nonfatal", "aer_dev_nonfatal", "TOTAL_ERR_NONFATAL"),
              ("fatal", "aer_dev_fatal", "TOTAL_ERR_FATAL"))


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _total(path, key):
    text = _read(path)
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            return parts[1]
    return ""


def _short(addr):
    return addr.split(":", 1)[1]


def watched_devices(pci):
    """[(address, sysfs dir, is_port)]: AER-capable display devices and their ports."""
    base = os.path.join(pci, "devices")
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for addr in entries:
        path = os.path.join(base, addr)
        klass = (_read(os.path.join(path, "class")) or "").strip()
        if not klass.startswith("0x03"):
            continue
        if not os.path.exists(os.path.join(path, "aer_dev_correctable")):
            continue
        real = os.path.realpath(path)
        port = os.path.dirname(real)
        port_addr = os.path.basename(port)
        if port_addr.count(":") == 2 and os.path.exists(os.path.join(port, "aer_dev_correctable")):
            if port_addr not in [a for a, _, _ in found]:
                found.append((port_addr, port, True))
        found.append((addr, path, False))
    return found


class PcieSensors:
    def __init__(self, pci, warn=None):
        self.warn = warn or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.devices = watched_devices(pci)
        self.seen = {}

    def columns(self):
        cols = []
        for addr, _, is_port in self.devices:
            short = _short(addr)
            cols += ["aer_%s_%s" % (sev, short) for sev, _, _ in SEVERITIES]
            if is_port:
                cols += ["link_gts_%s" % short, "link_width_%s" % short]
        return cols

    def values(self):
        row = []
        for addr, path, is_port in self.devices:
            for sev, name, key in SEVERITIES:
                value = _total(os.path.join(path, name), key)
                before = self.seen.get((addr, sev))
                if value and before and int(value) > int(before):
                    self.warn("blackbox: PCIe AER %s errors on %s rose %s -> %s"
                              % (sev, addr, before, value))
                if value:
                    self.seen[(addr, sev)] = value
                row.append(value)
            if is_port:
                speed = (_read(os.path.join(path, "current_link_speed")) or "").split()
                width = (_read(os.path.join(path, "current_link_width")) or "").strip()
                row += [speed[0] if speed else "", width]
        return row
