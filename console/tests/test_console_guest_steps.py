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
# ANSWERS["windows_iso"] is read ONLY for its non-emptiness now (via
# require_windows_iso_answer) - plan_steps points every step at
# windows_media_path(guest_workdir) instead, never at this literal. It is
# kept as a plausible-looking path purely so a stray print reads sensibly.
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


# The physical NVMe address ANSWERS["dedicated_nvme"] ("/dev/nvme1n1")
# resolves to by default in this suite - measured on the real production
# domain (2026-08-28: `virsh dumpxml Windows` shows this exact address on
# the NVMe hostdev). Kept as the default so most tests never have to think
# about it; the identity tests below deliberately point elsewhere.
DEFAULT_NVME_PCI = "0000:03:00.0"


def _default_pci_address_of(_disk):
    return DEFAULT_NVME_PCI


def _inert_chown(path, uid, gid):
    """The default `chown` for plan(): a no-op. build_done()'s True branch
    now reasserts qemu ownership on every call (see guest_steps.py's
    ensure_qemu_owned) - without this, every test in the file that reaches
    that branch through plan() would fall back to the REAL os.chown and the
    REAL /var/lib/libvirt/qemu, which happens to work only because THIS
    particular test process runs as root on the real host (see CLAUDE.md's
    'sessions run as root on the live server') and would break on any other
    machine. Tests that actually want to OBSERVE the chown calls build their
    own steps.plan_steps(...) directly and inject their own recorder -
    see RecordingChown below."""


def plan(tmp, virsh, answers=None, disk_bytes=DISK_BYTES, pci_address_of=None):
    # disk_mode is stated EXPLICITLY here, and every test using this helper
    # inherits it, because none of them is about the disk mode: an implicit
    # "wipe" against a domain already wired to this disk is refused outright
    # (refuse_implicit_wipe), which would otherwise turn most of this file
    # into that one refusal. The tests that ARE about the mode build their
    # answers by hand and deliberately leave the key out.
    given = dict(ANSWERS, disk_mode="wipe")
    given.update(answers or {})
    given["guest_workdir"] = tmp
    return steps.plan_steps(given, {}, tmp, virsh=virsh,
                            size_of=lambda device: disk_bytes,
                            pci_address_of=pci_address_of or _default_pci_address_of,
                            qemu_owner=lambda: (0, 0), chown=_inert_chown)


def by_name(plan_list):
    return {s.name: s for s in plan_list}


def arg(cmd, flag):
    """The value a flag actually carries - presence alone proves nothing."""
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


# --- le temoin du payload est COPIE de fetch_payload.py, jamais devine --- #
# guest_steps.py ne peut pas importer fetch_payload.py (regle du module :
# rien sous console/guest/ n est importable depuis la phase activate, ces
# fichiers importent jinja2). Le chemin est donc recopie - et une copie qui
# derive silencieusement redonne exactement le defaut corrige ici : une
# etape payload sautee alors qu agent.exe manque. Cette assertion lit la
# SOURCE de fetch_payload.py et exige que le litteral y soit encore.
_FETCH_SRC = pathlib.Path(CONSOLE, "guest", "fetch_payload.py").read_text()
check("le temoin du payload est bien agent/agent.exe",
      str(steps.PAYLOAD_WITNESS), os.path.join("agent", "agent.exe"))
check("et fetch_payload.py depose TOUJOURS agent.exe a cet endroit",
      'drivers_dir / "agent" / "agent.exe"' in _FETCH_SRC, True)


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
        # The copy under the workdir, not ANSWERS["windows_iso"]: see the
        # comment on that key above and on source_iso in plan_steps itself.
        "--windows-iso": str(steps.windows_media_path(tmp)),
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
          arg(define_cmd, "--windows-iso"), str(steps.windows_media_path(tmp)))
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

    # payload: the predicate stays shallow - fetch_payload.py is offline-first
    # and cheap to replay - but shallow is NOT "any file at all". It names one
    # witness, agent.exe, because that is the piece an INHERITED payload/ tree
    # (produced by an older fetch_payload.py, on a host that has run this
    # before) cannot have: agent.exe is copied out of the package itself, it
    # is not downloaded. "Any file" accepted such a tree, skipped the step,
    # and left step 40 of the provisioning with no agent to install.
    payload_dir = pathlib.Path(payload_cmd[payload_cmd.index("--drivers-dir") + 1])
    payload_dir.mkdir(parents=True, exist_ok=True)
    check("un repertoire de payload vide n est pas fait",
          st["payload"].already_done(), False)
    (payload_dir / "nvidia").mkdir()
    (payload_dir / "nvidia" / "driver.exe").write_text("x")
    check("UN ARBRE PAYLOAD HERITE, PEUPLE MAIS SANS agent.exe, N EST PAS FAIT",
          st["payload"].already_done(), False)
    witness = payload_dir / steps.PAYLOAD_WITNESS
    witness.parent.mkdir(parents=True, exist_ok=True)
    check("un repertoire agent/ vide ne suffit pas non plus",
          st["payload"].already_done(), False)
    witness.write_text("MZ")
    check("un payload portant agent.exe est fait", st["payload"].already_done(), True)

    # build: the ISO alone never suffices - the fingerprint must agree.
    # The COPY console/hooks/install.py places under the workdir is what
    # this step reads now, never the wizard's raw 'windows_iso' answer - so
    # the fixture writes bytes at windows_media_path(tmp), the fixed
    # convention, instead of at a path the answer used to be free to name.
    iso_out = pathlib.Path(build_cmd[build_cmd.index("--output") + 1])
    medium = steps.windows_media_path(tmp)
    medium.write_bytes(b"windows medium")
    fresh = by_name(plan(tmp, FakeVirsh(NO_DOMAIN)))
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
    changed = by_name(plan(tmp, FakeVirsh(NO_DOMAIN), {"retro": True}))
    check("changer une reponse invalide l empreinte",
          changed["build"].already_done(), False)
    # The one that used to slip through: a new administrator password left
    # `secrets` undone but `build` done, so the ISO shipped the OLD password.
    for secret in ("admin_password", "ltsc_key", "apollo_password"):
        moved = by_name(plan(tmp, FakeVirsh(NO_DOMAIN),
                             {secret: "une-toute-autre-valeur"}))
        check(f"changer {secret} force la reconstruction de l ISO",
              moved["build"].already_done(), False)
    # And a package upgrade must not reuse the previous version's ISO.
    upgraded = steps.plan_steps(
        dict(ANSWERS, guest_workdir=tmp), {}, tmp,
        virsh=FakeVirsh(NO_DOMAIN), size_of=lambda d: DISK_BYTES,
        build_inputs={"guest/build.py": "une-version-suivante"})
    check("une mise a jour du paquet force la reconstruction de l ISO",
          by_name(upgraded)["build"].already_done(), False)
    # A missing medium cannot be fingerprinted: rebuild, never skip. The
    # answer no longer selects the file read, so the fixture is removed
    # instead of pointing 'windows_iso' at a nonexistent path.
    medium.unlink()
    gone = by_name(plan(tmp, FakeVirsh(NO_DOMAIN)))
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
def nvme_hostdev(pci_address=DEFAULT_NVME_PCI):
    """Le fragment <hostdev> mesure sur le domaine de production reel
    (2026-08-28, `virsh dumpxml Windows`) : la SOURCE porte l adresse PCI
    HOTE, sans l attribut 'type' que libvirt ajoute a l adresse INVITE
    (alias/bus virtuel) qui suit - deux elements distincts, et seul le
    premier dit quel disque PHYSIQUE est passe a la VM.
    """
    domain, bus, rest = pci_address.split(":")
    slot, func = rest.split(".")
    return ("<hostdev mode='subsystem' type='pci' managed='yes'>"
            "<driver name='vfio'/><source>"
            f"<address domain='0x{domain}' bus='0x{bus}' slot='0x{slot}' "
            f"function='0x{func}'/>"
            "</source><alias name='hostdev0'/>"
            "<address type='pci' domain='0x0000' bus='0x07' slot='0x00' "
            "function='0x0'/></hostdev>")


