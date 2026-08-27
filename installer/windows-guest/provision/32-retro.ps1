<#
    Etape 32 : le retrogaming, quand il a ete demande.

    Cette etape s execute TOUJOURS, cochee ou non : une etape absente ne
    laisse aucune trace, et six mois plus tard personne ne sait si elle a
    echoue ou n a jamais tourne. Desactivee, elle dit POURQUOI et sort en
    succes.

    Elle n execute PAS « retro sync » : les partages ne sont montes qu a
    l etape 35, donc G:\ROMs n existe pas encore et un scan ne trouverait
    aucune ROM. La premiere synchronisation vient de l hote. Meme raison pour
    le manifeste UTILISATEUR (G:\retro\emulators.toml) : son absence est
    normale ici, jamais une erreur.

    Deux prerequis mesures sont VERIFIES, jamais supposes :
      - 7zr.exe, sans lequel RetroArch — donc l essentiel de la bibliotheque
        retro — ne s installe pas du tout ;
      - 1,5 Gio libres dans le dossier temporaire, sur la partition SYSTEME.

    Elle laisse enfin sur le volume persistant un TEMOIN DURABLE de ce qui
    s est installe, de ce qui a echoue et de quand (Write-RetroStatus).
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# 1,3 Gio mesure : RetroArch et ses cores transitent ENSEMBLE par le dossier
# temporaire avant de basculer vers D:. 1,5 Gio couvre ce transit et sa marge.
$MinTempFreeGiB = 1.5
$EmulationRoot = 'D:\Emulation'
$RetroBinDir = 'D:\Emulation\bin'
# Pas de numero de version dans le chemin : l installateur est choisi par
# motif ci-dessous, et un chemin qui l annoncerait mentirait au premier bump.
$PythonRoot = 'C:\Python'
$PayloadRetro = Join-Path $PayloadRoot 'drivers\retro'

# Le temoin durable de cette etape : ce qu il est, ou il va, pourquoi la, et
# le contrat qu il offre a l hote sont dans l asset - qui l ecrit dans le
# fichier produit lui-meme. Meme decoupe que l etape 25, pour la meme raison :
# une prose qui porte un raisonnement se deplace, elle ne se rabote pas.
. (Join-Path $PayloadRoot 'provision\assets\retro-status.ps1')
. (Join-Path $PayloadRoot 'provision\assets\retro-7zr.ps1')

# --- La case a-t-elle ete cochee ?
$toggle = Join-Path $PayloadRoot 'config\retro.psd1'
if (-not (Test-Path $toggle)) {
    # build.py rend ce fichier DANS TOUS LES CAS (apollo.render_retro) et
    # payload.verify_staged l exige : son absence ne dit pas « le proprietaire
    # n en veut pas » mais « charge utile anterieure a l option ». Seul le
    # premier etat autorise a ne rien faire en silence, d ou le throw.
    throw "$toggle est absent : charge utile anterieure a l option retrogaming, son etat ne peut pas etre deduit"
}
$retro = Import-PowerShellDataFile -Path $toggle
if (-not $retro.Enabled) {
    Write-Host "retrogaming desactive (config\retro.psd1 : Enabled = `$false) : ni Python, ni 7zr, ni emulateur ne sont installes. Cette etape a bien tourne et n avait rien a faire."
    # Meme raison, pour l hote : sans temoin il confondrait « pas voulu » et
    # « jamais arrive jusqu ici ». D: peut ne pas etre monte ici (verifie plus
    # bas) : l option est off, il n y a rien a proteger, donc pas un echec.
    if (Test-Path 'D:\state') { Write-RetroStatus 'disabled' @() }
    return
}
Write-Host 'retrogaming demande (config\retro.psd1 : Enabled = $true)'

# --- Le volume persistant. Les emulateurs pesent des gigaoctets et vivent sur
# D:, jamais sur C: regeneree a chaque reconstruction. L etape 20 le monte et
# le marque ; sans ce marqueur, rien ne prouve que D: est le bon volume.
# Aucun temoin n est possible ni perime avant ce point : il vit sur ce
# volume-la. Un provisionnement qui echoue ici laisse le PROVISION.failed de
# run-all.ps1, sur C:, et rien sur D: - qui n est pas monte.
if (-not (Test-Path 'D:\state\NIVUUS-DATA.id')) {
    throw 'le volume persistant prepare par l etape 20 est introuvable (D:\state\NIVUUS-DATA.id absent) : les emulateurs n ont nulle part ou aller'
}

# Des l entree, AVANT le moindre prerequis : le temoin porte l identifiant de
# CE passage et dit « je n ai pas fini ». Sans cette ligne, une interruption
# entre ici et l installation ne laisserait rien, et le « status=ok » d un
# provisionnement anterieur - que D: conserve - continuerait d affirmer que
# tout va bien pour le passage courant.
Write-RetroStatus 'started' @()

