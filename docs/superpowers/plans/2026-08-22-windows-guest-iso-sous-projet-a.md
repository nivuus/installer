# Générateur d'ISO Windows unattended (sous-projet A) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produire, depuis un média Windows 11 IoT Enterprise LTSC 2024 officiel et une clé produit, une installation Windows sans aucune interaction qui enchaîne un provisionnement en session 1, et répondre à la question HDR avant d'investir dans les sous-projets B, C et D.

**Architecture:** Un module Python `installer/windows-guest/` qui (1) inspecte le média LTSC pour refuser toute édition non conforme, (2) rend un `autounattend.xml` depuis un template Jinja2, (3) assemble une charge utile `/nivuus/` repérable par fichier marqueur, (4) grave le tout dans un **second ISO** que le programme d'installation Windows découvre seul. La logique pure (rendu, analyse, sélection, construction de commandes) est séparée des appels de sous-processus, ce qui la rend testable sans média Windows.

**Tech Stack:** Python 3.13.5 (bibliothèque standard uniquement pour la lecture du WIM), Jinja2 3.1.6, `xorriso` 1.5.6, `swtpm`, `qemu-img`, libvirt 11.3 / OVMF 4M. PowerShell 5.1 et C# (`Add-Type`) côté invité. **Aucune installation de paquet.**

**Spec:** `docs/superpowers/specs/2026-08-22-windows-guest-iso-design.md`

---

## Écart assumé vis-à-vis de la spec : deux médias au lieu d'une réinjection

La spec prévoyait de réinjecter `autounattend.xml` dans une copie du média Windows
par `xorriso`. **Cette voie est abandonnée**, sur mesure et non sur intuition :

```
$ xorriso -as mkisofs -help | grep -i udf     # → aucun résultat
$ xorriso -version | head -1
xorriso 1.5.6 : RockRidge filesystem manipulator, libburnia project.
```

`xorriso` 1.5.6 n'écrit **pas** d'UDF. Or l'`install.wim` d'un média Windows 11
24H2 dépasse 4 GiB, taille que seul l'UDF porte sur un média Windows : le pilote
CDFS de Windows ne lit pas les fichiers ISO9660 multi-extent. Une réinjection
produirait un média dont `install.wim` est illisible par le programme
d'installation, et le symptôme n'apparaîtrait qu'au premier démarrage réel.

**À la place** : le média LTSC reste **intact** (donc vérifiable par empreinte), et
un second ISO minuscule, `nivuus-unattend.iso`, porte `autounattend.xml` et la
charge utile. Le programme d'installation Windows balaie **la racine de tous les
lecteurs amovibles en lecture seule** à la recherche d'`autounattend.xml` — c'est
son ordre de recherche documenté, et le mécanisme même qui rend la manœuvre
possible. La VM reçoit deux lecteurs CD, ce que le domaine libvirt maîtrise déjà.

Bénéfices : le risque « réinjection UEFI » de la spec **disparaît** (on ne
fabrique plus aucun média amorçable), et le média LTSC — le seul artefact
irremplaçable du projet — n'est jamais réécrit.

Trois conséquences sur la table des fichiers de la spec :

- `build_media.py` devient `unattend_iso.py` (fabrication, plus réinjection) ;
- l'inspection WIM reste dans `media.py`, seule, pour tenir la règle des 200 lignes ;
- **pas de `__init__.py`** : `windows-guest` contient un trait d'union, donc n'est
  pas importable comme paquet. Les modules sont plats et le répertoire est ajouté
  à `sys.path`, exactement comme `install-engine/run.py` le fait pour `steps`.

## Le média, mesuré (2026-08-22)

Le média est arrivé pendant la rédaction de ce plan :
`/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso`
(5 144 817 664 o, `sha256 4f59662a96fc1da48c1b415d6c369d08af55ddd64e8f1c84e0166d9e50405d7a`).

Il a été inspecté, et **trois hypothèses du cadrage tombent** :

| Mesure | Conséquence |
| --- | --- |
| `sources/install.wim` = **4 332 322 774 o**, soit **au-dessus des 4 GiB** | confirme par la mesure, et non par déduction, que la réinjection `xorriso` était impossible |
| Le média porte **trois** images, toutes LTSC : `Windows 11 Enterprise LTSC 2024` (`EnterpriseS`), **`Windows 11 IoT Enterprise LTSC 2024` (`IoTEnterpriseS`)**, `Windows 11 IoT Enterprise Subscription LTSC 2024` (`IoTEnterpriseSK`) | la sélection par nom seul est trop fragile : **c'est `EDITIONID` qui tranche** |
| Le média est **`en-US` uniquement** (`<LANGUAGES><LANGUAGE>en-US</LANGUAGE>`) | le compte intégré s'appelle **`Administrator`**, pas `Administrateur` : une ouverture de session automatique visant `Administrateur` échouerait en silence, donc **pas de session 1, donc aucun provisionnement**. La langue du programme d'installation reste `en-US` ; seuls le clavier et les formats régionaux passent en `fr-FR` |

Toutes les images sont en build **26100**, `ARCH 9` (x86_64), état
`IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE`, ~18,4 Go décompressés.

Le tout a été lu **sans `wimtools`** : l'en-tête WIM pointe vers un bloc XML
UTF-16LE non compressé (`rhXmlData` à l'offset `0x48`). Le plan en tire parti et
**n'ajoute aucune dépendance apt**.

⚠️ `/media/backup` est monté `data=writeback,barrier=0` : garder l'empreinte
ci-dessus et la revérifier avant toute fabrication, ce média étant le seul
artefact irremplaçable du projet.

## Global Constraints

- **Cible unique** : Windows 11 IoT Enterprise LTSC 2024, build **26100**. Toute autre édition détectée dans le média est une **erreur bloquante**, jamais un repli silencieux.
- **200 lignes maximum par fichier** (règle `CLAUDE.md` du dépôt). Découper avant de dépasser.
- **Commentaires en anglais uniquement** (règle `CLAUDE.md`).
- **La clé produit n'entre JAMAIS dans le dépôt** ni dans une ligne de commande (elle fuirait dans `ps` et dans l'historique). Elle vit dans `/root/.config/nivuus/windows-ltsc.key` (mode 600), lue par `--key-file`. Aucun test, aucune fixture, aucun exemple ne contient de clé réelle — les tests utilisent `AAAAA-BBBBB-CCCCC-DDDDD-EEEEE`.
- **Installation entièrement hors-ligne** : tout binaire nécessaire est embarqué dans la charge utile. Aucun téléchargement pendant le provisionnement.
- **Le provisionnement passe par `FirstLogonCommands`, jamais par `SetupComplete.cmd`** — la session 0 est aveugle à l'affichage.
- **Aucune lettre de lecteur codée en dur** côté invité : la charge utile se trouve par balayage à la recherche du marqueur `\nivuus\PAYLOAD.id`.
- **Aucune nouvelle dépendance apt.** Les métadonnées du WIM sont lues directement (`media.py`) ; `xorriso`, `swtpm`, `qemu-img` et libvirt sont déjà installés. Le dépôt est en mode développement.
- Les tests suivent la convention du dépôt (`scripts/tests/test_*.py`) : script exécutable directement par `python3`, assertions simples, aucune fixture pytest, sortie `OK - ...` ou `FAIL (n)` + `sys.exit(1)`.

### Résolution d'une ambiguïté de la spec — le signal de fin

La spec décrit WinRM comme activé par `00-bootstrap.ps1` *et* comme le signal de
fin écrit par le dernier script. Les deux sont conservés sans se contredire :
`00-bootstrap.ps1` **configure** WinRM (indispensable pour déboguer un
provisionnement qui échoue en cours de route) mais **laisse sa règle de pare-feu
désactivée** ; `99-marker.ps1` **ouvre** le port 5985 en tout dernier geste.
« 5985 joignable » signifie donc bien « provisionnement terminé ». **Correction
(revue finale, 2026-08-22)** : l'hôte ne lit PAS le marqueur pour confirmer -
`wait_ready()` (tâche 7) retourne sur un simple connect TCP réussi sur 5985 et
ne parle jamais WinRM. `C:\nivuus\state\PROVISION.done` reste écrit et reste
utile, mais **uniquement en lecture manuelle** (runbook de la tâche 8) : lire
le marqueur et comparer son `provision_version` demande `pywinrm` et le mot de
passe Administrateur de l'invité, qui n'existe pas encore et revient au
propriétaire de choisir - ce n'est donc pas automatisé par ce sous-projet.

---

## Structure des fichiers

| Fichier | Responsabilité |
| --- | --- |
| `installer/windows-guest/autounattend.py` | validation des paramètres + rendu du fichier de réponses |
| `installer/windows-guest/templates/autounattend.xml.j2` | le fichier de réponses |
| `installer/windows-guest/media.py` | lecture des métadonnées XML du WIM, refus de toute édition non conforme |
| `installer/windows-guest/payload.py` | assemblage et vérification du répertoire `/nivuus/` |
| `installer/windows-guest/unattend_iso.py` | gravure et vérification de `nivuus-unattend.iso` |
| `installer/windows-guest/build.py` | ligne de commande qui enchaîne tout |
| `installer/windows-guest/testdomain.py` | domaine libvirt jetable + attente de disponibilité |
| `installer/windows-guest/templates/domain-test.xml.j2` | le domaine de test |
| `installer/windows-guest/provision/run-all.ps1` | ordonnanceur des étapes, reprenable après redémarrage |
| `installer/windows-guest/provision/00-bootstrap.ps1` | journalisation, politique d'exécution, WinRM (port fermé), reprise |
| `installer/windows-guest/provision/10-nvidia.ps1` | pilote NVIDIA |
| `installer/windows-guest/provision/20-sudovda.ps1` | pilote SudoVDA |
| `installer/windows-guest/provision/99-marker.ps1` | marqueur, fin de l'ouverture de session automatique, ouverture de 5985 |
| `installer/windows-guest/probe/AdvancedColor.cs` | P/Invoke `DisplayConfigGetDeviceInfo` |
| `installer/windows-guest/probe/advanced-color.ps1` | la sonde du test d'acceptation |
| `installer/windows-guest/README.md` | mode d'emploi et runbook d'acceptation |
| `scripts/tests/test_windows_guest_autounattend.py` | tâche 1 |
| `scripts/tests/test_windows_guest_media.py` | tâche 2 |
| `scripts/tests/test_windows_guest_payload.py` | tâche 3 |
| `scripts/tests/test_windows_guest_iso.py` | tâche 4 |
| `scripts/tests/test_windows_guest_provision.py` | tâche 5 |
| `scripts/tests/test_windows_guest_domain.py` | tâche 7 |

Préambule commun à tous les tests (le trait d'union interdit l'import par nom) :

```python
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))
```

---

### Task 1: Fichier de réponses `autounattend.xml`

**Files:**
- Create: `installer/windows-guest/autounattend.py`
- Create: `installer/windows-guest/templates/autounattend.xml.j2`
- Test: `scripts/tests/test_windows_guest_autounattend.py`