def installed_xml(tmp, windows_iso=None, nvme_pci=DEFAULT_NVME_PCI):
    """Le XML que virsh rendrait pour un domaine d installation correct."""
    windows = windows_iso or str(steps.windows_media_path(tmp))
    unattend = os.path.join(tmp, "nivuus-unattend.iso")
    return ("<domain><devices>"
            f"<disk device='cdrom'><source file='{windows}'/></disk>"
            f"<disk device='cdrom'><source file='{unattend}'/></disk>"
            f"{nvme_hostdev(nvme_pci)}"
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
              f"<source file='{steps.windows_media_path(tmp)}'/></disk>"
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


# --- defaut 2 : les chemins d ISO sont FIXES, changer 'dedicated_nvme' ---
# reconstruit l ISO mais ne change ni source_iso ni iso_out - le predicat
# doit donc verifier le disque REELLEMENT branche, pas seulement les medias.
AUTRE_NVME = "0000:02:00.0"  # une autre carte NVMe, mesuree sur le meme hote

with tempfile.TemporaryDirectory() as tmp:
    xml = installed_xml(tmp, nvme_pci=AUTRE_NVME)
    st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, xml),
                                      "domstate": (0, "shut off\n")})))
    check("les deux medias sont bons mais le NVMe est celui d avant : "
          "define n est PAS fait (defaut 2)",
          st["define"].already_done(), False)


# --- defaut 1 : le domaine de regime (sans medias, redefini par
# guest-ready-watch.py une fois l invite pret) est un etat TERMINAL
# legitime, pas un travail a refaire - a condition que le disque soit le bon.
with tempfile.TemporaryDirectory() as tmp:
    regime = "<domain><devices>" + nvme_hostdev() + "</devices></domain>"
    st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, regime),
                                      "domstate": (0, "shut off\n")})))
    check("domaine de regime (sans medias) avec le bon disque : define est "
          "fait - sinon la phase rejoue a chaque activation, pour toujours "
          "(defaut 1)",
          st["define"].already_done(), True)

# Le meme domaine de regime, mais avec le MAUVAIS disque, n est PAS un etat
# terminal pour autant : ce n est le cas que si le disque correspond.
with tempfile.TemporaryDirectory() as tmp:
    regime_autre = "<domain><devices>" + nvme_hostdev(AUTRE_NVME) + "</devices></domain>"
    st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, regime_autre),
                                      "domstate": (0, "shut off\n")})))
    check("domaine de regime avec le mauvais disque n est pas fait",
          st["define"].already_done(), False)

# L identite ne peut pas etre verifiee (adresse PCI illisible) : lu comme un
# desaccord, jamais comme un laissez-passer - meme regle que l empreinte du
# build ("cannot tell => rebuild, never skip", voir le docstring du module).
with tempfile.TemporaryDirectory() as tmp:
    regime = "<domain><devices>" + nvme_hostdev() + "</devices></domain>"
    st = by_name(plan(tmp, FakeVirsh({"dumpxml": (0, regime),
                                      "domstate": (0, "shut off\n")}),
                      pci_address_of=lambda disk: None))
    check("une adresse PCI illisible n est jamais lue comme une correspondance",
          st["define"].already_done(), False)


# --- les fonctions pures elles-memes, directement --------------------------
check("hostdev_source_addresses lit l adresse HOTE, pas l adresse invite",
      steps.hostdev_source_addresses(nvme_hostdev()), {DEFAULT_NVME_PCI})
check("l adresse invite (bus 0x07, tamponnee par libvirt) n est jamais "
      "confondue avec l adresse hote",
      "0000:07:00.0" in steps.hostdev_source_addresses(nvme_hostdev()), False)
