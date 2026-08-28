<#
    Stage 10: NVIDIA display driver.

    Mandatory before the HDR probe means anything: the Advanced Color stack is
    the driver's, not the OS's. Installed offline from the payload with
    -noreboot, so the vendor installer never restarts on its own schedule. If
    it reports that a reboot is required, this stage defers device
    verification to 99-marker.ps1 and run-all.ps1 restarts the guest between
    stages: rebooting from inside this stage, before run-all.ps1 records it as
    done, would rerun the whole install and reboot forever.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$installer = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\nvidia') -Filter '*.exe' |
             Sort-Object Name | Select-Object -First 1
if (-not $installer) { throw "no NVIDIA installer in $PayloadRoot\drivers\nvidia" }

Write-Host "installing $($installer.Name)"
$proc = Start-Process -FilePath $installer.FullName `
                      -ArgumentList '-s', '-noreboot', '-clean' `
                      -Wait -PassThru

# NVIDIA's silent installer returns 0 (success), 1 (success, reboot required),
# or anything else on failure.
if ($proc.ExitCode -eq 0) {
    $gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
    if (-not $gpu) { throw 'no NVIDIA display device after installing the driver' }
    if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }
    Write-Host "NVIDIA device OK: $($gpu.FriendlyName)"
}
elseif ($proc.ExitCode -eq 1) {
    $StateDir = 'C:\nivuus\state'
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Set-Content -Path (Join-Path $StateDir 'reboot.requested') -Value (Get-Date -Format o)
    Write-Host 'NVIDIA installer requires a reboot; device verification deferred to 99-marker.ps1'
}
else {
    throw "NVIDIA installer exited $($proc.ExitCode)"
}
