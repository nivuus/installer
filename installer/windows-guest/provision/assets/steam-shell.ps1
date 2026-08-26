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
$Wallpaper = 'C:\nivuus\wallpaper.png'

# Le fond est dessine PAR CE SCRIPT, pas par Windows. Sans explorer.exe il n y a
# pas de bureau, donc pas de papier peint : une image posee dans le registre ne
# s afficherait nulle part. Or l ecran noir se voit vraiment — entre l ouverture
# de session et l apparition de Steam, jusqu a trois minutes au tout premier
# lancement, le temps qu il telecharge sa mise a jour.
#
# La fenetre reste DERRIERE tout le reste (pas de TopMost) : elle habille le
# vide, elle ne doit jamais passer devant un jeu. Et tout l habillage est dans un
# try : un fond qui echoue ne doit pas empecher la console de demarrer, ce qui
# serait echanger un ecran noir contre un ecran noir sans Steam.
try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    if (Test-Path $Wallpaper) {
        $form = New-Object System.Windows.Forms.Form
        $form.FormBorderStyle = 'None'
        $form.WindowState = 'Maximized'
        $form.BackColor = [System.Drawing.Color]::FromArgb(14, 17, 23)
        $form.BackgroundImage = [System.Drawing.Image]::FromFile($Wallpaper)
        $form.BackgroundImageLayout = 'Zoom'
        $form.ShowInTaskbar = $false
        $form.TopMost = $false
        $form.Show()
        $form.SendToBack()
        [System.Windows.Forms.Application]::DoEvents()
    }
}
catch {
    Write-Warning "wallpaper not shown: $_"
}

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
    # DoEvents garde la fenetre de fond vivante : une form jamais pompee cesse
    # de se redessiner et Windows la marque « ne repond pas ».
    try { [System.Windows.Forms.Application]::DoEvents() } catch { }
    Start-Sleep -Seconds 3
}
