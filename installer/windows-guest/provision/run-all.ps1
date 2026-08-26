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

$rebootPending = $false
$currentStage = $null
Start-Transcript -Path 'C:\nivuus\provision.log' -Append | Out-Null
try {
    $stages = @('00-bootstrap.ps1', '10-nvidia.ps1', '15-virtio.ps1',
                '20-disk.ps1', '25-apollo.ps1', '30-steam.ps1',
                '35-shares.ps1',
                '40-agent.ps1', '50-power.ps1', '55-updates.ps1',
                '99-marker.ps1')
    foreach ($stage in $stages) {
        $currentStage = $stage
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

        # A stage that needs a reboot leaves this sentinel. The .done file
        # above is written first, so on resume the stage is skipped instead of
        # rerunning and requesting another reboot forever.
        $reboot = Join-Path $StateDir 'reboot.requested'
        if (Test-Path $reboot) {
            Remove-Item -Path $reboot -Force
            Write-Host "$stage requested a reboot, restarting"
            $rebootPending = $true
            break
        }
    }
    if (-not $rebootPending) { Write-Host 'provisioning complete' }
}
catch {
    # A failed stage otherwise leaves nothing behind: no marker, nothing on
    # D:, and 5985 stays closed (correctly - a failure must never look like
    # readiness), so the VNC console was the only way to diagnose it. Write
    # what failed and why to C: and, when it is mounted, D: too - D: is the
    # one volume that survives the next reinstall, so the failure stays
    # readable even after a fresh attempt starts from scratch.
    $failure = @(
        "stage=$currentStage",
        "failed=$(Get-Date -Format o)",
        "error=$($_.Exception.Message)"
    )
    Set-Content -Path (Join-Path $StateDir 'PROVISION.failed') -Value $failure -Encoding ASCII
    if (Test-Path 'D:\state') {
        Set-Content -Path 'D:\state\PROVISION.failed' -Value $failure -Encoding ASCII
    }
    throw
}
finally {
    Stop-Transcript | Out-Null
}

if ($rebootPending) { Restart-Computer -Force }