check("domain_matches_disk compare correctement",
      steps.domain_matches_disk(nvme_hostdev(), "/dev/nvme1n1",
                                pci_address_of=lambda d: DEFAULT_NVME_PCI), True)
check("domain_matches_disk rend False sur un disque different",
      steps.domain_matches_disk(nvme_hostdev(), "/dev/nvme1n1",
                                pci_address_of=lambda d: AUTRE_NVME), False)
check("domain_matches_disk rend False quand l adresse est introuvable",
      steps.domain_matches_disk(nvme_hostdev(), "/dev/nvme1n1",
                                pci_address_of=lambda d: None), False)
check("un domaine sans le moindre hostdev ne rend aucune adresse",
      steps.hostdev_source_addresses("<domain><devices/></domain>"), set())

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
              arg(argv, "--windows-iso"), str(steps.windows_media_path(tmp)))
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


# --- l aide au demarrage : KEY_ENTER pour passer l invite du media --------
# Sans elle, mesure sur la console reelle le 2026-08-28 : 42 MINUTES sur
# "Press any key to boot from CD or DVD......", disque fige a 194 Ko, puis
# "BdsDxe: No bootable option or device was found" - Setup ne demarre
# jamais. C est ce defaut precis que start_run() corrige.
#
# runner EST TOUJOURS un faux ici, jamais steps.default_runner : celui-ci
# lancerait un vrai `virsh start Windows` en sous-processus, exactement
# l appel interdit contre le domaine de production (voir la consigne de la
# tache).
class Unbounded(Exception):
    """Le faux virsh a ete appele plus que de raison."""


class BoundedVirsh(FakeVirsh):
    """FakeVirsh, mais qui REFUSE au-dela d un plafond.

    Sans ce plafond, la falsification qui compte ici - retirer la borne de
    send_boot_keys - ne fait pas ECHOUER la suite, elle la FIGE : le `sleep`
    injecte rend la main aussitot, donc une boucle non bornee tourne pour
    toujours. La signature au dehors est alors un `rc=124` de delai depasse,
    strictement indiscernable d une contention d environnement (il y en a
    eu cinq dans la meme journee). Un plafond transforme cet enlisement en
    un FAIL nomme, ce qu une epreuve doit produire.
    """

    LIMIT = 4 * 12 + 20         # 4x la borne attendue, plus les lectures d etat

    def __call__(self, *args):
        if len(self.calls) >= self.LIMIT:
            raise Unbounded(f"plus de {self.LIMIT} appels virsh")
        return super().__call__(*args)


def _install_domain_xml(tmp):
    """Le XML du domaine d INSTALLATION : il porte les DEUX medias.

    C est ce que l etape `define` produit, et c est le seul etat ou une
    frappe a un sens - le firmware est sur "Press any key to boot from CD or
    DVD......". Un domaine de regime (console provisionnee, hibernee) ne
    porte plus aucun des deux : voir redefine_steady_state().
    """
    return ("<domain><devices>"
            f"<disk><source file='{steps.windows_media_path(tmp)}'/></disk>"
            f"<disk><source file='{pathlib.Path(tmp, 'nivuus-unattend.iso')}'/></disk>"
            "</devices></domain>")


def _run_start(tmp, virsh_answers):
    """Lance l etape start avec des faux virsh/runner/sleep, et rend les
    appels observes : (calls du virsh, argv lances par le runner,
    duree de chaque pause)."""
    fake_virsh = BoundedVirsh(virsh_answers)
    launched = []
    slept = []
    given = dict(ANSWERS, guest_workdir=tmp)
    st = by_name(steps.plan_steps(
        given, {}, tmp, virsh=fake_virsh, runner=launched.append,
        size_of=lambda d: DISK_BYTES, pci_address_of=_default_pci_address_of,
        qemu_owner=lambda: (0, 0), chown=_inert_chown, sleep=slept.append))
    try:
        st["start"].run()
    except Unbounded as exc:
        failures.append(f"l envoi de frappes n est pas borne ({exc}) : une "
                        "boucle sans borne finirait par frapper Windows Setup")
    return fake_virsh.calls, launched, slept


def off_and_keyable(tmp):
    return {"dumpxml": (0, _install_domain_xml(tmp)),
            "domstate": (0, "shut off\n"), "send-key": (0, "")}


with tempfile.TemporaryDirectory() as tmp:
    calls, launched, slept = _run_start(tmp, off_and_keyable(tmp))
    send_key_calls = [c for c in calls if c[0] == "send-key"]

    check("virsh start est bien lance", launched, [["virsh", "start", "Windows"]])
    check("start envoie KEY_ENTER apres avoir demarre un domaine eteint",
          len(send_key_calls) > 0, True)
    # BORNE, exactement - pas "au moins", pas "a peu pres". Un envoi non
    # borne finirait par frapper Windows Setup une fois l invite passee
    # (voir recette-b.md : un ENTER de trop y a valide un bouton Annuler
    # focus et gele une construction a 8%).
    check("l envoi est borne A EXACTEMENT BOOT_KEY_ATTEMPTS frappes",
          len(send_key_calls), steps.BOOT_KEY_ATTEMPTS)
    check("BOOT_KEY_ATTEMPTS vaut 12, comme la recette acceptee",
          steps.BOOT_KEY_ATTEMPTS, 12)
    check("chaque frappe cible le bon domaine, codeset linux, KEY_ENTER",
          all(c == ["send-key", steps.DOMAIN_NAME, "--codeset", "linux",
                    "KEY_ENTER"] for c in send_key_calls), True)
    check("une pause separe chaque frappe, autant que de frappes",
          len(slept), steps.BOOT_KEY_ATTEMPTS)
    # Les DEUX lectures (les medias portes, puis l etat) precedent toute
    # frappe : aucune ENTER n arrive avant que la question ait ete posee.
    # Ecrit sans indexer en dur - retirer send_boot_keys ferait sortir un
    # `calls[1]` en IndexError, c est-a-dire une trace, pas un FAIL nomme.
    kinds = [c[0] for c in calls]
    check("les lectures precedent la premiere frappe",
          kinds[:kinds.index("send-key")] if "send-key" in kinds else None,
          ["dumpxml", "domstate"])

