<#
    Demarre Steam, mais SEULEMENT une fois qu Apollo a pose un vrai affichage.

    C est l entree « detached » des deux applications d apps.json. Detache, donc
    hors du groupe de processus qu Apollo tue a la fin d une session : un jeu
    lance depuis ce Steam survit a une deconnexion du client.

    L ATTENTE EST LA RAISON D ETRE DE CE SCRIPT. L interface de Steam est du
    Chromium (CEF), et CEF choisit son moteur de rendu UNE FOIS, au demarrage,
    parmi les adaptateurs qui pilotent un affichage. Or hors session cette
    machine n en a aucun sur la RTX 4070 — mesure du 2026-08-26, invite au
    repos :
        NVIDIA GeForce RTX 4070             VideoModeDescription vide
        Microsoft Basic Display Adapter     1280 x 800 a 1 Hz   <- le bureau
        SudoMaker Virtual Display Adapter   800 x 600
    Un Steam demarre dans cet etat retombe sur SwiftShader, un rasteriseur
    LOGICIEL, pour toute la duree du processus (webhelper_gpu.txt :
    gpu_compositing = disabled_software). La petite fenetre du bureau y survit ;
    Big Picture, qui repeint 5120x1440 en entier, non — d ou « ca ne laggue
    qu en Big Picture ».

    Le journal existe pour que la prochaine session tranche sans avoir a
    deviner : il enregistre ce que le script a observe et par quelle condition
    il est sorti.
#>
param([ValidateSet('Desktop', 'BigPicture')][string]$Mode = 'Desktop')

$ErrorActionPreference = 'Continue'
$SteamExe = 'D:\Steam\steam.exe'
$Log = 'C:\nivuus\apollo\steam-launch.log'

# Borne haute VOLONTAIREMENT COURTE. Attendre protege le rendu ; attendre trop
# reproduit le defaut qu on vient de corriger, ou un prep-cmd retardait chaque
# session de 182 secondes. Passe ce delai on lance quand meme : un Steam en
# rendu logiciel vaut mieux qu un ecran vide.
$WaitSeconds = 45

function Get-Adapters {
    Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Select-Object Name, VideoModeDescription, CurrentRefreshRate
}

function Write-Log([string]$Message) {
    try { Add-Content -Path $Log -Value ("{0}  {1}" -f (Get-Date -Format 's'), $Message) } catch { }
}

Write-Log "--- lancement demande, Mode=$Mode"

# Deux signaux, dont un seul suffit :
#  - un moniteur WMI apparait : SudoVDA a presente un EDID, donc un affichage
#    est reellement attache ;
#  - la VGA emulee perd son mode : c est ce que fait litteralement
#    dd_configuration_option = ensure_only_display, qui desactive tout autre
#    affichage au profit de l ecran virtuel.
$reason = 'delai depasse'
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    $monitors = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue)
    if ($monitors.Count -gt 0) { $reason = "moniteur attache ($($monitors.Count))"; break }
    $basic = Get-Adapters | Where-Object { $_.Name -like '*Basic Display*' }
    if ($basic -and -not $basic.VideoModeDescription) { $reason = 'VGA emulee desactivee'; break }
    Start-Sleep -Milliseconds 500
}
Write-Log "attente terminee : $reason"
foreach ($a in Get-Adapters) {
    Write-Log ("  adaptateur {0} | mode '{1}' | {2} Hz" -f $a.Name, $a.VideoModeDescription, $a.CurrentRefreshRate)
}

if (Get-Process -Name 'steam' -ErrorAction SilentlyContinue) {
    # Steam tourne deja (session precedente, ou reprise apres pause). Le
    # protocole steam:// est le seul moyen de lui faire ouvrir Big Picture sans
    # demarrer une seconde instance.
    if ($Mode -eq 'BigPicture') { Start-Process 'steam://open/bigpicture' }
    Write-Log 'Steam tournait deja'
    return
}
if (-not (Test-Path $SteamExe)) {
    Write-Log "AUCUN steam.exe a $SteamExe"
    return
}
if ($Mode -eq 'BigPicture') { Start-Process -FilePath $SteamExe -ArgumentList '-bigpicture' }
else { Start-Process -FilePath $SteamExe }
Write-Log "Steam demarre ($Mode)"
