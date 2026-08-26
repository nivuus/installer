<#
    Stage 25: Apollo, its virtual display, and the configuration that survives
    a rebuild.

    Order is load-bearing. The installer must run before the junction, because
    it creates the config directory; the junction must exist before the
    service starts, or Apollo writes its pairings onto C: where the next
    rebuild would erase them.

    /D= is deliberately NOT passed: NSIS wants it unquoted and last, and the
    default path contains a space that PowerShell would quote. The install
    location is read back from the registry instead of assumed.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
$ApolloState = 'D:\state\apollo'

$installer = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\apollo') `
                           -Filter '*.exe' | Sort-Object Name | Select-Object -First 1
if (-not $installer) { throw "no Apollo installer in $PayloadRoot\drivers\apollo" }

Write-Host "installing $($installer.Name)"
$proc = Start-Process -FilePath $installer.FullName -ArgumentList '/S' -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "Apollo installer exited $($proc.ExitCode)" }

# The uninstall registry key name could not be verified offline (the
# installer's strings are compressed): try it, but do not assume it is
# right. Pick whichever candidate actually contains sunshine.exe, so a stale
# or wrong registry value does not fail a perfectly healthy default install.
$regRoot = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Apollo' `
                             -ErrorAction SilentlyContinue).InstallLocation
$candidates = @($regRoot, 'C:\Program Files\Apollo') | Where-Object { $_ }
$root = $candidates | Where-Object { Test-Path (Join-Path $_ 'sunshine.exe') } | Select-Object -First 1
if (-not $root) {
    throw "Apollo not found under any candidate location: $($candidates -join ', ')"
}
Write-Host "Apollo installed at $root"

