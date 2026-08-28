#!/usr/bin/env python3
"""Synchronise la bibliothèque rétro de la console, depuis l'hôte.

C'est la PREMIÈRE synchronisation, et toutes les suivantes. L'étape 32 du
provisionnement (`32-retro.ps1`) installe les émulateurs mais ne synchronise
jamais : les partages ne sont montés qu'à l'étape 35, donc `G:\\ROMs` n'existe
pas encore quand elle tourne, et le manifeste du propriétaire
(`G:\\retro\\emulators.toml`) n'est pas lisible non plus. Ce script reprend là où
elle s'arrête, une fois la console prête.

LA SÉQUENCE, ET POURQUOI ELLE EST DANS CET ORDRE :

 1. `retro install`, AVEC le manifeste utilisateur cette fois. C'est aussi la
    réparation d'une installation partielle : une URL morte chez un éditeur
    n'emporte pas le provisionnement (l'étape 32 avertit sans bloquer), donc
    c'est ici qu'on rejoue.
 2. le témoin durable est RAFRAÎCHI avec le résultat de ce passage. Sans cela
    un « partial » survivrait à sa propre réparation, et l'installation
    réparée resterait indéfiniment refusée.
 3. `retro scan`, hors sentinelle : il ne touche pas à Steam, et le tenir hors
    de la fenêtre de retenue garde celle-ci aussi courte que possible.
 4. la sentinelle `steam.hold`, PUIS l'arrêt de Steam, PUIS `retro sync`. Cet
    ordre-là exactement : c'est `steam-launch.ps1` qui relance Steam à chaque
    session Moonlight, et il ne consulte la sentinelle qu'au moment de lancer.
    Arrêter Steam avant de poser la sentinelle laisserait une session ouvrir
    un Steam qui réécrirait `shortcuts.vdf` à sa fermeture — la synchronisation
    serait perdue sans le moindre message.
 5. le retrait de la sentinelle, dans un `finally`, quoi qu'il arrive.

CE QUI EST REFUSÉ, ET POURQUOI C'EST LE CŒUR DE CE SCRIPT :

`retro scan` fabrique les chemins d'exécutables depuis le MANIFESTE sans
vérifier qu'ils existent, et la garde de racine de `retro sync` les accepte
puisqu'ils sont bien sous la racine d'émulation. Synchroniser après une
installation partielle produirait donc, sans autre protection, une
bibliothèque peuplée d'entrées qui ne démarrent pas — cassé, pas absent, et
c'est le propriétaire qui le découvre depuis son canapé.

C'est pourquoi l'appel à `retro scan` ci-dessous passe `--emulation-root-local`
: donné, `retro` vérifie lui-même que chaque exécutable existe et ignore (en
le signalant) les systèmes dont l'émulateur manque. `retro scan` tourne DANS
l'invité (`Guest.retro`, via WinRM) : « cette machine » et « la console », du
point de vue de la commande qui tourne réellement, désignent donc le MÊME
disque — la valeur passée est celle de `--emulation-root`, pas un chemin côté
hôte. Retirer ce paramètre rendrait ce correctif inerte en silence ; un test
de `test_windows_guest_retro_sync.py` épingle sa présence et sa valeur.

Cette vérification est une défense EN PROFONDEUR, pas la première ligne : le
témoin durable ci-dessous refuse de synchroniser AVANT même que `scan` ne
tourne dès qu'un passage n'est pas « ok » (y compris « partial »).

D'où le témoin durable que l'étape 32 laisse sur le volume persistant
(`D:\\state\\retro.status`, écrit par `provision/assets/retro-status.ps1`, qui
inscrit son propre contrat en tête du fichier produit). Seul « ok » autorise la
synchronisation ; « disabled » dit qu'il n'y a pas de rétrogaming sur cette
console, ce qui n'est pas une panne. Ce script le lit AVANT de synchroniser, et
refuse en nommant ce qu'il dit.

LE RETROGAMING EST OPTIONNEL. Sur une console où il n'a pas été demandé, ce
script le DIT et sort en succès, au lieu d'échouer sur une commande
introuvable.

IL REFUSE AUSSI DE COUPER UNE PARTIE EN COURS. Arrêter Steam pendant une
session de streaming coupe le jeu du propriétaire sans un mot, et rien ne
relance Steam avant sa prochaine connexion : c'est le seul geste de ce script
qui laisse la console dans un état PIRE qu'avant, et il ne se répare pas tout
seul. `--force` passe outre, en le disant.

Usage :
    python3 retro_sync.py [--guest-ip 192.168.3.2] [--steamgriddb-key CLE]

Codes de sortie : 0 succès (ou fonctionnalité non activée), 2 invité
injoignable, 3 rétrogaming introuvable ou témoin absent, 4 le témoin refuse la
synchronisation, 5 le scan a échoué, 6 la synchronisation a échoué, 7 une
session de streaming est en cours.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import os
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINRM_EXEC = HERE / "winrm_exec.py"

# --- Ce que l'invité porte. Ces valeurs sont écrites en PowerShell ailleurs
# dans ce dépôt ; le test les épingle contre CES fichiers-là plutôt que de les
# recopier, pour qu'un renommage d'un côté ne survive pas au silence de l'autre
# (le pont retro.psd1 s'était rompu exactement comme ça).
# provision/assets/retro-status.ps1
STATUS_FILE = r"D:\state\retro.status"
RUN_FILE = r"C:\nivuus\state\provision.started"
RUN_UNKNOWN = "hors-run-all"
# provision/assets/steam-launch.ps1
HOLD_FILE = r"C:\nivuus\state\steam.hold"
HOLD_MAX_AGE_S = 300
# 32-retro.ps1
EMULATION_ROOT = r"D:\Emulation"
RETRO_EXE = r"C:\Python\Scripts\retro.exe"

# steam-launch.ps1 ($SteamExe). Épinglé par le test contre CE fichier-là, et
# pas seulement par correction : si les deux chemins divergent, le `Test-Path`
# de l'arrêt échoue, `-shutdown` n'est jamais envoyé — mais la terminaison
# forcée, elle, tue Steam quand même. L'arrêt propre dégénérerait en kill dur
# EN SILENCE, et le shortcuts.vdf que Steam s'apprêtait à écrire serait perdu.
STEAM_ROOT = r"D:\Steam"
STEAM_EXE = r"D:\Steam\steam.exe"
# templates/apps.json.j2 : la commande qu'Apollo SUIT pendant toute la session
# de streaming. Elle vit exactement aussi longtemps que la session, ce qui en
# fait le seul marqueur fiable depuis l'hôte — Apollo, lui, tourne en service
# et serait présent session ou pas.
SESSION_SCRIPT = "steam-session.ps1"
ROMS_ROOT = r"G:\ROMs"
USER_MANIFEST = r"G:\retro\emulators.toml"
INVENTORY = r"C:\nivuus\state\retro-inventory.json"

STATUS_OK = "ok"
STATUS_DISABLED = "disabled"
STATUS_PARTIAL = "partial"
STATUS_MANIFEST_UNREADABLE = "manifest-unreadable"

# La sentinelle expire au bout de HOLD_MAX_AGE_S, pour qu'un hôte qui disparaît
# en cours de route ne prive pas indéfiniment de Steam une console sans clavier
# ni écran. Une synchronisation qui dure plus longtemps que ça n'a rien
# d'anormal — l'artwork se télécharge jeu par jeu — donc on la rajeunit
# pendant qu'elle travaille : l'expiration continue de protéger contre un hôte
# MORT, sans se déclencher contre un hôte VIVANT.
HOLD_TOUCH_INTERVAL_S = 60

# Le témoin est lu par un humain, dans six mois, sur une console sans clavier :
# il porte le rapport, pas un journal. Au-delà, la queue est ce qui compte (le
# rapport d'installation nomme ses échecs à la fin).
#
# LA BORNE EN CARACTÈRES N'EST PAS COSMÉTIQUE. Le témoin voyage vers l'invité
# DANS une ligne de commande : pywinrm encode le script PowerShell en UTF-16LE
# puis en base64, et le passe à `powershell -encodedcommand`. Windows refuse
# une ligne de commande de plus de 32767 caractères, et l'encodage multiplie la
# taille par ~2,7. Un rapport bavard ferait donc échouer l'écriture du témoin —
# c'est-à-dire précisément l'écriture qui doit survivre à l'incident.
MAX_REPORT_LINES = 200
MAX_REPORT_CHARS = 4000

_MISSING = 44  # sortie convenue de nos scripts PowerShell : « fichier absent »


class GuestError(RuntimeError):
    """L'invité est injoignable, ou une commande n'a pas rendu ce qu'il faut."""


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _winrm_run(kind: str, script: str, guest_ip: str | None = None):
    """Une commande dans l'invité, par le même chemin que testdomain.py.

    winrm_exec.py lit le mot de passe dans un fichier, jamais depuis argv :
    l'appeler en sous-processus, plutôt que d'importer pywinrm ici, garde ce
    secret hors de la table des processus de cet hôte.
    """
    env = {**os.environ, "LC_ALL": "C"}
    if guest_ip:
        env["GUEST_IP"] = guest_ip
    proc = subprocess.run([sys.executable, str(WINRM_EXEC), kind, script],
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


class Guest:
    """L'invité, vu comme quelques gestes plutôt que comme du PowerShell.

    Tout ce qui traverse WinRM voyage en base64 : la sortie de PowerShell 5.1
    passe par la page de code de la console (CP850 ici), et le rapport de
    `retro` est accentué. Sans cela le témoin DURABLE recevrait du mojibake,
    et personne ne le corrigerait six mois plus tard.
    """

    def __init__(self, run=_winrm_run, guest_ip: str | None = None,
                 retro_exe: str = RETRO_EXE):
        self._run = run
        self._guest_ip = guest_ip
        self.retro_exe = retro_exe

    def ps(self, script: str):
        return self._run("ps", script, self._guest_ip)

    def exists(self, path: str) -> bool:
        rc, out, err = self.ps(
            f"if (Test-Path -LiteralPath {_ps_quote(path)}) "
            f"{{ exit 0 }} else {{ exit {_MISSING} }}")
        if rc == _MISSING:
            return False
        if rc != 0:
            raise GuestError(f"impossible de tester {path} dans l'invité : "
                             f"{(err or out).strip()}")
        return True

    def read_text(self, path: str) -> str | None:
        """Le contenu du fichier, ou None s'il n'existe pas."""
        rc, out, err = self.ps(
            f"$p = {_ps_quote(path)}; "
            f"if (-not (Test-Path -LiteralPath $p)) {{ exit {_MISSING} }}; "
            "[Console]::Out.Write("
            "[Convert]::ToBase64String([System.IO.File]::ReadAllBytes($p)))")
        if rc == _MISSING:
            return None
        if rc != 0:
            raise GuestError(f"lecture de {path} impossible dans l'invité : "
                             f"{(err or out).strip()}")
        return base64.b64decode("".join(out.split())).decode("utf-8", "replace")

    def write_text(self, path: str, text: str) -> None:
        blob = base64.b64encode(text.encode("utf-8")).decode("ascii")
        parent = str(Path(path.replace("\\", "/")).parent).replace("/", "\\")
        rc, out, err = self.ps(
            f"New-Item -ItemType Directory -Force -Path {_ps_quote(parent)} "
            "| Out-Null; "
            f"[System.IO.File]::WriteAllBytes({_ps_quote(path)}, "
            f"[Convert]::FromBase64String({_ps_quote(blob)}))")
        if rc != 0:
            raise GuestError(f"écriture de {path} impossible dans l'invité : "
                             f"{(err or out).strip()}")

    def remove(self, path: str) -> None:
        rc, out, err = self.ps(
            f"Remove-Item -LiteralPath {_ps_quote(path)} -Force "
            "-ErrorAction SilentlyContinue")
        if rc != 0:
            raise GuestError(f"suppression de {path} impossible dans "
                             f"l'invité : {(err or out).strip()}")

    def retro(self, args):
        """`retro <args>` dans l'invité : (code de sortie, rapport).

        PYTHONUTF8/PYTHONIOENCODING et OutputEncoding sont posés ENSEMBLE :
        le premier décide de ce que Python écrit, le second de ce que
        PowerShell croit lire. Un seul des deux laisse le rapport accentué
        illisible dans le témoin.
        """
        quoted = " ".join(_ps_quote(a) for a in args)
        rc, out, err = self.ps(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; "
            f"$sortie = & {_ps_quote(self.retro_exe)} {quoted} 2>&1 | Out-String; "
            "$code = $LASTEXITCODE; "
            "[Console]::Out.Write(\"EXIT=$code`n\"); "
            "[Console]::Out.Write([Convert]::ToBase64String("
            "[System.Text.Encoding]::UTF8.GetBytes($sortie)))")
        tete, _, corps = out.partition("\n")
        if not tete.startswith("EXIT="):
            raise GuestError(
                "l'invité n'a pas rendu le code de sortie de `retro "
                f"{' '.join(args)}` (WinRM a rendu {rc}) : "
                f"{(err or out).strip()[:400]}")
        rapport = base64.b64decode("".join(corps.split())).decode("utf-8", "replace")
        brut = tete[len("EXIT="):].strip()
        try:
            return int(brut), rapport
        except ValueError:
            # $LASTEXITCODE vide (la commande n'a jamais démarré) rend
            # « EXIT= » : une trace Python ici serait illisible depuis un
            # canapé, alors que la cause tient en une phrase.
            raise GuestError(
                f"`retro {' '.join(args)}` n'a pas rendu de code de sortie "
                f"exploitable ({brut!r}) : la commande n'a probablement jamais "
                f"démarré dans l'invité. {rapport.strip()[:400]}") from None


# --------------------------------------------------------------------------
# Le témoin : lecture, décision, réécriture. Tout ce qui suit est pur, donc
# vérifiable sans Windows — et c'est là que vit le contrat.
# --------------------------------------------------------------------------

def parse_witness(text: str):
    """(en-tête, clés, rapport) d'un témoin.

    L'en-tête est le contrat que retro-status.ps1 inscrit en tête du fichier
    produit. On le REPREND tel quel quand on réécrit le témoin, plutôt que
    d'en recopier une version ici : deux textes indépendants divergent, et
    c'est le lecteur du témoin qui paye la divergence.
    """
    header, cles, rapport = [], {}, []
    phase = "header"
    for ligne in text.splitlines():
        if phase == "header":
            if not ligne.strip() or ligne.startswith("#"):
                header.append(ligne)
                continue
            phase = "cles"
        if phase == "cles":
            if ligne.strip() == "report:":
                phase = "report"
                continue
            cle, sep, valeur = ligne.partition("=")
            if sep:
                cles[cle.strip()] = valeur.strip()
            continue
        rapport.append(ligne)
    return header, cles, rapport


def install_status(exit_code: int) -> str:
    """Le status que MÉRITE un `retro install`, selon son code de sortie.

    Même lecture que 32-retro.ps1, qui écrit le témoin la première fois : 0
    tout est installé, 1 au moins un émulateur manque et le rapport nomme
    lesquels, le reste le manifeste n'a pas pu être lu.
    """
    if exit_code == 0:
        return STATUS_OK
    if exit_code == 1:
        return STATUS_PARTIAL
    return STATUS_MANIFEST_UNREADABLE


def sync_refusal(status: str | None, run: str | None, run_courant: str) -> str | None:
    """Le motif de refus de synchroniser, ou None quand c'est autorisé.

    Fermé par défaut : tout ce qui n'est pas un « ok » du passage courant est
    un refus, y compris un status inconnu — un témoin qu'on ne sait pas lire
    ne prouve rien, et une bibliothèque d'entrées mortes est pire qu'une
    bibliothèque absente.
    """
    if status is None:
        return (f"aucun témoin sur {STATUS_FILE} : l'étape 32 du "
                "provisionnement n'a jamais écrit sur ce volume. Rien ne dit "
                "que les émulateurs sont installés, et synchroniser sans le "
                "savoir peuple Steam d'entrées qui ne démarrent pas. Vérifier "
                r"C:\nivuus\provision.log et D:\state\PROVISION.failed.")
    # DÉFENSE EN PROFONDEUR, et rien de plus dans la séquence de ce script :
    # le rafraîchissement écrit toujours le passage COURANT juste avant que
    # cette garde ne relise le fichier, donc ce cas n'y survient pas. Il
    # existe pour l'appelant qui lirait un témoin sans l'avoir rafraîchi, et
    # pour que la règle du contrat vive dans la fonction qui juge.
    if run is not None and run != run_courant:
        return (f"le témoin porte run={run} alors que le passage de "
                f"provisionnement courant est {run_courant} : il décrit une "
                "installation ANTÉRIEURE, conservée par le volume persistant. "
                "Son status ne dit rien de ce qui vient de se passer. Rejouer "
                "le provisionnement, ou relancer ce script (qui rejoue "
                "« retro install » et rafraîchit le témoin).")
    if status == STATUS_OK:
        return None
    if status == STATUS_DISABLED:
        return ("le témoin dit « disabled » : le rétrogaming n'a pas été "
                "demandé sur cette console, il n'y a rien à synchroniser. "
                "Cocher l'option et reconstruire la machine virtuelle pour "
                "l'activer.")
    if status == STATUS_PARTIAL:
        return ("le témoin dit « partial » : au moins un émulateur n'est pas "
                "installé, et le rapport du témoin nomme lesquels. `retro "
                "scan` fabriquerait quand même leurs chemins sans vérifier "
                "qu'ils existent : la bibliothèque Steam se peuplerait "
                "d'entrées qui ne démarrent pas. Lever la cause (URL morte, "
                "réseau) puis relancer ce script, qui rejoue l'installation.")
    if status == STATUS_MANIFEST_UNREADABLE:
        return ("le témoin dit « manifest-unreadable » : le manifeste n'a pas "
                "pu être lu et AUCUN émulateur n'est installé. Corriger "
                f"{USER_MANIFEST} (le rapport du témoin dit ce qui cloche) "
                "puis relancer ce script.")
    if status in ("started", "interrupted"):
        return (f"le témoin dit « {status} » : l'étape 32 n'a pas abouti, "
                "l'installation des émulateurs est incomplète ou nulle. Le "
                "rapport du témoin porte la cause (error=). Lever la cause "
                "puis relancer ce script.")
    return (f"le témoin porte un status inconnu de ce script : « {status} ». "
            "Refus par précaution : la liste des status autorisés est en tête "
            f"de {STATUS_FILE}, et seul « ok » autorise la synchronisation.")


# Une seule ligne d'en-tête ajoutée par l'hôte, parce que le contrat que le
# témoin porte dit « écrit par 32-retro.ps1 » et que ce n'est plus vrai après
# ce passage. Elle est ajoutée une fois, jamais empilée à chaque exécution.
HOST_NOTE = ("# Ce temoin a ete RAFRAICHI depuis l hote par retro_sync.py, "
             "apres avoir rejoue « retro install » : le status ci-dessous "
             "decrit ce passage-la.")


def format_witness(header, run: str, status: str, emulation_root: str,
                   rapport, when: str | None = None) -> str:
    """Le témoin, dans la forme EXACTE que Write-RetroStatus produit.

    Même ordre de clés, mêmes fins de ligne, même absence de BOM : le fichier
    doit rester lisible par quiconque connaît l'un des deux écrivains.
    """
    if when is None:
        when = datetime.datetime.now().astimezone().isoformat()
    lignes = list(header)
    if HOST_NOTE not in lignes:
        lignes.append(HOST_NOTE)
    lignes += [f"run={run}", f"status={status}", f"when={when}",
               f"emulation_root={emulation_root}", "report:"]
    lignes += list(rapport)
    return "\r\n".join(lignes) + "\r\n"


def cap_report(text: str):
    """La queue du rapport, bornée en lignes ET en caractères.

    La queue, jamais la tête : c'est la fin d'un rapport d'installation qui
    nomme ce qui a échoué.
    """
    lignes = text.splitlines()
    coupe = max(0, len(lignes) - MAX_REPORT_LINES)
    lignes = lignes[coupe:]
    while lignes and sum(len(ln) + 2 for ln in lignes) > MAX_REPORT_CHARS:
        lignes.pop(0)
        coupe += 1
    if coupe:
        return [f"[... {coupe} ligne(s) omise(s) par retro_sync.py ...]"] + lignes
    return lignes


# --------------------------------------------------------------------------
# Les gestes de la séquence.
# --------------------------------------------------------------------------

def streaming_session(guest) -> str | None:
    """Ce qui identifie la session de streaming en cours, ou None.

    Apollo SUIT steam-session.ps1 pendant toute la session (apps.json.j2) : le
    processus existe tant que quelqu'un joue, et disparaît avec la session.
    """
    rc, out, err = guest.ps(
        "$p = @(Get-CimInstance Win32_Process -Filter "
        "\"Name = 'powershell.exe'\" -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.CommandLine -like '*{SESSION_SCRIPT}*' }}); "
        "if ($p.Count -eq 0) { [Console]::Out.Write('aucune') } else { "
        "[Console]::Out.Write(($p | ForEach-Object "
        "{ \"PID $($_.ProcessId) depuis $($_.CreationDate)\" }) -join ' ; ') }")
    if rc != 0:
        raise GuestError("impossible de savoir si une session de streaming est "
                         f"en cours : {(err or out).strip()}")
    marque = out.strip()
    return None if marque in ("", "aucune") else marque


def session_refusal(session: str | None, force: bool) -> str | None:
    """Le motif de refus quand quelqu'un joue, ou None.

    C'est le seul chemin de ce script qui laisse la console dans un état PIRE
    qu'avant, et il ne se répare pas tout seul : arrêter Steam coupe la partie
    en cours et tout jeu lancé depuis lui, et rien ne le relance avant la
    prochaine connexion. C'est une commande manuelle, donc l'opérateur doit
    pouvoir passer outre — mais en sachant ce qu'il interrompt.
    """
    if session is None:
        return None
    if force:
        return None
    return (f"une session de streaming est EN COURS ({session}). "
            "Synchroniser arrêterait Steam, donc la partie en cours et tout "
            "jeu lancé depuis lui, sans prévenir personne — et rien ne le "
            "relancera avant la prochaine connexion Moonlight. Attendre la fin "
            "de la session, ou relancer avec --force en connaissance de cause.")


def warn_session(session: str | None, force: bool) -> None:
    if session is not None and force:
        print(f"avertissement : --force donné alors qu'une session de "
              f"streaming est en cours ({session}) ; Steam va être arrêté et "
              "la partie en cours perdue.", file=sys.stderr)


def read_run_id(guest) -> str:
    """L'identifiant du passage de provisionnement courant.

    Même repli que retro-status.ps1 quand run-all.ps1 n'a rien laissé : les
    deux côtés doivent nommer l'absence de la même façon, sinon la
    comparaison échoue là où elle devrait passer.
    """
    contenu = guest.read_text(RUN_FILE)
    return RUN_UNKNOWN if contenu is None else contenu.strip()


def place_hold(guest) -> None:
    """La sentinelle qui empêche steam-launch.ps1 de relancer Steam."""
    guest.write_text(HOLD_FILE,
                     "pose par retro_sync.py (hote) le "
                     + datetime.datetime.now().astimezone().isoformat())


def touch_hold(guest) -> None:
    guest.write_text(HOLD_FILE,
                     "rajeunie par retro_sync.py (hote) le "
                     + datetime.datetime.now().astimezone().isoformat())


def remove_hold(guest) -> None:
    guest.remove(HOLD_FILE)


class HoldKeeper:
    """Rajeunit la sentinelle tant que la synchronisation travaille.

    Le fil est démon et s'arrête sur l'événement : si CE processus meurt, plus
    rien ne rajeunit la sentinelle et elle expire d'elle-même — l'expiration
    protège toujours contre un hôte disparu, jamais contre un hôte occupé.
    """

    def __init__(self, guest, interval: float = HOLD_TOUCH_INTERVAL_S,
                 toucher=touch_hold):
        self._guest = guest
        self._interval = interval
        self._toucher = toucher
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._boucle, daemon=True)

    def _boucle(self):
        while not self._stop.wait(self._interval):
            try:
                self._toucher(self._guest)
            except Exception as exc:  # noqa: BLE001 - jamais fatal
                print(f"avertissement : la sentinelle n'a pas pu être "
                      f"rajeunie ({exc}) ; elle expire dans "
                      f"{HOLD_MAX_AGE_S} s", file=sys.stderr)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()


