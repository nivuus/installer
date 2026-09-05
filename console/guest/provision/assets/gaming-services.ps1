<#
    Services de jeu (Gaming Services) : l installer, et le garder a jour.

    UN seul fichier pour les deux, parce que c est le meme geste : sur un
    paquet deja present, `winget install` bascule de lui-meme en mise a niveau.
    L etape 34 dot-source ce fichier et appelle Update-GamingServices ; la tache
    planifiee le relance avec -Run a chaque ouverture de session. Le chemin
    exerce au provisionnement est donc exactement celui qui tournera ensuite.

    POURQUOI une tache, et pas une pose figee dans l image : LTSC n a pas de
    Store, donc RIEN ne mettra jamais ce paquet a jour tout seul, et les jeux
    refusent de demarrer contre une copie perimee (<< Ensure GamingServices is
    up to date >>). Une version figee a la construction est un echec programme
    pour plus tard, sur une console que personne ne veut reinstaller pour ca.

    ASCII PUR, sans accents : Windows PowerShell 5.1 relit un .ps1 sans BOM dans
    la page de codes ANSI, ou tout octet non-ASCII change de sens.

    PIEGE : `exit` ci-dessous ne s execute que sous -Run. Ce fichier est
    DOT-SOURCE par l etape 34, et un `exit` en dot-source quitte l appelant -
    il emporterait le provisionnement au milieu de l etape.
#>
param([switch]$Run, [switch]$Force)

$GamingDir = 'C:\nivuus\gaming'
$StampFile = Join-Path $GamingDir 'last-check.txt'
$LogFile = Join-Path $GamingDir 'refresh.log'
$StatusFile = 'D:\state\gaming-services.txt'
# La table des quatre paquets et leur pourquoi vivent dans xbox-stack.ps1.
# L identifiant ci-dessous n est garde ici que pour le temoin durable, qui le
# publie a l hote ; il est celui que l URL du Store porte,
# https://apps.microsoft.com/detail/<id>
$StoreId = '9MWPM2CQNLHN'
# 0x8A15002B, APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE : << aucune version
# plus recente >>. C est le cas NORMAL et de loin le plus frequent, et le
# traiter comme un echec ferait crier la console tous les jours.
$NoUpgradeAvailable = -1978335189
# 20 heures, pas 24 : une session de jeu quotidienne tombe rarement a la
# minute pres, et un seuil de 24 h sauterait le controle un jour sur deux par
# simple derive de quelques minutes.
$MinHoursBetweenChecks = 20

