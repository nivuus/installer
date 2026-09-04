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
# Les services de la chaine d authentification. TOUS livres en 'Manual' par
# LTSC, et leurs declencheurs de demarrage a la demande ne se declenchent pas
# sur cette edition : mesure du 2026-08-30, ils etaient tous 'Stopped' apres
# chaque redemarrage et l ouverture de session Xbox echouait tant qu ils
# l etaient. D ou 'Automatic', qui est le geste durable.
$XboxServices = @('wlidsvc', 'XblAuthManager', 'XboxNetApiSvc', 'XblGameSave', 'LicenseManager')

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
        Les services de la chaine Xbox, en demarrage automatique ET demarres.

        ClipSVC est traite A PART et volontairement : c est un service PROTEGE,
        Set-Service y rend "Access is denied" meme en administrateur. Il se
        DEMARRE tres bien, il ne se reconfigure pas - le refuser en bloc ferait
        echouer une etape pour un service qui, lui, repond.
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
    # ClipSVC est PROTEGE : on le demarre, on ne le reconfigure pas, donc rien
    # a relire de son type de demarrage - il restera 'Manual' et c est normal.
    try { Start-Service -Name 'ClipSVC' -ErrorAction Stop ; $done += 'ClipSVC' }
    catch { $failed += "ClipSVC ($($_.Exception.Message))" }

    if ($failed.Count -gt 0) {
        Write-GamingLog "services Xbox incomplets : $($failed -join ' ; ')"
        return $false
    }
    Write-GamingLog "services Xbox en Automatic et demarres (relu) : $($done -join ', ')"
    return $true
}
