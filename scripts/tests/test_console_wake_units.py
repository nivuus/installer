#!/usr/bin/env python3
"""The wake units must agree with the gate they trigger.

A unit whose ExecStart names a different port than its filename, or that
lost its no-start-limit drop-in, fails in a way nothing reports: systemd
disables the socket after five starts and wake-on-demand simply stops.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UNITS = os.path.join(ROOT, "console", "host", "systemd")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


for port in ("47984", "47989"):
    sock = os.path.join(UNITS, f"vm-trigger-{port}.socket")
    svc = os.path.join(UNITS, f"vm-trigger-{port}.service")
    check(f"vm-trigger-{port}.socket exists", os.path.isfile(sock))
    check(f"vm-trigger-{port}.service exists", os.path.isfile(svc))
    if not (os.path.isfile(sock) and os.path.isfile(svc)):
        continue
    stext, vtext = open(sock).read(), open(svc).read()

    check(f"{port}: listens on every interface",
          f"ListenStream=0.0.0.0:{port}" in stext)
    # Accept=false: ONE service instance handles the listening socket and
    # reads the first bytes itself. Accept=true would spawn a per-connection
    # instance and the gate could not refuse before the VM starts.
    check(f"{port}: Accept=false", "Accept=false" in stext)
    check(f"{port}: enabled into sockets.target",
          "WantedBy=sockets.target" in stext)

    # The port in ExecStart is the gate's only argument; a mismatch with the
    # filename makes the 47984 probe log claim to be the 47989 wake path.
    check(f"{port}: ExecStart carries this very port",
          f"ExecStart=/usr/local/sbin/vm-wake-gate.py {port}" in vtext)
    check(f"{port}: ordered after its socket",
          f"After=vm-trigger-{port}.socket" in vtext)
    check(f"{port}: oneshot", "Type=oneshot" in vtext)
    # The gate waits on the VM's IP for up to 180 s; a shorter deadline kills
    # a wake that was working.
    check(f"{port}: start deadline leaves room for a VM boot",
          "TimeoutStartSec=300" in vtext)

dropin = os.path.join(UNITS, "vm-trigger-no-start-limit.conf")
check("the no-start-limit drop-in exists", os.path.isfile(dropin))
if os.path.isfile(dropin):
    check("the drop-in disables the start limit",
          "StartLimitIntervalSec=0" in open(dropin).read())

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the wake units agree with the gate and cannot hit the start limit")
