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
# Secrets are baked INTO the image (the product key and the administrator
# password into the answer file, the Apollo password into secrets.psd1), so
# they must move the fingerprint - otherwise `secrets` says "not done" while
# `build` says "done", and the ISO ships the OLD password.
with_key = steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                                   answers={"retro": False, "ltsc_key": "AAA"},
                                   data_gib=800)
check("declarer un secret change l empreinte", with_key == a, False)
check("changer un secret change l empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False, "ltsc_key": "BBB"},
                              data_gib=800) == with_key, False)
# ... but never in clear: the fingerprint file lives next to the ISO.
check("le materiau de l empreinte ne porte pas le secret en clair",
      "AAA" in json.dumps(steps._secret_digest("ltsc_key", "AAA")), False)
check("deux reponses de meme valeur ne se confondent pas",
      steps._secret_digest("ltsc_key", "X") == steps._secret_digest("admin_password", "X"),
      False)

# --- les entrees de construction du paquet ------------------------------ #
# A package upgrade must not reuse an ISO built by the previous version.
check("les entrees de construction changent l empreinte",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=800,
                              build_inputs={"guest/build.py": "d1"}) == a, False)
check("et une entree modifiee aussi",
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=800,
                              build_inputs={"guest/build.py": "d1"}) ==
      steps.build_fingerprint(iso="/m/w.iso", payload_files={"a": "h1"},
                              answers={"retro": False}, data_gib=800,
                              build_inputs={"guest/build.py": "d2"}), False)
real_inputs = steps.package_inputs()
check("le paquet reel declare bien build.py parmi ses entrees",
      "guest/build.py" in real_inputs, True)
check("et le gabarit du fichier de reponses",
      "guest/templates/autounattend.xml.j2" in real_inputs, True)
check("et l arborescence de provisionnement",
      any(k.startswith("guest/provision/") for k in real_inputs), True)
check("aucune entree du paquet ne manque",
      sorted(k for k, v in real_inputs.items() if v == "missing"), [])

# --- le garde-fou de derive de l empreinte ------------------------------- #
# BUILD_INPUT_FILES est une liste EXPLICITE, et une liste explicite est
# precisement ce que l on oublie d etendre en ajoutant un module. L oubli est
# invisible : rien ne casse, l ISO garde son ancienne empreinte, et une mise a
# jour du paquet reutilise une image construite par le code precedent. Ce
# depot a deja paye cette panne une fois, sur une table de placement.
# Donc : chaque .py directement sous guest/ est classe, d un cote ou de
# l autre, et l orphelin est NOMME.
_orphelins = steps.undeclared_guest_modules()
check("chaque module de guest/ est declare ou exclu explicitement",
      _orphelins, [])
if _orphelins:
    failures.append(
        "modules de console/guest/ absents de BUILD_INPUT_FILES comme de "
        f"BUILD_INPUT_EXCLUDED : {', '.join(_orphelins)}. Ajoute chacun aux "
        "entrees s il entre dans l image, ou aux exclusions AVEC sa raison.")
# Les deux listes se contredisent si un nom figure dans les deux : l exclusion
# serait alors une fausse promesse, puisque le fichier est quand meme hache.
check("aucun module n est a la fois entree et exclusion",
      sorted(set(steps.BUILD_INPUT_FILES) & set(steps.BUILD_INPUT_EXCLUDED)), [])
# Une exclusion sans raison ecrite n est pas une decision, c est un oubli
# range. Chacune porte une phrase qu un relecteur peut contester.
check("chaque exclusion porte sa raison",
      sorted(name for name, why in steps.BUILD_INPUT_EXCLUDED.items()
             if not str(why).strip()), [])
# Le garde-fou doit savoir echouer : un module non declare doit ressortir,
# nomme. Sans cette epreuve, une implementation qui rend toujours [] passerait
# pour un garde-fou.
with tempfile.TemporaryDirectory() as _tmp_guest:
    _faux = pathlib.Path(_tmp_guest)
    (_faux / "build.py").write_text("# declare")
    (_faux / "domain.py").write_text("# exclu")
    (_faux / "un_module_neuf.py").write_text("# ni l un ni l autre")
    (_faux / "notes.txt").write_text("pas un module")
    check("le garde-fou nomme le module orphelin",
          steps.undeclared_guest_modules(_faux), ["un_module_neuf.py"])
    (_faux / "un_module_neuf.py").unlink()
    check("et ne voit rien quand tout est classe",
          steps.undeclared_guest_modules(_faux), [])

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


