#!/usr/bin/env python3
"""Classify the Windows guest's provisioning state and log it to the journal.

activate.py's 'start' step launches Windows Setup and returns immediately -
it does not wait for Setup to finish, which can take the better part of an
hour. Without this watch, "installation en cours" and "a echoue" are
indistinguishable: both look, from the host, like a running domain it
cannot yet talk to - for an hour, then forever.

THE SIGNAL IS NOT INVENTED, but it changed on 2026-08-26 and this module
used to still describe the old one. Port 5985 (WinRM) is no longer the
witness: console/guest/provision/00-bootstrap.ps1 now opens it at the FIRST
stage, deliberately - a closed port used to fail exactly where it mattered,
when a later stage threw, 99-marker.ps1 was never reached, and the only
remote door into the guest stayed shut precisely when something needed
looking at. "5985 reachable" therefore now only means the guest is alive
enough to accept a command - never that provisioning finished.

The real witness is the file 99-marker.ps1 writes as its very last act:
C:\\nivuus\\state\\PROVISION.done, stamped with the payload's own
provision_version (see console/guest/payload.py's PROVISION_VERSION). This
script reads that file over WinRM - through console/guest/winrm_exec.py,
the same tool console/guest/testdomain.py's _marker_present() already uses
for the identical reason - and checks its version, never its mere
presence: a rebuild boots a disk that can still carry the PREVIOUS run's
marker, so presence alone would declare a console ready before its own
installation had begun. See marker_says_ready() and read_marker() below.

Four states, not three, because "not started" and "installation en cours"
call for opposite operator reactions (go look at the libvirt hooks, versus
just wait) and nothing distinguished them before this script existed:

  NOT_STARTED - the domain is not 'running': it never started, or it
                stopped/crashed. Look at the libvirt hooks/journal.
  INSTALLING  - the domain is running, the current-version marker is not
                there yet (WinRM unreachable, marker absent, or a stale
                version), and not for unreasonably long yet.
  FAILED      - the domain is running, the current-version marker is still
                missing past INSTALL_TIMEOUT_S. The guest cannot say why
                on its own - so only the host's own journal (this line)
                can make the failure legible.
  READY       - the marker carries the CURRENT provision_version. This
                overrides everything else: a guest that has finished
                provisioning IS ready, even if it took longer than
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

The IP-discovery method is the host's own neighbour table, keyed by the
domain's MAC address (see find_guest_ip()) - NOT virsh domifaddr, which
handle-vm-start.sh also uses but which cannot ever answer on this topology.
Measured on the running production VM (2026-08-28): `--source agent` fails
(the domain declares no <channel>, so no guest agent ever answers),
`--source lease` and `--source arp` both return empty tables, and `virsh
net-list --all` declares no libvirt network at all - the domain sits on an
externally managed bridge, not a libvirt one with a lease file of its own.
handle-vm-start.sh carries the same defect; it goes unnoticed there only
because started/begin/rules.sh installs the forward-ports unconditionally,
regardless of whether the IP lookup found anything.

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
WINRM_PORT = 5985
PORT_PROBE_TIMEOUT_S = 3

# The provisioning marker's path in the guest, and the tool used to read it
# over WinRM. Same file 99-marker.ps1 writes, same tool testdomain.py's
# _marker_present() already uses for the same reason - see the module
# docstring.
MARKER_PATH = r"C:\nivuus\state\PROVISION.done"
WINRM_EXEC = "/opt/nivuus-packages/console/guest/winrm_exec.py"

# The password winrm_exec.py needs is the one console/guest_steps.py's
# 'secrets' step ALREADY wrote - never re-derived here. That step writes
# it under activate.py's DEFAULT_GUEST_WORKDIR (/var/lib/nivuus/guest),
# not winrm_exec.py's OWN default (/root/.config/nivuus/windows-admin.pass,
# which is build.py's manual-test default, unused by the real activate
# pipeline): the two just happen to differ, so this must be passed
# explicitly, never left to winrm_exec.py's default matching by luck.
# Copied, not imported, same reason as DOMAIN_PY above - this script runs
# standalone under /usr/local/sbin/.
GUEST_PASS_FILE = "/var/lib/nivuus/guest/secrets/windows-admin.pass"

# Hardcoded, English, literal strings winrm_exec.py itself prints on stderr
# when it could not even reach the guest to run the command - as opposed to
# the remote command's OWN (locale-dependent, unpredictable) error text when
# WinRM answered but the marker file does not exist. read_marker() below
# tells the two apart by these prefixes, never by return code: winrm_exec.py
# returns 1 in both cases (a "file not found" `type` also exits 1), so the
# return code alone cannot distinguish them - only these fixed messages can.
_WINRM_UNREACHABLE_PREFIXES = (
    "error: cannot reach guest",
    "error: password file not found",
)

# "a reasonable delay" per the task this script implements: long enough
# that a normal unattended LTSC install (including the reboots driver
# installs cause) is never mistaken for a failure, short enough that a
# genuinely stuck install is reported well before an operator would
# otherwise notice on their own.
INSTALL_TIMEOUT_S = 2 * 3600

STATE_DIR = "/var/lib/nivuus"
STATE_PATH = os.path.join(STATE_DIR, "guest-ready-state.json")

# installer/packages/discovery.py's PACKAGES_DIR default, plus this
# package's own name (console/nivuus-package.yaml). Copied, not imported:
# this script is deployed standalone under /usr/local/sbin/, the same
# reason handle-vm-start.sh and vm-idle-shutdown.sh do not import from
# console/. apply_packages() copies the whole package tree there once, at
# install time, and never removes it - so guest/domain.py is reliably at
# this path on any machine where this script itself is running.
DOMAIN_PY = "/opt/nivuus-packages/console/guest/domain.py"

NOT_STARTED = "not_started"
INSTALLING = "installing"
FAILED = "failed"
READY = "ready"
TERMINAL_STATES = frozenset({FAILED, READY})

_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# Both single and double quotes are tolerated even though every real sample
# measured on this host used single quotes (virsh dumpxml AND the Step-2
# test double agree) - a defensive margin against a libvirt version that
# quotes XML attributes differently costs nothing here.
_MAC_RE = re.compile(r"mac address=['\"]([0-9a-fA-F:]+)['\"]")
_BRIDGE_RE = re.compile(r"source bridge=['\"]([^'\"]+)['\"]")


def classify(*, domstate: str, ready: bool, elapsed_s: float) -> str:
    """The four-way split. See the module docstring for what each means.

    ready wins over everything, INCLUDING elapsed_s past the timeout: a
    guest whose current-version marker is there IS a provisioned guest,
    however long it took to get there - the clock only distinguishes
    "still waiting" from "give up waiting and say so", it never overrides
    a guest that has actually finished.
    """
    if ready:
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


def query_dumpxml(vm_name: str = VM_NAME, run=subprocess.run) -> str:
    """The domain's XML, or "" when libvirt cannot answer - same convention
    as query_domstate()."""
    proc = run(["virsh", "dumpxml", vm_name], capture_output=True, text=True,
               env=dict(os.environ, LC_ALL="C", LANG="C"))
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def query_neigh(bridge: str, run=subprocess.run) -> str:
    """Raw `ip neigh show dev <bridge>` output, or "" on failure.

    NOTE the measured format gotcha: with the `dev <bridge>` filter applied,
    iproute2 OMITS the `dev <bridge>` token from each line (it is redundant
    once filtered) - only the unfiltered `ip neigh show` includes it. Both
    were measured on this host (iproute2-6.15.0) for Task 1's Step 1. The
    parser in find_guest_ip() below never assumes that token is present.
    """
    proc = run(["ip", "neigh", "show", "dev", bridge], capture_output=True,
               text=True, env=dict(os.environ, LC_ALL="C", LANG="C"))
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def find_guest_ip(vm_name: str = VM_NAME, run=subprocess.run,
                  dumpxml=None, neigh=None) -> str | None:
    """The guest's IPv4, found through the host's neighbour table rather
    than through any of libvirt's own IP-discovery sources - see the module
    docstring for why none of those (agent, lease, arp) can ever answer on
    this topology, measured on the running production VM.

    What DOES work, measured the same day: the domain XML declares its own
    MAC address and bridge; the host's neighbour table on that bridge
    associates the MAC with an IP once the guest has spoken at all (DHCP
    request, gratuitous ARP, any traffic) - no libvirt network, no guest
    agent channel, required. A MAC absent from the table means the guest
    has not spoken yet, which is a state to report (see classify()), not an
    error to raise - so this returns None, never raises, on every miss.

    IPv6 neighbour entries carry the SAME MAC (link-local, always present
    once a guest is up) and are deliberately skipped: returning one would
    send the WinRM probe (see probe_port()) to an address the guest is not
    listening on.

    `dumpxml`/`neigh` are injected as bare callables - dumpxml() takes no
    argument, neigh(bridge) takes the bridge name found in the XML - rather
    than a `run=` shim, so tests can hand back literal strings without also
    reimplementing subprocess.CompletedProcess. See
    test_console_guest_ready.py.
    """
    dumpxml = dumpxml or (lambda: query_dumpxml(vm_name, run=run))
    neigh = neigh or (lambda bridge: query_neigh(bridge, run=run))

    xml = dumpxml() or ""
    mac_match = _MAC_RE.search(xml)
    bridge_match = _BRIDGE_RE.search(xml)
    if not mac_match or not bridge_match:
        return None
    mac = mac_match.group(1).lower()
    bridge = bridge_match.group(1)

    table = neigh(bridge) or ""
    for line in table.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        ip = tokens[0]
        if ":" in ip:
            # IPv6 - never returned, see the docstring above.
            continue
        lower_tokens = [t.lower() for t in tokens]
        if "lladdr" not in lower_tokens:
            continue
        lladdr_idx = lower_tokens.index("lladdr")
        if lladdr_idx + 1 >= len(tokens):
            continue
        if lower_tokens[lladdr_idx + 1] == mac:
            return ip
    return None


def probe_port(ip: str, port: int = WINRM_PORT,
               timeout: float = PORT_PROBE_TIMEOUT_S) -> bool:
    """A plain TCP connect - WinRM's own protocol is not spoken here, only
    reachability. Used as a cheap pre-check before attempting the slower
    WinRM session in read_marker(): since 00-bootstrap.ps1, a reachable
    port only proves the guest is alive, never that it is done - see the
    module docstring."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def marker_says_ready(marker_text: str, expected: str) -> bool:
    """Pure, no I/O: true iff marker_text carries a WHOLE LINE reading
    provision_version=<expected>.

    Version-checked on purpose, per the module docstring - a rebuild boots
    a disk that already holds the PREVIOUS run's marker, so presence alone
    proves nothing about THIS run. An absent or empty marker_text (the
    guest never wrote one, or read_marker() below could not reach it)
    naturally contains no such line and returns False.

    The match is anchored to a full line, not a bare substring: a plain
    `"provision_version=B3" in text` would also match a marker carrying
    "provision_version=B30" (B3 is a prefix of B30), which is exactly the
    kind of false positive this whole check exists to rule out.
    """
    pattern = re.compile(
        r"(?m)^provision_version=" + re.escape(expected) + r"\s*$")
    return pattern.search(marker_text) is not None


