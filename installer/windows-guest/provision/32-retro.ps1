<#
    Etape 32 : le retrogaming, quand il a ete demande.

    Cette etape s execute TOUJOURS, cochee ou non. Une etape absente ne laisse
    aucune trace, et six mois plus tard personne ne sait si elle a echoue ou
    n a jamais tourne : quand le retrogaming est desactive, elle ecrit
    POURQUOI elle s arrete et sort en succes.

    Elle n execute PAS « retro sync ». Les partages ne sont montes qu a
    l etape 35, donc G:\ROMs n existe pas encore et un scan ne trouverait
    aucune ROM. La premiere synchronisation de la bibliotheque vient de
    l hote, apres le provisionnement. Meme raison pour le manifeste
    UTILISATEUR (G:\retro\emulators.toml) : illisible ici, son absence est
    normale et jamais une erreur.

    Deux prerequis mesures sont VERIFIES, jamais supposes :
      - 7zr.exe, sans lequel RetroArch — donc l essentiel de la bibliotheque
        retro — ne s installe pas du tout ;
      - 1,5 Gio libres dans le dossier temporaire, sur la partition SYSTEME.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# 1,3 Gio mesure : RetroArch et ses cores transitent ENSEMBLE par le dossier
# temporaire avant de basculer vers le volume d emulation. 1,5 Gio couvre ce
# transit et la marge du reste.
$MinTempFreeGiB = 1.5
$EmulationRoot = 'D:\Emulation'
$RetroBinDir = 'D:\Emulation\bin'
# Pas de numero de version dans le chemin : l installateur est choisi par
# motif ci-dessous, et un chemin qui annonce une version que la charge utile
# ne porte plus mentirait au premier bump.
$PythonRoot = 'C:\Python'
$PayloadRetro = Join-Path $PayloadRoot 'drivers\retro'

# --- La case a-t-elle ete cochee ?
$toggle = Join-Path $PayloadRoot 'config\retro.psd1'
if (-not (Test-Path $toggle)) {
    # build.py rend ce fichier DANS TOUS LES CAS (apollo.render_retro) et
    # payload.verify_staged l exige : son absence ne signifie pas « le
    # proprietaire n en veut pas », mais « cette charge utile est anterieure a
    # l option ». Les deux ne se confondent pas, et seule la premiere autorise
    # a ne rien faire en silence.
    throw "$toggle est absent : charge utile anterieure a l option retrogaming, son etat ne peut pas etre deduit"
}
$retro = Import-PowerShellDataFile -Path $toggle
if (-not $retro.Enabled) {
    Write-Host "retrogaming desactive (config\retro.psd1 : Enabled = `$false) : ni Python, ni 7zr, ni emulateur ne sont installes. Cette etape a bien tourne et n avait rien a faire."
    return
}
Write-Host 'retrogaming demande (config\retro.psd1 : Enabled = $true)'

# --- Le volume persistant. Les emulateurs pesent des gigaoctets et vivent sur
# D:, jamais sur C: qui est regeneree a chaque reconstruction. L etape 20 le
# monte et le marque ; sans ce marqueur, rien ne prouve que D: est le bon
# volume et il n y a nulle part ou installer.
if (-not (Test-Path 'D:\state\NIVUUS-DATA.id')) {
    throw 'le volume persistant prepare par l etape 20 est introuvable (D:\state\NIVUUS-DATA.id absent) : les emulateurs n ont nulle part ou aller'
}

# --- L espace temporaire. L installation extrait dans %TEMP% AVANT de
# basculer vers le volume d emulation, pour qu une extraction interrompue ne
# laisse pas une installation a moitie ecrasee. Le transit pese donc sur la
# partition SYSTEME, qui n est pas celle des jeux : une VM au disque systeme
# etroit echoue au milieu du provisionnement, et le message doit dire
# exactement cela plutot que de laisser lire un manque d espace generique.
$tempPath = [System.IO.Path]::GetTempPath()
$tempDrive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($tempPath)).Substring(0, 1)
$tempFreeGiB = [math]::Round($tempDrive.Free / 1GB, 2)
if ($tempDrive.Free -lt ($MinTempFreeGiB * 1GB)) {
    throw "le dossier temporaire $tempPath n a que $tempFreeGiB Gio libres sur la partition SYSTEME $($tempDrive.Name): ; l installation des emulateurs y fait transiter environ 1,3 Gio avant de basculer vers $EmulationRoot, il en faut au moins $MinTempFreeGiB. Ce n est PAS le volume des jeux qui manque de place : agrandir la partition systeme, ou pointer TEMP ailleurs."
}
Write-Host "dossier temporaire : $tempFreeGiB Gio libres sur $($tempDrive.Name): (minimum $MinTempFreeGiB Gio)"

# Les deux prerequis sont tenus : a partir d ici seulement, on ecrit.
New-Item -ItemType Directory -Force -Path $EmulationRoot | Out-Null