with tempfile.TemporaryDirectory() as tmp:
    # Le domaine tournait DEJA : run() ne doit envoyer AUCUNE frappe. Une
    # ENTER dans une session vivante irait a ce qui s y joue, pas a un
    # firmware. Ce test appelle .run() DIRECTEMENT, sans passer par
    # already_done() (que le pipeline reel consulte avant d appeler run())
    # - c est le garde-fou interne a start_run() qui est ici sous epreuve,
    # pas seulement le comportement du pipeline autour de lui.
    running_and_keyable = {"dumpxml": (0, _install_domain_xml(tmp)),
                           "domstate": (0, "running\n"), "send-key": (0, "")}
    calls, launched, slept = _run_start(tmp, running_and_keyable)
    send_key_calls = [c for c in calls if c[0] == "send-key"]
    check("un domaine deja demarre ne recoit AUCUNE frappe",
          send_key_calls, [])
    check("et rien n a dormi entre des frappes qui n existent pas",
          slept, [])

# --- LA CONSOLE HIBERNEE : `shut off`, et pourtant une session vivante ---- #
# Toute la strategie energetique de cet hote repose sur S4 : vm-idle-shutdown.sh
# hiberne l invite avec `shutdown /h /f`, et libvirt rapporte alors EXACTEMENT
# `shut off` - le meme mot que pour un domaine qui n a jamais demarre. Verifie
# sur la VM de production le 2026-08-28, hibernee avec la session de son
# proprietaire dedans. `virsh start` la REPREND. Le seul garde-fou `was_up`
# etait donc defait par un etat que rien ne distingue.
# Ce qui les separe : les medias portes. La console de regime (provisionnee,
# puis hibernee) n en porte AUCUN - redefine_steady_state() les retire.
with tempfile.TemporaryDirectory() as tmp:
    hibernated = {"dumpxml": (0, "<domain><devices/></domain>"),
                  "domstate": (0, "shut off\n"), "send-key": (0, "")}
    calls, launched, slept = _run_start(tmp, hibernated)
    check("une console hibernee est bien redemarree", launched,
          [["virsh", "start", "Windows"]])
    check("MAIS ELLE NE RECOIT AUCUNE FRAPPE : la session de son "
          "proprietaire est dedans",
          [c for c in calls if c[0] == "send-key"], [])
    check("et rien n a dormi pour des frappes qui n existent pas", slept, [])

with tempfile.TemporaryDirectory() as tmp:
    # Le cas intermediaire : un domaine qui ne porte QU UN des deux medias
    # n est pas un domaine d installation. Rien ne prouve qu il soit sur
    # l invite du firmware, donc pas de frappe.
    half = {"dumpxml": (0, "<domain><devices><disk><source file='"
                        f"{steps.windows_media_path(tmp)}'/></disk></devices></domain>"),
            "domstate": (0, "shut off\n"), "send-key": (0, "")}
    calls, launched, slept = _run_start(tmp, half)
    check("un domaine ne portant qu un seul media ne recoit aucune frappe",
          [c for c in calls if c[0] == "send-key"], [])

with tempfile.TemporaryDirectory() as tmp:
    # libvirtd injoignable au moment de lire les medias : on ne sait pas ou
    # on frappe, donc on ne frappe pas. `virsh start` reste tente (c est
    # l etape), mais l aide au demarrage s abstient.
    unreadable = {"domstate": (0, "shut off\n"), "send-key": (0, "")}
    calls, launched, slept = _run_start(tmp, unreadable)
    check("un dumpxml injoignable ne fait frapper personne",
          [c for c in calls if c[0] == "send-key"], [])

with tempfile.TemporaryDirectory() as tmp:
    # Un send-key qui echoue (virsh injoignable, domaine disparu) est une
    # aide, pas une condition : le demarrage ne doit PAS echouer pour ca,
    # et les 12 tentatives doivent quand meme toutes avoir lieu (la
    # premiere tentative ratee n arrete pas les suivantes).
    fails_send_key = {"dumpxml": (0, _install_domain_xml(tmp)),
                      "domstate": (0, "shut off\n"),
                      "send-key": (1, "error: Domain not found")}
    try:
        calls, launched, slept = _run_start(tmp, fails_send_key)
    except steps.GuestBuildError:
        failures.append("un send-key en echec a fait echouer le demarrage "
                        "(ce n est qu une aide, pas une condition)")
    else:
        send_key_calls = [c for c in calls if c[0] == "send-key"]
        check("un send-key qui echoue n empeche pas les tentatives suivantes",
              len(send_key_calls), steps.BOOT_KEY_ATTEMPTS)

# send_boot_keys() directement : la fonction elle-meme, sans passer par
# plan_steps - la meme regle de bornage doit valoir isolement. Le virsh est
# PLAFONNE ici aussi : sans plafond, retirer la borne fait tourner cette
# boucle pour toujours (le sleep injecte rend la main aussitot) et la suite
# sort en rc=124, indiscernable d une contention d environnement.
fake_virsh = BoundedVirsh({"send-key": (0, "")})
slept = []
try:
    steps.send_boot_keys(fake_virsh, slept.append, domain="Windows")
except Unbounded as exc:
    failures.append(f"send_boot_keys seule n est pas bornee ({exc})")
check("send_boot_keys seule envoie exactement BOOT_KEY_ATTEMPTS frappes",
      len(fake_virsh.calls), steps.BOOT_KEY_ATTEMPTS)
check("send_boot_keys seule cible le domaine passe en argument",
      all(c[1] == "Windows" for c in fake_virsh.calls), True)


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