**Interfaces:**
- Consomme : rien (première tâche).
- Produit : `UnattendParams(product_key, admin_password, image_name, hostname="NIVUUS-WIN", setup_language="en-US", user_locale="fr-FR", autologon_count=5)`, `validate(p) -> None`, `render(p, templates_dir=TEMPLATES_DIR) -> str`, `UnattendError(ValueError)`, les constantes `PAYLOAD_MARKER` (`\nivuus\PAYLOAD.id`) et `DRIVE_LETTERS`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
#!/usr/bin/env python3
"""Tests for the Windows unattended answer file renderer.

Run: python3 scripts/tests/test_windows_guest_autounattend.py
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import autounattend as ua  # noqa: E402

NS = {"u": "urn:schemas-microsoft-com:unattend"}
IMAGE = "Windows 11 IoT Enterprise LTSC"
GOOD = dict(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
            admin_password="p4ssw0rd!", image_name=IMAGE)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def rejects(label, **overrides):
    params = ua.UnattendParams(**{**GOOD, **overrides})
    try:
        ua.validate(params)
    except ua.UnattendError:
        return
    failures.append(f"{label}: accepted what it must reject")


rejects("malformed key", product_key="AAAAA-BBBBB")
rejects("lowercase key", product_key="aaaaa-bbbbb-ccccc-ddddd-eeeee")
rejects("empty password", admin_password="")
rejects("hostname too long", hostname="NIVUUS-WINDOWS-2024")
rejects("hostname with space", hostname="NIVUUS WIN")
rejects("empty image name", image_name="   ")
rejects("autologon count too low", autologon_count=1)

xml_text = ua.render(ua.UnattendParams(**GOOD))
root = ET.fromstring(xml_text)

passes = [s.get("pass") for s in root.findall("u:settings", NS)]
check("settings passes", passes, ["windowsPE", "specialize", "oobeSystem"])

check("image name is injected", IMAGE in xml_text, True)
check("product key is injected", GOOD["product_key"] in xml_text, True)
check("EULA accepted", "<AcceptEula>true</AcceptEula>" in xml_text, True)
check("wipes disk 0", "<WillWipeDisk>true</WillWipeDisk>" in xml_text, True)
squished = xml_text.replace("\n", "").replace(" ", "")
check("EFI partition sized 260 MB",
      "<Type>EFI</Type><Size>260</Size>" in squished, True)
check("MSR partition sized 16 MB",
      "<Type>MSR</Type><Size>16</Size>" in squished, True)
check("data partition extends",
      "<Type>Primary</Type><Extend>true</Extend>" in squished, True)

# SetupComplete.cmd runs as SYSTEM in session 0, blind to the display.
check("never uses SetupComplete", "SetupComplete" in xml_text, False)

cmds = root.findall(".//u:FirstLogonCommands/u:SynchronousCommand", NS)
check("two first-logon commands", len(cmds), 2)
check("orders", [c.find("u:Order", NS).text for c in cmds], ["1", "2"])

launcher = cmds[0].find("u:CommandLine", NS).text
check("scans drives for the marker", "\\nivuus\\PAYLOAD.id" in launcher, True)
check("no hardcoded payload drive", "D:\\nivuus\\provision" in launcher, False)
check("launches run-all.ps1", "run-all.ps1" in launcher, True)

guard = cmds[1].find("u:CommandLine", NS).text
check("guard writes a loud failure marker",
      "NIVUUS-PAYLOAD-NOT-FOUND" in guard, True)

check("autologon enabled", "<AutoLogon>" in xml_text, True)
check("autologon count", "<LogonCount>5</LogonCount>" in xml_text, True)
# The medium is en-US: the built-in account is Administrator. Targeting
# "Administrateur" would make the automatic logon fail silently.
check("autologon targets the en-US built-in account",
      "<Username>Administrator</Username>" in xml_text, True)
check("setup stays en-US", "<UILanguage>en-US</UILanguage>" in xml_text, True)
check("regional formats are French",
      "<UserLocale>fr-FR</UserLocale>" in xml_text, True)
check("keyboard is French", "<InputLocale>fr-FR</InputLocale>" in xml_text, True)

# XML-special characters in a password must not corrupt the document.
amp = ua.render(ua.UnattendParams(**{**GOOD, "admin_password": "a&b<c>"}))
ET.fromstring(amp)
check("password is XML-escaped", "a&amp;b&lt;c&gt;" in amp, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all autounattend rendering tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_autounattend.py`
Expected: `ModuleNotFoundError: No module named 'autounattend'`

- [ ] **Step 3: Écrire le module**

`installer/windows-guest/autounattend.py` :

```python
"""Validation and rendering of the Windows 11 LTSC unattended answer file.

Pure logic: no subprocess, no writes outside the caller's hands, so the whole
module is testable without a Windows medium.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

KEY_RE = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}$")
# NetBIOS name: 15 characters maximum, letters, digits and hyphens.
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")

# The built-in administrator account of an en-US installation. A French
# medium would name it "Administrateur"; targeting the wrong name makes the
# automatic logon fail silently, and with it every session-1 provisioning step.
ADMIN_ACCOUNT = "Administrator"

# The install medium letter is unpredictable, so the guest scans every drive
# for this marker instead of assuming D:.
PAYLOAD_MARKER = r"\nivuus\PAYLOAD.id"
DRIVE_LETTERS = "C D E F G H I J K L M N O P Q R S T U V W X Y Z"


class UnattendError(ValueError):
    """Raised when answer-file parameters cannot produce a valid install."""


@dataclass(frozen=True)
class UnattendParams:
    product_key: str
    admin_password: str
    image_name: str
    hostname: str = "NIVUUS-WIN"
    # The LTSC 2024 medium is en-US only, so Setup itself must stay en-US;
    # only the keyboard and regional formats become French.
    setup_language: str = "en-US"
    user_locale: str = "fr-FR"
    # The NVIDIA driver reboots, so provisioning must survive at least two
    # extra logons before the automatic logon is turned off again.
    autologon_count: int = 5


def validate(params: UnattendParams) -> None:
    if not KEY_RE.match(params.product_key):
        raise UnattendError(
            "product key must look like XXXXX-XXXXX-XXXXX-XXXXX-XXXXX, "
            f"got {params.product_key!r}"
        )
    if not params.admin_password:
        raise UnattendError("administrator password must not be empty")
    if not HOSTNAME_RE.match(params.hostname):
        raise UnattendError(
            f"hostname must be 1-15 chars of [A-Za-z0-9-], got {params.hostname!r}"
        )
    if not params.image_name.strip():
        raise UnattendError("image name must not be empty")
    if params.autologon_count < 3:
        raise UnattendError("autologon count must be >= 3: provisioning reboots")


def render(params: UnattendParams, templates_dir: str = TEMPLATES_DIR) -> str:
    """Render the answer file. Values are XML-escaped; the template is not."""
    validate(params)
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(enabled_extensions=("j2",), default=True),
        keep_trailing_newline=True,
    )
    return env.get_template("autounattend.xml.j2").render(
        product_key=params.product_key,
        admin_password=params.admin_password,
        image_name=params.image_name,
        hostname=params.hostname,
        setup_language=params.setup_language,
        user_locale=params.user_locale,
        admin_account=ADMIN_ACCOUNT,
        autologon_count=params.autologon_count,
        drive_letters=DRIVE_LETTERS,
        payload_marker=PAYLOAD_MARKER,
    )
```

- [ ] **Step 4: Écrire le template**

`installer/windows-guest/templates/autounattend.xml.j2` :

```xml
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE"
               processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35"
               language="neutral" versionScope="nonSxS">
      <SetupUILanguage><UILanguage>{{ setup_language }}</UILanguage></SetupUILanguage>
      <UILanguage>{{ setup_language }}</UILanguage>
      <InputLocale>{{ user_locale }}</InputLocale>
      <SystemLocale>{{ user_locale }}</SystemLocale>
      <UserLocale>{{ user_locale }}</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup"
               processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35"
               language="neutral" versionScope="nonSxS">
      <DiskConfiguration>
        <WillShowUI>OnError</WillShowUI>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order><Type>EFI</Type><Size>260</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order><Type>MSR</Type><Size>16</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order><Type>Primary</Type><Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order><PartitionID>1</PartitionID>
              <Label>System</Label><Format>FAT32</Format>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order><PartitionID>2</PartitionID>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>3</Order><PartitionID>3</PartitionID>
              <Label>Windows</Label><Letter>C</Letter><Format>NTFS</Format>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/NAME</Key>
              <Value>{{ image_name }}</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
          <WillShowUI>OnError</WillShowUI>
        </OSImage>
      </ImageInstall>
      <UserData>
        <ProductKey>
          <Key>{{ product_key }}</Key>
          <WillShowUI>OnError</WillShowUI>
        </ProductKey>
        <AcceptEula>true</AcceptEula>
      </UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35"
               language="neutral" versionScope="nonSxS">
      <ComputerName>{{ hostname }}</ComputerName>
      <ProductKey>{{ product_key }}</ProductKey>
      <TimeZone>Romance Standard Time</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35"
               language="neutral" versionScope="nonSxS">
      <UserAccounts>
        <AdministratorPassword>
          <Value>{{ admin_password }}</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
      </UserAccounts>
      <AutoLogon>
        <Enabled>true</Enabled>
        <Username>{{ admin_account }}</Username>
        <LogonCount>{{ autologon_count }}</LogonCount>
        <Password>
          <Value>{{ admin_password }}</Value>
          <PlainText>true</PlainText>
        </Password>
      </AutoLogon>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
        <NetworkLocation>Work</NetworkLocation>
      </OOBE>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Description>Nivuus provisioning bootstrap</Description>
          <CommandLine>cmd /c "for %d in ({{ drive_letters }}) do @if exist %d:{{ payload_marker }} start /wait powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\nivuus\provision\run-all.ps1 -PayloadRoot %d:\nivuus"</CommandLine>
        </SynchronousCommand>
        <SynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Description>Fail loudly when the payload medium was not found</Description>
          <CommandLine>cmd /c "if not exist C:\nivuus\state\provision.started echo NIVUUS-PAYLOAD-NOT-FOUND: provisioning never ran &gt; C:\NIVUUS-PAYLOAD-NOT-FOUND.txt"</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
```

Deux détails qui coûtent cher s'ils sont faux :

- dans un `cmd /c` de ligne de commande, la variable de boucle s'écrit `%d`
  (un seul `%`) — c'est dans un fichier `.cmd` qu'il faudrait `%%d` ;
- le compte intégré s'appelle **`Administrator`**, le média étant `en-US`.
  `/usr/local/bin/winvm` vise `Administrateur` parce que l'invité actuel a été
  installé depuis un média français : **il faudra le pointer sur le nouveau nom**
  quand la migration aboutira.

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_autounattend.py`
Expected: `OK - all autounattend rendering tests passed`

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/autounattend.py \
        installer/windows-guest/templates/autounattend.xml.j2 \
        scripts/tests/test_windows_guest_autounattend.py
git commit -m "feat(windows-guest): render the unattended answer file"
```

---

### Task 2: Inspection du média LTSC

Le média est la seule chose qu'on ne peut pas reconstruire, et il porte **trois**
éditions LTSC dont une seule est la cible. L'édition est donc **lue**, jamais
supposée, et tout écart est bloquant.

Les métadonnées sont lues directement dans le WIM : son en-tête pointe vers un
bloc XML UTF-16LE non compressé. Aucun outil externe, donc aucune dépendance
nouvelle, et un analyseur entièrement testable sans média.

**Files:**
- Create: `installer/windows-guest/media.py`
- Test: `scripts/tests/test_windows_guest_media.py`

**Interfaces:**
- Consomme : rien.
- Produit : `read_wim_xml(wim_path) -> str`, `parse_wim_xml(xml_text) -> list[dict]` (clés `index:int`, `name`, `edition_id`, `build`, `languages:list[str]`), `select_ltsc_image(images, image_name=None) -> dict`, `inspect_iso(iso_path, mount_dir=MOUNT_DIR, image_name=None) -> dict`, `MediaError(RuntimeError)`, `TARGET_BUILD = "26100"`, `TARGET_EDITION_ID = "IoTEnterpriseS"`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
#!/usr/bin/env python3
"""Tests for Windows installation medium inspection.

The fixture is the real XML metadata of
en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso, trimmed of its
file counters: three LTSC editions, only one of which is the target.
Run: python3 scripts/tests/test_windows_guest_media.py
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import media  # noqa: E402


def image(index, name, edition, build="26100"):
    return (
        f'<IMAGE INDEX="{index}"><WINDOWS><ARCH>9</ARCH>'
        f"<EDITIONID>{edition}</EDITIONID>"
        "<INSTALLATIONTYPE>Client</INSTALLATIONTYPE>"
        "<LANGUAGES><LANGUAGE>en-US</LANGUAGE><DEFAULT>en-US</DEFAULT></LANGUAGES>"
        f"<VERSION><MAJOR>10</MAJOR><MINOR>0</MINOR><BUILD>{build}</BUILD>"
        "<SPBUILD>1742</SPBUILD></VERSION><SYSTEMROOT>WINDOWS</SYSTEMROOT>"
        f"</WINDOWS><FLAGS>{edition}</FLAGS><NAME>{name}</NAME>"
        f"<DESCRIPTION>{name}</DESCRIPTION></IMAGE>"
    )


REAL_MEDIUM = "﻿<WIM>" + "".join([
    image(1, "Windows 11 Enterprise LTSC 2024", "EnterpriseS"),
    image(2, "Windows 11 IoT Enterprise LTSC 2024", "IoTEnterpriseS"),
    image(3, "Windows 11 IoT Enterprise Subscription LTSC 2024", "IoTEnterpriseSK"),
]) + "</WIM>"

CONSUMER = "﻿<WIM>" + "".join([
    image(1, "Windows 11 Home", "Core"),
    image(2, "Windows 11 Pro", "Professional"),
]) + "</WIM>"

WRONG_BUILD = "﻿<WIM>" + image(
    1, "Windows 11 IoT Enterprise LTSC 2024", "IoTEnterpriseS", build="22631"
) + "</WIM>"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def refuses(label, images, must_mention):
    try:
        media.select_ltsc_image(images)
    except media.MediaError as exc:
        if must_mention not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} omits {must_mention!r}")
        return
    failures.append(f"{label}: accepted a medium it must refuse")


images = media.parse_wim_xml(REAL_MEDIUM)
check("three images parsed", len(images), 3)
check("index is an int", images[1]["index"], 2)
check("name", images[1]["name"], "Windows 11 IoT Enterprise LTSC 2024")
check("edition id", images[1]["edition_id"], "IoTEnterpriseS")
check("build", images[1]["build"], "26100")
check("languages", images[1]["languages"], ["en-US"])
check("BOM does not break parsing", images[0]["index"], 1)

chosen = media.select_ltsc_image(images)
check("selects IoT Enterprise LTSC, not plain Enterprise", chosen["index"], 2)
check("selects by edition id", chosen["edition_id"], "IoTEnterpriseS")

# The Subscription edition (IoTEnterpriseSK) needs subscription activation the
# purchased key does not provide: picking it would install something that
# deactivates later.
only_sub = [i for i in images if i["edition_id"] == "IoTEnterpriseSK"]
refuses("subscription edition alone", only_sub, "IoTEnterpriseSK")

refuses("consumer medium", media.parse_wim_xml(CONSUMER), "Windows 11 Pro")
refuses("wrong build", media.parse_wim_xml(WRONG_BUILD), "26100")
refuses("empty medium", [], "no image")

# --image-name is the way out if a future medium carries several IoT editions.
check("explicit name wins",
      media.select_ltsc_image(images,
                              image_name="Windows 11 IoT Enterprise LTSC 2024")["index"],
      2)
try:
    media.select_ltsc_image(images, image_name="Windows 11 Pro")
    failures.append("explicit name: accepted a name absent from the medium")
except media.MediaError:
    pass

# A file that is not a WIM must fail on its magic, not on a stack trace.
import tempfile  # noqa: E402

with tempfile.NamedTemporaryFile(suffix=".wim") as fh:
    fh.write(b"not a wim at all" * 16)
    fh.flush()
    try:
        media.read_wim_xml(fh.name)
        failures.append("read_wim_xml: accepted a file that is not a WIM")
    except media.MediaError:
        pass

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all medium inspection tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_media.py`
Expected: `ModuleNotFoundError: No module named 'media'`

