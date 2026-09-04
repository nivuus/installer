<#
    Stage 30: Steam, installed ON D: rather than configured to install there.

    Pre-seeding libraryfolders.vdf does not hold: Steam rewrites it and the
    default folder drifts back to C:. /D=D:\Steam makes D:\Steam\steamapps the
    default library BY CONSTRUCTION, and nothing can fall back.

    The consequence reaches past the games: wiping C: leaves the whole Steam
    install intact, config\loginusers.vdf included, so the session token
    survives. Re-running the installer on the new C: only recreates registry
    entries and shortcuts - no library to re-add, no login.

    EXPLORER.EXE EST LE SHELL, et ce n est pas un retour en arriere cosmetique
    (2026-08-30). Le kiosque precedent - un PowerShell a la place du shell -
    empechait TOUTE activation d application UWP : Parametres, Securite Windows,
    Store et Xbox rendaient tous « Class not registered ». Or le formulaire de
    connexion a un compte Microsoft EST une application UWP
    (Microsoft.AAD.BrokerPlugin) : l ouverture de session Xbox Live echouait
    donc en 0x80040154, et tout jeu GDK - Forza Horizon 6 en tete, achete sur
    Steam - restait fige sur son ecran de demarrage, ecran noir et SANS message.
    Aucun paquet manquant n expliquait ce silence : c etait le shell.

    Le bureau qui revient avec Explorer est MASQUE, pas subi : desktop-chrome.ps1
    (tache a l ouverture de session) pose le fond Nivuus, cache les icones et
    met la barre des taches en masquage automatique.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$SteamDir = 'D:\Steam'
$NivuusDir = 'C:\nivuus\apollo'

$setup = Join-Path $PayloadRoot 'drivers\steam\SteamSetup.exe'
if (-not (Test-Path $setup)) { throw "missing $setup" }

$fresh = -not (Test-Path (Join-Path $SteamDir 'steam.exe'))
# NSIS: /D= must be the last argument and must not be quoted. D:\Steam has no
# space, so PowerShell passes it through untouched.
$proc = Start-Process -FilePath $setup -ArgumentList '/S', "/D=$SteamDir" `
                      -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "SteamSetup exited $($proc.ExitCode)" }

if (-not (Test-Path (Join-Path $SteamDir 'steam.exe'))) {
    throw "no steam.exe under $SteamDir after installing"
}
if ($fresh) { Write-Host "Steam installed into $SteamDir" }
else { Write-Host "Steam re-registered against the existing $SteamDir" }

$login = Join-Path $SteamDir 'config\loginusers.vdf'
if (Test-Path $login) { Write-Host 'existing Steam session preserved' }

# --- STEAM NE DOIT PAS DEMARRER TOUT SEUL -----------------------------------
# SteamSetup pose HKCU\...\Run\Steam. Sous le kiosque cette cle n etait JAMAIS
# executee - seul Explorer traite les entrees Run - et elle dormait, inoffensive.
# Avec Explorer comme shell elle se reveille, et elle casse la session Moonlight :
# Apollo doit lancer Steam lui-meme (entree « detached » d apps.json) APRES que
# SudoVDA a cree l ecran virtuel, et son steam-session.ps1 - dont la sortie
# ferme la session - sort aussitot en trouvant Steam deja la. Mesure du
# 2026-08-30 : « La connexion a ete interrompue » a chaque tentative, tant que
# cette cle etait presente.
#
# C est aussi la raison de la separation du 2026-08-26 : un Steam lance avant
# l ecran virtuel choisit SwiftShader, son rasteriseur LOGICIEL, pour toute la
# duree du processus.
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Remove-ItemProperty -Path $runKey -Name 'Steam' -ErrorAction SilentlyContinue
if ((Get-ItemProperty -Path $runKey -Name 'Steam' -ErrorAction SilentlyContinue)) {
    throw "HKCU Run\Steam est toujours la : Explorer relancerait Steam en doublon et Apollo fermerait la session des son ouverture"
}
Write-Host 'Steam autostart removed (Apollo launches Steam after the virtual display exists)'

# --- LE SHELL ---------------------------------------------------------------
# AutoRestartShell est le filet de Winlogon (il relance le shell quand il sort) ;
# il vaut 1 par defaut mais est pose explicitement, parce qu une session sans
# shell vivant montre un ecran noir a un client de streaming et qu il n y a pas
# de moniteur pour s en apercevoir.
$wallpaperSrc = Join-Path $PayloadRoot 'assets\wallpaper.png'
if (Test-Path $wallpaperSrc) {
    Copy-Item -Path $wallpaperSrc -Destination 'C:\nivuus\wallpaper.png' -Force
    Write-Host 'wallpaper installed'
}
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'Shell' -Value 'explorer.exe'
Set-ItemProperty -Path $winlogon -Name 'AutoRestartShell' -Value 1 -Type DWord

# Read back: writing a registry value proves the write, never that Windows
# will honour it - but a value that did not land cannot be honoured either.
$shellNow = (Get-ItemProperty -Path $winlogon -Name 'Shell').Shell
if ($shellNow -ne 'explorer.exe') {
    throw "Winlogon Shell n a pas pris explorer.exe, got '$shellNow' : sans lui aucune application UWP ne s active et l ouverture de session Xbox Live echoue en 0x80040154"
}
Write-Host 'explorer.exe is the session shell (UWP activation works, Xbox sign-in possible)'

# --- LES DEUX TACHES DE SESSION ---------------------------------------------
# Interactives et sans mot de passe, comme l agent (40) et Services de jeu (34) :
# l ouverture de session automatique permanente garantit que l utilisateur
# present est Administrator. Elles sont INDEPENDANTES du shell, donc insensibles
# a ce qu il devient - c est tout l interet par rapport a l ancien kiosque.
New-Item -ItemType Directory -Force -Path $NivuusDir | Out-Null
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' `
    -LogonType Interactive -RunLevel Highest

