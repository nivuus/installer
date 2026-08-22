<#
    Stage 10: NVIDIA display driver.

    Mandatory before the HDR probe means anything: the Advanced Color stack is
    the driver's, not the OS's. Installed offline from the payload; the
    installer reboots on its own and run-all.ps1 resumes afterwards.
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
# NVIDIA's silent installer returns 0 or 1 on success; anything else is a failure.
if ($proc.ExitCode -notin @(0, 1)) { throw "NVIDIA installer exited $($proc.ExitCode)" }

$gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
if (-not $gpu) { throw 'no NVIDIA display device after installing the driver' }
if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }
Write-Host "NVIDIA device OK: $($gpu.FriendlyName)"