# --- le media Windows survit au redemarrage ------------------------------ #
# The wizard's 'windows_iso' answer names a file on the LIVE medium the
# installer runs from; after the reboot that medium is gone. install is the
# only phase that ever sees both roots, so it is the only place the medium
# can be copied - see console/hooks/install.py's own module docstring. What
# is tested here is copy_windows_medium() itself: the function install.py
# calls, and the one plan_steps above now trusts for source_iso.
check("DEFAULT_GUEST_WORKDIR suit le defaut declare dans wizard.yaml",
      f'default: "{steps.DEFAULT_GUEST_WORKDIR}"'
      in pathlib.Path(CONSOLE, "wizard.yaml").read_text(), True)
check("windows_media_path place la copie sous le repertoire de travail",
      steps.windows_media_path("/var/lib/nivuus/guest"),
      pathlib.Path("/var/lib/nivuus/guest") / steps.WINDOWS_MEDIA_FILENAME)

check_raises("require_windows_iso_answer refuse une reponse vide",
             steps.GuestBuildError,
             lambda: steps.require_windows_iso_answer({"windows_iso": "   "}))
check("require_windows_iso_answer nettoie la reponse",
      steps.require_windows_iso_answer({"windows_iso": "  /media/x.iso  "}),
      "/media/x.iso")

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    source = root / "live-medium.iso"
    dest = root / "target" / steps.WINDOWS_MEDIA_FILENAME

    # Source absent: refused by name, nothing written - install must refuse
    # while the operator is still at the wizard, not after the reboot.
    check_raises("un media source absent est refuse", steps.GuestBuildError,
                 lambda: steps.copy_windows_medium(str(source), str(dest)))
    check("rien n est ecrit quand la source est absente", dest.exists(), False)

    # A directory where a file was expected: os.stat() alone does not catch
    # this (it succeeds on a directory too) - only the copy attempt does,
    # which is why copy_windows_medium wraps that in its own try/except.
    as_dir = root / "not-a-file.iso"
    as_dir.mkdir()
    check_raises("un media source qui est un repertoire est refuse",
                 steps.GuestBuildError,
                 lambda: steps.copy_windows_medium(str(as_dir), str(dest)))
    check("rien n est ecrit quand la source est un repertoire",
          dest.exists(), False)

    # Not enough room: refused BEFORE a single byte lands at dest, and the
    # message names both figures. free_bytes is injected on purpose - this
    # does not require an actual full disk (see copy_windows_medium's own
    # docstring for the same trade-off made for the skip-when-complete case
    # below: cheap and correct beats a several-GB hash on every install).
    source.write_bytes(b"x" * 1000)
    try:
        steps.copy_windows_medium(str(source), str(dest),
                                  free_bytes=lambda p: 10)
        failures.append("un media trop gros pour la place disponible "
                        "n a pas ete refuse")
    except steps.GuestBuildError as exc:
        check("le refus de place nomme la taille du media", "1000" in str(exc), True)
        check("le refus de place nomme l espace libre", "10" in str(exc), True)
    check("rien n est ecrit quand la place manque", dest.exists(), False)

    # A DIFFERENT size at dest is NOT "already done": this is the case that
    # tells "skip on size match" apart from "skip whenever dest exists" -
    # the mutation that slipped past every other check in this block on the
    # first pass at writing it. dest must end up holding source's bytes.
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"z" * 3)  # deliberately not len(source)
    steps.copy_windows_medium(str(source), str(dest))
    check("une taille differente au but est bien recopiee",
          dest.read_bytes(), source.read_bytes())

    # A normal copy: bytes land at dest, matching the source exactly, and no
    # .partial leftover survives a successful run.
    dest.unlink()
    steps.copy_windows_medium(str(source), str(dest))
    check("le media copie est bien present", dest.is_file(), True)
    check("le contenu copie est identique a la source",
          dest.read_bytes(), source.read_bytes())
    check("aucun fichier .partial ne survit a une copie reussie",
          dest.with_name(dest.name + ".partial").exists(), False)

    # Already complete (SAME SIZE): not redone. Proven by mutating the
    # source's CONTENT while keeping its size identical - a real re-copy
    # would pick up the new bytes; the skip leaves dest untouched. This is
    # exactly the trade-off documented on copy_windows_medium and, before
    # it, on media_identity(): size, not a hash, decides "already done".
    before = dest.read_bytes()
    source.write_bytes(b"y" * 1000)
    check("la source a bien change de contenu a taille egale",
          source.read_bytes() == before, False)
    steps.copy_windows_medium(str(source), str(dest))
    check("une copie de meme taille n est pas refaite",
          dest.read_bytes(), before)


# --- l espace de preparation et les droits qemu (tache 2) ---------------- #
# Two measured failures from the same evening: build.py stages ~1.8 GiB of
# drivers under tempfile's default location (/tmp unless TMPDIR says
# otherwise), and /tmp here is a 10 GiB tmpfs already 97% full - "No space
# left on device" mid-build. Then, ISO built, `virsh start` refused twice
# with "Permission denied": once on the workdir (drwxr-x--- root:root,
# unreadable to libvirt-qemu), once on the ISO itself (root:root 0600).
# Both are proven here without building a real ISO or touching the real
# /var/lib/libvirt/qemu or passwd database - everything is injected.

class FakeBuildRunner:
    """A fake runner that MIMICS build.py's own observable contract (write
    the ISO at --output, chmod it 0600) instead of just recording the call -
    "a fake bench must implement the WHOLE interface the code reads" is a
    mistake this suite has already paid for once (see the module docstring).
    Without this, build_run()'s own chown step would have nothing real to
    act on."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, env=None):
        self.calls.append((list(argv), dict(env) if env is not None else None))
        out = pathlib.Path(arg(argv, "--output"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake unattend iso")
        os.chmod(out, 0o600)


class RecordingChown:
    """A fake chown: records every call, optionally fails on chosen paths -
    real os.chown to an arbitrary uid needs root, which the test process is
    not."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def __call__(self, path, uid, gid):
        self.calls.append((path, uid, gid))
        if path in self.fail_on:
            raise PermissionError(13, "Permission denied")


