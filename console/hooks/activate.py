#!/usr/bin/env python3
"""Activate phase for the console package: arm what install only placed,
then build and start the Windows guest.

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

ARMING COMES FIRST, UNCONDITIONALLY. Everything below it drives
guest_steps.plan_steps() to build and start the Windows guest, and that can
fail in ways arming never does (a missing medium, a hook refusing the VM
start). An operator who fixes the medium by hand and reboots must not also
have to re-arm the wake sockets - so arming happens, and is verified to have
happened, before a single guest-build step runs.

CLASSIFYING THE FAILURE IS THE POINT. A VM start triggers libvirt's GPU
hooks, which detach the card from the host and stop ollama,
nvidia-persistenced and the Tdarr nodes. On a freshly installed machine
those hooks have never run. A hook refusing the start is DESIGNED
behaviour, not a build failure - reporting it as one would send the
operator hunting through build output for a problem that actually lives in
the GPU handover. Three classes, one line each (see classify()):
  1. refus motive   - guest_steps.py itself refused an input (empty secret,
                       missing medium, disk too small).
  2. hook refusal    - everything was built; the 'start' step failed.
  3. panne           - a step's own command exited non-zero for a reason
                       neither this hook nor guest_steps.py can name.
Only the LAST LINE this process writes to stderr ever reaches an operator
(installer/packages/runner.py keeps just the tail line on a hook failure),
so each classified message must be self-contained - there is no second
chance to add context afterwards.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The engine launches this file by absolute path (cwd=console/), so Python
# only puts this script's OWN directory (console/hooks/) on sys.path - not
# HERE (console/), where guest_steps.py lives. Same reason install.py needs
# this before `from retro import ...`.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import guest_steps  # noqa: E402

# unit file (under /etc/systemd/system) -> the .wants directory that enables it
WANTS = {
    "vm-trigger-47984.socket": "sockets.target.wants",
    "vm-trigger-47989.socket": "sockets.target.wants",
    "vm-idle-shutdown.timer": "timers.target.wants",
}

UNIT_DIR = "etc/systemd/system"

# console/wizard.yaml's own default for the 'guest_workdir' answer. Copied,
# not imported: the wizard manifest is data read by the packages engine, not
# a module this hook can import, and this is the one place that needs the
# literal. An operator who never touches the field still gets this path.
DEFAULT_GUEST_WORKDIR = "/var/lib/nivuus/guest"


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


class StepCommandFailed(guest_steps.GuestBuildError):
    """A step's own command exited non-zero.

    A SEPARATE exception type from guest_steps.GuestBuildError, never a
    message-text sniff: classify() has to tell "the command failed" apart
    from "guest_steps.py itself refused before launching anything" (a bad
    disk, an empty secret, a medium that is not there), and a package's own
    refusal wording is free to change without breaking that distinction.
    """


def classifying_runner(argv: list[str]) -> None:
    """guest_steps.default_runner, tagging a command failure as such.

    Kept local to activate.py rather than a change to guest_steps.py's own
    default_runner: a human running a step by hand wants the plain message,
    not this hook's own bookkeeping type.
    """
    try:
        guest_steps.default_runner(argv)
    except guest_steps.GuestBuildError as exc:
        raise StepCommandFailed(str(exc)) from exc


class ActivationFailure(RuntimeError):
    """Carries the one already-classified line an operator will read."""


def classify(step_name: str, exc: Exception) -> str:
    """One line, already sorted into the class an operator needs to read.

    See the module docstring for the three classes. The 'start' step is
    the discriminator for class 2, but the step name ALONE is not enough -
    it has to be paired with WHAT failed. libvirt's own dispatcher does
    propagate a prepare hook's non-zero exit as the start failure (this is
    established and tested elsewhere in this repository), and
    classifying_runner tags exactly that case as StepCommandFailed: the
    command ran, start to finish, and came back non-zero. That is real
    evidence of a hook refusal.

    A FileNotFoundError/PermissionError (virsh itself missing or
    unusable) is NOT that evidence - subprocess.run() raises those before
    the command ever runs, so nothing about a hook can be inferred from
    them. Blaming hooks there would send an operator looking at the GPU
    handover for a problem that is really "virsh is not on PATH". Both
    keep the raw exception at the end of the message - that is what lets a
    human (or the next debugging session) tell the two apart regardless of
    which branch actually fires.
    """
    if step_name == "start" and isinstance(exc, StepCommandFailed):
        return (
            "console activate: le demarrage de la VM Windows a echoue a "
            "l etape 'start' ; l ISO et la definition du domaine sont deja "
            "en place, la cause est a chercher du cote des hooks libvirt "
            "qui gerent la bascule GPU/hote (ollama, nvidia-persistenced, "
            f"Tdarr) - {exc}")
    if isinstance(exc, guest_steps.GuestBuildError) \
            and not isinstance(exc, StepCommandFailed):
        # guest_steps.py refused this input outright (media_identity() can
        # do this again from inside build_run(), not only at plan time) -
        # same class as a refusal caught before any step ran.
        return f"console activate: entree refusee a l etape '{step_name}' - {exc}"
    return (f"console activate: la construction de l invite a echoue a "
            f"l etape '{step_name}' - {exc}")


def run_steps(step_list, emit_fn=emit) -> None:
    """Run each step in order, skipping what already_done() says is done.

    Raises ActivationFailure with an already-classified message on the
    first failure. Stopping there is correct, not merely convenient:
    guest_steps.plan_steps() orders the five steps so each depends on the
    one before it (secrets -> payload -> build -> define -> start).
    """
    count = len(step_list) or 1
    for index, step in enumerate(step_list):
        pct = 40 + int(55 * index / count)
        if step.already_done():
            emit_fn({"event": "progress", "pct": pct,
                     "msg": f"{step.name} : deja fait, etape ignoree"})
            continue
        emit_fn({"event": "progress", "pct": pct,
                 "msg": f"{step.name} : {step.description or 'en cours'}"})
        try:
            step.run()
        except Exception as exc:  # noqa: BLE001 - classified below, never a bare traceback
            raise ActivationFailure(classify(step.name, exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    ctx = json.load(sys.stdin)
    hw = ctx.get("hw") or {}
    answers = ctx.get("answers") or {}
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

    # Arming is unconditional and already done above. Everything from here
    # builds and starts the Windows guest; its own failures must never read
    # as an arming problem (see classify() / the module docstring).
    workdir = str(answers.get("guest_workdir") or DEFAULT_GUEST_WORKDIR)
    emit({"event": "progress", "pct": 35,
          "msg": "Preparation du plan de construction de l invite"})
    try:
        step_list = guest_steps.plan_steps(answers, hw, workdir,
                                           runner=classifying_runner)
    except guest_steps.GuestBuildError as exc:
        print(f"console activate: entree refusee - {exc}", file=sys.stderr)
        return 1

    try:
        run_steps(step_list)
    except ActivationFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1

    emit({"event": "progress", "pct": 100,
          "msg": "console : cycle de vie arme, invite Windows construite et a jour"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