# --- Python. Le paquet retro est du Python, et LTSC n en embarque aucun.
# InstallAllUsers : le declenchement depuis l hote passe par WinRM, dans une
# autre session que celle-ci — un Python installe « pour cet utilisateur »
# lui serait invisible.
$python = Join-Path $PythonRoot 'python.exe'
if (-not (Test-Path $python)) {
    $installer = Get-ChildItem -Path $PayloadRetro -Filter 'python-*-amd64.exe' `
                               -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $installer) {
        throw "aucun installateur Python dans $PayloadRetro : la charge utile a ete construite sans --retro alors que la case est cochee (voir fetch_payload.py --retro)"
    }
    $proc = Start-Process -FilePath $installer.FullName -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=1', "TargetDir=$PythonRoot", 'Include_pip=1',
        'Include_test=0', 'Include_doc=0', 'PrependPath=1', 'Shortcuts=0',
        'AssociateFiles=0')
    if ($proc.ExitCode -ne 0) { throw "l installateur Python a rendu $($proc.ExitCode)" }
}
if (-not (Test-Path $python)) { throw "pas de python.exe sous $PythonRoot apres installation" }
Write-Host "Python installe : $PythonRoot"

# --- 7zr.exe. EXIGENCE, PAS COMMODITE. Les archives de RetroArch — les
# seules du manifeste — utilisent le filtre de compression BCJ2, que la
# bibliotheque Python d extraction marque « Unsupported » dans son propre
# code, et l editeur ne publie aucune variante .zip. Sans ce binaire,
# l emulateur qui couvre l essentiel de la bibliotheque retro ne s installe
# pas du tout.
#
# Il va sur D: pour survivre a la reconstruction de C:, et son dossier entre
# dans le PATH MACHINE : le paquet cherche 7zr par shutil.which(), et
# « retro install » relance depuis l hote tourne dans une autre session.
$sevenZr = Join-Path $PayloadRetro '7zr.exe'
if (-not (Test-Path $sevenZr)) {
    throw "$sevenZr est absent : sans 7zr.exe, les archives BCJ2 de RetroArch sont inextractibles et aucun emulateur retro ne s installe"
}
New-Item -ItemType Directory -Force -Path $RetroBinDir | Out-Null
Copy-Item -Path $sevenZr -Destination $RetroBinDir -Force
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if ($machinePath -notlike "*$RetroBinDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$machinePath;$RetroBinDir", 'Machine')
}
# Le PATH machine ne redescend pas dans un processus deja lance : il faut
# aussi le poser ici, sans quoi le « retro install » de cette etape meme ne
# trouverait pas le binaire qu elle vient de deposer.
$env:Path = "$env:Path;$RetroBinDir"
# Relire au lieu de croire l ecriture, et verifier la PROPRIETE qui compte :
# que 7zr.exe se resolve par le PATH, exactement comme le fera shutil.which().
if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -notlike "*$RetroBinDir*") {
    throw "$RetroBinDir n est pas entre dans le PATH machine : un 'retro install' declenche depuis l hote ne trouverait pas 7zr.exe"
}
if (-not (Get-Command '7zr.exe' -ErrorAction SilentlyContinue)) {
    throw "7zr.exe ne se resout pas par le PATH depuis $RetroBinDir : les archives BCJ2 de RetroArch resteraient inextractibles"
}
Write-Host "7zr.exe depose dans $RetroBinDir et joignable par le PATH"

# --- Le paquet retro, hors ligne. Les roues sont figees a la construction
# (fetch_payload.py --retro) et posees sans index : le provisionnement ne doit
# pas dependre de PyPI, seuls les emulateurs se telechargent.
$wheels = Join-Path $PayloadRetro 'wheels'
if (-not (Test-Path $wheels)) {
    throw "$wheels est absent : la charge utile ne porte pas le paquet retro (fetch_payload.py --retro)"
}
& $python -m pip install --no-index --find-links $wheels --upgrade retro
if ($LASTEXITCODE -ne 0) { throw "pip install retro a rendu $LASTEXITCODE" }
$retroExe = Join-Path $PythonRoot 'Scripts\retro.exe'
if (-not (Test-Path $retroExe)) { throw "pas de $retroExe apres l installation du paquet" }

# --- Les emulateurs du manifeste noyau : telecharges, verifies par empreinte,
# installes sous D:\Emulation. Idempotent (temoin .retro-version par
# emulateur), donc rejouable a chaque reconstruction sans retelecharger.
& $retroExe install --emulation-root $EmulationRoot
$installExit = $LASTEXITCODE
if ($installExit -eq 0) {
    Write-Host "emulateurs installes dans $EmulationRoot"
}
elseif ($installExit -eq 1) {
    # Code 1 = au moins un emulateur manque, les autres sont installes et le
    # rapport nomme lesquels. Une URL morte chez un editeur ne doit pas
    # emporter le provisionnement d une console dont le streaming, lui,
    # fonctionne : l operateur reste joignable et rejoue « retro install »
    # depuis l hote sans reconstruire la VM. Meme arbitrage que ViGEmBus
    # (etape 25) et que les partages non montes (etape 35).
    Write-Host "WARNING: au moins un emulateur ne s est pas installe (rapport ci-dessus) ; rejouer 'retro install' depuis l hote une fois la cause levee"
}
else {
    throw "retro install a rendu $installExit : le manifeste n a pas pu etre lu, aucun emulateur n a ete installe"
}

Write-Host 'retrogaming installe ; la premiere synchronisation de la bibliotheque viendra de l hote, une fois G: monte par l etape 35'
