<#
    Le shell Windows de la session de l appliance : le fond d ecran, et rien
    d autre.

    Remplacer explorer.exe supprime la barre des taches, le fond d ecran et les
    icones du bureau au lieu de les cacher — cette machine n a pas d ecran
    physique et aucun usage d un bureau Windows.

    CE SHELL NE LANCE PLUS STEAM, et c est la correction du 2026-08-26. Il le
    faisait, en boucle, et cela cassait deux choses a la fois :

    1. L interface de Steam est du Chromium (CEF), et CEF choisit son moteur de
       rendu UNE FOIS, au demarrage, parmi les adaptateurs qui portent un ecran.
       Ce script s execute a l ouverture de session — donc avant qu un client
       soit connecte, donc avant que SudoVDA ait cree l ecran virtuel. Le Steam
       lance ici ne trouvait aucun ecran sur la RTX 4070 et retombait sur
       SwiftShader, un rasteriseur LOGICIEL, pour toute la duree du processus
       (D:\Steam\logs\webhelper_gpu.txt : gpu_compositing = disabled_software).
       La petite fenetre du bureau y survivait, Big Picture non.
    2. La boucle relançait Steam dans les trois secondes, donc quitter Steam ne
       fermait pas la session Moonlight : elle rouvrait Steam.

    Steam est desormais lance par Apollo (entree « detached » de apps.json), une
    fois l ecran virtuel present, et surveille par steam-session.ps1 dont la
    sortie ferme la session. Ce script garde le seul role qui reste : etre un
    shell vivant. Sans shell, Winlogon considere la session comme perdue, et
    l ecran noir se voit vraiment sur une machine sans moniteur.

    STEAM.HOLD : la synchronisation de bibliotheque (hote) arrete Steam le
    temps de reecrire shortcuts.vdf, et c est steam-launch.ps1 - pas ce
    script, qui ne relance plus rien - qui fait sauter son propre lancement
    tant que le sentinel tient. Ce script-ci n a pas de geste a annuler : il
    n a que le fond d ecran a completer, pour que le proprietaire voie POURQUOI
    l ecran reste sans Steam au lieu d y lire une panne. Un ecran fige sans
    explication finit redemarre a la main, potentiellement au milieu d une
    ecriture.
#>
$ErrorActionPreference = 'Continue'
$Wallpaper = 'C:\nivuus\wallpaper.png'
$HoldFile = 'C:\nivuus\state\steam.hold'
# Meme duree que cote steam-launch.ps1 : au-dela, le sentinel est perime et le
# message ne doit plus s afficher, l horodatage du fichier restant seul juge.
$HoldMaxAgeSeconds = 300

# Le fond est dessine PAR CE SCRIPT, pas par Windows. Sans explorer.exe il n y a
# pas de bureau, donc pas de papier peint : une image posee dans le registre ne
# s afficherait nulle part.
#
# La fenetre reste DERRIERE tout le reste (pas de TopMost) : elle habille le
# vide, elle ne doit jamais passer devant un jeu. Et tout l habillage est dans un
# try : un fond qui echoue ne doit pas empecher le shell de vivre, ce qui serait
# echanger un ecran noir contre une session sans shell.
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

        # Cache derriere le fond, invisible tant que rien n est en retenue : le
        # sentinel est l exception, pas la norme, et un message qui reste en
        # permanence deviendrait aussi ignorable qu un ecran fige.
        $holdLabel = New-Object System.Windows.Forms.Label
        $holdLabel.Dock = 'Bottom'
        $holdLabel.Height = 64
        $holdLabel.TextAlign = 'MiddleCenter'
        $holdLabel.Font = New-Object System.Drawing.Font('Segoe UI', 14, [System.Drawing.FontStyle]::Bold)
        $holdLabel.ForeColor = [System.Drawing.Color]::White
        $holdLabel.BackColor = [System.Drawing.Color]::FromArgb(14, 17, 23)
        $holdLabel.Text = 'Mise a jour de la bibliotheque Steam en cours...'
        $holdLabel.Visible = $false
        $form.Controls.Add($holdLabel)

        $form.Show()
        $form.SendToBack()
        [System.Windows.Forms.Application]::DoEvents()
    }
}
catch {
    Write-Warning "wallpaper not shown: $_"
}

while ($true) {
    # DoEvents garde la fenetre de fond vivante : une form jamais pompee cesse
    # de se redessiner et Windows la marque « ne repond pas ».
    try { [System.Windows.Forms.Application]::DoEvents() } catch { }

    # Inerte tant que personne ne pose le sentinel : un Test-Path de plus tous
    # les trois secondes, sur un cycle qui tournait deja. L age vient de
    # l horodatage du FICHIER et non d une variable de CE process, car ce
    # shell peut lui-meme redemarrer (AutoRestartShell) pendant la retenue
    # sans que ca ne la fasse repartir de zero.
    try {
        if ($holdLabel) {
            $onHold = $false
            if (Test-Path $HoldFile) {
                $age = (Get-Date) - (Get-Item $HoldFile).LastWriteTime
                if ($age.TotalSeconds -lt $HoldMaxAgeSeconds) { $onHold = $true }
            }
            $holdLabel.Visible = $onHold
        }
    }
    catch { }

    Start-Sleep -Seconds 3
}
