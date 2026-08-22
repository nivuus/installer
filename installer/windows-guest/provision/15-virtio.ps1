<#
    Stage 15: virtio drivers.

    NetKVM is blocking: without it the guest has no network at all, so no DHCP
    lease, no 192.168.3.2, no agent and no wake-on-demand. WinFsp and viofs are
    a comfort - they mount the host's /media/data share - and this stage must
    never fail the whole provisioning over them.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$netkvm = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\virtio\netkvm') `
                        -Filter '*.inf' -ErrorAction SilentlyContinue |
          Select-Object -First 1
if (-not $netkvm) { throw "no NetKVM .inf in $PayloadRoot\drivers\virtio\netkvm" }

# /install binds the driver to devices already present; without it the NIC
# stays in "other devices" until a reboot no one triggers.
$proc = Start-Process -FilePath 'pnputil.exe' `
                      -ArgumentList '/add-driver', $netkvm.FullName, '/install' `
                      -Wait -PassThru -NoNewWindow
# pnputil returns 3010 for "installed, reboot required", which is a success.
if ($proc.ExitCode -notin @(0, 3010)) {
    throw "pnputil failed on $($netkvm.Name): exit $($proc.ExitCode)"
}

$nic = Get-NetAdapter -ErrorAction SilentlyContinue |
       Where-Object { $_.InterfaceDescription -match 'VirtIO|Red Hat' }
if (-not $nic) { throw 'no virtio network adapter after installing NetKVM' }
Write-Host "NetKVM OK: $($nic.InterfaceDescription) status $($nic.Status)"

# --- Everything below is best-effort. A failure here is logged, never fatal.
try {
    $msi = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\winfsp') `
                         -Filter '*.msi' -ErrorAction Stop | Select-Object -First 1
    if ($msi) {
        $p = Start-Process -FilePath 'msiexec.exe' `
                           -ArgumentList '/i', "`"$($msi.FullName)`"", '/qn', '/norestart' `
                           -Wait -PassThru
        Write-Host "WinFsp installer exited $($p.ExitCode)"
    }
    $viofs = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\virtio\viofs') `
                           -Filter '*.inf' -ErrorAction Stop | Select-Object -First 1
    if ($viofs) {
        Start-Process -FilePath 'pnputil.exe' `
                      -ArgumentList '/add-driver', $viofs.FullName, '/install' `
                      -Wait -PassThru -NoNewWindow | Out-Null
        Write-Host 'viofs driver submitted'
    }
}
catch {
    Write-Host "virtiofs is optional and did not install: $($_.Exception.Message)"
}
