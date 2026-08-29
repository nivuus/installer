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

Lancer : python3 console/tests/test_windows_guest_retro_sync.py
"""
import ast
import base64
import contextlib
import io
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
GUEST = REPO / "console" / "guest"
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
_runall_code = code_only(
    (PROVISION / "run-all.ps1").read_text(encoding="utf-8"))


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
# Le controle ci-dessus epingle le fichier que retro-status.ps1 et retro_sync
# LISENT l un contre l autre - mais rien jusqu ici ne relie ca a ce que
# run-all.ps1 ECRIT reellement. Une ligne supprimee la-bas (celle qui pose
# provision.started a chaque passage) ne fait echouer aucun des seize fichiers
# de test de ce depot : les deux lecteurs replient alors en silence sur le
# meme repli (RUN_UNKNOWN / hors-run-all), la comparaison de passage passe
# TOUJOURS, et la protection contre un temoin perime devient un no-op muet -
# pire que l absence de temoin.
check("run-all.ps1 pose bien le temoin de passage a l endroit que les deux "
      "lecteurs attendent",
      _litteral(_runall_code, "StateDir") + "\\provision.started",
      retro_sync.RUN_FILE)
check("... et le pose depuis le code, pas seulement dans un commentaire",
      "Join-Path $StateDir 'provision.started'" in _runall_code, True)
# Le repli quand run-all.ps1 n a rien laisse : si les deux cotes le nomment
# differemment, la comparaison de passage echoue la ou elle devrait passer, et
# l hote refuse de synchroniser une installation saine.
check("le repli « pas de passage » est nomme pareil des deux cotes",
      retro_sync.RUN_UNKNOWN in _status_code, True)
check("la sentinelle posee est celle que steam-launch.ps1 consulte",
      retro_sync.HOLD_FILE, _litteral(_launch_code, "HoldFile"))
check("l hote connait le vrai delai d expiration de la sentinelle",
      retro_sync.HOLD_MAX_AGE_S, _nombre(_launch_code, "HoldMaxAgeSeconds"))
# Le chemin de steam.exe n est pas un detail de confort : si l hote et
# steam-launch.ps1 divergent, le Test-Path de l arret echoue, « -shutdown »
# n est jamais envoye, mais la terminaison forcee tue Steam quand meme. L arret
# propre degenererait en kill dur EN SILENCE, et le shortcuts.vdf que Steam
# s appretait a ecrire serait perdu — la panne meme que tout ce script existe
# pour empecher.
check("steam.exe est celui que steam-launch.ps1 lance",
      retro_sync.STEAM_EXE, _litteral(_launch_code, "SteamExe"))
check("la racine Steam est le dossier de ce steam.exe la",
      retro_sync.STEAM_EXE.rsplit("\\", 1)[0], retro_sync.STEAM_ROOT)

# Le marqueur de session : la commande qu Apollo SUIT pendant toute la session
# (apps.json.j2). Un renommage la-bas rendrait la sonde aveugle, et le script
# couperait des parties en cours sans jamais s en apercevoir.
_apps = (GUEST / "templates" / "apps.json.j2").read_text(encoding="utf-8")
_suivies = re.findall(r'"cmd":\s*"[^"]*?([\w.-]+\.ps1)', _apps)
check("apps.json.j2 declare encore une commande suivie par session",
      bool(_suivies), True)
check("l hote sonde la commande qu Apollo suit reellement",
      sorted(set(_suivies)), [retro_sync.SESSION_SCRIPT])
check("ce script existe bien dans la charge utile",
      (PROVISION / "assets" / retro_sync.SESSION_SCRIPT).is_file(), True)

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

# La cle package= : l identite de la CONSTRUCTION du paquet installee sur la
# console. Deux roues peuvent porter le meme « 0.1.0 » sans contenir le meme
# code, et c est la SEULE valeur qui les distingue. Les DEUX ecrivains doivent
# l ecrire : la comparaison de cles ci-dessus attrape un renommage d un seul
# cote, mais elle resterait verte si la cle disparaissait des deux — d ou les
# deux controles qui suivent, un par ecrivain.
check("Write-RetroStatus ecrit l identite du paquet", "package" in _cles_ps, True)
check("l hote l ecrit lui aussi", "package" in _cles_hote, True)
check("... entre emulation_root= et report:, des deux cotes",
      _cles_ps[_cles_ps.index("emulation_root") + 1:], ["package"])
check("le repli « identite inconnue » est nomme pareil des deux cotes",
      retro_sync.PACKAGE_UNKNOWN, _litteral(_retro_code, "RetroPackage"))
# Le contrat vit dans le fichier PRODUIT, jamais dans une prose ailleurs : qui
# ouvre le temoin doit y lire ce que package= veut dire sans avoir a retrouver
# le script qui l ecrit.
check("le temoin explique lui-meme a quoi sert package=",
      "package= identifie" in _status_ps1, True)
check("l hote ecrit vraiment la valeur qu on lui donne",
      retro_sync.parse_witness(retro_sync.format_witness(
          ["# en-tete"], "R1", "ok", "D:\\Emulation", [], when="W",
          package="0.1.0+20260829143512.a1b2c3d4"))[1]["package"],
      "0.1.0+20260829143512.a1b2c3d4")
check("... et replie sur « inconnue » quand personne n a su dire",
      retro_sync.parse_witness(_produit)[1]["package"],
      retro_sync.PACKAGE_UNKNOWN)

# Le partage par lequel la roue peut voyager. Rien d autre ne le peut :
# Guest.write_text encode tout en base64 DANS une ligne de commande bornee a
# 32 767 caracteres, et une roue pese cent fois cela. Les deux bouts sont
# ecrits ailleurs — la source cote hote dans domain.py, la lettre cote invite
# dans 35-shares.ps1 — donc on les relit LA-BAS.
_domain = (GUEST / "domain.py").read_text(encoding="utf-8")
_source_console = re.search(
    r'\{"source": "([^"]+)", "tag": "Console"', _domain).group(1)
_shares_code = code_only(
    (PROVISION / "35-shares.ps1").read_text(encoding="utf-8"))
_lettre_console = re.search(
    r"Tag = 'Console';\s*Letter = '(\w)'", _shares_code).group(1)
check("l hote depose les roues DANS le partage Console qu il exporte",
      str(retro_sync.HOST_WHEELS_SHARE).startswith(_source_console + "/"), True)
check("... et la console les lit par la lettre que 35-shares.ps1 lui donne",
      retro_sync.GUEST_WHEELS.split("\\")[0], _lettre_console + ":")
check("les deux bouts designent le meme sous-dossier du partage",
      str(retro_sync.HOST_WHEELS_SHARE)[len(_source_console) + 1:],
      retro_sync.GUEST_WHEELS.split("\\", 1)[1].replace("\\", "/"))
# Le manifeste du proprietaire vit deja sur ce partage-la : si les deux
# divergeaient, l un des deux ne serait plus monte du tout.
check("les roues voyagent par le partage qui porte deja le manifeste",
      retro_sync.GUEST_WHEELS.rsplit("\\", 1)[0],
      retro_sync.USER_MANIFEST.rsplit("\\", 1)[0])
# pip est lance par le python que l etape 32 installe, pas par « le python du
# PATH » : le declenchement vient de l hote, dans une autre session WinRM.
check("pip est lance par le python que l etape 32 installe",
      retro_sync.PYTHON_EXE,
      _litteral(_retro_code, "PythonRoot") + "\\python.exe")
check("... et l etape 32 compose bien ce chemin-la",
      "Join-Path $PythonRoot 'python.exe'" in _retro_code, True)

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

# --- 4 bis. L identite du paquet : la reference, et la decision ---------- #
# Deux roues peuvent porter le meme « 0.1.0 » sans contenir le meme code. Tant
# que rien ne les distingue, « pip install --upgrade » ne reinstalle RIEN
# (mesure du 2026-08-29 : « Requirement already satisfied »), et un correctif
# ecrit, teste et commite ici peut rester sans le moindre effet sur la console
# — l erreur obtenue decrivant alors le symptome d origine, exactement comme si
# le correctif etait faux. C est la dette D6. Ce qui suit est ce qui la
# CONSTATE : une roue de reference sur le disque de l hote, ce que la console
# dit executer, et la decision qui les oppose.

_tmp = pathlib.Path(tempfile.mkdtemp(prefix="nivuus-retro-identite-"))


def _roue(dossier, version):
    """Une roue factice : la seule entree qui compte, sa METADATA.

    Le nom de fichier echappe le « + » du segment local PEP 440 en « _ » ;
    c est precisement pour ca que la version doit etre lue DANS la roue.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"retro-{version.replace('+', '_')}-py3-none-any.whl"
    with zipfile.ZipFile(chemin, "w") as z:
        z.writestr(f"retro-{version}.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: retro\nVersion: {version}\n")
    return chemin