def stop_steam(guest) -> str:
    """Arrête Steam, proprement d'abord, et rend ce qui s'est réellement passé.

    `-shutdown` laisse Steam écrire son propre shortcuts.vdf AVANT que la
    synchronisation ne le réécrive ; le tuer d'emblée ferait perdre ce que le
    propriétaire a fait dans Steam depuis son dernier démarrage. La force
    n'arrive qu'après, pour ne pas rester bloqué sur une fenêtre modale que
    personne ne peut fermer sur une console sans clavier.

    L'invité RELIT la table des processus après la terminaison forcée plutôt
    que d'annoncer son succès : un Steam encore vivant réécrirait
    shortcuts.vdf à sa fermeture, et la synchronisation serait perdue en
    silence — le message doit venir d'ici, pas du paquet, et avant l'écriture.
    """
    rc, out, err = guest.ps(
        f"$exe = {_ps_quote(STEAM_EXE)}; "
        "$exeEtat = if (Test-Path -LiteralPath $exe) { 'present' } "
        "else { 'absent' }; $etat = 'absent'; "
        "if (Get-Process -Name 'steam' -ErrorAction SilentlyContinue) { "
        "  $etat = 'propre'; "
        "  if ($exeEtat -eq 'present') "
        "{ Start-Process -FilePath $exe -ArgumentList '-shutdown' }; "
        "  $limite = (Get-Date).AddSeconds(45); "
        "  while ((Get-Date) -lt $limite -and "
        "(Get-Process -Name 'steam' -ErrorAction SilentlyContinue)) "
        "{ Start-Sleep -Seconds 2 }; "
        "  if (Get-Process -Name 'steam' -ErrorAction SilentlyContinue) { "
        "    $etat = 'force'; "
        "    Get-Process -Name 'steam' -ErrorAction SilentlyContinue | "
        "Stop-Process -Force -ErrorAction SilentlyContinue; "
        "    Start-Sleep -Seconds 2; "
        "    if (Get-Process -Name 'steam' -ErrorAction SilentlyContinue) "
        "{ $etat = 'vivant' } } }; "
        "[Console]::Out.Write(\"steam=$etat exe=$exeEtat\")")
    if rc != 0:
        raise GuestError(f"impossible d'arrêter Steam dans l'invité : "
                         f"{(err or out).strip()}")
    etats = dict(m.split("=", 1) for m in out.split() if "=" in m)
    etat, exe = etats.get("steam"), etats.get("exe")
    if exe == "absent":
        # Le chemin d'ici ne désigne plus le Steam de la console : l'arrêt
        # propre n'a pas pu être demandé, et seule la force a agi.
        print(f"avertissement : {STEAM_EXE} est introuvable dans l'invité ; "
              "l'arrêt propre n'a pas pu être demandé et Steam n'a été arrêté "
              "que par la force — ce qu'il n'avait pas encore écrit est perdu.",
              file=sys.stderr)
    if etat == "vivant":
        raise GuestError(
            "Steam est TOUJOURS en cours après l'arrêt propre puis la "
            "terminaison forcée : synchroniser maintenant serait perdu, "
            "puisqu'il réécrirait shortcuts.vdf à sa fermeture. Rien n'a été "
            "écrit ; chercher ce qui le maintient en vie.")
    if etat is None:
        raise GuestError(f"l'invité n'a pas dit ce qu'il est advenu de Steam : "
                         f"{out.strip()[:200]!r}")
    print({"absent": "Steam ne tournait pas",
           "propre": "Steam s'est arrêté proprement (-shutdown)",
           "force": "Steam n'a pas répondu au -shutdown et a été terminé de "
                    "force"}[etat])
    return etat


