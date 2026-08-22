<#
    Nivuus guest provisioning entry point.

    Runs in session 1, launched by FirstLogonCommands and re-launched after each
    reboot by C:\nivuus\resume.cmd. Never the SYSTEM-session completion script:
    that runs in session 0, which is blind to the display.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
Set-Content -Path (Join-Path $StateDir 'provision.started') -Value (Get-Date -Format o)

Start-Transcript -Path 'C:\nivuus\provision.log' -Append | Out-Null
try {
    $stages = @('00-bootstrap.ps1', '10-nvidia.ps1', '20-sudovda.ps1', '99-marker.ps1')
    foreach ($stage in $stages) {
        $done = Join-Path $StateDir "$stage.done"
        if (Test-Path $done) {
            Write-Host "skip $stage (already done)"
            continue
        }
        $script = Join-Path $PayloadRoot "provision\$stage"
        if (-not (Test-Path $script)) { throw "missing provisioning stage: $script" }
        Write-Host "=== $stage ==="
        & $script -PayloadRoot $PayloadRoot
        Set-Content -Path $done -Value (Get-Date -Format o)
    }
    Write-Host 'provisioning complete'
}
finally {
    Stop-Transcript | Out-Null
}
