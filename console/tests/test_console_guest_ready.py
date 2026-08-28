#!/usr/bin/env python3
"""Tests for console/host/guest-ready-watch.py.

activate.py's 'start' step launches Windows Setup and returns immediately;
without this script, "installing" and "failed" are indistinguishable for an
hour, then forever. This suite proves the four-way classification fires for
the right reason in each case, that the IP lookup uses the host's own
neighbour table (the only source that answers on this topology - measured
on the running production VM, see find_guest_ip()'s docstring) rather than
virsh domifaddr, that a READY guest triggers a media-less redefinition of
the domain, and that the timer stops itself only once a state is ACTUALLY
terminal.

guest-ready-watch.py's own subprocess calls (virsh, systemctl, domain.py
define) are never launched for real: every one of those dependencies is
injected as a fake implementing the FULL interface the code under test
actually uses (returncode/stdout/stderr for a virsh-shaped call, a plain
bool for redefine/probe) - a fake missing a field the code reads would make
the fake, not the code, the thing being measured. That mistake was already
made once on this plan.

Round-1 review found two defects that combine into "the domain keeps its
install media forever": redefine_steady_state()'s argv hit
domain.py's REAL guard_fresh_varstore() unconditionally (a documented
guard, not a bug - see domain.py's own docstring), and main() stopped the
timer on READY whether or not the redefinition actually succeeded, so a
first refusal was never retried. Both fixes are proven below by calling
domain.py's REAL guard functions (imported for real, never faked) with the
exact state and flags this script's own redefinition constructs - not by
comparing the argv to a fake run() that cannot see whether domain.py itself
would accept it. domain.guard_replace()/domain.guard_fresh_varstore() are
pure decision functions (no I/O, never touch virsh) - calling them
directly proves the real refusal/pass path without ever executing `virsh
define` or `domain.py define`.

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
GUEST_DIR = ROOT / "console" / "guest"

spec = importlib.util.spec_from_file_location("guest_ready_watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

# The REAL domain.py, not a fake - see the module docstring on why this
# suite needs it. jinja2-only at module scope (no hardware import unless a
# function that needs it is actually called, which none of the guard/parser
# functions below do), same convention as the other console/tests/* suites
# that import domain.py directly.
sys.path.insert(0, str(GUEST_DIR))
import domain  # noqa: E402

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


# --- IP discovery: the host's neighbour table, keyed by the domain's own --
# MAC/bridge - NOT virsh domifaddr (agent/lease/arp), which handle-vm-start.sh
# also uses but which cannot ever answer on this topology. See the module
# docstring on find_guest_ip() for the 2026-08-28 measurement on the running
# production VM that established this. ------------------------------------ #

class FakeVirshCall:
    """Implements the full interface query_domstate() reads off a
    subprocess.run() result: returncode, stdout, stderr. A fake missing one
    of these would make the fake pass for reasons that have nothing to do
    with the code under test."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


# The neighbour table is the only source that works on this topology: the
# domain sits on an EXTERNAL bridge, so libvirt has no lease to hand out and
# no guest agent answers. Measured on the running production VM.
XML = "<domain><devices><interface type='bridge'>" \
      "<mac address='52:54:00:48:e0:3e'/>" \
      "<source bridge='internalBridge'/></interface></devices></domain>"
NEIGH = ("192.168.3.2 dev internalBridge lladdr 52:54:00:48:e0:3e REACHABLE\n"
         "fe80::426f:90c7:b3a2:c6b dev internalBridge lladdr "
         "52:54:00:48:e0:3e STALE\n")
check("l IPv4 est trouvee par le MAC du domaine",
      watch.find_guest_ip(dumpxml=lambda: XML, neigh=lambda br: NEIGH),
      "192.168.3.2")

# An IPv6 entry carries the same MAC. Returning it would send the WinRM probe
# to an address the guest does not listen on.
check("l IPv6 n est jamais rendue a la place",
      ":" in (watch.find_guest_ip(dumpxml=lambda: XML,
                                  neigh=lambda br: NEIGH) or ""), False)

