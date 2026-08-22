#!/usr/bin/env python3
"""Run one command in the Windows guest over WinRM.

/usr/local/bin/winrm speaks Basic only, which the guest does not enable:
Enable-PSRemoting offers Negotiate. pywinrm with the ntlm transport negotiates
correctly (measured 2026-08-22, Basic returned 401).

The password is read from a file, never from argv, so it cannot leak into the
process table or shell history.

Usage: winrm_exec.py {cmd|ps} <command...>
Env:   GUEST_IP (default 192.168.3.2), GUEST_USER (default Administrator),
       GUEST_PASS_FILE (default /root/.config/nivuus/windows-admin.pass)
"""
import os
import sys

import winrm

IP = os.environ.get("GUEST_IP", "192.168.3.2")
USER = os.environ.get("GUEST_USER", "Administrator")
PASS_FILE = os.environ.get(
    "GUEST_PASS_FILE", "/root/.config/nivuus/windows-admin.pass"
)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("cmd", "ps"):
        print(__doc__, file=sys.stderr)
        return 2
    with open(PASS_FILE) as fh:
        password = fh.read().strip()
    session = winrm.Session(
        f"http://{IP}:5985/wsman",
        auth=(USER, password),
        transport="ntlm",
        server_cert_validation="ignore",
    )
    command = " ".join(sys.argv[2:])
    result = (session.run_cmd if sys.argv[1] == "cmd" else session.run_ps)(command)
    out = result.std_out.decode("utf-8", "replace").strip()
    err = result.std_err.decode("utf-8", "replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]", err, file=sys.stderr)
    return result.status_code


if __name__ == "__main__":
    raise SystemExit(main())
