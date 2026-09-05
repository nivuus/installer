<#
    Masquer le curseur de la souris pendant une session Big Picture, et le
    rendre ensuite.

    Dot-source par steam-session.ps1, qui appelle ces deux fonctions et pose
    lui-meme le type [NivuusWin] dont elles se servent. Le decoupage n a pas
    d autre raison que la limite de 200 lignes par fichier, la meme qui a sorti
    apollo-drivers.ps1 et apollo-junction.ps1 de 25-apollo.ps1.

    POURQUOI CE MASQUAGE EXISTE. Big Picture se pilote entierement a la
    manette : le pointeur ne bouge jamais et reste pose au milieu de l ecran de
    la TV pendant toute la partie. C est le dernier element d interface Windows
    visible sur une machine dont on a retire explorer.exe et cache les quatre
    fenetres PowerShell precisement pour qu il ne reste rien a l ecran.
    Le defaut n est pas neuf : l apps.json ecrit a la main du 2026-07-23 faisait
    porter a l entree "Steam Big Picture" un nomousy en plus de -bigpicture, et
    le passage au gabarit apps.json.j2 l a laisse tomber sans que rien ne le
    signale.

    POURQUOI PAS nomousy. C etait un executable tiers de plus a telecharger,
    empreindre et verifier dans une charge utile qui doit s installer hors
    ligne. Les curseurs systeme de Windows font exactement la meme chose depuis
    user32.dll, qui est deja la.

    POURQUOI PAS UN prep-cmd. Apollo attend la fin d un prep-cmd AVANT de lancer
    l application : la maximisation vivait la et coutait 182 s par session
    (journal Apollo du 2026-08-26). L en-tete d apps.json.j2 en a fait une
    regle. Ce masquage se greffe donc sur la boucle de surveillance de
    steam-session.ps1, jamais devant elle.

    POURQUOI PAS UNE CLE APOLLO : IL N Y EN A PAS. Une cle de capture du
    curseur serait meilleure - elle cesserait de le capturer DANS LE FLUX en le
    laissant intact dans l invite, la ou ce fichier le cache dans l invite pour
    tout le monde. Elle n existe pas dans la version installee. Mesure du
    2026-08-29 : la table des options que le binaire lit, extraite des chaines
    de C:\Program Files\Apollo\sunshine.exe (0.4.6), va de "qp" a "locale" et se
    termine sur le message "Warning: Unrecognized configurable option [" ; elle
    ne contient AUCUNE cle de curseur. Les plus proches sont mouse, keyboard et
    controller, qui activent des peripheriques d entree. L IHM web n en connait
    pas davantage (aucune occurrence de "cursor" dans config-*.js ni dans
    locale/en.json). sunshine.conf.j2 n a donc pas ete touche : une cle inventee
    y serait ignoree EN SILENCE, ce que son en-tete interdit. La voie reste
    ouverte pour la version d Apollo qui introduirait la cle.

    CE QUE CE MASQUAGE COUTE, DU COUP : pendant une session Big Picture, une
    prise de main par le bureau distant voit un pointeur invisible, qui pointe
    quand meme. Il redevient visible a la fin de la session, ou au lancement
    suivant de l application Desktop, qui restaure les curseurs en partant.
#>

# Les identifiants OCR_* de winuser.h : fleche, saisie, sablier, croix, fleche
# haute, les quatre redimensionnements, deplacement, interdit, main, et la
# fleche au sablier du demarrage. TOUS, pas seulement la fleche - Big Picture
# affiche un sablier pendant ses chargements et une main sur ce qui est
# cliquable.
$SystemCursorIds = @(32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644,
                     32645, 32646, 32648, 32649, 32650)

# Un curseur ENTIEREMENT TRANSPARENT pose comme curseur systeme : plan AND a 1
# et plan XOR a 0 laissent chaque pixel de l ecran tel quel. Il n y a donc plus
# rien a dessiner, donc plus rien a capturer, et aucun fichier .cur a embarquer.
function Set-CursorHidden {
    try {
        # CreateCursor exige les dimensions du curseur systeme : les coder en
        # dur ferait echouer l appel sur un poste a curseurs agrandis.
        $w = [NivuusWin]::GetSystemMetrics([NivuusWin]::SM_CXCURSOR)
        $h = [NivuusWin]::GetSystemMetrics([NivuusWin]::SM_CYCURSOR)
        if ($w -le 0 -or $h -le 0) { return $false }
        $planeBytes = [int](($w * $h) / 8)
        $andPlane = New-Object byte[] $planeBytes
        $xorPlane = New-Object byte[] $planeBytes
        for ($i = 0; $i -lt $planeBytes; $i++) { $andPlane[$i] = 0xFF }
        foreach ($id in $SystemCursorIds) {
            # SetSystemCursor DETRUIT le curseur qu on lui confie : un handle
            # unique reutilise serait mort des le deuxieme identifiant, et seule
            # la fleche disparaitrait. Un curseur neuf par identifiant, donc.
            $blank = [NivuusWin]::CreateCursor([IntPtr]::Zero, 0, 0, $w, $h, $andPlane, $xorPlane)
            if ($blank -eq [IntPtr]::Zero) { return $false }
            [NivuusWin]::SetSystemCursor($blank, $id) | Out-Null
        }
        return $true
    }
    catch {
        Write-Warning "curseur non masque : $_"
        return $true   # ne pas reessayer en boucle sur une erreur durable
    }
}

# SPI_SETCURSORS relit les curseurs du registre : c est la seule facon de rendre
# ce que SetSystemCursor a remplace, l original ayant ete detruit par l appel.
function Restore-SystemCursors {
    try {
        [NivuusWin]::SystemParametersInfo([NivuusWin]::SPI_SETCURSORS, 0, [IntPtr]::Zero, 0) | Out-Null
    }
    catch {
        Write-Warning "curseurs non restaures : $_"
    }
}
