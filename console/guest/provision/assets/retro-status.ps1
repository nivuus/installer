<#
    Le temoin durable de l etape 32 (retrogaming), sur le volume persistant.

    Pourquoi il existe : l etape AVERTIT SANS BLOQUER quand « retro install »
    n installe qu une partie des emulateurs - une URL morte chez un editeur ne
    doit pas emporter le provisionnement d une console dont le streaming, lui,
    fonctionne. Le provisionnement est donc declare complet et la console
    prete ; sans temoin, le seul recit de l echec vivrait dans la
    transcription, sur C:, que la reconstruction suivante efface. Il va donc
    sur D:, le seul volume qui traverse une reinstallation - exactement la
    raison que le bloc de rattrapage de run-all.ps1 donne pour son
    PROVISION.failed.

    Qui le lit : l hote, AVANT de synchroniser la bibliotheque. « retro scan »
    fabrique les chemins d executables depuis le manifeste sans verifier qu ils
    existent, et la garde de racine les accepte puisqu ils sont bien sous la
    racine d emulation : une installation partielle peuplerait donc la
    bibliotheque Steam d entrees qui ne demarrent pas.

    Le contrat est ECRIT DANS LE FICHIER PRODUIT ($RetroStatusHeader) : qui
    l ouvre a la liste des status sous les yeux sans avoir a retrouver ce
    script-ci.

    Dot-source this file, then call: Write-RetroStatus <status> [<lignes>]
    (il lit $EmulationRoot dans la portee de son appelant, 32-retro.ps1).
#>

$RetroStatusFile = 'D:\state\retro.status'

# L identifiant du PASSAGE de provisionnement courant. D: survit aux
# reconstructions : sans lui, un « status=ok » laisse par une installation
# ANTERIEURE affirmerait que tout va bien pour le passage en cours, et le
# lecteur n aurait aucun moyen de le contester - un temoin perime qui dit
# « ok » est pire que pas de temoin du tout. run-all.ps1 horodate chaque
# passage dans provision.started, sur C:, regeneree a chaque reconstruction :
# deux passages ne peuvent pas porter le meme.
$RetroRunFile = 'C:\nivuus\state\provision.started'
$RetroRunId = if (Test-Path $RetroRunFile) {
    (Get-Content -Path $RetroRunFile -Raw).Trim()
} else { 'hors-run-all' }

# Le contrat, en tete du temoin. Les lignes de commentaire commencent par '#' ;
# le reste est 'cle=valeur', puis 'report:' suivi du rapport brut. ASCII strict
# comme toute chaine executee de ce depot : PowerShell 5.1 lit un .ps1 en ANSI.
$RetroStatusHeader = @(
    '# Temoin de l etape 32 (retrogaming), ecrit par 32-retro.ps1 sur le',
    '# volume persistant. A lire AVANT toute synchronisation de la',
    '# bibliotheque : une installation partielle peuple Steam d entrees qui ne',
    '# demarrent pas, parce que le scan fabrique les chemins depuis le',
    '# manifeste sans verifier qu ils existent.',
    '#',
    '# status :',
    '#   started              l etape a commence ; rien n est encore installe',
    '#   disabled             option decochee : rien n est installe, c est voulu',
    '#   interrupted          l etape a leve avant l installation (voir error=)',
    '#   ok                   tous les emulateurs du manifeste sont installes',
    '#   partial              au moins un manque ; report: nomme lesquels',
    '#   manifest-unreadable  manifeste illisible : rien n a ete installe',
    '#',
    '# Seul "ok" autorise la synchronisation. "disabled" dit qu il n y a pas de',
    '# retrogaming sur cette console, ce qui n est pas une panne. Tout autre',
    '# status, "started" compris, veut dire que l etape n a pas abouti.',
    '#',
    '# run= identifie le passage de provisionnement : le contenu de',
    '# C:\nivuus\state\provision.started, que run-all.ps1 horodate a chaque',
    '# lancement. Un run different de celui du passage courant signale un',
    '# temoin d une installation ANTERIEURE, conserve par le volume : son',
    '# status ne dit alors rien de ce qui vient de se passer.'
)

function Write-RetroStatus([string]$State, [string[]]$Report) {
    # Le dernier status ecrit, pour que le rattrapage de 32-retro.ps1 sache
    # s il a quelque chose a dire : un status precis deja pose (par exemple
    # « manifest-unreadable », qui leve ensuite) ne doit pas etre remplace par
    # un « interrupted » generique en remontant.
    $script:RetroStatusLast = $State
    $lines = $RetroStatusHeader + @(
        "run=$RetroRunId", "status=$State", "when=$(Get-Date -Format o)",
        "emulation_root=$EmulationRoot", 'report:') + $Report
    # UTF-8 (le rapport est accentue), et SANS le BOM que Set-Content poserait.
    [System.IO.File]::WriteAllLines($RetroStatusFile, [string[]]$lines)
}