# A MAC that is not in the table means the guest has not spoken yet - which is
# a state to report, not an error to raise.
check("un invite muet rend None",
      watch.find_guest_ip(dumpxml=lambda: XML, neigh=lambda br: ""), None)

# The MAC comparison must not depend on case: `ip neigh` and libvirt do not
# agree on it across versions.
check("la comparaison de MAC ignore la casse",
      watch.find_guest_ip(dumpxml=lambda: XML,
                          neigh=lambda br: NEIGH.upper()), "192.168.3.2")

# The bridge queried is the one the domain XML declares, not a hardcoded
# name - a domain on a different bridge must be looked up there instead.
seen_bridges = []
watch.find_guest_ip(dumpxml=lambda: XML,
                    neigh=lambda br: seen_bridges.append(br) or NEIGH)
check("neigh() is called with the bridge the domain XML declares",
      seen_bridges, ["internalBridge"])

# No mac/bridge in the XML at all (domain not yet defined, or no interface
# element parsed): nothing to look up, so None - not an exception.
check("no mac/bridge in the domain XML: None, not an exception",
      watch.find_guest_ip(dumpxml=lambda: "<domain/>", neigh=lambda br: NEIGH),
      None)

# Measured gotcha from Task 1's Step 1: `ip neigh show dev <bridge>` (the
# dev-filtered form query_neigh() actually runs) OMITS the `dev <bridge>`
# token per line - only the unfiltered `ip neigh show` includes it. The
# parser must not depend on that token being present.
NEIGH_NO_DEV_TOKEN = "192.168.3.2 lladdr 52:54:00:48:e0:3e REACHABLE\n"
check("the parser does not depend on the optional dev token",
      watch.find_guest_ip(dumpxml=lambda: XML,
                          neigh=lambda br: NEIGH_NO_DEV_TOKEN),
      "192.168.3.2")

run, _ = recording_run({("domstate", None): FakeVirshCall(0, "running\n")})
check("query_domstate strips the trailing newline",
      watch.query_domstate(run=run), "running")

run, _ = recording_run({("domstate", None): FakeVirshCall(1, "", "unreachable")})
check("an unreachable libvirtd reads as the empty state, not an exception",
      watch.query_domstate(run=run), "")


# --- READY redefines the domain WITHOUT the two install media ----------- #
# guard_replace() in domain.py refuses an existing domain without
# --replace, so it must be there; guard_fresh_varstore() refuses an
# existing NVRAM varstore without --keyed-varstore (round-1 fix - see the
# module docstring), so that must be there too; --windows-iso/--unattend-iso
# must NOT be there, or install_media() would build the INSTALL domain
# again instead of the steady-state one - see domain.py's own domain_xml().

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
check("--keyed-varstore is passed (guard_fresh_varstore() would otherwise "
      "refuse the varstore OUR OWN earlier define already created)",
      "--keyed-varstore" in argv, True)
check("neither install medium is passed: this must build the STEADY-STATE "
      "domain, not the install one",
      "--windows-iso" in argv or "--unattend-iso" in argv, False)


def failing_redefine_run(argv, **_kwargs):
    return FakeVirshCall(1, "", "domain busy")


check("a failed redefine is reported as failure, not raised",
      watch.redefine_steady_state(run=failing_redefine_run), False)


# --- the argv above, checked against domain.py's REAL parser and REAL ---
# guards - never a fake, and never `virsh define` itself. This is the
# round-1 fix: the argv-only check above cannot see whether domain.py
# itself would accept it; this section proves it does, past the exact
# point (guard_fresh_varstore) where the unpatched code used to refuse.

