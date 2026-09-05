<#
    L avertissement STEAM.HOLD, et rien d autre.

    Ce fichier REMPLACE steam-shell.ps1 (2026-08-30). Explorer.exe est redevenu
    le shell de la session, et ce changement n est pas cosmetique : sans lui,
    AUCUNE application UWP ne s active. Le formulaire de connexion a un compte
    Microsoft en est une (Microsoft.AAD.BrokerPlugin), donc l ouverture de
    session Xbox Live echouait en 0x80040154 (REGDB_E_CLASSNOTREG), et tout jeu
    GDK - Forza Horizon 6 en tete - restait fige sur son ecran de demarrage,
    ecran noir et SANS message. Voir 30-steam.ps1.

    Ce que ce script N A PLUS A FAIRE, parce qu Explorer s en charge :
      - le fond d ecran, les icones et la barre des taches (30-steam.ps1 les
        regle par le registre, une fois pour toutes) ;
      - rester vivant pour Winlogon : ce n est plus le shell, sa sortie
        n emporte plus la session.

    Ce qu il GARDE, et qui justifie qu il survive : dire au proprietaire
    POURQUOI l ecran reste sans Steam pendant une synchronisation de
    bibliotheque. Un ecran fige sans explication finit redemarre a la main,
    potentiellement au milieu d une ecriture - c est tout l objet du sentinel.

    Il est lance par une tache AtLogOn (30-steam.ps1), comme l agent de
    l etape 40 : independante du shell, donc insensible a ce qu il devient.

    ASCII PUR, sans accents : Windows PowerShell 5.1 relit un .ps1 sans BOM
    dans la page de codes ANSI, ou tout octet non-ASCII change de sens.
#>
$ErrorActionPreference = 'Continue'
$HoldFile = 'C:\nivuus\state\steam.hold'
# Meme duree que steam-launch.ps1 : au-dela, le sentinel est perime et le
# message ne doit plus s afficher, l horodatage du fichier restant seul juge.
$HoldMaxAgeSeconds = 300

# Get-Item SEUL, jamais Test-Path puis Get-Item : entre les deux, l hote a le
# temps de retirer le sentinel, et lire l horodatage d un fichier qui vient de
# disparaitre est exactement la course que ce couple veut eviter (meme geste
# que steam-launch.ps1).
function Test-SteamHold {
    $item = Get-Item -Path $HoldFile -ErrorAction SilentlyContinue
    if (-not $item) { return $false }
    $age = (Get-Date) - $item.LastWriteTime
    return ($age.TotalSeconds -lt $HoldMaxAgeSeconds)
}

# Tout l habillage est sous try : un message qui ne peut pas s afficher ne doit
# pas laisser une exception non geree dans une tache que personne ne lit. La
# console diffuse tres bien sans cette bande.
try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.FormBorderStyle = 'None'
    $form.StartPosition = 'Manual'
    $form.ShowInTaskbar = $false
    # PAS de TopMost : cette bande habille un bureau en attente, elle ne doit
    # jamais passer devant un jeu. Pendant une retenue il n y a de toute facon
    # aucun jeu - Steam est precisement ce qu on empeche de demarrer.
    $form.TopMost = $false
    # Noir pur, charte Nivuus (paragraphe 3, noir et blanc sans exception) et
    # meme fond que le papier peint : la bande ne dessine pas de couture.
    $form.BackColor = [System.Drawing.Color]::FromArgb(0, 0, 0)

    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $form.Width = $screen.Width
    $form.Height = 64
    $form.Left = $screen.Left
    $form.Top = $screen.Bottom - 64

    $holdLabel = New-Object System.Windows.Forms.Label
    $holdLabel.Dock = 'Fill'
    $holdLabel.TextAlign = 'MiddleCenter'
    $holdLabel.Font = New-Object System.Drawing.Font('Segoe UI', 14, [System.Drawing.FontStyle]::Bold)
    $holdLabel.ForeColor = [System.Drawing.Color]::White
    $holdLabel.BackColor = [System.Drawing.Color]::FromArgb(0, 0, 0)
    $holdLabel.Text = 'Mise a jour de la bibliotheque Steam en cours...'
    $form.Controls.Add($holdLabel)

    # Invisible tant que rien n est en retenue : le sentinel est l exception,
    # pas la norme, et un message permanent deviendrait aussi ignorable qu un
    # ecran fige. La form SUIT le label : sans fond d ecran a habiller (Explorer
    # le dessine desormais), une bande noire permanente n aurait aucun sens.
    $holdLabel.Visible = $false
    $form.Show()
    $form.Visible = $false
    [System.Windows.Forms.Application]::DoEvents()
}
catch {
    Write-Warning "hold notice not shown: $_"
}

while ($true) {
    # DoEvents garde la fenetre vivante : une form jamais pompee cesse de se
    # redessiner et Windows la marque "ne repond pas".
    try { [System.Windows.Forms.Application]::DoEvents() } catch { }

    # L age vient de l horodatage du FICHIER et non d une variable de ce
    # process : la tache peut redemarrer pendant la retenue sans que celle-ci
    # reparte de zero.
    try {
        if ($holdLabel) {
            $holdLabel.Visible = (Test-SteamHold)
            $form.Visible = $holdLabel.Visible
        }
    }
    catch { }

    Start-Sleep -Seconds 3
}
