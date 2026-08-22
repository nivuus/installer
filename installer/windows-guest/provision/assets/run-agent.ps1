<#
    Launcher for the Guacamole agent inside the interactive session.

    Writes its own session id BEFORE starting the agent: that file is the
    appliance's replacement for check-session.sh, which cannot be used here -
    it requires the //192.168.3.2/c CIFS mount that the cutover removes, and
    it launches a C:\dev development build that does not exist on an
    appliance.
#>
$ErrorActionPreference = 'Continue'

$state = 'C:\nivuus\state'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$sid = (Get-Process -Id $PID).SessionId
Set-Content -Path (Join-Path $state 'agent-session.txt') -Value "$sid" `
            -Encoding ASCII -NoNewline

$env:SIGNALING_URL = 'ws://192.168.3.1:8080'
$env:LOCAL_IP      = '192.168.3.2'
$env:RUST_LOG      = 'info'

# UTF-8 both ways: the agent writes UTF-8 and PowerShell would otherwise decode
# it through the OEM code page, turning every accent into mojibake.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$log = New-Object System.IO.StreamWriter('C:\nivuus\agent.log', $false,
                                         (New-Object System.Text.UTF8Encoding($false)))
try {
    & 'C:\nivuus\agent\agent.exe' *>&1 |
        ForEach-Object { $log.WriteLine([string]$_); $log.Flush() }
}
finally { $log.Close() }
