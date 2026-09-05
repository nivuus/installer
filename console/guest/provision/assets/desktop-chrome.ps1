<#
    L habillage du bureau : fond Nivuus, sans icones, sans barre des taches.

    POURQUOI un script rejoue a l ouverture de session, et pas trois ecritures
    dans 30-steam.ps1 : ces trois reglages vivent dans HKCU, et deux d entre eux
    dans des cles qu Explorer cree LUI-MEME au premier demarrage.
    StuckRects3\Settings n existe pas tant qu Explorer n a jamais tourne - or au
    provisionnement il n a jamais tourne, la session etait un kiosque. Ecrire un
    binaire de 40 octets invente a sa place serait poser une structure que
    Windows n a pas ecrite, pour un reglage qui n a pas besoin de ce risque.

    Rejoue a chaque ouverture de session, il est IDEMPOTENT et il rattrape aussi
    le profil recree apres une reconstruction de C:.

    Depuis le 2026-08-30 Explorer est de nouveau le shell (voir 30-steam.ps1 :
    sans lui aucune application UWP ne s active, donc pas d ouverture de session
    Xbox Live). Le bureau existe donc reellement : ce qui etait ABSENT sous le
    kiosque doit maintenant etre MASQUE.

    ASCII PUR, sans accents : Windows PowerShell 5.1 relit un .ps1 sans BOM dans
    la page de codes ANSI, ou tout octet non-ASCII change de sens.
#>
$ErrorActionPreference = 'Continue'
$Wallpaper = 'C:\nivuus\wallpaper.png'
$Advanced = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
$StuckRects = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3'

# --- 1. Les icones du bureau ---
# HideIcons masque le contenu du bureau sans rien supprimer : les raccourcis
# restent sur le profil, ils ne sont plus dessines.
try {
    Set-ItemProperty -Path $Advanced -Name 'HideIcons' -Value 1 -Type DWord -ErrorAction Stop
    Write-Host 'desktop icons hidden'
}
catch { Write-Warning "HideIcons: $_" }

# --- 2. La barre des taches, en masquage automatique ---
# L octet 8 de StuckRects3\Settings porte les drapeaux de la barre ; le bit
# 0x01 est l auto-masquage. On lit la valeur EXISTANTE et on n allume qu un
# bit : le reste de la structure est ecrit par Windows et ne se devine pas.
#
# La cle absente n est pas une panne : Explorer ne l a pas encore creee, et
# cette tache repassera a la prochaine ouverture de session - celle ou Explorer
# aura tourne.
try {
    $settings = (Get-ItemProperty -Path $StuckRects -Name 'Settings' -ErrorAction Stop).Settings
    if ($settings.Length -gt 8) {
        if (($settings[8] -band 0x01) -eq 0x01) {
            Write-Host 'taskbar already set to auto-hide'
        }
        else {
            $settings[8] = $settings[8] -bor 0x01
            Set-ItemProperty -Path $StuckRects -Name 'Settings' -Value $settings -ErrorAction Stop
            # Explorer ne relit cette valeur qu au demarrage : sans redemarrage
            # la barre resterait visible jusqu a la session suivante.
            Get-Process explorer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Write-Host 'taskbar set to auto-hide (explorer restarted)'
        }
    }
    else {
        # Toutes les autres branches de ce fichier disent ce qu elles ont fait
        # ou pourquoi elles n ont rien fait : un silence ici serait
        # indiscernable d un succes.
        Write-Host "taskbar: valeur Settings trop courte ($($settings.Length) octets), rien touche"
    }
}
catch {
    Write-Host 'taskbar: StuckRects3 not written yet (explorer has not run), retrying next logon'
}

# --- 3. Le fond d ecran ---
# Windows le dessine maintenant lui-meme : sous le kiosque il fallait le peindre
# dans une form, faute de bureau. SystemParametersInfo l applique a chaud, sinon
# la valeur du registre n aurait d effet qu a la session suivante.
try {
    if (Test-Path $Wallpaper) {
        Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'Wallpaper' -Value $Wallpaper
        # 6 = Ajuster : l image garde ses proportions et Windows complete en
        # noir, la couleur de la charte Nivuus - donc pas de couture visible
        # quand le client n est pas en 16:9 (le telephone en 2410x1080).
        Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'WallpaperStyle' -Value '6'
        Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'TileWallpaper' -Value '0'
        # LE NOIR DES BANDES, et il a change de main le 2026-08-30. Le style 6
        # complete l image avec la COULEUR DE FOND DU BUREAU, pas avec du noir
        # implicite - sous le kiosque c etait le BackColor de la form que
        # steam-shell.ps1 tenait. Explorer redevenu shell, c est cette cle-ci,
        # que rien ne posait : elle vaut << 0 0 0 >> par defaut (mesure du
        # 2026-09-04 sur l invite), donc la charte tenait par chance. Le
        # telephone du salon streame en 2410x1080, jamais en 16:9, et ces
        # bandes sont a l ecran a chaque session.
        Set-ItemProperty -Path 'HKCU:\Control Panel\Colors' -Name 'Background' -Value '0 0 0'
        Add-Type -Namespace Nivuus -Name Wp -MemberDefinition @'
[DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool SystemParametersInfo(int action, int param, string value, int flags);
'@
        # SPI_SETDESKWALLPAPER = 0x14 ; SPIF_UPDATEINIFILE|SPIF_SENDCHANGE = 0x03
        [void][Nivuus.Wp]::SystemParametersInfo(0x14, 0, $Wallpaper, 0x03)
        Write-Host "wallpaper applied: $Wallpaper"
    }
    else { Write-Warning "wallpaper missing: $Wallpaper" }
}
catch { Write-Warning "wallpaper: $_" }