_VERSION_HOTE = "0.1.0+20260829143512.a1b2c3d4.g9f8e7d6"
_VERSION_CONSOLE = "0.1.0+20260101000000.cccc3333"

_vide = _tmp / "vide"
_vide.mkdir()
_ref = _tmp / "reference"
_roue(_ref, _VERSION_HOTE)

check("un wheelhouse sans roue n offre aucune reference",
      retro_sync.identite_roue(_vide), None)
check("un wheelhouse qui n existe pas non plus",
      retro_sync.identite_roue(_tmp / "nulle-part"), None)
check("la version de reference est lue dans la METADATA de la roue",
      retro_sync.identite_roue(_ref), _VERSION_HOTE)
# Le controle ci-dessus se satisferait d une lecture du nom de fichier si le
# nom portait la version telle quelle. Il ne la porte pas : pip echappe le
# « + ». Reconstituer la version a l envers est la devinette qui se trompe en
# silence, et ce controle-ci l interdit.
check("... et pas dans son nom de fichier, qui a echappe le « + »",
      "+" in sorted(_ref.glob("retro-*.whl"))[0].name, False)
_deux = _tmp / "deux"
_roue(_deux, "0.1.0+20260829143512.aaaaaaaa")
_roue(_deux, "0.1.0+20260830143512.bbbbbbbb")
check("deux roues cote a cote sont une ambiguite, pas une reference",
      retro_sync.identite_roue(_deux), None)

