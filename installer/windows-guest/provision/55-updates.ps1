<#
    Stage 55: Windows Update policy.

    LTSC already removed feature updates - that is why it was chosen. What
    remains is the monthly security rollup, and this guest is reachable from
    the WAN on the streaming ports: turning security updates off would trade a
    breakage risk for an intrusion risk, and a reinstall does not undo an
    intrusion.

    What breaks this configuration is not a security fix but a DRIVER pushed by
    Windows Update - it would replace the NVIDIA driver the whole HDR chain
    depends on, or SudoVDA itself. So that, and only that, is blocked.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$wu = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
New-Item -Path $wu -Force | Out-Null
# ExcludeWUDriversInQualityUpdate is the load-bearing setting: it blocks
# drivers from the monthly security rollup. This is the primary control.
Set-ItemProperty -Path $wu -Name 'ExcludeWUDriversInQualityUpdate' -Value 1 -Type DWord

# Verify ExcludeWUDriversInQualityUpdate was written. Only this one is read
# back: it is the load-bearing value named above (the other two set below -
# NoAutoRebootWithLoggedOnUsers and SearchOrderConfig - are comfort and
# defense-in-depth respectively, not what protects the NVIDIA/SudoVDA driver
# chain this stage exists for).
$check = Get-ItemProperty -Path $wu -Name 'ExcludeWUDriversInQualityUpdate'
if ($check.ExcludeWUDriversInQualityUpdate -ne 1) {
    throw "ExcludeWUDriversInQualityUpdate not set to 1, got '$($check.ExcludeWUDriversInQualityUpdate)'"
}

$au = Join-Path $wu 'AU'
New-Item -Path $au -Force | Out-Null
# A reboot in the middle of a streaming session is the failure mode this
# prevents; the host reboots the guest on its own schedule instead.
Set-ItemProperty -Path $au -Name 'NoAutoRebootWithLoggedOnUsers' -Value 1 -Type DWord

$search = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching'
New-Item -Path $search -Force | Out-Null
# SearchOrderConfig may be a legacy Windows XP-era key that is silently
# ignored on build 26100, but if honoured it adds defense-in-depth on top of
# ExcludeWUDriversInQualityUpdate. Keep it: it costs nothing and does not hurt.
Set-ItemProperty -Path $search -Name 'SearchOrderConfig' -Value 0 -Type DWord

Write-Host 'security updates on, driver updates excluded, no unattended reboot'