- [ ] **Step 3: Écrire le module**

`installer/windows-guest/media.py` :

```python
"""Inspection of the Windows installation medium.

The edition is read from install.wim's own metadata, never assumed: the LTSC
2024 medium carries three editions - Enterprise LTSC, IoT Enterprise LTSC and
IoT Enterprise Subscription LTSC - and only IoTEnterpriseS is the target. A
medium that does not carry it is a hard error, never a silent fallback.

The WIM header points at an uncompressed UTF-16LE XML blob, so the whole thing
is readable with the standard library: no wimtools, no new apt dependency, and
a parser that is testable without a medium.
"""
from __future__ import annotations

import os
import struct
import subprocess
import xml.etree.ElementTree as ET

TARGET_BUILD = "26100"
TARGET_EDITION_ID = "IoTEnterpriseS"
MOUNT_DIR = "/run/nivuus-winmedia"

WIM_MAGIC = b"MSWIM\x00\x00\x00"
# rhXmlData in the WIM header: 7-byte size, 1 flag byte, then a u64 offset.
XML_RESHDR_OFFSET = 0x48


class MediaError(RuntimeError):
    """Raised when the Windows medium is not the expected LTSC release."""


def read_wim_xml(wim_path: str) -> str:
    """Return the XML metadata blob stored at the end of a WIM archive."""
    with open(wim_path, "rb") as fh:
        header = fh.read(0x60)
        if header[:8] != WIM_MAGIC:
            raise MediaError(f"{wim_path} is not a WIM archive")
        reshdr = header[XML_RESHDR_OFFSET:XML_RESHDR_OFFSET + 24]
        size = int.from_bytes(reshdr[0:7], "little")
        offset = struct.unpack_from("<Q", reshdr, 8)[0]
        fh.seek(offset)
        blob = fh.read(size)
    if len(blob) != size:
        raise MediaError(f"{wim_path} is truncated: XML metadata unreadable")
    return blob.decode("utf-16-le")


def parse_wim_xml(xml_text: str) -> list[dict]:
    """Parse the WIM metadata into one record per image."""
    try:
        root = ET.fromstring(xml_text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise MediaError(f"unreadable WIM metadata: {exc}") from exc
    images = []
    for img in root.findall("IMAGE"):
        images.append({
            "index": int(img.get("INDEX", "0")),
            "name": (img.findtext("NAME") or "").strip(),
            "edition_id": (img.findtext("WINDOWS/EDITIONID") or "").strip(),
            "build": (img.findtext("WINDOWS/VERSION/BUILD") or "").strip(),
            "languages": [e.text for e in img.findall("WINDOWS/LANGUAGES/LANGUAGE")],
        })
    return images


def _describe(images: list[dict]) -> str:
    return ", ".join(
        f"#{i['index']} {i.get('name', '?')} ({i.get('edition_id', '?')})"
        for i in images
    )


def select_ltsc_image(images: list[dict], image_name: str | None = None) -> dict:
    """Return the IoT Enterprise LTSC image, or raise naming what was found."""
    if not images:
        raise MediaError("no image found in install.wim - is this a Windows medium?")
    if image_name is not None:
        candidates = [i for i in images if i.get("name") == image_name]
        if not candidates:
            raise MediaError(
                f"no image named {image_name!r} on this medium; found: "
                + _describe(images)
            )
    else:
        # Edition ID, not the display name: "IoT Enterprise Subscription LTSC"
        # reads like the target but is IoTEnterpriseSK, which needs a
        # subscription the purchased key does not carry.
        candidates = [i for i in images
                      if i.get("edition_id") == TARGET_EDITION_ID]
        if not candidates:
            raise MediaError(
                f"no {TARGET_EDITION_ID} image on this medium; found: "
                + _describe(images)
            )
    if len(candidates) > 1:
        raise MediaError(
            "several matching images, pick one with --image-name: "
            + _describe(candidates)
        )
    chosen = candidates[0]
    build = chosen.get("build", "?")
    if build != TARGET_BUILD:
        raise MediaError(
            f"image {chosen.get('name')!r} is build {build}, expected "
            f"{TARGET_BUILD} (Windows 11 24H2) - HDR needs the 24H2 base"
        )
    return chosen


def inspect_iso(iso_path: str, mount_dir: str = MOUNT_DIR,
                image_name: str | None = None) -> dict:
    """Loop-mount the ISO read-only and return its target image record."""
    if os.geteuid() != 0:
        raise MediaError("inspecting the medium requires root (loop mount)")
    os.makedirs(mount_dir, exist_ok=True)
    subprocess.run(["mount", "-o", "loop,ro", iso_path, mount_dir], check=True)
    try:
        wim = os.path.join(mount_dir, "sources", "install.wim")
        if not os.path.exists(wim):
            esd = os.path.join(mount_dir, "sources", "install.esd")
            if os.path.exists(esd):
                raise MediaError(
                    "this medium ships sources/install.esd (retail image); the "
                    "volume LTSC medium ships sources/install.wim"
                )
            raise MediaError(f"no sources/install.wim in {iso_path}")
        return select_ltsc_image(parse_wim_xml(read_wim_xml(wim)), image_name)
    finally:
        subprocess.run(["umount", mount_dir], check=False)
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_media.py`
Expected: `OK - all medium inspection tests passed`

- [ ] **Step 5: Vérifier sur le média réel**

```bash
sudo python3 -c "
import sys; sys.path.insert(0, 'installer/windows-guest')
import media
print(media.inspect_iso('/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso'))
"
```

Expected: `{'index': 2, 'name': 'Windows 11 IoT Enterprise LTSC 2024', 'edition_id': 'IoTEnterpriseS', 'build': '26100', 'languages': ['en-US']}`

- [ ] **Step 6: Commit**

```bash
git add installer/windows-guest/media.py scripts/tests/test_windows_guest_media.py
git commit -m "feat(windows-guest): pick IoTEnterpriseS out of the three LTSC editions"
```

---

### Task 3: Charge utile `/nivuus/`

L'installation est **entièrement hors-ligne** : ce que la charge utile ne porte
pas n'existera jamais dans l'invité. Un binaire manquant doit donc faire échouer
la fabrication, jamais l'installation.

**Files:**
- Create: `installer/windows-guest/payload.py`
- Test: `scripts/tests/test_windows_guest_payload.py`

**Interfaces:**
- Consomme : rien.
- Produit : `PayloadSources(provision_dir, probe_dir, drivers_dir)` (chemins `pathlib.Path`), `missing_binaries(drivers_dir) -> list[str]`, `plan_payload(sources) -> list[tuple[Path, str]]`, `marker_text(image_name, build_id) -> str`, `parse_marker(text) -> dict`, `stage_payload(dest_root, sources, marker) -> None`, `verify_staged(dest_root) -> None`, `PayloadError(RuntimeError)`, `MARKER_NAME = "PAYLOAD.id"`, `PROVISION_VERSION = "A1"`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
#!/usr/bin/env python3
"""Tests for the offline payload staged onto the unattend ISO.

Run: python3 scripts/tests/test_windows_guest_payload.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import payload  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def make_tree(root: pathlib.Path, *, with_nvidia=True, with_sudovda=True):
    (root / "provision").mkdir(parents=True)
    (root / "provision" / "run-all.ps1").write_text("# run-all\n")
    (root / "provision" / "00-bootstrap.ps1").write_text("# bootstrap\n")
    (root / "probe").mkdir()
    (root / "probe" / "advanced-color.ps1").write_text("# probe\n")
    drivers = root / "drivers"
    (drivers / "nvidia").mkdir(parents=True)
    (drivers / "sudovda").mkdir(parents=True)
    if with_nvidia:
        (drivers / "nvidia" / "580.00-desktop-win11-64bit.exe").write_bytes(b"MZ")
    if with_sudovda:
        (drivers / "sudovda" / "SudoVDA.inf").write_text("[Version]\n")
        (drivers / "sudovda" / "install.bat").write_text("@echo off\n")
    return payload.PayloadSources(provision_dir=root / "provision",
                                  probe_dir=root / "probe",
                                  drivers_dir=drivers)


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src")

    check("nothing missing on a complete tree",
          payload.missing_binaries(sources.drivers_dir), [])

    plan = dict(payload.plan_payload(sources))
    dests = sorted(plan.values())
    check("provision scripts are staged",
          "provision/run-all.ps1" in dests, True)
    check("probe is staged", "probe/advanced-color.ps1" in dests, True)
    check("nvidia driver is staged",
          "drivers/nvidia/580.00-desktop-win11-64bit.exe" in dests, True)
    check("sudovda inf is staged", "drivers/sudovda/SudoVDA.inf" in dests, True)
    check("no absolute destination",
          any(d.startswith("/") for d in dests), False)

    dest = root / "staging" / "nivuus"
    marker = payload.marker_text("Windows 11 IoT Enterprise LTSC 2024",
                                "20260822-1200")
    payload.stage_payload(dest, sources, marker)
    payload.verify_staged(dest)
    check("marker written", (dest / payload.MARKER_NAME).exists(), True)

    parsed = payload.parse_marker((dest / payload.MARKER_NAME).read_text())
    check("marker target build", parsed["target_build"], "26100")
    check("marker provision version",
          parsed["provision_version"], payload.PROVISION_VERSION)
    check("marker image name",
          parsed["image_name"], "Windows 11 IoT Enterprise LTSC 2024")

    # A staged tree missing its marker is a broken payload, not a warning.
    (dest / payload.MARKER_NAME).unlink()
    try:
        payload.verify_staged(dest)
        failures.append("verify_staged: accepted a payload with no marker")
    except payload.PayloadError:
        pass

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    sources = make_tree(root / "src", with_nvidia=False, with_sudovda=False)
    missing = payload.missing_binaries(sources.drivers_dir)
    check("both binaries reported missing", len(missing), 2)
    check("nvidia named in the report",
          any("nvidia" in m.lower() for m in missing), True)
    check("sudovda named in the report",
          any("sudovda" in m.lower() for m in missing), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all payload staging tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_payload.py`
Expected: `ModuleNotFoundError: No module named 'payload'`

- [ ] **Step 3: Écrire le module**

`installer/windows-guest/payload.py` :

```python
"""Assembly and verification of the offline /nivuus payload.

Everything the guest will ever need must be here: provisioning runs with no
network at all. A missing binary fails the build, never the install.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MARKER_NAME = "PAYLOAD.id"
PROVISION_VERSION = "A1"
TARGET_BUILD = "26100"


class PayloadError(RuntimeError):
    """Raised when the payload is incomplete or cannot be staged."""


@dataclass(frozen=True)
class PayloadSources:
    provision_dir: Path
    probe_dir: Path
    drivers_dir: Path


def missing_binaries(drivers_dir: Path) -> list[str]:
    """Return a human-readable list of the offline binaries not provided."""
    missing = []
    if not list((drivers_dir / "nvidia").glob("*.exe")):
        missing.append(
            f"NVIDIA driver installer (*.exe) in {drivers_dir / 'nvidia'}"
        )
    if not (drivers_dir / "sudovda" / "SudoVDA.inf").exists():
        missing.append(
            "SudoVDA driver package (SudoVDA.inf, install.bat, sudovda.cer) in "
            f"{drivers_dir / 'sudovda'} - copy it from a machine's "
            r"C:\Program Files\Apollo\drivers\sudovda"
        )
    return missing


def _walk(src_dir: Path, prefix: str) -> list[tuple[Path, str]]:
    entries = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            entries.append((path, f"{prefix}/{path.relative_to(src_dir).as_posix()}"))
    return entries


