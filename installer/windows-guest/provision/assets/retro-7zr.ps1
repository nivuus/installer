<#
    Helper de l etape 32 : rendre 7zr.exe joignable, durablement, par le PATH.

    EXIGENCE, PAS COMMODITE. Les archives de RetroArch — les seules du
    manifeste — utilisent le filtre de compression BCJ2, que la bibliotheque
    Python d extraction marque « Unsupported » dans son propre code, et
    l editeur ne publie aucune variante .zip. Sans ce binaire, l emulateur qui
    couvre l essentiel de la bibliotheque retro ne s installe pas du tout.

    Ce qui compte n est pas qu il soit copie quelque part, mais qu il se
    RESOLVE : le paquet le cherche par shutil.which(), y compris depuis un
    « retro install » relance depuis l hote, donc dans une autre session que
    celle du provisionnement. D ou le PATH MACHINE, et une verification qui
    relit au lieu de croire l ecriture.

    Sorti de 32-retro.ps1 (arrive a la limite des 200 lignes) plutot que
    raccourci, meme decoupe que apollo-drivers.ps1 : c est deja un ensemble
    coherent et autonome, et couper le long de cette couture garde ses
    commentaires intacts au lieu de raboter ceux qui gagnent leur place.

    Dot-source this file, then call:
        Install-Retro7zr -PayloadRetro <dossier> -BinDir <dossier>
#>

function Install-Retro7zr {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadRetro,
        [Parameter(Mandatory = $true)][string]$BinDir
    )
    $sevenZr = Join-Path $PayloadRetro '7zr.exe'
    if (-not (Test-Path $sevenZr)) {
        throw "$sevenZr est absent : sans 7zr.exe, les archives BCJ2 de RetroArch sont inextractibles et aucun emulateur retro ne s installe"
    }
    # Il va sur D: pour survivre a la reconstruction de C:.
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Copy-Item -Path $sevenZr -Destination $BinDir -Force
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if ($machinePath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable('Path', "$machinePath;$BinDir", 'Machine')
    }
    # Le PATH machine ne redescend pas dans un processus deja lance : sans
    # cette ligne, le « retro install » de l etape meme ne le trouverait pas.
    $env:Path = "$env:Path;$BinDir"
    # Relire au lieu de croire l ecriture, et verifier la PROPRIETE qui compte :
    # que 7zr.exe se resolve par le PATH, exactement comme le fera which().
    if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -notlike "*$BinDir*") {
        throw "$BinDir n est pas entre dans le PATH machine : un 'retro install' declenche depuis l hote ne trouverait pas 7zr.exe"
    }
    if (-not (Get-Command '7zr.exe' -ErrorAction SilentlyContinue)) {
        throw "7zr.exe ne se resout pas par le PATH depuis $BinDir : les archives BCJ2 de RetroArch resteraient inextractibles"
    }
    Write-Host "7zr.exe depose dans $BinDir et joignable par le PATH"
}
