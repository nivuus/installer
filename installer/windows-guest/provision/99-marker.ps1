<#
    Stage 99: close the provisioning.

    Order matters: everything else must be true before port 5985 opens,
    because the host treats a reachable 5985 as "the guest is provisioned".
    This is also where 10-nvidia.ps1's device check lands when that stage had
    to defer it to survive a driver-install reboot.

    ⚠️ Unlike sub-project A, this stage does NOT disable the automatic logon.
    The appliance holds a session open permanently: Apollo captures an
    interactive desktop and the agent must live in session 1. With the dummy
    plug removed, that desktop is reachable only through Apollo (paired
    client), the agent (authenticated platform) or the VNC console, which
    listens on 127.0.0.1 and is therefore root-on-the-host only.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
$AgentTaskName = 'guacamole-agent'

# Keep the probe on C: so it can be run again without the payload medium.
# Destination is the parent, not 'C:\nivuus\probe': Copy-Item nests into
# ...\probe\probe when the destination directory already exists.
Copy-Item -Path (Join-Path $PayloadRoot 'probe') -Destination 'C:\nivuus' `
          -Recurse -Force

$gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
if (-not $gpu) { throw 'no NVIDIA display device at end of provisioning' }
if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }

# Match on the vendor-declared hardware ID, same as 25-apollo.ps1, not a
# loose instance-ID wildcard: this is the LAST check before the appliance is
# declared ready, so it must be the precise one - a wildcard would happily
# accept some other root-enumerated display device.
$vda = Get-PnpDevice -Class Display -PresentOnly -ErrorAction SilentlyContinue |
       Where-Object {
           $hwids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
                                           -KeyName 'DEVPKEY_Device_HardwareIds' `
                                           -ErrorAction SilentlyContinue).Data
           $hwids -contains 'Root\SudoMaker\SudoVDA'
       } | Select-Object -First 1
if (-not $vda -or $vda.Status -ne 'OK') {
    throw 'no working SudoVDA device (hardware ID Root\SudoMaker\SudoVDA) at end of provisioning'
}

$svc = Get-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
if (-not $svc -or $svc.Status -ne 'Running') {
    throw "ApolloService is $(if ($svc) { $svc.Status } else { 'absent' })"
}

if (-not (Test-Path 'D:\Steam\steam.exe')) { throw 'no steam.exe on D:' }
if (-not (Test-Path 'D:\state\NIVUUS-DATA.id')) { throw 'D: carries no Nivuus marker' }

# La preuve que l'agent vit dans la session INTERACTIVE. check-session.sh ne
# peut pas servir sur une appliance : il exige le montage CIFS que la bascule
# supprime et un binaire de developpement sous C:\dev.
#
# NE PAS comparer a 1 en dur. Windows numerote les sessions en incrementant, et
# rien ne garantit que la session console porte l'ID 1 : apres la
# reconstruction du 2026-08-25 elle valait 2, et ce controle a refuse une
# appliance parfaitement saine. Ce qui compte n'est pas la valeur de l'ID, c'est
# que ce soit la MEME session que celle ou tourne le bureau.
#
# Cet etage est lance par la cle Run a l'ouverture de session, donc il s'execute
# LUI-MEME dans la session console : son propre SessionId est la reference, sans
# appel d'API ni Add-Type (qui couterait trois minutes de compilation C#).
$sessionFile = Join-Path $StateDir 'agent-session.txt'
if (-not (Test-Path $sessionFile)) { throw 'the agent never reported a session id' }
$sid = (Get-Content $sessionFile -Raw).Trim()
$consoleSid = "$((Get-Process -Id $PID).SessionId)"
if ($sid -ne $consoleSid) {
    throw "the agent runs in session '$sid', not the interactive session '$consoleSid': window capture and input injection would both fail"
}

# The session file only proves the agent ran once, at stage-40 time. What
# must be durable is that it comes back at every future logon - so check the
# scheduled task is registered and enabled, not that agent.exe is alive right
# now. A live-process check would be flaky: the agent legitimately exits when
# it cannot reach its signalling server (not part of this appliance), and a
# perfectly healthy guest would fail that check by coincidence of timing.
$agentTask = Get-ScheduledTask -TaskName $AgentTaskName -ErrorAction SilentlyContinue
if (-not $agentTask) { throw "scheduled task $AgentTaskName is not registered" }
if ($agentTask.State -eq 'Disabled') { throw "scheduled task $AgentTaskName is disabled" }

