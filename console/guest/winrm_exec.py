#!/usr/bin/env python3
# policy: allow-fr-file - ruled 2026-09-05, reason below.
# This marker is the escape hatch the socle PROVIDES, and it requires a
# written reason: it is a named, dated exception, not the control being
# lowered. The real translation is tracked in docs/console-dettes.md
# under CI-3.
# 1 line, and it is a FALSE POSITIVE of the engine: the line is English and
# holds French only inside the quoted example string ("; Ecrit par
# << retro >>"), which is the DATA this file describes. The per-line
# 'policy: allow-fr' marker has no effect inside a string, hence the
# file-level exemption.

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

FAILURES OF THE CLIENT ITSELF are told apart, by a fixed English stderr
prefix and an exit code, so a consumer never reads a wrong password as a
network problem:

  error: cannot reach guest ...         exit 69  connection refused / timeout
  error: guest rejected the credentials exit 77  authentication (HTTP 401)
  error: WinRM protocol error ...       exit 76  any other WinRM/WS-Man error
  error: password file not found ...    exit 1   nothing was attempted

The remote command's own exit status is returned unchanged and may take any
value, so the stderr PREFIX is what identifies a client failure; the remote
command's stderr is always printed behind "[stderr]".

Usage: winrm_exec.py {cmd|ps} <command...>
Env:   GUEST_IP (default 192.168.3.2), GUEST_USER (default Administrator),
       GUEST_PASS_FILE (default: the file activate's `secrets` step writes,
       console/guest_secrets.py's ADMIN_PASSWORD_FILE)
"""
import os
import sys

import requests
import winrm
from winrm.exceptions import (
    AuthenticationError,
    WinRMError,
    WinRMOperationTimeoutError,
    WinRMTransportError,
)

# console/, for guest_secrets - the single source of the password path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guest_secrets import ADMIN_PASSWORD_FILE  # noqa: E402

IP = os.environ.get("GUEST_IP", "192.168.3.2")
USER = os.environ.get("GUEST_USER", "Administrator")
PASS_FILE = os.environ.get("GUEST_PASS_FILE", ADMIN_PASSWORD_FILE)

# See the docstring. sysexits(3) values: none of them is a code this tool
# used before, and EX_UNAVAILABLE / EX_PROTOCOL / EX_NOPERM say what they mean.
UNREACHABLE_PREFIX = "error: cannot reach guest"
AUTH_PREFIX = "error: guest rejected the credentials"
PROTOCOL_PREFIX = "error: WinRM protocol error"
EXIT_UNREACHABLE = 69
EXIT_PROTOCOL = 76
EXIT_AUTH = 77


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
    # Order matters: AuthenticationError is a WinRMError, and requests'
    # ConnectTimeout is both a ConnectionError and a Timeout. Anything else is
    # a bug of this tool and is left to raise, with its traceback.
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f"{UNREACHABLE_PREFIX} at {IP}:5985: {e}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except AuthenticationError as e:
        print(f"{AUTH_PREFIX} at {IP}:5985 for user {USER} "
              f"(password file {PASS_FILE}): {e}", file=sys.stderr)
        return EXIT_AUTH
    except (WinRMTransportError, WinRMOperationTimeoutError, WinRMError,
            requests.exceptions.RequestException) as e:
        print(f"{PROTOCOL_PREFIX} from guest at {IP}:5985: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_PROTOCOL
    out = result.std_out.decode("utf-8", "replace").strip()
    err = result.std_err.decode("utf-8", "replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]", err, file=sys.stderr)
    return result.status_code


if __name__ == "__main__":
    raise SystemExit(main())