def read_marker(ip: str, run=subprocess.run) -> str | None:
    """The provisioning marker's raw content, read over WinRM - or None
    when WinRM itself could not be reached (connection failure, or a
    missing GUEST_PASS_FILE), as opposed to "" (or any text without the
    expected line) when WinRM DID answer but the marker is not there yet,
    or not at the expected version. These are deliberately told apart:
    "the guest has not spoken at all" and "it has spoken but is not done"
    call for different journal lines - see main().

    Same tool testdomain.py's _marker_present() already uses for the same
    check: console/guest/winrm_exec.py, which reads its password from a
    FILE (GUEST_PASS_FILE), never from argv - so GUEST_IP is passed through
    the environment here too, never on the command line.
    """
    proc = run([sys.executable, WINRM_EXEC, "cmd", f"type {MARKER_PATH}"],
              capture_output=True, text=True,
              env=dict(os.environ, GUEST_IP=ip, GUEST_PASS_FILE=GUEST_PASS_FILE,
                       LC_ALL="C"))
    stderr = getattr(proc, "stderr", "") or ""
    if stderr.startswith(_WINRM_UNREACHABLE_PREFIXES):
        return None
    return getattr(proc, "stdout", "") or ""


def expected_provision_version() -> str:
    """console/guest/payload.py's PROVISION_VERSION, read at call time, not
    copied here - copying it would silently drift the day payload.py bumps
    it for a new build.

    Imported lazily, INSIDE this function rather than at module scope, same
    convention and same reason as domain.py's own lazy HardwareError import
    (see CLAUDE.md's "the lesson that outlives the bug"): this script is
    deployed standalone under /usr/local/sbin/, and a module-scope import
    would make importing THIS FILE depend on guest/payload.py being on
    sys.path even for callers that never reach READY. DOMAIN_PY already
    names where apply_packages() puts the whole package tree - payload.py
    sits right next to domain.py in that same directory.
    """
    guest_dir = os.path.dirname(DOMAIN_PY)
    if guest_dir not in sys.path:
        sys.path.insert(0, guest_dir)
    import payload  # local import by design - see the docstring above
    return payload.PROVISION_VERSION


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