# La decision. FONCTION PURE, sur le modele de sync_refusal : elle rend le
# motif de refus, ou None quand c est autorise. C est ce qui la rend
# verifiable sans Windows, et c est la que vit le contrat.
check("la meme construction des deux cotes autorise",
      retro_sync.ecart_identite(_VERSION_HOTE, _VERSION_HOTE), None)
# Absence de reference n est PAS un ecart : refuser la punirait une console
# dont le wheelhouse a simplement ete nettoye.
check("sans roue de reference, il n y a rien a reprocher a la console",
      retro_sync.ecart_identite(_VERSION_CONSOLE, None), None)
check("... meme quand la console ne sait pas repondre non plus",
      retro_sync.ecart_identite(None, None), None)

_motif = retro_sync.ecart_identite(None, _VERSION_HOTE)
check("une console qui ne sait pas dire ce qu elle execute est REFUSEE",
      _motif is not None, True)
check("... et le refus dit que son paquet est anterieur",
      "ANT" in (_motif or ""), True)
check("... en nommant la construction que l hote a livree",
      _VERSION_HOTE in (_motif or ""), True)
check("... et en donnant le remede, sans quoi refuser serait cruel",
      "--reinstaller-le-paquet" in (_motif or ""), True)

_motif = retro_sync.ecart_identite(_VERSION_CONSOLE, _VERSION_HOTE)
check("deux constructions differentes sont REFUSEES", _motif is not None, True)
# Un refus qui ne dit pas ce qu il a lu envoie chercher dans la VM ce que
# l hote avait sous les yeux — meme regle que les refus de temoin.
check("... et le refus nomme les DEUX constructions",
      _VERSION_CONSOLE in (_motif or "") and _VERSION_HOTE in (_motif or ""),
      True)
check("... et donne le remede lui aussi",
      "--reinstaller-le-paquet" in (_motif or ""), True)


# --- 5. La sequence, contre un faux invite ------------------------------- #


class FakeGuest:
    """Ce que l hote a le droit de demander a la console, et rien de plus."""

    def __init__(self, fichiers=None, codes=None, leve=None,
                 retro_exe=retro_sync.RETRO_EXE, session=None,
                 steam="steam=propre exe=present", rapports=None):
        self.fichiers = dict(fichiers or {})
        self.codes = dict(codes or {})
        # Ce que chaque sous-commande REPOND. « retro identite » rend une
        # version, pas un « rapport identite » : la garde d identite se juge
        # sur ce que la console dit, donc le faux invite doit pouvoir le dire.
        self.rapports = dict(rapports or {})
        self.leve = leve or {}
        self.retro_exe = retro_exe
        self.session = session
        self.sessions_vues = 0
        self.steam = steam
        self.journal = []
        # Les arguments COMPLETS du dernier appel a chaque sous-commande : le
        # journal ci-dessus ne garde que le nom (args[0]), pour rester lisible
        # dans les scenarios de sequence. Un test sur ce que l hote PASSE a
        # `retro scan` a besoin de la ligne entiere.
        self.args_vus = {}

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
        """Les deux seuls scripts qui rendent quelque chose : la sonde de
        session et l arret de Steam. La reponse de session peut etre une
        LISTE, pour simuler une session qui commence en cours de route."""
        if retro_sync.SESSION_SCRIPT in script:
            self.journal.append(("session", None))
            reponses = (self.session if isinstance(self.session, list)
                        else [self.session])
            i = min(self.sessions_vues, len(reponses) - 1)
            self.sessions_vues += 1
            return 0, reponses[i] or "aucune", ""
        self.journal.append(("ps", script))
        return 0, self.steam, ""

    def execute(self, commande, quoi):
        """Le canal des commandes brutes du vrai invite (Guest.execute), par
        lequel passe la reinstallation du paquet. La surface doit etre la
        meme : un faux plus etroit prouverait le comportement d une API qui
        n existe pas."""
        self.journal.append(("execute", commande))
        return 0, ""

    def retro(self, args):
        self.journal.append(("retro", args[0]))
        self.args_vus[args[0]] = list(args)
        if args[0] in self.leve:
            raise self.leve[args[0]]
        return (self.codes.get(args[0], 0),
                self.rapports.get(args[0], f"rapport {args[0]}"))


