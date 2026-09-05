<#
    Etape 33 : winget, hors ligne.

    Cette etape n existe que pour l etape 34. IoT Enterprise LTSC n embarque
    AUCUN Microsoft Store - c est la raison pour laquelle cette edition a ete
    choisie - et « Services de jeu » (Gaming Services) n existe que sous forme
    de paquet du Store. La source « msstore » de winget est le seul chemin
    mesure qui atteigne le Store depuis cette edition (mesure du 2026-08-30 sur
    l invite de production, build 26100).

    Tout ce qui est pose ici voyage HORS LIGNE et EPINGLE (fetch_payload.py,
    WINGET_VERSION) : c est la part que la construction peut figer, et elle la
    fige. Ce que l etape 34 ira chercher en ligne est ce qui, par nature, ne
    peut pas l etre.

    LE MODE D ECHEC A CONNAITRE : un bundle App Installer pose SANS ses
    frameworks x64 s installe sans la moindre erreur et ne depose aucun
    winget.exe. La documentation IoT de Microsoft nomme ce silence. D ou
    l ordre ci-dessous - dependances d abord, bundle ensuite - et la
    verification finale, qui LANCE winget au lieu de constater sa presence.

    MESURE, 2026-08-30, et elle ne se voit pas en lisant le code : Add-AppxPackage
    rend 0x80070005 « acces refuse » quand il est appele par WinRM. La session
    reseau n a pas les droits du service de deploiement AppX. Ce n est pas un
    probleme ici - le provisionnement tourne dans la session 1 interactive,
    lancee par FirstLogonCommands - mais quiconque rejoue cette etape depuis
    l hote pour diagnostiquer se heurtera au refus et croira a un droit
    manquant. Le contournement est une tache planifiee /it, comme l etape 40.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$PayloadWinget = Join-Path $PayloadRoot 'drivers\winget'
# Copie sur C: de ce que le rafraichissement de l etape 34 devra dot-sourcer a
# CHAQUE ouverture de session : l ISO de charge utile n est pas garantie montee
# des que le provisionnement est fini, et une tache qui la cherche echouerait
# silencieusement des le premier redemarrage sans elle.
$GamingDir = 'C:\nivuus\gaming'

New-Item -ItemType Directory -Force -Path $GamingDir | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\winget-path.ps1') `
          -Destination (Join-Path $GamingDir 'winget-path.ps1') -Force
. (Join-Path $GamingDir 'winget-path.ps1')

# --- Les frameworks, AVANT le bundle. Ils sont poses un par un et leurs
# erreurs sont retenues sans lever : un framework deja present a la meme
# version rend une erreur qui n en est pas une, et distinguer les deux cas ici
# ne servirait a rien - c est la resolution de winget.exe, plus bas, qui dit
# si le compte y est. Une erreur n est donc rapportee QUE si la fin echoue.
$deps = @(Get-ChildItem -Path (Join-Path $PayloadWinget 'deps') -Filter '*.appx' `
                        -ErrorAction SilentlyContinue)
if ($deps.Count -eq 0) {
    throw "aucun framework dans $PayloadWinget\deps : la charge utile a ete construite avant que winget y entre, ou fetch_payload.py n a pas extrait le zip de dependances. Le bundle seul s installerait sans erreur et ne deposerait pas winget.exe."
}
$depErrors = @()
foreach ($dep in $deps) {
    try {
        Add-AppxPackage -Path $dep.FullName -ErrorAction Stop
        Write-Host "framework pose : $($dep.Name)"
    }
    catch {
        $depErrors += "$($dep.Name) : $($_.Exception.Message.Split([char]10)[0])"
        Write-Host "framework refuse (peut-etre deja present) : $($dep.Name)"
    }
}

# --- Le bundle. Deux poses, et elles ne font pas la meme chose :
#   Add-AppxPackage l installe pour l UTILISATEUR courant, donc tout de suite,
#     donc utilisable par l etape 34 qui suit dans cette meme session ;
#   Add-AppxProvisionedPackage l inscrit dans l IMAGE, avec sa licence hors
#     ligne, pour que tout profil recree ensuite l ait aussi.
# La seconde sans la premiere ne donnerait rien avant la prochaine ouverture
# de session ; la premiere sans la seconde disparaitrait avec le profil.
$bundles = @(Get-ChildItem -Path $PayloadWinget -Filter '*.msixbundle')
if ($bundles.Count -ne 1) {
    throw "il faut exactement un .msixbundle dans $PayloadWinget, il y en a $($bundles.Count) ($($bundles.Name -join ', ')) : un relevement de version qui laisse l ancien a cote du nouveau rendrait le choix arbitraire"
}
$bundle = $bundles[0].FullName
$license = Join-Path $PayloadWinget 'License1.xml'
if (-not (Test-Path $license)) {
    throw "$license est absent : Add-AppxProvisionedPackage refuse un paquet du Store sans sa licence hors ligne, et winget disparaitrait au premier profil recree"
}
# Meme traitement que les frameworks, et pour la meme raison : reposer le
# bundle deja present a cette version rend une erreur qui n en est pas une, et
# une etape rejouee a la main - ce que fait quiconque diagnostique - ne doit
# pas echouer sur un etat qui est justement celui qu elle cherche a obtenir.
# C est la verification finale qui tranche, pas ce code de retour.
$bundleError = ''
try { Add-AppxPackage -Path $bundle -ErrorAction Stop }
catch { $bundleError = $_.Exception.Message.Split([char]10)[0] }
# Meme protection, et pour exactement la meme raison : cette etape est rejouee
# a la main pour diagnostiquer (voir l en-tete), et run-all.ps1 n ecrit son
# temoin .done qu au retour de l etape - un echec plus bas y ramene. Non
# protegee, l inscription mourait AVANT la verification finale, qui est
# justement ce qui doit trancher.
try { Add-AppxProvisionedPackage -Online -PackagePath $bundle -LicensePath $license | Out-Null }
catch { $bundleError = ($bundleError, $_.Exception.Message.Split([char]10)[0] |
                        Where-Object { $_ }) -join ' | ' }
Write-Host "App Installer pose et inscrit dans l image : $($bundles[0].Name)"

# --- La verification. On ne se contente pas de constater le paquet : c est
# exactement l etat qu un bundle sans frameworks produit. On resout
# l executable, puis on le LANCE - le seul constat qui distingue « installe »
# de « utilisable », et le seul que l etape 34 puisse tenir pour acquis.
try { $winget = Resolve-Winget }
catch {
    $detail = if ($depErrors.Count -gt 0) { " Les frameworks avaient rendu : $($depErrors -join ' | ')" } else { '' }
    if ($bundleError) { $detail += " Le bundle avait rendu : $bundleError" }
    throw "$($_.Exception.Message)$detail"
}
$version = @(& $winget --version 2>&1 | ForEach-Object { "$_" })
if ($LASTEXITCODE -ne 0) {
    throw "$winget existe mais 'winget --version' a rendu $LASTEXITCODE : $($version -join ' ')"
}
Write-Host "winget utilisable : $($version -join ' ') ($winget)"
