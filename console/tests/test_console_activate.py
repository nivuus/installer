#!/usr/bin/env python3
"""Arming must be a symlink, must be idempotent, and must never dangle -
and on the machine being activated it must also take effect NOW.

Nine winvm-proxy-*.socket entries sit in this host's sockets.target.wants/
as REGULAR FILES; systemd ignores them with "is not a symlink, ignoring".
That is the failure this asserts against: a unit that looks enabled and
is not.

The second half is the same failure one boot later: the activation unit is
WantedBy=multi-user.target, so it runs after sockets.target and
timers.target: linking alone leaves the wake sockets silent and the idle
timer stopped until a further reboot, with the stamp file already written.
Nothing here touches the machine's own systemd: the reload/start path is
driven with a STUB systemctl first on PATH, and the --root runs assert that
no systemctl is invoked at all.

Arming still works, and still comes FIRST: if the build fails, the wake
sockets must already be armed - an operator who fixes the medium by hand
then reboots should not also have to re-arm anything. The first scenario
below proves exactly that with a REAL guest_steps.plan_steps() call: the
context on purpose carries no secrets, so the build is refused (a missing
'ltsc_key' answer) while the arming links still land - never a fabricated
or mocked build failure, the real refusal guest_steps.py raises.

A hook refusing the VM start is DESIGNED behaviour, not a build failure.
Reporting it as one would send the operator hunting through build logs for
a problem that lives in the GPU handover. That half is proven directly
against activate.run_steps() with SIMULATED steps (never a real build, never
a real virsh) - see the classification section at the bottom.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "console", "hooks", "activate.py")

LINKS = {
    "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket":
        "/etc/systemd/system/vm-trigger-47984.socket",
    "etc/systemd/system/sockets.target.wants/vm-trigger-47989.socket":
        "/etc/systemd/system/vm-trigger-47989.socket",
    "etc/systemd/system/timers.target.wants/vm-idle-shutdown.timer":
        "/etc/systemd/system/vm-idle-shutdown.timer",
    "etc/systemd/system/timers.target.wants/nivuus-guest-ready.timer":
        "/etc/systemd/system/nivuus-guest-ready.timer",
}

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


def stub_systemctl(directory, exit_code=0):
    """A systemctl that records its arguments instead of driving systemd.

    Returns the path of the journal it appends to. Running the real thing
    from a test would reload and start units on the machine under test.
    """
    log = os.path.join(directory, "systemctl.log")
    script = os.path.join(directory, "systemctl")
    with open(script, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'echo "$@" >> {log}\n'
                 f"exit {exit_code}\n")
    os.chmod(script, 0o755)
    return log


def stub_env(directory):
    return dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"])


def ctx_json(root, **extra_answers):
    """The stdin context, with 'guest_workdir' ALWAYS pinned under `root`.

    Without this, a missing answer falls back to guest_steps.py's real
    default (/var/lib/nivuus/guest) and a test run would read/write that
    path on the machine actually running the test - never acceptable here.
    Deliberately carries no secrets/medium answers by default, so
    guest_steps.plan_steps() genuinely refuses rather than a test double
    standing in for it.
    """
    answers = {
        "dedicated_nvme": "/dev/nvme1n1",
        "retro": False,
        "guest_workdir": os.path.join(root, "var/lib/nivuus/guest"),
    }
    answers.update(extra_answers)
    return json.dumps({
        "package": {"name": "console", "version": "1.0.0", "root": "console"},
        "hw": {}, "answers": answers,
    })


def run(root, env=None, **extra_answers):
    return subprocess.run(
        [sys.executable, HOOK, "--phase", "activate", "--root", root],
        input=ctx_json(root, **extra_answers), capture_output=True, text=True,
        cwd=ROOT, env=env)


# A target where install has run: the unit files are present. No secret and
# no Windows medium is given, so the REAL guest_steps.plan_steps() refuses -
# the point of this scenario is that arming still happens, and is still
# verified, even though the guest build never gets past its own front door.
with tempfile.TemporaryDirectory() as root:
    units = os.path.join(root, "etc/systemd/system")
    os.makedirs(units)
    for name in ("vm-trigger-47984.socket", "vm-trigger-47989.socket",
                 "vm-idle-shutdown.timer", "nivuus-guest-ready.timer"):
        open(os.path.join(units, name), "w").write("[Unit]\n")

    bin_dir = os.path.join(root, "stub-bin")
    os.makedirs(bin_dir)
    log = stub_systemctl(bin_dir)

    proc = run(root, env=stub_env(bin_dir))
    check("a missing medium/secret refuses the phase (rc != 0)",
          proc.returncode != 0)
    check("the refusal is reported as a motivated one, not a build panne",
          "entree refusee" in (proc.stderr or "").lower())
    check("and it names the actual missing answer",
          "ltsc_key" in (proc.stderr or ""))

    # A --root that is not "/" describes a target being installed, or a
    # throwaway tree: reloading and starting there would drive the
    # INSTALLER's systemd, not the target's.
    check("a non-/ root never invokes systemctl", not os.path.exists(log))
    for rel, target in LINKS.items():
        path = os.path.join(root, rel)
        check(f"{rel} is a symlink", os.path.islink(path))
        if os.path.islink(path):
            check(f"{rel} points at {target}", os.readlink(path) == target)
    check("arming happened even though the build was refused",
          all(os.path.islink(os.path.join(root, rel)) for rel in LINKS))

    # Idempotent: an interrupted activation retries at the next boot, and
    # the stamp file is written only on success (main() returns 1 both
    # times here, since the answers never change).
    again = run(root)
    check(f"activate is idempotent (rc={again.returncode})",
          again.returncode == proc.returncode)

# A target where a unit is missing: refuse rather than dangle.
with tempfile.TemporaryDirectory() as root:
    os.makedirs(os.path.join(root, "etc/systemd/system"))
    proc = run(root)
    check("a missing unit is refused, not linked", proc.returncode != 0)
    check("the refusal names the missing unit",
          "vm-trigger-47984.socket" in (proc.stderr or ""))
    dangling = os.path.join(
        root, "etc/systemd/system/sockets.target.wants/vm-trigger-47984.socket")
    check("no dangling link is left behind", not os.path.lexists(dangling))

# The reload/start half, driven directly: main() only takes it when --root is
# "/", and running the hook that way would arm this very machine.
spec = importlib.util.spec_from_file_location("console_activate", HOOK)
activate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activate)

check("start_now covers exactly the four armed units",
      sorted(activate.WANTS) == sorted([
          "vm-idle-shutdown.timer",
          "vm-trigger-47984.socket",
          "vm-trigger-47989.socket",
          "nivuus-guest-ready.timer"]))

with tempfile.TemporaryDirectory() as bin_dir:
    log = stub_systemctl(bin_dir)
    saved = os.environ["PATH"]
    os.environ["PATH"] = bin_dir + os.pathsep + saved
    try:
        broken = activate.start_now(list(activate.WANTS))
    finally:
        os.environ["PATH"] = saved
    check("a successful systemctl reports nothing broken", broken == [])
    recorded = open(log).read().splitlines()
    check("the reload comes first", recorded[:1] == ["daemon-reload"])
    for unit in activate.WANTS:
        check(f"{unit} is started, not merely linked",
              f"start {unit}" in recorded)

with tempfile.TemporaryDirectory() as bin_dir:
    stub_systemctl(bin_dir, exit_code=1)
    saved = os.environ["PATH"]
    os.environ["PATH"] = bin_dir + os.pathsep + saved
    try:
        broken = activate.start_now(list(activate.WANTS))
    finally:
        os.environ["PATH"] = saved
    # A failing systemctl must be REPORTED, never fatal: the links already
    # make the next boot correct, and systemctl is legitimately unusable in
    # constrained environments.
    check("a failing systemctl is reported for every command",
          len(broken) == 1 + len(activate.WANTS))

# --- classifying a step failure: the real deliverable of this task ------- #
#
# A hook refusing the VM start is DESIGNED behaviour, not a build failure:
# by the time the 'start' step runs, secrets/payload/build/define have all
# already succeeded, so a failure exactly there lives in libvirt's own GPU
# hooks (detach from the host, stop ollama, nvidia-persistenced, Tdarr), not
# in this phase's construction of the guest. Driven with SIMULATED steps
# (activate.guest_steps.Step, never guest_steps.plan_steps() itself) so this
# never touches a real command, a real medium, or the real domain.


def fake_step(name, run):
    return activate.guest_steps.Step(name, lambda: False, run, None, None,
                                     "etape simulee")


def message_for(name, run):
    """Drive activate.run_steps() with one simulated, always-not-done step.

    Returns the classified message activate.py would print to stderr, or ""
    if the step did not actually fail (a bug in the fixture, not the code
    under test).
    """
    try:
        activate.run_steps([fake_step(name, run)], emit_fn=lambda event: None)
    except activate.ActivationFailure as exc:
        return str(exc)
    return ""


def _start_refused_run():
    def run_it():
        # This is exactly the shape guest_steps.default_runner raises for a
        # non-zero exit, wrapped as activate.StepCommandFailed - what
        # activate.classifying_runner does for a real 'virsh start Windows'
        # that a libvirt prepare hook refused.
        raise activate.StepCommandFailed(
            "virsh exited with status 1: virsh start Windows")
    return message_for("start", run_it)


def _build_panne_run():
    def run_it():
        raise activate.StepCommandFailed(
            "build.py exited with status 1: python3 guest/build.py "
            "--windows-iso /media/backup/ltsc.iso --output nivuus-unattend.iso")
    return message_for("build", run_it)


def _refused_input_mid_step_run():
    # guest_steps.py can refuse an input from INSIDE a step's run(), not
    # only before any step is planned - build_run() re-checks the medium
    # with media_identity() right before launching. That is still class 1,
    # not class 3: guest_steps.GuestBuildError raised directly, never
    # wrapped as StepCommandFailed.
    def run_it():
        raise activate.guest_steps.GuestBuildError(
            "the Windows medium /media/backup/ltsc.iso is not readable")
    return message_for("build", run_it)


def _start_binary_missing_run():
    # NOT a StepCommandFailed: subprocess.run() raises FileNotFoundError
    # BEFORE any command ever executes when the binary itself is absent
    # (e.g. 'virsh' not on PATH) - classifying_runner only wraps a
    # guest_steps.GuestBuildError (a command that DID run and exited
    # non-zero), so this propagates through run_steps() unwrapped. Round-1
    # review: classify() used to key ONLY on the step name, so this used to
    # be misreported as a hook refusal even though no hook - and no virsh -
    # ever ran. Proves the fix: the step name alone must not be enough.
    def run_it():
        raise FileNotFoundError(2, "No such file or directory", "virsh")
    return message_for("start", run_it)


def _unexpected_exception_run():
    # A totally unnamed failure (neither GuestBuildError nor
    # StepCommandFailed) at a step that is NOT 'start'. This is what
    # run_steps()'s broad `except Exception` exists to catch - narrowed to
    # only GuestBuildError/StepCommandFailed, this exact exception would
    # propagate out of run_steps() as a raw traceback instead of the
    # classified message an operator can read on their first boot.
    def run_it():
        raise RuntimeError("disque disparu pendant la construction")
    return message_for("payload", run_it)


hook_refused = _start_refused_run()
build_panne = _build_panne_run()
refused_mid_step = _refused_input_mid_step_run()
start_binary_missing = _start_binary_missing_run()
unexpected = _unexpected_exception_run()

check("a hook refusing the VM start is reported as such",
      "hook" in hook_refused.lower())
check("and is not presented as a build failure",
      "construction" not in hook_refused.lower())

check("a build panne is reported as a construction failure",
      "construction" in build_panne.lower())
check("and never mentions a hook",
      "hook" not in build_panne.lower())

check("an input refused mid-step is reported as a motivated refusal",
      "entree refusee" in refused_mid_step.lower())
check("not confused with a build panne",
      "construction" not in refused_mid_step.lower())
check("nor with a hook refusal",
      "hook" not in refused_mid_step.lower())

# Round-1 review, point 1: classification of a 'start' failure must look at
# WHAT failed, not just WHICH step failed.
check("a missing virsh binary is NOT presented as a hook refusal",
      "hook" not in start_binary_missing.lower())
check("but the raw error naming the missing binary survives",
      "virsh" in start_binary_missing)
check("a real hook-refusal exit code is STILL reported as one",
      "hook" in _start_refused_run().lower())

# Round-1 review, point 2: run_steps()'s broad `except Exception` must turn
# an utterly unexpected failure into a classified line, not a bare
# traceback - the whole reason it is broad rather than narrowed to the
# exception types this module already knows about.
check("a totally unexpected exception still yields a classified message",
      unexpected.startswith("console activate:"))
check("... naming the step it happened in",
      "'payload'" in unexpected)
check("... and keeping the raw cause readable",
      "disque disparu" in unexpected)

# --- LA COUTURE : le runner DE PRODUCTION traverse l etape 'build' ------- #
#
# Tout le reste de ce fichier pilote run_steps() avec des pas SIMULES, et
# test_console_guest_steps.py plane l etape build avec un faux runner. Entre
# les deux, personne ne faisait passer classifying_runner - le seul runner de
# production - par un vrai plan_steps(). C est exactement la couture par
# laquelle le defaut est passe : build_run() s est mis a appeler
# runner(build_cmd, env=env), guest_steps.default_runner a gagne le
# parametre, classifying_runner non - et la phase activate mourait au 3e des
# 5 pas avec un TypeError, banc au vert. Le double de test etait PLUS
# PERMISSIF que la production, il masquait le desaccord au lieu de le
# reveler.
#
# Ce qui suit est la seule assertion du corpus ou la commande est REELLEMENT
# lancee en sous-processus par le runner de production. L interpreteur est
# un faux (un script shell mis a la place de `python`), jamais le vrai
# build.py : construire une ISO prendrait des minutes et lirait un medium
# qui n existe pas ici.

GUEST_ANSWERS = {
    "ltsc_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
    "admin_password": "motdepasse",
    "apollo_password": "motdepasse2",
    "dedicated_nvme": "/dev/nvme1n1",
    "windows_iso": "/media/inexistant/ltsc.iso",
    "retro": False,
}


def fake_interpreter(directory, exit_code=0):
    """Un `python` de facade : ecrit l ISO demandee, note son TMPDIR, sort.

    Mis a la place de l interpreteur (plan_steps(python=...)), il recoit
    exactement l argv que build_run() a compose et l environnement que le
    runner de production lui a passe - donc il OBSERVE ce que la production
    fait, sans rien construire.
    """
    script = os.path.join(directory, "fauxpython")
    with open(script, "w") as fh:
        fh.write(
            "#!/bin/sh\n"
            "out=\"\"\n"
            "while [ $# -gt 0 ]; do\n"
            "  [ \"$1\" = \"--output\" ] && out=\"$2\"\n"
            "  shift\n"
            "done\n"
            f"[ {exit_code} -ne 0 ] && exit {exit_code}\n"
            "printf '%s' \"$TMPDIR\" > \"$out.tmpdir\"\n"
            ": > \"$out\"\n"
            "chmod 600 \"$out\"\n"
            "exit 0\n")
    os.chmod(script, 0o755)
    return script


def build_step_with(workdir, exit_code=0):
    """L etape 'build' d un VRAI plan, cablee sur le runner de production."""
    steps_mod = activate.guest_steps
    steps_mod.windows_media_path(workdir).write_bytes(b"medium")
    answers = dict(GUEST_ANSWERS, guest_workdir=workdir)
    planned = steps_mod.plan_steps(
        answers, {}, workdir,
        virsh=lambda *a: subprocess.CompletedProcess(list(a), 1, "", ""),
        runner=activate.classifying_runner,          # LE runner de production
        size_of=lambda disk: 2000 * 1024 ** 3,
        pci_address_of=lambda disk: "0000:03:00.0",
        qemu_owner=lambda: (os.getuid(), os.getgid()),
        chown=lambda path, uid, gid: None,
        python=fake_interpreter(workdir, exit_code))
    return {step.name: step for step in planned}["build"]


with tempfile.TemporaryDirectory() as workdir:
    step = build_step_with(workdir)
    raised = None
    try:
        step.run()
    except Exception as exc:                    # noqa: BLE001 - c est l epreuve
        raised = exc
    if raised is not None:
        failures.append("le runner de production n a PAS traverse l etape "
                        f"build : {type(raised).__name__}: {raised}")
    iso = os.path.join(workdir, "nivuus-unattend.iso")
    check("l ISO demandee a bien ete produite par la commande lancee",
          os.path.isfile(iso))
    # Et la tache 2 s execute VRAIMENT : le TMPDIR arrive jusqu au processus.
    tmpdir_seen = ""
    if os.path.isfile(iso + ".tmpdir"):
        tmpdir_seen = open(iso + ".tmpdir").read()
    check("le TMPDIR de la tache 2 atteint reellement le processus lance",
          tmpdir_seen.startswith(workdir + os.sep))
    check("... et ce n est pas le /tmp du systeme", tmpdir_seen != "/tmp")

with tempfile.TemporaryDirectory() as workdir:
    # Et le classement survit au passage : une commande qui sort non-zero
    # reste une 'panne', nommee, jamais une trace.
    step = build_step_with(workdir, exit_code=1)
    message = ""
    try:
        activate.run_steps([step], emit_fn=lambda event: None)
    except activate.ActivationFailure as exc:
        message = str(exc)
    check("un build.py en echec reste classe comme une panne de construction",
          "construction" in message.lower())
    check("... et nomme l etape", "'build'" in message)

# --- une exception depuis already_done() est CLASSEE, pas une trace ------ #
# build_done() a le droit de lever (il reaffirme les droits qemu sur la
# branche "ISO deja a jour", ou rendre False declencherait une
# reconstruction de plusieurs minutes qui ne corrige rien). run_steps()
# n entourait que run() : l exception sortait non classee, main() ne connait
# qu ActivationFailure, et l operateur recevait une trace Python au lieu de
# la ligne unique que ce module promet.
def raising_done_step(name, exc):
    def boom():
        raise exc
    return activate.guest_steps.Step(name, boom, lambda: None, None, None, "")


def classified_done_failure(label, name, exc):
    """Le message classe qu already_done() produit - ou un FAIL nomme.

    Le `except Exception` large n est pas de la prudence decorative : c est
    la falsification elle-meme. Sans lui, retirer le garde de run_steps()
    ferait sortir l exception brute et TUERAIT le fichier a cet endroit -
    une trace, exactement le defaut sous epreuve, au lieu d un FAIL nomme.
    """
    try:
        activate.run_steps([raising_done_step(name, exc)],
                           emit_fn=lambda event: None)
    except activate.ActivationFailure as classified:
        return str(classified)
    except Exception as raw:                    # noqa: BLE001 - c est l epreuve
        failures.append(f"{label} : already_done() a laisse fuir "
                        f"{type(raw).__name__} sans classement ({raw})")
        return ""
    failures.append(f"{label} : rien n a echoue")
    return ""


refused_predicate = classified_done_failure(
    "un refus leve par already_done()", "build",
    activate.guest_steps.GuestBuildError(
        "could not hand /var/lib/nivuus/guest/nivuus-unattend.iso to the "
        "qemu user (uid 64055): Operation not permitted"))
check("un refus leve par already_done() ressort CLASSE",
      refused_predicate.startswith("console activate:"))
check("... nommant l etape ou il s est produit", "'build'" in refused_predicate)
check("... et classe comme un refus motive, pas une panne",
      "entree refusee" in refused_predicate.lower())

surprise = classified_done_failure(
    "une exception inattendue depuis already_done()", "define",
    RuntimeError("libvirtd a disparu"))
check("une exception inattendue depuis already_done() est classee elle aussi",
      surprise.startswith("console activate:") and "libvirtd a disparu" in surprise)

# already_done() is honoured: a step that says it is already done must
# never have its run() called at all.
ran = []
skip_step = activate.guest_steps.Step(
    "define", lambda: True, lambda: ran.append("define"), None, None, "")
activate.run_steps([skip_step], emit_fn=lambda event: None)
check("an already-done step is skipped, never run", ran == [])

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - arming is a real symlink, idempotent, never dangles, starts the "
      "units on the machine it activates, and a step failure is classified "
      "as a motivated refusal, a hook refusal, or a build panne")
