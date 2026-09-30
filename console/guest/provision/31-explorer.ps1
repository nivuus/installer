<#
    Stage 31: the Explorer navigation pane shows only what the appliance user
    works with.

    Split out of stage 30 (Steam), which keeps the C: restriction. Hiding
    Windows' own shortcuts (Home, Gallery, Desktop, Downloads, Documents,
    Pictures, Music, Videos) is interface tidiness, not security: the account is
    an administrator and can undo every value written here.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# THE NAVIGATION PANE NO LONGER SHOWS WINDOWS' SHORTCUTS (Home, Gallery,
# Desktop, Downloads, Documents, Pictures, Music, Videos): the user works only in
# "Mes Fichiers" and in their applications.
#  - Home and Gallery are pane nodes: System.IsPinnedToNameSpaceTree=0. Hiding
#    Home also takes the Quick access pins with it.
#  - The six user folders of This PC: ThisPCPolicy=Hide, set on the 64-bit AND
#    the 32-bit view (WOW6432Node), as Windows does.
foreach ($clsid in '{f874310e-b6b7-47dc-bc84-b9e6b38f5903}', '{e88865ea-0e1c-4e20-9aa6-edcd0212c87c}') {
    $key = "HKCU:\Software\Classes\CLSID\$clsid"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name 'System.IsPinnedToNameSpaceTree' -Value 0 -PropertyType DWord -Force | Out-Null
}
$userFolders = @(
    '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}', # Desktop
    '{d3162b92-9365-467a-956b-92703aca08af}', # Documents
    '{088e3905-0323-4b02-9826-5d99428e115f}', # Downloads
    '{3dfdf296-dbec-4fb4-81d1-6a3438bcf4de}', # Music
    '{24ad3ad4-a569-4530-98e1-ab02f9417aa8}', # Pictures
    '{f86fa3ab-70d2-4fc7-9c99-fcbf05467f3a}'  # Videos
)
foreach ($root in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\FolderDescriptions',
                  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Explorer\FolderDescriptions') {
    foreach ($guid in $userFolders) {
        $bag = "$root\$guid\PropertyBag"
        if (-not (Test-Path $bag)) { New-Item -Path $bag -Force | Out-Null }
        Set-ItemProperty -Path $bag -Name 'ThisPCPolicy' -Value 'Hide' -Type String
        if ((Get-ItemProperty -Path $bag).ThisPCPolicy -ne 'Hide') {
            throw "hiding $guid from This PC did not take"
        }
    }
}
Write-Host 'Explorer navigation pane: Windows shortcuts hidden
