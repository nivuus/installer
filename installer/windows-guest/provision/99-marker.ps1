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

# The session-1 proof. check-session.sh cannot be used on an appliance: it
# needs the CIFS mount the cutover removes and a C:\dev development build.
$sessionFile = Join-Path $StateDir 'agent-session.txt'
if (-not (Test-Path $sessionFile)) { throw 'the agent never reported a session id' }
$sid = (Get-Content $sessionFile -Raw).Trim()
if ($sid -ne '1') {
    throw "the agent runs in session '$sid', not 1: window capture and input injection would both fail"
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

Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                    -Name 'NivuusProvision' -ErrorAction SilentlyContinue

$marker = @(
    "provision_version=B1",
    "completed=$(Get-Date -Format o)",
    "computer=$env:COMPUTERNAME",
    "agent_session=$sid"
)
Set-Content -Path (Join-Path $StateDir 'PROVISION.done') -Value $marker -Encoding ASCII

Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Enable-NetFirewallRule
Write-Host 'provisioning marker written, WinRM reachable'