class Cfg:
    guest_label = "la console"
    force = False
    emulation_root = retro_sync.EMULATION_ROOT
    steam_root = retro_sync.STEAM_ROOT
    steam_root_windows = retro_sync.STEAM_ROOT
    roms = retro_sync.ROMS_ROOT
    user_manifest = retro_sync.USER_MANIFEST
    user_profiles = retro_sync.USER_PROFILES
    bios = retro_sync.BIOS_ROOT
    inventory = retro_sync.INVENTORY
    retro_exe = retro_sync.RETRO_EXE
    steamgriddb_key = None
    # Un wheelhouse VIDE par defaut : les scenarios ci-dessous portent sur la
    # sequence, pas sur l identite, et l absence de reference laisse passer.
    wheelhouse = str(_vide)
    partage_roues = str(_tmp / "partage-inutilise")
    reinstaller_le_paquet = False


# Le faux invite doit offrir la MEME surface que le vrai, sinon ces scenarios
# prouvent le comportement d une API qui n existe pas.
_publiques = {n for n in dir(retro_sync.Guest)
              if not n.startswith("_") and callable(getattr(retro_sync.Guest, n))}
check("le faux invite couvre toute la surface du vrai",
      sorted(_publiques - set(dir(FakeGuest))), [])


# Ce que la console repond, lu par la fonction qui l interroge.
_g = FakeGuest(rapports={"identite": _VERSION_CONSOLE + "\n"})
check("l identite de la console est la premiere ligne de « retro identite »",
      retro_sync.identite_invitee(_g), _VERSION_CONSOLE)
# La sous-commande interrogee ici doit etre celle que l etape 32 appelle
# la-bas : un renommage cote paquet doit casser ICI, pas six mois plus tard
# sur une console qui refuserait de se synchroniser sans qu on sache pourquoi.
check("... relevee par la sous-commande que l etape 32 appelle elle aussi",
      f"& $retroExe {_g.args_vus['identite'][0]}" in _retro_code, True)
# Un code non nul n est PAS une panne a remonter : sur un paquet anterieur a
# D6 la sous-commande n existe pas, argparse rend 2, et c est le constat qu on
# cherche.
check("une console qui ne connait pas la sous-commande rend None, sans lever",
      retro_sync.identite_invitee(
          FakeGuest(codes={"identite": 2},
                    rapports={"identite": "usage: retro [-h] ..."})), None)
check("un rapport vide ne se fait pas passer pour une identite",
      retro_sync.identite_invitee(FakeGuest(rapports={"identite": "\n  \n"})),
      None)


def temoin(status, run="R1"):
    return retro_sync.format_witness(["# contrat"], run, status,
                                     retro_sync.EMULATION_ROOT, [])


class CfgForce(Cfg):
    force = True


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
# « retro identite » est ecarte de l ordre comme l est la relecture du temoin
# plus bas : c est une LECTURE, elle ne touche a rien, et la garde d identite
# qui la fait a sa propre place epinglee (section 5 ter et section 7).
_appels = [a for a in g.journal
           if a[0] in ("retro", "write", "remove", "ps")
           and a != ("retro", "identite")]
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
# LES BIOS SONT PORTES APRES L INSTALLATION ET AVANT LE SCAN, et cette place
# est le contrat. Apres, parce que « retro install » efface le dossier de
# chaque emulateur a chaque montee de version : les BIOS qui y avaient ete
# portes disparaissent avec lui, et un jeu qui demarrait cesse de demarrer
# sans que rien ne change du cote de la bibliotheque. Avant le scan, pour que
# le rapport decrive une console ou ils sont deja en place.
check("les BIOS sont mis en place avant le scan", _reste[0], ("retro", "bios"))
check("le scan precede la sentinelle", _reste[1], ("retro", "scan"))
# Sans --emulation-root-local, `retro scan` fabrique les chemins d executables
# depuis le manifeste SANS verifier qu ils existent : une installation
# partielle peuplerait Steam d entrees qui ne demarrent pas (voir la
# docstring du module). `retro scan` tourne DANS l invite (guest.retro), donc
# « cette machine » et « la console », du point de vue de cette commande-la,
# designent le MEME disque : la valeur locale doit etre celle de
# --emulation-root, pas un chemin cote hote.
# SANS --emulation-root-local, « retro bios » telecharge dans le dossier du
# proprietaire et NE PORTE RIEN chez les emulateurs. Le rapport serait alors
# VERT — « present, empreinte verifiee » — sur des BIOS qu aucun emulateur ne
# voit, ce qui est pire qu un rapport rouge. Retirer ce parametre rendrait
# l etape inerte en silence : c est pourquoi il est epingle ici.
_args_bios = g.args_vus["bios"]
check("les BIOS sont portes chez les emulateurs, pas seulement telecharges",
      "--emulation-root-local" in _args_bios, True)
if "--emulation-root-local" in _args_bios:
    check("... avec la racine que la commande, qui tourne dans l invite, "
          "atteint reellement",
          _args_bios[_args_bios.index("--emulation-root-local") + 1],
          retro_sync.EMULATION_ROOT)
