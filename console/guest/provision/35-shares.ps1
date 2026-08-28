<#
    Stage 35: mount the host folders the appliance is allowed to see.

    Four TARGETED shares, never a whole filesystem root: virtiofsd runs as root
    and passthrough mode carries no permission net, so whatever is exposed is
    exposed without a filter. Sub-folders keep an incident inside a blast radius
    the owner chose.

    "Console" is this machine's vocabulary - what it is to whoever uses it, not
    what runs inside it. It stays true if the guest ever changes OS, and does
    not collide with the host, which is called Nivuus.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# Same order as domain.py's SHARES. The letters start at E: because C: is the
# disposable system and D: the games partition.
$Shares = @(
    @{ Tag = 'Downloads';   Letter = 'E'; Label = 'Telechargements' },
    @{ Tag = 'Games';       Letter = 'F'; Label = 'Jeux' },
    @{ Tag = 'Console';     Letter = 'G'; Label = 'Console' },
    @{ Tag = 'ConsoleSave'; Letter = 'H'; Label = 'Sauvegardes Console' }
)

# LE BINAIRE D ABORD, ET C EST UN ORDRE OBLIGATOIRE. $PayloadRoot designe le
# media de reponses, donc un LECTEUR OPTIQUE, et les lettres que veulent les
# partages sont precisement celles que ces lecteurs occupent. Toucher aux lecteurs
# avant d avoir copie ce qu on lit dessus scie la branche : mesure du
# 2026-08-26, l etape a demonte F: puis a echoue sur « A drive with the name
# 'F' does not exist » — et run-all.ps1 lisant TOUS les etages suivants sur ce
# meme lecteur, le provisionnement s est arrete net.
$virtiofs = Join-Path $PayloadRoot 'drivers\virtio\viofs\virtiofs.exe'
if (-not (Test-Path $virtiofs)) { throw "missing $virtiofs" }
$installed = 'C:\nivuus\virtiofs.exe'
Copy-Item -Path $virtiofs -Destination $installed -Force

# On NE DEMONTE PAS les lecteurs optiques : le provisionnement en depend encore.
# Les services sont crees en demarrage automatique et ne sont demarres
# maintenant que si leur lettre est libre ; les autres monteront au demarrage
# suivant, une fois les medias ejectes (testdomain.py eject-media). C est la
# seule facon d avoir les deux : un payload lisible jusqu au bout, et les
# partages sur les lettres voulues.
$taken = @(Get-Volume | Where-Object { $_.DriveType -eq 'CD-ROM' -and $_.DriveLetter } |
           ForEach-Object { [string]$_.DriveLetter })

