#!/usr/bin/env python3
"""Run one command in the Windows guest over WinRM.

/usr/local/bin/winrm speaks Basic only, which the guest does not enable:
Enable-PSRemoting offers Negotiate. pywinrm with the ntlm transport negotiates
correctly (measured 2026-08-22, Basic returned 401).

The password is read from a file, never from argv, so it cannot leak into the
process table or shell history.

ENCODING, measured 2026-08-29, and it has two halves. Reading the guest's
own settings.ini here returned "; A%crit par A. retro A." while the same
file, pulled as raw bytes, is clean UTF-8: "; Ecrit par << retro >>". The
file was never corrupt, the readback was.

  1. TRANSPORT, handled below: PowerShell encodes stdout in the guest's ANSI
     code page, and this tool decodes UTF-8. Every command therefore carries
     a preamble that makes the guest emit UTF-8 (chcp 65001 for cmd).
  2. READING, which only the CALLER can fix: Windows PowerShell 5.1 reads a
     BOM-less file as ANSI, so a UTF-8 file is already a wrong string before
     any transport. Pass -Encoding UTF8 to Get-Content. Without it the
     preamble below faithfully transports mojibake.

This is not cosmetic: this repository writes its guest-side comments in
French, so a measurement that reads them back sees garbage, and may "fix" a
file that was never broken.

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
    try:
        with open(PASS_FILE) as fh:
            password = fh.read().strip()
    except FileNotFoundError:
        print(f"error: password file not found: {PASS_FILE}", file=sys.stderr)
        return 1
    session = winrm.Session(
        f"http://{IP}:5985/wsman",
        auth=(USER, password),
        transport="ntlm",
        server_cert_validation="ignore",
    )
    command = " ".join(sys.argv[2:])
    # The preamble goes BEFORE the command: set afterwards it would encode
    # nothing, the output being already written. See the docstring.
    try:
        if sys.argv[1] == "cmd":
            result = session.run_cmd("chcp 65001 >nul & " + command)
        else:
            result = session.run_ps(
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + command
            )
    except Exception as e:
        print(f"error: cannot reach guest at {IP}:5985: {e}", file=sys.stderr)
        return 1
    out = result.std_out.decode("utf-8", "replace").strip()
    err = result.std_err.decode("utf-8", "replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]", err, file=sys.stderr)
    return result.status_code


if __name__ == "__main__":
    raise SystemExit(main())