check("les BIOS sont cherches dans le dossier du proprietaire, sur le partage",
      _args_bios[_args_bios.index("--bios") + 1], retro_sync.BIOS_ROOT)

_args_scan = g.args_vus["scan"]
check("le scan verifie les executables sur le disque local",
      "--emulation-root-local" in _args_scan, True)
if "--emulation-root-local" in _args_scan:
    check("... avec la racine que la commande, qui tourne dans l invite, "
          "atteint elle-meme (meme valeur que --emulation-root)",
          _args_scan[_args_scan.index("--emulation-root-local") + 1],
          retro_sync.EMULATION_ROOT)
check("la sentinelle est posee AVANT l arret de Steam",
      _reste[2], ("write", retro_sync.HOLD_FILE))
check("Steam est arrete avant la synchronisation", _reste[3][0], "ps")
check("... et c est bien un arret de Steam", "steam" in _reste[3][1], True)
check("la synchronisation vient ensuite", _reste[4], ("retro", "sync"))
check("la sentinelle est retiree apres",
      _reste[5], ("remove", retro_sync.HOLD_FILE))
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

# --- 5 bis. Une partie en cours ------------------------------------------ #
# C est le seul chemin qui laisse la console PIRE qu avant, et il ne se repare
# pas tout seul : Steam coupe emporte le jeu lance depuis lui, et rien ne le
# relance avant la prochaine connexion.

check("sans session, rien ne s oppose a la synchronisation",
      retro_sync.session_refusal(None, False), None)
check("une session en cours refuse la synchronisation",
      retro_sync.session_refusal("PID 42 depuis hier", False) is not None, True)
check("... et le refus nomme ce qu il protege",
      "PID 42" in retro_sync.session_refusal("PID 42 depuis hier", False), True)
check("--force passe outre, puisque c est une commande manuelle",
      retro_sync.session_refusal("PID 42 depuis hier", True), None)

g = invite("ok", session="PID 42 depuis 2026-08-26")
_code, _dit, _signale = lancer(g)
check("une session en cours arrete tout avant le premier telechargement",
      _code, 7)
check("... sans rejouer l installation",
      [a for a in actions(g, "retro") if a[1] != "identite"], [])
check("... sans toucher au temoin ni a la sentinelle", actions(g, "write"), [])
check("... et en disant QUOI est en cours", "PID 42" in _signale, True)

# La session peut commencer PENDANT l installation et le scan, qui se comptent
# en minutes : la seconde sonde est celle qui protege reellement.
g = invite("ok", session=[None, "PID 43 depuis maintenant"])
_code, _dit, _signale = lancer(g)
check("une session qui commence en cours de route est vue a temps", _code, 7)
check("... Steam n est pas arrete", actions(g, "ps"), [])
check("... rien n est synchronise", ("retro", "sync") in g.journal, False)
check("... et la sentinelle posee entre-temps est rendue",
      retro_sync.HOLD_FILE in g.fichiers, False)

g = invite("ok", session="PID 44")
_code, _dit, _signale = lancer(g, CfgForce())
check("--force synchronise malgre la session", _code, 0)
check("... en avertissant franchement de ce qui est perdu",
      "force" in _signale and "PID 44" in _signale, True)

# L arret de Steam est juge sur la table des processus RELUE, pas annonce.
g = invite("ok", steam="steam=vivant exe=present")
_leve = _essai_erreur(lambda: lancer(g))
check("un Steam qui survit a la terminaison forcee leve",
      isinstance(_leve, retro_sync.GuestError), True)
check("... en disant que rien n a ete ecrit",
      "shortcuts.vdf" in str(_leve), True)
check("... et rien n est synchronise", ("retro", "sync") in g.journal, False)
check("... la sentinelle etant rendue quand meme",
      retro_sync.HOLD_FILE in g.fichiers, False)

g = invite("ok", steam="steam=force exe=absent")
_code, _dit, _signale = lancer(g)
check("un steam.exe introuvable ne passe pas inapercu", _code, 0)
check("... et l hote dit que seule la force a agi",
      retro_sync.STEAM_EXE in _signale and "force" in _signale, True)

g = invite("ok", steam="rien du tout")
check("un invite muet sur le sort de Steam leve plutot que de continuer",
      isinstance(_essai_erreur(lambda: lancer(g)), retro_sync.GuestError), True)

# --- 5 ter. L identite du paquet, dans la sequence ----------------------- #
# La garde arrive AVANT le premier telechargement, pour la raison que la garde
# de session ci-dessus donne deja : un refus qui arrive apres dix minutes
# d installation est un refus qui arrive trop tard.


class CfgIdentite(Cfg):
    wheelhouse = str(_ref)
    partage_roues = str(_tmp / "partage")


class CfgRemede(CfgIdentite):
    reinstaller_le_paquet = True


