#!/usr/bin/env python3
"""Encoding of what comes back from the guest over WinRM.

MEASURED 2026-08-29, and the reason this suite exists. Reading the guest's
own DuckStation settings.ini through winrm_exec.py returned

    ; A%crit par A� retro A�. Ce fichier n'est posAc que s'il est absent

while the same file, pulled as raw bytes and decoded here, is clean UTF-8:

    ; Écrit par « retro ». Ce fichier n'est posé que s'il est absent

The file was never corrupt - the READBACK was. Two independent halves, and
only fixing both gives the line above:

1. TRANSPORT, fixed here. PowerShell encodes stdout in the guest's ANSI code
   page; winrm_exec decodes it as UTF-8. Every non-ASCII byte is mangled.
   Setting [Console]::OutputEncoding makes the guest emit UTF-8, which is
   what the decode already assumes.
2. READING, which belongs to the caller and cannot be fixed here. Windows
   PowerShell 5.1's Get-Content reads a BOM-less file as ANSI, so a UTF-8
   file is already wrong as a string BEFORE any transport. Callers pass
   -Encoding UTF8. The docstring of winrm_exec says so.

This matters beyond cosmetics: this repository writes its guest-side
comments in French, so a measurement that reads them back sees garbage and
may "fix" a file that was never broken.

Run: python3 console/tests/test_windows_guest_winrm_exec.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import winrm_exec  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_in(label, needle, haystack):
    if needle not in haystack:
        failures.append(f"{label}: {needle!r} absent de {haystack!r}")


class FakeResult:
    def __init__(self):
        self.std_out = b""
        self.std_err = b""
        self.status_code = 0


class FakeSession:
    """Records the command instead of running it. Captured by the module."""

    envoye = {}

    def __init__(self, url, auth, transport, server_cert_validation):
        FakeSession.envoye["url"] = url
        FakeSession.envoye["transport"] = transport

    def run_ps(self, command):
        FakeSession.envoye["ps"] = command
        return FakeResult()

    def run_cmd(self, command):
        FakeSession.envoye["cmd"] = command
        return FakeResult()


class FakeWinrm:
    Session = FakeSession


def lancer(argv, tmp_pass):
    """Runs main() with a fake winrm module and a password file that exists."""
    FakeSession.envoye = {}
    vrai = winrm_exec.winrm
    vrai_argv = sys.argv
    vrai_pass = winrm_exec.PASS_FILE
    winrm_exec.winrm = FakeWinrm
    winrm_exec.PASS_FILE = str(tmp_pass)
    sys.argv = argv
    try:
        winrm_exec.main()
    finally:
        winrm_exec.winrm = vrai
        sys.argv = vrai_argv
        winrm_exec.PASS_FILE = vrai_pass
    return FakeSession.envoye


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    tmp_pass = pathlib.Path(d) / "windows-admin.pass"
    tmp_pass.write_text("motdepasse\n")

    # --- Le preambule d'encodage, la moitie transport -----------------------
    envoye = lancer(["winrm_exec.py", "ps", "hostname"], tmp_pass)
    check_in(
        "la commande ps force la sortie en UTF-8",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        envoye.get("ps", ""),
    )
    check_in("la commande demandee suit le preambule", "hostname", envoye.get("ps", ""))

    # Le preambule precede la commande : pose apres, il ne servirait a rien,
    # la sortie etant deja encodee quand il s'executerait.
    ps = envoye.get("ps", "")
    if "OutputEncoding" in ps and "hostname" in ps:
        check(
            "le preambule vient AVANT la commande",
            ps.index("OutputEncoding") < ps.index("hostname"),
            True,
        )
    else:
        failures.append("le preambule vient AVANT la commande: preambule absent")

    # --- Les arguments multiples restent joints -----------------------------
    envoye = lancer(["winrm_exec.py", "ps", "Get-Item", "C:\\nivuus"], tmp_pass)
    check_in(
        "les arguments multiples sont joints",
        "Get-Item C:\\nivuus",
        envoye.get("ps", ""),
    )

    # --- cmd : chcp, parce que [Console] n'existe pas la-bas ----------------
    envoye = lancer(["winrm_exec.py", "cmd", "hostname"], tmp_pass)
    check_in("la commande cmd bascule en page de code 65001",
             "chcp 65001", envoye.get("cmd", ""))
    check_in("la commande demandee suit chcp", "hostname", envoye.get("cmd", ""))

    # --- Ce que le preambule ne doit PAS changer ----------------------------
    check("le transport reste ntlm", envoye.get("transport"), "ntlm")

    # --- La moitie LECTURE est dite au caller, pas silencieuse --------------
    doc = winrm_exec.__doc__ or ""
    check_in("le docstring avertit du piege Get-Content", "-Encoding UTF8", doc)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("OK - winrm_exec encoding tests passed")
