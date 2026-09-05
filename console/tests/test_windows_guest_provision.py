#!/usr/bin/env python3
"""Static checks on the guest provisioning scripts.

PowerShell cannot run here, so these are the invariants a Linux host can still
enforce: ordering, no hardcoded drive letters, and the session-1 contract.
Run: python3 console/tests/test_windows_guest_provision.py
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GUEST = REPO / "console" / "guest"
PROVISION = GUEST / "provision"
PROBE = GUEST / "probe"

STAGES = ["00-bootstrap.ps1", "10-nvidia.ps1", "15-virtio.ps1", "20-disk.ps1",
          "25-apollo.ps1", "30-steam.ps1", "32-retro.ps1", "33-winget.ps1",
          "34-gaming-services.ps1", "35-shares.ps1",
          "40-agent.ps1", "45-debloat.ps1", "50-power.ps1",
          "55-updates.ps1", "99-marker.ps1"]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def code_only(text):
    """Strip PowerShell comments, block ones included.

    _shell_code (below, pre-existing) only drops lines that START with '#'
    or '<#', which leaves every interior line of a <# ... #> block comment
    counted as "code" - a docstring paragraph can therefore satisfy a check
    meant to verify actual behaviour. This strips the whole <# ... #> span
    first (DOTALL, non-greedy: this repo never nests block comments), then
    drops single-line '#' comments the same way.
    """
    without_blocks = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(ln for ln in without_blocks.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#"))


for name in STAGES + ["run-all.ps1"]:
    check(f"{name} exists", (PROVISION / name).is_file(), True)
for name in ["AdvancedColor.cs", "advanced-color.ps1"]:
    check(f"{name} exists", (PROBE / name).is_file(), True)

# hiberfil.sys carries Hidden + System, and PowerShell's FileSystem provider
# filters those out: Test-Path returns $false on a 6.8 GB file that is plainly
# there, and it has no -Force to override that. Measured on the guest on
# 2026-08-25 - it made 99-marker.ps1 refuse an appliance that hibernates
# perfectly, an hour into provisioning. [System.IO.File]::Exists sees it.
for stage in ("50-power.ps1", "99-marker.ps1"):
    body = (PROVISION / stage).read_text(encoding="utf-8")
    hiberfil_lines = [ln for ln in body.splitlines()
                      if "hiberfil.sys" in ln and not ln.lstrip().startswith("#")]
    check(f"{stage} tests hiberfil.sys at all", bool(hiberfil_lines), True)
    check(f"{stage} never uses Test-Path on hiberfil.sys (Hidden+System)",
          any("Test-Path" in ln for ln in hiberfil_lines), False)

# --- Etapes 33 et 34 : winget hors ligne, puis « Services de jeu » du Store.
#
# Ce que ces controles gardent n est pas la syntaxe PowerShell (rien ici ne
# peut l executer) mais les quatre constats qui ont coute une mesure sur
# l invite de production le 2026-08-30, et qu une relecture innocente
# defairait sans s en apercevoir.
_winget = (PROVISION / "33-winget.ps1").read_text(encoding="utf-8")
_winget_code = code_only(_winget)
_gaming = (PROVISION / "34-gaming-services.ps1").read_text(encoding="utf-8")
_gaming_code = code_only(_gaming)
_gs_asset = (PROVISION / "assets" / "gaming-services.ps1").read_text(encoding="utf-8")
_gs_code = code_only(_gs_asset)
# La pile Xbox est sortie dans son propre asset le 2026-08-30 : gaming-services
# garde l etranglement, le journal et le temoin, xbox-stack porte les paquets,
# les services et le SEUL appel a winget.
_xbox_asset = (PROVISION / "assets" / "xbox-stack.ps1").read_text(encoding="utf-8")
_xbox_code = code_only(_xbox_asset)
_wpath_code = code_only(
    (PROVISION / "assets" / "winget-path.ps1").read_text(encoding="utf-8"))

# 1. Les frameworks AVANT le bundle. Un bundle App Installer pose sans eux
# s installe SANS erreur et ne depose aucun winget.exe : inverser ces deux
# lignes ne casse rien de visible, et l etape 34 echouerait ensuite en
# parlant du Store.
_i_deps = _winget_code.find("foreach ($dep in $deps)")
_i_bundle = _winget_code.find("Add-AppxPackage -Path $bundle")
check("33-winget pose les frameworks avant le bundle",
      _i_deps >= 0 and _i_bundle > _i_deps, True)
check("33-winget refuse un payload sans framework au lieu de poser le bundle seul",
      "aucun framework" in _winget_code, True)

# 2 bis. L INSCRIPTION DANS L IMAGE EST AUSSI REJOUABLE que la pose. Le
# commentaire qui justifie le try/catch du bundle - reposer une version deja
# presente rend une erreur qui n en est pas une - vaut mot pour mot pour
# Add-AppxProvisionedPackage, et l en-tete de l etape dit qu on la rejoue a la
# main pour diagnostiquer. Non protegee, elle mourait AVANT la verification
# finale, qui est justement ce qui devait trancher. Et run-all.ps1 n ecrit le
# temoin .done qu au retour de l etape : un echec plus bas ramene ici au
# passage suivant.
check("l inscription dans l image est protegee comme la pose du bundle",
      "try { Add-AppxProvisionedPackage" in _winget_code, True)

# 2. La licence hors ligne. Sans elle Add-AppxProvisionedPackage refuse, et
# winget repartirait avec le premier profil recree.
check("33-winget inscrit winget dans l image, pas seulement dans le profil",
      "Add-AppxProvisionedPackage -Online" in _winget_code, True)
check("33-winget passe la licence hors ligne", "-LicensePath $license" in _winget_code, True)

# 3. La verification LANCE winget. Constater le paquet est precisement l etat
# qu un bundle sans frameworks produit : seul un appel qui rend 0 distingue
# « installe » de « utilisable ».
check("33-winget lance winget au lieu de constater sa presence",
      "--version" in _winget_code and "$LASTEXITCODE" in _winget_code, True)
# Et l alias par utilisateur n est jamais le chemin retenu : il n existe pas
# encore dans la session qui vient de l installer.
check("le chemin de winget vient du paquet, pas de l alias WindowsApps",
      "WindowsApps" in _wpath_code, False)
check("Resolve-Winget distingue le paquet absent du paquet vide",
      _wpath_code.count("throw"), 2)
# LE TRI DES VERSIONS EST UN TRI DE TEXTE, et c est mesure : sur l invite,
# Get-AppxPackage rend un Version de type System.String, et
#   @('1.9.0.0','1.29.290.0') | Sort-Object -Descending
# donne « 1.9.0.0 > 1.29.290.0 ». Choisir « la plus recente » ainsi retient donc
# l ANCIENNE des que deux versions cohabitent pour l utilisateur - ce qui est
# exactement le cas que « -Descending | Select -First 1 » existe pour traiter.
for _label, _code in (("winget-path.ps1", _wpath_code),
                      ("gaming-services.ps1", _gs_code)):
    check(f"{_label} trie les versions en versions, pas en texte",
          "Sort-Object -Property Version" in _code, False)
    check(f"... {_label} passe par [version]", "[version]" in _code, True)

# 4. L etape 34 NE LEVE PAS quand le Store est injoignable : un Store en
# panne pendant l heure du provisionnement ne doit pas emporter une console
# qui diffuse Steam, d autant que la tache rejouera le meme geste demain.
_i_update = _gaming_code.find("Update-GamingServices -Force")
check("34 appelle bien l installation", _i_update >= 0, True)
check("34 ne fait pas echouer le provisionnement sur un Store injoignable",
      "throw" in _gaming_code[_i_update:], False)
# La tache est posee AVANT l installation, sans quoi un echec l emporterait
# avec lui - et c est elle, pas l installation d aujourd hui, qui repare.
_i_task = _gaming_code.find("Register-ScheduledTask")
check("34 pose la tache avant de tenter l installation",
      _i_task >= 0 and _i_task < _i_update, True)
check("34 relit les declencheurs au lieu de croire l enregistrement",
      "$registered.Triggers.Count" in _gaming_code, True)
# ... et le message doit rester lisible quand la tache est ABSENTE : le -or
# court-circuite sur $null, et « $($registered.Triggers.Count) » s interpole
# alors en vide - « (trouve ) », qui se lit comme un bug du message plutot que
# comme l absence qu il decrit.
check("... et le dit lisiblement quand la tache est absente",
      "if (-not $registered)" in _gaming_code, True)

# Le script du rafraichissement tourne longtemps apres que l ISO de charge
# utile a disparu : il est lance depuis C:, jamais depuis $PayloadRoot.
check("34 lance la tache depuis C:, pas depuis la charge utile",
      "-File $Script -Run" in _gaming_code and "$PayloadRoot" not in
      _gaming_code[_i_task:_i_task + 400], True)
check("34 copie le script sur C: avant de l enregistrer",
      _gaming_code.find("Copy-Item") < _i_task, True)

# L identifiant Store, tel que l URL du Store le porte. Ecrit une seule fois.
check("l identifiant Store de Services de jeu est celui demande",
      "9MWPM2CQNLHN" in _gs_code, True)
check("... et n est pas recopie dans les etapes", "9MWPM2CQNLHN" in _gaming_code, False)

# « Aucune mise a niveau disponible » (0x8A15002B) est le cas NORMAL, a chaque
# ouverture de session sauf le jour d une publication. Le traiter comme un
# echec ferait crier la console tous les jours.
check("le code « deja a jour » est accepte, pas traite en echec",
      "-1978335189" in _gs_code, True)
# UNE seule commande, et c est le fond de l affaire : sur un paquet deja
# present `winget install` bascule de lui-meme en mise a niveau. Deux chemins
# de code separes - un pour poser, un pour mettre a jour - se mettraient a
# diverger, et c est celui que personne ne regarde qui tournerait tous les
# jours.
check("l installation vaut mise a jour : une seule commande winget",
      _xbox_code.count("install --id"), 1)
check("... et elle vise la source msstore, seule accessible depuis LTSC",
      "--source msstore" in _xbox_code, True)
# LA CHAINE ENTIERE, pas seulement Services de jeu : un jeu GDK ne dit jamais
# lequel de ces composants lui manque, il reste fige sans message.
for _pkg in ("9MWPM2CQNLHN", "9WZDNCRD1HKW", "9MV0B5HZVK9Z", "9WZDNCRFJBMP"):
    check(f"xbox-stack.ps1 pose le paquet Store {_pkg}", _pkg in _xbox_code, True)
# Les services d ouverture de session sont livres en Manual par LTSC et leurs
# declencheurs a la demande ne partent pas : sans Automatic ils sont Stopped a
# chaque redemarrage et la connexion Xbox echoue.
check("xbox-stack.ps1 met les services Xbox en demarrage automatique",
      "Automatic" in _xbox_code and "wlidsvc" in _xbox_code, True)
# ClipSVC est PROTEGE : Set-Service y rend « Access is denied » meme en
# administrateur, alors qu il se DEMARRE tres bien. Le traiter comme les autres
# ferait echouer l etape pour un service qui, lui, repond.
check("... et traite ClipSVC a part, sans tenter de le reconfigurer",
      "ClipSVC" in _xbox_code and "Set-Service -Name 'ClipSVC'" not in _xbox_code, True)
# XblGameSave EST TRAITE COMME ClipSVC : demarre, jamais reconfigure. Mesure du
# 2026-09-04 sur l invite, quatre passages : Set-Service NE LEVE PAS et le
# demarrage repart a 'Manual' (registre Start = 3) trois fois sur quatre. Ce
# n est pas une panne - `sc qtriggerinfo XblGameSave` montre un declencheur
# NETWORK EVENT / RPC INTERFACE EVENT, donc Windows regere ce service, et le
# forcer revient a se battre contre le systeme pour obtenir un temoin
# DEFINITIVEMENT rouge. Or un temoin toujours rouge cesse d etre lu, et c est le
# prochain VRAI defaut qui passerait inapercu. 'Manual' + declencheur est donc
# l etat ATTENDU ; ce qui compte est qu il soit Running.
check("XblGameSave n est plus force en Automatic",
      "'XblGameSave'" in _xbox_code
      and "$XboxServices = @('wlidsvc', 'XblAuthManager', 'XboxNetApiSvc', 'LicenseManager')"
      in _xbox_code, True)
check("... il est demarre sans etre reconfigure, comme ClipSVC",
      "$XboxServicesDemarresSeulement" in _xbox_code, True)
check("... et ces deux-la sont verifies Running, pas seulement demarres",
      "'Running'" in _xbox_code, True)
# La ligne de succes ne doit plus dire << en Automatic >> de SIX services quand
# deux ne le sont pas et ne doivent pas l etre : c est le meme faux oracle, en
# plus discret, que la constante qu elle remplace.
check("... et la ligne de succes ne dit plus Automatic de tout le monde",
      "en Automatic et demarres (relu)" in _xbox_code, False)

# RELIRE PLUTOT QUE CROIRE, jusque sur les services. MESURE le 2026-09-04 sur
# l invite de production : le journal a ecrit « services Xbox en Automatic et
# demarres : wlidsvc, XblAuthManager, XboxNetApiSvc, XblGameSave,
# LicenseManager + ClipSVC » alors que XblGameSave etait reste en 'Manual'
# (HKLM\SYSTEM\CurrentControlSet\Services\XblGameSave\Start = 3). Set-Service
# n avait pas leve, et la ligne du journal est une CONSTANTE - la liste voulue,
# jamais ce qui a pris. Un reglage qui ne prend pas et un reglage qui prend
# donnaient donc exactement le meme message, ce qui est la definition d une
# panne muette. Le reste du depot relit deja ses ecritures (Winlogon Shell dans
# 30-steam.ps1, les declencheurs dans 34) ; ici aussi.
check("les services Xbox sont RELUS apres avoir ete regles",
      "Get-Service -Name $name" in _xbox_code
      and "StartType" in _xbox_code, True)
check("... et le journal nomme ce qui a pris, pas la liste voulue",
      "$XboxServices -join ', '" in _xbox_code, False)

# Le piege du dot-source : l etape 34 dot-source cet asset, et un `exit` en
# dot-source quitte l APPELANT - il emporterait le provisionnement.
_i_run = _gs_code.find("if ($Run)")
check("l asset ne sort du processus que sous -Run",
      _i_run >= 0 and all(_gs_code.find(f"exit {c}") > _i_run for c in ("0", "1")), True)

# L etranglement, et la panne muette qu il a reellement eue le 2026-08-30 :
# [datetime]::TryParse sur une variable non typee echoue a resoudre sa
# surcharge, l erreur n est pas terminante, et le controle se contentait de ne
# JAMAIS s appliquer. Rien ne le montrait sauf le journal - deux controles
# complets a deux minutes d intervalle la ou il devait y en avoir un.
check("l horodatage n est pas relu par TryParse (surcharge non resolue)",
      "TryParse" in _gs_code, False)
check("il est relu en culture invariante, comme il est ecrit (-Format o)",
      "InvariantCulture" in _gs_code and "RoundtripKind" in _gs_code, True)
check("un horodatage illisible vaut « pas d horodatage », jamais une erreur",
      "catch { $last = $null }" in _gs_code, True)

# LE CONTRAT « NE LEVE PAS » NE SURVIT PAS AU DOT-SOURCE, si l asset ne le
# retablit pas lui-meme. $ErrorActionPreference est a portee DYNAMIQUE : l etape
# 34 le met a 'Stop' (comme toutes les etapes), et dot-source ensuite cet asset,
# qui herite donc de 'Stop'. Deux consequences, et aucune ne se voit en lisant
# l asset seul :
#   - `& $winget ... 2>&1` : sous 'Stop', la MOINDRE ligne d erreur native de
#     winget devient une exception terminante (NativeCommandError) au lieu
#     d une ligne de $out ;
#   - Add-Content du journal : un disque plein leve au lieu d ecrire.
# Dans les deux cas l exception traverse Update-GamingServices - dont l en-tete
# promet « NE LEVE PAS » - et emporte l etape 34, qui promet de ne jamais faire
# echouer le provisionnement. Et comme la tache planifiee, elle, tourne sous le
# 'Continue' par defaut, les DEUX chemins ne se comportaient pas pareil en
# panne : exactement ce que l en-tete de l asset dit qu ils font.
check("l asset retablit lui-meme le mode non terminant qu il promet",
      "$ErrorActionPreference = 'Continue'" in _gs_code, True)
# Dans la FONCTION, pas au niveau du fichier : un dot-source appliquerait le
# 'Continue' au reste de l etape 34, qui compte sur 'Stop' pour ses copies.
_i_upd = _gs_code.find("function Update-GamingServices")
check("... dans la fonction, sans desarmer le 'Stop' de l etape qui l appelle",
      _i_upd >= 0
      and _gs_code.find("$ErrorActionPreference = 'Continue'") > _i_upd, True)

# L ETRANGLEMENT NE COUVRE QUE LE STORE, et les services passent AVANT lui.
# Deux raisons mesurees le 2026-09-04, et elles vont dans le meme sens :
#   - Set-Service est local, instantane et gratuit ; c est le Store qui est
#     lent, distant et qu il ne faut pas interroger a chaque ouverture de
#     session. Etrangler les deux ensemble etrangle la moitie qui ne coute
#     rien.
#   - le demarrage de XblGameSave a ete VU repartir en 'Manual' tout seul entre
#     deux passages ; le seul geste qui repare une derive pareille est celui
#     qu on rejoue a chaque ouverture de session, donc jamais etrangle.
# Et l horodatage date desormais le controle du STORE, sans quoi un service qui
# refuse de tenir empechait de l ecrire A JAMAIS : les quatre appels winget
# repartaient a chaque ouverture de session ET tous les jours, indefiniment -
# exactement ce que l etranglement existe pour eviter.
_i_svc = _gs_code.find("Set-XboxServicesAutomatic")
_i_throttle = _gs_code.find("-lt $MinHoursBetweenChecks")
check("les services sont reposes avant l etranglement, donc a chaque passage",
      _i_svc >= 0 and _i_throttle >= 0 and _i_svc < _i_throttle, True)
check("l horodatage est ecrit des que les paquets sont bons",
      "if ($failed.Count -eq 0) {" in _gs_code, True)

# Le temoin durable : l etape ne levant pas, ce fichier sur le volume qui
# survit aux reconstructions est la SEULE trace persistante de l issue.
check("l asset ecrit un temoin sur le volume persistant",
      "D:\\state\\gaming-services.txt" in _gs_code, True)
check("un temoin impossible (D: non monte) n est pas un echec",
      "if (-not (Test-Path 'D:\\state')) { return }" in _gs_code, True)


# Recursive: provision/assets/*.ps1 (run-agent.ps1, steam-session.ps1) are
# shipped and executed inside the guest just like the numbered stages, and
# must not escape the 200-line, hardcoded-drive-letter and session-0 guards
# below just because they live one directory deeper.
texts = {p.name: p.read_text(encoding="utf-8")
         for p in list(PROVISION.rglob("*")) + list(PROBE.iterdir()) if p.is_file()}

for name, text in texts.items():
    check(f"{name} under 200 lines", len(text.splitlines()) <= 200, True)
    # Every drive letter must come from the marker scan, never be assumed.
    check(f"{name} has no hardcoded payload drive",
          bool(re.search(r"[D-Z]:\\nivuus", text)), False)
    # Session 0 is blind to the display; nothing may migrate back to it.
    check(f"{name} never mentions SetupComplete", "SetupComplete" in text, False)

runall = texts["run-all.ps1"]
positions = [runall.find(s) for s in STAGES]
check("run-all lists every stage", all(p >= 0 for p in positions), True)
check("run-all keeps the stages ordered", positions, sorted(positions))
check("run-all skips stages already done", ".done" in runall, True)
check("run-all takes PayloadRoot", "$PayloadRoot" in runall, True)
check("run-all mentions reboot.requested", "reboot.requested" in runall, True)
check("run-all mentions Restart-Computer", "Restart-Computer" in runall, True)
# The .done file must be written before the reboot sentinel is consumed, or a
# stage that needs a reboot would rerun from scratch on every resume.
check("run-all writes .done before consuming the reboot sentinel",
      runall.find("Set-Content -Path $done") < runall.find("reboot.requested"), True)
# FIX 14 (final review): a failed stage must leave a diagnosable trace
# instead of nothing - no marker, closed 5985, VNC as the only recourse.
check("run-all writes PROVISION.failed on a failed stage",
      "PROVISION.failed" in runall, True)
check("run-all mirrors the failure marker onto D: when it is mounted",
      "D:\\state\\PROVISION.failed" in runall, True)
# The failure marker must never be mistaken for readiness: 5985 only opens
# from 99-marker.ps1, never from this catch block.
check("run-all's catch block does not open 5985",
      "Enable-NetFirewallRule" in runall[runall.find("catch"):runall.find("finally")],
      False)

boot = texts["00-bootstrap.ps1"]
check("bootstrap enables PSRemoting", "Enable-PSRemoting" in boot, True)
# WinRM stays REACHABLE from here on. It used to be firewalled until the end so
# that "5985 reachable" meant "provisioning finished" - a proxy that failed in
# the only case worth catching, since a stage that throws never reaches
# 99-marker and the rule never opens. Readiness is the marker's job now.
check("bootstrap leaves 5985 reachable",
      any("Disable-NetFirewallRule" in ln for ln in boot.splitlines()
          if not ln.lstrip().startswith("#")), False)
check("bootstrap writes the resume script", "resume.cmd" in boot, True)
check("bootstrap registers the resume entry", "CurrentVersion\\Run" in boot, True)
check("resume script rescans drives", "%%d" in boot, True)

marker = texts["99-marker.ps1"]
# The version lives in two languages; a silent drift would make the host accept
# a guest provisioned by an older payload.
sys.path.insert(0, str(GUEST))
import payload  # noqa: E402
check("marker version matches payload.PROVISION_VERSION",
      f"provision_version={payload.PROVISION_VERSION}" in marker, True)
# Le marqueur n OUVRE plus 5985 : la regle est ouverte depuis l etage 00 et le
# reste. Il se contente de verifier qu elle l est toujours, au cas ou une
# strategie de groupe ou un durcissement l aurait refermee en cours de route.
check("marker re-verifies the WinRM rule", "Enable-NetFirewallRule" in marker, True)
check("marker clears the resume entry", "Remove-ItemProperty" in marker, True)
check("marker writes PROVISION.done", "PROVISION.done" in marker, True)
# Le marqueur EST le signal de disponibilite - le port ne l a jamais ete, il
# n en etait qu un proxy. Il doit donc etre ecrit avant le dernier controle de
# la regle, pour qu un appelant qui voit la regle saine trouve aussi le
# marqueur.
check("marker writes PROVISION.done before its firewall re-check",
      marker.find("PROVISION.done") < marker.find("Enable-NetFirewallRule"), True)
# 10-nvidia.ps1 may have deferred its device check to survive a driver reboot;
# this is where that deferred verification must land, before port 5985 opens.
check("marker verifies the NVIDIA device", "Get-PnpDevice" in marker, True)
check("marker verifies the NVIDIA device before opening 5985",
      marker.find("Get-PnpDevice") < marker.find("Enable-NetFirewallRule"), True)

nvidia = texts["10-nvidia.ps1"]
check("nvidia installs silently", "-noreboot" in nvidia, True)
check("nvidia verifies the device afterwards", "Get-PnpDevice" in nvidia, True)
check("nvidia writes reboot.requested", "reboot.requested" in nvidia, True)
# The sentinel must only be written on the "success, reboot required" path
# (ExitCode 1), never unconditionally.
check("nvidia requests reboot only on the ExitCode -eq 1 path",
      nvidia.find("-eq 1") < nvidia.find("reboot.requested"), True)

cs = texts["AdvancedColor.cs"]
for symbol in ("GetDisplayConfigBufferSizes", "QueryDisplayConfig",
               "DisplayConfigGetDeviceInfo", "QDC_ONLY_ACTIVE_PATHS"):
    check(f"probe uses {symbol}", symbol in cs, True)
check("probe reports bits per colour", "bitsPerColorChannel" in cs, True)
check("probe output matches the reference format",
      "target={0} rc={1} supported={2} enabled={3} bpc={4} adapterLuid={5}:{6} "
      "outputTechnology={7} namerc={8} name={9}" in cs, True)
# Every line must self-identify the display it measured, so three possibly
# concurrent displays (emulated VGA, GPU dummy plug, SudoVDA) can be told apart.
check("probe queries the target device name (self-identification)",
      "INFO_TYPE_TARGET_DEVICE_NAME" in cs, True)
# Pin the value, not just the symbol's presence: a wrong info type still
# compiles and still returns rc=0, but silently names the wrong display.
check("target device name info type is 2 (GET_TARGET_NAME)",
      "INFO_TYPE_TARGET_DEVICE_NAME = 2;" in cs, True)

# --- Sub-project B.
# R1: the version lives in two languages and must move in one step.
# B3 -> B4 le 2026-09-04. LA VERSION DOIT BOUGER QUAND LA SEQUENCE BOUGE, et
# elle vient de bouger de deux etapes (33-winget, 34-gaming-services), d un
# shell (le kiosque a laisse la place a explorer.exe) et de trois assets. Sans
# ce relevement, le marqueur d un invite provisionne AVANT tout cela relit
# « B3 » et guest-ready-watch.py le declare a jour : la console qui n a jamais
# vu winget passe pour une console qui l a. MESURE le 2026-09-04 sur l invite
# de production, et c est exactement ce piege en action - son
# C:\nivuus\state\PROVISION.done porte encore provision_version=B1, du
# 2026-08-26, alors que le depot etait deja en B3.
check("provision version is B4", payload.PROVISION_VERSION, "B4")
check("the standalone SudoVDA stage is gone",
      (PROVISION / "20-sudovda.ps1").exists(), False)

marker = texts["99-marker.ps1"]
# A kept autologon is the whole point of the appliance: Apollo captures an
# interactive desktop and the agent lives in session 1. A had to disable it.
check("marker no longer disables autologon", "AutoAdminLogon" in marker, False)
check("marker verifies the agent session", "agent-session.txt" in marker, True)
check("marker verifies Apollo runs", "ApolloService" in marker, True)
check("marker verifies Steam", "steam.exe" in marker, True)
# FIX 1 (review round 1): the closing stage is the LAST place a loose match
# belongs - it must match the same precise hardware ID as 25-apollo.ps1, not
# a ROOT\DISPLAY\* wildcard that would accept any root-enumerated display.
check("marker matches the virtual display by its precise hardware ID",
      "Root\\SudoMaker\\SudoVDA" in marker, True)
check("marker no longer uses the loose ROOT\\DISPLAY\\* wildcard",
      "ROOT\\DISPLAY\\*" in marker, False)
# FIX 2 (review round 1): the session file only proves the agent ran once, at
# stage-40 time. The durable property is that it will run again - a
# registered, enabled scheduled task - not that agent.exe is alive right now
# (it legitimately exits when it cannot reach its signalling server, which is
# not part of this appliance, so a live-process check would be flaky).
check("marker verifies the agent scheduled task is registered",
      "Get-ScheduledTask" in marker, True)
check("marker verifies the agent task is not disabled",
      "-eq 'Disabled'" in marker, True)
# 5985 opens last, after every check: the host reads a reachable 5985 as
# "provisioned", and a premature open already lied once.
check("marker opens 5985 last",
      marker.rfind("Enable-NetFirewallRule") > marker.rfind("throw"), True)

power = texts["50-power.ps1"]
check("power stage enables permanent autologon",
      "AutoAdminLogon" in power and "AutoLogonCount" in power, True)
check("power stage enables hibernation", "/hibernate on" in power, True)

agent = texts["40-agent.ps1"]
check("agent runs interactively", "LogonType Interactive" in agent, True)
check("agent task carries no password", "-Password" in agent, False)

steam = texts["30-steam.ps1"]
check("Steam installs onto D:", "/D=$SteamDir" in steam, True)

apollo_stage = texts["25-apollo.ps1"]
apollo_junction = texts["apollo-junction.ps1"]
# The junction maneuver itself was split into provision/assets/
# apollo-junction.ps1 to keep 25-apollo.ps1 under 200 lines; the stage still
# dot-sources and calls it.
check("Apollo config is junctioned", "mklink /J" in apollo_junction, True)
check("25-apollo.ps1 dot-sources the junction helper",
      "apollo-junction.ps1" in apollo_stage, True)
check("Apollo install location is read, not assumed",
      "InstallLocation" in apollo_stage, True)
# FIX 3 (review round 1): 20-sudovda.ps1's certificate-trust coverage moved
# here, not away - install.bat seeds the driver's own certificate. Assert the
# behaviour follows the file it moved into, instead of evaporating with the
# file that used to hold it.
check("Apollo runs the bundled SudoVDA installer (certificate trust delegated to it)",
      "install.bat" in apollo_stage, True)

# Task 1 (sub-project C2): 25-apollo.ps1 verified SudoVDA scrupulously by
# hardware ID and said nothing about ViGEmBus, Apollo's virtual gamepad
# driver. Its absence is silent by construction - image and sound work, the
# pad just does nothing - and it is unconditional: it has nothing to do with
# retrogaming, it is what makes ANY gamepad passed through Moonlight work at
# all. The two device checks were split into their own helper (same 200-line
# reason as the junction above) and are verified there.
apollo_drivers = texts["apollo-drivers.ps1"]
# Code with comments stripped: the mechanism checks below must bite on what
# actually runs, not on a hardware-ID string that could just as well be
# sitting in a comment or a Write-Host message while the real lookup was
# swapped for something else (e.g. Get-Service).
apollo_drivers_code = "\n".join(
    ln for ln in apollo_drivers.splitlines()
    if ln.strip() and not ln.lstrip().startswith("#"))
check("25-apollo.ps1 dot-sources the driver-verification helper",
      "apollo-drivers.ps1" in apollo_stage, True)
check("driver helper still checks SudoVDA by its precise hardware ID",
      "Root\\SudoMaker\\SudoVDA" in apollo_drivers, True)
check("driver helper still treats a missing SudoVDA as fatal",
      "throw 'no working SudoVDA device" in apollo_drivers, True)
# The historical Root\ViGEmBus ID was abandoned upstream in 2018 (renamed to
# Nefarius\ViGEmBus\Gen1 alongside the vendor's own rename); a check still
# aimed at the old ID would never match a real install and would warn on
# every single provisioning run, healthy or not - the exact "noise teaches
# the operator to ignore it" trap this file already warns about elsewhere.
check("driver helper checks ViGEmBus by its modern vendor hardware ID",
      "Nefarius\\ViGEmBus\\Gen1" in apollo_drivers, True)
check("driver helper does not still look for the abandoned pre-2018 ID",
      "Root\\ViGEmBus'" in apollo_drivers, False)
# Mechanism check, not a string search: the ViGEmBus lookup must actually go
# through the same hardware-ID function as SudoVDA, on code lines only. A
# mutation that swapped the lookup for a Get-Service call while leaving the
# surrounding comments and warning text untouched must fail these two.
check("ViGEmBus is resolved via the shared hardware-ID lookup",
      "Get-PnpDeviceByHardwareId -HardwareId 'Nefarius\\ViGEmBus\\Gen1'"
      in apollo_drivers_code, True)
check("driver helper never falls back to a service-name check",
      "Get-Service" in apollo_drivers_code, False)
check("driver helper names the consequence of a missing ViGEmBus",
      "no gamepad will work over Moonlight" in apollo_drivers, True)
# Deliberate asymmetry with SudoVDA: a missing display leaves nothing to
# diagnose from, so that stays fatal. A missing ViGEmBus still leaves image,
# sound and WinRM reachable, so the stage warns instead of throwing - see the
# reasoning written into apollo-drivers.ps1 itself.
check("a missing ViGEmBus warns instead of throwing",
      "WARNING: no working ViGEmBus device" in apollo_drivers, True)
check("a missing ViGEmBus does not abort the stage",
      "throw" in apollo_drivers_code.rsplit(
          "Get-PnpDeviceByHardwareId -HardwareId 'Nefarius\\ViGEmBus\\Gen1'", 1)[-1],
      False)

# FIX 11 (final review): cross-check the literals that were only recoupled
# by convention until now - the same guard the PROVISION_VERSION check above
# already applies, extended to the other repeated constants. A future edit
# to only one side would otherwise drift silently.
import autounattend  # noqa: E402
import apollo  # noqa: E402

winrm_exec_text = (GUEST / "winrm_exec.py").read_text(encoding="utf-8")
check("Administrator: 40-agent.ps1 trigger matches autounattend.ADMIN_ACCOUNT",
      f"-User '{autounattend.ADMIN_ACCOUNT}'" in agent, True)
check("Administrator: 40-agent.ps1 principal matches autounattend.ADMIN_ACCOUNT",
      f"-UserId '{autounattend.ADMIN_ACCOUNT}'" in agent, True)
check("Administrator: 50-power.ps1 autologon user matches autounattend.ADMIN_ACCOUNT",
      f"-Value '{autounattend.ADMIN_ACCOUNT}'" in power, True)
check("Administrator: winrm_exec.py default GUEST_USER matches autounattend.ADMIN_ACCOUNT",
      f'"{autounattend.ADMIN_ACCOUNT}"' in winrm_exec_text, True)

check("guacamole-agent: 40-agent.ps1 and 99-marker.ps1 name the same scheduled task",
      "'guacamole-agent'" in agent and "'guacamole-agent'" in marker, True)

check(r"D:\Steam: 30-steam.ps1 matches apollo.STEAM_DIR",
      apollo.STEAM_DIR in steam, True)
check(r"D:\Steam: 99-marker.ps1 matches apollo.STEAM_DIR",
      apollo.STEAM_DIR in marker, True)

for name in ["run-all.ps1", "25-apollo.ps1", "40-agent.ps1", "99-marker.ps1",
             "run-agent.ps1", "steam-launch.ps1", "steam-hold-notice.ps1"]:
    check(rf"C:\nivuus\state: {name} uses the canonical state directory",
          "C:\\nivuus\\state" in texts[name], True)

# Every stage must accept the payload root, or run-all cannot drive it.
for name in STAGES:
    check(f"{name} takes PayloadRoot", "$PayloadRoot" in texts[name], True)

# FIX 4 (review round 1): the converse guard. A stage dropped on disk and
# forgotten in STAGES/run-all's $stages would be silently skipped by
# run-all.ps1 (it only indexes the named array, never globs the directory)
# with nothing anywhere reporting it.
disk_stages = sorted(p.name for p in PROVISION.glob("*.ps1") if p.name != "run-all.ps1")
check("every provision stage on disk is listed in STAGES",
      all(name in STAGES for name in disk_stages), True)

# hiberfil.sys carries Hidden + System, and PowerShell's FileSystem provider
# filters those out: Test-Path returns $false on a 6.8 GB file that is plainly
# there, and it has no -Force to override that. Measured on the guest on
# 2026-08-25 - it made 99-marker.ps1 refuse an appliance that hibernates
# perfectly, an hour into provisioning. [System.IO.File]::Exists sees it.
for stage in ("50-power.ps1", "99-marker.ps1"):
    body = (PROVISION / stage).read_text(encoding="utf-8")
    hiberfil_lines = [ln for ln in body.splitlines()
                      if "hiberfil.sys" in ln and not ln.lstrip().startswith("#")]
    check(f"{stage} tests hiberfil.sys at all", bool(hiberfil_lines), True)
    check(f"{stage} never uses Test-Path on hiberfil.sys (Hidden+System)",
          any("Test-Path" in ln for ln in hiberfil_lines), False)

# Le media de reponses est un CD-ROM : Copy-Item reporte son attribut ReadOnly
# sur la copie, et sunshine.conf/apps.json sont exactement les fichiers que
# l IHM web d Apollo reecrit — geles, elle ne peut plus rien enregistrer.
# Mesure sur l invite le 2026-08-25 : UnauthorizedAccessException a l ecriture.
_apollo = (PROVISION / "25-apollo.ps1").read_text(encoding="utf-8")
check("25-apollo.ps1 retire l attribut ReadOnly herite du media",
      "Set-ItemProperty" in _apollo and "Attributes" in _apollo, True)

_apollo_after = (PROVISION / "25-apollo.ps1").read_text(encoding="utf-8")
# Sans adapter_name, Apollo capture sur le « Microsoft Basic Render Driver »
# (WARP) des lors qu'aucun ecran physique n'est branche sur le GPU — le cas de
# cette appliance. NVENC est alors essaye sur WARP, echoue, et le flux tombe en
# x264 logiciel a 1280x800/1 Hz : tout client abandonne avec error -5. Le nom
# est detecte dans l'invite, jamais ecrit en dur : l'hote de construction ne
# connait que les identifiants PCI, pas le nom commercial DXGI.
check("25-apollo.ps1 epingle l adaptateur de capture",
      "adapter_name" in _apollo_after, True)
check("25-apollo.ps1 detecte le nom du GPU au lieu de le figer",
      "Win32_VideoController" in _apollo_after and
      "NVIDIA GeForce RTX 4070" not in _apollo_after, True)

# Steam est le shell de la session : plus d explorer.exe, donc ni barre des
# taches, ni fond d ecran, ni icones. Le lanceur boucle parce que Steam qui se
# ferme ne doit pas laisser un ecran noir sur une machine sans ecran physique.
_steam = (PROVISION / "30-steam.ps1").read_text(encoding="utf-8")
# EXPLORER.EXE EST LE SHELL depuis le 2026-08-30, et ce test garde la RAISON,
# pas le gout : le kiosque precedent empechait TOUTE activation d application
# UWP (« Class not registered »), or le formulaire de connexion a un compte
# Microsoft en est une - donc l ouverture de session Xbox Live echouait en
# 0x80040154 et tout jeu GDK restait fige sur un ecran noir SANS message.
check("30-steam.ps1 rend explorer.exe shell de session",
      "Winlogon" in _steam and "'explorer.exe'" in _steam, True)
check("... et ne remet pas le kiosque a la place",
      "steam-shell.ps1" in _steam, False)
# LE PIEGE QUI A COUTE L ACCES A LA CONSOLE : sous le kiosque HKCU\...\Run
# n etait jamais execute (seul Explorer traite ces entrees) et la cle Steam de
# SteamSetup y dormait. Avec Explorer elle relance Steam en doublon, Apollo
# trouve Steam deja la, et steam-session.ps1 - dont la sortie ferme la session -
# sort aussitot : « La connexion a ete interrompue » a chaque tentative.
check("30-steam.ps1 retire le demarrage automatique de Steam",
      "CurrentVersion\\Run" in _steam and "Remove-ItemProperty" in _steam, True)
check("... et relit la cle au lieu de croire la suppression",
      "Explorer relancerait Steam en doublon" in _steam, True)
# Les deux taches AtLogOn remplacent ce que le shell faisait : elles sont
# INDEPENDANTES du shell, donc insensibles a ce qu il devient.
check("30-steam.ps1 pose la tache d habillage du bureau",
      "desktop-chrome" in _steam, True)
check("30-steam.ps1 pose la tache d avertissement steam.hold",
      "steam-hold-notice" in _steam, True)
# LA DUREE MAXIMALE NE S ECRIT PAS PAR AFFECTATION, et ce defaut-la emportait
# TOUT le provisionnement. MESURE sur l invite de production le 2026-09-04 :
#   (New-ScheduledTaskSettingsSet).ExecutionTimeLimit.GetType() = System.String
#   sa valeur par defaut = « PT72H », une duree ISO-8601
# La conversion TimeSpan -> « PT5M » est faite par le PARAMETRE du cmdlet, pas
# par la propriete. Une affectation y depose donc « 00:05:00 », que le
# planificateur refuse - mesure du meme jour, sur une tache jetable :
#   Register-ScheduledTask : The task XML contains a value which is
#   incorrectly formatted or out of range. (37,36):ExecutionTimeLimit:00:05:00
# Sous $ErrorActionPreference = 'Stop', l etape 30 mourait donc la, et avec
# elle la console entiere. 40-agent.ps1 ecrit deja la forme juste.
_steam_code = code_only(_steam)
check("30-steam.ps1 n affecte jamais la duree maximale a la propriete",
      ".ExecutionTimeLimit =" in _steam_code, False)
check("... elle passe par le parametre du cmdlet, comme dans 40-agent.ps1",
      "New-ScheduledTaskSettingsSet" in _steam_code
      and "-ExecutionTimeLimit" in _steam_code, True)
check("30-steam.ps1 arme le filet AutoRestartShell",
      "AutoRestartShell" in _steam, True)
_shell = (PROVISION / "assets" / "steam-hold-notice.ps1").read_text(encoding="utf-8")
_shell_code = [ln for ln in _shell.splitlines()
               if ln.strip() and not ln.lstrip().startswith(("#", "<#", "»"))]
# LE SHELL NE DOIT PLUS LANCER STEAM. Il s execute a l ouverture de session,
# avant qu un client soit connecte et donc avant que SudoVDA ait cree l ecran
# virtuel : le Chromium de Steam, qui choisit son moteur de rendu une seule fois
# au demarrage, retombait alors sur le rasteriseur LOGICIEL SwiftShader pour
# toute la duree du processus (webhelper_gpu.txt : gpu_compositing =
# disabled_software), ce qui rendait Big Picture inutilisable. La boucle de
# relance empechait de surcroit « quitter Steam » de fermer la session.
check("le shell de session ne lance pas Steam",
      any("steam.exe" in ln.lower() or "Start-Process" in ln
          for ln in _shell_code), False)
check("le shell reste vivant pour que Winlogon ne perde pas la session",
      "while ($true)" in _shell, True)

# USO laisse NetKVM emettre des super-datagrammes UDP que le conntrack de
# l'hote classe « invalid » et que firewalld jette sans journaliser : Apollo
# croit streamer, le client ne recoit rien, et aucun des deux ne le dit.
_virtio = (PROVISION / "15-virtio.ps1").read_text(encoding="utf-8")
check("15-virtio.ps1 desarme UDP Segmentation Offload",
      "UDP Segmentation Offload" in _virtio, True)
check("15-virtio.ps1 relit la valeur au lieu de croire l ecriture",
      "is still" in _virtio, True)

# Windows numerote les sessions en incrementant : la session console n'est pas
# forcement la 1. Apres la reconstruction du 2026-08-25 elle valait 2, et un
# controle code en dur a refuse une appliance saine.
_marker = (PROVISION / "99-marker.ps1").read_text(encoding="utf-8")
check("99-marker.ps1 ne compare pas la session a 1 en dur",
      "-ne '1'" in _marker, False)
check("99-marker.ps1 compare a la session console",
      "(Get-Process -Id $PID).SessionId" in _marker, True)

# C: est regeneree a chaque reconstruction : une bibliotheque Steam qui y
# atterrit par megarde est exactement le defaut que la separation C:/D: existe
# pour empecher. Le masque est un champ de bits, une lettre par bit depuis A.
check("30-steam.ps1 masque C: dans les boites de dialogue",
      "NoDrives" in _steam and "NoViewOnDrive" in _steam, True)
check("30-steam.ps1 relit les valeurs posees", "did not take" in _steam, True)

# steam-session.ps1 est la commande SUIVIE par Apollo : sa sortie ferme la
# session Moonlight, donc quitter Steam ferme le flux. Elle ne lance rien —
# c est l entree « detached » d apps.json qui demarre Steam, hors du groupe de
# processus qu Apollo tue a la deconnexion, faute de quoi une deconnexion
# emporterait le jeu en cours.
_sess = (PROVISION / "assets" / "steam-session.ps1").read_text(encoding="utf-8")
_sess_code = [ln for ln in _sess.splitlines()
              if ln.strip() and not ln.lstrip().startswith(("#", "<#", "»"))]
check("steam-session.ps1 ne demarre pas Steam lui-meme",
      any("Start-Process" in ln for ln in _sess_code), False)
check("steam-session.ps1 rend la main quand Steam a disparu pour de bon",
      "RestartGraceSeconds" in _sess, True)
# Ce delai EST l attente ressentie apres avoir quitte Steam, resserree deux
# fois sur mesure : 30 s (ressenti « une minute »), 10 s (mesure : 12 s de bout
# en bout), puis 3 s. Le sondage s y ajoutant tel quel, il doit rester
# sous la seconde.
check("l attente apres la fermeture de Steam reste courte",
      "$RestartGraceSeconds = 3" in _sess, True)
check("le sondage ne rallonge pas l attente",
      "Start-Sleep -Seconds 3" in _sess or "Start-Sleep -Seconds 1" in _sess, False)
# Au premier lancement Steam telecharge sa mise a jour avant d ouvrir une
# fenetre ; trente secondes n y suffisent pas et la session se fermerait pendant
# que Steam demarre encore.
check("steam-session.ps1 laisse a Steam le temps de sa premiere mise a jour",
      "AppearDeadlineSeconds = 300" in _sess, True)
# LA SURVEILLANCE DE STEAM NE DOIT JAMAIS ATTENDRE DERRIERE AUTRE CHOSE.
# La maximisation et la surveillance tenaient dans deux boucles successives.
# Un Steam deja lance et replie dans la zone de notification n a pas de fenetre
# principale, donc pas de titre a reconnaitre : la premiere boucle allait au
# bout de ses 180 s, et quitter Steam ne fermait rien pendant tout ce temps.
# Mesure du 2026-08-27 : session ouverte a 11:16:05, Apollo n a rendu la main
# qu a 11:19:08 — 180 s de boucle plus les 3 s de grace, au dixieme pres.
# Une seule boucle desormais : elle surveille, et greffe la maximisation dessus.
_sess_loops = [ln for ln in _sess_code if re.match(r"\s*while \(\$true\)", ln)]
check("une seule boucle principale surveille Steam",
      len(_sess_loops), 1)
check("la maximisation se greffe sur la surveillance",
      "$maximized = Set-SteamMaximized" in _sess, True)
check("la maximisation ne bloque plus la surveillance",
      any("while" in ln and "windowDeadline" in ln for ln in _sess_code), False)
# En Big Picture la fenetre est deja plein ecran : partir avec le travail fait
# evite d interroger la table des processus une fois de plus par demi-seconde.
check("Big Picture ne cherche aucune fenetre a maximiser",
      "$maximized = ($Mode -ne 'Desktop')" in _sess, True)
# La maximisation vivait dans un prep-cmd, qu Apollo attend AVANT de lancer
# l application, et sa boucle ne sortait jamais avant son delai : 182 secondes
# de retard mesurees a chaque session le 2026-08-26. Elle doit sortir des que la
# fenetre est trouvee.
check("la boucle de maximisation sort des qu elle a trouve la fenetre",
      "break" in _sess, True)
check("maximize-steam.ps1 ne subsiste pas en doublon",
      (PROVISION / "assets" / "maximize-steam.ps1").exists(), False)

# --- Dette C4 : le curseur de la souris reste pose sur Big Picture ------------
#
# L apps.json ecrit a la main du 2026-07-23 faisait porter a l entree "Steam
# Big Picture" un nomousy en plus de -bigpicture ; le passage au gabarit
# apps.json.j2 l a laisse tomber sans que rien ne le signale. Le masquage
# revient ici, et NULLE PART AILLEURS :
#   - pas dans un prep-cmd : Apollo attend sa fin AVANT de lancer
#     l application, 182 s mesurees le 2026-08-26 ;
#   - pas par nomousy : verifie absent de l invite le 2026-08-29 (C:\nivuus,
#     C:\Program Files\Apollo, C:\Windows, D:\, et Get-Command) - il faudrait
#     donc l embarquer, l empreindre et le verifier dans une charge utile qui
#     s installe hors ligne, pour ce que user32.dll fait deja ;
#   - pas dans sunshine.conf : la table des options d Apollo 0.4.6, lue dans
#     les chaines de C:\Program Files\Apollo\sunshine.exe le 2026-08-29 (elle
#     va de "qp" a "locale" et se termine par le message "Warning:
#     Unrecognized configurable option ["), ne contient AUCUNE cle de curseur.
#     Les plus proches sont mouse / keyboard / controller, qui activent des
#     peripheriques d entree. Une cle inventee serait ignoree EN SILENCE.
# Tout ce qui suit lit le CODE SEUL (code_only) : les en-tetes de ces deux
# fichiers expliquent deja le pourquoi en toutes lettres, et un controle lu sur
# le texte brut resterait vert alors que le comportement aurait disparu.
_sess_body = code_only(_sess)
_cursor_path = PROVISION / "assets" / "steam-cursor.ps1"
check("steam-cursor.ps1 existe", _cursor_path.is_file(), True)
# Il est dot-source par $PSScriptRoot : il doit atterrir dans C:\nivuus\apollo a
# cote de steam-session.ps1, pas rester sur le media de reponses.
check("25-apollo.ps1 copie steam-cursor.ps1 a cote de steam-session.ps1",
      "'steam-session.ps1', 'steam-launch.ps1', 'steam-cursor.ps1'"
      in apollo_stage, True)
_cursor = _cursor_path.read_text(encoding="utf-8")
_cursor_body = code_only(_cursor)
# L en-tete de steam-cursor.ps1 nomme nomousy pour raconter l historique ; c est
# le CODE qui ne doit appeler aucun binaire tiers.
check("le masquage n appelle aucun binaire tiers",
      "nomousy" in (_sess_body + _cursor_body).lower(), False)
check("le masquage passe par les curseurs systeme de user32.dll",
      "CreateCursor" in _cursor_body and "SetSystemCursor" in _cursor_body, True)
# SetSystemCursor DETRUIT le curseur qu on lui confie : un handle unique
# reutilise serait mort des le deuxieme identifiant, et seule la fleche
# disparaitrait - un defaut qui laisse le sablier de chargement a l ecran.
check("un curseur vide est cree pour CHAQUE identifiant systeme",
      "$blank = [NivuusWin]::CreateCursor" in _cursor_body
      and "foreach ($id in $SystemCursorIds)" in _cursor_body, True)
# CreateCursor exige les dimensions du curseur systeme (mesure sur l invite le
# 2026-08-29 : 32x32) ; les coder en dur ferait echouer l appel ailleurs.
check("les dimensions viennent du systeme, pas d une constante",
      "GetSystemMetrics" in _cursor_body, True)
# 🔴 Le mode Desktop lance le client Steam NORMAL, qui se pilote a la souris :
# un masquage pose pour la session entiere le rendrait inutilisable.
check("seul le mode Big Picture masque le curseur",
      "$cursorHidden = ($Mode -ne 'BigPicture')" in _sess_body, True)
# Meme regle que la maximisation : ca se greffe sur la boucle de surveillance,
# jamais devant elle. La surveillance ne doit JAMAIS attendre derriere autre
# chose - c est ce qui avait coute 180 s de "quitter Steam ne ferme rien".
check("le masquage se greffe sur la boucle de surveillance",
      "$cursorHidden = Set-CursorHidden" in _sess_body, True)
check("steam-session.ps1 dot-source les fonctions de curseur",
      "steam-cursor.ps1" in _sess_body, True)
# Apollo tue le groupe de processus de la commande suivie quand le client se
# deconnecte : une session Big Picture peut donc mourir sans jamais executer sa
# propre restauration, et laisser des curseurs vides derriere elle. Le mode
# Desktop - le seul qui ait besoin de la souris - repart donc TOUJOURS de
# curseurs relus du registre. C est le filet, et il n est pas facultatif.
check("le mode Desktop restaure les curseurs avant de surveiller",
      "if ($Mode -eq 'Desktop') { Restore-SystemCursors }" in _sess_body, True)
check("la restauration relit les curseurs du registre (SPI_SETCURSORS)",
      "SPI_SETCURSORS" in _cursor_body and "SPI_SETCURSORS" in _sess_body, True)
check("une sortie normale de Big Picture rend le curseur",
      "if ($Mode -eq 'BigPicture') { Restore-SystemCursors }" in _sess_body, True)
# Windows PowerShell 5.1 relit un .ps1 SANS BOM dans la page de codes ANSI :
# tout octet non-ASCII y change de sens. Les commentaires francais de ces deux
# fichiers s ecrivent donc sans accents ni guillemets typographiques.
for _name in ("steam-session.ps1", "steam-cursor.ps1"):
    _raw = (PROVISION / "assets" / _name).read_bytes()
    check(f"{_name} ne porte pas de BOM", _raw.startswith(b"\xef\xbb\xbf"), False)
    check(f"{_name} est en ASCII pur (fichier sans BOM)",
          [ln for ln in _raw.decode("utf-8").splitlines()
           if any(ord(c) > 126 for c in ln)], [])

# steam-launch.ps1 est l entree « detached » : elle demarre Steam, mais SEULEMENT
# une fois qu Apollo a pose un affichage. Hors session la RTX 4070 ne pilote
# aucun ecran — mesure du 2026-08-26, le bureau vit sur la VGA emulee en
# 1280x800 a 1 Hz — et le Chromium de Steam, qui choisit son moteur de rendu une
# seule fois au demarrage, retomberait sur le rasteriseur logiciel SwiftShader.
_launch = (PROVISION / "assets" / "steam-launch.ps1").read_text(encoding="utf-8")
check("steam-launch.ps1 attend un affichage avant de demarrer Steam",
      "WmiMonitorBasicDisplayParams" in _launch, True)
check("steam-launch.ps1 demarre bien Steam",
      "Start-Process" in _launch, True)
# L attente doit rester BORNEE : un prep-cmd qui ne sortait jamais avant son
# delai retardait chaque session de 182 s. Un Steam en rendu logiciel vaut mieux
# qu un ecran vide, donc on lance quand meme passe le delai.
check("l attente est bornee", "WaitSeconds = 45" in _launch, True)

# Task 2 (sub-project C2) : steam.hold. La synchronisation de bibliotheque
# (hote) arrete Steam pour reecrire shortcuts.vdf, que Steam re-ecrit lui-meme
# a sa fermeture - sans garde, la synchro reussit sans rien produire, par
# intermittence, des qu une session redemarre Steam pendant l ecriture.
#
# Le shell de session (steam-shell.ps1) ne lance plus Steam depuis le
# 2026-08-26 (voir les checks plus bas) : c est steam-launch.ps1, l entree
# « detached » qu Apollo invoque a chaque nouvelle session, qui demarre
# reellement Steam - et donc le seul endroit ou une garde a quelque chose a
# empecher. La reintroduire dans le shell rouvrirait exactement les deux bugs
# que son en-tete documente (rendu logiciel avant l ecran virtuel, et Steam
# qui se relance quand on le quitte).
#
# Tout ce qui suit lit le code, jamais le docstring (code_only, defini plus
# haut) : le docstring de ce fichier parle deja de steam.hold, LastWriteTime
# et 300 en toutes lettres pour expliquer le pourquoi, et un check qui lirait
# le texte brut resterait vert meme si le comportement decrit disparaissait du
# code.
_launch_code = code_only(_launch)
check("steam-launch.ps1 connait le sentinel steam.hold",
      "steam.hold" in _launch_code, True)
check("steam-launch.ps1 definit Test-SteamHold plutot que de disperser le controle",
      "function Test-SteamHold" in _launch_code, True)
# TOCTOU (revue) : Test-Path puis Get-Item laisse une fenetre ou l hote peut
# retirer le sentinel entre les deux. Get-Item seul, avec -ErrorAction
# SilentlyContinue, rend le meme $null que le fichier soit absent ou parti
# entre-temps - une seule question, jamais un horodatage lu sur du vide.
check("steam-launch.ps1 lit le sentinel par un Get-Item unique (pas de Test-Path puis Get-Item)",
      "Get-Item -Path $HoldFile -ErrorAction SilentlyContinue" in _launch_code, True)
check("steam-launch.ps1 ne teste plus l existence separement avant de lire l horodatage",
      "Test-Path $HoldFile" in _launch_code, False)
# L age doit venir de l horodatage du FICHIER, jamais d une variable interne :
# ce script est un nouveau processus a chaque invocation, sans etat persistant
# entre deux sessions - une minuterie en memoire repartirait de zero a chaque
# lancement, ne garantissant jamais l expiration.
check("steam-launch.ps1 lit l age du sentinel sur son horodatage de fichier",
      "LastWriteTime" in _launch_code, True)
check("steam-launch.ps1 fait expirer le sentinel au bout de cinq minutes",
      "$HoldMaxAgeSeconds = 300" in _launch_code, True)

# La fonction elle-meme, isolee du reste du fichier (entre sa signature et
# l appel qui la termine) : c est ici, et non a l echelle du fichier entier,
# qu il faut verifier l assemblage - une lecture a l echelle du fichier
# laisserait passer un retour $true retire (revue, CRITIQUE 1) ou un -lt
# invertit en -gt (revue, CRITIQUE 2), les deux rendant la garde decorative
# tout en laissant chaque "piece" (steam.hold, LastWriteTime, 300) presente.
_fn_start = _launch_code.find("function Test-SteamHold")
_fn_end = _launch_code.find('Write-Log "--- lancement demande')
check("la fonction Test-SteamHold est bien localisee dans le fichier",
      -1 not in (_fn_start, _fn_end) and _fn_start < _fn_end, True)
_fn_body = _launch_code[_fn_start:_fn_end]
# Mutation testee (CRITIQUE 2) : inverser -lt en -gt. Le texte exact
# "-lt $HoldMaxAgeSeconds" disparait alors du corps de la fonction, et ce
# check a lui seul le detecte deja.
check("la retenue exige un age INFERIEUR au maximum, jamais l inverse (-lt, pas -gt)",
      "-lt $HoldMaxAgeSeconds" in _fn_body, True)
_after_lt = _fn_body[_fn_body.find("-lt $HoldMaxAgeSeconds"):] if "-lt $HoldMaxAgeSeconds" in _fn_body else ""
# Mutation testee (CRITIQUE 1) : retirer "return $true" de la branche active
# (la fonction retomberait alors systematiquement sur "return $false" plus
# bas, et Test-SteamHold ne dirait plus jamais vrai - garde purement
# decorative a chacun de ses trois points d appel).
check("le sentinel frais (age < maximum) fait retourner $true depuis Test-SteamHold",
      "return $true" in _after_lt, True)
# Et ce $true doit preceder le $false de la branche perimee, pas l inverse -
# sinon rien ne garantit qu il s agit bien de LA branche active.
check("le $true de la branche active precede le $false de la branche perimee",
      "return $true" in _after_lt and "return $false" in _after_lt and
      _after_lt.find("return $true") < _after_lt.find("return $false"), True)

# La propriete qui compte le plus : la garde ne doit RIEN casser du
# comportement normal. En l absence de sentinel (ou perime), Steam doit
# toujours etre lance - sans quoi la garde aurait cache un vrai defaut derriere
# un defaut different.
check("steam-launch.ps1 demarre toujours Steam quand le sentinel est absent ou perime",
      "Start-Process -FilePath $SteamExe" in _launch_code, True)

# IMPORTANT 3 (revue) : un controle unique tout en haut laisse une fenetre de
# $WaitSeconds (45 s) entre "sentinel absent" et le lancement reel - l hote
# peut poser le sentinel pendant cette attente, et Steam demarrerait quand
# meme, en plein milieu de l ecriture. Le controle doit donc etre repete a
# CHACUN des trois points ou ce script peut faire demarrer ou reagir avec
# Steam, pas seulement au tout debut.
_launch_lines = [ln.strip() for ln in _launch_code.splitlines() if ln.strip()]


def line_index(lines, needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i
    return -1


_idx_wait = line_index(_launch_lines, "$deadline = (Get-Date).AddSeconds($WaitSeconds)")
_idx_bigpicture_uri = line_index(_launch_lines, "Start-Process 'steam://open/bigpicture'")
_idx_final_bigpicture = line_index(_launch_lines, "Start-Process -FilePath $SteamExe -ArgumentList")
_idx_final_default = line_index(_launch_lines, "else { Start-Process -FilePath $SteamExe }")
_guard = "if (Test-SteamHold) { return }"
check("tous les anchors necessaires a ces controles ont ete localises",
      -1 not in (_idx_wait, _idx_bigpicture_uri, _idx_final_bigpicture, _idx_final_default), True)
_idx_first_guard = line_index(_launch_lines, _guard)
check("un premier controle precede l attente d affichage (evite d attendre 45 s pour rien)",
      _idx_first_guard != -1 and _idx_wait != -1 and _idx_first_guard < _idx_wait, True)
check("un controle protege directement l envoi de steam://open/bigpicture a un Steam deja vivant",
      _idx_bigpicture_uri > 0 and _launch_lines[_idx_bigpicture_uri - 1] == _guard, True)
check("un dernier controle precede immediatement le lancement final de Steam (mode Big Picture)",
      _idx_final_bigpicture > 0 and _launch_lines[_idx_final_bigpicture - 1] == _guard, True)
check("ce meme dernier controle couvre aussi la branche par defaut (Desktop)",
      _idx_final_default == _idx_final_bigpicture + 1, True)

# Les lecteurs optiques s emparent des premieres lettres libres et se posent
# exactement la ou les partages doivent aller : mesure le 2026-08-26, les deux
# media d installation tenaient E: et F:. L etage doit les deplacer AVANT.
_sh = (PROVISION / "35-shares.ps1").read_text(encoding="utf-8")
# L etape ne doit JAMAIS demonter un lecteur optique : $PayloadRoot est le media
# de reponses, et run-all.ps1 y lit tous les etages suivants. Le 2026-08-26 elle
# a demonte F: puis echoue sur « A drive with the name 'F' does not exist »,
# arretant net le provisionnement.
check("35-shares.ps1 ne demonte aucun lecteur optique", "mountvol" in _sh, False)
# Et le binaire doit etre copie AVANT tout le reste, tant que le media est lisible.
check("35-shares.ps1 copie virtiofs.exe avant de creer les services",
      _sh.index("Copy-Item") < _sh.index("sc.exe create"), True)
check("35-shares.ps1 cree un service par partage (VirtioFsSvc n en monte qu un)",
      "sc.exe create" in _sh, True)
check("35-shares.ps1 relit le montage au lieu de croire le demarrage du service",
      "Test-Path" in _sh and "not mounted" in _sh, True)

# GameDVR est une SECONDE capture d ecran tournant a cote de celle d Apollo, et
# WSearch indexerait les partages virtiofs de l etage 35 a travers un systeme de
# fichiers reseau. Defender et Edge restent : c est un choix, pas un oubli.
_deb = (PROVISION / "45-debloat.ps1").read_text(encoding="utf-8")
check("45-debloat.ps1 coupe GameDVR", "GameDVR_Enabled" in _deb, True)
check("45-debloat.ps1 coupe l indexation", "WSearch" in _deb, True)
check("45-debloat.ps1 relit chaque service au lieu de croire Set-Service",
      "is still" in _deb, True)
check("45-debloat.ps1 ne touche pas a Defender",
      "Defender" in _deb and "Remove" not in _deb.split("Defender")[1][:200], True)

# Sans explorer.exe il n y a pas de bureau, donc pas de papier peint : une image
# posee dans le registre ne s afficherait nulle part. Le lanceur la dessine
# lui-meme, DERRIERE tout le reste, et sous try — un fond qui echoue ne doit pas
# empecher la console de demarrer.
# Le fond d ecran a CHANGE DE MAIN le 2026-08-30 : Explorer le dessine, donc
# desktop-chrome.ps1 se contente de le poser dans le registre. Le peindre dans
# une form n avait de sens que sans bureau.
_chrome = (PROVISION / "assets" / "desktop-chrome.ps1").read_text(encoding="utf-8")
check("l habillage pose le fond par le registre, plus par une form",
      "SystemParametersInfo" in _chrome and "BackgroundImage" not in _chrome, True)
check("l habillage cache les icones du bureau", "HideIcons" in _chrome, True)
check("l habillage met la barre des taches en masquage automatique",
      "StuckRects3" in _chrome, True)
# La cle StuckRects3 n existe pas tant qu Explorer n a jamais tourne : ecrire un
# binaire de 40 octets invente a sa place poserait une structure que Windows n a
# pas ecrite. On n allume qu un bit sur la valeur EXISTANTE, et l absence de cle
# est un report au prochain logon, pas une panne.
check("l habillage n invente pas la valeur binaire de la barre",
      "-bor 0x01" in _chrome, True)
# LE NOIR DES BANDES LATERALES, et il ne vient plus de la meme main. Sous le
# kiosque, steam-shell.ps1 peignait le fond dans une form dont il posait lui-
# meme le BackColor a #000. Explorer redevenu shell, c est Windows qui dessine,
# et le style 6 (Ajuster) complete l image avec la COULEUR DE FOND DU BUREAU -
# HKCU\Control Panel\Colors\Background, que rien ici ne posait. Elle vaut
# « 0 0 0 » par defaut (mesure du 2026-09-04 sur l invite), donc la charte
# tenait par chance : le telephone du salon streame en 2410x1080, jamais en
# 16:9, et ces bandes sont visibles a chaque session. La charte Nivuus
# (paragraphe 3) est noir et blanc sans exception - on la pose.
check("l habillage pose le noir des bandes laterales du fond",
      "Colors" in _chrome and "Background" in _chrome, True)
check("... et le style Ajuster, qui est ce qui les cree",
      "WallpaperStyle" in _chrome, True)
check("... et rejoue au logon suivant si Explorer n a pas encore ecrit la cle",
      "retrying next logon" in _chrome, True)
# Une valeur Settings trop courte tombait dans le vide : aucun message, alors
# que toutes les autres branches de ce fichier disent ce qu elles ont fait ou
# pourquoi elles n ont rien fait. Un silence est indiscernable d un succes.
check("une valeur de barre inexploitable est dite, pas avalee",
      "trop courte" in _chrome, True)
check("l avertissement ne passe jamais devant un jeu",
      "$form.TopMost = $false" in _shell, True)
check("l habillage ne peut pas empecher la console de demarrer",
      "hold notice not shown" in _shell, True)

# Task 2 (sub-project C2) : le shell ne pose ni ne consomme le sentinel
# steam.hold (c est steam-launch.ps1 qui empeche le relancement, voir plus
# haut), mais c est lui qui possede l ecran - il doit dire au proprietaire que
# la bibliotheque se met a jour, faute de quoi un ecran fige sans Steam se lit
# comme une panne et quelqu un finit par redemarrer la machine en plein milieu
# d une ecriture.
#
# code_only (pas _shell brut, pas meme _shell_code du check plus haut, qui ne
# retire que les lignes DEBUTANT par # ou <# et laisse donc passer tout le
# corps du docstring) : le docstring de ce fichier decrit deja steam.hold,
# son expiration a cinq minutes et parle de "bibliotheque" en toutes lettres
# (revue, IMPORTANT 4) - un check lu sur le texte brut resterait vert meme si
# le label et son affectation disparaissaient entierement du code.
_shell_code2 = code_only(_shell)
check("le shell lit le meme sentinel steam.hold que steam-launch.ps1",
      "steam.hold" in _shell_code2, True)
check("le shell definit lui aussi Test-SteamHold (meme protection TOCTOU que steam-launch.ps1)",
      "function Test-SteamHold" in _shell_code2, True)
check("le shell lit le sentinel par un Get-Item unique (pas de Test-Path puis Get-Item)",
      "Get-Item -Path $HoldFile -ErrorAction SilentlyContinue" in _shell_code2, True)
# Meme regle d expiration que steam-launch.ps1, sur le meme horodatage de
# fichier - jamais une minuterie a lui, puisque AutoRestartShell peut relancer
# ce script pendant la retenue elle-meme.
check("le shell fait aussi expirer le sentinel au bout de cinq minutes, sur l horodatage du fichier",
      "LastWriteTime" in _shell_code2 and "$HoldMaxAgeSeconds = 300" in _shell_code2, True)
check("le shell affiche un message pendant la retenue (dans le CODE, pas seulement le docstring)",
      "bibliotheque" in _shell_code2.lower(), True)
# Le message doit rester l EXCEPTION : invisible par defaut...
check("le message de retenue reste cache hors retenue",
      "$holdLabel.Visible = $false" in _shell_code2, True)
# ... et bascule reellement selon Test-SteamHold : forcer $holdLabel.Visible a
# $false en permanence (revue, IMPORTANT 5 - l exigence "le proprietaire doit
# voir ce qui se passe" resterait alors lettre morte) laisserait le check
# ci-dessus au vert, puisqu il ne regarde que l etat par defaut. Celui-ci
# verifie l affectation qui le fait VARIER, dans la boucle.
check("la visibilite du message suit Test-SteamHold (pas figee sur sa valeur par defaut)",
      "$holdLabel.Visible = (Test-SteamHold)" in _shell_code2, True)

# New-Item -Force sur une cle de registre EXISTANTE ne cree pas : il SUPPRIME
# l arbre puis le recree, et echoue sur « Cannot delete a subkey tree ». Le
# 2026-08-26 l etage d epuration est mort dessus apres avoir desactive dix
# services, emportant tout ce qui suivait.
for _stage in ("45-debloat.ps1", "30-steam.ps1"):
    _body = (PROVISION / _stage).read_text(encoding="utf-8")
    _lines = _body.splitlines()
    # Le garde peut etre sur la ligne meme (forme courte) ou sur la precedente
    # (forme en bloc) : les deux protegent, seule leur absence est un defaut.
    _bad = [ln for n, ln in enumerate(_lines)
            if "New-Item" in ln and "-Force" in ln and "HK" in ln
            and not ln.lstrip().startswith("#")
            and "Test-Path" not in ln
            and "Test-Path" not in (_lines[n - 1] if n else "")]
    check(f"{_stage} ne force pas New-Item sur une cle de registre", _bad, [])

# WinRM doit rester JOIGNABLE pendant tout le provisionnement. Il etait
# coupe-feu jusqu au dernier geste de 99-marker, ce qui faisait de « 5985
# joignable » un proxy de « invite pret » — un proxy qui mentait exactement la
# ou ca comptait : quand un etage leve, 99-marker n est jamais atteint, la
# regle reste fermee, et la seule porte d entree disparait au moment ou il faut
# regarder. Trois cycles sont morts ainsi le 2026-08-26, l appliance n ayant ni
# invite Executer (plus d explorer) ni console utilisable (Apollo prend
# l affichage).
_boot = (PROVISION / "00-bootstrap.ps1").read_text(encoding="utf-8")
_code = [ln for ln in _boot.splitlines() if not ln.lstrip().startswith("#")]
check("00-bootstrap.ps1 ne referme pas la regle WinRM",
      any("Disable-NetFirewallRule" in ln for ln in _code), False)
check("00-bootstrap.ps1 relit la regle au lieu de croire Enable-PSRemoting",
      "stayed disabled" in _boot, True)

# --- Tache 4 (sous-projet C2) : 32-retro.ps1, l etape de retrogaming.
#
# Tout ce qui suit lit le CODE SEUL (code_only) : le docstring de cette etape
# explique deja 7zr, le BCJ2, les 1,5 Gio et l absence de « retro sync » en
# toutes lettres, et un controle lu sur le texte brut resterait vert alors
# meme que la mecanique aurait disparu - le motif attrape quatre fois sur ce
# seul sous-projet.
_retro = (PROVISION / "32-retro.ps1").read_text(encoding="utf-8")
_retro_code = code_only(_retro)
_retro_lines = [ln.strip() for ln in _retro_code.splitlines() if ln.strip()]
# Deux morceaux de l etape vivent dans provision/assets/, comme apollo-drivers
# pour l etape 25 : le temoin durable et la mise en place de 7zr. Ils sont
# dot-sources, donc ils tournent DANS l etape - les controles les suivent la
# ou le code est parti, ils ne s allegent pas d avoir traverse un fichier.
_status_ps1 = (PROVISION / "assets" / "retro-status.ps1").read_text(encoding="utf-8")
_status_code = code_only(_status_ps1)
_7zr_code = code_only((PROVISION / "assets" / "retro-7zr.ps1").read_text(encoding="utf-8"))
for _asset in ("retro-status.ps1", "retro-7zr.ps1"):
    check(f"32-retro.ps1 dot-source assets\\{_asset} (dans le code)",
          f"assets\\{_asset}')" in _retro_code, True)

# Le nom du dossier de pilotes retrogaming : nomme une fois cote Python
# (payload.RETRO_DIRNAME, deja epingle a fetch_payload.RETRO_DIRNAME par
# test_windows_guest_fetch_payload.py), mais recopie ici en litteral
# PowerShell plutot qu importe - Windows ne lit pas payload.py. Un renommage
# d un seul cote n est detecte par rien : sur une console ou la case EST
# cochee, l etape leve alors une erreur bruyante qui accuse la charge utile
# d avoir ete construite sans --retro (voir le message "aucun installateur
# Python dans $PayloadRetro" plus bas), alors que le vrai probleme est ce
# desaccord de nom entre les deux cotes.
check("32-retro.ps1 cherche les pilotes retrogaming au nom que Python leur donne",
      f"'drivers\\{payload.RETRO_DIRNAME}'" in _retro_code, True)

# L etape reste dans la liste MEME quand l option n est pas cochee : une etape
# absente ne laisse aucune trace. La position se lit sur le code de
# run-all.ps1, pas sur son texte : le commentaire qui justifie l insertion
# nomme lui aussi le fichier, et satisferait un find() sur le texte brut.
_runall_code = code_only(runall)
check("run-all lance 32-retro.ps1 (dans le code, pas seulement en commentaire)",
      "'32-retro.ps1'" in _runall_code, True)
check("32-retro.ps1 s insere entre Steam (30) et les partages (35)",
      _runall_code.find("'30-steam.ps1'") < _runall_code.find("'32-retro.ps1'")
      < _runall_code.find("'35-shares.ps1'"), True)


def _first_line_with(needle, start=0):
    for i in range(start, len(_retro_lines)):
        if needle in _retro_lines[i]:
            return i
    return -1


# 1. Le basculement. L etape lit config\retro.psd1, le fichier que build.py
# rend DANS TOUS LES CAS - et son absence n est pas « desactive » mais « charge
# utile anterieure a l option », deux etats qu elle ne doit pas confondre.
check("32-retro.ps1 lit le basculement rendu par build.py",
      "Import-PowerShellDataFile" in _retro_code
      and "config\\retro.psd1" in _retro_code, True)
_idx_absent = _first_line_with("if (-not (Test-Path $toggle))")
_idx_read = _first_line_with("Import-PowerShellDataFile")
_absent_block = _retro_lines[_idx_absent:_idx_read]
check("la garde « basculement absent » est bien localisee",
      _idx_absent >= 0 and _idx_read > _idx_absent, True)
# Le message seul ne prouve rien : remplacer le throw par un Write-Host suivi
# d un return laissait ce controle vert, alors que c est l UNIQUE invariant
# pour lequel toute la distinction « absent != desactive » existe.
check("un basculement absent LEVE (le throw, pas seulement son texte)",
      any(ln.startswith("throw") for ln in _absent_block), True)
check("... et ne sort jamais en succes a la place",
      any("return" in ln or "Write-Host" in ln for ln in _absent_block), False)
check("la levee dit pourquoi l absence n est pas un refus",
      "$toggle est absent" in _retro_code, True)

# 2. Option non cochee : l etape DIT pourquoi elle s arrete, puis sort en
# succes. Mutations couvertes : retirer le return (l etape installerait tout
# malgre le refus), et retirer le message (elle sortirait muette, ce que le
# proprietaire ne pourrait pas distinguer d une etape jamais executee).
_idx_guard = _first_line_with("if (-not $retro.Enabled) {")
_idx_msg = _first_line_with("retrogaming desactive", _idx_guard if _idx_guard >= 0 else 0)
_idx_return = _first_line_with("return", _idx_guard if _idx_guard >= 0 else 0)
# Install-Retro7zr remplace le Copy-Item parti dans l asset : c est la meme
# action, appelee depuis ici, et elle doit rester derriere les memes gardes.
_actions = ["Start-Process", "pip install", "$retroExe install",
            "Install-Retro7zr", "New-Item -ItemType Directory"]
_idx_actions = [_first_line_with(a) for a in _actions]
check("la garde « option non cochee » est bien localisee", _idx_guard >= 0, True)
check("toutes les actions d installation ont ete localisees",
      all(i >= 0 for i in _idx_actions), True)
check("la garde ecrit POURQUOI elle s arrete (dans le code, pas le docstring)",
      _idx_msg > _idx_guard, True)
check("la garde sort de l etape (return), au lieu de continuer",
      _idx_return > _idx_guard, True)
check("aucune installation n a lieu avant cette sortie",
      all(i > _idx_return for i in _idx_actions), True)
check("l etape sort en SUCCES quand l option n est pas cochee (return, pas throw)",
      "throw" in "\n".join(_retro_lines[_idx_guard:_idx_return + 1]), False)

# 3. Le volume persistant, VERIFIE et non suppose : c est le marqueur que
# l etape 20 pose, pas la seule existence de la lettre D:.
check("32-retro.ps1 verifie le volume prepare par l etape 20",
      "D:\\state\\NIVUUS-DATA.id" in _retro_code, True)

# 4. L espace temporaire. Prerequis MESURE : ~1,3 Gio transitent par %TEMP%,
# sur la partition systeme qui n est pas celle des jeux. Mutations couvertes :
# inverser la comparaison (-lt en -gt, soit refuser les machines qui ont la
# place), et deplacer la verification apres les installations qu elle protege.
check("32-retro.ps1 exige 1,5 Gio dans le dossier temporaire",
      "$MinTempFreeGiB = 1.5" in _retro_code, True)
check("l espace libre est LU sur le lecteur du dossier temporaire",
      "[System.IO.Path]::GetTempPath()" in _retro_code
      and "Get-PSDrive" in _retro_code, True)
check("la garde exige un espace SUPERIEUR au minimum, jamais l inverse",
      "if ($tempDrive.Free -lt ($MinTempFreeGiB * 1GB))" in _retro_code, True)
check("le message nomme la partition systeme et le transit mesure",
      "1,3 Gio" in _retro_code and "SYSTEME" in _retro_code
      and "PAS le volume des jeux" in _retro_code, True)
_idx_temp = _first_line_with("if ($tempDrive.Free -lt ($MinTempFreeGiB * 1GB))")
check("l espace est verifie AVANT toute installation",
      _idx_temp >= 0 and all(i > _idx_temp for i in _idx_actions), True)

# 5. 7zr.exe : EXIGENCE, pas commodite. Sans lui, les archives BCJ2 de
# RetroArch sont inextractibles et la console n a aucun emulateur retro. Le
# paquet le cherche par shutil.which(), donc ce qui compte n est pas qu il
# soit copie quelque part mais qu il se RESOLVE par le PATH - y compris pour
# un « retro install » relance depuis l hote, dans une autre session.
check("32-retro.ps1 depose 7zr.exe depuis la charge utile",
      "$sevenZr = Join-Path $PayloadRetro '7zr.exe'" in _7zr_code
      and "Copy-Item -Path $sevenZr -Destination $BinDir" in _7zr_code, True)
check("7zr.exe survit a la reconstruction de C: (il va sur le volume persistant)",
      "$RetroBinDir = 'D:\\Emulation\\bin'" in _retro_code, True)
check("son dossier entre dans le PATH machine, pas seulement dans ce processus",
      "[Environment]::SetEnvironmentVariable('Path'" in _7zr_code, True)
check("le PATH machine est relu au lieu d etre cru",
      "[Environment]::GetEnvironmentVariable('Path', 'Machine') -notlike"
      in _7zr_code, True)
check("la resolution par le PATH est verifiee, pas supposee",
      "Get-Command '7zr.exe'" in _7zr_code, True)
check("un 7zr.exe absent de la charge utile leve",
      "sans 7zr.exe" in _7zr_code, True)
_idx_7zr = _first_line_with("Install-Retro7zr -PayloadRetro")
_idx_install = _first_line_with("$retroExe install")
check("7zr.exe est en place AVANT que les emulateurs s installent",
      _idx_7zr >= 0 and _idx_install > _idx_7zr, True)

# 6. Le paquet, hors ligne : les roues voyagent dans la charge utile, et le
# provisionnement ne doit pas dependre de PyPI.
check("le paquet retro s installe sans index, depuis les roues embarquees",
      "--no-index" in _retro_code and "--find-links $wheels" in _retro_code, True)
check("Python vient de la charge utile, jamais du reseau",
      "python-*-amd64.exe" in _retro_code, True)

# 6 bis. L IDENTITE de la construction installee. Deux roues peuvent porter le
# meme 0.1.0 sans contenir le meme code : « pip install --upgrade » ne
# reinstalle alors RIEN (mesure du 2026-08-29, « Requirement already
# satisfied »), et un correctif ecrit dans le paquet peut rester sans le
# moindre effet sur la console sans que rien ne le dise. Le temoin doit donc
# porter QUELLE construction tourne, pour que l hote puisse le constater.
check("l etape part d une identite inconnue, jamais d une supposition",
      "$RetroPackage = 'inconnue'" in _retro_code, True)
check("l etape releve l identite du paquet qu elle vient d installer",
      "& $retroExe identite" in _retro_code, True)
_idx_pip = _first_line_with("-m pip install")
_idx_identite = _first_line_with("& $retroExe identite")
# Avant le pip install, le releve decrirait le paquet PRECEDENT - le mensonge
# meme que cette cle existe pour empecher.
check("... APRES le pip install, jamais avant",
      _idx_pip >= 0 and _idx_identite > _idx_pip, True)
# Un paquet anterieur a cette sous-commande fait rendre 2 a argparse. Ce n est
# pas une panne : c est le constat que l hote cherche. L etape doit alors
# laisser « inconnue » plutot que d ecrire la premiere ligne d un usage.
check("un « retro identite » en echec laisse l identite inconnue",
      "if ($LASTEXITCODE -eq 0 -and $identite.Count -gt 0)" in _retro_code, True)
check("le temoin porte l identite relevee, sur chacune de ses issues",
      len(re.findall(r"Write-RetroStatus '[\w-]+' [^\n]*\$RetroPackage",
                     _retro_code)),
      len(re.findall(r"Write-RetroStatus '[\w-]+'", _retro_code)))
check("Write-RetroStatus ecrit cette identite dans le temoin",
      '"package=$Package"' in _status_code, True)
# 7. « retro install », et surtout PAS « retro sync » : les partages ne sont
# montes qu a l etape 35, donc G:\ROMs n existe pas encore et un scan
# produirait une bibliotheque vide. Le controle porte sur les invocations
# elles-memes, pas sur le mot « sync » - le dernier message de l etape parle
# de synchronisation, a juste titre.
check("32-retro.ps1 installe les emulateurs",
      "& $retroExe install --emulation-root $EmulationRoot" in _retro_code, True)
_invocations = [ln for ln in _retro_lines if "$retroExe " in ln]
check("aucune invocation de retro autre qu install (jamais sync ici)",
      [ln for ln in _invocations if " sync" in ln], [])
check("l etape dit d ou viendra la premiere synchronisation",
      "viendra de l hote" in _retro_code, True)
# Un emulateur dont l URL est morte (code 1) ne doit pas emporter tout le
# provisionnement d une console dont le streaming fonctionne - meme arbitrage
# que ViGEmBus et que les partages non montes. Un manifeste illisible (code 2),
# lui, ne laisse rien d installe et doit lever.
check("un echec partiel avertit au lieu de bloquer la console",
      "elseif ($installExit -eq 1)" in _retro_code
      and "warning: au moins un emulateur" in _retro_code, True)
check("l avertissement s ecrit en minuscules, comme ailleurs dans le depot",
      "WARNING" in _retro_code, False)
check("un echec total leve",
      "throw \"retro install a rendu $installExit" in _retro_code, True)

# 8. Le TEMOIN DURABLE. L arbitrage ci-dessus (avertir sans bloquer) laisse
# declarer le provisionnement complet avec un dossier d emulation partiel : la
# premiere synchronisation depuis l hote fabriquerait alors une bibliotheque
# Steam d entrees qui ne demarrent pas, puisque le scan construit les chemins
# depuis le manifeste sans verifier qu ils existent. L avertissement, lui, ne
# vit que dans le journal de C:, efface a la reconstruction suivante. Le
# temoin doit donc etre sur D:, comme le PROVISION.failed de run-all.ps1.
check("le temoin est ecrit sur le volume PERSISTANT, pas dans le journal de C:",
      "$RetroStatusFile = 'D:\\state\\retro.status'" in _status_code, True)
check("le temoin dit quand",
      "when=$(Get-Date -Format o)" in _status_code, True)
check("le temoin porte le rapport, donc ce qui a reussi et ce qui a echoue",
      "'report:') + $Report" in _status_code, True)

# Le temoin doit dire de QUEL passage il parle. D: survit aux reconstructions :
# sans identifiant, le « status=ok » d une installation ANTERIEURE affirme que
# tout va bien pour le passage courant, et rien ne permet de le contester. Un
# temoin perime qui dit « ok » est pire que pas de temoin du tout.
check("le temoin porte un identifiant de passage",
      "run=$RetroRunId" in _status_code, True)
# Sur le fichier lu, pas sur le mot : le contrat inscrit dans le temoin nomme
# lui aussi provision.started, et il satisferait un « in » sur le texte.
check("l identifiant vient de l horodatage que run-all pose a chaque passage",
      "$RetroRunFile = 'C:\\nivuus\\state\\provision.started'" in _status_code
      and "Get-Content -Path $RetroRunFile" in _status_code, True)
# Ecrit AVANT tout le reste : c est ce qui empeche un temoin ancien de se
# faire passer pour recent, puisque le passage courant l ecrase des l entree.
_idx_started = _first_line_with("Write-RetroStatus 'started'")
_idx_volume = _first_line_with("D:\\state\\NIVUUS-DATA.id")
check("le temoin « en cours » est ecrit des l entree dans l etape",
      _idx_started >= 0 and all(i > _idx_started for i in _idx_actions), True)
check("... juste apres le volume qui le porte, seul endroit ou l ecrire",
      _idx_volume >= 0 and _idx_started > _idx_volume, True)
# Une interruption entre « started » et l installation doit laisser un temoin
# qui le DIT, plutot que le silence - lequel ne se distingue pas d une etape
# jamais atteinte.
check("toute levee ulterieure laisse un temoin qui le dit",
      "Write-RetroStatus 'interrupted'" in _retro_code
      and "error=$($_.Exception.Message)" in _retro_code, True)
_catch = _retro_code[_retro_code.rindex("catch {"):]
check("le rattrapage releve apres avoir temoigne, il n avale pas l echec",
      "throw" in _catch, True)
check("un status precis deja pose n est pas ecrase par le generique",
      "if ($RetroStatusLast -eq 'started')" in _retro_code, True)

# Le vocabulaire doit nommer la situation reelle : « failed » ne couvrait que
# le manifeste illisible, et les echecs plus precoces n ecrivaient rien.
check("chaque issue de l etape laisse un temoin, l option decochee comprise",
      sorted(set(re.findall(r"Write-RetroStatus '([\w-]+)'", _retro_code))),
      ["disabled", "interrupted", "manifest-unreadable", "ok", "partial",
       "started"])
# Le contrat vit dans le fichier PRODUIT : le lecteur qui l ouvre a la liste
# des status sous les yeux sans avoir a retrouver le script qui l ecrit.
check("le contrat est ECRIT dans le temoin, pas seulement declare a cote",
      "$lines = $RetroStatusHeader + @(" in _status_code, True)
_header = _status_code[_status_code.find("$RetroStatusHeader"):
                       _status_code.find("function Write-RetroStatus")]
for _state in ["started", "disabled", "interrupted", "ok", "partial",
               "manifest-unreadable"]:
    check(f"le temoin documente lui-meme son status « {_state} »",
          _state in _header, True)
check("le temoin dit lui-meme lequel autorise la synchronisation",
      'Seul "ok" autorise la synchronisation' in _header, True)
check("le temoin explique lui-meme a quoi sert run=",
      "run= identifie le passage" in _header, True)
# Meme regle pour package= : qui ouvre le temoin doit y lire ce que la cle veut
# dire, et ce que vaut « inconnue », sans avoir a retrouver le script qui
# l ecrit.
check("le temoin explique lui-meme a quoi sert package=",
      "package= identifie" in _header, True)
check("... y compris ce que veut dire une identite inconnue",
      '"inconnue" veut dire' in _header, True)
# Le rapport doit voyager jusqu au temoin : un temoin qui ne porte que
# « status=partial » ne dit pas QUEL emulateur manque.
check("le temoin d un echec partiel embarque le rapport de l installation",
      "Write-RetroStatus 'partial' $installOutput" in _retro_code, True)
_idx_status_partial = _first_line_with("Write-RetroStatus 'partial'")
_idx_warn = _first_line_with("warning: au moins un emulateur")
check("le temoin est ecrit dans la branche de l echec partiel",
      _idx_status_partial >= 0 and abs(_idx_warn - _idx_status_partial) <= 2, True)

# 9. Les flux d erreur des outils natifs. pip ecrit A COUP SUR sur stderr
# l avertissement « installed in ... which is not on PATH » (le PATH machine a
# change, pas celui de ce processus), et PowerShell 5.1 sous
# $ErrorActionPreference = 'Stop' peut promouvoir ce flux en erreur
# terminante : une installation REUSSIE echouerait sur un avertissement.
for needle, what in [("-m pip install", "pip"), ("$retroExe install", "retro install")]:
    _lines = [ln for ln in _retro_lines if needle in ln]
    check(f"{what} redirige son flux d erreur (2>&1)",
          bool(_lines) and all("2>&1" in ln for ln in _lines), True)

# 10. Un seul installateur Python. Un relevement de version laisse l ANCIEN
# dans le dossier des pilotes ; « le premier par ordre alphabetique » est
# justement l ancien, et il s installerait avec les roues de la nouvelle
# version - un echec sur l invite, bruyant mais tres tardif.
_idx_multi = _first_line_with("if ($installers.Count -gt 1) {")
_idx_pick = _first_line_with("$installer = $installers[0]")
check("l ambiguite est refusee AVANT qu un installateur soit choisi",
      _idx_multi >= 0 and _idx_pick > _idx_multi, True)
check("plusieurs installateurs Python levent, jamais ne se departagent",
      any(ln.startswith("throw")
          for ln in _retro_lines[_idx_multi:_idx_pick]), True)
check("aucun choix implicite du premier installateur venu",
      "Select-Object -First 1" in _retro_code, False)


# UN SEUL bloc de rapport, et il est le DERNIER du fichier. Ce n est pas du
# style : ce fichier a porte deux blocs identiques jusqu au 2026-08-30, l un a
# la ligne 200 et l autre a la fin, avec 113 lignes de checks recopiees entre
# les deux. Le `sys.exit(1)` du premier faisait que le MOINDRE echec en amont
# empechait les ~1100 lignes suivantes de s executer - silencieusement, en
# affichant un « FAIL (1) » parfaitement credible. Mesure de la reparation :
# deux sondes fabriquees pour echouer, une tot et une tard, etaient rapportees
# 1 sur 2 avant, 2 sur 2 apres.
# Ne jamais reintroduire un `sys.exit` ailleurs qu ici.
if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all provisioning script checks passed")
