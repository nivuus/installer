<#
    La commande SUIVIE par Apollo pendant toute la session de streaming.

    Elle ne lance rien. Steam est demarre par l entree "detached" de
    apps.json, et c est deliberement separe : Apollo tue le groupe de processus
    de la commande suivie quand le client se deconnecte, or un jeu lance depuis
    Steam serait dans ce groupe. Un Steam detache survit a la deconnexion, la
    partie continue, et la VM s hiberne plus tard sur inactivite comme prevu.

    Ce script sert deux fins que rien d autre ne couvrait :

    1. QUITTER STEAM DOIT FERMER LA SESSION MOONLIGHT. Apollo termine une
       session quand sa commande suivie rend la main. Sans commande suivie -
       le cas jusqu ici, ou l application ne declarait que "detached" -
       Apollo n a aucun processus a surveiller : la session survit a Steam et
       le client reste devant un fond d ecran qu il doit fermer a la main.

    3. MASQUER LE CURSEUR, EN BIG PICTURE SEULEMENT. Big Picture se pilote a
       la manette : le pointeur ne bouge jamais et reste pose au milieu de
       l ecran de la TV. Le mode Desktop, lui, lance le client Steam NORMAL,
       qui se pilote a la souris - masquer pour la session entiere le rendrait
       inutilisable, d ou le pilotage par -Mode, ici comme pour la
       maximisation. Le pourquoi complet (l historique nomousy, la cle Apollo
       qui n existe pas, ce que ce masquage n est pas) est dans l en-tete de
       steam-cursor.ps1, dot-source plus bas.

    2. MAXIMISER LA FENETRE. Cette tache vivait dans un prep-cmd, ce qui est le
       pire endroit possible : Apollo attend la fin d un prep-cmd AVANT de
       lancer l application, et la boucle ne sortait jamais avant son delai.
       Mesure sur l invite le 2026-08-26, journal Apollo :
           21:16:04  Executing Do Cmd: [maximize-steam.ps1]
           21:19:06  Spawning [D:\Steam\steam.exe]
       soit 182 secondes de retard a CHAQUE session, Steam n etant demarre
       qu apres. Ici la boucle sort des que la fenetre est maximisee, et elle
       ne retarde plus rien puisque Steam demarre en parallele.
#>
param([ValidateSet('Desktop', 'BigPicture')][string]$Mode = 'Desktop')

# Une erreur ne doit pas faire sortir ce script : sortir, c est fermer la
# session du client. On journalise et on continue.
$ErrorActionPreference = 'Continue'

# Steam qui disparait puis revient tout seul est une MISE A JOUR qui se
# relance, pas quelqu un qui quitte. Les deux sont indiscernables dans la table
# des processus et seul le delai les separe : on laisse a Steam le temps de
# revenir avant de conclure que la session est finie.
#
# CE DELAI EST DIRECTEMENT CE QU ATTEND L UTILISATEUR APRES AVOIR QUITTE STEAM,
# et il a ete resserre deux fois sur mesure : 30 s (ressenti "une minute"),
# puis 10 s (mesure : 12 s de bout en bout), puis ici. Un Steam qui se relance
# pour sa mise a jour respawne dans la foulee - il lance son successeur avant
# de mourir - la ou un Steam quitte reste absent pour toujours ; trois secondes
# separent deja les deux cas.
#
# LA MARGE EST DESORMAIS MINCE, ET C EST ASSUME. Se tromper de cote ne coute
# qu une session a rouvrir : Steam etant lance detache, sa mise a jour se
# poursuit sans le flux, et le lanceur retrouvera un Steam vivant a la
# reconnexion suivante. Le defaut inverse - attendre - se paie a CHAQUE sortie.
$RestartGraceSeconds = 3

# Au TOUT PREMIER lancement, Steam telecharge sa propre mise a jour avant
# d ouvrir la moindre fenetre - 242 Mo mesures le 2026-08-26. Attendre moins,
# c est fermer la session pendant que Steam demarre encore.
$AppearDeadlineSeconds = 300

function Test-SteamRunning {
    [bool](Get-Process -Name 'steam' -ErrorAction SilentlyContinue)
}

