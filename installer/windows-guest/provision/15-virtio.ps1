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

# Poll for the PnP network device to reach OK status with a 60-second timeout.
# Device enumeration is not instantaneous, and healthy guests can exhibit
# delays after pnputil /install depending on boot timing. Note: Get-PnpDevice
# reports driver binding via Status='OK'; Get-NetAdapter reports link state
# via Status (Up/Down/Disconnected). These are different Status domains.
$deadline = (Get-Date).AddSeconds(60)
$pnpDevice = $null
while ((Get-Date) -lt $deadline) {
    $pnpDevice = Get-PnpDevice -Class Net -ErrorAction SilentlyContinue |
                 Where-Object { $_.Description -match 'VirtIO|Red Hat' -and $_.Status -eq 'OK' }
    if ($pnpDevice) { break }
    Start-Sleep -Seconds 2
}
if (-not $pnpDevice) { throw "no virtio network device reached OK status after installing NetKVM (waited 60 sec)" }

# Get the adapter for logging link state (diagnosis only; link depends on host bridge, not driver).
$nic = Get-NetAdapter -ErrorAction SilentlyContinue |
       Where-Object { $_.InterfaceDescription -match 'VirtIO|Red Hat' }
$linkStatus = if ($nic) { $nic.Status } else { 'not yet enumerated' }
Write-Host "NetKVM driver OK (PnP device bound): adapter link state is $linkStatus"

# --- Everything below is best-effort. A failure here is logged, never fatal.

# WinFsp: try to install, log success or failure without failing provisioning
try {
    $msi = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\winfsp') `
                         -Filter '*.msi' -ErrorAction Stop | Select-Object -First 1
    if ($msi) {
        $p = Start-Process -FilePath 'msiexec.exe' `
                           -ArgumentList '/i', $msi.FullName, '/qn', '/norestart' `
                           -Wait -PassThru
        if ($p.ExitCode -in @(0, 3010)) {
            Write-Host "WinFsp installed successfully"
        }
        else {
            Write-Host "WinFsp installer FAILED: exit $($p.ExitCode)"
        }
    }
}
catch {
    Write-Host "WinFsp install skipped (optional): $($_.Exception.Message)"
}

# viofs: try to install, log success or failure without failing provisioning
try {
    $viofs = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\virtio\viofs') `
                           -Filter '*.inf' -ErrorAction Stop | Select-Object -First 1
    if ($viofs) {
        $p = Start-Process -FilePath 'pnputil.exe' `
                           -ArgumentList '/add-driver', $viofs.FullName, '/install' `
                           -Wait -PassThru -NoNewWindow
        if ($p.ExitCode -in @(0, 3010)) {
            Write-Host "viofs driver installed successfully"
        }
        else {
            Write-Host "viofs driver install FAILED: pnputil exit $($p.ExitCode)"
        }
    }
}
catch {
    Write-Host "viofs install skipped (optional): $($_.Exception.Message)"
}
