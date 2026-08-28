#!/usr/bin/env python3
"""Activate phase for the console package: arm what install only placed.

Enablement is a SYMLINK, never `systemctl enable`. systemctl fails
silently in constrained environments - a query subcommand simply prints
nothing - so an enable that "returned" tells you nothing. A symlink either
exists or raises. This is also exactly what systemctl does for a unit
carrying WantedBy=sockets.target.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES, which systemd ignores with "is not a symlink, ignoring".
Every link here is verified to point at an existing unit before it is
created, so a unit that looks enabled always is.

The links alone only make the NEXT boot correct. The unit that runs this
phase, nivuus-package-activate@.service, is WantedBy=multi-user.target, so
it runs AFTER sockets.target and timers.target have already been reached:
without a daemon-reload and an explicit start, the wake sockets do not
listen and the idle timer does not tick until a second reboot - while the
stamp file says the package is activated. So the units are started here
too, tolerating failure, because the links guarantee the next boot anyway.

The VM itself is still built by hand (guest/build.py then domain.py, both
already inside this package); wiring that in is phase 2d.
"""
import argparse
import json
import os
import subprocess
import sys

# unit file (under /etc/systemd/system) -> the .wants directory that enables it
WANTS = {
    "vm-trigger-47984.socket": "sockets.target.wants",
    "vm-trigger-47989.socket": "sockets.target.wants",
    "vm-idle-shutdown.timer": "timers.target.wants",
}

UNIT_DIR = "etc/systemd/system"


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def arm(root: str, unit: str, wants: str) -> None:
    """Link one unit into its .wants directory. Idempotent.

    Raises FileNotFoundError if the unit is absent: a dangling link is
    worse than no link, because it reads as enabled.
    """
    unit_path = os.path.join(root, UNIT_DIR, unit)
    if not os.path.isfile(unit_path):
        raise FileNotFoundError(unit_path)

    wants_dir = os.path.join(root, UNIT_DIR, wants)
    os.makedirs(wants_dir, exist_ok=True)
    link = os.path.join(wants_dir, unit)

    # The link target is an ABSOLUTE path in the running system's namespace,
    # not in the throwaway root: systemd resolves it after the reboot, when
    # this root IS /.
    target = f"/{UNIT_DIR}/{unit}"
    if os.path.islink(link) and os.readlink(link) == target:
        return
    if os.path.lexists(link):
        os.remove(link)      # a regular file here is the bug, not a state
    os.symlink(target, link)


def start_now(units) -> list:
    """Reload systemd and start the units just armed. Returns what failed.

    Never raises and never fails the phase: systemctl is legitimately
    unusable in constrained environments (in a PID namespace it cannot even
    reach systemd's private socket, and query subcommands then print nothing
    rather than erroring), and every unit is already linked, so the next boot
    is correct with or without this.
    """
    failed = []
    commands = [["systemctl", "daemon-reload"]]
    commands += [["systemctl", "start", unit] for unit in units]
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:                      # systemctl absent
            failed.append(f"{' '.join(cmd)} : {exc}")
            continue
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            failed.append(f"{' '.join(cmd)} : {detail or proc.returncode}")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    json.load(sys.stdin)
    root = args.root.rstrip("/") or "/"

    emit({"event": "progress", "pct": 30,
          "msg": "Armement des unites de reveil et du minuteur d inactivite"})
    for unit, wants in WANTS.items():
        try:
            arm(root, unit, wants)
        except FileNotFoundError as exc:
            print(f"console activate: unite absente, rien arme : {exc}",
                  file=sys.stderr)
            return 1

    # Only ever act on the CURRENT machine's systemd. With --root pointing at
    # a target being installed (or a throwaway root in a test), reloading and
    # starting would drive the WRONG systemd - the installer's own.
    if root == "/":
        broken = start_now(list(WANTS))
        if broken:
            print("console activate: unites liees mais non demarrees ; "
                  "l armement prendra effet au prochain redemarrage",
                  file=sys.stderr)
            for item in broken:
                print(f"  - {item}", file=sys.stderr)

    emit({"event": "progress", "pct": 100,
          "msg": "console : cycle de vie arme ; l invite Windows se construit "
                 "encore a la main (guest/build.py)"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
