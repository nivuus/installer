#!/usr/bin/env python3
"""The idle C-state cap is derived from the detected CPU, through the real
hardware parser and the real resolve hook.

Run: python3 console/tests/test_console_idle_cap.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(REPO / "console"))

import idle_cap  # noqa: E402
from common import hardware as common_hw  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def cpuinfo(vendor, family, model, name="Test CPU"):
    return (f"processor\t: 0\nvendor_id\t: {vendor}\ncpu family\t: {family}\n"
            f"model\t\t: {model}\nmodel name\t: {name}\n\nprocessor\t: 1\n"
            f"vendor_id\t: {vendor}\ncpu family\t: {family}\nmodel\t\t: 1\n")


def topology(text):
    with tempfile.NamedTemporaryFile("w", suffix=".cpuinfo") as fh:
        fh.write(text)
        fh.flush()
        return common_hw.cpu_topology(fh.name)


# Parsing: first processor block wins, ints are ints.
adl = topology(cpuinfo("GenuineIntel", 6, 151, "12th Gen Intel(R) Core(TM) i9-12900K"))
check("vendor", adl["vendor"], "GenuineIntel")
check("family", adl["family"], 6)
check("model_id", adl["model_id"], 151)
check("model name kept", adl["model"], "12th Gen Intel(R) Core(TM) i9-12900K")
missing = common_hw.cpu_topology("/nonexistent/cpuinfo")
check("unreadable cpuinfo -> no identity", (missing["vendor"], missing["family"], missing["model_id"]),
      ("", None, None))

# The rule.
check("Alder Lake-S is capped", idle_cap.cstate_cap_params(adl), ["intel_idle.max_cstate=3"])
check("other Intel model not capped",
      idle_cap.cstate_cap_params(topology(cpuinfo("GenuineIntel", 6, 183))), [])
check("AMD with the same numbers not capped",
      idle_cap.cstate_cap_params(topology(cpuinfo("AuthenticAMD", 6, 151))), [])
check("unknown identity not capped", idle_cap.cstate_cap_params(missing), [])
check("empty snapshot not capped", idle_cap.cstate_cap_params({}), [])


def resolve_cmdline(cpu):
    """Run the real resolve hook; fake only the PCI lookups it makes."""
    ctx = {"hw": {"iommu": {"supported": True}, "memory_mib": 65536,
                  "gpus": [{"slot": "01:00.0", "discrete": True}], "cpu": cpu},
           "answers": {}}
    code = (
        "import sys, runpy; sys.argv=['resolve.py','--phase','resolve']\n"
        f"sys.path.insert(0, {str(REPO / 'console')!r})\n"
        "import hardware\n"
        "hardware.vfio_ids_for_slot = lambda slot: ['10de:2786']\n"
        "hardware.pci_address_for_device = lambda d: None\n"
        "hardware.passthrough_nvme = lambda wanted_address=None: "
        "{'id': '144d:a80a', 'address': '0000:02:00.0'}\n"
        "hardware.block_device_for_pci_address = lambda a: ''\n"
        f"runpy.run_path({str(REPO / 'console/hooks/resolve.py')!r}, run_name='__main__')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], input=json.dumps(ctx),
                          capture_output=True, text=True, timeout=30)
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    return [e for e in events if e.get("event") == "platform"], proc.stderr


for label, cpu, want in (("capped CPU", adl, True),
                         ("other CPU", topology(cpuinfo("GenuineIntel", 6, 183)), False)):
    platform, err = resolve_cmdline(cpu)
    check(f"{label}: platform event emitted ({err[-200:]})", len(platform), 1)
    if platform:
        check(f"{label}: cap in kernel-cmdline",
              "intel_idle.max_cstate=3" in platform[0]["kernel-cmdline"], want)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - idle cap tests passed")
