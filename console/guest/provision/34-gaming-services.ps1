<#
    Etape 34 : « Services de jeu » (Gaming Services), pose et tenu a jour.

    Le paquet que le Store publie sous l identifiant 9MWPM2CQNLHN. C est lui
    que reclament les jeux batis sur le GDK - y compris ceux achetes sur Steam
    (Forza, Sea of Thieves, Halo MCC...) qui ouvrent une session Xbox Live. Sans
    lui ils ne demarrent pas, et le message qu ils affichent parle d un composant
    que rien, sur cette edition de Windows, ne sait installer.

    Il n arrive PAS avec la charge utile hors ligne, et ce n est pas un renoncement :
    le Store ne publie aucun fichier autonome, et le figer serait de toute facon
    l erreur - les jeux comparent sa version a la leur et refusent une copie
    perimee. C est pourquoi l essentiel de cette etape n est pas l installation
    mais la TACHE qui la rejoue : LTSC n a pas de Store, donc rien d autre ne
    mettra jamais ce paquet a jour.

    CETTE ETAPE NE FAIT PAS ECHOUER LE PROVISIONNEMENT. Meme arbitrage que
    ViGEmBus (etape 25), les partages non montes (35) et un emulateur manquant
    (32) : un Store injoignable pendant l heure du provisionnement ne doit pas
    emporter une console qui diffuse parfaitement Steam. Ici l argument est
    meme plus fort qu ailleurs - la tache posee ci-dessous rejoue le meme geste
    a chaque ouverture de session, donc un echec d aujourd hui se repare tout
    seul au prochain demarrage. Le temoin sur D: dit a l hote ce qu il en est.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$TaskName = 'gaming-services-refresh'
$GamingDir = 'C:\nivuus\gaming'
$Script = Join-Path $GamingDir 'gaming-services.ps1'

# winget-path.ps1 y a ete depose par l etape 33, qui a aussi verifie que
# winget se LANCE. Son absence ici ne veut pas dire « winget manque » mais
# « l etape 33 n a pas tourne » - deux causes differentes, deux messages.
$resolver = Join-Path $GamingDir 'winget-path.ps1'
if (-not (Test-Path $resolver)) {
    throw "$resolver est absent : l etape 33 n a pas tourne, ou sa copie a echoue. Cette etape ne sait pas ou chercher winget."
}
# xbox-stack.ps1 D ABORD : gaming-services.ps1 le dot-source des son chargement,
# donc il doit deja etre a cote quand la ligne suivante s execute.
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\xbox-stack.ps1') `
          -Destination (Join-Path $GamingDir 'xbox-stack.ps1') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\gaming-services.ps1') `
          -Destination $Script -Force
. $resolver
. $Script

# --- La tache AVANT l installation, deliberement. Si le Store est injoignable
# aujourd hui, ce qui compte est que la console reessaie demain ; poser la
# tache en second la ferait sauter par la sortie anticipee du cas d echec.
#
# DEUX declencheurs, et l etranglement de 20 h dans le script les reconcilie :
#   - a l ouverture de session, le seul moment garanti sur une VM reveillee a
#     la demande et eteinte des qu elle est inactive ;
#   - tous les jours a 4 h, pour la console qui reste allumee plusieurs jours
#     d affilee et n ouvrirait donc pas de nouvelle session.
# Deux minutes de retard a l ouverture : Apollo cree l ecran virtuel et lance
# Steam dans cet intervalle, et une mise a niveau de paquet qui se dispute la
# machine avec ce demarrage-la se verrait dans le flux.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User 'Administrator'
$logonTrigger.Delay = 'PT2M'
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '04:00'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $Script -Run"
)
# Interactive, comme l agent de l etape 40 : la tache ne porte aucun mot de
# passe, et l ouverture de session automatique permanente garantit que
# l utilisateur present est Administrator.
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($logonTrigger, $dailyTrigger) -Principal $principal `
    -Settings $settings | Out-Null
# Relire plutot que croire Register-ScheduledTask : une strategie de groupe
# peut refuser un declencheur sans que l enregistrement echoue, et la console
# n aurait alors plus rien pour se rattraper.
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
# Les deux cas separement : le -or court-circuite sur $null, et le compte
# s interpolait alors en vide - << (trouve ) >>, qui se lit comme un message
# casse plutot que comme l absence qu il decrit.
if (-not $registered) {
    throw "la tache $TaskName n existe pas apres son enregistrement : sans elle Services de jeu ne serait jamais mis a jour, ce qui est l objet meme de cette etape"
}
if ($registered.Triggers.Count -lt 2) {
    throw "la tache $TaskName n a retenu que $($registered.Triggers.Count) declencheur(s) sur 2 : une strategie de groupe peut en refuser un sans faire echouer l enregistrement, et la console n aurait plus rien pour se rattraper"
}
Write-Host "tache $TaskName posee (ouverture de session +2 min, et 4 h chaque jour)"

# --- L installation elle-meme, par le MEME chemin de code que la tache : ce
# qui est exerce ici est litteralement ce qui tournera demain. -Force saute
# l etranglement, qui n a pas de sens au premier passage.
if (Update-GamingServices -Force) {
    $version = Get-GamingServicesVersion
    # Le paquet installe ne suffit pas : ce que les jeux interrogent, ce sont
    # ses deux services. Un paquet pose dont les services ne demarrent pas est
    # l etat que « Ensure GamingServices is up to date » decrit vraiment.
    $services = @(Get-Service -Name 'GamingServices', 'GamingServicesNet' -ErrorAction SilentlyContinue)
    $stopped = @($services | Where-Object { $_.Status -ne 'Running' })
    if ($services.Count -lt 2) {
        Write-Host "warning: Services de jeu $version est installe mais n a pose que $($services.Count) service(s) sur 2 ; $GamingDir\refresh.log et D:\state\gaming-services.txt gardent la trace"
    }
    elseif ($stopped.Count -gt 0) {
        Write-Host "warning: Services de jeu $version est installe, $($stopped.Name -join ', ') ne tourne pas encore ; il demarre automatiquement, mais le verifier si un jeu GDK refuse de se lancer"
    }
    else {
        Write-Host "Services de jeu $version installe, GamingServices et GamingServicesNet tournent"
    }
}
else {
    Write-Host "warning: Services de jeu n a pas pu etre installe maintenant (voir $GamingDir\refresh.log et D:\state\gaming-services.txt). Le provisionnement CONTINUE volontairement : Steam n en depend pas, et la tache $TaskName rejouera a la prochaine ouverture de session."
}
