#!/usr/bin/env python3
"""The five guest-build steps: what they would launch, and what lets them skip.

Nothing here builds an ISO or starts a domain, and that is the point of the
module under test: it CONSTRUCTS command lines instead of running them, so
the whole decision - what to launch, in which order, what may be skipped -
is observable without a gigabyte of I/O and without detaching the GPU from
the host.

Two properties carry the design and are the easiest to break silently:

  * the partition derivation SUBTRACTS what Windows needs, because the games
    partition is the one carrying the fixed size (autounattend puts it first
    and <Extend> only applies to the last partition created). Reversed, a
    small disk yields a tiny C: - a console that installs, then suffocates on
    its first Windows update;
  * an unreadable fingerprint file means REBUILD, never skip. Rebuilding by
    mistake costs twenty minutes; skipping by mistake ships a console that
    does not match the answers that were given.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE = os.path.dirname(HERE)
MODULE = os.path.join(CONSOLE, "guest_steps.py")

spec = importlib.util.spec_from_file_location("guest_steps", MODULE)
steps = importlib.util.module_from_spec(spec)
sys.path.insert(0, CONSOLE)
# Registered before exec: @dataclass resolves annotations through
# sys.modules[cls.__module__], which is None for an unregistered module.
sys.modules["guest_steps"] = steps
spec.loader.exec_module(steps)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001 - a bare traceback is the failure
        failures.append(f"{label}: raised {type(exc).__name__} instead of "
                        f"{exc_type.__name__}")
        return
    failures.append(f"{label}: raised nothing")


# --- la derivation, dans le bon sens ------------------------------------- #
# The GAMES partition carries the fixed size and Windows takes the rest, so
# the derivation SUBTRACTS what Windows needs. Getting this backwards yields
# a tiny C: on a small disk - a console that installs, then suffocates on its
# first Windows update.
GIB = 1024 ** 3
check("un disque de 1 To laisse la place aux jeux",
      steps.data_partition_gib(1000 * GIB) < 1000 - 120, True)
check("et en garde assez pour Windows",
      1000 - steps.data_partition_gib(1000 * GIB) >= 120, True)
check("un disque de 500 Go donne une partition plus petite",
      steps.data_partition_gib(500 * GIB) < steps.data_partition_gib(1000 * GIB),
      True)
check_raises("un disque trop petit est refuse", steps.GuestBuildError,
             lambda: steps.data_partition_gib(100 * GIB))

# A refusal names its cause: an operator reads the message, not a traceback.
try:
    steps.data_partition_gib(100 * GIB)
except steps.GuestBuildError as exc:
    check("le refus nomme la taille du disque", "100" in str(exc), True)

# --- l empreinte -------------------------------------------------------- #
# Two builds agree only if what ENTERS the image is the same. Dates are not
# part of it: the payload gets touched without the ISO needing a rebuild.
a = steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                            answers={"retro": False}, data_gib=800)
b = steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                            answers={"retro": True}, data_gib=800)
check("une reponse differente change l empreinte", a == b, False)
check("la meme entree rend la meme empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=800) == a, True)
check("un media different change l empreinte",
      steps.build_fingerprint(iso="/m/other.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=800) == a, False)
check("un payload different change l empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h2"},
                              answers={"retro": False}, data_gib=800) == a, False)
check("une taille de partition differente change l empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=700) == a, False)
# Secrets shape the guest but must not be hashed alongside the rest: the
# `secrets` step has its own predicate, comparing file content to the answer.
check("un secret ne rentre pas dans l empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False, "ltsc_key": "AAA"},
                              data_gib=800) == a, True)

# --- un fichier d empreinte illisible veut dire RECONSTRUIRE ------------- #
with tempfile.TemporaryDirectory() as tmp:
    stamp = pathlib.Path(tmp) / "build.fingerprint"
    stamp.write_text("{ceci n est pas du json")
    check("une empreinte illisible ne fait jamais sauter la construction",
          steps.build_is_current(str(stamp), a), False)
    check("une empreinte absente ne fait jamais sauter la construction",
          steps.build_is_current(str(pathlib.Path(tmp) / "nowhere"), a), False)
    # Valid JSON, wrong shape: a list, then a document with no fingerprint.
    stamp.write_text("[]")
    check("une empreinte de mauvaise forme reconstruit",
          steps.build_is_current(str(stamp), a), False)
    stamp.write_text(json.dumps({"built": "2026-08-28"}))
    check("une empreinte sans le champ attendu reconstruit",
          steps.build_is_current(str(stamp), a), False)
    steps.write_build_stamp(str(stamp), b)
    check("une empreinte qui ne correspond pas reconstruit",
          steps.build_is_current(str(stamp), a), False)
    steps.write_build_stamp(str(stamp), a)
    check("une empreinte identique autorise le saut",
          steps.build_is_current(str(stamp), a), True)
    # A directory is neither missing nor parseable: still "rebuild".
    check("un chemin qui n est pas un fichier reconstruit",
          steps.build_is_current(tmp, a), False)


# --- les cinq etapes, leurs commandes et leurs predicats ----------------- #
ANSWERS = {
    "dedicated_nvme": "/dev/nvme1n1",
    "retro": False,
    "admin_password": "motdepasse-admin",
    "windows_iso": "/media/backup/ltsc.iso",
    "ltsc_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
    "apollo_password": "motdepasse-apollo",
    "guest_workdir": "/var/lib/nivuus/guest",
}
DISK_BYTES = 1000 * GIB


class FakeVirsh:
    """A virsh that answers from a script instead of talking to libvirtd.

    Injected, never patched globally: running the real thing here would
    read - and, for `start`, drive - the production domain.
    """

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, *args):
        self.calls.append(list(args))
        rc, out = self.answers.get(args[0], (1, ""))
        return subprocess.CompletedProcess(list(args), rc, out, "")


def plan(tmp, virsh, answers=None, disk_bytes=DISK_BYTES):
    given = dict(ANSWERS)
    given.update(answers or {})
    given["guest_workdir"] = tmp
    return steps.plan_steps(given, {}, tmp, virsh=virsh,
                            size_of=lambda device: disk_bytes)


def by_name(plan_list):
    return {s.name: s for s in plan_list}


NO_DOMAIN = {"dumpxml": (1, ""), "domstate": (1, "")}
RUNNING = {"dumpxml": (0, "<domain/>"), "domstate": (0, "running\n")}
OFF = {"dumpxml": (0, "<domain/>"), "domstate": (0, "shut off\n")}

with tempfile.TemporaryDirectory() as tmp:
    plan_list = plan(tmp, FakeVirsh(NO_DOMAIN))
    check("les cinq etapes, dans l ordre",
          [s.name for s in plan_list],
          ["secrets", "payload", "build", "define", "start"])

    st = by_name(plan_list)

    # Nothing exists yet: no step may be skipped.
    for name in ("secrets", "payload", "build", "define", "start"):
        check(f"{name} n est pas fait quand rien n existe",
              st[name].already_done(), False)

    # The command lines are built, never launched. Each flag below is a real
    # add_argument of the script it targets - checked against the source, not
    # invented (build.py reads the product key from a FILE, on purpose).
    payload_cmd = st["payload"].command
    check("payload appelle fetch_payload.py",
          payload_cmd[1].endswith("guest/fetch_payload.py"), True)
    check("payload passe --drivers-dir", "--drivers-dir" in payload_cmd, True)
    check("payload passe le choix retro explicitement",
          "--no-retro" in payload_cmd, True)

    build_cmd = st["build"].command
    check("build appelle build.py", build_cmd[1].endswith("guest/build.py"), True)
    for flag in ("--windows-iso", "--drivers-dir", "--output", "--key-file",
                 "--password-file", "--apollo-password-file", "--apollo-user",
                 "--data-partition-gb", "--hostname", "--no-retro"):
        check(f"build passe {flag}", flag in build_cmd, True)
    check("build ne met JAMAIS un secret sur la ligne de commande",
          any(ANSWERS[k] in build_cmd
              for k in ("ltsc_key", "admin_password", "apollo_password")), False)
    check("build dimensionne la partition de jeux depuis le disque reel",
          build_cmd[build_cmd.index("--data-partition-gb") + 1],
          str(steps.data_partition_gib(DISK_BYTES)))

    define_cmd = st["define"].command
    check("define appelle domain.py define",
          define_cmd[1].endswith("guest/domain.py") and define_cmd[2] == "define",
          True)
    check("define ne passe pas --replace, qui jetterait la session hibernee",
          "--replace" in define_cmd, False)
    check("start demande virsh start sur le domaine de production",
          st["start"].command, ["virsh", "start", "Windows"])

    # secrets: present, 0600 and matching the answers.
    st["secrets"].run()
    check("secrets est fait une fois les fichiers ecrits",
          st["secrets"].already_done(), True)
    key_file = build_cmd[build_cmd.index("--key-file") + 1]
    check("le fichier de cle est en 0600",
          os.stat(key_file).st_mode & 0o777, 0o600)
    check("le fichier de cle porte la reponse",
          pathlib.Path(key_file).read_text().strip(), ANSWERS["ltsc_key"])
    # A weakened mode is not "done": build.py refuses a readable secret file.
    os.chmod(key_file, 0o644)
    check("un secret lisible par le groupe doit etre reecrit",
          st["secrets"].already_done(), False)
    os.chmod(key_file, 0o600)
    # A stale value is not "done" either: the answer changed, the file must.
    other = by_name(plan(tmp, FakeVirsh(NO_DOMAIN), {"ltsc_key": "ZZZZZ"}))
    check("un secret perime doit etre reecrit",
          other["secrets"].already_done(), False)

    # payload: the predicate is deliberately shallow - fetch_payload.py is
    # offline-first and cheap to replay, so an existing non-empty tree is
    # enough. An empty directory is not.
    payload_dir = pathlib.Path(payload_cmd[payload_cmd.index("--drivers-dir") + 1])
    payload_dir.mkdir(parents=True, exist_ok=True)
    check("un repertoire de payload vide n est pas fait",
          st["payload"].already_done(), False)
    (payload_dir / "nvidia").mkdir()
    (payload_dir / "nvidia" / "driver.exe").write_text("x")
    check("un payload peuple est fait", st["payload"].already_done(), True)

    # build: the ISO alone never suffices - the fingerprint must agree.
    iso_out = pathlib.Path(build_cmd[build_cmd.index("--output") + 1])
    source_iso = pathlib.Path(tmp) / "source.iso"
    source_iso.write_bytes(b"windows medium")
    fresh = by_name(plan(tmp, FakeVirsh(NO_DOMAIN), {"windows_iso": str(source_iso)}))
    fresh_cmd = fresh["build"].command
    iso_out = pathlib.Path(fresh_cmd[fresh_cmd.index("--output") + 1])
    iso_out.parent.mkdir(parents=True, exist_ok=True)
    iso_out.write_bytes(b"iso")
    check("une ISO sans empreinte doit etre reconstruite",
          fresh["build"].already_done(), False)
    steps.write_build_stamp(steps.build_stamp_path(str(iso_out)),
                            fresh["build"].fingerprint())
    check("une ISO avec l empreinte attendue peut etre sautee",
          fresh["build"].already_done(), True)
    # Change one answer that shapes the image: the stamp no longer applies.
    changed = by_name(plan(tmp, FakeVirsh(NO_DOMAIN),
                           {"windows_iso": str(source_iso), "retro": True}))
    check("changer une reponse invalide l empreinte",
          changed["build"].already_done(), False)
    # A missing medium cannot be fingerprinted: rebuild, never skip.
    gone = by_name(plan(tmp, FakeVirsh(NO_DOMAIN),
                        {"windows_iso": str(pathlib.Path(tmp) / "absent.iso")}))
    check("un media introuvable ne fait jamais sauter la construction",
          gone["build"].already_done(), False)
    check_raises("et lancer la construction sans media est refuse nommement",
                 steps.GuestBuildError, gone["build"].run)

# define / start: driven entirely by the injected virsh.
with tempfile.TemporaryDirectory() as tmp:
    v = FakeVirsh(RUNNING)
    st = by_name(plan(tmp, v))
    check("define est fait quand virsh dumpxml repond",
          st["define"].already_done(), True)
    check("start est fait quand le domaine ne dit pas shut off",
          st["start"].already_done(), True)
    check("les predicats interrogent bien virsh sur le domaine",
          ["dumpxml", "Windows"] in v.calls and ["domstate", "Windows"] in v.calls,
          True)

with tempfile.TemporaryDirectory() as tmp:
    st = by_name(plan(tmp, FakeVirsh(OFF)))
    check("define est fait mais start ne l est pas sur un domaine eteint",
          (st["define"].already_done(), st["start"].already_done()), (True, False))

with tempfile.TemporaryDirectory() as tmp:
    # virsh unreachable: never read as "already done". An unreachable
    # hypervisor read as a transitional state is the 2026-08-24 infinite-wake
    # bug; here the same misreading would silently skip the domain.
    st = by_name(plan(tmp, FakeVirsh({})))
    check("un virsh injoignable ne fait sauter aucune etape",
          (st["define"].already_done(), st["start"].already_done()), (False, False))


# --- les refus nomment leur cause --------------------------------------- #
with tempfile.TemporaryDirectory() as tmp:
    check_raises("un secret vide est refuse", steps.GuestBuildError,
                 lambda: plan(tmp, FakeVirsh(NO_DOMAIN), {"ltsc_key": "   "}))
    try:
        plan(tmp, FakeVirsh(NO_DOMAIN), {"apollo_password": ""})
    except steps.GuestBuildError as exc:
        check("le refus nomme le secret manquant",
              "apollo_password" in str(exc), True)

    check_raises("un disque non declare est refuse", steps.GuestBuildError,
                 lambda: plan(tmp, FakeVirsh(NO_DOMAIN), {"dedicated_nvme": ""}))
    check_raises("un media non declare est refuse", steps.GuestBuildError,
                 lambda: plan(tmp, FakeVirsh(NO_DOMAIN), {"windows_iso": ""}))

    # A disk sysfs cannot size is a refusal too, not a crash - and it names
    # the device so an operator knows which one.
    def unsized(device):
        raise steps.GuestBuildError(f"cannot size {device}")

    try:
        steps.plan_steps(dict(ANSWERS), {}, tmp, virsh=FakeVirsh(NO_DOMAIN),
                         size_of=unsized)
    except steps.GuestBuildError as exc:
        check("le refus nomme le disque", "nvme1n1" in str(exc), True)

    # hw may already carry the size; sysfs is then not consulted at all.
    def forbidden(device):
        raise AssertionError("sysfs must not be read when hw already knows")

    sized = steps.plan_steps(dict(ANSWERS, guest_workdir=tmp),
                             {"dedicated_nvme_size_bytes": 500 * GIB}, tmp,
                             virsh=FakeVirsh(NO_DOMAIN), size_of=forbidden)
    cmd = by_name(sized)["build"].command
    check("hw fournit la taille quand il la connait",
          cmd[cmd.index("--data-partition-gb") + 1],
          str(steps.data_partition_gib(500 * GIB)))


# --- l arborescence du payload entre dans l empreinte -------------------- #
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "apollo").mkdir()
    (root / "apollo" / "setup.exe").write_bytes(b"one")
    tree = steps.payload_tree(str(root))
    check("l arborescence porte un chemin relatif",
          list(tree), ["apollo/setup.exe"])
    check("et le condensat du contenu",
          tree["apollo/setup.exe"], hashlib.sha256(b"one").hexdigest())
    (root / "apollo" / "setup.exe").write_bytes(b"two")
    check("un contenu modifie change l arborescence",
          steps.payload_tree(str(root)) == tree, False)


if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - five steps, each skippable on an observation, each command built "
      "and none launched")
