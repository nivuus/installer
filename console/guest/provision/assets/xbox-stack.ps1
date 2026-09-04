<#
    La pile Xbox : les paquets du Store et les services d ouverture de session.

    Sorti de gaming-services.ps1 le 2026-08-30, quand "Services de jeu" a cesse
    de suffire. Un jeu GDK achete sur Steam ne demande pas un composant mais une
    CHAINE, et il ne dit jamais lequel manque : Forza Horizon 6 restait fige sur
    son ecran de demarrage, ecran noir et SANS message, avec Services de jeu
    pourtant installe et ses deux services Running.

    Dot-source par gaming-services.ps1, qui porte l etranglement, le journal et
    le temoin durable - donc Write-GamingLog est deja defini quand ces fonctions
    tournent, et ce fichier n en definit aucun de son cote.

    ASCII PUR, sans accents : Windows PowerShell 5.1 relit un .ps1 sans BOM dans
    la page de codes ANSI, ou tout octet non-ASCII change de sens.
#>

$Packages = @(
    @{ Id = '9MWPM2CQNLHN'; Name = 'Microsoft.GamingServices';       Label = 'Services de jeu' },
    @{ Id = '9WZDNCRD1HKW'; Name = 'Microsoft.XboxIdentityProvider'; Label = 'Fournisseur d identite Xbox' },
    @{ Id = '9MV0B5HZVK9Z'; Name = 'Microsoft.GamingApp';            Label = 'Application Xbox' },
    @{ Id = '9WZDNCRFJBMP'; Name = 'Microsoft.WindowsStore';         Label = 'Microsoft Store' }
)
# Les services de la chaine d authentification qu on RECONFIGURE. Livres en
# 'Manual' par LTSC, et leurs declencheurs de demarrage a la demande ne partent
# pas sur cette edition : mesure du 2026-08-30, ils etaient tous 'Stopped' apres
# chaque redemarrage et l ouverture de session Xbox echouait tant qu ils
# l etaient. D ou 'Automatic', qui est le geste durable - et il TIENT sur ces
# quatre-la (registre Start = 2, relu le 2026-09-04).
$XboxServices = @('wlidsvc', 'XblAuthManager', 'XboxNetApiSvc', 'LicenseManager')

# Ceux qu on DEMARRE sans jamais les reconfigurer, et c est deliberé pour deux
# raisons differentes :
#
#   ClipSVC      service PROTEGE. On le demarre, on ne le reconfigure pas.
#
#   XblGameSave  service a DECLENCHEUR. `sc qtriggerinfo XblGameSave` rend un
#                NETWORK EVENT / RPC INTERFACE EVENT : Windows regere son type
#                de demarrage, et il repart a 'Manual' tout seul. Mesure du
#                2026-09-04, quatre passages : Set-Service NE LEVE PAS et la
#                valeur revient a 3 trois fois sur quatre.
#
# NE REMETS PAS XblGameSave DANS LA LISTE DU DESSUS. Le forcer, c est se battre
# contre le systeme d exploitation pour obtenir un temoin DEFINITIVEMENT rouge
# sur D:\state\gaming-services.txt - et un temoin toujours rouge cesse d etre
# lu, si bien que le prochain VRAI defaut de la chaine passerait inapercu.
# 'Manual' + declencheur est l etat ATTENDU de ces deux services ; ce qui
# compte, et ce qui est verifie plus bas, est qu ils soient Running.
$XboxServicesDemarresSeulement = @('ClipSVC', 'XblGameSave')

function Install-StorePackage {
    <#
        Un paquet du Store, installe ou mis a niveau - c est la meme commande.
        Rend $true si le paquet est present a la sortie. NE LEVE PAS : l appelant
        traite les quatre paquets et ne doit pas s arreter au premier absent.
    #>
    param([hashtable]$Package, [string]$Winget)

    $before = Get-AppxVersion $Package.Name
    # --disable-interactivity : personne n est devant. --accept-*-agreements :
    # la source msstore exige d avoir affiche ses conditions au moins une fois,
    # et une invite sans lecteur bloquerait la tache jusqu au delai maximal.
    $out = @(& $Winget install --id $Package.Id --source msstore `
                               --accept-package-agreements --accept-source-agreements `
                               --disable-interactivity 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    $after = Get-AppxVersion $Package.Name

    if (($code -eq 0 -or $code -eq $NoUpgradeAvailable) -and $after) {
        $what = if ($before -eq $after) { "deja a jour en $after" } else { "$before -> $after" }
        Write-GamingLog "$($Package.Label) : $what"
        return $true
    }
    # winget a rendu un succes sans que le paquet soit la : le seul cas ou son
    # code de sortie ment, et il vaut mieux le nommer que le croire.
    Write-GamingLog "echec $($Package.Label) : winget=$code ; $($out -join ' / ')"
    return $false
}

function Set-XboxServicesAutomatic {
    <#
        La chaine Xbox : quatre services en demarrage automatique ET demarres,
        deux autres seulement demarres (voir $XboxServicesDemarresSeulement au
        haut du fichier - un service protege, un service a declencheur).

        Rend $true quand tout est dans l etat ATTENDU, ce qui n est pas
        "tout en Automatic" : pour les deux derniers, Running suffit et
        'Manual' est normal.
    #>
    $failed = @()
    $done = @()
    foreach ($name in $XboxServices) {
        try {
            Set-Service -Name $name -StartupType Automatic -ErrorAction Stop
            Start-Service -Name $name -ErrorAction Stop
        }
        catch { $failed += "$name ($($_.Exception.Message))" ; continue }
        # RELIRE, parce que Set-Service peut ne pas lever ET ne pas prendre.
        # Mesure du 2026-09-04 sur l invite : XblGameSave est reste en 'Manual'
        # sans la moindre erreur, et la ligne de journal ci-dessous - qui
        # nommait la liste VOULUE - annoncait un succes complet. Un reglage qui
        # ne prend pas et un reglage qui prend disaient exactement la meme
        # chose. Le service reste tout de meme demarre : ce qui est perdu est
        # la DUREE du geste, pas la session en cours, et c est cela qu il faut
        # pouvoir lire.
        $now = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $now -or $now.StartType -ne 'Automatic') {
            $vu = if ($now) { $now.StartType } else { 'absent' }
            $failed += "$name (Set-Service n a pas leve, mais le demarrage est reste $vu)"
            continue
        }
        $done += $name
    }
    # Les deux qu on ne reconfigure pas. On ne relit donc PAS leur type de
    # demarrage - il restera 'Manual', et c est l etat attendu, pas un echec.
    # Ce qu on relit, c est qu ils tournent : Start-Service peut rendre la main
    # sur un service qui retombe, et c est Running que la chaine Xbox exige.
    foreach ($name in $XboxServicesDemarresSeulement) {
        try { Start-Service -Name $name -ErrorAction Stop }
        catch { $failed += "$name ($($_.Exception.Message))" ; continue }
        $now = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $now -or $now.Status -ne 'Running') {
            $vu = if ($now) { $now.Status } else { 'absent' }
            $failed += "$name (demarre sans erreur, mais le statut est $vu)"
            continue
        }
        $done += "$name (demarre, type de demarrage laisse tel quel)"
    }

    if ($failed.Count -gt 0) {
        Write-GamingLog "services Xbox incomplets : $($failed -join ' ; ')"
        return $false
    }
    Write-GamingLog "chaine de services Xbox dans l etat attendu (relu) : $($done -join ', ')"
    return $true
}
