<#
    Stage 20: SudoVDA virtual display driver.

    SudoVDA is what lets a client stream at its own resolution; it is also the
    component whose HDR support requires the 24H2 base this whole migration is
    about. The package is the one Apollo ships in drivers\sudovda.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$dir = Join-Path $PayloadRoot 'drivers\sudovda'
$cert = Join-Path $dir 'sudovda.cer'
if (-not (Test-Path $cert)) { throw "missing $cert" }

# The driver is self-signed: without the certificate in both stores, the
# unattended install would sit on a trust prompt no one can answer.
certutil.exe -addstore -f Root $cert | Out-Null
certutil.exe -addstore -f TrustedPublisher $cert | Out-Null

$install = Join-Path $dir 'install.bat'
if (-not (Test-Path $install)) { throw "missing $install" }
$proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $install `
                      -WorkingDirectory $dir -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "SudoVDA install.bat exited $($proc.ExitCode)" }

$vda = Get-PnpDevice -InstanceId 'ROOT\DISPLAY\*' -ErrorAction SilentlyContinue |
       Where-Object { $_.Status -eq 'OK' }
if (-not $vda) { throw 'no working ROOT\DISPLAY device after installing SudoVDA' }
Write-Host "SudoVDA OK: $($vda.InstanceId)"