class GuestPip(FakeGuest):
    """Un invite dont « pip install » remplace REELLEMENT le paquet.

    `apres` est ce que « retro identite » repondra une fois pip passe : c est
    la seule facon de distinguer une reinstallation faite d une
    reinstallation crue faite, qui est le defaut d origine de D6.
    """

    def __init__(self, apres, **kw):
        super().__init__(**kw)
        self.apres = apres
        self.pip = []

    def execute(self, commande, quoi):
        self.pip.append(commande)
        self.journal.append(("pip", None))
        self.rapports["identite"] = self.apres
        return 0, f"Successfully installed retro-{self.apres}"


# a) l ecart REFUSE, et absolument rien d autre n a lieu.
g = invite("ok", rapports={"identite": _VERSION_CONSOLE})
_code, _dit, _signale = lancer(g, CfgIdentite())
check("un paquet en ecart refuse la synchronisation", _code, 8)
check("... et le refus est SIGNALE, pas glisse dans la sortie normale",
      "REFUS" in _signale.upper(), True)
# Un refus qui ne dit pas ce qu il a lu envoie chercher dans la VM ce que
# l hote avait sous les yeux — les deux roues portent le meme « 0.1.0 ».
check("... en nommant les DEUX constructions",
      _VERSION_CONSOLE in _signale and _VERSION_HOTE in _signale, True)
check("... et le remede, sans quoi la console devient non synchronisable",
      "--reinstaller-le-paquet" in _signale, True)
check("un refus d identite n installe rien",
      [a for a in actions(g, "retro") if a[1] != "identite"], [])
check("... n ecrit rien : ni temoin, ni sentinelle", actions(g, "write"), [])
# La session est sondee avant, et c est une LECTURE : elle ne derange rien.
# Ce qui compte est qu aucun arret de Steam n ait eu lieu.
check("... et ne derange meme pas Steam",
      [a for a in actions(g, "ps") if a[1] != "session"], [])

# b) une console anterieure a « retro identite » : meme refus, autre message.
g = invite("ok", codes={"identite": 2},
           rapports={"identite": "usage: retro [-h] ..."})
_code, _dit, _signale = lancer(g, CfgIdentite())
check("une console qui ne sait pas dire ce qu elle execute est refusee",
      _code, 8)
check("... et le refus dit que son paquet est ANTERIEUR", "ANT" in _signale, True)
check("... sans rien installer",
      [a for a in actions(g, "retro") if a[1] != "identite"], [])

# c) la meme construction des deux cotes : tout se deroule, et le temoin porte
# desormais QUELLE construction tourne la-bas.
g = invite("ok", rapports={"identite": _VERSION_HOTE + "\n"})
_code, _dit, _signale = lancer(g, CfgIdentite())
check("la meme construction des deux cotes laisse tout se derouler", _code, 0)
check("... et le temoin porte l identite relevee sur la console",
      retro_sync.parse_witness(
          g.fichiers[retro_sync.STATUS_FILE])[1]["package"], _VERSION_HOTE)

# d) aucune roue de reference : ce n est PAS un ecart. Refuser la punirait une
# console dont le wheelhouse a simplement ete nettoye. Mais se taire ferait
# croire a une verification qui n a pas eu lieu.
g = invite("ok", rapports={"identite": _VERSION_CONSOLE})
_code, _dit, _signale = lancer(g)
check("sans roue de reference, l hote laisse passer", _code, 0)
check("... en avertissant qu aucun ecart ne peut etre constate",
      "wheelhouse" in _signale, True)
check("... et le temoin porte quand meme ce que la console a dit",
      retro_sync.parse_witness(
          g.fichiers[retro_sync.STATUS_FILE])[1]["package"], _VERSION_CONSOLE)
# Une console muette, sans reference non plus : le temoin le DIT plutot que
# d inventer une version.
g = invite("ok", codes={"identite": 2})
check("une console muette et aucune reference : rien a reprocher", lancer(g)[0], 0)
check("... et le temoin dit « inconnue » plutot que d inventer",
      retro_sync.parse_witness(
          g.fichiers[retro_sync.STATUS_FILE])[1]["package"],
      retro_sync.PACKAGE_UNKNOWN)

# e) LE REMEDE. Sans lui, refuser serait cruel : la console deviendrait non
# synchronisable sans geste de sortie, et la seule facon de remettre le paquet
# a jour serait de reconstruire la charge utile, l ISO, et de reprovisionner.
_partage = pathlib.Path(CfgRemede.partage_roues)
g = GuestPip(_VERSION_HOTE,
             fichiers={retro_sync.RUN_FILE: "R1\r\n",
                       retro_sync.STATUS_FILE: temoin("ok"),
                       retro_sync.RETRO_EXE: ""},
             rapports={"identite": _VERSION_CONSOLE})
_code, _dit, _signale = lancer(g, CfgRemede())
check("le remede remet la console a niveau et la synchronisation reprend",
      _code, 0)
