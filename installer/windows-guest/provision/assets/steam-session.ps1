<#
    La commande SUIVIE par Apollo pendant toute la session de streaming.

    Elle ne lance rien. Steam est demarre par l entree « detached » de
    apps.json, et c est deliberement separe : Apollo tue le groupe de processus
    de la commande suivie quand le client se deconnecte, or un jeu lance depuis
    Steam serait dans ce groupe. Un Steam detache survit a la deconnexion, la
    partie continue, et la VM s hiberne plus tard sur inactivite comme prevu.

    Ce script sert deux fins que rien d autre ne couvrait :

    1. QUITTER STEAM DOIT FERMER LA SESSION MOONLIGHT. Apollo termine une
       session quand sa commande suivie rend la main. Sans commande suivie —
       le cas jusqu ici, ou l application ne declarait que « detached » —
       Apollo n a aucun processus a surveiller : la session survit a Steam et
       le client reste devant un fond d ecran qu il doit fermer a la main.

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
$RestartGraceSeconds = 30

# Au TOUT PREMIER lancement, Steam telecharge sa propre mise a jour avant
# d ouvrir la moindre fenetre — 242 Mo mesures le 2026-08-26. Attendre moins,
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

# --- 2. Maximiser la fenetre, puis passer a autre chose -----------------------
# Steam restaure sa geometrie precedente, qui sur un ecran virtuel tout neuf est
# une petite fenetre dans un coin. En Big Picture la fenetre est deja plein
# ecran et il n y a rien a maximiser.
if ($Mode -eq 'Desktop') {
    try {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NivuusWin {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    public const int SW_MAXIMIZE = 3;
}
'@
        $windowDeadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $windowDeadline) {
            $p = Get-Process -Name 'steam' -ErrorAction SilentlyContinue |
                 Where-Object { $_.MainWindowTitle -eq 'Steam' } | Select-Object -First 1
            if ($p) {
                [NivuusWin]::ShowWindow($p.MainWindowHandle, [NivuusWin]::SW_MAXIMIZE) | Out-Null
                break
            }
            Start-Sleep -Milliseconds 500
        }
    }
    catch {
        Write-Warning "fenetre non maximisee : $_"
    }
}

# --- 3. Tenir la session aussi longtemps que Steam vit ------------------------
while ($true) {
    if (Test-SteamRunning) {
        Start-Sleep -Seconds 3
        continue
    }
    $graceEnd = (Get-Date).AddSeconds($RestartGraceSeconds)
    $cameBack = $false
    while ((Get-Date) -lt $graceEnd) {
        Start-Sleep -Seconds 2
        if (Test-SteamRunning) { $cameBack = $true; break }
    }
    if (-not $cameBack) { break }
}

# Rendre la main FERME la session Moonlight. C est le comportement voulu.
Write-Host 'Steam ferme - fin de la session de streaming'