def plan_payload(sources: PayloadSources) -> list[tuple[Path, str]]:
    """Map each source file to its destination path relative to /nivuus."""
    return (_walk(sources.provision_dir, "provision")
            + _walk(sources.probe_dir, "probe")
            + _walk(sources.drivers_dir, "drivers"))


def marker_text(image_name: str, build_id: str) -> str:
    return (
        "nivuus_payload=1\n"
        f"target_build={TARGET_BUILD}\n"
        f"provision_version={PROVISION_VERSION}\n"
        f"image_name={image_name}\n"
        f"build_id={build_id}\n"
    )


def parse_marker(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def stage_payload(dest_root: Path, sources: PayloadSources, marker: str) -> None:
    """Copy the payload under dest_root (the future /nivuus of the ISO)."""
    missing = missing_binaries(sources.drivers_dir)
    if missing:
        raise PayloadError(
            "offline payload incomplete, refusing to build:\n  - "
            + "\n  - ".join(missing)
        )
    if dest_root.exists():
        shutil.rmtree(dest_root)
    for src, rel in plan_payload(sources):
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (dest_root / MARKER_NAME).write_text(marker)


def verify_staged(dest_root: Path) -> None:
    """Fail loudly on anything the guest bootstrap depends on being there."""
    required = [
        MARKER_NAME,
        "provision/run-all.ps1",
        "provision/00-bootstrap.ps1",
        "probe/advanced-color.ps1",
    ]
    for rel in required:
        path = dest_root / rel
        if not path.is_file() or path.stat().st_size == 0:
            raise PayloadError(f"staged payload is missing or empty: {rel}")
    if not list((dest_root / "drivers" / "nvidia").glob("*.exe")):
        raise PayloadError("staged payload has no NVIDIA driver installer")
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_payload.py`
Expected: `OK - all payload staging tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/windows-guest/payload.py scripts/tests/test_windows_guest_payload.py
git commit -m "feat(windows-guest): stage the offline provisioning payload"
```

---

### Task 4: Gravure de `nivuus-unattend.iso`

L'ISO produite n'est **pas amorçable** : elle n'est là que pour être lue. Le
programme d'installation Windows, démarré depuis le média LTSC, balaie la racine
de tous les lecteurs amovibles en lecture seule et y trouve `autounattend.xml`.

**Files:**
- Create: `installer/windows-guest/unattend_iso.py`
- Test: `scripts/tests/test_windows_guest_iso.py`

**Interfaces:**
- Consomme : rien.
- Produit : `xorriso_command(staging_dir, output_iso, volid=VOLID) -> list[str]`, `build_iso(staging_dir, output_iso) -> None`, `list_iso(iso_path) -> list[str]`, `verify_iso(iso_path) -> None`, `IsoError(RuntimeError)`, `VOLID = "NIVUUS_UA"`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
#!/usr/bin/env python3
"""Tests for the secondary unattend ISO.

Builds a real (tiny) ISO with xorriso, which must be installed.
Run: python3 scripts/tests/test_windows_guest_iso.py
"""
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import unattend_iso as ui  # noqa: E402

if shutil.which("xorriso") is None:
    print("FAIL (1)\n  - xorriso is not installed")
    sys.exit(1)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


cmd = ui.xorriso_command("/stage", "/out.iso")
check("uses mkisofs emulation", cmd[:3], ["xorriso", "-as", "mkisofs"])
# ISO level 3 lifts the 4 GiB single-file limit; Joliet is what Windows reads.
check("iso level 3", "-iso-level" in cmd and cmd[cmd.index("-iso-level") + 1] == "3", True)
check("joliet", "-J" in cmd, True)
check("rock ridge", "-rational-rock" in cmd, True)
check("volume id", cmd[cmd.index("-volid") + 1], ui.VOLID)
check("output last-but-one", cmd[-2:], ["/out.iso", "/stage"])

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    stage = root / "stage"
    (stage / "nivuus" / "provision").mkdir(parents=True)
    (stage / "autounattend.xml").write_text("<unattend/>\n")
    (stage / "nivuus" / "PAYLOAD.id").write_text("nivuus_payload=1\n")
    (stage / "nivuus" / "provision" / "run-all.ps1").write_text("# run-all\n")

    iso = root / "nivuus-unattend.iso"
    ui.build_iso(stage, iso)
    check("iso exists", iso.is_file(), True)

    listing = ui.list_iso(iso)
    check("answer file at the root", "/autounattend.xml" in listing, True)
    check("marker present", "/nivuus/PAYLOAD.id" in listing, True)
    check("provision script present",
          "/nivuus/provision/run-all.ps1" in listing, True)
    check("paths are unquoted", any(p.startswith("'") for p in listing), False)

    ui.verify_iso(iso)

    # An ISO without the answer file at its root is useless: Setup would ask
    # every question by hand. That must fail here, not at first boot.
    bad_stage = root / "bad"
    (bad_stage / "nivuus").mkdir(parents=True)
    (bad_stage / "nivuus" / "PAYLOAD.id").write_text("nivuus_payload=1\n")
    bad_iso = root / "bad.iso"
    ui.build_iso(bad_stage, bad_iso)
    try:
        ui.verify_iso(bad_iso)
        failures.append("verify_iso: accepted an ISO with no autounattend.xml")
    except ui.IsoError:
        pass

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all unattend ISO tests passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_iso.py`
Expected: `ModuleNotFoundError: No module named 'unattend_iso'`

- [ ] **Step 3: Écrire le module**

`installer/windows-guest/unattend_iso.py` :

```python
"""Build the small secondary ISO that Windows Setup reads the answer file from.

The Windows medium itself is never modified. Setup searches the root of every
removable read-only drive for autounattend.xml, so a second CD-ROM can carry
both the answer file and the offline payload.

Why not reinject into the Windows medium, as first designed: xorriso 1.5.6 has
no UDF support (verified: `xorriso -as mkisofs -help | grep -i udf` is empty),
and a Windows 11 24H2 install.wim exceeds 4 GiB - a size only UDF carries on a
Windows medium, since Windows' CDFS driver cannot read ISO9660 multi-extent
files. Rebuilding the medium would produce an install.wim Setup cannot read.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

VOLID = "NIVUUS_UA"


class IsoError(RuntimeError):
    """Raised when the unattend ISO cannot be built or is unusable."""


def xorriso_command(staging_dir, output_iso, volid: str = VOLID) -> list[str]:
    """Build the xorriso argument list. Pure, so it can be asserted on."""
    return [
        "xorriso", "-as", "mkisofs",
        "-iso-level", "3",       # lifts the 4 GiB single-file limit
        "-J", "-joliet-long",    # Joliet is the tree Windows reads
        "-rational-rock",
        "-volid", volid,
        "-o", str(output_iso), str(staging_dir),
    ]


def build_iso(staging_dir, output_iso, volid: str = VOLID) -> None:
    cmd = xorriso_command(staging_dir, output_iso, volid)
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise IsoError(f"xorriso failed ({proc.returncode}): {proc.stderr.strip()}")


def list_iso(iso_path) -> list[str]:
    """Return the absolute path of every file in the ISO."""
    proc = subprocess.run(
        ["xorriso", "-indev", str(iso_path), "-find", "/", "-type", "f"],
        text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise IsoError(f"cannot read {iso_path}: {proc.stderr.strip()}")
    # xorriso quotes every path it prints: 'path'
    return [line.strip().strip("'") for line in proc.stdout.splitlines()
            if line.strip().startswith("'")]


def verify_iso(iso_path) -> None:
    """Assert the ISO carries what Setup and the guest bootstrap depend on."""
    listing = list_iso(iso_path)
    for required in ("/autounattend.xml", "/nivuus/PAYLOAD.id",
                     "/nivuus/provision/run-all.ps1"):
        if required not in listing:
            raise IsoError(
                f"{Path(iso_path).name} is missing {required} "
                f"({len(listing)} files present)"
            )
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_iso.py`
Expected: `OK - all unattend ISO tests passed`

- [ ] **Step 5: Commit**

```bash
git add installer/windows-guest/unattend_iso.py scripts/tests/test_windows_guest_iso.py
git commit -m "feat(windows-guest): build the secondary unattend ISO"
```

---

### Task 5: Provisionnement de l'invité et sonde HDR

Le minimum strict pour répondre à la question HDR : pilote NVIDIA (c'est lui qui
porte la pile Advanced Color), SudoVDA, et la sonde. Apollo, Steam et l'agent
Guacamole relèvent du sous-projet B.

**Point de conception non évident** : `FirstLogonCommands` ne s'exécute qu'une
fois, or l'installation du pilote NVIDIA redémarre la machine. Le
provisionnement est donc **reprenable** : `00-bootstrap.ps1` dépose
`C:\nivuus\resume.cmd` — qui rebalaie les lecteurs, sans lettre codée en dur — et
l'enregistre dans `HKLM\...\CurrentVersion\Run` ; chaque étape pose un fichier
`.done` que `run-all.ps1` respecte ; `99-marker.ps1` retire l'entrée `Run`.

**Files:**
- Create: `installer/windows-guest/provision/run-all.ps1`
- Create: `installer/windows-guest/provision/00-bootstrap.ps1`
- Create: `installer/windows-guest/provision/10-nvidia.ps1`
- Create: `installer/windows-guest/provision/20-sudovda.ps1`
- Create: `installer/windows-guest/provision/99-marker.ps1`
- Create: `installer/windows-guest/probe/AdvancedColor.cs`
- Create: `installer/windows-guest/probe/advanced-color.ps1`
- Test: `scripts/tests/test_windows_guest_provision.py`

**Interfaces:**
- Consomme : `payload.PROVISION_VERSION` (tâche 3) — la valeur écrite dans le marqueur `C:\nivuus\state\PROVISION.done`.
- Produit : côté invité, `C:\nivuus\state\PROVISION.done` (lu par `testdomain.py wait-ready`, tâche 7) et le port 5985 ouvert ; côté C#, `[NivuusAdvancedColor]::Run() -> string[]`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
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

STAGES = ["00-bootstrap.ps1", "10-nvidia.ps1", "20-sudovda.ps1", "99-marker.ps1"]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


for name in STAGES + ["run-all.ps1"]:
    check(f"{name} exists", (PROVISION / name).is_file(), True)
for name in ["AdvancedColor.cs", "advanced-color.ps1"]:
    check(f"{name} exists", (PROBE / name).is_file(), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)

texts = {p.name: p.read_text(encoding="utf-8")
         for p in list(PROVISION.iterdir()) + list(PROBE.iterdir()) if p.is_file()}

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
check("marker disables autologon", "AutoAdminLogon" in marker, True)
check("marker clears the resume entry", "Remove-ItemProperty" in marker, True)
check("marker writes PROVISION.done", "PROVISION.done" in marker, True)

nvidia = texts["10-nvidia.ps1"]
check("nvidia installs silently", "-noreboot" in nvidia, True)
check("nvidia verifies the device afterwards", "Get-PnpDevice" in nvidia, True)

sudovda = texts["20-sudovda.ps1"]
check("sudovda trusts the publisher certificate", "TrustedPublisher" in sudovda, True)
check("sudovda verifies the device afterwards", "ROOT\\DISPLAY" in sudovda, True)

cs = texts["AdvancedColor.cs"]
for symbol in ("GetDisplayConfigBufferSizes", "QueryDisplayConfig",
               "DisplayConfigGetDeviceInfo", "QDC_ONLY_ACTIVE_PATHS"):
    check(f"probe uses {symbol}", symbol in cs, True)
check("probe reports bits per colour", "bitsPerColorChannel" in cs, True)
check("probe output matches the reference format",
      "target={0} rc={1} supported={2} enabled={3} bpc={4}" in cs, True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all provisioning script checks passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_provision.py`
Expected: `FAIL (7)` listant les fichiers absents

- [ ] **Step 3: Écrire `run-all.ps1`**

```powershell
<#
    Nivuus guest provisioning entry point.

    Runs in session 1, launched by FirstLogonCommands and re-launched after each
    reboot by C:\nivuus\resume.cmd. Never SetupComplete.cmd: that runs as SYSTEM
    in session 0, which is blind to the display.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
Set-Content -Path (Join-Path $StateDir 'provision.started') -Value (Get-Date -Format o)

Start-Transcript -Path 'C:\nivuus\provision.log' -Append | Out-Null
try {
    $stages = @('00-bootstrap.ps1', '10-nvidia.ps1', '20-sudovda.ps1', '99-marker.ps1')
    foreach ($stage in $stages) {
        $done = Join-Path $StateDir "$stage.done"
        if (Test-Path $done) {
            Write-Host "skip $stage (already done)"
            continue
        }
        $script = Join-Path $PayloadRoot "provision\$stage"
        if (-not (Test-Path $script)) { throw "missing provisioning stage: $script" }
        Write-Host "=== $stage ==="
        & $script -PayloadRoot $PayloadRoot
        Set-Content -Path $done -Value (Get-Date -Format o)
    }
    Write-Host 'provisioning complete'
}
finally {
    Stop-Transcript | Out-Null
}
```

- [ ] **Step 4: Écrire `00-bootstrap.ps1`**

```powershell
<#
    Stage 00: logging, execution policy, WinRM (firewalled), reboot resume.

    WinRM is configured now so a failed provisioning can still be debugged, but
    its firewall rule stays disabled: 99-marker.ps1 opens port 5985 as the very
    last gesture, which is what makes "5985 reachable" mean "guest is ready".
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope LocalMachine -Force

# Resume after the reboots the driver installers trigger. The payload drive
# letter can change between boots, so the resume script rescans for the marker.
$resume = 'C:\nivuus\resume.cmd'
$body = @(
    '@echo off',
    'for %%d in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do @if exist %%d:\nivuus\PAYLOAD.id powershell.exe -NoProfile -ExecutionPolicy Bypass -File %%d:\nivuus\provision\run-all.ps1 -PayloadRoot %%d:\nivuus'
)
New-Item -ItemType Directory -Force -Path 'C:\nivuus' | Out-Null
Set-Content -Path $resume -Value $body -Encoding ASCII
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                 -Name 'NivuusProvision' -Value ('cmd /c "' + $resume + '"')

Enable-PSRemoting -Force -SkipNetworkProfileCheck
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Disable-NetFirewallRule

Write-Host "bootstrap done, payload at $PayloadRoot"
```

- [ ] **Step 5: Écrire `10-nvidia.ps1`**

```powershell
<#
    Stage 10: NVIDIA display driver.

    Mandatory before the HDR probe means anything: the Advanced Color stack is
    the driver's, not the OS's. Installed offline from the payload; the
    installer reboots on its own and run-all.ps1 resumes afterwards.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$installer = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\nvidia') -Filter '*.exe' |
             Sort-Object Name | Select-Object -First 1
if (-not $installer) { throw "no NVIDIA installer in $PayloadRoot\drivers\nvidia" }

Write-Host "installing $($installer.Name)"
$proc = Start-Process -FilePath $installer.FullName `
                      -ArgumentList '-s', '-noreboot', '-clean' `
                      -Wait -PassThru
# NVIDIA's silent installer returns 0 or 1 on success; anything else is a failure.
if ($proc.ExitCode -notin @(0, 1)) { throw "NVIDIA installer exited $($proc.ExitCode)" }

$gpu = Get-PnpDevice -Class Display | Where-Object { $_.FriendlyName -match 'NVIDIA' }
if (-not $gpu) { throw 'no NVIDIA display device after installing the driver' }
if ($gpu.Status -ne 'OK') { throw "NVIDIA device status is $($gpu.Status)" }
Write-Host "NVIDIA device OK: $($gpu.FriendlyName)"
```

- [ ] **Step 6: Écrire `20-sudovda.ps1`**

```powershell
<#
    Stage 20: SudoVDA virtual display driver.

    SudoVDA is what lets a client stream at its own resolution; it is also the
    component whose HDR support requires the 24H2 base this whole migration is
    about. The package is the one Apollo ships in drivers\sudovda.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$dir = Join-Path $PayloadRoot 'drivers\sudovda'
$cert = Join-Path $dir 'sudovda.cer'
if (-not (Test-Path $cert)) { throw "missing $cert" }

# The driver is self-signed: without the certificate in both stores, the
# unattended install would sit on a trust prompt no one can answer.
certutil.exe -addstore -f Root $cert | Out-Null
certutil.exe -addstore -f TrustedPublisher $cert | Out-Null

$install = Join-Path $dir 'install.bat'
if (-not (Test-Path $install)) { throw "missing $install" }
$proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $install `
                      -WorkingDirectory $dir -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "SudoVDA install.bat exited $($proc.ExitCode)" }

$vda = Get-PnpDevice -InstanceId 'ROOT\DISPLAY\*' -ErrorAction SilentlyContinue |
       Where-Object { $_.Status -eq 'OK' }
if (-not $vda) { throw 'no working ROOT\DISPLAY device after installing SudoVDA' }
Write-Host "SudoVDA OK: $($vda.InstanceId)"
```

- [ ] **Step 7: Écrire `99-marker.ps1`**

```powershell
<#
    Stage 99: close the provisioning.

    Order matters: everything else must be true before port 5985 opens, because
    the host treats a reachable 5985 as "the guest is provisioned".
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'

# Keep the probe on C: so it can be run again without the payload medium.
Copy-Item -Path (Join-Path $PayloadRoot 'probe') -Destination 'C:\nivuus\probe' `
          -Recurse -Force

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $winlogon -Name 'AutoAdminLogon' -Value '0'
Remove-ItemProperty -Path $winlogon -Name 'DefaultPassword' -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' `
                    -Name 'NivuusProvision' -ErrorAction SilentlyContinue

$marker = @(
    "provision_version=A1",
    "completed=$(Get-Date -Format o)",
    "computer=$env:COMPUTERNAME"
)
Set-Content -Path (Join-Path $StateDir 'PROVISION.done') -Value $marker -Encoding ASCII

Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' | Enable-NetFirewallRule
Write-Host 'provisioning marker written, WinRM reachable'
```

- [ ] **Step 8: Écrire `probe/AdvancedColor.cs`**

```csharp
// Advanced Color (HDR) probe for the Nivuus Windows guest.
//
// Reference measurement on the outgoing Windows Server 2022 guest:
//     sizes rc=0 paths=1 modes=2
//     target=24832 rc=31 supported=0 enabled=0 bpc=0
// rc=31 is ERROR_GEN_FAILURE. The acceptance test of sub-project A is the same
// probe reporting supported=1 and bpc>=10 on Windows 11 LTSC 26100.
//
// Must run in session 1: in session 0 QueryDisplayConfig reports zero paths.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class NivuusAdvancedColor
{
    const uint QDC_ONLY_ACTIVE_PATHS = 0x00000002;
    const uint INFO_TYPE_ADVANCED_COLOR = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct LUID { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    struct RATIONAL { public uint Numerator; public uint Denominator; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_SOURCE_INFO
    {
        public LUID adapterId; public uint id; public uint modeInfoIdx;
        public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_TARGET_INFO
    {
        public LUID adapterId; public uint id; public uint modeInfoIdx;
        public uint outputTechnology; public uint rotation; public uint scaling;
        public RATIONAL refreshRate; public uint scanLineOrdering;
        public int targetAvailable; public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_INFO
    {
        public PATH_SOURCE_INFO sourceInfo;
        public PATH_TARGET_INFO targetInfo;
        public uint flags;
    }

    // DISPLAYCONFIG_MODE_INFO is a 64-byte union we never read: 16 bytes of
    // header plus a 48-byte payload kept as blittable words so the array
    // marshals without any per-field marshalling rules.
    [StructLayout(LayoutKind.Sequential)]
    struct MODE_INFO
    {
        public uint infoType; public uint id; public LUID adapterId;
        public ulong u0, u1, u2, u3, u4, u5;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct DEVICE_INFO_HEADER
    {
        public uint type; public uint size; public LUID adapterId; public uint id;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ADVANCED_COLOR_INFO
    {
        public DEVICE_INFO_HEADER header;
        public uint value;            // bit 0: supported, bit 1: enabled
        public uint colorEncoding;
        public uint bitsPerColorChannel;
    }

    [DllImport("user32.dll")]
    static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPath,
                                                  out uint numMode);

    [DllImport("user32.dll")]
    static extern int QueryDisplayConfig(uint flags, ref uint numPath,
                                         [Out] PATH_INFO[] paths, ref uint numMode,
                                         [Out] MODE_INFO[] modes, IntPtr topologyId);

    [DllImport("user32.dll")]
    static extern int DisplayConfigGetDeviceInfo(ref ADVANCED_COLOR_INFO info);

    public static string[] Run()
    {
        var lines = new List<string>();
        uint numPath, numMode;
        int rc = GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out numPath,
                                             out numMode);
        lines.Add(string.Format("sizes rc={0} paths={1} modes={2}", rc, numPath,
                                numMode));
        if (rc != 0 || numPath == 0) { return lines.ToArray(); }

        var paths = new PATH_INFO[numPath];
        var modes = new MODE_INFO[numMode];
        rc = QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref numPath, paths,
                                ref numMode, modes, IntPtr.Zero);
        if (rc != 0) { lines.Add("query rc=" + rc); return lines.ToArray(); }

        for (uint i = 0; i < numPath; i++)
        {
            var info = new ADVANCED_COLOR_INFO();
            info.header.type = INFO_TYPE_ADVANCED_COLOR;
            info.header.size = (uint)Marshal.SizeOf(typeof(ADVANCED_COLOR_INFO));
            info.header.adapterId = paths[i].targetInfo.adapterId;
            info.header.id = paths[i].targetInfo.id;
            int grc = DisplayConfigGetDeviceInfo(ref info);
            lines.Add(string.Format(
                "target={0} rc={1} supported={2} enabled={3} bpc={4}",
                info.header.id, grc, info.value & 1, (info.value >> 1) & 1,
                info.bitsPerColorChannel));
        }
        return lines.ToArray();
    }
}
```

- [ ] **Step 9: Écrire `probe/advanced-color.ps1`**

```powershell
<#
    Advanced Color (HDR) probe - the acceptance test of sub-project A.

    MUST run in session 1. Over WinRM (session 0) QueryDisplayConfig reports
    zero paths even in the middle of a streaming session, so launch it through
    a scheduled task created with /it, never through winvm directly.

    Budget about three minutes: Add-Type compiles C# on the fly.
#>
param([string]$OutFile = 'C:\nivuus\state\advanced-color.txt')

$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AdvancedColor.cs')

New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
[NivuusAdvancedColor]::Run() | Tee-Object -FilePath $OutFile
```

- [ ] **Step 10: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_provision.py`
Expected: `OK - all provisioning script checks passed`

- [ ] **Step 11: Commit**

```bash
git add installer/windows-guest/provision installer/windows-guest/probe \
        scripts/tests/test_windows_guest_provision.py
git commit -m "feat(windows-guest): session-1 provisioning stages and HDR probe"
```

---

### Task 6: Ligne de commande `build.py`

**Files:**
- Create: `installer/windows-guest/build.py`
- Modify: `installer/Makefile` (ajout de la cible `windows-unattend`)

**Interfaces:**
- Consomme : `media.inspect_iso`, `autounattend.UnattendParams`/`render`, `payload.PayloadSources`/`marker_text`/`stage_payload`/`verify_staged`, `unattend_iso.build_iso`/`verify_iso`.
- Produit : l'exécutable `python3 installer/windows-guest/build.py`, et `nivuus-unattend.iso`.

Pas de test unitaire dédié : ce module n'est qu'un assemblage des quatre
précédents, tous testés. Sa vérification est l'étape 4 ci-dessous, sur un média
réel.

- [ ] **Step 1: Écrire le module**

`installer/windows-guest/build.py` :

```python
#!/usr/bin/env python3
"""Build nivuus-unattend.iso from a Windows 11 IoT Enterprise LTSC medium.

The Windows medium is only read: it is never rebuilt (see unattend_iso.py).
The product key is read from a 0600 file and never passed on the command line,
where it would leak into ps output and shell history.

Usage:
    sudo python3 build.py --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
                          --drivers-dir /media/data/nivuus-win-payload \
                          --output /media/data/iso/nivuus-unattend.iso
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autounattend  # noqa: E402
import media  # noqa: E402
import payload  # noqa: E402
import unattend_iso  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_KEY_FILE = "/root/.config/nivuus/windows-ltsc.key"
DEFAULT_PASSWORD_FILE = "/root/.config/nivuus/windows-admin.pass"


def read_secret(path: str, what: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"missing {what}: {path}")
    mode = p.stat().st_mode & 0o077
    if mode:
        raise SystemExit(f"{path} is group/world readable ({oct(mode)}); chmod 600 it")
    value = p.read_text().strip()
    if not value:
        raise SystemExit(f"{path} is empty")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Build the Nivuus unattend ISO")
    ap.add_argument("--windows-iso", required=True,
                    help="official Windows 11 IoT Enterprise LTSC 2024 medium")
    ap.add_argument("--drivers-dir", required=True,
                    help="directory holding nvidia/ and sudovda/ payload binaries")
    ap.add_argument("--output", default="/media/data/iso/nivuus-unattend.iso")
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE)
    ap.add_argument("--password-file", default=DEFAULT_PASSWORD_FILE)
    ap.add_argument("--hostname", default="NIVUUS-WIN")
    ap.add_argument("--image-name", default=None,
                    help="pick an image explicitly when the medium has several")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    key = read_secret(args.key_file, "product key file")
    password = read_secret(args.password_file, "administrator password file")

    print(f"inspecting {args.windows_iso}")
    image = media.inspect_iso(args.windows_iso, image_name=args.image_name)
    print(f"  image #{image['index']}: {image['name']} "
          f"(edition {image.get('edition_id')}, build {image['build']})")

    params = autounattend.UnattendParams(
        product_key=key, admin_password=password,
        image_name=image["name"], hostname=args.hostname,
    )
    answer_file = autounattend.render(params)

    sources = payload.PayloadSources(
        provision_dir=HERE / "provision",
        probe_dir=HERE / "probe",
        drivers_dir=Path(args.drivers_dir),
    )
    build_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    with tempfile.TemporaryDirectory(prefix="nivuus-unattend-") as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        (stage / "autounattend.xml").write_text(answer_file)
        payload.stage_payload(stage / "nivuus", sources,
                              payload.marker_text(image["name"], build_id))
        payload.verify_staged(stage / "nivuus")
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        unattend_iso.build_iso(stage, out)

    unattend_iso.verify_iso(out)
    print(f"\nwrote {out} ({out.stat().st_size // 1024} KiB)")
    print(f"  sha256 unattend : {sha256(out)}")
    print(f"  sha256 windows  : {sha256(Path(args.windows_iso))}")
    print("\nAttach BOTH ISOs to the guest: the LTSC medium boots, this one is "
          "only read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Ajouter la cible Makefile**

Dans `installer/Makefile`, ajouter `windows-unattend` à la ligne `.PHONY` puis :

```make
# Build the secondary ISO that unattends the Windows guest install (root:
# inspecting the LTSC medium needs a loop mount).
# Usage: sudo make windows-unattend WINDOWS_ISO=/path/ltsc.iso DRIVERS_DIR=/path/payload
windows-unattend:
	@test -n "$(WINDOWS_ISO)" || { echo "Set WINDOWS_ISO=/path/to/ltsc.iso"; exit 1; }
	@test -n "$(DRIVERS_DIR)" || { echo "Set DRIVERS_DIR=/path/to/payload"; exit 1; }
	python3 $(INSTALLER_DIR)/windows-guest/build.py \
	  --windows-iso $(WINDOWS_ISO) --drivers-dir $(DRIVERS_DIR) \
	  $(if $(OUTPUT),--output $(OUTPUT),)
```

- [ ] **Step 3: Revérifier l'empreinte du média**

`/media/backup` est monté `data=writeback,barrier=0`, et ce média est le seul
artefact irremplaçable du projet.

```bash
sha256sum /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso
```

Expected: `4f59662a96fc1da48c1b415d6c369d08af55ddd64e8f1c84e0166d9e50405d7a`

- [ ] **Step 4: Vérifier sur le média réel**

Prérequis : la clé dans `/root/.config/nivuus/windows-ltsc.key` (`chmod 600`),
un mot de passe dans `/root/.config/nivuus/windows-admin.pass` (`chmod 600`),
et `DRIVERS_DIR/nvidia/*.exe` + `DRIVERS_DIR/sudovda/` remplis.

```bash
cd installer
sudo make windows-unattend \
     WINDOWS_ISO=/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
     DRIVERS_DIR=/media/data/nivuus-win-payload
```

Expected :

```
image #2: Windows 11 IoT Enterprise LTSC 2024 (edition IoTEnterpriseS, build 26100)
wrote /media/data/iso/nivuus-unattend.iso
```

C'est bien **#2** qu'il faut lire : #1 est `EnterpriseS` et #3 la variante par
abonnement, qu'aucune clé achetée n'active.

Vérifier ensuite que la clé n'a pas fui dans l'ISO produite hors de son fichier
de réponses — un seul emplacement attendu :

```bash
xorriso -indev /media/data/iso/nivuus-unattend.iso -find / -type f
```

- [ ] **Step 5: Commit**

```bash
git add installer/windows-guest/build.py installer/Makefile
git commit -m "feat(windows-guest): command line that builds the unattend ISO"
```

---

### Task 7: Domaine libvirt de test jetable

Le GPU réel est **indispensable** : sur virtio-gpu, un `supported=0` condamnerait
LTSC à tort et un `supported=1` ne prouverait rien, puisque c'est le pilote
NVIDIA qui porte la pile Advanced Color.

**Trois pièges de plateforme encodés dans le template :**

1. **Aucun périphérique virtio pour le disque ni le réseau.** Le média LTSC n'a
   pas de pilote virtio intégré : le disque système est en `sata`, la carte
   réseau en `e1000e`. Un disque virtio serait simplement invisible du
   programme d'installation.
2. **`<loader>`/`<nvram>` explicites, jamais `firmware='efi'`.** La sélection
   automatique de firmware a déjà cassé l'hibernation S4 sur cette machine :
   les descripteurs OVMF de Debian ne déclarent que `acpi-s3`.
3. **Une sortie VGA émulée en plus du GPU passé**, sinon l'installation
   automatique se déroule à l'aveugle — le pilote NVIDIA n'existe pas encore
   dans l'invité au moment où le programme d'installation tourne.

Et deux abstentions délibérées : **pas de hugepages** (le pool est dimensionné
pour la VM de production) et **aucun `hostdev` vers le NVMe Samsung**
(`144d:a808`) — Server 2022 reste intact comme retour arrière.

**Files:**
- Create: `installer/windows-guest/testdomain.py`
- Create: `installer/windows-guest/templates/domain-test.xml.j2`
- Test: `scripts/tests/test_windows_guest_domain.py`

**Interfaces:**
- Consomme : `nivuus-unattend.iso` (tâche 6). **Ne consomme PAS**
  `C:\nivuus\state\PROVISION.done` (tâche 5, corrigé revue finale 2026-08-22) :
  `wait_ready()` ne lit que le port 5985, jamais le marqueur - voir sa docstring.
- Produit : `domain_xml(**kwargs) -> str`, `create_disk(path, size_gib)`, `define(xml)`, `wait_ready(domain, timeout_s)`, `teardown(domain)`, `DOMAIN_NAME = "Windows-LTSC-test"`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
#!/usr/bin/env python3
"""Tests for the throwaway LTSC test domain XML.

Run: python3 scripts/tests/test_windows_guest_domain.py
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "windows-guest"))

import testdomain  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


xml_text = testdomain.domain_xml(
    disk_path="/media/data/vm/windows-ltsc-test.qcow2",
    windows_iso="/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso",
    unattend_iso="/media/data/iso/nivuus-unattend.iso",
)
root = ET.fromstring(xml_text)

check("domain name", root.findtext("name"), testdomain.DOMAIN_NAME)
check("kvm domain", root.get("type"), "kvm")

os_el = root.find("os")
check("q35 machine", os_el.find("type").get("machine"), "pc-q35-9.2")
# Explicit firmware paths: automatic selection already broke S4 on this host.
check("no firmware autoselection", os_el.get("firmware"), None)
check("secure boot loader", os_el.findtext("loader"),
      "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd")
check("loader is secure-boot capable",
      os_el.find("loader").get("secure"), "yes")
check("nvram template with Microsoft keys",
      os_el.find("nvram").get("template"), "/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
# Secure Boot needs SMM, and SMM needs q35.
check("smm on", root.find("features/smm").get("state"), "on")

check("tpm 2.0 emulated",
      root.find("devices/tpm/backend").get("version"), "2.0")
check("tpm backend is swtpm",
      root.find("devices/tpm/backend").get("type"), "emulator")
check("tpm model", root.find("devices/tpm").get("model"), "tpm-crb")

disks = root.findall("devices/disk")
system = [d for d in disks if d.get("device") == "disk"]
check("one system disk", len(system), 1)
# The LTSC medium carries no virtio driver: a virtio disk would be invisible.
check("system disk on sata", system[0].find("target").get("bus"), "sata")
check("system disk is qcow2", system[0].find("driver").get("type"), "qcow2")
check("system disk lives on /media/data",
      system[0].find("source").get("file").startswith("/media/data/"), True)

cdroms = [d for d in disks if d.get("device") == "cdrom"]
check("two cdroms", len(cdroms), 2)
sources = sorted(c.find("source").get("file") for c in cdroms)
check("both media attached", sources,
      [
       "/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso",
       "/media/data/iso/nivuus-unattend.iso"])
booting = [c for c in cdroms if c.find("boot") is not None]
check("only the windows medium boots", len(booting), 1)
check("windows medium boots first",
      booting[0].find("source").get("file"),
      "/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso")

nic = root.find("devices/interface/model")
check("e1000e nic (no inbox virtio driver)", nic.get("type"), "e1000e")

addrs = [h.find("source/address") for h in root.findall("devices/hostdev")]
slots = sorted((a.get("bus"), a.get("slot"), a.get("function")) for a in addrs)
check("gpu and its audio function are passed", slots,
      [("0x01", "0x00", "0x0"), ("0x01", "0x00", "0x1")])
# The Samsung NVMe stays with the production VM: Server 2022 is the rollback.
check("nvme is never passed", "0x03" in [a.get("bus") for a in addrs], False)

check("an emulated console exists", root.find("devices/graphics") is not None, True)
check("hugepages are not claimed", root.find("memoryBacking"), None)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all test domain XML checks passed")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 scripts/tests/test_windows_guest_domain.py`
Expected: `ModuleNotFoundError: No module named 'testdomain'`

- [ ] **Step 3: Écrire le template**

`installer/windows-guest/templates/domain-test.xml.j2` :

```xml
<domain type='kvm'>
  <name>{{ name }}</name>
  <memory unit='GiB'>{{ memory_gib }}</memory>
  <vcpu placement='static'>{{ vcpus }}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-9.2'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE_4M.secboot.fd</loader>
    <nvram template='/usr/share/OVMF/OVMF_VARS_4M.ms.fd'>{{ nvram_path }}</nvram>
  </os>
  <features>
    <acpi/>
    <apic/>
    <smm state='on'/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
    </hyperv>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='off'>
    <topology sockets='1' dies='1' cores='{{ vcpus // 2 }}' threads='2'/>
  </cpu>
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='none' discard='unmap'/>
      <source file='{{ disk_path }}'/>
      <target dev='sda' bus='sata'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{{ windows_iso }}'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
      <boot order='1'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{{ unattend_iso }}'/>
      <target dev='sdc' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='bridge'>
      <source bridge='{{ bridge }}'/>
      <mac address='{{ mac }}'/>
      <model type='e1000e'/>
    </interface>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <source>
        <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
      </source>
    </hostdev>
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <source>
        <address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>
      </source>
    </hostdev>
    <graphics type='vnc' port='-1' listen='127.0.0.1'/>
    <video>
      <model type='vga' vram='16384' heads='1'/>
    </video>
    <input type='tablet' bus='usb'/>
    <console type='pty'/>
    <memballoon model='none'/>
  </devices>
</domain>
```

- [ ] **Step 4: Écrire le module**

`installer/windows-guest/testdomain.py` :

```python
#!/usr/bin/env python3
"""Throwaway libvirt domain that answers the HDR question on the real GPU.

Server 2022 stays untouched on the NVMe: this domain only ever writes a qcow2
on /media/data, and never receives the Samsung NVMe hostdev.

Usage:
    python3 testdomain.py xml
    sudo python3 testdomain.py define --windows-iso ... --unattend-iso ...
    python3 testdomain.py wait-ready
    sudo python3 testdomain.py teardown
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"

DOMAIN_NAME = "Windows-LTSC-test"
DISK_PATH = "/media/data/vm/windows-ltsc-test.qcow2"
NVRAM_PATH = "/var/lib/libvirt/qemu/nvram/Windows-LTSC-test_VARS.fd"
BRIDGE = "internalBridge"
# Locally administered MAC, distinct from the production VM's.
MAC = "52:54:00:4c:54:53"
WINRM_PORT = 5985


class DomainError(RuntimeError):
    """Raised when the test domain cannot be created or does not come up."""


def _virsh(*args: str) -> subprocess.CompletedProcess:
    # virsh output is localized; LC_ALL=C keeps state strings parseable.
    return subprocess.run(["virsh", *args], text=True, capture_output=True,
                          env={**os.environ, "LC_ALL": "C"})


def domain_xml(*, disk_path: str = DISK_PATH, windows_iso: str,
               unattend_iso: str, name: str = DOMAIN_NAME,
               nvram_path: str = NVRAM_PATH, bridge: str = BRIDGE,
               mac: str = MAC, memory_gib: int = 16, vcpus: int = 8) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)),
                      autoescape=select_autoescape(enabled_extensions=("j2",),
                                                   default=True),
                      keep_trailing_newline=True)
    return env.get_template("domain-test.xml.j2").render(
        name=name, disk_path=disk_path, windows_iso=windows_iso,
        unattend_iso=unattend_iso, nvram_path=nvram_path, bridge=bridge,
        mac=mac, memory_gib=memory_gib, vcpus=vcpus,
    )


def assert_gpu_free() -> None:
    """The production VM owns the GPU while it runs; refuse rather than fight."""
    state = _virsh("domstate", "Windows").stdout.strip()
    if state and state != "shut off":
        raise DomainError(
            f"the Windows domain is {state!r}: shut it down first, the GPU "
            "cannot be assigned to two domains"
        )


def create_disk(path: str = DISK_PATH, size_gib: int = 120) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if Path(path).exists():
        raise DomainError(f"{path} already exists; run teardown first")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", path, f"{size_gib}G"],
                   check=True, capture_output=True)


def define(xml: str) -> None:
    path = Path("/run") / f"{DOMAIN_NAME}.xml"
    path.write_text(xml)
    proc = _virsh("define", str(path))
    if proc.returncode != 0:
        raise DomainError(f"virsh define failed: {proc.stderr.strip()}")


def guest_ip(domain: str = DOMAIN_NAME) -> str | None:
    proc = _virsh("domifaddr", domain, "--source", "arp")
    for line in proc.stdout.splitlines():
        for field in line.split():
            if "/" in field and field.count(".") == 3:
                return field.split("/")[0]
    return None


def wait_ready(domain: str = DOMAIN_NAME, timeout_s: int = 5400) -> str:
    """Wait for provisioning to finish: 99-marker.ps1 opens 5985 last."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ip = guest_ip(domain)
        if ip:
            with socket.socket() as sock:
                sock.settimeout(2)
                if sock.connect_ex((ip, WINRM_PORT)) == 0:
                    return ip
        time.sleep(15)
    raise DomainError(
        f"{domain} did not open {WINRM_PORT} within {timeout_s}s; connect to "
        "the VNC console and read C:\\nivuus\\provision.log"
    )


def teardown(domain: str = DOMAIN_NAME, disk_path: str = DISK_PATH) -> None:
    _virsh("destroy", domain)
    _virsh("undefine", domain, "--nvram")
    Path(disk_path).unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Throwaway LTSC test domain")
    ap.add_argument("action", choices=["xml", "define", "wait-ready", "teardown"])
    ap.add_argument("--windows-iso", default="/media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso")
    ap.add_argument("--unattend-iso", default="/media/data/iso/nivuus-unattend.iso")
    ap.add_argument("--disk-size", type=int, default=120)
    args = ap.parse_args(argv)

    if args.action == "xml":
        print(domain_xml(windows_iso=args.windows_iso,
                         unattend_iso=args.unattend_iso))
        return 0
    if args.action == "define":
        assert_gpu_free()
        create_disk(size_gib=args.disk_size)
        define(domain_xml(windows_iso=args.windows_iso,
                          unattend_iso=args.unattend_iso))
        print(f"defined {DOMAIN_NAME}; start it with: virsh start {DOMAIN_NAME}")
        return 0
    if args.action == "wait-ready":
        print(f"guest ready at {wait_ready()}")
        return 0
    teardown()
    print(f"{DOMAIN_NAME} removed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `python3 scripts/tests/test_windows_guest_domain.py`
Expected: `OK - all test domain XML checks passed`

- [ ] **Step 6: Valider le XML contre le schéma libvirt**

Les assertions du test disent ce que le domaine doit contenir ; seul libvirt dit
si le document est recevable. Ça coûte deux commandes et évite de découvrir une
erreur de schéma une fois le GPU immobilisé.

```bash
python3 installer/windows-guest/testdomain.py xml > /tmp/domain-test.xml
virt-xml-validate /tmp/domain-test.xml
```

Expected: `/tmp/domain-test.xml validates`

- [ ] **Step 7: Commit**

```bash
git add installer/windows-guest/testdomain.py \
        installer/windows-guest/templates/domain-test.xml.j2 \
        scripts/tests/test_windows_guest_domain.py
git commit -m "feat(windows-guest): throwaway libvirt domain for the HDR test"
```

---

### Task 8: Test d'acceptation et documentation

C'est ici que le sous-projet A rend son verdict : **LTSC répond-il au besoin
HDR, oui ou non**, avant tout investissement dans B, C et D.

**Files:**
- Create: `installer/windows-guest/README.md`
- Modify: `CLAUDE.md` (section « Cloud-gaming host », résultat de la mesure)

**Interfaces:**
- Consomme : tout ce qui précède.
- Produit : le verdict, et la valeur de référence pour la suite du projet.

⚠️ **Cette tâche immobilise le GPU et arrête la VM de production.** Elle se
planifie ; elle ne s'improvise pas au milieu d'une session de jeu.

- [ ] **Step 1: Écrire le README**

`installer/windows-guest/README.md` — contenu :

- l'objet du module et son rattachement à la spec ;
- la préparation de `DRIVERS_DIR` : `nvidia/` (l'exécutable du pilote, tel que
  téléchargé) et `sudovda/` (copie de `C:\Program Files\Apollo\drivers\sudovda`
  de l'invité actuel : `SudoVDA.inf`, `SudoVDA.dll`, `SudoVDA.cat`,
  `sudovda.cer`, `install.bat`, `nefconc.exe`) ;
- les deux fichiers de secrets et leur mode `600` ;
- la commande de fabrication et celle du domaine de test ;
- **pourquoi deux médias** (résumé de la section « Écart assumé » de ce plan) ;
- **pourquoi `FirstLogonCommands` et jamais `SetupComplete.cmd`** ;
- le runbook d'acceptation ci-dessous.

- [ ] **Step 2: Pré-vol — désarmer le réveil de la VM de production (ajouté revue finale, 2026-08-22, I4)**

Sur cet hôte, `vm-trigger-47989.socket` démarre `Windows` (production) sur tout
`GET /serverinfo` Moonlight, et `vm-idle-shutdown.timer` **réarme les deux
sockets de réveil toutes les 10 min tant que la VM est éteinte** — les arrêter
à la main ne tient donc pas. Un réveil pendant le test se dispute le GPU que le
domaine de test possède. `systemctl` est inutilisable depuis une session Claude
sur cet hôte : ce qui suit est pour un humain sur un vrai shell.

```bash
sudo systemctl mask --now vm-trigger-47984.socket vm-trigger-47989.socket vm-idle-shutdown.timer
```

- [ ] **Step 3: Libérer le GPU**

```bash
virsh shutdown --mode acpi Windows    # plain `virsh shutdown` silently no-ops
virsh domstate Windows                # wait for "shut off"
```

- [ ] **Step 4: Créer et démarrer le domaine de test**

```bash
sudo python3 installer/windows-guest/testdomain.py define \
     --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
     --unattend-iso /media/data/iso/nivuus-unattend.iso
virsh start Windows-LTSC-test
virsh vncdisplay Windows-LTSC-test    # watch the unattended install
```

Expected : l'installation se déroule **sans une seule question**. Si le
programme d'installation pose la moindre question, le fichier de réponses n'a
pas été trouvé ou a été refusé — vérifier `X:\Windows\Panther\setuperr.log`
depuis un shell WinPE (Maj+F10).

- [ ] **Step 5: Attendre la fin du provisionnement**

```bash
python3 installer/windows-guest/testdomain.py wait-ready
```

Expected : `guest ready at 192.168.3.x` en moins de 90 min. En cas d'échec,
la console VNC et `C:\nivuus\provision.log` disent où l'on s'est arrêté ; les
fichiers `C:\nivuus\state\*.done` disent quelles étapes ont déjà réussi.

`wait_ready()` ne confirme QUE le port 5985 ouvert (voir sa docstring, corrigé
revue finale 2026-08-22) : il ne lit jamais le marqueur. C'est l'objet du pas
suivant.

- [ ] **Step 6: Lire le marqueur de version à la main (ajouté revue finale, 2026-08-22, I1)**

```bash
IP=192.168.3.x
W="/usr/local/bin/winrm -hostname $IP -username Administrator -password <pass>"
$W 'type C:\nivuus\state\PROVISION.done'
```

Expected : une ligne `provision_version=A1` (ou la valeur courante de
`payload.PROVISION_VERSION` dans `installer/windows-guest/payload.py`). Un
marqueur absent ou portant une version différente signifie que 5985 s'est
ouvert sans que la charge utile attendue ait tourné jusqu'au bout — ne pas
lancer la sonde avant d'avoir compris pourquoi.

- [ ] **Step 7: Lancer la sonde en session 1**

**La sonde ne vaut rien en session 0.** Deux voies, dans cet ordre de préférence :

*Depuis la console VNC*, ouvrir une session `Administrator` (le média est
`en-US`) et lancer :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\nivuus\probe\advanced-color.ps1
```

*Ou depuis l'hôte*, une session étant ouverte sur la console (`/it` n'exécute la
tâche que si un utilisateur est connecté — c'est exactement la contrainte que ce
sous-projet contourne) :

```bash
IP=192.168.3.x
W="/usr/local/bin/winrm -hostname $IP -username Administrator -password <pass>"
$W 'schtasks /create /tn NivuusHdrProbe /tr "cmd /c powershell -NoProfile -ExecutionPolicy Bypass -File C:\nivuus\probe\advanced-color.ps1" /sc once /st 00:00 /ru Administrator /it /f'
$W 'schtasks /run /tn NivuusHdrProbe'
sleep 180   # Add-Type compiles C# on the fly
$W 'type C:\nivuus\state\advanced-color.txt'
```

- [ ] **Step 8: Lire le verdict**

**Le critère d'acceptation de la spec (`supported=1 et bpc>=10`) est insuffisant
et a été corrigé** (revue finale, 2026-08-22) : lire `supported=` seul confond
deux situations opposées. La sonde peut désormais mesurer jusqu'à trois écrans
à la fois (VGA émulé, sortie HDMI du GPU passé, SudoVDA une fois créé), chaque
ligne s'auto-identifiant (LUID d'adaptateur, technologie de sortie, nom convivial
du moniteur) — voir Task 8 côté sonde. Quatre issues, distinguées par `rc`, pas
par `supported` :

| Ligne observée | Signification | Verdict |
| --- | --- | --- |
| `rc=31` (ERROR_GEN_FAILURE) sur **tous** les écrans | l'OS/pilote ne sait pas faire d'Advanced Color, point. C'est la référence actuelle sur Server 2022. | **LTSC ne règle pas le problème.** |
| `rc=0 supported=0` sur un écran sans métadonnées HDR (le dongle HDMI factice) | l'API fonctionne ; cet écran précis n'est simplement pas HDR-capable. | **PASS pour la question OS** (ne pas lire comme un échec). |
| `rc=0 supported=1 bpc>=10` sur un écran HDR-capable | succès complet. | **PASS.** |
| `query rc=122` (ERROR_INSUFFICIENT_BUFFER, course connue, non ré-essayée) et aucune ligne `target=` | la sonde a échoué à interroger, pas mesuré quoi que ce soit. | **Pas un verdict : relancer la sonde.** |

La ligne de référence `target=24832 rc=31 supported=0 enabled=0 bpc=0` diffère
d'une ligne saine `rc=0 supported=0` **par le seul champ `rc`** ; ne lire que
`supported=` confond les deux — c'est exactement l'erreur que corrige ce tableau.

**Question tranchée le 2026-08-22 — on ne mesure PAS SudoVDA dans ce sous-projet.**

SudoVDA ne crée un moniteur qu'à la demande d'Apollo, et Apollo ne le crée qu'au
**démarrage d'un flux**, à la résolution demandée par un client. Les deux options
envisagées au cadrage sont donc rejetées : (a) écrire un client SudoVDA minimal
mettrait du code C neuf, compilé en croisé et inexécutable depuis Linux, sur le
chemin critique d'un passage unique ; (b) avancer Apollo exigerait de l'installer,
d'appairer un client Moonlight et d'ouvrir un flux pendant la fenêtre GPU — chaque
étape étant une façon de la perdre.

**Ce que A doit trancher est une question d'OS, pas d'écran.** Le blocage sur
Server 2022 est `rc=31 ERROR_GEN_FAILURE`, et l'API échoue *quel que soit
l'affichage* (mesuré, voir CLAUDE.md). Donc `rc=0` sur le dongle HDMI — même avec
`supported=0` — prouve que l'OS et la pile NVIDIA participent : le verrou saute,
la migration est justifiée. Que SudoVDA expose ensuite le HDR est une question
*SudoVDA-sur-24H2*, à laquelle ses mainteneurs répondent déjà oui, et que le
sous-projet B exercera dans sa vraie configuration avec un vrai client — un
meilleur test qu'un test synthétique.

**Trou assumé** : si tout répond `rc=0 supported=0`, on prouve que l'API
fonctionne sans jamais observer un « oui ». Le contrôle positif le moins cher est
**physique** : débrancher le dongle HDMI et brancher un vrai câble vers la TV le
temps du test. Son EDID HDR est authentique, et une ligne `supported=1 bpc>=10`
sur ce chemin serait une preuve directe de bout en bout. À défaut, on s'en tient
à `rc=0` et on consigne l'hypothèse résiduelle.

**L'installeur Apollo est présent dans la charge utile** (`\nivuus\drivers\apollo\`)
mais **aucune étape ne l'exécute** : il est là pour permettre un essai manuel de
SudoVDA dans la même fenêtre si `rc=0` tombe, sans refabriquer l'ISO.

- [ ] **Step 9: Vérifier l'activation dans le même passage**

C'est le geste qui coûte une minute ici et une migration entière si on l'oublie.

```bash
$W 'cscript //nologo C:\Windows\System32\slmgr.vbs /dli'
```

Expected : `Licence status: Licensed`. Une clé IoT LTSC revendue au détail passe
par le canal volume et n'active pas toujours — un échec ici bloque tout le
projet et doit être connu **maintenant**, pas en fin de migration.

- [ ] **Step 10: Consigner le résultat**

Ajouter à `CLAUDE.md`, dans la section « Cloud-gaming host = Apollo + SudoVDA »,
la mesure obtenue (les deux lignes de la sonde, l'état d'activation, la date) et
la conclusion : LTSC 26100 valide ou invalide la migration HDR. Le passage
existant qui affirme « HDR cannot work on this VM » reste vrai pour Server 2022
et doit être daté comme tel, pas supprimé.

Ce même test valide au passage **Secure Boot + vTPM + passthrough ensemble**,
la combinaison la plus risquée du projet — le noter explicitement.

- [ ] **Step 11: Rendre le GPU à la production**

```bash
sudo python3 installer/windows-guest/testdomain.py teardown
virsh start Windows        # or leave it to the wake-on-demand sockets
```

`teardown` détruit le domaine, son NVRAM et son qcow2. Le NVMe et Server 2022
n'ont jamais été touchés.

**Démasquer le réveil (I4, symétrique du Step 2)** — encore un geste pour un
humain sur un vrai shell, pas depuis une session Claude :

```bash
sudo systemctl unmask vm-trigger-47984.socket vm-trigger-47989.socket vm-idle-shutdown.timer
sudo systemctl start vm-trigger-47984.socket vm-trigger-47989.socket vm-idle-shutdown.timer
```

- [ ] **Step 12: Commit**

```bash
git add installer/windows-guest/README.md CLAUDE.md
git commit -m "docs(windows-guest): acceptance runbook and HDR verdict"
```

---

## Ce que le sous-projet A ne fait pas

Apollo, Steam et l'agent Guacamole (**B**) ; le domaine libvirt généré depuis
`hardware.py` avec hugepages et topologie (**C**) ; l'écran du wizard,
`WindowsGuestConfig`, `steps/windows_guest.py` et les drapeaux de `build-iso`
(**D**). A se pilote entièrement en ligne de commande.

**Explicitement refusé** : tout contournement des systèmes anti-triche.

## Deux risques de la spec qui restent ouverts après A

| Risque | Statut à la fin de A |
| --- | --- |
| ~~Média LTSC absent~~ | **levé le 2026-08-22** : le média est en place et inspecté (voir « Le média, mesuré ») |
| **Compilation croisée de `agent.exe`** (`cargo-xwin`) jamais éprouvée sur du code DXGI / Windows.Graphics.Capture / ProjFS | intact — à sonder tôt et **indépendamment de A**, car il bloque B |

Le risque « réinjection UEFI » est **supprimé**, pas traité : on ne fabrique
plus de média amorçable.

## Vérification finale

```bash
for t in autounattend media payload iso provision domain; do
    python3 scripts/tests/test_windows_guest_$t.py || echo "ÉCHEC: $t"
done
```

Les six doivent afficher `OK - ...`. Les tâches 1 à 5 et 7 sont vérifiables
**sans média Windows ni GPU** ; seules les tâches 6 et 8 exigent le matériel.

---

## Amendements pendant l'exécution (2026-08-22)

Le plan a été exécuté tâche par tâche, chaque tâche relue par un agent indépendant,
puis suivi d'une revue finale de branche entière et d'une vague de correctifs.
**Douze écarts au code dicté ci-dessus ont été décidés en cours de route** ; le code
fusionné les porte, ce texte non. Un exécutant qui rejouerait ce plan à la lettre
reproduirait les défauts qu'ils corrigent — lire `git log` comme référence.

| # | Où | Ce que le plan disait | Ce qui a été retenu, et pourquoi |
| --- | --- | --- | --- |
| 1 | `autounattend.py` | le message d'erreur d'une clé malformée interpolait `{product_key!r}` | **la valeur est retirée du message.** Une faute de frappe dans le fichier de clé imprimait la clé de licence en clair sur stderr — exactement la fuite que le module dit exister pour empêcher |
| 2 | `media.py` | `read_wim_xml` lisait l'en-tête sans vérifier sa longueur | **garde de troncature explicite.** Un fichier à la bonne signature mais tronqué levait un `struct.error` opaque au lieu d'un échec bruyant |
| 3 | `media.py` (test) | seul le cas « pas un WIM » était couvert | **un WIM synthétique** prouve l'arithmétique d'offsets sans média — la promesse du docstring n'était pas tenue pour la seule fonction qui en fait |
| 4 | `payload.py` | `verify_staged` n'attestait que NVIDIA ; `missing_binaries` ne vérifiait que `SudoVDA.inf` | **les deux couvrent les trois fichiers SudoVDA**, et `verify_staged` appelle `missing_binaries` pour qu'ils ne divergent jamais. `20-sudovda.ps1` échoue sans `sudovda.cer` ni `install.bat` : la garde de fabrication doit couvrir ce que l'invité consomme |
| 5 | `payload.py` | `shutil.rmtree(dest_root)` sans garde | **refus avant suppression** si `dest_root` résout vers un répertoire source ou une racine |
| 6 | `unattend_iso.py` | `list_iso` faisait `line.strip("'")` | **`shlex.split`.** `xorriso` échappe une apostrophe à la mode POSIX ; un `Bob's driver.exe` dans la charge utile produisait un chemin corrompu |
| 7 | `provision/` | l'étape NVIDIA « redémarre et le provisionnement reprend » | **le redémarrage n'existait nulle part** : `-noreboot` était passé et rien ne redémarrait, donc toute la machinerie de reprise était du code mort. L'étape 10 pose un jeton `reboot.requested`, `run-all.ps1` écrit le `.done` **puis** consomme le jeton et redémarre (cet ordre évite une boucle infinie), et la vérification NVIDIA différée remonte dans `99-marker.ps1` |
| 8 | `probe/AdvancedColor.cs` (`:1677`) et son test (`:1375`) | chaque ligne de sonde tenait en cinq champs (`target rc supported enabled bpc`) | **auto-identification de l'écran mesuré** : LUID d'adaptateur, technologie de sortie, nom convivial du moniteur ajoutés via `DISPLAYCONFIG_TARGET_DEVICE_NAME`. Avec jusqu'à trois écrans actifs à la fois (VGA émulé, dongle HDMI du GPU, SudoVDA), un `target=<id>` nu ne dit pas lequel a été mesuré — et c'est cette sonde qui porte tout le verdict du sous-projet |
| 9 | `media.py` | `subprocess.run(["mount", ...], check=True)` | **contrôle explicite du code de retour**, levant `MediaError` (jamais `CalledProcessError`, que `build.py` ne rattrape pas) avec le stderr du montage, plus un refus d'empiler un second montage si `mount_dir` est déjà monté |
| 10 | `build.py` | `out.parent.mkdir(parents=True, exist_ok=True)` sans mode, ISO écrite avec les permissions par défaut | **parent créé en `0700`, ISO chmod `0600`** après fabrication. L'ISO embarque la clé produit et le mot de passe Administrateur en clair ; sa destination par défaut vit sous `/media/data`, exporté en SMB avec le port WAN ouvert |
| 11 | `provision/99-marker.ps1` | `Copy-Item -Destination 'C:\nivuus\probe'` | **`-Destination 'C:\nivuus'`** : le chemin `\probe` imbriquait en `...\probe\probe` quand la destination existait déjà (ce qui est le cas courant, `C:\nivuus` étant créé par `00-bootstrap.ps1`) |
| 12 | `testdomain.py` | `assert_gpu_free()` ne vérifiait que l'état du domaine `Windows`, aucun contrôle des détenteurs de `/dev/nvidia*` | **`gpu_holders()` ajoutée**, refusant le `define` si un processus tient encore `/dev/nvidia*` — ce domaine jetable n'a aucun hook libvirt pour les arrêter, contrairement à la VM de production. Mesuré en conditions réelles sur cet hôte : `find /proc -lname '/dev/nvidia*'` (les deux formes, y compris celle documentée dans `CLAUDE.md`) **sort en erreur systématiquement pour des raisons sans rapport avec un vrai détenteur** ; un `returncode != 0` interprété comme un échec de scan aurait donc refusé tout `define`, même GPU libre. L'énumération est en Python pur (`os.listdir`/`glob.glob`/`os.readlink`), sans code de retour à mésinterpréter, et n'échoue que si `/proc` lui-même est illisible |

## Pièges découverts au premier démarrage réel (2026-08-22)

Quatre défauts que **seul un vrai démarrage pouvait révéler** — ni les tests, ni
`virt-xml-validate`, ni la revue finale ne pouvaient les voir.

### 1. Le média Windows exige une frappe clavier pour démarrer — bloquant

Premier démarrage : `Press any key to boot from CD or DVD......`, puis le délai
expire et `BdsDxe: No bootable option or device was found.` L'installation ne
commence jamais. C'est le comportement normal de l'image d'amorçage UEFI que
Microsoft place sur ses médias (`efisys.bin`), qui attend une touche.

**Contournement retenu ici** : après `virsh start`, envoyer des frappes pendant
la fenêtre d'amorçage —

```bash
for i in $(seq 1 60); do virsh send-key <domaine> KEY_ENTER; sleep 0.5; done
```

**Ce n'est pas une solution durable** : c'est un geste de l'hôte, pas une
propriété du média. Pour le sous-projet D, deux vraies options : remplacer
l'image d'amorçage El Torito du média par `efisys_noprompt.bin` — mais on a
justement renoncé à reconstruire le média (voir l'écart UDF / 4 GiB) — ou
intégrer l'envoi de frappes à l'étape du moteur qui démarre l'invité. La seconde
est cohérente avec l'architecture à deux médias.

### 2. `<driver name='vfio'/>` est obligatoire dans les `hostdev`

Sans lui, `virsh start` échoue sur « l'hôte ne prend pas en charge le
passe-système des périphériques PCI » : libvirt retombe sur le backend hérité,
supprimé depuis longtemps. Le domaine de production le déclare ; le template ne
le faisait pas. `virt-xml-validate` ne pouvait pas l'attraper — il valide le
schéma, pas la sémantique. Corrigé, avec une assertion de non-régression.

### 3. `create_disk` créait un répertoire que qemu ne peut pas traverser

`Path(path).parent.mkdir(...)` sans mode donne `drwxr-x--- root:root` ; qemu
tourne en `libvirt-qemu` et échoue sur « Permission non accordée ». La propriété
dynamique de libvirt corrige le fichier image, **jamais son répertoire parent**.
Corrigé : `mode=0o755`.

### 4. Le durcissement de l'ISO se retourne contre lui-même

Le correctif I2 met l'ISO en `0600` dans un répertoire `0700` — ce qui empêche
aussi qemu de la lire. Résolu par ACL, qui préserve l'intention (l'ISO reste
illisible pour tout le monde d'autre) en n'accordant que le principal qui en a
besoin :

```bash
setfacl -m u:libvirt-qemu:x /media/data/iso
setfacl -m u:libvirt-qemu:r /media/data/iso/nivuus-unattend.iso
```

À intégrer dans `build.py` : poser l'ACL à l'écriture, plutôt que de laisser
l'opérateur la découvrir au premier démarrage.

⚠️ Noté au passage : libvirt applique sa propriété dynamique **au média LTSC
lui-même** (`mallanic` → `libvirt-qemu`) et ne la rend pas si le démarrage échoue
en cours de route. Le rétablir fait partie du démontage.

### Levée des vérifications dues (2026-08-22, après fusion)

Les trois points laissés en suspens ci-dessus ont été traités :

- **La charge utile est assemblée** dans `/media/data/nivuus-win-payload/` :
  pilote GeForce Game Ready **610.88** (28/07/2026, DCH, 979 651 304 o, taille
  conforme à celle annoncée par NVIDIA), `sudovda/` extrait de l'installeur
  **Apollo 0.4.6** (certificat `CN=sudovda@su.mk`, valide jusqu'en 2030), et
  l'installeur Apollo lui-même, non exécuté.
- **L'ISO a été produite** : `/media/data/iso/nivuus-unattend.iso`, 975 140 Kio,
  `sha256 b52395712615b2b881a1e4689c8e9f62d8cdbda5901a333cc37d425e1e270367`,
  mode 0600 dans un répertoire 0700. L'inspection a bien sélectionné l'image **#2**
  (`IoTEnterpriseS`, build 26100) parmi les trois éditions du média.
- **Le confinement des secrets est vérifié** : la clé produit et le mot de passe
  Administrateur apparaissent **2 fois chacun dans l'image entière, et ces 2
  occurrences sont dans `/autounattend.xml`** — nulle part ailleurs. C'est le
  contrôle de l'étape 4 de la tâche 6, désormais fait sur l'artefact réel.
- `/root/.config/nivuus/windows-admin.pass` a été créé sur demande explicite du
  propriétaire : 24 caractères base62 tirés de `/dev/urandom`, mode 0600. Base62
  seul et non un jeu étendu, pour satisfaire la complexité Windows (majuscule,
  minuscule, chiffre) **sans aucun caractère à échapper** en XML, `cmd`,
  PowerShell ou WinRM.

Reste dû : le test d'acceptation lui-même (tâche 8), qui immobilise le GPU.
