#!/usr/bin/env python3
"""The wake units must agree with the gate they trigger.

A unit whose ExecStart names a different port than its filename, or that
lost its no-start-limit drop-in, fails in a way nothing reports: systemd
disables the socket after five starts and wake-on-demand simply stops.
"""
import configparser
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UNITS = os.path.join(ROOT, "console", "host", "systemd")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def load_unit(path):
    """systemd units are INI. Parsing them - rather than searching the raw
    text - is what makes an assertion about a DIRECTIVE rather than about a
    string that may well be commented out.

    strict=False: systemd tolerates a repeated key (later wins), configparser
    raises on it by default.
    optionxform=str: configparser lowercases keys, and systemd directives are
    case-sensitive - ExecStart would silently become execstart.
    """
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def get_safely(parser, section, key, label, expected=None):
    """Read a directive safely, treating absence as a named failure."""
    try:
        value = parser[section][key]
        if expected is not None:
            check(label, value == expected)
        return value
    except KeyError:
        check(f"{label} (directive exists)", False)
        return None


for port in ("47984", "47989"):
    sock = os.path.join(UNITS, f"vm-trigger-{port}.socket")
    svc = os.path.join(UNITS, f"vm-trigger-{port}.service")
    check(f"vm-trigger-{port}.socket exists", os.path.isfile(sock))
    check(f"vm-trigger-{port}.service exists", os.path.isfile(svc))
    if not (os.path.isfile(sock) and os.path.isfile(svc)):
        continue

    sock_unit = load_unit(sock)
    svc_unit = load_unit(svc)

    # Socket directives
    get_safely(sock_unit, "Unit", "Description",
               f"{port}: socket has description")
    get_safely(sock_unit, "Socket", "ListenStream",
               f"{port}: listens on every interface", f"0.0.0.0:{port}")
    get_safely(sock_unit, "Socket", "Accept",
               f"{port}: Accept=false", "false")
    get_safely(sock_unit, "Socket", "TriggerLimitIntervalSec",
               f"{port}: trigger limit interval", "2")
    get_safely(sock_unit, "Socket", "TriggerLimitBurst",
               f"{port}: trigger limit burst", "200")
    get_safely(sock_unit, "Install", "WantedBy",
               f"{port}: enabled into sockets.target", "sockets.target")

    # Service directives
    get_safely(svc_unit, "Unit", "Requires",
               f"{port}: requires its socket", f"vm-trigger-{port}.socket")
    get_safely(svc_unit, "Unit", "After",
               f"{port}: ordered after its socket", f"vm-trigger-{port}.socket")
    # The port in ExecStart is the gate's only argument; a mismatch with the
    # filename makes the 47984 probe log claim to be the 47989 wake path.
    get_safely(svc_unit, "Service", "ExecStart",
               f"{port}: ExecStart carries this very port",
               f"/usr/local/sbin/vm-wake-gate.py {port}")
    get_safely(svc_unit, "Service", "Type",
               f"{port}: oneshot", "oneshot")
    # The gate waits on the VM's IP for up to 180 s; a shorter deadline kills
    # a wake that was working.
    get_safely(svc_unit, "Service", "TimeoutStartSec",
               f"{port}: start deadline leaves room for a VM boot", "300")

dropin = os.path.join(UNITS, "vm-trigger-no-start-limit.conf")
check("the no-start-limit drop-in exists", os.path.isfile(dropin))
if os.path.isfile(dropin):
    dropin_unit = load_unit(dropin)
    get_safely(dropin_unit, "Unit", "StartLimitIntervalSec",
               "the drop-in disables the start limit", "0")

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the wake units agree with the gate and cannot hit the start limit")
