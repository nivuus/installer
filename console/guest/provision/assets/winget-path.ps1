<#
    Resolve-Winget : ou est winget.exe, vraiment.

    Trois consommateurs le demandent - l etape 33 qui vient de l installer,
    l etape 34 qui s en sert, et le rafraichissement qui tourne a chaque
    ouverture de session, longtemps apres que l ISO de charge utile a disparu.
    D ou un asset, copie sur C: par l etape 33, plutot qu une fonction
    dupliquee trois fois.

    On ne passe PAS par l alias %LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe.
    C est un point de reanalyse par utilisateur, pose a l ouverture de session :
    il n existe pas encore dans la session qui vient de l installer, il est
    absent des taches planifiees qui tournent avant que le profil soit complet,
    et le PATH d un processus DEJA lance ne le voit pas apparaitre. Le chemin
    d installation du paquet, lui, existe des que le paquet existe.
#>

function Resolve-Winget {
    <#
        Rend le chemin complet de winget.exe, ou leve en disant laquelle des
        deux situations distinctes se presente : le paquet n est pas la, ou
        il est la mais vide. La seconde est le mode d echec SILENCIEUX de
        l App Installer - un bundle pose sans ses frameworks s installe sans
        erreur et ne depose aucun executable - et la confondre avec la
        premiere enverrait chercher un probleme de deploiement la ou il y a
        un probleme de dependances.
    #>
    # [version], et pas le tri par defaut : Get-AppxPackage rend un Version
    # de type System.String (mesure 2026-09-04 sur l invite), et un tri de
    # texte place << 1.9.0.0 >> au-dessus de << 1.29.290.0 >>. On retiendrait
    # donc l ANCIENNE des que deux versions cohabitent - le cas meme que
    # << -Descending | Select -First 1 >> existe pour traiter.
    $pkg = Get-AppxPackage -Name 'Microsoft.DesktopAppInstaller' -ErrorAction SilentlyContinue |
           Sort-Object { [version]$_.Version } -Descending | Select-Object -First 1
    if (-not $pkg) {
        throw "le paquet Microsoft.DesktopAppInstaller n est pas installe pour cet utilisateur : winget n a jamais ete pose, ou l a ete dans une autre session (voir l etape 33)"
    }
    $exe = Join-Path $pkg.InstallLocation 'winget.exe'
    if (-not (Test-Path $exe)) {
        throw "$($pkg.PackageFullName) est installe mais ne contient pas winget.exe : le bundle a ete pose sans ses frameworks x64 (VCLibs Desktop, Windows App Runtime), ce que l App Installer fait SANS erreur. Reposer drivers\winget\deps\*.appx avant le bundle."
    }
    return $exe
}