# The bundled SudoVDA package. install.bat seeds its own certificate into Root
# and TrustedPublisher, then removes and recreates the device node, so running
# it is idempotent.
$vdaDir = Join-Path $root 'drivers\sudovda'
if (Test-Path (Join-Path $vdaDir 'install.bat')) {
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install.bat' `
                       -WorkingDirectory $vdaDir -Wait -PassThru
    Write-Host "SudoVDA install.bat exited $($p.ExitCode)"
}
# Match on the vendor-declared hardware ID, not a ROOT\DISPLAY\* instance-ID
# wildcard: an instance ID only encodes the INF's enumerator string (e.g.
# ROOT\DISPLAY\0003) and would also match an unrelated root-enumerated
# display device. From SudoVDA.inf: %DeviceName%=SudoVDA_Install,
# Root\SudoMaker\SudoVDA - keep this exact, do not loosen it back to a wildcard.
$vda = Get-PnpDevice -Class Display -PresentOnly -ErrorAction SilentlyContinue |
       Where-Object {
           $hwids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
                                           -KeyName 'DEVPKEY_Device_HardwareIds' `
                                           -ErrorAction SilentlyContinue).Data
           $hwids -contains 'Root\SudoMaker\SudoVDA'
       } | Select-Object -First 1
if (-not $vda -or $vda.Status -ne 'OK') {
    throw 'no working SudoVDA device (hardware ID Root\SudoMaker\SudoVDA): SudoVDA did not install'
}
Write-Host "SudoVDA OK: $($vda.InstanceId)"

$config = Join-Path $root 'config'

# --- The junction. See provision\assets\apollo-junction.ps1 for the
# maneuver itself (split out to keep this file under 200 lines).
. (Join-Path $PayloadRoot 'provision\assets\apollo-junction.ps1')
Set-ApolloConfigJunction -Config $config -ApolloState $ApolloState
Write-Host "config junctioned to $ApolloState"

# --- Generated files: always rewritten. The repo stays authoritative on
# purpose - pinning to first-install would strand the appliance on stale
# config forever - but sunshine.conf and apps.json are ALSO exactly the two
# files Apollo's own web UI persists to (its Configuration and Applications
# pages). Anything the owner added or changed there is therefore lost on
# every rebuild, silently, unless we say so here: back up the existing file
# whenever it differs from what we are about to write, and log loudly.
function Backup-IfChanged {
    param([Parameter(Mandatory = $true)][string]$Source,
          [Parameter(Mandatory = $true)][string]$Destination)
    # $stamp distingue « Apollo vient d ecrire ses defauts » de « le
    # proprietaire a modifie sa config ». Sans lui l avertissement se declenche
    # a la PREMIERE installation, ou personne n a rien pu modifier : le service
    # Apollo demarre avant cette copie et depose ses propres sunshine.conf et
    # apps.json dans le repertoire jonctionne. Un avertissement qui crie a
    # chaque installation neuve apprend a l operateur a l ignorer — et il
    # comptera, lui, a la reconstruction.
    $stamp = Join-Path (Split-Path -Parent $Destination) '.nivuus-config-written'
    if ((Test-Path $stamp) -and (Test-Path $Destination) -and
        (Get-Content -Raw -Path $Destination) -ne (Get-Content -Raw -Path $Source)) {
        $backup = "$Destination.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -Path $Destination -Destination $backup -Force
        Write-Host "WARNING: $Destination had owner changes (Apollo's web UI writes to this exact file) - backed up to $backup before overwriting it with the repo version"
    }
    Copy-Item -Path $Source -Destination $Destination -Force
    # Le media de reponses est un CD-ROM : Copy-Item en reporte l attribut
    # ReadOnly sur la copie. Or ces deux fichiers sont exactement ceux que l IHM
    # web d Apollo reecrit — ReadOnly les gele, et toute sauvegarde depuis l IHM
    # echoue. Mesure sur l invite le 2026-08-25 : un WriteAllBytes sur
    # apps.json levait UnauthorizedAccessException.
    Set-ItemProperty -Path $Destination -Name Attributes -Value 'Archive'
}
Backup-IfChanged -Source (Join-Path $PayloadRoot 'config\sunshine.conf') `
                  -Destination (Join-Path $ApolloState 'sunshine.conf')
Backup-IfChanged -Source (Join-Path $PayloadRoot 'config\apps.json') `
                  -Destination (Join-Path $ApolloState 'apps.json')
Set-Content -Path (Join-Path $ApolloState '.nivuus-config-written') `
            -Value (Get-Date -Format o) -Encoding ASCII

# adapter_name : SANS lui, l'appliance streame en x264 LOGICIEL.
#
# Apollo capture par Desktop Duplication, et l'encodeur doit vivre sur
# l'adaptateur qui possede la sortie capturee. Quand AUCUN ecran physique n'est
# branche sur le GPU — le cas de cette appliance, dont le bouchon HDMI est
# retire — Apollo retient l'adaptateur par defaut, qui est le « Microsoft Basic
# Render Driver » (WARP, vendor 0x1414). NVENC est alors essaye SUR WARP,
# echoue, et la sonde retombe sur libx264. Le flux se negocie en 1280x800 a
# 1 Hz et tout client abandonne avant d'emettre le moindre paquet UDP :
# CLIENT CONNECTED puis DISCONNECTED 150 ms plus tard, « Initial Ping Timeout »
# cote hote et « error -5 » cote Moonlight. Mesure sur l'invite le 2026-08-25 ;
# epingler l'adaptateur a fait apparaitre h264_nvenc, hevc_nvenc ET av1_nvenc.
#
# Le nom est DETECTE ici plutot qu'ecrit dans le gabarit : l'hote de
# construction ne connait que les identifiants PCI du GPU passe, pas son nom
# commercial DXGI, et celui-ci change avec la carte.
$gpuName = (Get-CimInstance Win32_VideoController |
            Where-Object { $_.Name -like 'NVIDIA*' } |
            Select-Object -First 1).Name
if (-not $gpuName) { throw 'no NVIDIA video controller: cannot pin Apollo capture adapter' }
$confPath = Join-Path $ApolloState 'sunshine.conf'
$conf = @(Get-Content -Path $confPath | Where-Object { $_ -notmatch '^\s*adapter_name\s*=' })
$conf += "adapter_name = $gpuName"
Set-Content -Path $confPath -Value $conf
Write-Host "capture adapter pinned to $gpuName"
New-Item -ItemType Directory -Force -Path 'C:\nivuus\apollo' | Out-Null
# Les deux moities de chaque application d'apps.json ; elles remplacent le
# prep-cmd maximize-steam.ps1. Le pourquoi est dans leurs propres en-tetes.
'steam-session.ps1', 'steam-launch.ps1' | ForEach-Object {
    Copy-Item (Join-Path $PayloadRoot "provision\assets\$_") "C:\nivuus\apollo\$_" -Force }
Remove-Item 'C:\nivuus\apollo\maximize-steam.ps1' -Force -EA SilentlyContinue

# --- Web-manager credentials: seeded only once, so a rebuild keeps whatever
# the owner set. The presence check is our own marker, not an Apollo-internal
# filename (e.g. sunshine_state.json): nothing offline proves the web-manager
# credentials live in that particular file rather than beside it, and an
# Apollo upgrade could rename or restructure it without us noticing. This
# marker's meaning is exactly what we need it to mean, and only we write it.
$secrets = Import-PowerShellDataFile -Path (Join-Path $PayloadRoot 'config\secrets.psd1')
$credsMarker = Join-Path $ApolloState '.nivuus-creds-seeded'
if (-not (Test-Path $credsMarker)) {
    # The values stay in variables: Start-Transcript records the source line,
    # not the expansion, so the password never lands in provision.log.
    $p = Start-Process -FilePath (Join-Path $root 'sunshine.exe') `
                       -ArgumentList '--creds', $secrets.ApolloUser, $secrets.ApolloPassword `
                       -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -ne 0) { throw "sunshine.exe --creds exited $($p.ExitCode)" }
    # Records only the date, never the credentials.
    Set-Content -Path $credsMarker -Value (Get-Date -Format o) -Encoding ASCII
    Write-Host 'Apollo web credentials seeded'
}
else {
    Write-Host 'Apollo state exists: web credentials left untouched'
}

# --- Service and firewall. Apollo ships the scripts; use them rather than
# reimplement sc/netsh incantations that would drift from the vendor's.
$scripts = Join-Path $root 'scripts'
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'autostart-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
# Its exit code is a thin cmd/sc wrapper, not reliably meaningful; check the
# effect. Without this, a transient "sc config" failure leaves Apollo running
# today (the Status check below still passes) but dead after the next reboot,
# with nothing reported - and this appliance hibernates/wakes constantly.
$startType = (Get-Service -Name 'ApolloService').StartType
if ($startType -ne 'Automatic') {
    throw "ApolloService StartType is $startType, expected Automatic: it would not survive a reboot"
}

# Delete first: netsh happily creates a duplicate rule on every rebuild.
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'delete-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'add-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
# Same reasoning as StartType above: a netsh failure here would leave the
# guest silently unreachable by Moonlight, the machine's entire purpose.
if (-not (Get-NetFirewallRule -DisplayName 'Apollo' -ErrorAction SilentlyContinue)) {
    throw "no firewall rule named 'Apollo' after add-firewall-rule.bat"
}

Start-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
$svc = Get-Service -Name 'ApolloService'
if ($svc.Status -ne 'Running') { throw "ApolloService is $($svc.Status), expected Running" }
Set-Content -Path (Join-Path $StateDir 'apollo.root') -Value $root -Encoding ASCII
Write-Host 'Apollo running'
