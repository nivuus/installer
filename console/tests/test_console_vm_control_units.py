#!/usr/bin/env python3
"""The control-channel units must agree with the handler and with each other.

A socket whose group does not match the sysusers entry, or a wake unit that
no longer matches the name the handler starts, fails in a way nothing
reports: the platform just sees 'permission denied' or a wake that never
happens.
"""
import configparser
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOST = os.path.join(ROOT, "console", "host")
UNITS = os.path.join(HOST, "systemd")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def load_unit(path):
    # strict=False: systemd tolerates a repeated key. optionxform=str:
    # configparser lowercases keys, systemd directives are case-sensitive.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def get(parser, section, key, label, expected):
    try:
        check(label, parser[section][key] == expected)
    except KeyError:
        check(f"{label} (directive exists)", False)


sock_path = os.path.join(UNITS, "nivuus-vm-control.socket")
svc_path = os.path.join(UNITS, "nivuus-vm-control@.service")
wake_path = os.path.join(UNITS, "nivuus-vm-wake.service")
sysusers_path = os.path.join(HOST, "sysusers", "nivuus-vm.conf")
handler_path = os.path.join(HOST, "vm-control.sh")

for p in (sock_path, svc_path, wake_path, sysusers_path, handler_path):
    check(f"{os.path.relpath(p, ROOT)} exists", os.path.isfile(p))

if not failures:
    sock = load_unit(sock_path)
    get(sock, "Socket", "ListenStream", "socket listens on the control path",
        "/run/nivuus/vm-control.sock")
    get(sock, "Socket", "SocketMode", "socket mode is 0660", "0660")
    get(sock, "Socket", "SocketGroup", "socket group is nivuus-vm", "nivuus-vm")
    get(sock, "Socket", "Accept", "socket spawns one service per connection", "yes")
    get(sock, "Install", "WantedBy", "socket is wanted by sockets.target", "sockets.target")

    svc = load_unit(svc_path)
    get(svc, "Service", "ExecStart", "template runs the handler",
        "/usr/local/sbin/vm-control.sh")
    get(svc, "Service", "StandardInput", "template reads the request on the socket", "socket")
    get(svc, "Service", "StandardOutput", "template writes the reply on the socket", "socket")

    wake = load_unit(wake_path)
    get(wake, "Service", "Type", "wake unit is oneshot", "oneshot")
    get(wake, "Service", "ExecStart", "wake unit runs handle-vm-start.sh",
        "/usr/local/sbin/handle-vm-start.sh")
    # systemd counts STARTS, not failures: a platform retrying while the guest
    # boots would trip the default 5-per-10s limit (the 2026-07-13 incident
    # documented in host-network-rf-wan.md).
    get(wake, "Unit", "StartLimitIntervalSec", "wake unit has no start limit", "0")

    handler = open(handler_path, encoding="utf-8").read()
    match = re.search(r'^WAKE_UNIT="([^"]+)"', handler, re.MULTILINE)
    check("handler names a wake unit", match is not None)
    if match:
        check("handler's WAKE_UNIT is the unit file that ships",
              match.group(1) == os.path.basename(wake_path))
    check("handler is executable", os.access(handler_path, os.X_OK))

    sysusers = open(sysusers_path, encoding="utf-8").read().split("\n")
    check("sysusers declares the nivuus-vm group",
          any(re.fullmatch(r"g\s+nivuus-vm\s+-", line.strip()) for line in sysusers))

if failures:
    print("FAIL:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ok: control-channel units agree")
