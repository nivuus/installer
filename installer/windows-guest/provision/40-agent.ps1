<#
    Stage 40: the Guacamole agent.

    The agent is a payload artefact with no user state: it is redeployed on
    every rebuild by construction, so nothing about it goes to D:.

    It must run in session 1, never as a service. Window capture
    (Windows.Graphics.Capture) and input injection (SendInput) do not cross the
    session boundary - the same constraint that banned the SYSTEM-session
    completion script in sub-project A.

    The task carries NO password: an AtLogOn trigger with an Interactive logon
    type runs in the session of whoever is logged on, which permanent autologon
    guarantees is Administrator in session 1.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$TaskName = 'guacamole-agent'
$AgentDir = 'C:\nivuus\agent'

New-Item -ItemType Directory -Force -Path $AgentDir | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'drivers\agent\agent.exe') `
          -Destination (Join-Path $AgentDir 'agent.exe') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\run-agent.ps1') `
          -Destination (Join-Path $AgentDir 'run-agent.ps1') -Force

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File C:\nivuus\agent\run-agent.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'Administrator'
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null
Write-Host "scheduled task $TaskName registered"

# Prove session 1 now rather than trust the trigger: provisioning already runs
# inside the interactive session, so starting the task here exercises exactly
# the path the next logon will take.
$sessionFile = 'C:\nivuus\state\agent-session.txt'
Remove-Item -Path $sessionFile -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
$deadline = (Get-Date).AddSeconds(60)
while (-not (Test-Path $sessionFile) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if (-not (Test-Path $sessionFile)) {
    throw "the agent task never reported a session id; check $AgentDir\..\agent.log"
}
Write-Host "agent reported session $(Get-Content $sessionFile)"