class RecordingChmod:
    """Un faux chmod qui ENREGISTRE puis applique reellement.

    Applique pour de vrai : le mode resultant du repertoire est ce qui est
    ensuite verifie, et un chmod simule prouverait seulement qu un appel a
    eu lieu, pas que qemu peut traverser."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def __call__(self, path, mode):
        self.calls.append((path, mode))
        if path in self.fail_on:
            raise PermissionError(13, "Permission denied")
        os.chmod(path, mode)


QEMU_UID, QEMU_GID = 64055, 64055  # libvirt-qemu:libvirt-qemu on this host,
# measured 2026-08-28 via resolve_qemu_owner() itself against the real
# /var/lib/libvirt/qemu - injected here, never hardcoded in guest_steps.py

with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    os.chmod(tmp, 0o750)        # drwxr-x--- : l etat mesure sur le terrain
    runner = FakeBuildRunner()
    chown = RecordingChown()
    chmod = RecordingChmod()
    given = dict(ANSWERS, guest_workdir=tmp)
    planned = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=runner,
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=chown,
        chmod=chmod))
    # secrets/ is written by the REAL step, never fabricated: what the
    # traverse grant must not expose is the directory that step creates.
    planned["secrets"].run()
    build_step = planned["build"]
    build_step.run()

    check("build ne lance qu une seule commande", len(runner.calls), 1)
    argv, env = runner.calls[0]
    check("la commande lancee est bien build.py",
          argv[1].endswith("guest/build.py"), True)
    # Ecrit pour ECHOUER NOMMEMENT si `env=` disparaissait de l appel :
    # `env` vaut alors None, et un `env["TMPDIR"]` direct sortirait en
    # TypeError - une trace, pas un FAIL nomme, et le reste du fichier ne
    # tournerait meme pas.
    check("build.py recoit un TMPDIR explicite",
          env is not None and "TMPDIR" in env, True)
    staged = pathlib.Path(env["TMPDIR"]) if env and "TMPDIR" in env else None
    check("l espace de preparation vit SOUS le repertoire de travail",
          staged is not None
          and str(staged).startswith(str(pathlib.Path(tmp)) + os.sep), True)
    check("l espace de preparation n est pas le /tmp du systeme",
          str(staged) == "/tmp", False)
    check("le repertoire de preparation existe reellement sur le disque",
          staged is not None and staged.is_dir(), True)
    check("le reste de l environnement (PATH, etc.) est conserve",
          env.get("PATH") if env else None, os.environ.get("PATH"))

    # Deux droits DIFFERENTS, et la difference est le correctif de ce tour.
    # Les FICHIERS changent de proprietaire (qemu les ouvre) ; le
    # REPERTOIRE ne gagne que le bit de traversee. Le posseder donnerait a
    # qemu le droit de renommer `secrets/` - la clef produit et deux mots de
    # passe - et d y substituer les siens. Traverser demande moins.
    chowned = {c[0]: (c[1], c[2]) for c in chown.calls}
    check("LE REPERTOIRE DE TRAVAIL N EST PAS DONNE A qemu",
          str(pathlib.Path(tmp)) in chowned, False)
    check("il devient traversable, et c est tout",
          os.stat(tmp).st_mode & 0o007, 0o001)
    check("aucun droit de LECTURE du repertoire n est concede",
          os.stat(tmp).st_mode & 0o004, 0)
    check("aucun droit d ECRITURE du repertoire n est concede",
          os.stat(tmp).st_mode & 0o002, 0)
    check("les droits du proprietaire et du groupe sont inchanges",
          os.stat(tmp).st_mode & 0o770, 0o750)
    check("secrets/ reste 0700, inatteignable par qemu",
          os.stat(pathlib.Path(tmp, "secrets")).st_mode & 0o777, 0o700)
    iso_target = str(pathlib.Path(tmp, "nivuus-unattend.iso"))
    check("l ISO produite est chownee pour l utilisateur qemu",
          chowned.get(iso_target), (QEMU_UID, QEMU_GID))
    windows_medium_target = str(steps.windows_media_path(tmp))
    check("le media Windows copie est LUI AUSSI chowne pour l utilisateur qemu",
          chowned.get(windows_medium_target), (QEMU_UID, QEMU_GID))

    # THE FALSIFICATION THIS STEP EXISTS TO PREVENT: only the owner may
    # change. build.py's own deliberate 0600 (it carries the product key and
    # two passwords in clear - see its own printed warning) must survive
    # untouched; widening it would be a security regression dressed up as a
    # fix. (Verified by hand during implementation: forcing an extra
    # os.chmod(iso_out, 0o644) into build_run made this single assertion
    # fail, and only this one - the guard is load-bearing, not decorative.)
    check("le mode de l ISO reste 0600, jamais elargi",
          os.stat(iso_target).st_mode & 0o777, 0o600)

# A chown failure (a real "Permission denied" from the kernel, e.g. the
# process running the install is not actually root) is refused BY NAME, not
# swallowed - the operator needs to know qemu still cannot open the file.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    iso_target = str(pathlib.Path(tmp, "nivuus-unattend.iso"))
    chown = RecordingChown(fail_on={iso_target})
    given = dict(ANSWERS, guest_workdir=tmp)
    build_step = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=FakeBuildRunner(),
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=chown))["build"]
    try:
        build_step.run()
        failures.append("un chown en echec sur l ISO n a pas ete refuse")
    except steps.GuestBuildError as exc:
        check("le refus de chown nomme le fichier vise", iso_target in str(exc), True)
        check("le refus de chown nomme l uid qemu vise", str(QEMU_UID) in str(exc), True)

# --- ROUND 2 : l appropriation est une PROPRIETE de l artefact, pas un
# effet de bord de sa construction ----------------------------------------- #
# The first pass only chowned on a FRESH build; an already-current ISO
# (the nominal case on every reprise) never got its ownership checked
# again. Fixed by having build_done()'s "already current" branch reassert
# it too - proven here by writing the ISO BY HAND (mimicking "built once,
# then found root-owned again"), never via build_step.run(), so a passing
# `runner.calls == []` is the actual proof that no rebuild happened.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    runner = FakeBuildRunner()
    chown = RecordingChown()
    chmod = RecordingChmod()
    given = dict(ANSWERS, guest_workdir=tmp)
    build_step = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=runner,
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=chown,
        chmod=chmod))["build"]

    iso_target = pathlib.Path(build_step.command[build_step.command.index("--output") + 1])
    iso_target.parent.mkdir(parents=True, exist_ok=True)
    iso_target.write_bytes(b"deja construite, mal possedee")
    os.chmod(iso_target, 0o600)
    steps.write_build_stamp(steps.build_stamp_path(str(iso_target)),
                            build_step.fingerprint())

    check("une ISO deja a jour reste consideree comme faite",
          build_step.already_done(), True)
    check("et surtout : AUCUNE reconstruction n a ete lancee pour l obtenir",
          runner.calls, [])

    chowned = {c[0]: (c[1], c[2]) for c in chown.calls}
    check("le repertoire de travail n est PAS davantage donne a qemu ici",
          str(pathlib.Path(tmp)) in chowned, False)
    check("il finit traversable SANS reconstruction",
          os.stat(tmp).st_mode & 0o001, 0o001)
    check("l ISO deja presente finit possedee par qemu SANS reconstruction",
          chowned.get(str(iso_target)), (QEMU_UID, QEMU_GID))
    check("le media Windows copie finit LUI AUSSI possede par qemu SANS reconstruction",
          chowned.get(str(steps.windows_media_path(tmp))), (QEMU_UID, QEMU_GID))
    check("le mode de l ISO deja presente reste 0600, jamais elargi",
          os.stat(iso_target).st_mode & 0o777, 0o600)

# A chown failure on that SAME "already done" branch must not be swallowed
# into "not done" - that would send the caller into a full, multi-minute
# rebuild that cannot fix a permission problem (the bytes are already
# right). It is raised immediately, by name, exactly like the fresh-build
# path above, and the rebuild it would otherwise trigger never happens.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    given = dict(ANSWERS, guest_workdir=tmp)
    probe = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=FakeBuildRunner(),
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=_inert_chown))["build"]
    iso_target = pathlib.Path(probe.command[probe.command.index("--output") + 1])
    iso_target.parent.mkdir(parents=True, exist_ok=True)
    iso_target.write_bytes(b"deja construite")
    os.chmod(iso_target, 0o600)
    steps.write_build_stamp(steps.build_stamp_path(str(iso_target)), probe.fingerprint())

    runner = FakeBuildRunner()  # must stay untouched below
    chown = RecordingChown(fail_on={str(iso_target)})
    build_step = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=runner,
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=chown))["build"]
    try:
        build_step.already_done()
        failures.append("un chown en echec sur une ISO deja a jour n a pas ete refuse")
    except steps.GuestBuildError as exc:
        check("le refus (ISO deja a jour) nomme le fichier vise",
              str(iso_target) in str(exc), True)
    check("et surtout : le refus de chown n a PAS declenche de reconstruction",
          runner.calls, [])

# --- la traversee : idempotente, et refusee NOMMEMENT si elle echoue ------ #
# Un repertoire deja traversable ne doit pas etre reecrit : l activation
# rejoue a chaque demarrage tant que le jalon n existe pas, et un operateur
# qui a elargi le repertoire a la main ne doit pas voir son choix reecrit a
# chaque tour.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    os.chmod(tmp, 0o751)        # le bit de traversee est DEJA la
    chmod = RecordingChmod()
    given = dict(ANSWERS, guest_workdir=tmp)
    build_step = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=FakeBuildRunner(),
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=_inert_chown,
        chmod=chmod))["build"]
    build_step.run()
    check("un repertoire deja traversable n est pas rechmode", chmod.calls, [])
    check("et il l est toujours", os.stat(tmp).st_mode & 0o777, 0o751)

# Un chmod refuse par le noyau est nomme, jamais avale : sans traversee,
# qemu recevra "Permission denied" sur les deux medias, et l operateur doit
# lire pourquoi.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    os.chmod(tmp, 0o750)
    chmod = RecordingChmod(fail_on={str(pathlib.Path(tmp))})
    given = dict(ANSWERS, guest_workdir=tmp)
    build_step = by_name(steps.plan_steps(
        given, {}, tmp, virsh=FakeVirsh(NO_DOMAIN), runner=FakeBuildRunner(),
        size_of=lambda d: DISK_BYTES,
        qemu_owner=lambda: (QEMU_UID, QEMU_GID), chown=_inert_chown,
        chmod=chmod))["build"]
    try:
        build_step.run()
        failures.append("un chmod en echec sur le repertoire n a pas ete refuse")
    except steps.GuestBuildError as exc:
        check("le refus de traversee nomme le repertoire vise",
              str(pathlib.Path(tmp)) in str(exc), True)


# --- resolve_qemu_owner : lu du systeme, jamais devine -------------------- #
class _FakeStat:
    def __init__(self, uid, gid):
        self.st_uid = uid
        self.st_gid = gid


def _stat_ok(path):
    check("resolve_qemu_owner interroge /var/lib/libvirt/qemu",
          path, steps.QEMU_STATE_DIR)
    return _FakeStat(QEMU_UID, QEMU_GID)


def _getpwuid_ok(uid):
    check("resolve_qemu_owner cherche le compte du bon uid", uid, QEMU_UID)
    return object()  # only existence matters here, never a hardcoded name


check("resolve_qemu_owner lit (uid, gid) sur le systeme, sans nom code en dur",
      steps.resolve_qemu_owner(stat_fn=_stat_ok, getpwuid=_getpwuid_ok),
      (QEMU_UID, QEMU_GID))


def _stat_missing(path):
    raise FileNotFoundError(2, "No such file or directory")


try:
    steps.resolve_qemu_owner(stat_fn=_stat_missing)
    failures.append("un /var/lib/libvirt/qemu absent n a pas ete refuse")
except steps.GuestBuildError as exc:
    check("l absence de libvirt est refusee nommement (le repertoire)",
          steps.QEMU_STATE_DIR in str(exc), True)


def _stat_orphan(path):
    return _FakeStat(999999, 999999)


def _getpwuid_unknown(uid):
    raise KeyError(uid)


try:
    steps.resolve_qemu_owner(stat_fn=_stat_orphan, getpwuid=_getpwuid_unknown)
    failures.append("un uid sans compte systeme n a pas ete refuse")
except steps.GuestBuildError as exc:
    check("un utilisateur qemu inexistant est refuse EN LE NOMMANT (l uid)",
          "999999" in str(exc), True)


# --- le garde-fou du mode disque ---------------------------------------- #
# LE GARDE EXISTANT EST MONTE A L ENVERS, et c est tout l objet de ce bloc.
# build.py::enforce_disk_mode_guard ne refuse que « rebuild » - le mode SUR,
# celui qui ne reformate que C: - et laisse passer « wipe », le mode qui
# efface le disque ENTIER. Or plan_steps() ne passait aucun --disk-mode, donc
# build.py prenait son defaut, donc « wipe ».
#
# Mesure du 2026-09-05 sur l invite de production : un SEUL disque physique
# (Samsung SSD 970 EVO Plus 2 To, GPT) porte a la fois C: (111,9 Go) et
# D: (1750 Go). Sur D: vivent D:\Steam (356,93 Go), la session Steam, le
# shortcuts.vdf de la bibliotheque retro, D:\Emulation, et
# D:\state\apollo\credentials (cacert.pem + cakey.pem, LA RACINE
# D APPAIRAGE Apollo : la perdre, c est reappairer chaque client Moonlight a
# la main, sans ecran). Le gabarit autounattend.xml.j2 porte lui-meme la
# cicatrice du 2026-08-25, ou une reconstruction a deja mange cette
# partition.
#
# CE QUI NE PEUT PAS SERVIR DE PREUVE : D:\state\NIVUUS-DATA.id. Il est sur
# le disque de l invite, et ce disque est lie a vfio-pci - il n a AUCUN
# peripherique bloc cote hote (voir hardware.py::parse_nvme_controllers :
# « lsblk cannot see the passthrough disk »). L hote ne peut donc ni le
# monter ni le lire. Le domaine libvirt deja defini sur CE disque est la
# preuve equivalente qui, elle, existe cote hote.
with tempfile.TemporaryDirectory() as tmp:
    steps.windows_media_path(tmp).write_bytes(b"windows medium")
    # Le domaine de regime : aucun media, le bon disque. C est exactement
    # l etat de la console d aujourd hui.
    console_existante = FakeVirsh(
        {"dumpxml": (0, "<domain><devices>" + nvme_hostdev()
                     + "</devices></domain>"),
         "domstate": (0, "shut off\n")})

    # LE ROUGE : aujourd hui, cette reconstruction part sans un mot. Le refus
    # se lit sur l ETAPE build - la ou l ISO qui efface serait fabriquee -
    # et non a la construction du plan, qui ne doit rien executer.
    sans_mode = dict(ANSWERS, guest_workdir=tmp)
    sans_mode.pop("disk_mode", None)
    implicite = by_name(steps.plan_steps(
        sans_mode, {}, tmp, virsh=console_existante,
        runner=lambda *a, **k: None,
        size_of=lambda device: DISK_BYTES,
        pci_address_of=_default_pci_address_of,
        qemu_owner=lambda: (0, 0), chown=_inert_chown))
    check("construire le plan n execute toujours rien",
          implicite["build"].command[1].endswith("build.py"), True)
    try:
        implicite["build"].run()
        failures.append("un wipe implicite sur une console existante n a pas "
                        "ete refuse")
    except steps.GuestBuildError as exc:
        message = str(exc)
        check("le refus nomme la partition de donnees",
              "D:" in message, True)
        check("le refus nomme le remede exact",
              "--disk-mode rebuild --target-disk-verified" in message, True)

    # Un wipe EXPLICITEMENT demande reste possible : c est une console qu on
    # reconstruit vraiment de zero, et l operateur l a ecrit.
    voulu = by_name(plan(tmp, console_existante, {"disk_mode": "wipe"}))
    check("un wipe explicite est accepte sur une console existante",
          arg(voulu["build"].command, "--disk-mode"), "wipe")

    # Machine neuve : aucun domaine, donc rien a detruire. Le plan passe, et
    # le mode est passe EXPLICITEMENT plutot que laisse au defaut de build.py.
    neuve = by_name(plan(tmp, FakeVirsh(NO_DOMAIN)))
    check("sur une machine neuve le mode est passe explicitement",
          arg(neuve["build"].command, "--disk-mode"), "wipe")

    # Le mode sur : passe tel quel, et la signature de l operateur n est
    # JAMAIS inventee ici - sans elle, c est build.py qui refuse, avec son
    # propre message.
    sans_signature = by_name(plan(tmp, console_existante,
                                  {"disk_mode": "rebuild"}))
    check("le mode rebuild est transmis",
          arg(sans_signature["build"].command, "--disk-mode"), "rebuild")
    check("la signature de l operateur n est pas inventee",
          "--target-disk-verified" in sans_signature["build"].command, False)

    signe = by_name(plan(tmp, console_existante,
                         {"disk_mode": "rebuild", "target_disk_verified": True}))
    check("la signature de l operateur est transmise quand elle est donnee",
          "--target-disk-verified" in signe["build"].command, True)

    # Un mode inconnu est refuse ici, pas trois etapes plus loin.
    check_raises("un mode disque inconnu est refuse", steps.GuestBuildError,
                 lambda: plan(tmp, console_existante,
                              {"disk_mode": "format-everything"}))


if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - five steps, each skippable on an observation, each command built "
      "and none launched")