foreach ($s in $Shares) {
    $name = "NivuusShare_$($s.Tag)"
    # One service per share: the packaged VirtioFsSvc mounts a SINGLE tag (on
    # Z:), so four shares need four instances with their own tag and letter.
    sc.exe delete $name 2>&1 | Out-Null
    $bin = "`"$installed`" -t $($s.Tag) -m $($s.Letter):"
    sc.exe create $name binPath= $bin start= auto DisplayName= "Nivuus share $($s.Tag)" 2>&1 | Out-Null
    sc.exe failure $name reset= 86400 actions= restart/5000/restart/10000/restart/30000 2>&1 | Out-Null
    # SANS CE DRAPEAU, LES ACTIONS DE REPRISE CI-DESSUS NE SERVENT A RIEN DANS
    # NOTRE CAS. Par defaut le SCM ne les declenche que si le service meurt sans
    # avoir annonce SERVICE_STOPPED — un plantage. virtiofs.exe, lui, SORT
    # proprement avec un code d erreur quand il ne peut pas monter : cote hote
    # virtiofsd pas encore pret, tag absent du XML du domaine, lettre encore
    # tenue par un lecteur optique au demarrage suivant. Le SCM voyait un arret
    # normal, ne reessayait jamais, et le partage restait absent jusqu au
    # prochain provisionnement. failureflag= 1 etend la reprise aux sorties en
    # erreur, ce qui est exactement le mode d echec qu on observe ici.
    sc.exe failureflag $name 1 2>&1 | Out-Null
    if ($taken -contains $s.Letter) {
        Write-Host "$($s.Tag): $($s.Letter): still held by an optical drive, will mount at next boot"
    } else {
        sc.exe start $name 2>&1 | Out-Null
    }
}

# Read back: a service that started is not a share that mounted. virtiofs needs
# the host side present, and a tag with no matching <filesystem> in the domain
# XML fails silently at mount time.
#
# ET IL EXISTE UN SECOND MODE D ECHEC, QUE CE FILET NE RATTRAPE PAS : LES
# PARTAGES NE SURVIVENT PAS A L HIBERNATION DE LA VM.
#
# Mesure le 2026-08-28. vm-idle-shutdown.sh a hiberne la machine a 11:16:50
# (« strikes=3/3 », flows=0), une sonde Moonlight l a reveillee a 11:41:43.
# Cote hote, qemu et les quatre virtiofsd sont alors NEUFS — nes a 11:42:22,
# releves par « ps -eo pid,lstart » — et le peripherique vhost-user-fs-pci a
# ete reenumere a froid ; le journal de virtiofsd dit « Client disconnected,
# shutting down » a 11:16:53Z puis « Client connected » a 11:42:26Z. Cote
# invite, Windows reprend son image memoire (Kernel-Boot 27, « boot type was
# 0x2 »), donc les quatre virtiofs.exe SONT TOUJOURS VIVANTS, StartTime du
# 26/08 22:22 — au-dessus d un tuyau mort. Les volumes WinFsp sont demontes,
# les quatre lettres ont disparu, et « Test-Path G:\ » rend False.
#
# Pourquoi le filet ci-dessus ne joue pas : failureflag= 1 etend la reprise aux
# sorties EN ERREUR, mais ici virtiofs.exe NE SORT PAS. Le SCM voit quatre
# services « Running » parfaitement sains. Verifie : aucun evenement Service
# Control Manager ni WinFsp dans les journaux Systeme et Application depuis
# l hibernation. La panne est TOTALEMENT SILENCIEUSE — cote console, les jeux
# disparaissent et rien n explique pourquoi.
#
# Le remede immediat, mesure efficace le meme jour :
#     Get-Service NivuusShare_* | Restart-Service -Force
# (les virtiofsd hotes attendent deja, connectes, et les lettres sont libres).
# Un montage CIFS que l hote tiendrait sur G$ est a remonter apres, sa
# connexion d arbre SMB etant morte avec le volume.
#
# Le correctif durable reste A FAIRE, et il n a pas sa place dans ce script,
# qui ne tourne qu au provisionnement. Deux pistes, la premiere etant le
# pendant exact du reveil :
#   - une tache planifiee sur evenement Kernel-Power 107 (« The system has
#     resumed from sleep ») qui rejoue Restart-Service sur les quatre ;
#   - une sonde cote hote dans vm-idle-shutdown.sh, qui porte deja trois blocs
#     « Self-heal » : si Test-Path echoue alors que la VM tourne, redemarrer
#     les services et le journaliser.
# La regle a retenir pour l une comme pour l autre : SURVEILLER LA LETTRE, PAS
# LE SERVICE. C est le raisonnement que ce script tient deja a la relecture
# ci-dessus ; il manque seulement de le tenir APRES le provisionnement.
Start-Sleep -Seconds 8
$missing = @()
foreach ($s in $Shares) {
    if (Test-Path "$($s.Letter):\") {
        Write-Host "$($s.Tag) mounted on $($s.Letter): ($($s.Label))"
    } else {
        $missing += "$($s.Tag) -> $($s.Letter):"
    }
}
# Relire la configuration de reprise. Une action de reprise mal posee ne se voit
# jamais : elle ne manque qu au moment ou elle aurait du servir, c est-a-dire
# quand plus personne ne regarde.
$noRetry = @()
foreach ($s in $Shares) {
    $name = "NivuusShare_$($s.Tag)"
    $flag = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$name" `
             -Name 'FailureActionsOnNonCrashFailures' -ErrorAction SilentlyContinue).FailureActionsOnNonCrashFailures
    $actions = (sc.exe qfailure $name) -join ' '
    if ($flag -ne 1 -or $actions -notmatch 'RESTART') { $noRetry += $s.Tag }
}
if ($noRetry) {
    throw "recovery actions did not take on: $($noRetry -join ', ') - a share that fails to mount would stay unmounted forever"
}
Write-Host 'shares retry on their own after a failed mount (failureflag + restart actions)'

if ($missing) {
    # Not fatal, and deliberately so. A domain without these <filesystem>
    # entries is a legitimate configuration, and a letter still held by an
    # optical drive resolves itself at the next boot. Refusing here would
    # strand an otherwise healthy appliance over a convenience mount - the
    # exact mistake this stage already made once by unmounting its own payload.
    Write-Host "WARNING: shares not mounted yet: $($missing -join ', ') - optical drives still hold the letters, or the domain XML lacks a matching <filesystem> tag"
}
