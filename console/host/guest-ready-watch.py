#!/usr/bin/env python3
"""Classify the Windows guest's provisioning state and log it to the journal.

activate.py's 'start' step launches Windows Setup and returns immediately -
it does not wait for Setup to finish, which can take the better part of an
hour. Without this watch, "installation en cours" and "a echoue" are
indistinguishable: both look, from the host, like a running domain it
cannot yet talk to - for an hour, then forever.

THE SIGNAL IS NOT INVENTED. console/guest/provision/99-marker.ps1 orders its
fourteen stages so that everything else is true before port 5985 (WinRM)
opens - "the host treats a reachable 5985 as 'the guest is provisioned'".
Building a second signal here would create two truths; this script only
reads the one that already exists.

Four states, not three, because "not started" and "installation en cours"
call for opposite operator reactions (go look at the libvirt hooks, versus
just wait) and nothing distinguished them before this script existed:

  NOT_STARTED - the domain is not 'running': it never started, or it
                stopped/crashed. Look at the libvirt hooks/journal.
  INSTALLING  - the domain is running, 5985 is still closed, and not for
                unreasonably long yet.
  FAILED      - the domain is running, 5985 is still closed, past
                INSTALL_TIMEOUT_S. The guest cannot say why - WinRM is
                exactly what is closed - so only the host's own journal
                (this line) can make the failure legible.
  READY       - 5985 answers. This overrides everything else: a reachable
                guest IS a provisioned guest, even if it took longer than
                INSTALL_TIMEOUT_S. The clock only measures a reasonable
                delay, never a hard deadline.

On READY, this script also redefines the domain WITHOUT the two install
media (the official Windows ISO and the answer ISO guest_steps.py's
'define' step attached to boot Setup) - see redefine_steady_state(). A
console that reboots still carrying either medium risks re-running Windows
Setup instead of booting the installed system. Redefining a domain that is
currently running does not disturb it: `virsh define`'s own manual page
says so plainly - "If domain is already running, the changes will take
effect on the next boot" - and this script never calls `virsh start`
itself, so the domain that keeps running is the one Setup is still inside
of, or has already finished configuring; only the NEXT start reads the new,
media-less definition.

That redefinition passes --keyed-varstore, not just --replace. Without it,
domain.py's guard_fresh_varstore() refuses ANY existing NVRAM varstore
unconditionally - and by READY, the varstore always already exists, because
our own earlier `define --windows-iso ... --unattend-iso ...` step is what
created it. --keyed-varstore asserts exactly that: this varstore is not the
stale, pre-Secure-Boot leftover the guard exists to catch, it is the one
THIS package's own pipeline created, already keyed, a short while ago. See
guard_fresh_varstore()'s docstring in domain.py for the full argument.

Consequently the timer does NOT stop on READY alone - only once the
redefinition has actually succeeded. Stopping on READY unconditionally
would mean a redefinition that fails once (a transient libvirt hiccup, disk
full, anything) never gets retried, and the domain keeps its install media
forever - the exact failure this whole mechanism exists to prevent. See
main()'s READY branch.

The IP-discovery method (virsh domifaddr, agent source then lease source)
is copied from handle-vm-start.sh, not reinvented - see that script for why
each source is tried in that order and why either can legitimately come up
empty here (no guest-agent channel yet, and this bridge is externally
managed, not a libvirt network with its own lease file).

Deployed to /usr/local/sbin/ by console/hooks/install.py, armed (as a timer
only - nivuus-guest-ready.service carries no [Install] section of its own,
matching vm-idle-shutdown.{service,timer}) by console/hooks/activate.py.
The timer self-stops once a TERMINAL state is reached: FAILED unconditionally,
READY only once the media-less redefinition has succeeded - see main().
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time

VM_NAME = "Windows"
# guest/domain.py's own BRIDGE constant. Copied, not imported: this script
# is deployed standalone under /usr/local/sbin/ - the same reason
# handle-vm-start.sh and vm-idle-shutdown.sh do not import from console/.
VM_INTERFACE = "internalBridge"
WINRM_PORT = 5985
PORT_PROBE_TIMEOUT_S = 3

# "a reasonable delay" per the task this script implements: long enough
# that a normal unattended LTSC install (including the reboots driver
# installs cause) is never mistaken for a failure, short enough that a
# genuinely stuck install is reported well before an operator would
# otherwise notice on their own.
INSTALL_TIMEOUT_S = 2 * 3600

STATE_DIR = "/var/lib/nivuus"
STATE_PATH = os.path.join(STATE_DIR, "guest-ready-state.json")

# installer/packages/discovery.py's PACKAGES_DIR default, plus this
# package's own name (console/nivuus-package.yaml). Copied, not imported,
# for the same standalone-script reason as VM_INTERFACE above:
# apply_packages() copies the whole package tree there once, at install
# time, and never removes it - so guest/domain.py is reliably at this path
# on any machine where this script itself is running.
DOMAIN_PY = "/opt/nivuus-packages/console/guest/domain.py"

NOT_STARTED = "not_started"
INSTALLING = "installing"
FAILED = "failed"
READY = "ready"
TERMINAL_STATES = frozenset({FAILED, READY})

_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def classify(*, domstate: str, port_open: bool, elapsed_s: float) -> str:
    """The four-way split. See the module docstring for what each means.

    port_open wins over everything, INCLUDING elapsed_s past the timeout:
    a reachable guest is a provisioned guest, however long it took to get
    there - the clock only distinguishes "still waiting" from "give up
    waiting and say so", it never overrides a guest that answered.
    """
    if port_open:
        return READY
    if domstate != "running":
        return NOT_STARTED
    if elapsed_s >= INSTALL_TIMEOUT_S:
        return FAILED
    return INSTALLING


def query_domstate(vm_name: str = VM_NAME, run=subprocess.run) -> str:
    """The domain's state, or "" when libvirt cannot answer.

    LC_ALL=C keeps the state string in English ('shut off', 'running');
    "" (never a real state string) is what an unreachable libvirtd and an
    absent domain both look like, and classify() treats both as
    NOT_STARTED - there is nothing here to distinguish them by, and both
    call for the same operator reaction (go look at the hooks/libvirtd).
    """
    proc = run(["virsh", "domstate", vm_name], capture_output=True,
               text=True, env=dict(os.environ, LC_ALL="C", LANG="C"))
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def find_guest_ip(vm_name: str = VM_NAME, interface: str = VM_INTERFACE,
                  run=subprocess.run) -> str | None:
    """The guest's IP, by the same method handle-vm-start.sh already uses:
    try the QEMU guest agent first, then libvirt's own lease record.

    Neither source is guaranteed to answer here - this console has no
    guest-agent channel until the agent stage of provisioning runs, and
    the bridge is externally managed (NetworkManager's dnsmasq-shared, not
    a libvirt network with its own lease file) - so a miss on both is
    expected during most of an install, not a bug. None means "cannot
    check the port yet", nothing more.
    """
    env = dict(os.environ, LC_ALL="C", LANG="C")
    for source in ("agent", "lease"):
        proc = run(["virsh", "domifaddr", vm_name, "--interface", interface,
                    "--source", source], capture_output=True, text=True,
                   env=env)
        if proc.returncode != 0:
            continue
        match = _IPV4_RE.search(proc.stdout or "")
        if match:
            return match.group(0)
    return None


def probe_port(ip: str, port: int = WINRM_PORT,
               timeout: float = PORT_PROBE_TIMEOUT_S) -> bool:
    """A plain TCP connect - WinRM's own protocol is not spoken here, only
    reachability, exactly what 99-marker.ps1 promises ("a reachable
    5985")."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_state(path: str = STATE_PATH) -> dict:
    """Whatever was recorded, or {} on any error - a missing/corrupt state
    file must read as "never seen this domain running before", never
    raise: this script runs unattended off a timer."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def redefine_steady_state(run=subprocess.run,
                          python_bin: str | None = None) -> bool:
    """Redefine the domain WITHOUT the two install media.

    No --windows-iso/--unattend-iso: domain.py's own install_media() then
    returns None, which is exactly what makes domain_xml() render the
    steady-state definition instead of the install one - see domain.py's
    own docstring on domain_xml(). --replace is unconditional here: by the
    time this runs the domain is necessarily already defined (classify()
    only reaches READY once domstate is 'running'), so guard_replace()
    would refuse without it.

    --keyed-varstore is ALSO unconditional, and just as necessary:
    domain.py's guard_fresh_varstore() otherwise refuses ANY existing NVRAM
    varstore, unconditionally on --replace - and by READY, the varstore at
    domain.py's NVRAM_PATH always exists, because our own earlier `define
    --windows-iso ... --unattend-iso ...` step is what created it. Without
    this flag this call can never succeed even once: it would always hit
    that guard, and the domain would carry its install media forever. See
    guard_fresh_varstore()'s own docstring for why asserting this here -
    and only here - is safe.

    Returns whether the command succeeded; never raises. This is
    best-effort housekeeping - if it fails, the classification already
    reported above (and already saved) must not be undone by it, so a
    failure here is logged by the caller, which must also NOT stop the
    timer: see main()'s handling of the READY branch.
    """
    python_bin = python_bin or sys.executable or "python3"
    proc = run([python_bin, DOMAIN_PY, "define", "--replace",
               "--keyed-varstore"], capture_output=True, text=True)
    return getattr(proc, "returncode", 1) == 0


def stop_self(run=subprocess.run) -> None:
    """Stop the timer that re-triggers this script - there is nothing left
    to learn once a terminal state is reached. Best-effort: if systemctl
    cannot be reached, the next probe just finds the same terminal state
    again and tries to stop it again."""
    run(["systemctl", "stop", "nivuus-guest-ready.timer"],
       capture_output=True, text=True)


def main(*, domstate_fn=None, ip_fn=None, probe_fn=None, redefine_fn=None,
         stop_fn=None, state_path: str | None = None, now_fn=None) -> int:
    domstate_fn = domstate_fn or query_domstate
    ip_fn = ip_fn or find_guest_ip
    probe_fn = probe_fn or probe_port
    redefine_fn = redefine_fn or redefine_steady_state
    stop_fn = stop_fn or stop_self
    now_fn = now_fn or time.time
    state_path = state_path or STATE_PATH

    state = load_state(state_path)
    domstate = domstate_fn()
    is_running = domstate == "running"
    now = now_fn()

    if not is_running:
        # The domain is not (or no longer) running: whatever elapsed clock
        # was being kept no longer means anything - the next time it starts
        # running is a fresh attempt.
        save_state(state_path, {})
        label = domstate or "injoignable"
        print(f"guest-ready: domaine {VM_NAME!r} non actif (etat={label!r}) "
             "- installation non demarree, voir les hooks libvirt")
        return 0

    first_running_at = state.get("first_running_at")
    if not isinstance(first_running_at, (int, float)):
        first_running_at = now
    elapsed = now - first_running_at

    ip = ip_fn()
    port_open = bool(ip) and probe_fn(ip)
    result = classify(domstate=domstate, port_open=port_open, elapsed_s=elapsed)
    save_state(state_path, {"first_running_at": first_running_at,
                            "classification": result})

    if result == READY:
        print(f"guest-ready: invite joignable sur {ip}:{WINRM_PORT} apres "
             f"{int(elapsed)}s - provisionnement termine")
        # The timer stops ONLY once the redefinition itself has actually
        # succeeded - not merely because the port answered. A reachable
        # guest that still carries its install media is not yet the
        # terminal state this script exists to reach: stopping here
        # unconditionally would mean the domain keeps its install media
        # FOREVER, since nothing would ever retry the redefinition again.
        if redefine_fn():
            print("guest-ready: domaine redefini sans les medias "
                 "d installation")
            stop_fn()
        else:
            print("guest-ready: la redefinition sans les medias a echoue - "
                 "le domaine garde les medias d installation ; nouvelle "
                 "tentative au prochain passage de ce minuteur",
                 file=sys.stderr)
    elif result == FAILED:
        print(f"guest-ready: domaine actif depuis {int(elapsed)}s, port "
             f"{WINRM_PORT} toujours ferme (seuil {INSTALL_TIMEOUT_S}s "
             "depasse) - l invite ne peut rien dire tant que WinRM est "
             "ferme ; cette ligne de journal est la seule trace lisible "
             "de l echec", file=sys.stderr)
        stop_fn()
    else:
        print(f"guest-ready: installation en cours depuis {int(elapsed)}s "
             f"(port {WINRM_PORT} pas encore ouvert)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
