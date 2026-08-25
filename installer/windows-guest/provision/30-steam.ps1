<#
    Stage 30: Steam, installed ON D: rather than configured to install there.

    Pre-seeding libraryfolders.vdf does not hold: Steam rewrites it and the
    default folder drifts back to C:. /D=D:\Steam makes D:\Steam\steamapps the
    default library BY CONSTRUCTION, and nothing can fall back.

    The consequence reaches past the games: wiping C: leaves the whole Steam
    install intact, config\loginusers.vdf included, so the session token
    survives. Re-running the installer on the new C: only recreates registry
    entries and shortcuts - no library to re-add, no login.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$SteamDir = 'D:\Steam'

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

# Steam IS the shell: no explorer.exe, therefore no taskbar, no wallpaper and
# no desktop icons - not hidden, absent. This appliance has no physical screen
# and no use for a Windows desktop.
#
# AutoRestartShell is Winlogon's own safety net (it relaunches the shell when
# it exits); it defaults to 1 but is set explicitly here, because a session
# with no live shell shows a streaming client a black screen and there is no
# monitor to notice it on. The launcher loops on top of that, since Steam
# closing is not the shell closing.
#
# The agent is unaffected: 40-agent.ps1 runs it from an AtLogOn scheduled task,
# which is independent of the shell.
$shellScript = 'C:\nivuus\apollo\steam-shell.ps1'
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\steam-shell.ps1') `
          -Destination $shellScript -Force
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'Shell' `
    -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $shellScript"
Set-ItemProperty -Path $winlogon -Name 'AutoRestartShell' -Value 1 -Type DWord

# Read back: writing a registry value proves the write, never that Windows
# will honour it - but a value that did not land cannot be honoured either.
$shellNow = (Get-ItemProperty -Path $winlogon -Name 'Shell').Shell
if ($shellNow -notlike "*steam-shell.ps1*") {
    throw "Winlogon Shell did not take the appliance launcher, got '$shellNow'"
}
Write-Host 'Steam is the session shell (no explorer, no taskbar, no desktop)'

# C: DISPARAIT DE L'INTERFACE. Le kiosque a deja supprime explorer.exe, donc il
# n'y a plus de bureau ni d'explorateur pour y naviguer ; il reste les boites de
# dialogue de fichiers que Steam ouvre lui-meme (ajouter une bibliotheque, un
# jeu non-Steam). Or C: est REGENEREE a chaque reconstruction : tout ce qu'on y
# depose est perdu, et une bibliotheque Steam qui y atterrit par megarde est
# exactement le defaut que la separation C:/D: existe pour empecher.
#
# NoDrives masque le lecteur dans l'explorateur et les boites de dialogue ;
# NoViewOnDrive en refuse l'ouverture meme quand le chemin est saisi a la main.
# Le masque est un champ de bits, une lettre par bit depuis A : C: vaut 4.
#
# Ce sont des restrictions d'INTERFACE, pas de securite. Le compte de
# l'appliance est administrateur et peut les lever ; Windows, Apollo, Steam et
# l'agent continuent d'acceder normalement a C: par les API de fichiers, sans
# quoi rien ne fonctionnerait. On empeche l'erreur, pas l'adversaire.
$policies = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer'
New-Item -Path $policies -Force | Out-Null
$driveC = 4
Set-ItemProperty -Path $policies -Name 'NoDrives' -Value $driveC -Type DWord
Set-ItemProperty -Path $policies -Name 'NoViewOnDrive' -Value $driveC -Type DWord
$readback = Get-ItemProperty -Path $policies
if ($readback.NoDrives -ne $driveC -or $readback.NoViewOnDrive -ne $driveC) {
    throw "hiding C: did not take: NoDrives=$($readback.NoDrives) NoViewOnDrive=$($readback.NoViewOnDrive)"
}
Write-Host 'C: hidden from the file dialogs (interface restriction, not a security boundary)'