def refresh_witness(guest, ancien: str | None, run: str, status: str,
                    rapport: str, emulation_root: str) -> None:
    """Réécrit le témoin avec le résultat du `retro install` qu'on vient de
    rejouer. Sans ça, un « partial » survivrait à sa propre réparation."""
    header, _, _ = parse_witness(ancien) if ancien else ([], {}, [])
    if not header:
        header = ["# Temoin de l etape 32 (retrogaming). Le contrat complet "
                  "(les six status, et lequel autorise la synchronisation) est "
                  "dans provision/assets/retro-status.ps1, qui l inscrit "
                  "normalement ici meme."]
    guest.write_text(STATUS_FILE, format_witness(
        header, run, status, emulation_root, cap_report(rapport)))


# --------------------------------------------------------------------------
# La séquence.
# --------------------------------------------------------------------------

def synchronise(guest, cfg) -> int:
    run_courant = read_run_id(guest)
    temoin = guest.read_text(STATUS_FILE)
    _, cles, _ = parse_witness(temoin) if temoin else ([], {}, [])
    status_lu = cles.get("status")

    # Le rétrogaming est OPTIONNEL. Un témoin qui dit « disabled » POUR CE
    # PASSAGE est la parole même du propriétaire : on ne rejoue pas une
    # installation qu'il n'a pas demandée, et on ne remplace pas son choix par
    # un « ok » en rafraîchissant le témoin par-dessus.
    if status_lu == STATUS_DISABLED and cles.get("run") == run_courant:
        print("le rétrogaming n'est pas activé sur cette console : le témoin "
              f"{STATUS_FILE} dit « disabled » pour le passage courant "
              f"({run_courant}). Rien à synchroniser. Pour l'activer : cocher "
              "l'option dans l'assistant, puis reconstruire la machine "
              "virtuelle.")
        return 0

    # Sur une console où il n'a pas été demandé il n'y a pas de `retro.exe`,
    # et lancer la séquence échouerait sur une commande introuvable — un
    # message qui ressemble à une panne alors que c'est un choix. On regarde
    # donc AVANT de lancer quoi que ce soit.
    if not guest.exists(cfg.retro_exe):
        if status_lu == STATUS_DISABLED or temoin is None:
            dit = ("le témoin dit « disabled »" if status_lu == STATUS_DISABLED
                   else "aucun témoin de l'étape 32 n'existe sur ce volume")
            print("le rétrogaming n'est pas activé sur cette console : "
                  f"{cfg.retro_exe} n'existe pas et {dit}. Rien à "
                  "synchroniser. Pour l'activer : cocher l'option dans "
                  "l'assistant, puis reconstruire la machine virtuelle.")
            return 0
        print(f"error: {cfg.retro_exe} est absent alors que le témoin dit "
              f"« {status_lu} » : le paquet retro n'a pas fini de s'installer. "
              r"Lire C:\nivuus\provision.log, puis rejouer l'étape 32.",
              file=sys.stderr)
        return 3

    # Quelqu'un joue-t-il ? On demande AVANT le premier téléchargement : un
    # refus qui arrive après dix minutes d'installation est un refus qui
    # arrive trop tard. La question est reposée juste avant d'arrêter Steam,
    # parce qu'une session peut commencer entre-temps.
    session = streaming_session(guest)
    refus = session_refusal(session, cfg.force)
    if refus is not None:
        print(f"error: {refus}", file=sys.stderr)
        return 7
    warn_session(session, cfg.force)

    # 1. L'installation, rejouée AVEC le manifeste du propriétaire : il vit
    # sur le partage, que l'étape 32 ne pouvait pas encore lire.
    print(f"« retro install » sur {cfg.guest_label}...")
    code, rapport = guest.retro(
        ["install", "--emulation-root", cfg.emulation_root,
         "--user-manifest", cfg.user_manifest])
    print(rapport.rstrip())
    status = install_status(code)

    # 2. Le témoin, rafraîchi : il décrit désormais CE passage.
    refresh_witness(guest, temoin, run_courant, status, rapport,
                    cfg.emulation_root)
    print(f"témoin rafraîchi : {STATUS_FILE} → status={status}")

    # 3. La garde. Elle lit ce qui vient d'être écrit, jamais ce qu'on croit
    # avoir écrit : un refus doit venir du fichier que l'invité porte.
    relu = guest.read_text(STATUS_FILE)
    _, cles_relues, _ = parse_witness(relu) if relu else ([], {}, [])
    refus = sync_refusal(cles_relues.get("status"), cles_relues.get("run"),
                         run_courant)
    if refus is not None:
        print(f"error: synchronisation REFUSÉE — {refus}", file=sys.stderr)
        return 4

    # 4. L'inventaire. Hors sentinelle : il ne touche pas à Steam, et la
    # fenêtre de retenue doit rester aussi courte que possible.
    print("« retro scan »...")
    code, rapport = guest.retro(
        ["scan", "--roms", cfg.roms, "--roms-windows", cfg.roms,
         "--emulation-root", cfg.emulation_root,
         "--emulation-root-local", cfg.emulation_root,
         "--user-manifest", cfg.user_manifest, "--output", cfg.inventory])
    print(rapport.rstrip())
    if code != 0:
        print("error: le scan des ROMs a échoué, rien n'a été synchronisé "
              "(Steam n'a même pas été arrêté).", file=sys.stderr)
        return 5

    # 5. La fenêtre : sentinelle, arrêt de Steam, synchronisation. La
    # sentinelle D'ABORD : entre l'arrêt de Steam et l'écriture, une session
    # Moonlight relancerait Steam, qui réécrirait shortcuts.vdf à sa
    # fermeture — la synchronisation serait perdue sans un mot.
    #
    # La pose et le fil sont SOUS le try : un échec au démarrage du fil
    # laisserait sinon une sentinelle sans chemin de retrait, bornée par sa
    # seule expiration.
    keeper = HoldKeeper(guest)
    try:
        place_hold(guest)
        keeper.start()
        # La session a pu commencer pendant l'installation et le scan, qui se
        # comptent en minutes. La sentinelle est déjà posée, donc aucune
        # NOUVELLE session ne verra Steam démarrer ; c'est la session déjà en
        # cours qu'on refuse d'interrompre.
        session = streaming_session(guest)
        refus = session_refusal(session, cfg.force)
        if refus is not None:
            print(f"error: {refus}", file=sys.stderr)
            return 7
        warn_session(session, cfg.force)
        stop_steam(guest)
        args = ["sync", "--steam-root", cfg.steam_root,
                "--steam-root-windows", cfg.steam_root_windows,
                "--emulation-root", cfg.emulation_root,
                "--inventory", cfg.inventory]
        if cfg.steamgriddb_key:
            args += ["--steamgriddb-key", cfg.steamgriddb_key]
        code, rapport = guest.retro(args)
    finally:
        # Quoi qu'il arrive, y compris si la synchronisation a levé : une
        # sentinelle oubliée prive la console de Steam. Son expiration est un
        # filet contre un hôte MORT, pas une excuse pour ne pas la retirer.
        keeper.stop()
        try:
            remove_hold(guest)
        except Exception as exc:  # noqa: BLE001 - ne masque jamais l'échec réel
            print(f"avertissement : la sentinelle {HOLD_FILE} n'a pas pu être "
                  f"retirée ({exc}) ; elle expire d'elle-même dans "
                  f"{HOLD_MAX_AGE_S} s", file=sys.stderr)

    print(rapport.rstrip())
    if code != 0:
        print(f"error: « retro sync » a rendu {code} ; la bibliothèque n'est "
              "pas à jour (le rapport ci-dessus dit pourquoi).", file=sys.stderr)
        return 6
    print("bibliothèque synchronisée ; Steam repartira à la prochaine session "
          "Moonlight (steam-launch.ps1).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Synchronise la bibliothèque rétro de la console")
    ap.add_argument("--guest-ip", default=None,
                    help="adresse de la console (défaut : celle de "
                         "winrm_exec.py, 192.168.3.2)")
    ap.add_argument("--emulation-root", default=EMULATION_ROOT)
    ap.add_argument("--steam-root", default=STEAM_ROOT,
                    help="chemin par lequel la CONSOLE lit shortcuts.vdf ; "
                         "tout tourne dans l'invité, donc identique à "
                         "--steam-root-windows")
    ap.add_argument("--steam-root-windows", default=STEAM_ROOT)
    ap.add_argument("--roms", default=ROMS_ROOT)
    ap.add_argument("--user-manifest", default=USER_MANIFEST,
                    help="manifeste du propriétaire, sur le partage ; son "
                         "absence est normale, jamais une erreur")
    ap.add_argument("--inventory", default=INVENTORY)
    ap.add_argument("--retro-exe", default=RETRO_EXE)
    ap.add_argument("--force", action="store_true",
                    help="synchroniser MÊME si une session de streaming est "
                         "en cours ; Steam sera arrêté et la partie perdue")
    ap.add_argument("--steamgriddb-key", default=os.environ.get("STEAMGRIDDB_KEY"),
                    help="clé d'API pour l'artwork ; passée à l'invité, donc "
                         "visible dans sa table des processus le temps de la "
                         "synchronisation (défaut : $STEAMGRIDDB_KEY)")
    return ap


def main(argv=None) -> int:
    cfg = build_parser().parse_args(argv)
    cfg.guest_label = cfg.guest_ip or "la console"
    guest = Guest(guest_ip=cfg.guest_ip, retro_exe=cfg.retro_exe)
    return synchronise(guest, cfg)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