# La roue voyage par le PARTAGE, jamais par WinRM : Guest.write_text encode
# tout en base64 dans une ligne de commande bornee a 32 767 caracteres.
check("la roue de l hote est copiee sur le partage que la console lit",
      sorted(p.name for p in _partage.glob("*.whl")),
      sorted(p.name for p in _ref.glob("*.whl")))
check("pip est relance hors ligne, depuis ce partage vu par la console",
      bool(g.pip) and all(x in g.pip[0] for x in (
          "--no-index", retro_sync.GUEST_WHEELS, "--upgrade", "retro")), True)
check("... par le python que l etape 32 installe, pas « celui du PATH »",
      bool(g.pip) and retro_sync.PYTHON_EXE in g.pip[0], True)
# Une reinstallation qu on CROIT faite est exactement le defaut d origine :
# l identite doit etre RELUE, jamais supposee.
check("l identite est relue apres la reinstallation, jamais supposee",
      len([a for a in g.journal if a == ("retro", "identite")]), 2)
check("... et le temoin porte l identite d APRES la reinstallation",
      retro_sync.parse_witness(
          g.fichiers[retro_sync.STATUS_FILE])[1]["package"], _VERSION_HOTE)

# f) le remede qui ne prend pas : on refuse quand meme, et on le dit.
g = GuestPip(_VERSION_CONSOLE,
             fichiers={retro_sync.RUN_FILE: "R1\r\n",
                       retro_sync.STATUS_FILE: temoin("ok"),
                       retro_sync.RETRO_EXE: ""},
             rapports={"identite": _VERSION_CONSOLE})
_code, _dit, _signale = lancer(g, CfgRemede())
check("une reinstallation qui n a rien change refuse quand meme", _code, 8)
check("... en disant que l ecart PERSISTE apres le remede",
      "PERSISTE" in _signale, True)
check("... et rien n est synchronise", ("retro", "sync") in g.journal, False)
check("... ni ecrit", actions(g, "write"), [])

# g) le remede n est JAMAIS automatique : remplacer le paquet est un geste, et
# le faire en passant mettrait une console de salon a la merci de l etat de
# l arbre de l hote.
g = invite("ok", rapports={"identite": _VERSION_CONSOLE})
lancer(g, CfgIdentite())
check("sans l option, rien n est jamais reinstalle", actions(g, "pip"), [])

# h) les options, et leurs defauts.
_cfg = retro_sync.build_parser().parse_args([])
check("--wheelhouse a pour defaut la roue que fetch_payload.py depose",
      _cfg.wheelhouse, str(retro_sync.WHEELHOUSE))
check("--partage-roues a pour defaut le partage Console de l hote",
      _cfg.partage_roues, str(retro_sync.HOST_WHEELS_SHARE))
check("la reinstallation est fermee par defaut", _cfg.reinstaller_le_paquet, False)
check("--reinstaller-le-paquet l ouvre a la demande",
      retro_sync.build_parser().parse_args(
          ["--reinstaller-le-paquet"]).reinstaller_le_paquet, True)
# Le code de sortie du refus : 7 est pris par la session de streaming, 3 par le
# paquet absent. La docstring du module porte la liste, et un lecteur qui la
# consulte doit y trouver le 8.
check("la docstring du module annonce le code 8",
      "8" in retro_sync.__doc__.split("Codes de sortie")[1], True)


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
    retro_sync.cap_report("\n".join("x" * 120 for _ in range(500))),
    package=_VERSION_HOTE)
_journal.clear()
_vrai.write_text(retro_sync.STATUS_FILE, _pire)
_ligne = "powershell -encodedcommand " + base64.b64encode(
    _journal[-1][1].encode("utf-16-le")).decode("ascii")
check("le plus gros temoin possible tient dans une ligne de commande Windows",
      len(_ligne) < 32767, True)
# Un plafond seul se satisferait d une marge de trois caracteres : l en-tete du
# contrat peut grossir cote invite sans que ce fichier-ci change, donc on exige
# de la place devant.
check("... avec de la marge pour un contrat qui s allongerait cote invite",
      32767 - len(_ligne) >= 8000, True)


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
check("un code de sortie vide leve une erreur nommee, pas une trace Python",
      isinstance(_essai_erreur(retro_sync.Guest(run=_reponse(0, "EXIT=\n")).retro,
                               ["install"]), retro_sync.GuestError), True)
# Les deux pannes ne se confondent pas : une sortie SANS marqueur veut dire
# que le script PowerShell lui-meme n a pas tourne comme prevu, et le message
# doit le dire — sinon elle se deguise en « code de sortie illisible » et le
# lecteur cherche du cote de retro, qui n y est pour rien.
_sans_marqueur = _essai_erreur(retro_sync.Guest(run=_reponse(0, "bruit")).retro,
                               ["install"])
check("une sortie sans marqueur de code leve plutot que de mentir",
      isinstance(_sans_marqueur, retro_sync.GuestError), True)