function Write-GamingLog {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    Write-Host $line
    # Le journal ne doit pas grossir sans fin : il est reecrit quand il passe
    # 200 lignes, en gardant les 100 dernieres.
    if ((Test-Path $LogFile) -and ((Get-Content $LogFile).Count -gt 200)) {
        Set-Content -Path $LogFile -Value (Get-Content $LogFile | Select-Object -Last 100) -Encoding UTF8
    }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Write-GamingStatus {
    <#
        Le temoin durable, sur le volume qui survit aux reconstructions de C:.
        Sans lui l hote ne distingue pas << jamais tente >> de << tente et
        echoue >>, et comme l etape 34 ne fait pas echouer le provisionnement,
        ce fichier est la SEULE trace persistante de l issue. D: peut ne pas
        etre monte (etape 20 non atteinte, ou tache lancee trop tot) : ce n est
        pas un echec, juste un temoin impossible.
    #>
    param([string]$Status, [string]$Version, [string]$Detail = '')
    if (-not (Test-Path 'D:\state')) { return }
    $body = @("status=$Status", "version=$Version", "checked=$(Get-Date -Format o)",
              "store_id=$StoreId")
    if ($Detail) { $body += "detail=$Detail" }
    Set-Content -Path $StatusFile -Value $body -Encoding ASCII
}

# La pile Xbox (paquets, services, et les deux gestes qui les posent) vit dans
# son propre fichier ; celui-ci garde l etranglement, le journal et le temoin.
# Dot-source depuis $GamingDir, comme winget-path.ps1 : les DEUX appelants
# (l etape 34 et la tache -Run) y trouvent le fichier au meme endroit, parce que
# l etape 34 le copie avant de dot-sourcer celui-ci.
. (Join-Path $GamingDir 'xbox-stack.ps1')

function Get-AppxVersion {
    param([string]$Name)
    # [version] : Version est une String, et un tri de texte met << 1.9 >>
    # au-dessus de << 1.29 >> (mesure du 2026-09-04 sur l invite).
    $pkg = Get-AppxPackage -Name $Name -ErrorAction SilentlyContinue |
           Sort-Object { [version]$_.Version } -Descending | Select-Object -First 1
    if ($pkg) { return $pkg.Version }
    return ''
}

# L etape 34 appelle ce nom : il reste le temoin de version le plus parlant
# (c est le paquet dont les jeux comparent la version a la leur).
function Get-GamingServicesVersion { return (Get-AppxVersion 'Microsoft.GamingServices') }

function Update-GamingServices {
    <#
        Rend $true si Services de jeu est present et a jour a la sortie, $false
        sinon. NE LEVE PAS : les deux appelants veulent poursuivre - l etape 34
        parce qu un Store injoignable ne doit pas emporter une console qui
        diffuse tres bien Steam, la tache planifiee parce qu il n y a personne
        pour lire une exception a l ouverture de session.
    #>
    param([switch]$Force)

    # MODE NON TERMINANT RETABLI ICI : $ErrorActionPreference est a portee
    # DYNAMIQUE et l etape 34 dot-source ce fichier apres avoir pose 'Stop' -
    # sans cette ligne, << NE LEVE PAS >> est faux du seul fait d avoir ete
    # appele de la (`& $winget ... 2>&1` rend terminante la moindre ligne
    # d erreur native, l Add-Content du journal aussi), et les DEUX chemins ne
    # se comportent pas pareil en panne puisque la tache, elle, tourne sous le
    # 'Continue' par defaut. DANS LA FONCTION : au niveau du fichier, le
    # dot-source desarmerait le 'Stop' du reste de l etape 34.
    $ErrorActionPreference = 'Continue'

    New-Item -ItemType Directory -Force -Path $GamingDir | Out-Null

    # --- LES SERVICES D ABORD, ET JAMAIS ETRANGLES. Set-Service est local et
    # gratuit ; c est le Store, plus bas, qui est lent et distant. Et le
    # 2026-09-04 le demarrage de XblGameSave a ete VU repartir en 'Manual' tout
    # seul entre deux passages : une derive pareille ne se repare que par le
    # geste rejoue a chaque ouverture de session.
    $servicesOk = Set-XboxServicesAutomatic

    # --- L etranglement, QUI NE COUVRE QUE LE STORE. La VM est reveillee a la
    # demande et eteinte des qu elle est inactive : la session peut s ouvrir
    # plusieurs fois par jour, et interroger le Store a chacune n apprendrait
    # rien de plus.
    if (-not $Force -and (Test-Path $StampFile)) {
        # PAS [datetime]::TryParse avec [ref]$last : mesure du 2026-08-30, la
        # resolution de surcharge echoue sur une variable non typee, l erreur
        # n est pas terminante, et l etranglement ne s appliquait alors JAMAIS
        # - panne muette visible du seul journal, ou deux controles complets se
        # suivent a deux minutes. Parse explicite, culture invariante (ecrit
        # avec -Format o), et l illisible vaut << pas d horodatage >>.
        $last = $null
        try {
            $last = [datetime]::Parse((Get-Content $StampFile -Raw).Trim(),
                                      [Globalization.CultureInfo]::InvariantCulture,
                                      [Globalization.DateTimeStyles]::RoundtripKind)
        }
        catch { $last = $null }
        if ($last) {
            $age = (Get-Date) - $last
            if ($age.TotalHours -lt $MinHoursBetweenChecks) {
                Write-GamingLog ("Store non interroge : dernier controle il y a {0:N1} h (seuil {1} h), version {2}" -f `
                                 $age.TotalHours, $MinHoursBetweenChecks, (Get-GamingServicesVersion))
                return $servicesOk
            }
        }
    }

    try { $winget = Resolve-Winget }
    catch {
        Write-GamingLog "echec : $($_.Exception.Message)"
        Write-GamingStatus 'winget-absent' (Get-GamingServicesVersion) $_.Exception.Message
        return $false
    }

    # LES QUATRE PAQUETS, chacun traite independamment : un echec sur l un ne
    # doit pas emporter les autres, ils rendent des services differents au jeu.
    $failed = @()
    foreach ($pkg in $Packages) {
        if (-not (Install-StorePackage -Package $pkg -Winget $winget)) { $failed += $pkg.Label }
    }
    $after = Get-GamingServicesVersion

    # L horodatage date le controle du STORE, seul objet de l etranglement. Le
    # lier aussi aux services empechait de l ecrire A JAMAIS des qu un service
    # refusait de tenir - les quatre appels winget repartant alors a chaque
    # ouverture de session, indefiniment.
    if ($failed.Count -eq 0) {
        Set-Content -Path $StampFile -Value (Get-Date -Format o) -Encoding ASCII
    }
    if ($failed.Count -eq 0 -and $servicesOk) {
        Write-GamingLog "chaine Xbox complete (Services de jeu $after)"
        Write-GamingStatus 'ok' $after
        return $true
    }

    # Le temoin distingue les deux situations que l hote traite differemment :
    # une console qui n a JAMAIS eu Services de jeu, et une qui l a mais dont la
    # chaine d ouverture de session Xbox est incomplete - la seconde lance les
    # jeux non-GDK, la premiere ne lance rien de GDK du tout.
    $detail = @()
    if ($failed.Count -gt 0) { $detail += "paquets absents : $($failed -join ', ')" }
    if (-not $servicesOk) { $detail += 'services Xbox incomplets' }
    Write-GamingLog "chaine Xbox incomplete : $($detail -join ' ; ')"
    $status = if ($after) { 'chaine-incomplete' } else { 'absent' }
    Write-GamingStatus $status $after ($detail -join ' ; ')
    return $false
}

# Entree de la tache planifiee uniquement. Voir le PIEGE en tete de fichier :
# ce bloc ne doit jamais s executer en dot-source.
if ($Run) {
    . (Join-Path $GamingDir 'winget-path.ps1')
    if (Update-GamingServices -Force:$Force) { exit 0 }
    exit 1
}
