#!/usr/bin/env python3
"""Tests du declenchement de la synchronisation depuis l hote (retro_sync.py).

Rien ici n exige Windows : la sequence est exercee contre un FAUX invite qui
enregistre ce qu on lui demande, dans l ordre. C est le seul moyen de prouver
les proprietes qui comptent — la sentinelle posee AVANT l arret de Steam et
retiree quoi qu il arrive, le refus de synchroniser sur un temoin qui ne dit
pas « ok » — sans machine virtuelle sous la main.

Deux familles de controles :

 1. LE PONT avec les scripts PowerShell. Les chemins, l identifiant de repli
    et le vocabulaire des status sont ecrits en PowerShell dans
    provision/assets/ ; ce fichier les relit LA-BAS et les compare aux
    constantes de l hote. Deux litteraux independants divergent en silence —
    c est exactement comme ca que le pont retro.psd1 s etait rompu — et un
    renommage d un seul cote doit faire echouer un test.

 2. LE COMPORTEMENT. Les fonctions pures (sync_refusal, install_status,
    format_witness) sont APPELEES, jamais cherchees dans le texte : un
    controle qui lit une chaine se satisfait d un commentaire, ou du contrat
    inscrit dans le temoin lui-meme.

Lancer : python3 scripts/tests/test_windows_guest_retro_sync.py
"""
import ast
import base64
import contextlib
import io
import pathlib
import re
import sys
import threading

REPO = pathlib.Path(__file__).resolve().parents[2]
GUEST = REPO / "installer" / "windows-guest"
PROVISION = GUEST / "provision"
sys.path.insert(0, str(GUEST))