# 1) domain.py's real argument parser accepts this exact argv and produces
#    the flags main() would read - proves the CLI wiring, not just the
#    argv shape.
parsed = domain.build_arg_parser().parse_args(argv[2:])  # drop [python, DOMAIN_PY]
check("domain.py's real parser sees action=define", parsed.action, "define")
check("domain.py's real parser sees --replace", parsed.replace, True)
check("domain.py's real parser sees --keyed-varstore", parsed.keyed_varstore, True)
check("domain.py's real parser sees no install media",
      (parsed.windows_iso, parsed.unattend_iso), (None, None))

no_flags = domain.build_arg_parser().parse_args(["define"])
check("--keyed-varstore defaults to False on a plain define (the FIRST "
      "define, media attached, must stay exactly as strict as before)",
      no_flags.keyed_varstore, False)

# 2) domain.py's real guards, fed the parsed flags above and the state they
#    will ACTUALLY find at READY time: the domain exists (it is 'running'),
#    and its varstore exists (our own earlier define created it). Neither
#    guard is faked - a passing call here means domain.py itself, not a
#    stand-in for it, accepts this redefinition.
try:
    domain.guard_replace(exists=True, replace=parsed.replace)
    domain.guard_fresh_varstore(exists=True, keyed_varstore=parsed.keyed_varstore)
    real_guards_pass = True
except domain.DomainError as exc:
    real_guards_pass = False
    print(f"  (domain.py real guard raised: {exc})")
check("the redefinition's own flags pass domain.py's REAL guards, with "
      "the varstore already existing - the exact state READY finds it in",
      real_guards_pass, True)

# The other half of the same proof: WITHOUT --keyed-varstore (i.e. the
# pre-fix argv), the same state is refused by the REAL guard - this is
# what "the domain would keep its install media forever" actually meant.
pre_fix_refused = False
try:
    domain.guard_fresh_varstore(exists=True, keyed_varstore=False)
except domain.DomainError:
    pre_fix_refused = True
check("the PRE-FIX argv (no --keyed-varstore) is refused by the same real "
      "guard, with the same varstore-exists state - this is the bug",
      pre_fix_refused, True)


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

def run_main(domstate, port_open, elapsed_s, redefine_ok=True):
    """Drive main() with fully injected fakes and a throwaway state file.

    `elapsed_s` is made exact, not approximate: first_running_at is seeded
    to a fixed reference time on the FIRST call (prior_state is always None
    here - each scenario gets its own fresh state directory), and now_fn is
    pinned to that same reference plus elapsed_s, so classify() sees
    exactly the elapsed_s the test asked for regardless of wall-clock
    timing. `redefine_ok` controls what the injected redefine_fn reports -
    the round-1 fix under test is that main() must NOT stop the timer on
    READY when this is False. Returns (combined stdout+stderr text,
    stop_called, redefine_called).
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
        return redefine_ok

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

out, stopped, redefined = run_main("running", True, 600, redefine_ok=True)
check("READY + redefine succeeds: the timer stops (NOW actually terminal)",
      stopped, True)
check("READY: the domain is redefined without the install media", redefined, True)

# --- round-1 fix, proven directly: a READY guest whose redefinition FAILS
# must NOT stop the timer - otherwise the media-less redefinition is never
# retried and the domain keeps its install media forever, which is exactly
# the failure combining defect 1 (guard refusal) and defect 2 (unconditional
# stop) produced before this fix.
out, stopped, redefined = run_main("running", True, 600, redefine_ok=False)
check("READY + redefine FAILS: the timer keeps polling, NOT terminal yet",
      stopped, False)
check("READY + redefine FAILS: a redefinition was still attempted",
      redefined, True)
check("READY + redefine FAILS: the failure and the retry are explained",
      "echoue" in out.lower() and "medias" in out.lower(), True)

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the four states classify for the right reason, IP discovery "
      "uses the host's neighbour table keyed by the domain's MAC (the only "
      "source that answers on this topology), the redefinition argv "
      "survives domain.py's REAL guards (--keyed-varstore fix), and the "
      "timer stops on READY only once that redefinition has actually "
      "succeeded")
