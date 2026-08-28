#!/usr/bin/env python3
"""Tests for console/host/guest-ready-watch.py.

activate.py's 'start' step launches Windows Setup and returns immediately;
without this script, "installing" and "failed" are indistinguishable for an
hour, then forever. This suite proves the four-way classification fires for
the right reason in each case (never never touching a real virsh, systemctl
or domain.py), that the IP lookup reuses handle-vm-start.sh's own method
rather than inventing a second one, that a READY guest triggers a
media-less redefinition of the domain, and that the timer stops itself only
on a TERMINAL state.

Never calls the real virsh/systemctl/domain.py: every dependency
guest-ready-watch.py reads is injected as a fake implementing the FULL
interface the code under test actually uses (returncode/stdout/stderr for
a virsh-shaped call, a plain bool for redefine/probe) - a fake missing a
field the code reads would make the fake, not the code, the thing being
measured. That mistake was already made once on this plan.

Run: python3 console/tests/test_console_guest_ready.py
"""
import importlib.util
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCRIPT = ROOT / "console" / "host" / "guest-ready-watch.py"

spec = importlib.util.spec_from_file_location("guest_ready_watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- Step 1 from the brief, verbatim: the four states, classified for the
# right reason. port_open PRIMES over an elapsed clock past the timeout -
# a late-arriving guest is still a successfully provisioned one. -------- #

check("domaine eteint : la VM n a jamais demarre",
      watch.classify(domstate="shut off", port_open=False, elapsed_s=60),
      watch.NOT_STARTED)
check("domaine actif, port ferme, tot : installation en cours",
      watch.classify(domstate="running", port_open=False, elapsed_s=60),
      watch.INSTALLING)
check("domaine actif, port ferme, trop longtemps : echec",
      watch.classify(domstate="running", port_open=False, elapsed_s=4 * 3600),
      watch.FAILED)
check("port ouvert : provisionne",
      watch.classify(domstate="running", port_open=True, elapsed_s=600),
      watch.READY)
check("un port ouvert tardif reste un succes",
      watch.classify(domstate="running", port_open=True, elapsed_s=9 * 3600),
      watch.READY)

# The threshold is a NAMED constant, not a number the test recopies: prove
# the boundary sits exactly where the constant says it does, by referring
# to watch.INSTALL_TIMEOUT_S rather than writing its value out again.
check("juste sous le seuil : encore installation",
      watch.classify(domstate="running", port_open=False,
                     elapsed_s=watch.INSTALL_TIMEOUT_S - 1),
      watch.INSTALLING)
check("au seuil pile : echec",
      watch.classify(domstate="running", port_open=False,
                     elapsed_s=watch.INSTALL_TIMEOUT_S),
      watch.FAILED)


# --- IP discovery: the SAME method as handle-vm-start.sh - agent first, ---
# then lease - never a third, invented source. --------------------------- #

class FakeVirshCall:
    """Implements the full interface query_domstate()/find_guest_ip() read
    off a subprocess.run() result: returncode, stdout, stderr. A fake
    missing one of these would make the fake pass for reasons that have
    nothing to do with the code under test."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


AGENT_TABLE_HIT = (
    " Name       MAC address          Protocol     Address\n"
    "-------------------------------------------------------------------------------\n"
    " vnet25     52:54:00:48:e0:3e    ipv4         192.168.3.2/24\n\n")
LEASE_TABLE_HIT = AGENT_TABLE_HIT
EMPTY_TABLE = (
    " Name       MAC address          Protocol     Address\n"
    "-------------------------------------------------------------------------------\n\n")


def recording_run(script):
    """A fake virsh: `script` maps a (subcommand, source-or-None) key to the
    FakeVirshCall it should answer with. Records every call it saw."""
    calls = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        subcommand = argv[1]
        source = argv[argv.index("--source") + 1] if "--source" in argv else None
        return script[(subcommand, source)]

    return run, calls


run, calls = recording_run({("domifaddr", "agent"): FakeVirshCall(0, AGENT_TABLE_HIT)})
ip = watch.find_guest_ip(run=run)
check("agent source alone: IP found", ip, "192.168.3.2")
check("agent hit: lease is never tried", len(calls), 1)
check("the call targets the domain's own bridge (domain.py's BRIDGE)",
      "--interface" in calls[0] and
      calls[0][calls[0].index("--interface") + 1] == watch.VM_INTERFACE,
      True)
check("agent is tried first, exactly like handle-vm-start.sh",
      calls[0][-2:], ["--source", "agent"])

run, calls = recording_run({
    ("domifaddr", "agent"): FakeVirshCall(0, EMPTY_TABLE),
    ("domifaddr", "lease"): FakeVirshCall(0, LEASE_TABLE_HIT),
})
ip = watch.find_guest_ip(run=run)
check("agent empty, lease hit: IP found via lease", ip, "192.168.3.2")
check("both sources tried, agent first", [c[-2:] for c in calls],
      [["--source", "agent"], ["--source", "lease"]])

run, calls = recording_run({
    ("domifaddr", "agent"): FakeVirshCall(1, "", "agent not configured"),
    ("domifaddr", "lease"): FakeVirshCall(0, EMPTY_TABLE),
})
ip = watch.find_guest_ip(run=run)
check("neither source has an answer: None, not an exception", ip, None)

run, _ = recording_run({("domstate", None): FakeVirshCall(0, "running\n")})
check("query_domstate strips the trailing newline",
      watch.query_domstate(run=run), "running")

run, _ = recording_run({("domstate", None): FakeVirshCall(1, "", "unreachable")})
check("an unreachable libvirtd reads as the empty state, not an exception",
      watch.query_domstate(run=run), "")


# --- READY redefines the domain WITHOUT the two install media ----------- #
# guard_replace() in domain.py refuses an existing domain without
# --replace, so it must be there; --windows-iso/--unattend-iso must NOT
# be there, or install_media() would build the INSTALL domain again
# instead of the steady-state one - see domain.py's own domain_xml().

redefine_calls = []


def fake_redefine_run(argv, **_kwargs):
    redefine_calls.append(list(argv))
    return FakeVirshCall(0)


ok = watch.redefine_steady_state(run=fake_redefine_run, python_bin="python3")
check("redefine_steady_state reports success", ok, True)
check("exactly one command was run", len(redefine_calls), 1)
argv = redefine_calls[0]
check("it calls domain.py define", argv[:3], ["python3", watch.DOMAIN_PY, "define"])
check("--replace is passed (guard_replace() would refuse without it)",
      "--replace" in argv, True)
check("neither install medium is passed: this must build the STEADY-STATE "
      "domain, not the install one",
      "--windows-iso" in argv or "--unattend-iso" in argv, False)


def failing_redefine_run(argv, **_kwargs):
    return FakeVirshCall(1, "", "domain busy")


check("a failed redefine is reported as failure, not raised",
      watch.redefine_steady_state(run=failing_redefine_run), False)


# --- probe_port: a real loopback socket, not a mocked one --------------- #
import socket as _socket  # noqa: E402

with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    free_port = listener.getsockname()[1]
    check("a real listening socket is reachable",
          watch.probe_port("127.0.0.1", free_port, timeout=1), True)

# The listener above is now closed: nothing is listening on that port.
check("a closed port is unreachable",
      watch.probe_port("127.0.0.1", free_port, timeout=1), False)


# --- main(): the state machine end-to-end, with every dependency injected,
# never a real virsh/systemctl/domain.py. Proves the timer stops itself
# EXACTLY on a terminal state, and that READY (and only READY) redefines. #

def run_main(domstate, port_open, elapsed_s):
    """Drive main() with fully injected fakes and a throwaway state file.

    `elapsed_s` is made exact, not approximate: first_running_at is seeded
    to a fixed reference time on the FIRST call (prior_state is always None
    here - each scenario gets its own fresh state directory), and now_fn is
    pinned to that same reference plus elapsed_s, so classify() sees
    exactly the elapsed_s the test asked for regardless of wall-clock
    timing. Returns (combined stdout+stderr text, stop_called, redefine_called).
    """
    import contextlib
    import io

    stop_calls = []
    redefine_calls_ = []
    base = 1_000_000.0

    def domstate_fn():
        return domstate

    def ip_fn():
        return "192.168.3.2" if domstate == "running" else None

    def probe_fn(_ip):
        return port_open

    def redefine_fn():
        redefine_calls_.append(True)
        return True

    def stop_fn():
        stop_calls.append(True)

    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "state.json")
        if domstate == "running":
            # Seed first_running_at so elapsed_s above is exactly what
            # classify() sees, instead of "now - now" == 0 on a fresh state.
            watch.save_state(state_path, {"first_running_at": base})
        now_fn = lambda: base + elapsed_s  # noqa: E731

        buf = io.StringIO()
        errbuf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
            watch.main(domstate_fn=domstate_fn, ip_fn=ip_fn, probe_fn=probe_fn,
                      redefine_fn=redefine_fn, stop_fn=stop_fn,
                      state_path=state_path, now_fn=now_fn)
    return buf.getvalue() + errbuf.getvalue(), bool(stop_calls), bool(redefine_calls_)


out, stopped, redefined = run_main("shut off", False, 60)
check("NOT_STARTED: the timer keeps polling", stopped, False)
check("NOT_STARTED: no redefinition attempted", redefined, False)
check("NOT_STARTED: the message names the domain state", "shut off" in out, True)
check("NOT_STARTED: the message says the guest was never started",
      "non actif" in out, True)

out, stopped, redefined = run_main("running", False, 60)
check("INSTALLING: the timer keeps polling", stopped, False)
check("INSTALLING: no redefinition attempted", redefined, False)

out, stopped, redefined = run_main("running", False, watch.INSTALL_TIMEOUT_S)
check("FAILED: the timer stops (terminal state)", stopped, True)
check("FAILED: no redefinition attempted (the guest never answered)",
      redefined, False)
check("FAILED: the failure is explained in the journal text",
      "echec" in out.lower() or "ferme" in out.lower(), True)

out, stopped, redefined = run_main("running", True, 600)
check("READY: the timer stops (terminal state)", stopped, True)
check("READY: the domain is redefined without the install media", redefined, True)

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the four states classify for the right reason, IP discovery "
      "reuses handle-vm-start.sh's own method, READY redefines the domain "
      "without its install media, and the timer stops itself only on a "
      "terminal state")