# --- Rebuild invariants. Everything above proves the guest works RIGHT NOW;
# these prove it will still be Apollo's paired guest after the next C:
# rebuild - the entire reason D: and the junction exist. A junction that
# quietly reverted to a plain directory (antivirus, a backup tool, a future
# Apollo update recreating it) would still pass every check above, certify
# this guest as ready, and only show up as lost pairings at the next rebuild.
$apolloRootFile = Join-Path $StateDir 'apollo.root'
if (-not (Test-Path $apolloRootFile)) { throw 'apollo.root is missing: cannot verify the Apollo config junction' }
$apolloRoot = (Get-Content $apolloRootFile -Raw).Trim()
$apolloConfig = Join-Path $apolloRoot 'config'
$configItem = Get-Item -Path $apolloConfig -ErrorAction SilentlyContinue
if (-not $configItem -or -not ($configItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "$apolloConfig is not a junction: Apollo would write its pairings onto C:, lost on the next rebuild"
}
# Normalize like 25-apollo.ps1: on PowerShell 5.1, .Target can come back as
# the raw NT substitute name (\??\D:\state\apollo) instead of the plain path.
$junctionTarget = ($configItem.Target | Select-Object -First 1)
if ($junctionTarget) { $junctionTarget = ($junctionTarget -replace '^\\\?\?\\', '').TrimEnd('\') }
$expectedApolloState = 'D:\state\apollo'
if ($junctionTarget -ne $expectedApolloState) {
    throw "config junction points at '$junctionTarget', not '$expectedApolloState': pairings would not survive a rebuild"
}
if (-not (Test-Path (Join-Path $expectedApolloState 'sunshine.conf'))) {
    throw "$expectedApolloState\sunshine.conf is missing"
}
if (-not (Test-Path (Join-Path $expectedApolloState 'apps.json'))) {
    throw "$expectedApolloState\apps.json is missing"
}

# Hibernation is the host's entire energy strategy for this guest
# (vm-idle-shutdown.timer hibernates it after 10 minutes of inactivity).
# 50-power.ps1 only warns when hiberfil.sys is absent, because at that point
# there could still be a legitimate reason it has not appeared yet; by this
# closing stage there is none left, so make it fatal here instead.
# NE PAS employer Test-Path ici : hiberfil.sys porte les attributs Hidden +
# System, et le fournisseur FileSystem de PowerShell les filtre — Test-Path
# rend $false sur un fichier de 6,8 Go parfaitement present, et Test-Path n'a
# pas de parametre -Force pour passer outre. Mesure sur l'invite le 2026-08-25 :
# Test-Path = False alors que [System.IO.File]::GetAttributes rend
# « Hidden, System, Archive, NotContentIndexed ». Le piege est d'autant plus
# vicieux que le `if exist` de cmd, lui, voit le fichier — la recette manuelle
# passait donc au vert pendant que ce controle-ci echouait.
if (-not [System.IO.File]::Exists('C:\hiberfil.sys')) {
    throw 'hiberfil.sys is absent: hibernation is unavailable, and the host would silently never be able to sleep this guest'
}

Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                    -Name 'NivuusProvision' -ErrorAction SilentlyContinue

$marker = @(
    "provision_version=B2",
    "completed=$(Get-Date -Format o)",
    "computer=$env:COMPUTERNAME",
    "agent_session=$sid"
)
Set-Content -Path (Join-Path $StateDir 'PROVISION.done') -Value $marker -Encoding ASCII

# La regle est ouverte depuis 00-bootstrap.ps1 et le reste : ce n est plus ici
# que 5985 s ouvre. On se contente de verifier qu elle l est toujours - une
# strategie de groupe ou un durcissement applique en cours de provisionnement
# aurait pu la refermer, et l appliance serait alors injoignable sans que rien
# ne le dise.
$shut = @(Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' |
          Where-Object { -not $_.Enabled -or $_.Enabled -eq 'False' })
if ($shut) { $shut | Enable-NetFirewallRule }
Write-Host 'provisioning marker written, WinRM reachable'
