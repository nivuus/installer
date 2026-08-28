<#
    Launcher for the Guacamole agent inside the interactive session.

    Writes its own session id as the LAST thing before starting the agent,
    after every step that can throw: that file is the appliance's replacement
    for check-session.sh, which cannot be used here - it requires the
    //192.168.3.2/c CIFS mount that the cutover removes, and it launches a
    C:\dev development build that does not exist on an appliance. If it
    exists, 40-agent.ps1 and 99-marker.ps1 both trust it as proof the agent
    was genuinely invoked - it must never be written unless that is true.
#>
$ErrorActionPreference = 'Continue'

$state = 'C:\nivuus\state'
New-Item -ItemType Directory -Force -Path $state | Out-Null

$env:SIGNALING_URL = 'ws://192.168.3.1:8080'
$env:LOCAL_IP      = '192.168.3.2'
$env:RUST_LOG      = 'info'

# UTF-8 for our own console output only - a decoding nicety, not a
# precondition for the agent to run. Wrapped in try/catch: the setter is a
# documented source of "the handle is invalid" under some Task
# Scheduler/hidden-window configurations, and it must never be able to abort
# the launch below.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {
    Write-Warning "could not set console output encoding: $_"
}

# Append, not overwrite: with automatic task restarts configured, truncating
# here would erase the log at exactly the moment something has just failed.
$log = New-Object System.IO.StreamWriter('C:\nivuus\agent.log', $true,
                                         (New-Object System.Text.UTF8Encoding($false)))
try {
    # Everything above this point can throw and abort safely before any
    # session proof exists. From here on the launch is genuinely about to
    # happen, so the session file existing now means the agent was actually
    # invoked, not merely attempted.
    $sid = (Get-Process -Id $PID).SessionId
    Set-Content -Path (Join-Path $state 'agent-session.txt') -Value "$sid" `
                -Encoding ASCII -NoNewline

    & 'C:\nivuus\agent\agent.exe' *>&1 |
        ForEach-Object { $log.WriteLine([string]$_); $log.Flush() }
}
finally { $log.Close() }
