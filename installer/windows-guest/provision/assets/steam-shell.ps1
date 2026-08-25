<#
    Windows shell for the appliance session: Steam, and nothing else.

    Replacing explorer.exe removes the taskbar, the wallpaper and the desktop
    icons outright, rather than hiding them - this machine has no physical
    screen and no use for a Windows desktop, so the shell that draws one is
    pure surface.

    The loop is the whole point. Without a shell process alive the session
    shows a black screen and a streaming client sees nothing, so Steam closing
    for any reason - an update restart, a crash, the owner quitting it - must
    not be able to strand the appliance.
#>
$ErrorActionPreference = 'Continue'
$SteamExe = 'D:\Steam\steam.exe'

# Steam routinely exits its launcher process and continues in a child, so
# -Wait on the process we spawn would report "closed" while Steam is very much
# running and would spawn a second instance. Ask the process table instead: no
# steam process at all is the only honest definition of "closed".
while ($true) {
    if (-not (Get-Process -Name 'steam' -ErrorAction SilentlyContinue)) {
        if (Test-Path $SteamExe) {
            Start-Process -FilePath $SteamExe
        }
    }
    Start-Sleep -Seconds 3
}