import retro_sync  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def _essai_erreur(fn, *args):
    """L exception levee par fn(*args), ou None. Une fonction qui devait lever
    et ne leve pas rend None, donc le controle echoue."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


def code_only(text):
    """Le code PowerShell seul, commentaires de bloc compris.

    Meme idiome que test_windows_guest_provision.py, et pour la meme raison :
    l en-tete de retro-status.ps1 nomme en toutes lettres les chemins et les
    status qu on veut epingler, donc un controle sur le texte brut resterait
    vert apres la disparition du code correspondant.
    """
    sans_blocs = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(ln for ln in sans_blocs.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))


_status_ps1 = (PROVISION / "assets" / "retro-status.ps1").read_text(encoding="utf-8")
_status_code = code_only(_status_ps1)
_launch_code = code_only(
    (PROVISION / "assets" / "steam-launch.ps1").read_text(encoding="utf-8"))
_retro_ps1 = (PROVISION / "32-retro.ps1").read_text(encoding="utf-8")
_retro_code = code_only(_retro_ps1)


def _litteral(code, variable):
    """La valeur d une affectation PowerShell « $Nom = '...' », dans le CODE."""
    m = re.search(r"\$" + variable + r"\s*=\s*'([^']*)'", code)
    return m.group(1) if m else None


def _nombre(code, variable):
    m = re.search(r"\$" + variable + r"\s*=\s*(\d+)", code)
    return int(m.group(1)) if m else None


# --- 1. Le pont : l hote lit ce que l invite ecrit, aux memes endroits ---- #

check("le temoin lu est celui que retro-status.ps1 ecrit",
      retro_sync.STATUS_FILE, _litteral(_status_code, "RetroStatusFile"))
check("l identifiant de passage vient du meme fichier des deux cotes",
      retro_sync.RUN_FILE, _litteral(_status_code, "RetroRunFile"))
# Le repli quand run-all.ps1 n a rien laisse : si les deux cotes le nomment
# differemment, la comparaison de passage echoue la ou elle devrait passer, et
# l hote refuse de synchroniser une installation saine.
check("le repli « pas de passage » est nomme pareil des deux cotes",
      retro_sync.RUN_UNKNOWN in _status_code, True)
check("la sentinelle posee est celle que steam-launch.ps1 consulte",
      retro_sync.HOLD_FILE, _litteral(_launch_code, "HoldFile"))
check("l hote connait le vrai delai d expiration de la sentinelle",
      retro_sync.HOLD_MAX_AGE_S, _nombre(_launch_code, "HoldMaxAgeSeconds"))
check("la racine d emulation est celle de l etape 32",
      retro_sync.EMULATION_ROOT, _litteral(_retro_code, "EmulationRoot"))
# retro.exe : l etape 32 le compose depuis $PythonRoot, l hote le sonde en
# entier. Les deux doivent designer le meme fichier.
check("retro.exe est cherche la ou l etape 32 l installe",
      retro_sync.RETRO_EXE,
      _litteral(_retro_code, "PythonRoot") + "\\Scripts\\retro.exe")
check("... et l etape 32 compose bien ce chemin-la",
      "Join-Path $PythonRoot 'Scripts\\retro.exe'" in _retro_code, True)

# Le vocabulaire des status. L hote ne doit pas connaitre un mot que l invite
# n ecrit pas, ni ignorer un mot qu il ecrit : sync_refusal est ferme par
# defaut, donc un status inconnu est un refus — correct, mais avec un message
# qui n aide personne.
_status_ecrits = sorted(set(re.findall(r"Write-RetroStatus '([\w-]+)'", _retro_code)))
for _state in _status_ecrits:
    _refus = retro_sync.sync_refusal(_state, "R", "R")
    check(f"l hote sait quoi repondre au status « {_state} »",
          _refus is None or "inconnu" not in _refus, True)
check("les status que l hote nomme sont tous ecrits par l etape 32",
      sorted({retro_sync.STATUS_OK, retro_sync.STATUS_DISABLED,
              retro_sync.STATUS_PARTIAL, retro_sync.STATUS_MANIFEST_UNREADABLE}
             - set(_status_ecrits)), [])

# La forme du temoin. L hote le REECRIT : s il en changeait la forme, le
# fichier deviendrait illisible pour qui connait l autre ecrivain.
_bloc_ps = _status_code[_status_code.find("$lines = $RetroStatusHeader"):
                        _status_code.find("[System.IO.File]::WriteAllLines")]
_cles_ps = re.findall(r'"(\w+)=', _bloc_ps)
_produit = retro_sync.format_witness(["# en-tete"], "R1", "ok", "D:\\Emulation",
                                     ["ligne de rapport"], when="W")
_cles_hote = [ln.split("=")[0] for ln in _produit.splitlines()
              if "=" in ln and not ln.startswith("#")]
check("l hote ecrit les memes cles, dans le meme ordre, que Write-RetroStatus",
      _cles_hote, _cles_ps)
check("l hote ecrit lui aussi la ligne « report: »",
      "report:" in _bloc_ps and "report:" in _produit.splitlines(), True)
check("les lignes du temoin restent terminees comme celles de l invite",
      _produit.endswith("\r\n") and "\r\n" in _produit, True)

# --- 2. install_status : la meme lecture des codes de sortie que l etape 32 - #

_apres = _retro_code[_retro_code.find("$installExit = $LASTEXITCODE"):]
check("l etape 32 traite encore le code 0 comme un succes",
      "if ($installExit -eq 0)" in _apres, True)
check("... le code 1 comme une installation partielle",
      "elseif ($installExit -eq 1)" in _apres, True)
for _code, _attendu in ((0, "ok"), (1, "partial"), (2, "manifest-unreadable"),
                        (9, "manifest-unreadable")):
    check(f"install_status({_code})", retro_sync.install_status(_code), _attendu)
_ordre_ps = re.findall(r"Write-RetroStatus '([\w-]+)' \$installOutput", _apres)
check("l hote et l etape 32 mappent les codes sur les memes status",
      [retro_sync.install_status(0), retro_sync.install_status(1),
       retro_sync.install_status(2)], _ordre_ps)

# --- 3. La garde. Elle est APPELEE, status par status. ------------------- #

check("« ok » du passage courant autorise, et lui seul",
      retro_sync.sync_refusal("ok", "R1", "R1"), None)
for _state in ("started", "disabled", "interrupted", "partial",
               "manifest-unreadable", "inconnu-du-futur"):
    check(f"« {_state} » refuse la synchronisation",
          retro_sync.sync_refusal(_state, "R1", "R1") is not None, True)
check("un temoin absent refuse la synchronisation",
      retro_sync.sync_refusal(None, None, "R1") is not None, True)
check("un « ok » d un passage ANTERIEUR refuse la synchronisation",
      retro_sync.sync_refusal("ok", "R0", "R1") is not None, True)
check("... et le refus nomme les deux passages",
      all(x in retro_sync.sync_refusal("ok", "R0", "R1") for x in ("R0", "R1")),
      True)
# Un refus qui ne dit pas ce qu il a lu envoie chercher dans la VM ce que
# l hote avait sous les yeux.
check("le refus sur « partial » nomme le status lu",
      "partial" in retro_sync.sync_refusal("partial", "R1", "R1"), True)
check("le refus sur un temoin absent nomme le fichier attendu",
      retro_sync.STATUS_FILE in retro_sync.sync_refusal(None, None, "R1"), True)
# Un temoin sans run= (temoin d avant l identifiant de passage) ne doit pas
# faire echouer la comparaison en silence : c est le status qui tranche.
check("un temoin sans run= reste juge sur son status",
      retro_sync.sync_refusal("ok", None, "R1"), None)

# --- 4. La forme du temoin reecrit : aller-retour, et pas d empilement ---- #

_t1 = retro_sync.format_witness(["# contrat"], "R1", "partial", "D:\\Emulation",
                                ["a manque : dolphin"])
_h1, _c1, _r1 = retro_sync.parse_witness(_t1)
check("le temoin relu rend son status", _c1.get("status"), "partial")
check("le temoin relu rend son passage", _c1.get("run"), "R1")
check("le temoin relu rend son rapport", _r1, ["a manque : dolphin"])
check("l en-tete d origine est conserve", "# contrat" in _h1, True)
check("l hote signe le rafraichissement dans l en-tete",
      retro_sync.HOST_NOTE in _h1, True)
_t2 = retro_sync.format_witness(_h1, "R1", "ok", "D:\\Emulation", [])
check("la signature de l hote ne s empile pas a chaque passage",
      retro_sync.parse_witness(_t2)[0].count(retro_sync.HOST_NOTE), 1)
# Le rapport doit voyager jusqu au temoin, borne mais pas vide : c est la FIN
# qui nomme les echecs.
_long = "\n".join(f"l{i}" for i in range(retro_sync.MAX_REPORT_LINES + 50))
_borne = retro_sync.cap_report(_long)
check("un rapport trop long est borne",
      len(_borne) <= retro_sync.MAX_REPORT_LINES + 1, True)
check("... en gardant la fin, pas le debut",
      _borne[-1], f"l{retro_sync.MAX_REPORT_LINES + 49}")
check("... et en disant qu il a ete coupe", "omise" in _borne[0], True)
check("un rapport court passe tel quel", retro_sync.cap_report("a\nb"), ["a", "b"])
_gras = "\n".join("x" * 200 for _ in range(100))
_borne = retro_sync.cap_report(_gras)
check("un rapport peu de lignes mais tres gros est borne aussi",
      sum(len(ln) + 2 for ln in _borne[1:]) <= retro_sync.MAX_REPORT_CHARS, True)
check("... en gardant encore la fin", _borne[-1], "x" * 200)

# --- 5. La sequence, contre un faux invite ------------------------------- #


class FakeGuest:
    """Ce que l hote a le droit de demander a la console, et rien de plus."""

    def __init__(self, fichiers=None, codes=None, leve=None,
                 retro_exe=retro_sync.RETRO_EXE):
        self.fichiers = dict(fichiers or {})
        self.codes = dict(codes or {})
        self.leve = leve or {}
        self.retro_exe = retro_exe
        self.journal = []

    def read_text(self, path):
        self.journal.append(("read", path))
        return self.fichiers.get(path)

    def write_text(self, path, text):
        self.journal.append(("write", path))
        self.fichiers[path] = text

    def remove(self, path):
        self.journal.append(("remove", path))
        self.fichiers.pop(path, None)

    def exists(self, path):
        self.journal.append(("exists", path))
        return path in self.fichiers

    def ps(self, script):
        self.journal.append(("ps", script))
        return 0, "", ""

    def retro(self, args):
        self.journal.append(("retro", args[0]))
        if args[0] in self.leve:
            raise self.leve[args[0]]
        return self.codes.get(args[0], 0), f"rapport {args[0]}"


class Cfg:
    guest_label = "la console"
    emulation_root = retro_sync.EMULATION_ROOT
    steam_root = retro_sync.STEAM_ROOT
    steam_root_windows = retro_sync.STEAM_ROOT
    roms = retro_sync.ROMS_ROOT
    user_manifest = retro_sync.USER_MANIFEST
    inventory = retro_sync.INVENTORY
    retro_exe = retro_sync.RETRO_EXE
    steamgriddb_key = None


# Le faux invite doit offrir la MEME surface que le vrai, sinon ces scenarios
# prouvent le comportement d une API qui n existe pas.
_publiques = {n for n in dir(retro_sync.Guest)
              if not n.startswith("_") and callable(getattr(retro_sync.Guest, n))}
check("le faux invite couvre toute la surface du vrai",
      sorted(_publiques - set(dir(FakeGuest))), [])


def temoin(status, run="R1"):
    return retro_sync.format_witness(["# contrat"], run, status,
                                     retro_sync.EMULATION_ROOT, [])


def invite(status=None, run="R1", avec_retro=True, **kw):
    fichiers = {retro_sync.RUN_FILE: "R1\r\n"}
    if status is not None:
        fichiers[retro_sync.STATUS_FILE] = temoin(status, run)
    if avec_retro:
        fichiers[retro_sync.RETRO_EXE] = ""
    fichiers.update(kw.pop("fichiers", {}))
    return FakeGuest(fichiers=fichiers, **kw)


def actions(g, *quoi):
    return [a for a in g.journal if a[0] in quoi]


def lancer(g, cfg=None):
    """La sequence, sortie capturee : (code, ce qui a ete dit, ce qui a ete
    signale). Capturer plutot que laisser filer garde la sortie de ce test
    lisible, et permet surtout de verifier CE QUI EST DIT — un refus muet
    serait aussi inutilisable qu une absence de refus."""
    dit, signale = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(dit), contextlib.redirect_stderr(signale):
        code = retro_sync.synchronise(g, cfg or Cfg())
    return code, dit.getvalue(), signale.getvalue()


# a) l installation reussit : le temoin est rafraichi, puis tout s enchaine.
g = invite("partial")
check("une installation reparee synchronise", lancer(g)[0], 0)
_appels = [a for a in g.journal if a[0] in ("retro", "write", "remove", "ps")]
check("l installation est rejouee avant tout le reste",
      _appels[0], ("retro", "install"))
check("le temoin est rafraichi juste apres l installation",
      _appels[1], ("write", retro_sync.STATUS_FILE))
check("le temoin rafraichi dit « ok » apres une installation reussie",
      retro_sync.parse_witness(g.fichiers[retro_sync.STATUS_FILE])[1]["status"],
      "ok")
check("... et porte le passage COURANT, pas celui du temoin d avant",
      retro_sync.parse_witness(g.fichiers[retro_sync.STATUS_FILE])[1]["run"],
      "R1")
_reste = [a for a in _appels[2:] if a != ("read", retro_sync.STATUS_FILE)]
check("le scan precede la sentinelle", _reste[0], ("retro", "scan"))
check("la sentinelle est posee AVANT l arret de Steam",
      _reste[1], ("write", retro_sync.HOLD_FILE))
check("Steam est arrete avant la synchronisation", _reste[2][0], "ps")
check("... et c est bien un arret de Steam", "steam" in _reste[2][1], True)
check("la synchronisation vient ensuite", _reste[3], ("retro", "sync"))
check("la sentinelle est retiree apres",
      _reste[4], ("remove", retro_sync.HOLD_FILE))
check("la sentinelle ne survit pas a la synchronisation",
      retro_sync.HOLD_FILE in g.fichiers, False)

# b) l installation reste partielle : refus, et RIEN d autre n est touche.
g = invite("ok", codes={"install": 1})
_code, _dit, _signale = lancer(g)
check("une installation encore partielle est refusee", _code, 4)
check("... et le refus est SIGNALE, pas glisse dans la sortie normale",
      "REFUS" in _signale.upper() and "partial" in _signale, True)
check("... et le temoin le dit, contre l « ok » qu il portait",
      retro_sync.parse_witness(g.fichiers[retro_sync.STATUS_FILE])[1]["status"],
      "partial")
check("un refus ne scanne pas", ("retro", "scan") in g.journal, False)
check("un refus ne synchronise pas", ("retro", "sync") in g.journal, False)
check("un refus ne pose aucune sentinelle",
      actions(g, "write") == [("write", retro_sync.STATUS_FILE)], True)
check("un refus n arrete pas Steam", actions(g, "ps"), [])

# c) le manifeste est illisible : meme refus, status precis.
g = invite("ok", codes={"install": 2})
check("un manifeste illisible est refuse", lancer(g)[0], 4)
check("... et le temoin le nomme",
      retro_sync.parse_witness(g.fichiers[retro_sync.STATUS_FILE])[1]["status"],
      "manifest-unreadable")

# d) la synchronisation echoue : la sentinelle part quand meme.
g = invite("ok", codes={"sync": 5})
check("une synchronisation en echec le dit", lancer(g)[0], 6)
check("... et la sentinelle est retiree malgre l echec",
      retro_sync.HOLD_FILE in g.fichiers, False)

# e) la synchronisation LEVE : la sentinelle part quand meme, et l echec
# remonte sans etre avale par le nettoyage.
g = invite("ok", leve={"sync": retro_sync.GuestError("boum")})
try:
    lancer(g)
    _leve = None
except retro_sync.GuestError as exc:
    _leve = str(exc)
check("une levee pendant la synchronisation remonte", _leve, "boum")
check("... et la sentinelle est retiree quand meme",
      retro_sync.HOLD_FILE in g.fichiers, False)

# f) l arret de Steam leve : la sentinelle est deja posee, elle doit partir.
class GuestQuiRateLArret(FakeGuest):
    def ps(self, script):
        self.journal.append(("ps", script))
        raise retro_sync.GuestError("steam ne s arrete pas")


g = GuestQuiRateLArret(fichiers={retro_sync.RUN_FILE: "R1",
                                 retro_sync.STATUS_FILE: temoin("ok"),
                                 retro_sync.RETRO_EXE: ""})
try:
    lancer(g)
    _leve = None
except retro_sync.GuestError as exc:
    _leve = str(exc)
check("un arret de Steam impossible remonte", _leve, "steam ne s arrete pas")
check("... sans laisser la sentinelle derriere elle",
      retro_sync.HOLD_FILE in g.fichiers, False)

# g) le retrogaming n est pas active : on le DIT, on ne plante pas.
g = invite("disabled", avec_retro=False)
_code, _dit, _signale = lancer(g)
check("un retrogaming desactive sort en succes", _code, 0)
check("... en DISANT que la fonctionnalite n est pas activee",
      "pas activ" in _dit and _signale == "", True)
check("... sans rien lancer dans l invite", actions(g, "retro", "write", "ps"), [])

g = invite(None, avec_retro=False)
_code, _dit, _signale = lancer(g)
check("aucun temoin et aucun retro.exe : la fonctionnalite n est pas la", _code, 0)
check("... et c est dit, pas signale comme une panne",
      "pas activ" in _dit and _signale == "", True)
check("... sans rien lancer non plus", actions(g, "retro", "write", "ps"), [])

# Le choix du proprietaire prime meme si retro.exe traine encore : rejouer
# l installation reactiverait ce qu il a decoche, et le rafraichissement
# ecraserait son « disabled » par un « ok ».
g = invite("disabled", avec_retro=True)
check("un « disabled » du passage courant arrete tout", lancer(g)[0], 0)
check("... sans rejouer l installation", actions(g, "retro"), [])
check("... et sans toucher au temoin", actions(g, "write"), [])

# h) retro.exe absent alors que le temoin annonce une installation : ce n est
# pas « pas active », c est casse, et ca doit se voir.
g = invite("partial", avec_retro=False)
check("une installation annoncee mais absente est signalee", lancer(g)[0], 3)
check("... sans rien lancer", actions(g, "retro", "write", "ps"), [])

# i) un temoin d un passage ANTERIEUR : rejouer l installation le rafraichit
# au passage courant, et la synchronisation redevient possible. C est la
# reparation, pas un refus definitif.
g = invite("ok", run="R0")
check("un temoin perime est repare par le passage courant", lancer(g)[0], 0)
check("... et le temoin porte desormais le passage courant",
      retro_sync.parse_witness(g.fichiers[retro_sync.STATUS_FILE])[1]["run"],
      "R1")

# j) le scan echoue : Steam n est meme pas derange.
g = invite("ok", codes={"scan": 2})
check("un scan en echec s arrete la", lancer(g)[0], 5)
check("... sans arreter Steam", actions(g, "ps"), [])
check("... sans poser de sentinelle",
      ("write", retro_sync.HOLD_FILE) in g.journal, False)

# k) la garde lit le fichier RELU, pas ce que l hote croit avoir ecrit : un
# invite dont l ecriture n a pas pris doit faire refuser.
class GuestQuiNEcritPas(FakeGuest):
    def write_text(self, path, text):
        self.journal.append(("write", path))
        if path != retro_sync.STATUS_FILE:
            self.fichiers[path] = text


g = GuestQuiNEcritPas(fichiers={retro_sync.RUN_FILE: "R1",
                                retro_sync.RETRO_EXE: ""})
check("un temoin qui ne s ecrit pas fait refuser la synchronisation",
      lancer(g)[0], 4)
check("... et rien n est synchronise", ("retro", "sync") in g.journal, False)

# --- 6. La sentinelle rajeunie pendant une longue synchronisation -------- #

check("la sentinelle est rajeunie bien avant d expirer",
      retro_sync.HOLD_TOUCH_INTERVAL_S * 2 <= retro_sync.HOLD_MAX_AGE_S, True)
_touches = []
_keeper = retro_sync.HoldKeeper(object(), interval=0.01,
                                toucher=lambda g: _touches.append(1))
_keeper.start()
_debut = threading.Event()
_debut.wait(0.2)
_keeper.stop()
_apres_arret = len(_touches)
check("elle est effectivement rajeunie tant que la synchro travaille",
      len(_touches) >= 2, True)
_debut.wait(0.1)
check("... et plus du tout une fois la synchro finie",
      len(_touches) <= _apres_arret + 1, True)
# Un rajeunissement qui echoue ne doit pas emporter la synchronisation en
# cours : la sentinelle expirera, ce qui est deja son filet.
def _toucher_qui_leve(_g):
    _touches.append(1)
    raise retro_sync.GuestError("plus de reseau")


_touches.clear()
_signale = io.StringIO()
_keeper = retro_sync.HoldKeeper(object(), interval=0.01,
                                toucher=_toucher_qui_leve)
with contextlib.redirect_stderr(_signale):
    _keeper.start()
    _debut.wait(0.2)
    _keeper.stop()
# Le fil doit avoir REESSAYE apres la premiere levee : un fil mort au premier
# echec laisserait la sentinelle vieillir sans que personne ne le sache.
check("un rajeunissement en echec ne tue pas le fil", len(_touches) >= 2, True)
check("... et chaque echec est signale", _signale.getvalue().count("sentinelle"),
      len(_touches))

# --- 6 bis. Le vrai invite : ce qui traverse WinRM ----------------------- #


def _capture(journal):
    """Le transport de winrm_exec.py, remplace par un carnet : ce qui suit
    exerce le VRAI Guest, seulement sans WinRM au bout."""
    def run(kind, script, guest_ip=None):
        journal.append((kind, script, guest_ip))
        return 0, "", ""
    return run


check("les apostrophes sont doublees avant d entrer dans PowerShell",
      retro_sync._ps_quote("D:\\l'Emulation"), "'D:\\l''Emulation'")

_journal = []
_vrai = retro_sync.Guest(run=_capture(_journal), guest_ip="10.0.0.1")
_vrai.write_text(retro_sync.STATUS_FILE, "abc")
check("l ecriture cree le dossier avant d ecrire",
      "New-Item -ItemType Directory -Force -Path 'D:\\state'" in _journal[-1][1],
      True)
check("... et le contenu voyage en base64, jamais en clair",
      base64.b64encode(b"abc").decode() in _journal[-1][1] and
      "abc" not in _journal[-1][1].replace(base64.b64encode(b"abc").decode(), ""),
      True)
check("l adresse demandee est celle transmise a winrm_exec",
      _journal[-1][2], "10.0.0.1")

# Le temoin voyage DANS la ligne de commande : pywinrm encode le script en
# UTF-16LE puis en base64 et le passe a « powershell -encodedcommand ».
# Windows refuse au-dela de 32767 caracteres, et c est l ecriture du temoin —
# celle qui doit survivre a l incident — qui echouerait la premiere.
_bloc_header = _status_code[_status_code.find("$RetroStatusHeader = @("):]
_bloc_header = _bloc_header[:_bloc_header.find("\n)")]
_header_reel = re.findall(r"'((?:[^']|'')*)'", _bloc_header)
check("l en-tete reel du temoin a bien ete retrouve", len(_header_reel) > 10, True)
_pire = retro_sync.format_witness(
    _header_reel, "2026-08-26T12:00:00.0000000+02:00", "partial",
    retro_sync.EMULATION_ROOT,
    retro_sync.cap_report("\n".join("x" * 120 for _ in range(500))))
_journal.clear()
_vrai.write_text(retro_sync.STATUS_FILE, _pire)
_ligne = "powershell -encodedcommand " + base64.b64encode(
    _journal[-1][1].encode("utf-16-le")).decode("ascii")
check("le plus gros temoin possible tient dans une ligne de commande Windows",
      len(_ligne) < 32767, True)


def _reponse(rc, out):
    def run(kind, script, guest_ip=None):
        return rc, out, ""
    return run


check("un fichier absent se lit comme None, pas comme une chaine vide",
      retro_sync.Guest(run=_reponse(44, "")).read_text("D:\\x"), None)
check("un fichier present est decode depuis le base64",
      retro_sync.Guest(run=_reponse(
          0, base64.b64encode("épée".encode()).decode())).read_text("D:\\x"),
      "épée")
check("un invite injoignable leve plutot que de rendre None",
      isinstance(_essai_erreur(retro_sync.Guest(run=_reponse(1, "")).read_text,
                               "D:\\x"), retro_sync.GuestError), True)
_rc, _rapport = retro_sync.Guest(run=_reponse(
    0, "EXIT=1\n" + base64.b64encode("émulateur manquant".encode()).decode())
).retro(["install"])
check("le code de sortie de retro est celui de l invite, pas celui de WinRM",
      _rc, 1)
check("... et son rapport accentue arrive intact", _rapport, "émulateur manquant")
check("une sortie sans marqueur de code leve plutot que de mentir",
      isinstance(_essai_erreur(retro_sync.Guest(run=_reponse(0, "bruit")).retro,
                               ["install"]), retro_sync.GuestError), True)

# --- 7. La structure : le retrait est dans un finally, pas dans un chemin - #
# Statique et exact (ast) : un commentaire qui promet « quoi qu il arrive »,
# ou un retrait recopie dans chaque branche, ne peut pas satisfaire ceci.

_arbre = ast.parse((GUEST / "retro_sync.py").read_text(encoding="utf-8"))
_fn = next((n for n in ast.walk(_arbre)
            if isinstance(n, ast.FunctionDef) and n.name == "synchronise"), None)
check("retro_sync.py definit encore synchronise()", _fn is not None, True)


def _appels(noeud, nom):
    return [n for n in ast.walk(noeud) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == nom]


_essais = [n for n in ast.walk(_fn) if isinstance(n, ast.Try) and n.finalbody]
_avec_retrait = [t for t in _essais
                 if any(_appels(f, "remove_hold") for f in t.finalbody)]
check("le retrait de la sentinelle est dans un finally", bool(_avec_retrait), True)
_try = _avec_retrait[0] if _avec_retrait else None
check("la sentinelle n est retiree QUE la, jamais dans un chemin normal",
      len(_appels(_fn, "remove_hold")), 1)
if _try is not None:
    _corps = _try.body
    check("l arret de Steam est SOUS le finally qui retire la sentinelle",
          bool([n for n in _corps
                for _ in _appels(n, "stop_steam")]), True)
    check("la synchronisation aussi",
          any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "retro"
              for c in _corps for n in ast.walk(c)), True)
    _pose = _appels(_fn, "place_hold")
    check("la sentinelle est posee avant d entrer dans le bloc",
          bool(_pose) and _pose[0].lineno < _try.lineno, True)
_refresh = _appels(_fn, "refresh_witness")
_garde = _appels(_fn, "sync_refusal")
check("le temoin est rafraichi AVANT d etre relu par la garde",
      bool(_refresh) and bool(_garde) and _refresh[0].lineno < _garde[0].lineno,
      True)
check("la garde precede la pose de la sentinelle",
      bool(_garde) and bool(_pose) and _garde[0].lineno < _pose[0].lineno, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - la sequence hote refuse un temoin qui ne dit pas « ok », "
      "et rend la sentinelle quoi qu il arrive")
