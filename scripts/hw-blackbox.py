#!/usr/bin/env python3
"""Nivuus hardware black box — durable per-second recorder of the motherboard rails.

Why this exists (2026-08-16): the host has died four times with no trace at all
(04/08 19:30, 05/08 03:50, 07/08 18:23, plus a boot that never completed on
07/08). The iTCO watchdog is armed at 30 s and petted by PID 1, yet it never
reset the board — so the *platform* stops, not the kernel. Nothing above the
firmware can log that, and rsyslog buffers, so the last seconds are always lost.

This records the analogue state the freeze is suspected to come from (PSU rails,
package power, temperatures, fans) and fsyncs every sample, so the last line on
disk is the last moment the machine was alive.

Deliberately mapping-agnostic: nct6798 exposes in0..in14 as raw millivolts with
no labels, and the board-specific multipliers (+12V is probably in1 x12, +5V
probably in4 x5) are a guess. Rather than bake that guess in, every rail is
recorded raw and alerting is done on *drift from each rail's own baseline* —
which needs no mapping to be correct.

  run    — sample forever (systemd service)
  once   — print one sample and exit (testing)

Fail-open by design: a missing sensor yields an empty field, never an exception.
"""

import json
import os
import sys
import time

HWMON = "/sys/class/hwmon"
CHIP = os.environ.get("NIVUUS_HWMON_NAME", "nct6798")
NVME_CHIP = os.environ.get("NIVUUS_NVME_HWMON_NAME", "nvme")
EDAC = os.environ.get("NIVUUS_EDAC", "/sys/devices/system/edac/mc")
INTERRUPTS = os.environ.get("NIVUUS_INTERRUPTS", "/proc/interrupts")
RAPL = os.environ.get("NIVUUS_RAPL", "/sys/class/powercap/intel-rapl:0/energy_uj")
OUT = os.environ.get("NIVUUS_BLACKBOX", "/var/log/nivuus-blackbox.csv")
STATE = os.environ.get("NIVUUS_BLACKBOX_STATE", "/var/lib/nivuus/blackbox-baseline.json")
INTERVAL = float(os.environ.get("NIVUUS_BLACKBOX_INTERVAL", "1"))
MAX_BYTES = int(os.environ.get("NIVUUS_BLACKBOX_MAX", str(8 * 1024 * 1024)))
KEEP = int(os.environ.get("NIVUUS_BLACKBOX_KEEP", "3"))
# A rail wandering this far from its own long-run baseline is worth a journal
# entry. 3 % sits inside the ATX +/-5 % envelope, so it fires while the machine
# is still nominally in spec — which is the whole point of an early indicator.
DRIFT = float(os.environ.get("NIVUUS_BLACKBOX_DRIFT", "3.0"))
# An hour of samples, so a rail's *normal* excursions under load are inside the
# learned range before anything is judged against it. Learning over five minutes
# of idle would bake in an idle-only range and cry wolf on the first real load.
BASELINE_SAMPLES = int(os.environ.get("NIVUUS_BLACKBOX_BASELINE", "3600"))

# Only the temperatures worth the bytes. temp8 is the package Tj the CPU
# actually throttles on; the coretemp DTS sensors on this box under-read by
# ~25 C and must never be used for thermal decisions (see CLAUDE.md).
TEMPS = {"temp1_input": "systin", "temp8_input": "peci", "temp11_input": "pch"}
FANS = {"fan1_input": "fan1", "fan2_input": "fan2"}

# Machine checks and thermal events, summed across CPUs. MCE counts hardware
# faults the CPU reported (uncorrectable memory, cache, bus); THR/TRM count
# thermal throttle interrupts. Cheap to read and the only CPU-level fault
# visibility this box has.
IRQ_COUNTERS = ("MCE", "MCP", "THR", "TRM")


def irq_totals():
    """Sum each counter in IRQ_COUNTERS across all CPUs."""
    totals = dict.fromkeys(IRQ_COUNTERS, None)
    try:
        with open(INTERRUPTS) as fh:
            for line in fh:
                name, _, rest = line.partition(":")
                key = name.strip()
                if key in totals:
                    totals[key] = sum(int(v) for v in rest.split() if v.isdigit())
    except OSError:
        pass
    return totals


def edac_totals():
    """Correctable/uncorrectable memory errors, when a controller exists.

    On this board (B660 + non-ECC DIMMs) igen6_edac loads but registers no
    controller, so these stay empty: memory faults are not observable here at
    all, and only a memtest run can rule RAM in or out. Read anyway so the
    columns light up by themselves if ECC hardware ever appears.
    """
    ce = ue = 0
    found = False
    try:
        for entry in sorted(os.listdir(EDAC)):
            if not entry.startswith("mc"):
                continue
            for attr, add in (("ce_count", "ce"), ("ue_count", "ue")):
                value = read_int(os.path.join(EDAC, entry, attr))
                if value is None:
                    continue
                found = True
                if add == "ce":
                    ce += value
                else:
                    ue += value
    except OSError:
        pass
    return (str(ce), str(ue)) if found else ("", "")


def warn(msg):
    print(msg, file=sys.stderr, flush=True)


def read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def find_chip(name=None):
    """Resolve the hwmon directory by chip name — indexes are not stable."""
    wanted = name or CHIP
    try:
        entries = sorted(os.listdir(HWMON))
    except OSError:
        return None
    for entry in entries:
        path = os.path.join(HWMON, entry)
        try:
            with open(os.path.join(path, "name")) as fh:
                if fh.read().strip() == wanted:
                    return path
        except OSError:
            continue
    return None


