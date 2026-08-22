<#
    Stage 99: close the provisioning.

    Order matters: everything else must be true before port 5985 opens, because
    the host treats a reachable 5985 as "the guest is provisioned".
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'

# Keep the probe on C: so it can be run again without the payload medium.
Copy-Item -Path (Join-Path $PayloadRoot 'probe') -Destination 'C:\nivuus\probe' `
          -Recurse -Force

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
