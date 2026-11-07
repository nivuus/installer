"""Device temperatures for the hardware black box (scripts/hw-blackbox.py).

Two kinds of sensor, read two different ways:

* fast — plain sysfs registers (the DDR5 SPD hubs). Read inline, every sample.
* slow — reads that send a command to a device and wait for its answer: an
  ath10k card asks its firmware over WMI (up to a 3 s timeout), drivetemp
  issues an ATA SCT/SMART command to the disk, the GPU is asked through
  nvidia-smi. Each is polled by its own thread and the sampler only reads the
  cached value, so a device that hangs — exactly what the black box exists to
  witness — never stalls the per-second record. A value older than three poll
  periods is written blank: a sensor that stopped answering shows as a gap.

Columns are fixed at start-up and named after the device's bus address, never
the hwmon index, so a sensor keeps its column across boots.
"""

import os
import subprocess
import threading
import time

DIMM_CHIP = os.environ.get("NIVUUS_DIMM_HWMON_NAME", "spd5118")
# Both QCA9984 radios register "ath10k_hwmon". The onboard Intel card is left
# out: its "iwlwifi_<n>" hwmon hangs off a thermal zone rather than its PCI
# device, and it reads ENODATA whenever the interface is down (unused here).
WIFI_CHIP = os.environ.get("NIVUUS_WIFI_HWMON_NAME", "ath10k_hwmon")
DRIVE_CHIP = os.environ.get("NIVUUS_DRIVE_HWMON_NAME", "drivetemp")
NVIDIA_SMI = os.environ.get("NIVUUS_NVIDIA_SMI", "nvidia-smi")
# nvidia-smi is only run while nvidia-persistenced is up. The VM start hook
# (bind-vfio-gpu.sh) stops persistenced first, then refuses the start if any
# process still holds /dev/nvidia*: gating on the socket keeps the black box
# from ever being that process. It also keeps each call at ~25 ms instead of a
# full GPU initialisation.
PERSISTENCED = os.environ.get("NIVUUS_PERSISTENCED_SOCKET",
                              "/run/nvidia-persistenced/socket")
SLOW_PERIOD = float(os.environ.get("NIVUUS_BLACKBOX_SLOW_PERIOD", "30"))


def _millidegrees(path):
    try:
        with open(path) as fh:
            return "%.1f" % (int(fh.read().strip()) / 1000.0)
    except (OSError, ValueError):
        return ""


def _hwmons(root, accept):
    """(bus address, hwmon dir) for every chip whose name `accept` takes."""
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    found = []
    for entry in entries:
        path = os.path.join(root, entry)
        try:
            with open(os.path.join(path, "name")) as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if accept(name):
            device = os.path.realpath(os.path.join(path, "device"))
            found.append((os.path.basename(device), path))
    return sorted(found)


def dimm_sensors(root):
    """(column, reader) per DDR5 module, ordered by SPD hub I2C address."""
    return [("dimm_%s_c" % addr, lambda p=os.path.join(path, "temp1_input"): _millidegrees(p))
            for addr, path in _hwmons(root, lambda n: n == DIMM_CHIP)]


def _pci_short(addr):
    """0000:0a:00.0 -> 0a:00.0, the form used in every other column."""
    return addr.split(":", 1)[1] if addr.count(":") == 2 else addr


def wifi_sensors(root):
    return [("wifi_%s_c" % _pci_short(addr),
             lambda p=os.path.join(path, "temp1_input"): _millidegrees(p))
            for addr, path in _hwmons(root, lambda n: n == WIFI_CHIP)]


def drive_sensors(root, block):
    """(column, reader) per disk drivetemp attached to, named by block device.

    drivetemp's hwmon device is the SCSI device (`0:0:0:0`); its block/ child
    gives the disk name the inflight_* columns already use.
    """
    found = []
    for scsi, path in _hwmons(root, lambda n: n == DRIVE_CHIP):
        try:
            disk = sorted(os.listdir(os.path.join(path, "device", "block")))[0]
        except (OSError, IndexError):
            disk = scsi
        found.append(("drive_%s_c" % disk,
                      lambda p=os.path.join(path, "temp1_input"): _millidegrees(p)))
    return sorted(found)


def nvidia_gpus(pci):
    """PCI addresses of NVIDIA display devices, whichever driver holds them.

    Listed from the bus rather than from nvidia-smi so the column exists even
    when the card starts out bound to vfio-pci for the VM.
    """
    base = os.path.join(pci, "devices")
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for addr in entries:
        try:
            with open(os.path.join(base, addr, "vendor")) as fh:
                vendor = fh.read().strip()
            with open(os.path.join(base, addr, "class")) as fh:
                klass = fh.read().strip()
        except OSError:
            continue
        if vendor == "0x10de" and klass.startswith("0x03"):
            found.append(addr)
    return found


class NvidiaSmi:
    """One nvidia-smi call per poll, shared by every GPU column."""

    def __init__(self):
        self.lock, self.at, self.temps = threading.Lock(), 0.0, {}

    def query(self):
        if not os.path.exists(PERSISTENCED):
            return {}
        try:
            out = subprocess.run(
                [NVIDIA_SMI, "--query-gpu=pci.bus_id,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return {}
        temps = {}
        for line in out.splitlines():
            bus, _, temp = line.partition(",")
            temp = temp.strip()
            if temp.isdigit():
                temps[bus.strip().lower()[-7:]] = "%.1f" % float(temp)
        return temps

    def reader(self, addr):
        def read():
            with self.lock:
                if time.monotonic() - self.at >= SLOW_PERIOD / 2:
                    self.temps, self.at = self.query(), time.monotonic()
                return self.temps.get(_pci_short(addr).lower(), "")
        return read


def gpu_sensors(pci):
    smi = NvidiaSmi()
    return [("gpu_%s_c" % _pci_short(a), smi.reader(a)) for a in nvidia_gpus(pci)]


class SlowSensor:
    """Polls one reader on its own daemon thread; `value()` never blocks."""

    def __init__(self, read):
        self.read, self.cached, self.at = read, "", None
        self.first = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            value = self.read()
            self.cached, self.at = value, time.monotonic()
            self.first.set()
            time.sleep(SLOW_PERIOD)

    def value(self):
        if self.at is None or time.monotonic() - self.at > 3 * SLOW_PERIOD:
            return ""
        return self.cached


def slow_sensors(hwmon, block, pci):
    """(column, SlowSensor) for every WiFi radio, SATA disk and NVIDIA GPU."""
    readers = wifi_sensors(hwmon) + drive_sensors(hwmon, block) + gpu_sensors(pci)
    return [(name, SlowSensor(read)) for name, read in readers]