# --- 1. Attendre que le Steam detache paraisse -------------------------------
$deadline = (Get-Date).AddSeconds($AppearDeadlineSeconds)
while (-not (Test-SteamRunning) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if (-not (Test-SteamRunning)) {
    Write-Warning "Steam n a pas demarre en $AppearDeadlineSeconds s - fin de session"
    return
}

# --- 2. Surveiller Steam, et maximiser sa fenetre au passage -----------------
# UNE SEULE BOUCLE, et c est le coeur de ce script. Les deux taches tenaient
# autrefois dans deux boucles successives : maximiser d abord, surveiller
# ensuite. Un Steam deja lance et replie dans la zone de notification n a PAS de
# fenetre principale, donc pas de titre a reconnaitre, donc la premiere boucle
# allait au bout de son delai -- 180 s pendant lesquelles quitter Steam ne
# fermait rien. Mesure du 2026-08-27 : session ouverte a 11:16:05, Apollo
# n a rendu la main qu a 11:19:08, soit 180 s de boucle plus les 3 s de grace.
# Surveiller est la tache qui ne doit JAMAIS attendre ; maximiser se greffe
# dessus et echoue sans consequence.
$MaximizeDeadlineSeconds = 180

# Les deux greffes - maximiser, masquer le curseur - passent par user32.dll : un
# seul Add-Type les couvre, pose sans condition de mode. Il a lieu apres que
# Steam est paru et avant la boucle : il ne retarde donc ni l un ni l autre.
try {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NivuusWin {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern IntPtr CreateCursor(IntPtr hInst, int xHot, int yHot, int w, int h, byte[] andPlane, byte[] xorPlane);
    [DllImport("user32.dll")] public static extern bool SetSystemCursor(IntPtr hcur, uint id);
    [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint action, uint param, IntPtr pv, uint winIni);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    public const int SW_MAXIMIZE = 3;
    public const int SM_CXCURSOR = 13;
    public const int SM_CYCURSOR = 14;
    public const uint SPI_SETCURSORS = 0x0057;
}
'@
}
catch {
    Write-Warning "greffes user32 indisponibles : $_"
}

# Steam restaure sa geometrie precedente, qui sur un ecran virtuel tout neuf est
# une petite fenetre dans un coin. En Big Picture la fenetre est deja plein
# ecran : rien a maximiser, on part donc avec le travail deja fait.
$maximized = ($Mode -ne 'Desktop')
$maximizeDeadline = (Get-Date).AddSeconds($MaximizeDeadlineSeconds)

function Set-SteamMaximized {
    try {
        $p = Get-Process -Name 'steam' -ErrorAction SilentlyContinue |
             Where-Object { $_.MainWindowTitle -eq 'Steam' } | Select-Object -First 1
        if (-not $p) { return $false }
        [NivuusWin]::ShowWindow($p.MainWindowHandle, [NivuusWin]::SW_MAXIMIZE) | Out-Null
        return $true
    }
    catch {
        Write-Warning "fenetre non maximisee : $_"
        return $true   # ne pas reessayer en boucle sur une erreur durable
    }
}

# En Desktop le client Steam se pilote a la souris : rien a masquer, on part donc
# avec le travail deja fait - comme Big Picture part avec la maximisation deja
# faite. Le mode decide une fois, jamais a chaque tour de boucle.
$cursorHidden = ($Mode -ne 'BigPicture')
# Meme borne que la maximisation, pour une autre raison : elle n attend aucune
# fenetre, elle empeche un echec durable d etre reessaye deux fois par seconde.
$cursorDeadline = (Get-Date).AddSeconds($MaximizeDeadlineSeconds)

# Les deux fonctions de curseur vivent a cote, dans steam-cursor.ps1, avec leur
# propre en-tete - meme decoupe que apollo-drivers.ps1 pour 25-apollo.ps1, et
# pour la meme raison de 200 lignes. Elles se servent du type [NivuusWin] pose
# ci-dessus : l ordre compte. Si le fichier manque, on ne masque rien plutot que
# de crier deux fois par seconde dans la boucle.
try { . (Join-Path $PSScriptRoot 'steam-cursor.ps1') }
catch {
    Write-Warning "masquage du curseur indisponible : $_"
    $cursorHidden = $true
}

# LE FILET, ET IL N EST PAS FACULTATIF. Apollo tue le groupe de processus de la
# commande suivie a la deconnexion du client : une session Big Picture peut
# mourir sans jamais atteindre sa restauration, en bas de ce fichier. Le mode
# Desktop, seul a avoir besoin de la souris, repart donc TOUJOURS de curseurs
# relus du registre, quoi qu il soit arrive a la session precedente.
if ($Mode -eq 'Desktop') { Restore-SystemCursors }

while ($true) {
    if (Test-SteamRunning) {
        if (-not $maximized -and (Get-Date) -lt $maximizeDeadline) {
            $maximized = Set-SteamMaximized
        }
        if (-not $cursorHidden -and (Get-Date) -lt $cursorDeadline) {
            $cursorHidden = Set-CursorHidden
        }
        # Le sondage s ajoute tel quel au delai de grace, et son cout est nul
        # face aux heures que dure une session : une interrogation de la table
        # des processus, deux fois par seconde.
        Start-Sleep -Milliseconds 500
        continue
    }
    $graceEnd = (Get-Date).AddSeconds($RestartGraceSeconds)
    $cameBack = $false
    while ((Get-Date) -lt $graceEnd) {
        Start-Sleep -Milliseconds 500
        if (Test-SteamRunning) { $cameBack = $true; break }
    }
    if (-not $cameBack) { break }
}

# Sortie NORMALE (Steam ferme) : on rend le curseur avant de partir. Ce n est pas
# le seul chemin par lequel ce script meurt, d ou le filet en tete de mode
# Desktop plus haut.
if ($Mode -eq 'BigPicture') { Restore-SystemCursors }

# Rendre la main FERME la session Moonlight. C est le comportement voulu.
Write-Host 'Steam ferme - fin de la session de streaming'