def arg(cmd, flag):
    """The value a flag actually carries - presence alone proves nothing."""
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


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
    check("payload passe --drivers-dir avec le meme repertoire que build",
          arg(payload_cmd, "--drivers-dir"), os.path.join(tmp, "payload"))
    check("payload passe le choix retro explicitement",
          "--no-retro" in payload_cmd, True)

    build_cmd = st["build"].command
    check("build appelle build.py", build_cmd[1].endswith("guest/build.py"), True)
    # Presence is NOT enough: swapping --password-file and
    # --apollo-password-file leaves every flag present, and hands the Windows
    # Administrator the Apollo password. Each flag is pinned to its value.
    secrets_dir = os.path.join(tmp, "secrets")
    expected_args = {
        "--windows-iso": ANSWERS["windows_iso"],
        "--drivers-dir": os.path.join(tmp, "payload"),
        "--output": os.path.join(tmp, "nivuus-unattend.iso"),
        "--key-file": os.path.join(secrets_dir, "windows-ltsc.key"),
        "--password-file": os.path.join(secrets_dir, "windows-admin.pass"),
        "--apollo-password-file": os.path.join(secrets_dir, "apollo-ui.pass"),
        "--apollo-user": "nivuus",
        "--hostname": "NIVUUS-WIN",
        "--data-partition-gb": str(steps.data_partition_gib(DISK_BYTES)),
    }
    for flag, want in expected_args.items():
        check(f"build passe {flag} avec sa valeur", arg(build_cmd, flag), want)
    check("build passe le choix retro explicitement",
          "--no-retro" in build_cmd, True)
    check("build ne met JAMAIS un secret sur la ligne de commande",
          any(ANSWERS[k] in build_cmd
              for k in ("ltsc_key", "admin_password", "apollo_password")), False)
    # The three secret files carry three DIFFERENT paths: a copy/paste that
    # points two flags at one file is caught here even if both files exist.
    check("les trois fichiers de secret sont distincts",
          len({arg(build_cmd, f) for f in ("--key-file", "--password-file",
                                           "--apollo-password-file")}), 3)

    define_cmd = st["define"].command
    check("define appelle domain.py define",
          define_cmd[1].endswith("guest/domain.py") and define_cmd[2] == "define",
          True)
    # `command` est l argv d une machine vierge ; --replace n est ajoute qu au
    # lancement, et seulement si un domaine est reellement la (voir plus bas).
    check("l argv de base ne porte pas --replace",
          "--replace" in define_cmd, False)
    # DEUX medias, et pas n importe lesquels. Le gabarit de production amorce
    # sinon le NVMe, vierge a ce stade : Setup ne demarrerait jamais. Le media
    # officiel est celui qui amorce ; l ISO produite par build.py n est PAS
    # amorcable, c est le media de reponses que Setup lit une fois demarre.
    check("define passe le media Windows officiel",
          arg(define_cmd, "--windows-iso"), ANSWERS["windows_iso"])
    check("define passe l ISO de reponses que build vient de produire",
          arg(define_cmd, "--unattend-iso"),
          os.path.join(tmp, "nivuus-unattend.iso"))
    # Les deux flags ne portent PAS le meme chemin : les intervertir laisse
    # tous les flags presents et produit un domaine qui amorce une ISO non
    # amorcable. C est exactement l erreur que la presence seule ne voit pas.
    check("les deux medias sont deux fichiers differents",
          arg(define_cmd, "--windows-iso") == arg(define_cmd, "--unattend-iso"),
          False)
    check("l ISO de reponses de define est celle que build produit",
          arg(define_cmd, "--unattend-iso"), arg(build_cmd, "--output"))
    check("et le media Windows de define est celui que build consomme",
          arg(define_cmd, "--windows-iso"), arg(build_cmd, "--windows-iso"))
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
    # Rewriting over a file that already exists in 0644 must land in 0600:
    # os.open's mode only applies at CREATION, so the fchmod is what fixes it.
    os.chmod(key_file, 0o644)
    st["secrets"].run()
    check("reecrire par-dessus un fichier trop ouvert le referme",
          os.stat(key_file).st_mode & 0o777, 0o600)
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
    # The one that used to slip through: a new administrator password left
    # `secrets` undone but `build` done, so the ISO shipped the OLD password.
    for secret in ("admin_password", "ltsc_key", "apollo_password"):
        moved = by_name(plan(tmp, FakeVirsh(NO_DOMAIN),
                             {"windows_iso": str(source_iso),
                              secret: "une-toute-autre-valeur"}))
        check(f"changer {secret} force la reconstruction de l ISO",
              moved["build"].already_done(), False)
    # And a package upgrade must not reuse the previous version's ISO.
    upgraded = steps.plan_steps(
        dict(ANSWERS, guest_workdir=tmp, windows_iso=str(source_iso)), {}, tmp,
        virsh=FakeVirsh(NO_DOMAIN), size_of=lambda d: DISK_BYTES,
        build_inputs={"guest/build.py": "une-version-suivante"})
    check("une mise a jour du paquet force la reconstruction de l ISO",
          by_name(upgraded)["build"].already_done(), False)
    # A missing medium cannot be fingerprinted: rebuild, never skip.
    gone = by_name(plan(tmp, FakeVirsh(NO_DOMAIN),
                        {"windows_iso": str(pathlib.Path(tmp) / "absent.iso")}))
    check("un media introuvable ne fait jamais sauter la construction",
          gone["build"].already_done(), False)
    check_raises("et lancer la construction sans media est refuse nommement",
                 steps.GuestBuildError, gone["build"].run)