$sessionTasks = @(
    @{ Name = 'desktop-chrome'; Asset = 'desktop-chrome.ps1'
       # Sans limite de duree ce script ne rendrait jamais la main a une console
       # qui reste allumee : il applique et il sort.
       Limit = (New-TimeSpan -Minutes 5)
       Why = 'fond Nivuus, icones cachees, barre des taches en masquage automatique' },
    @{ Name = 'steam-hold-notice'; Asset = 'steam-hold-notice.ps1'
       # Celui-ci BOUCLE, volontairement : il surveille le sentinel toute la
       # session. Zero = pas de limite, sinon le planificateur le tuerait.
       Limit = ([TimeSpan]::Zero)
       Why = 'avertit pendant une synchronisation de bibliotheque, sinon un ecran fige se lit comme une panne' }
)

foreach ($t in $sessionTasks) {
    $dest = Join-Path $NivuusDir $t.Asset
    Copy-Item -Path (Join-Path $PayloadRoot "provision\assets\$($t.Asset)") `
              -Destination $dest -Force
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User 'Administrator'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
        "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $dest"
    )
    # UN jeu de reglages PAR tache, et la duree maximale passe par le
    # PARAMETRE du cmdlet - jamais par la propriete. MESURE le 2026-09-04 sur
    # l invite : ExecutionTimeLimit est une String qui porte une duree
    # ISO-8601 (« PT72H » par defaut), et c est le parametre qui convertit un
    # TimeSpan dans cette forme. Une affectation y deposait « 00:05:00 », que
    # Register-ScheduledTask refuse : « The task XML contains a value which is
    # incorrectly formatted or out of range. (37,36):ExecutionTimeLimit ». Sous
    # $ErrorActionPreference = 'Stop', cette etape mourait donc la, et le
    # provisionnement entier avec elle. 40-agent.ps1 ecrivait deja la forme
    # juste ; c est la meme ici.
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit $t.Limit
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $s | Out-Null
    # Relire plutot que croire Register-ScheduledTask : une strategie de groupe
    # peut refuser un declencheur sans que l enregistrement echoue.
    if (-not (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue)) {
        throw "la tache $($t.Name) n a pas ete enregistree ($($t.Why))"
    }
    Write-Host "tache $($t.Name) posee : $($t.Why)"
}

# C: DISPARAIT DE L INTERFACE, et c est DEPUIS LE 2026-08-30 PLUS necessaire,
# pas moins : le kiosque avait supprime explorer.exe, il n y avait donc ni
# bureau ni explorateur de fichiers pour y naviguer. Explorer etant revenu, les
# deux existent - en plus des boites de dialogue que Steam ouvre lui-meme
# (ajouter une bibliotheque, un jeu non-Steam). Or C: est REGENEREE a chaque
# reconstruction : tout ce qu on y depose est perdu, et une bibliotheque Steam
# qui y atterrit par megarde est exactement le defaut que la separation C:/D:
# existe pour empecher.
#
# NoDrives masque le lecteur dans l explorateur et les boites de dialogue ;
# NoViewOnDrive en refuse l ouverture meme quand le chemin est saisi a la main.
# Le masque est un champ de bits, une lettre par bit depuis A : C: vaut 4.
#
# Ce sont des restrictions d INTERFACE, pas de securite. Le compte de
# l appliance est administrateur et peut les lever ; Windows, Apollo, Steam et
# l agent continuent d acceder normalement a C: par les API de fichiers, sans
# quoi rien ne fonctionnerait. On empeche l erreur, pas l adversaire.
$policies = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer'
if (-not (Test-Path $policies)) { New-Item -Path $policies -Force | Out-Null }
$driveC = 4
Set-ItemProperty -Path $policies -Name 'NoDrives' -Value $driveC -Type DWord
Set-ItemProperty -Path $policies -Name 'NoViewOnDrive' -Value $driveC -Type DWord
$readback = Get-ItemProperty -Path $policies
if ($readback.NoDrives -ne $driveC -or $readback.NoViewOnDrive -ne $driveC) {
    throw "hiding C: did not take: NoDrives=$($readback.NoDrives) NoViewOnDrive=$($readback.NoViewOnDrive)"
}
Write-Host 'C: hidden from the file dialogs (interface restriction, not a security boundary)'