def _marker_detail(marker_text: str | None, expected: str) -> str:
    """A short French phrase explaining WHY the guest is not ready yet -
    WinRM unreachable, marker absent, or marker present but stale - the
    three causes classify() collapses into a single INSTALLING/FAILED
    state but that an operator reading the journal still needs told apart.
    """
    if marker_text is None:
        return f"WinRM injoignable sur le port {WINRM_PORT}"
    if not marker_text.strip():
        return f"temoin absent ({MARKER_PATH})"
    return f"temoin present mais pas a la version {expected}"


def main(*, domstate_fn=None, ip_fn=None, probe_fn=None, marker_fn=None,
         version_fn=None, redefine_fn=None, stop_fn=None,
         state_path: str | None = None, now_fn=None) -> int:
    domstate_fn = domstate_fn or query_domstate
    ip_fn = ip_fn or find_guest_ip
    probe_fn = probe_fn or probe_port
    marker_fn = marker_fn or read_marker
    version_fn = version_fn or expected_provision_version
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
        #
        # A resting VM is the NOMINAL state, not an anomaly - most of a
        # console's life is spent shut off or hibernated between sessions,
        # once provisioning finished cleanly long ago. Logging this line on
        # every tick (this timer runs every 2 minutes, see
        # host/systemd/nivuus-guest-ready.timer) would mean permanent,
        # unbounded noise on a console that is doing perfectly well - so it
        # is printed once, on the transition INTO not-running, and then
        # stays silent for as long as that stays true. already_reported is
        # read from the state THIS run loaded, before it gets overwritten
        # below - a domain that starts running again drops the whole dict
        # (see the running branch's own save_state call), so the flag
        # naturally reappears absent the next time it stops, and the line
        # is printed again: a genuine transition is still worth a line, only
        # the repeat of an unchanged rest is not.
        already_reported = bool(state.get("not_started_reported"))
        save_state(state_path, {"not_started_reported": True})
        if not already_reported:
            label = domstate or "injoignable"
            print(f"guest-ready: domaine {VM_NAME!r} non actif (etat={label!r}) "
                 "- installation non demarree, voir les hooks libvirt")
        return 0

    first_running_at = state.get("first_running_at")
    if not isinstance(first_running_at, (int, float)):
        first_running_at = now
    elapsed = now - first_running_at

    ip = ip_fn()
    # The TCP probe is a cheap pre-check: reading the marker means opening
    # a real WinRM session, worth skipping when the port is not even open.
    marker_text = marker_fn(ip) if ip and probe_fn(ip) else None
    expected = version_fn()
    ready = marker_text is not None and marker_says_ready(marker_text, expected)
    result = classify(domstate=domstate, ready=ready, elapsed_s=elapsed)
    save_state(state_path, {"first_running_at": first_running_at,
                            "classification": result})

    if result == READY:
        print(f"guest-ready: temoin de provisionnement a jour (version "
             f"{expected}) sur {ip} apres {int(elapsed)}s - "
             "provisionnement termine")
        # The timer stops ONLY once the redefinition itself has actually
        # succeeded - not merely because the marker was found. A ready
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
        detail = _marker_detail(marker_text, expected)
        print(f"guest-ready: domaine actif depuis {int(elapsed)}s, {detail} "
             f"(seuil {INSTALL_TIMEOUT_S}s depasse) - cette ligne de "
             "journal est la seule trace lisible de l echec",
             file=sys.stderr)
        stop_fn()
    else:
        detail = _marker_detail(marker_text, expected)
        print(f"guest-ready: installation en cours depuis {int(elapsed)}s "
             f"({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
