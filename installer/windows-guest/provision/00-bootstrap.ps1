<#
    Stage 00: logging, execution policy, WinRM (firewalled), reboot resume.

    WinRM is configured now so a failed provisioning can still be debugged, but
    its firewall rule stays disabled: 99-marker.ps1 opens port 5985 as the very
    last gesture, which is what makes "5985 reachable" mean "guest is ready".
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope LocalMachine -Force

# Resume after the reboots the driver installers trigger. The payload drive
# letter can change between boots, so the resume script rescans for the marker.
$resume = 'C:\nivuus\resume.cmd'
$body = @(
    '@echo off',
    'for %%d in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do @if exist %%d:\nivuus\PAYLOAD.id powershell.exe -NoProfile -ExecutionPolicy Bypass -File %%d:\nivuus\provision\run-all.ps1 -PayloadRoot %%d:\nivuus'
)
New-Item -ItemType Directory -Force -Path 'C:\nivuus' | Out-Null
Set-Content -Path $resume -Value $body -Encoding ASCII
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                 -Name 'NivuusProvision' -Value ('cmd /c "' + $resume + '"')

Enable-PSRemoting -Force -SkipNetworkProfileCheck
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Disable-NetFirewallRule

Write-Host "bootstrap done, payload at $PayloadRoot"