# Tout ce qui suit est sous garde : une levee quelconque (espace temporaire,
# installateur Python, 7zr, pip) doit laisser un temoin qui le DIT, avec sa
# cause, au lieu du silence que le lecteur ne pourrait pas distinguer d une
# etape jamais atteinte.
try {

    # --- L espace temporaire. L installation extrait dans %TEMP% AVANT de
    # basculer vers D:, pour qu une extraction interrompue ne laisse pas une
    # installation a moitie ecrasee. Le transit pese donc sur la partition
    # SYSTEME, pas sur celle des jeux : une VM au disque systeme etroit echoue au
    # milieu du provisionnement, et le message doit dire exactement cela.
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
    # autre session — un Python « pour cet utilisateur » lui serait invisible.
    $python = Join-Path $PythonRoot 'python.exe'
    if (-not (Test-Path $python)) {
        $installers = @(Get-ChildItem -Path $PayloadRetro -Filter 'python-*-amd64.exe' `
                                      -ErrorAction SilentlyContinue)
        if ($installers.Count -eq 0) {
            throw "aucun installateur Python dans $PayloadRetro : la charge utile a ete construite sans --retro alors que la case est cochee (voir fetch_payload.py --retro)"
        }
        if ($installers.Count -gt 1) {
            # Un relevement de version qui laisse l ancien a cote du nouveau :
            # « le premier » (ordre alphabetique) serait l ANCIEN, avec les roues
            # de la NOUVELLE version, et l echec viendrait bien plus tard.
            throw "plusieurs installateurs Python dans $PayloadRetro ($($installers.Name -join ', ')) : impossible de choisir, la charge utile en porte un perime - relancer fetch_payload.py --retro, qui supprime celui qui n est plus epingle"
        }
        $installer = $installers[0]
        $proc = Start-Process -FilePath $installer.FullName -Wait -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=1', "TargetDir=$PythonRoot", 'Include_pip=1',
            'Include_test=0', 'Include_doc=0', 'PrependPath=1', 'Shortcuts=0',
            'AssociateFiles=0')
        if ($proc.ExitCode -ne 0) { throw "l installateur Python a rendu $($proc.ExitCode)" }
    }
    if (-not (Test-Path $python)) { throw "pas de python.exe sous $PythonRoot apres installation" }
    Write-Host "Python installe : $PythonRoot"

    # --- 7zr.exe, sans lequel aucun emulateur retro ne s installe : le detail
    # (le filtre BCJ2, le PATH machine, la resolution verifiee) est dans
    # l asset, coupe le long de cette couture pour tenir sous les 200 lignes.
    Install-Retro7zr -PayloadRetro $PayloadRetro -BinDir $RetroBinDir

    # --- Le paquet retro, hors ligne. Les roues sont figees a la construction
    # (fetch_payload.py --retro) et posees sans index : seuls les emulateurs, plus
    # bas, se telechargent.
    $wheels = Join-Path $PayloadRetro 'wheels'
    if (-not (Test-Path $wheels)) {
        throw "$wheels est absent : la charge utile ne porte pas le paquet retro (fetch_payload.py --retro)"
    }
    # 2>&1 n est pas cosmetique : pip ecrit A COUP SUR sur son flux d erreur
    # l avertissement « installed in ... which is not on PATH » (le PATH MACHINE a
    # change, pas celui de ce processus deja lance), et 5.1 sous
    # $ErrorActionPreference = 'Stop' promeut ce flux en erreur terminante - une
    # installation REUSSIE se solderait alors par un echec de provisionnement.
    & $python -m pip install --no-index --find-links $wheels --upgrade retro 2>&1 |
        ForEach-Object { Write-Host "$_" }
    if ($LASTEXITCODE -ne 0) { throw "pip install retro a rendu $LASTEXITCODE" }
    $retroExe = Join-Path $PythonRoot 'Scripts\retro.exe'
    if (-not (Test-Path $retroExe)) { throw "pas de $retroExe apres l installation du paquet" }

    # --- Les emulateurs du manifeste noyau : telecharges, verifies par empreinte,
    # installes sous D:\Emulation. Idempotent (.retro-version par emulateur).
    # Meme redirection, meme raison qu au pip ; et le rapport doit arriver dans le
    # temoin, pas seulement dans la transcription.
    $installOutput = @(& $retroExe install --emulation-root $EmulationRoot 2>&1 |
                       ForEach-Object { "$_" })
    $installExit = $LASTEXITCODE
    $installOutput | ForEach-Object { Write-Host $_ }
    if ($installExit -eq 0) {
        Write-RetroStatus 'ok' $installOutput
        Write-Host "emulateurs installes dans $EmulationRoot"
    }
    elseif ($installExit -eq 1) {
        # Code 1 = au moins un emulateur manque, les autres sont installes et le
        # rapport nomme lesquels. Une URL morte ne doit pas emporter une console
        # dont le streaming fonctionne : l operateur reste joignable et rejoue
        # « retro install » depuis l hote. Meme arbitrage que ViGEmBus (25) et que
        # les partages non montes (35) - mais le temoin, lui, doit survivre.
        Write-RetroStatus 'partial' $installOutput
        Write-Host "warning: au moins un emulateur ne s est pas installe (rapport ci-dessus) ; rejouer 'retro install' depuis l hote une fois la cause levee. $RetroStatusFile le dit a l hote, qui doit refuser de synchroniser une bibliotheque partielle."
    }
    else {
        Write-RetroStatus 'manifest-unreadable' $installOutput
        throw "retro install a rendu $installExit : le manifeste n a pas pu etre lu, aucun emulateur n a ete installe"
    }

    Write-Host 'retrogaming installe ; la premiere synchronisation de la bibliotheque viendra de l hote, une fois G: monte par l etape 35'
}
catch {
    # Le temoin nomme la situation reelle plutot qu un « echec » generique :
    # « interrupted » veut dire que l etape a leve AVANT d avoir installe quoi
    # que ce soit, et error= porte la cause. La levee repart ensuite : c est
    # run-all.ps1 qui decide de l issue du provisionnement, pas cette etape.
    if ($RetroStatusLast -eq 'started') {
        Write-RetroStatus 'interrupted' @("error=$($_.Exception.Message)")
    }
    throw
}