# define / start: driven entirely by the injected virsh.
#
# « le domaine Windows existe » NE SUFFIT PLUS comme predicat de define, et
# c est le coeur de l affaire : cette etape emet desormais le domaine
# d INSTALLATION, medias attaches. Un domaine Windows preexistant SANS medias
# - le cas nominal d une console qu on reinstalle, et le cas de toute machine
# qui en a deja fait tourner un - ferait sinon sauter l etape, et `start`
# amorcerait un NVMe vierge sans que rien ne le signale.
def installed_xml(tmp, windows_iso=None):
    """Le XML que virsh rendrait pour un domaine d installation correct."""
    windows = windows_iso or ANSWERS["windows_iso"]
    unattend = os.path.join(tmp, "nivuus-unattend.iso")
    return ("<domain><devices>"
            f"<disk device='cdrom'><source file='{windows}'/></disk>"
            f"<disk device='cdrom'><source file='{unattend}'/></disk>"
            "</devices></domain>")


with tempfile.TemporaryDirectory() as tmp:
    v = FakeVirsh(RUNNING)
    st = by_name(plan(tmp, v))
    check("un domaine sans medias ne fait PAS sauter define",
          st["define"].already_done(), False)
    check("start est fait quand le domaine ne dit pas shut off",
          st["start"].already_done(), True)
    check("les predicats interrogent bien virsh sur le domaine",
          ["dumpxml", "Windows"] in v.calls and ["domstate", "Windows"] in v.calls,
          True)

with tempfile.TemporaryDirectory() as tmp:
    st = by_name(plan(tmp, FakeVirsh(OFF)))
    check("un domaine eteint sans medias ne fait sauter ni define ni start",
          (st["define"].already_done(), st["start"].already_done()), (False, False))

# ... et un domaine qui porte DEJA les deux bons medias, lui, est fait.
with tempfile.TemporaryDirectory() as tmp:
    ok = {"dumpxml": (0, installed_xml(tmp)), "domstate": (0, "shut off\n")}
    st = by_name(plan(tmp, FakeVirsh(ok)))
    check("un domaine portant les deux medias fait sauter define",
          st["define"].already_done(), True)

# Un SEUL des deux medias ne suffit pas : c est exactement le domaine a demi
# defini qui installe un ecran de langue et rien d autre.
with tempfile.TemporaryDirectory() as tmp:
    moitie = ("<domain><devices><disk device='cdrom'>"
              f"<source file='{ANSWERS['windows_iso']}'/></disk>"
              "</devices></domain>")
    st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, moitie),
                                      "domstate": (0, "shut off\n")})))
    check("un domaine ne portant qu un seul media n est pas fait",
          st["define"].already_done(), False)

