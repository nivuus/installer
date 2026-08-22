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
