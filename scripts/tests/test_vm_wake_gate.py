#!/usr/bin/env python3
"""Tests for the VM wake gate payload classifier.

Payloads below are REAL captures from journalctl (vm-wake-gate-*), including
the false positives that woke the VM without any Moonlight client involved.
Run: python3 scripts/tests/test_vm_wake_gate.py
"""
import importlib.util
import pathlib
import sys

GATE = pathlib.Path(__file__).resolve().parents[1] / "vm-wake-gate.py"

spec = importlib.util.spec_from_file_location("vm_wake_gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

# Real internet-scanner TLS ClientHellos that wrongly woke the VM (2026-07-22/24).
SCANNER_TLS = [
    bytes.fromhex("160301010a010001060303892eecf591f6842403"),  # Driftnet Ltd
    bytes.fromhex("1603010075010000710303fcf0cdcbc924b4fe1a"),  # Linode
    bytes.fromhex("16030301a4010001a00303f67e471a39ab363cc0"),  # Datacamp
    bytes.fromhex("16030100ee010000ea03035190253f92f3e656ff"),
]

# Real Moonlight probes that must keep waking the VM (LAN clients, 2026-07-23).
MOONLIGHT_HTTP = [
    b"GET /serverinfo?uniqueid=0123456789ABCDEF&uuid=6865eb8c-eceb HTTP/1.1\r\n",
    b"GET /serverinfo?uniqueid=0123456789ABCDEF&uuid=e3519fa2-d491 HTTP/1.1\r\n",
]

# Real rejected probes on 47989 (RDP-style scan) + generic HTTP crawlers.
NOISE_HTTP = [
    bytes.fromhex("0300002f2ae00000000000436f6f6b69653a206d"),
    b"GET / HTTP/1.1\r\nHost: 90.87.35.18\r\n\r\n",
    b"HEAD /serverinfo HTTP/1.0\r\n",
    b"",
]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got}, want {want}")


for payload in SCANNER_TLS:
    check(f"47984 scanner TLS {payload[:4].hex()}", gate.is_legit(payload, "47984"), False)

for payload in MOONLIGHT_HTTP:
    check(f"47989 moonlight {payload[:20]!r}", gate.is_legit(payload, "47989"), True)

for payload in NOISE_HTTP:
    check(f"47989 noise {payload[:20]!r}", gate.is_legit(payload, "47989"), False)

# A Moonlight-looking probe on 47984 must not wake either: TLS carries no
# distinguishable Moonlight signature, so 47984 is out of the wake path.
check("47984 never wakes", gate.is_legit(MOONLIGHT_HTTP[0], "47984"), False)
check("unknown port", gate.is_legit(MOONLIGHT_HTTP[0], "1234"), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all wake-gate classification tests passed")
