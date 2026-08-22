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
$LogFile = 'C:\nivuus\agent.log'

New-Item -ItemType Directory -Force -Path $AgentDir | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'drivers\agent\agent.exe') `
          -Destination (Join-Path $AgentDir 'agent.exe') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\run-agent.ps1') `
          -Destination (Join-Path $AgentDir 'run-agent.ps1') -Force

# Stop a still-running previous instance before touching the task
# definition. Unregister-ScheduledTask below removes the task object but does
# not touch an already-running action process, so without this a rebuild
# while the old agent is alive would end up with two concurrent instances
# instead of a replacement.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $stopDeadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $stopDeadline) {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $existing -or $existing.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 500
    }
}
# Nothing else legitimately holds this process name during provisioning.
Get-Process -Name 'agent' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $(Join-Path $AgentDir 'run-agent.ps1')"
)
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
$startedAt = Get-Date
Start-ScheduledTask -TaskName $TaskName
$deadline = $startedAt.AddSeconds(60)
while (-not (Test-Path $sessionFile) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if (-not (Test-Path $sessionFile)) {
    # agent.log is only created inside run-agent.ps1, after the session file,
    # so pointing at it unconditionally would be misleading in the case where
    # the task never even ran - mention it only when it actually exists.
    $logNote = if (Test-Path $LogFile) { "check $LogFile" } else { "$LogFile was never created" }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $info -or $info.LastRunTime -lt $startedAt) {
        throw "scheduled task $TaskName never left the queue; $logNote"
    }
    elseif ($info.State -eq 'Running') {
        throw "scheduled task $TaskName is running but produced no session id after 60s; $logNote"
    }
    else {
        throw "scheduled task $TaskName ran and exited (result $($info.LastTaskResult)) without writing a session id; $logNote"
    }
}
Write-Host "agent reported session $(Get-Content $sessionFile)"
