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

The VM itself is still built by hand (windows-guest/build.py then
domain.py); wiring that in is phase 2c.
"""
import argparse
import json
import os
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

    emit({"event": "progress", "pct": 100,
          "msg": "console : cycle de vie arme ; l invite Windows se construit "
                 "encore a la main (windows-guest/build.py)"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