check("... en nommant le transport, pas retro",
      "WinRM a rendu" in str(_sans_marqueur), True)

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
    _pose = [c for n in _corps for c in _appels(n, "place_hold")]
    check("la sentinelle est posee SOUS le finally qui la retire",
          len(_pose), 1)
    check("... et avant l arret de Steam",
          bool(_pose) and _pose[0].lineno
          < [c for n in _corps for c in _appels(n, "stop_steam")][0].lineno,
          True)
    _demarrage = [c for n in _corps for c in ast.walk(n)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                  and c.func.attr == "start"]
    check("le fil qui rajeunit la sentinelle demarre lui aussi sous le finally",
          bool(_demarrage), True)
_refresh = _appels(_fn, "refresh_witness")
_garde = _appels(_fn, "sync_refusal")
check("le temoin est rafraichi AVANT d etre relu par la garde",
      bool(_refresh) and bool(_garde) and _refresh[0].lineno < _garde[0].lineno,
      True)
check("la garde precede la pose de la sentinelle",
      bool(_garde) and bool(_pose) and _garde[0].lineno < _pose[0].lineno, True)
_sessions = _appels(_fn, "streaming_session")
check("la session est sondee deux fois : tot, puis juste avant d arreter Steam",
      len(_sessions), 2)
# La garde d identite : APRES le retro.exe absent (sinon une console sans
# retrogaming se ferait refuser au lieu d etre dite non concernee), APRES la
# sonde de session, et avant tout le reste. Le comportement est deja epingle
# plus haut ; ceci epingle sa PLACE, qu un refus correct mais tardif
# satisferait sans le dire.
#
# L ordre session-puis-identite n est pas cosmetique. --reinstaller-le-paquet
# lance un « pip install » DANS l invite : c est une mutation, et la garde de
# session existe pour qu aucune mutation n ait lieu pendant qu on joue. Place
# avant elle, le remede s executait puis le script refusait en code 7 — un
# refus qui arrive apres coup, alors qu un refus doit vouloir dire que rien
# ne s est produit.
_ecart = _appels(_fn, "ecart_identite")
check("la garde d identite est appelee dans la sequence", len(_ecart), 1)
_exists = [n for n in ast.walk(_fn) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute) and n.func.attr == "exists"]
check("... apres la garde qui verifie que retro.exe est la",
      bool(_ecart) and bool(_exists) and _ecart[0].lineno > _exists[0].lineno,
      True)
check("... APRES la sonde de session, qui garde la reinstallation",
      bool(_ecart) and _ecart[0].lineno > _sessions[0].lineno, True)
# La propriete qui compte vraiment : la seule MUTATION de ce bloc est sous la
# garde de session. Un refus code 7 doit signifier que rien n a ete fait.
_reinst = _appels(_fn, "reinstaller_paquet")
check("la reinstallation du paquet est appelee dans la sequence",
      len(_reinst), 1)
check("... et APRES la sonde de session : on ne modifie pas l invite "
      "pendant qu on joue",
      bool(_reinst) and _reinst[0].lineno > _sessions[0].lineno, True)
check("... et avant tout telechargement, la sonde de session l etant aussi",
      bool(_reinst) and bool(_refresh) and _reinst[0].lineno < _refresh[0].lineno,
      True)
check("... et avant le rafraichissement du temoin",
      bool(_ecart) and bool(_refresh) and _ecart[0].lineno < _refresh[0].lineno,
      True)
check("... la seconde sonde etant sous le finally qui rend la sentinelle",
      bool(_try) and any(c.lineno > _try.lineno for c in _sessions), True)

shutil.rmtree(_tmp, ignore_errors=True)

# --- Le scan recoit les DEUX sources du proprietaire --------------------- #
#
# Le manifeste dit quels emulateurs existent ; les profils disent quels
# DOSSIERS leur appartiennent. Avec le seul manifeste, un systeme du
# proprietaire devient INCONNU, et la synchronisation retire de Steam les jeux
# qu il servait -- en silence, en purgeant leur artwork. Mesure du 2026-08-29 :
# six jeux Switch perdus ainsi, parce que ryujinx.toml vit dans G:\retro\
# profiles et que scan ne le recevait pas.
_gp = invite("ok")
lancer(_gp, Cfg())
_scan_args = _gp.args_vus.get("scan", [])
check("le scan est lance une fois",
      len([a for a in actions(_gp, "retro") if a[1] == "scan"]), 1)
check("... avec le manifeste du proprietaire",
      "--user-manifest" in _scan_args, True)
check("... ET avec ses profils, sans quoi ses systemes sont inconnus",
      "--user-profiles" in _scan_args, True)
check("... et le dossier de profils est celui du partage",
      _scan_args[_scan_args.index("--user-profiles") + 1]
      if "--user-profiles" in _scan_args else None, retro_sync.USER_PROFILES)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - la sequence hote refuse un temoin qui ne dit pas « ok », "
      "et rend la sentinelle quoi qu il arrive")
