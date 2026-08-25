<#
    Stage 50: energy, and the permanent logon this appliance is built around.

    The host hibernates this guest after ten minutes of inactivity
    (vm-idle-shutdown.timer) and wakes it from a socket. Hibernation must
    therefore work, and the guest must never fall asleep or lock on its own -
    a locked desktop is one Apollo cannot capture, and a resume that lands on
    the secure desktop drops the stream after about ten seconds.

    ⚠️ This is the exact inverse of sub-project A, which disabled autologon as
    its last act. Two consumers need it: Apollo captures an interactive desktop
    and the agent must live in session 1.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

powercfg.exe /hibernate on
if ($LASTEXITCODE -ne 0) {
    throw "powercfg /hibernate on failed with exit code $LASTEXITCODE"
}
# SCHEME_MIN, the built-in High Performance plan. The guest is a gaming host;
# its power saving is the host hibernating the whole domain, not the guest
# downclocking itself.
powercfg.exe /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
if ($LASTEXITCODE -ne 0) {
    Write-Host "warning: powercfg /setactive failed with exit code $LASTEXITCODE"
}
foreach ($what in @('monitor-timeout-ac', 'standby-timeout-ac',
                    'disk-timeout-ac', 'hibernate-timeout-ac')) {
    powercfg.exe /change $what 0
    if ($LASTEXITCODE -ne 0) {
        Write-Host "warning: powercfg /change $what failed with exit code $LASTEXITCODE"
    }
}

# "Require a password on wakeup" - the powercfg CONSOLELOCK alias does not
# exist on this build, so the policy GUID is set directly.
$wake = 'HKLM:\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51'
New-Item -Path $wake -Force | Out-Null
Set-ItemProperty -Path $wake -Name 'ACSettingIndex' -Value 0 -Type DWord
Set-ItemProperty -Path $wake -Name 'DCSettingIndex' -Value 0 -Type DWord

$perso = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization'
New-Item -Path $perso -Force | Out-Null
Set-ItemProperty -Path $perso -Name 'NoLockScreen' -Value 1 -Type DWord

# Disable screensaver-triggered lock (LTSC ships without screensaver, but
# this prevents a screensaver-with-logon-on-resume from locking the desktop).
$desktop = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop'
New-Item -Path $desktop -Force | Out-Null
Set-ItemProperty -Path $desktop -Name 'ScreenSaveActive' -Value '0' -Type String

# --- Permanent autologon. The answer file's <LogonCount> counts DOWN and
# deletes AutoAdminLogon when it reaches zero; only these registry values,
# with no AutoLogonCount alongside them, survive indefinitely.
#
# The password is stored in cleartext in HKLM. That is how AutoAdminLogon
# works, and it is the posture this appliance already has: the answer file on
# the 0600 ISO carries it in cleartext too. The guest holds no other secret.
$secrets = Import-PowerShellDataFile -Path (Join-Path $PayloadRoot 'config\secrets.psd1')
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'AutoAdminLogon' -Value '1' -Type String
Set-ItemProperty -Path $winlogon -Name 'DefaultUserName' -Value 'Administrator' -Type String
Set-ItemProperty -Path $winlogon -Name 'DefaultDomainName' -Value $env:COMPUTERNAME -Type String
Set-ItemProperty -Path $winlogon -Name 'DefaultPassword' -Value $secrets.AdminPassword -Type String
Remove-ItemProperty -Path $winlogon -Name 'AutoLogonCount' -ErrorAction SilentlyContinue

# Verify AutoAdminLogon was written. This only proves the bytes landed in the
# registry, not that Windows reads it — a reboot is the real test.
$check = Get-ItemProperty -Path $winlogon -Name 'AutoAdminLogon'
if ($check.AutoAdminLogon -ne '1') {
    throw "AutoAdminLogon not set to '1', got '$($check.AutoAdminLogon)'"
}
Write-Host 'permanent autologon configured'

# NE PAS employer Test-Path ici : hiberfil.sys porte les attributs Hidden +
# System, et le fournisseur FileSystem de PowerShell les filtre — Test-Path
# rend $false sur un fichier de 6,8 Go parfaitement present, et Test-Path n'a
# pas de parametre -Force pour passer outre. Mesure sur l'invite le 2026-08-25 :
# Test-Path = False alors que [System.IO.File]::GetAttributes rend
# « Hidden, System, Archive, NotContentIndexed ». Le piege est d'autant plus
# vicieux que le `if exist` de cmd, lui, voit le fichier — la recette manuelle
# passait donc au vert pendant que ce controle-ci echouait.
if (-not [System.IO.File]::Exists('C:\hiberfil.sys')) {
    Write-Host 'warning: hiberfil.sys is absent; hibernation may not be available'
}