def rail_names(chip):
    if not chip:
        return []
    names = [f[:-6] for f in os.listdir(chip) if f.endswith("_input") and f.startswith("in")]
    return sorted(names, key=lambda n: int(n[2:]))


class Power:
    """Package watts from the RAPL energy counter, which wraps."""

    def __init__(self):
        self.prev, self.at = read_int(RAPL), time.monotonic()

    def watts(self):
        now, at = read_int(RAPL), time.monotonic()
        if now is None or self.prev is None or at <= self.at:
            self.prev, self.at = now, at
            return ""
        delta, span = now - self.prev, at - self.at
        self.prev, self.at = now, at
        if delta < 0:  # counter wrapped — skip this sample rather than lie
            return ""
        return "%.1f" % (delta / span / 1e6)


class Baseline:
    """Per-rail reference, learned once then kept across reboots.

    Each rail gets a median *and* the range it normally operates in, and drift
    is only counted outside that range. Without this, Vcore alone would
    dominate: it swings 0.62-0.87 V with CPU load, which is 30 %+ of its own
    median and entirely healthy, while a genuinely worrying 2 % sag on a fixed
    +12V rail would be buried under it.
    """

    def __init__(self, path):
        self.path, self.samples, self.ref = path, {}, {}
        try:
            with open(path) as fh:
                loaded = json.load(fh)
            # Reject the pre-2026-08-16 flat {rail: median} format outright: a
            # missing band would silently make every rail look rock-steady.
            if all(isinstance(v, dict) for v in loaded.values()):
                self.ref = loaded
        except (OSError, ValueError, AttributeError):
            self.ref = {}
        self.warned = set()

    def observe(self, rail, value):
        if rail in self.ref:
            return
        got = self.samples.setdefault(rail, [])
        got.append(value)
        if len(got) >= BASELINE_SAMPLES:
            got.sort()
            # p1/p99 rather than a symmetric band: a rail's normal swing is not
            # centred on its median (Vcore sits low at idle and spikes upward),
            # so anything symmetric misjudges one side. Trimming 1 % drops
            # single-sample glitches while keeping the genuine excursions.
            edge = max(1, len(got) // 100)
            self.ref[rail] = {"med": got[len(got) // 2],
                              "lo": got[edge - 1], "hi": got[-edge]}
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.ref, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            warn("blackbox: cannot persist baseline: %s" % exc)

    def check(self, rail, value):
        ref = self.ref.get(rail)
        if not ref or not ref.get("med"):
            return
        med = ref["med"]
        lo, hi = ref.get("lo", med), ref.get("hi", med)
        # Only what falls outside the rail's own observed range counts as drift.
        drift = max(lo - value, value - hi, 0.0) / med * 100.0
        if drift < DRIFT:
            self.warned.discard(rail)
        elif rail not in self.warned:
            self.warned.add(rail)
            warn("blackbox: rail %s drifted %.1f%% outside its range "
                 "(%d mV vs normal %d-%d mV)" % (rail, drift, value, lo, hi))


def rotate(path):
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    for i in range(KEEP - 1, 0, -1):
        older, newer = "%s.%d" % (path, i + 1), "%s.%d" % (path, i)
        if os.path.exists(newer):
            os.replace(newer, older)
    os.replace(path, path + ".1")


def open_log(path, header):
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    fh = open(path, "a")
    if fresh:
        fh.write(header + "\n")
    return fh


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    chip = find_chip()
    if not chip:
        warn("blackbox: hwmon chip %s not found — nothing to record" % CHIP)
        return 0

    rails = rail_names(chip)
    nvme = find_chip(NVME_CHIP)
    columns = (["ts"] + rails + list(TEMPS.values()) + list(FANS.values())
               + ["nvme_c", "watts", "load1"] + [c.lower() for c in IRQ_COUNTERS]
               + ["edac_ce", "edac_ue"])
    header = ",".join(columns)
    power, baseline = Power(), Baseline(STATE)
    seen_irq = {}

    def sample():
        row = ["%.3f" % time.time()]
        for rail in rails:
            value = read_int(os.path.join(chip, rail + "_input"))
            row.append("" if value is None else str(value))
            if value:
                baseline.observe(rail, value)
                baseline.check(rail, value)
        for attr in TEMPS:
            value = read_int(os.path.join(chip, attr))
            row.append("" if value is None else "%.1f" % (value / 1000.0))
        for attr in FANS:
            value = read_int(os.path.join(chip, attr))
            row.append("" if value is None else str(value))
        drive = read_int(os.path.join(nvme, "temp1_input")) if nvme else None
        row.append("" if drive is None else "%.1f" % (drive / 1000.0))
        row.append(power.watts())
        try:
            row.append(open("/proc/loadavg").read().split()[0])
        except OSError:
            row.append("")
        for name, total in irq_totals().items():
            row.append("" if total is None else str(total))
            # A machine check that was not there a second ago is a hardware
            # fault the CPU itself reported — always worth a journal entry.
            if total is not None and name in ("MCE", "THR"):
                before = seen_irq.get(name)
                if before is not None and total > before:
                    warn("blackbox: %s counter rose %d -> %d" % (name, before, total))
                seen_irq[name] = total
        row.extend(edac_totals())
        return ",".join(row)

    if mode == "once":
        print(header)
        time.sleep(min(INTERVAL, 1))
        print(sample())
        return 0

    fh = open_log(OUT, header)
    while True:
        fh.write(sample() + "\n")
        fh.flush()
        os.fsync(fh.fileno())  # the last line must survive an instant power cut
        if fh.tell() >= MAX_BYTES:
            fh.close()
            rotate(OUT)
            fh = open_log(OUT, header)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
