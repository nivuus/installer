<#
    Stage 00: logging, execution policy, WinRM, reboot resume.

    WinRM is REACHABLE from here on, deliberately. It used to be firewalled off
    until 99-marker.ps1 opened it as its last gesture, so that "5985 reachable"
    meant "guest is ready". That was a proxy, and it failed exactly where it
    mattered: when a stage throws, 99-marker is never reached, the rule stays
    shut, and the only remote way into the guest is gone - precisely when
    something needs looking at. Three provisioning runs died that way on
    2026-08-26 and each diagnosis cost an hour, because the appliance has no
    other door: the kiosk removed explorer (so no Run box) and Apollo
    deactivates the physical display (so no console).

    The marker file IS the truth about readiness; the port never was. Callers
    read C:\nivuus\state\PROVISION.done over WinRM instead - which the
    acceptance recipe already did anyway.
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
# The rule is left OPEN on purpose - see the header. Read back rather than
# trust Enable-PSRemoting: it reports success while a group policy or a
# hardened profile keeps the rule disabled behind it, and the failure would
# only surface much later as an unreachable guest.
$shut = @(Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' |
          Where-Object { -not $_.Enabled -or $_.Enabled -eq 'False' })
if ($shut) {
    $shut | Enable-NetFirewallRule
    $still = @(Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' |
               Where-Object { -not $_.Enabled -or $_.Enabled -eq 'False' })
    if ($still) { throw "WinRM firewall rule stayed disabled: $($still.Name -join ', ')" }
}

Write-Host "bootstrap done, payload at $PayloadRoot"
