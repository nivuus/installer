#!/usr/bin/env python3
"""Static checks on the guest provisioning scripts.

PowerShell cannot run here, so these are the invariants a Linux host can still
enforce: ordering, no hardcoded drive letters, and the session-1 contract.
Run: python3 scripts/tests/test_windows_guest_provision.py
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GUEST = REPO / "installer" / "windows-guest"
PROVISION = GUEST / "provision"
PROBE = GUEST / "probe"

STAGES = ["00-bootstrap.ps1", "10-nvidia.ps1", "15-virtio.ps1", "20-disk.ps1",
          "25-apollo.ps1", "30-steam.ps1", "35-shares.ps1",
          "40-agent.ps1", "45-debloat.ps1", "50-power.ps1",
          "55-updates.ps1", "99-marker.ps1"]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)

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
check("provision version is B2", payload.PROVISION_VERSION, "B2")
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
             "run-agent.ps1", "steam-launch.ps1", "steam-shell.ps1"]:
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
check("30-steam.ps1 installe Steam comme shell de session",
      "Winlogon" in _steam and "steam-shell.ps1" in _steam, True)
check("30-steam.ps1 arme le filet AutoRestartShell",
      "AutoRestartShell" in _steam, True)
_shell = (PROVISION / "assets" / "steam-shell.ps1").read_text(encoding="utf-8")
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
# La maximisation vivait dans un prep-cmd, qu Apollo attend AVANT de lancer
# l application, et sa boucle ne sortait jamais avant son delai : 182 secondes
# de retard mesurees a chaque session le 2026-08-26. Elle doit sortir des que la
# fenetre est trouvee.
check("la boucle de maximisation sort des qu elle a trouve la fenetre",
      "break" in _sess, True)
check("maximize-steam.ps1 ne subsiste pas en doublon",
      (PROVISION / "assets" / "maximize-steam.ps1").exists(), False)

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
check("steam-launch.ps1 connait le sentinel steam.hold",
      "steam.hold" in _launch, True)
# L age doit venir de l horodatage du FICHIER, jamais d une variable interne :
# ce script est un nouveau processus a chaque invocation, sans etat persistant
# entre deux sessions - une minuterie en memoire repartirait de zero a chaque
# lancement, ne garantissant jamais l expiration.
check("steam-launch.ps1 lit l age du sentinel sur son horodatage de fichier",
      "LastWriteTime" in _launch, True)
check("steam-launch.ps1 fait expirer le sentinel au bout de cinq minutes",
      "$HoldMaxAgeSeconds = 300" in _launch, True)
# La propriete qui compte le plus : la garde ne doit RIEN casser du
# comportement normal. En l absence de sentinel (ou perime), Steam doit
# toujours etre lance - sans quoi la garde aurait cache un vrai defaut derriere
# un defaut different.
check("steam-launch.ps1 demarre toujours Steam quand le sentinel est absent ou perime",
      "Start-Process -FilePath $SteamExe" in _launch, True)
check("le controle du sentinel precede la tentative de lancement, jamais l inverse",
      _launch.find("HoldFile") < _launch.find("Start-Process -FilePath $SteamExe"), True)
check("le sentinel actif fait sortir le script avant tout lancement",
      "return" in _launch[_launch.find("HoldFile"):_launch.find("Start-Process -FilePath $SteamExe")],
      True)

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
check("le lanceur dessine le fond lui-meme", "BackgroundImage" in _shell, True)
check("le fond ne passe jamais devant un jeu",
      "$form.TopMost = $false" in _shell and "SendToBack" in _shell, True)
check("l habillage ne peut pas empecher Steam de demarrer",
      "wallpaper not shown" in _shell, True)

# Task 2 (sub-project C2) : le shell ne pose ni ne consomme le sentinel
# steam.hold (c est steam-launch.ps1 qui empeche le relancement, voir plus
# haut), mais c est lui qui possede l ecran - il doit dire au proprietaire que
# la bibliotheque se met a jour, faute de quoi un ecran fige sans Steam se lit
# comme une panne et quelqu un finit par redemarrer la machine en plein milieu
# d une ecriture.
check("le shell lit le meme sentinel steam.hold que steam-launch.ps1",
      "steam.hold" in _shell, True)
# Meme regle d expiration que steam-launch.ps1, sur le meme horodatage de
# fichier - jamais une minuterie a lui, puisque AutoRestartShell peut relancer
# ce script pendant la retenue elle-meme.
check("le shell fait aussi expirer le sentinel au bout de cinq minutes, sur l horodatage du fichier",
      "LastWriteTime" in _shell and "$HoldMaxAgeSeconds = 300" in _shell, True)
check("le shell affiche un message pendant la retenue",
      "bibliotheque" in _shell.lower(), True)
# Le message doit rester l EXCEPTION : invisible par defaut, il ne doit
# s afficher que lorsque le sentinel est effectivement pose et frais.
check("le message de retenue reste cache hors retenue",
      "$holdLabel.Visible = $false" in _shell, True)

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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all provisioning script checks passed")