# Un domaine portant les medias d une AUTRE installation (autre ISO source)
# n est pas le notre : il doit etre redefini.
with tempfile.TemporaryDirectory() as tmp:
    autre = {"dumpxml": (0, installed_xml(tmp, "/media/backup/une-autre.iso")),
             "domstate": (0, "shut off\n")}
    st = by_name(plan(tmp, FakeVirsh(autre)))
    check("un domaine portant un autre media source n est pas fait",
          st["define"].already_done(), False)

# --- --replace : seulement quand il y a vraiment quelque chose a remplacer - #
# guard_replace() refuse de redefinir un domaine existant sans lui, donc sans
# cela l etape echouerait sur le cas meme que le predicat vient de declarer
# non fait. Mais il ne doit pas etre passe a l aveugle : sur une machine
# vierge il n y a rien a remplacer.
def lance(tmp, virsh_answers):
    """L argv REELLEMENT lance par define, via un lanceur qui l enregistre."""
    vus = []
    given = dict(ANSWERS, guest_workdir=tmp)
    steps_list = steps.plan_steps(given, {}, tmp, virsh=FakeVirsh(virsh_answers),
                                  runner=vus.append,
                                  size_of=lambda d: DISK_BYTES)
    by_name(steps_list)["define"].run()
    return vus[0]


with tempfile.TemporaryDirectory() as tmp:
    frais = lance(tmp, NO_DOMAIN)
    check("sur une machine vierge, define ne passe pas --replace",
          "--replace" in frais, False)
    perime = lance(tmp, RUNNING)
    check("face a un domaine preexistant, define passe --replace",
          "--replace" in perime, True)
    # Et il garde les deux medias dans les deux cas : --replace s ajoute, il
    # ne remplace rien de la ligne.
    for etiquette, argv in (("vierge", frais), ("preexistant", perime)):
        check(f"define ({etiquette}) garde le media Windows",
              arg(argv, "--windows-iso"), ANSWERS["windows_iso"])
        check(f"define ({etiquette}) garde l ISO de reponses",
              arg(argv, "--unattend-iso"),
              os.path.join(tmp, "nivuus-unattend.iso"))

with tempfile.TemporaryDirectory() as tmp:
    # virsh unreachable: never read as "already done". An unreachable
    # hypervisor read as a transitional state is the 2026-08-24 infinite-wake
    # bug; here the same misreading would silently skip the domain.
    st = by_name(plan(tmp, FakeVirsh({})))
    check("un virsh injoignable ne fait sauter aucune etape",
          (st["define"].already_done(), st["start"].already_done()), (False, False))


# --- les etats du domaine dont il faut SORTIR --------------------------- #
# `in shutdown` and `crashed` are not successes: read as "already started"
# they would leave a crashed guest untouched and the console dark.
for state, up in (("running", True), ("idle", True), ("paused", True),
                  ("pmsuspended", True), ("shut off", False),
                  ("in shutdown", False), ("crashed", False)):
    with tempfile.TemporaryDirectory() as tmp:
        st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, "<domain/>"),
                                          "domstate": (0, state + "\n")})))
        check(f"domstate '{state}' compte comme demarre", st["start"].already_done(), up)


# --- un echec nomme la commande, pas son premier argument ---------------- #
check("un script est nomme par son fichier",
      steps.command_label(["/usr/bin/python3", "/opt/x/guest/build.py", "--a"]),
      "build.py")
check("virsh est nomme virsh, jamais 'start'",
      steps.command_label(["virsh", "start", "Windows"]), "virsh")


# --- la dependance que la chaine appelle vraiment ------------------------ #
# guest/unattend_iso.py runs the `xorriso` binary; without the package, the
# activate phase fetches the whole payload and only then dies at image
# creation. Same class of gap as firewalld and python3-jinja2 before it.
iso_src = pathlib.Path(CONSOLE, "guest", "unattend_iso.py").read_text()
manifest = pathlib.Path(CONSOLE, "nivuus-package.yaml").read_text()
check("unattend_iso.py appelle bien xorriso", '"xorriso"' in iso_src, True)
check("et le manifeste le declare",
      any(line.strip() == "- xorriso" for line in manifest.splitlines()), True)


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
