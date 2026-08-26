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

# Recursive: provision/assets/*.ps1 (run-agent.ps1, maximize-steam.ps1) are
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
# WinRM is configured early for debugging but stays firewalled until the end,
# so "5985 reachable" means "provisioning finished".
check("bootstrap keeps 5985 closed", "Disable-NetFirewallRule" in boot, True)
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
check("marker opens 5985", "Enable-NetFirewallRule" in marker, True)
check("marker clears the resume entry", "Remove-ItemProperty" in marker, True)
check("marker writes PROVISION.done", "PROVISION.done" in marker, True)
# The marker is what makes "5985 reachable" mean "guest is provisioned", so it
# must be written strictly before the firewall rule that opens 5985.
check("marker writes PROVISION.done before opening 5985",
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
check("provision version is B1", payload.PROVISION_VERSION, "B1")
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
             "run-agent.ps1"]:
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
# -Wait mentirait : Steam quitte son lanceur et continue dans un enfant, donc
# une deuxieme instance serait lancee sur un Steam bien vivant.
_shell_code = [ln for ln in _shell.splitlines()
               if ln.strip() and not ln.lstrip().startswith(("#", "<#", "»"))]
check("le lanceur interroge la table des processus, pas -Wait",
      "Get-Process" in _shell and
      not any("-Wait" in ln for ln in _shell_code), True)

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

# Au premier lancement Steam telecharge sa mise a jour avant d ouvrir une
# fenetre ; trente secondes n y suffisent pas et le client voit un bureau vide.
_max = (PROVISION / "assets" / "maximize-steam.ps1").read_text(encoding="utf-8")
check("maximize-steam.ps1 laisse a Steam le temps de sa premiere mise a jour",
      "AddSeconds(180)" in _max, True)

# Les lecteurs optiques s emparent des premieres lettres libres et se posent
# exactement la ou les partages doivent aller : mesure le 2026-08-26, les deux
# media d installation tenaient E: et F:. L etage doit les deplacer AVANT.
_sh = (PROVISION / "35-shares.ps1").read_text(encoding="utf-8")
check("35-shares.ps1 degage les lettres prises par les lecteurs optiques",
      "CD-ROM" in _sh and "mountvol" in _sh, True)
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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all provisioning script checks passed")
