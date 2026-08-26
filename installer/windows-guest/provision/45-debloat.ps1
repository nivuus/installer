<#
    Stage 45: strip what an appliance does not need.

    IoT Enterprise LTSC is already the leanest SKU - no Store, no Cortana, no
    Xbox app, no OneDrive - so there is nothing to uninstall. What is left is
    running services and settings that cost latency, I/O or screen space on a
    machine whose only job is to stream a game.

    Two deliberate NON-removals, so nobody "finishes the job" later:
    Defender stays. C: is disposable, but D:, the Steam session and above all
    the four virtiofs shares into the host are not - a streaming-exposed guest
    mounting host folders read-write is the wrong place to trade protection for
    a few percent of CPU. Edge stays: the gain is marginal and WebView2 is a
    dependency of third-party components.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# GameDVR is a SECOND screen capture running alongside Apollo's. It takes GPU
# time and can contend with the encoder - on a machine that exists to stream,
# this is the first thing to switch off. HKCU alone is not enough: the policy
# key under HKLM is what survives a user profile being recreated.
New-Item -Path 'HKCU:\System\GameConfigStore' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\System\GameConfigStore' -Name 'GameDVR_Enabled' -Value 0 -Type DWord
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' `
                 -Name 'AllowGameDVR' -Value 0 -Type DWord
$dvr = (Get-ItemProperty 'HKCU:\System\GameConfigStore').GameDVR_Enabled
if ($dvr -ne 0) { throw "GameDVR is still enabled (got '$dvr')" }
Write-Host 'GameDVR disabled (it was a second capture competing with Apollo)'

# WSearch is the one that would hurt most now: it indexes the virtiofs shares
# added in stage 35 - Games and Downloads, potentially terabytes - across a
# network filesystem. The others are simply useless here.
$useless = @{
    'DiagTrack'        = 'telemetry'
    'SysMain'          = 'Superfetch: prefetching is pointless on a single-purpose box'
    'WSearch'          = 'would index the virtiofs shares over a network filesystem'
    'Spooler'          = 'no printer, and a documented attack surface'
    'PcaSvc'           = 'application compatibility telemetry'
    'lfsvc'            = 'geolocation'
    'MapsBroker'       = 'maps'
    'dmwappushservice' = 'WAP push message routing'
    'RetailDemo'       = 'store demo mode'
}
foreach ($name in $useless.Keys) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "$name absent, nothing to disable"; continue }
    Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
    Set-Service -Name $name -StartupType Disabled -ErrorAction Stop
    # Read back: Set-Service reports no error when a policy re-enables the
    # service behind it.
    $now = (Get-Service -Name $name).StartType
    if ($now -ne 'Disabled') { throw "$name is still '$now' after being disabled" }
    Write-Host "$name disabled ($($useless[$name]))"
}

# Toast notifications land IN THE MIDDLE of the streamed picture, where nobody
# can dismiss them without a mouse on the guest.
$push = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\PushNotifications'
New-Item -Path $push -Force | Out-Null
Set-ItemProperty -Path $push -Name 'ToastEnabled' -Value 0 -Type DWord
Write-Host 'toast notifications disabled (they would appear inside the stream)'

# Animations cost frames on every window transition, and every frame is encoded
# and shipped over the network. 2 = "adjust for best performance".
Set-ItemProperty -Path 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' `
                 -Name 'VisualFXSetting' -Value 2 -Type DWord -ErrorAction SilentlyContinue
Write-Host 'visual effects set to performance'
