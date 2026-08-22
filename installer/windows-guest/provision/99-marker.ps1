<#
    Stage 99: close the provisioning.

    Order matters: everything else must be true before port 5985 opens, because
    the host treats a reachable 5985 as "the guest is provisioned". This is
    also where 10-nvidia.ps1's device check lands when that stage had to defer
    it to survive a driver-install reboot.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'

# Keep the probe on C: so it can be run again without the payload medium.
# Destination is the parent, not 'C:\nivuus\probe': Copy-Item nests into
# ...\probe\probe when the destination directory already exists.
Copy-Item -Path (Join-Path $PayloadRoot 'probe') -Destination 'C:\nivuus' `
          -Recurse -Force

$gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
if (-not $gpu) { throw 'no NVIDIA display device at end of provisioning' }
if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }
Write-Host "NVIDIA device OK: $($gpu.FriendlyName)"

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'AutoAdminLogon' -Value '0'
Remove-ItemProperty -Path $winlogon -Name 'DefaultPassword' -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                    -Name 'NivuusProvision' -ErrorAction SilentlyContinue

$marker = @(
    "provision_version=A1",
    "completed=$(Get-Date -Format o)",
    "computer=$env:COMPUTERNAME"
)
Set-Content -Path (Join-Path $StateDir 'PROVISION.done') -Value $marker -Encoding ASCII

Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Enable-NetFirewallRule
Write-Host 'provisioning marker written, WinRM reachable'
