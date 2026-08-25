# Recette B — provisionnement automatisé de l'invité (sous-projet B)

> ⚠️ **Exécutée partiellement le 2026-08-25** (voir « Résultat de la première
> exécution » en fin de document) : construction, installation, marqueur, test
> 4b et test 2 sont mesurés ; les tests 1 et 3 restent dus, ils exigent la TV
> et un compte Steam. Le paragraphe ci-dessous reste valable pour toute
> exécution ultérieure.
>
> 🔴 **NE PAS L'EXÉCUTER PENDANT L'IMPLÉMENTATION.** Elle démarre un domaine, prend le GPU réel à l'hôte et
> arrête des conteneurs de production (Ollama, Tdarr). Rien ci-dessous n'a été
> vérifié ; les cases des tableaux de critères ne sont pas cochées parce que
> personne ne les a encore mesurées, pas parce qu'elles ont échoué.
>
> **N'exécuter cette recette que dans une fenêtre de maintenance choisie par
> le propriétaire de la machine, avec la VM `Windows` de production hors
> service.** Elle rivalise pour le même GPU que cette VM et suppose de couper
> des automatismes de l'hôte (réveil à la demande, hibernation auto) pour
> toute sa durée. Ce document est un mode opératoire pour un humain, sur une
> vraie console — pas une suite de commandes à faire rejouer par un agent, et
> pas quelque chose que l'implémenteur du plan doit lancer « pour vérifier ».

**Ce qu'on cherche à savoir** : le sous-projet B amène l'invité LTSC installé
par A à l'état d'appliance de cloud gaming (pilote NVIDIA, écran virtuel
SudoVDA, Apollo, Steam, agent Guacamole, énergie/hibernation, mises à jour) —
mais entièrement hors-ligne et sans jamais toucher la VM de production pendant
son implémentation. Cette recette est la seule chose qui vérifie que le
résultat marche réellement, sur un domaine et un disque jetables.

Le fait précis qu'elle règle : le 2026-08-22, une mesure a montré du HDR 10
bits réel sur l'écran virtuel SudoVDA d'un invité jetable — mais en **forçant**
l'état (`DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE`) plutôt qu'en laissant le
client le demander, parce que le décodeur Moonlight logiciel de l'hôte faisait
osciller sa demande de profondeur de couleur. Ce qui est prouvé à ce stade,
c'est que Windows sait allumer le HDR sur cet écran. Ce qui ne l'est pas, et
que le test 1 ci-dessous règle : que la TV qui pilote vraiment le flux
l'obtient de bout en bout, sans intervention côté invité.

---

## 1. Préconditions

Les quatre conditions ci-dessous sont bloquantes, dans cet ordre : chacune
rend les suivantes inutiles si elle échoue.

**1. `agent.exe` est déjà dans la charge utile hors-ligne.**

```bash
python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "installer/windows-guest")
import payload
missing = payload.missing_binaries(pathlib.Path("/media/data/nivuus-win-payload"))
print("\n".join(missing) or "payload complete")
PY
```

Attendu : `payload complete`. Si `agent.exe` apparaît dans la liste, **arrêter
ici** : cet agent se compile aujourd'hui *dans* la VM de production, et aucune
tâche de B ne peut le reconstruire — une fois cette VM effacée par la
bascule, il n'existe plus nulle part. Rien en aval de cette recette n'est
testable sans lui (le test 2 dépend entièrement de sa présence).

**2. La recette S4 du sous-projet C est passée.**

Voir [`recette-s4.md`](recette-s4.md). Elle mesure que Secure Boot + vTPM +
hibernation S4 fonctionnent ensemble sur le GPU réel passé en hostdev — la
physique dont cette recette-ci dépend sans la re-mesurer. Ne pas lancer B en
acceptation si S4 n'a jamais été exécutée avec succès : un échec de S4 se
manifesterait ici comme une hibernation ratée sans qu'on sache si la cause est
B (mauvaise configuration d'énergie, étape 7) ou C (S4 physiquement cassé).

**3. Le disque jetable est identifié et confirmé différent du NVMe de
production.**

Cette recette utilise `testdomain.py` (sous-projet A), pas `domain.py`
(production) : son disque est un fichier qcow2 ordinaire sur `/media/data`
(`/media/data/vm/windows-ltsc-test.qcow2`), jamais le NVMe Samsung passé en
hostdev à la VM `Windows` — ce dernier reste statiquement lié à `vfio-pci`
(`144d:a808`, voir CLAUDE.md) et n'apparaît même pas comme périphérique bloc
sur l'hôte. Confirmer avant de commencer :

```bash
grep 144d:a808 /etc/modprobe.d/vfio.conf     # doit lister l'id du NVMe : il ne quitte jamais vfio-pci
df -h /media/data                            # doit annoncer largement plus de 340G libres (voir §3)
ls -la /media/data/vm/windows-ltsc-test.qcow2 2>/dev/null   # ne doit PAS déjà exister
```

Si le fichier qcow2 existe déjà (recette précédente jamais nettoyée), lancer
`sudo python3 installer/windows-guest/testdomain.py teardown` avant de
continuer — mais seulement si ce n'est pas un test 3 en cours (voir §6, où au
contraire il ne faut *surtout pas* le supprimer).

**4. La VM `Windows` de production est arrêtée.**

```bash
LC_ALL=C virsh domstate Windows
```

Attendu : `shut off`. Sinon, arrêter ici : `testdomain.py define` refuse de
toute façon de continuer tant que le GPU n'est pas libre
(`assert_gpu_free()`), mais mieux vaut le savoir avant de construire l'ISO.

---

## 2. Construction

Trois secrets doivent déjà exister, mode 600, avant de lancer `build.py` :
`/root/.config/nivuus/windows-ltsc.key`, `/root/.config/nivuus/windows-admin.pass`
(hérités de A) et `/root/.config/nivuus/apollo-ui.pass` (nouveau en B — le mot
de passe de l'IHM web Apollo). `build.py` les lit et refuse sinon avec un
message explicite ; ils ne passent jamais par la ligne de commande.

```bash
cd installer/windows-guest

# a. Récupérer ce qui PEUT être téléchargé : Steam, WinFsp, virtio-win.
#    nvidia/, apollo/ et agent/ ne sont PAS touchés par ce script — ils
#    doivent déjà être en place (précondition 1) ; agent.exe en particulier
#    n'est jamais fetchable, il n'existe plus que dans la VM de production.
python3 fetch_payload.py --drivers-dir /media/data/nivuus-win-payload
```

Attendu : une ligne `fetching …` ou `keeping existing …` par artefact, un
sha256 par ligne, puis le rappel final qu'`agent/agent.exe` n'a pas été et ne
sera jamais récupéré par ce script. Une `FetchError` (URL morte, empreinte qui
a changé) arrête ici — c'est voulu : mieux vaut casser la construction que
l'installation hors-ligne.

```bash
# b. Construire l'ISO de réponses. --disk-mode par défaut est "wipe" : ce
#    premier build vise un disque jetable vierge, la valeur par défaut est
#    correcte ici.
sudo python3 build.py \
  --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
  --drivers-dir /media/data/nivuus-win-payload \
  --output /media/data/iso/nivuus-unattend.iso
```

Attendu : `inspecting …`, l'image détectée, puis `wrote … KiB`, deux lignes
sha256, et le rappel que le fichier de sortie porte trois secrets en clair
(mode 0600 — le vérifier : `stat -c '%a' /media/data/iso/nivuus-unattend.iso`
doit répondre `600`).

```bash
# c. Vérifier que la configuration RENDUE est bien montée sur l'ISO. C'est le
#    contrôle que la recette exige nommément — un build.py vert ne le
#    garantit PAS à lui seul : unattend_iso.verify_iso() ne vérifie que trois
#    chemins fixes (autounattend.xml, PAYLOAD.id, run-all.ps1), jamais
#    config/.
xorriso -indev /media/data/iso/nivuus-unattend.iso -find /nivuus/config -type f
```

Attendu, exactement trois lignes : `/nivuus/config/sunshine.conf`,
`/nivuus/config/apps.json`, `/nivuus/config/secrets.psd1`. Il en manque une →
ne pas passer à l'installation : soit un `build.py` d'avant la tâche 9 a été
utilisé par erreur, soit l'assemblage du répertoire temporaire a mal tourné
sans lever d'exception — un ISO qui *semble* correct (taille plausible,
`wrote` affiché) mais dont `config/` est incomplet installerait un invité qui
échoue silencieusement bien plus tard, à l'étape 25 côté invité, à des
dizaines de minutes de là.

---

## 3. Installation

### Étape 0 — libérer le GPU et neutraliser les automatismes de l'hôte

Même piège que la recette S4, pour la même raison : `vm-idle-shutdown.timer`
s'autorépare toutes les dix minutes tant que `Windows` est éteint (ce que la
précondition 4 garantit pour toute la durée de cette recette) — il relancerait
`nivuus-ollama` (le conteneur GPU qu'on arrête ci-dessous) et réarmerait les
sockets de réveil `vm-trigger-47984/47989` : une simple sonde Moonlight sur
47989 démarrerait alors la VM `Windows` de **production** en pleine recette,
entrant en concurrence avec le domaine de test pour le GPU.

`systemctl` ne fonctionne pas depuis une session automatisée sur cet hôte
(voir CLAUDE.md, « Host Shell Gotchas ») ; piloter systemd via D-Bus. Un
humain sur une vraie console peut à la place utiliser
`systemctl mask --now vm-idle-shutdown.timer vm-trigger-47984.socket
vm-trigger-47989.socket` directement.

```bash
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StopUnit string:"vm-idle-shutdown.timer" string:"replace"
dbus-send $M.StopUnit string:"vm-trigger-47984.socket" string:"replace"
dbus-send $M.StopUnit string:"vm-trigger-47989.socket" string:"replace"
dbus-send $M.MaskUnitFiles array:string:"vm-idle-shutdown.timer","vm-trigger-47984.socket","vm-trigger-47989.socket" boolean:false boolean:true
dbus-send $M.Reload

# Libérer le GPU : rien ne le fait automatiquement pour un domaine jetable
# (contrairement à la production, Windows-LTSC-test n'a pas de crochets sous
# /etc/libvirt/hooks/qemu.d/Windows-LTSC-test/).
docker stop mediamanager-tdarr-node-nvenc-1 mediamanager-tdarr-node-1 \
            mediamanager-tdarr-1 nivuus-ollama
dbus-send $M.StopUnit string:"nvidia-persistenced.service" string:"replace"
```

### Étape 1 — définir le domaine de test et démarrer

⚠️ **Ne jamais nommer ce domaine `Windows`.** `testdomain.py` le nomme
`Windows-LTSC-test` — le laisser tel quel.

```bash
cd installer/windows-guest

# --disk-size DOIT dépasser l'arithmétique du fichier de réponses : 140 GiB
# pour D: (--data-partition-gb par défaut de build.py) + de quoi loger C:,
# qui prend tout le reste et doit rester au-dessus de ~60 GiB pour qu'un LTSC
# avec ses fichiers d'échange et d'hibernation puisse se maintenir. La valeur
# par défaut de testdomain.py (--disk-size 120) est trop petite — Windows
# Setup échouera bruyamment en créant les partitions, mais aura fait perdre
# tout un cycle d'installation pour rien. 340 donne un C: d'environ 199 GiB.
#
# Depuis le 2026-08-25, D: est la PREMIÈRE partition du disque et c'est elle
# qui porte une taille fixe, C: prenant le reste — l'inverse de la disposition
# d'avant. La raison est dans le gabarit : en aval de la partition Windows,
# Windows Setup déplace la partition de données et l'emporte à la
# reconstruction.
sudo python3 testdomain.py define \
  --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
  --unattend-iso /media/data/iso/nivuus-unattend.iso \
  --disk-size 340
```

⚠️ **L'ISO doit avoir été construite avec `--data-partition-gb 140`.** Le défaut
de `build.py` vise le NVMe de production (820 GiB de jeux sur un disque de 1 To,
soit environ 110 GiB pour Windows) et ne tient pas sur un banc de 340 GiB.

```bash
virsh start Windows-LTSC-test
```

Le média Windows attend une frappe (« Press any key to boot from CD »). Sans
elle : `No bootable option or device was found`.

```bash
for i in $(seq 1 40); do
  virsh send-key Windows-LTSC-test --codeset linux KEY_ENTER >/dev/null 2>&1
  sleep 1
done
```

### Étape 2 — attendre le MARQUEUR, pas le port

🔴 **C'est le point le plus facile à rater de toute la recette.**
`testdomain.py wait-ready` ne fait qu'attendre que le port 5985 réponde. Ce
n'est pas la même chose que « le provisionnement est fini » : `00-bootstrap.ps1`
appelle `Enable-PSRemoting` (qui ouvre 5985 en effet de bord) puis
`Disable-NetFirewallRule` quelques lignes plus loin dans le même script — il
existe donc une fenêtre étroite, en tout début de provisionnement, où le port
répond alors que rien n'est fait. Seul `99-marker.ps1`, en tout dernier
geste, rouvre définitivement 5985 — après avoir vérifié le GPU, l'écran
virtuel, Apollo, Steam et la session de l'agent. Le signal de fin réel est
donc `C:\nivuus\state\PROVISION.done`, jamais le port seul.

```bash
export GUEST_IP=$(python3 testdomain.py wait-ready)
echo "guest IP: $GUEST_IP"

# Garde-fou identique à S4 : ne jamais laisser un environnement périmé viser
# la VM de production.
if [ "$GUEST_IP" = "192.168.3.2" ]; then
    echo "REFUS : GUEST_IP pointe la VM de production, pas le domaine jetable" >&2
    exit 1
fi

marker=""
for i in $(seq 1 240); do   # jusqu'à 60 min : le pilote NVIDIA (10-nvidia.ps1)
                            # + son redémarrage sont le plus long des postes
  marker=$(GUEST_IP="$GUEST_IP" python3 winrm_exec.py cmd \
           'type C:\nivuus\state\PROVISION.done' 2>/dev/null)
  case "$marker" in
    *provision_version=B1*) echo "$marker"; break ;;
  esac
  marker=""
  sleep 15
done
if [ -z "$marker" ]; then
  echo "le marqueur n'est jamais apparu avec provision_version=B1 — lire" >&2
  echo "C:\\nivuus\\provision.log via la console VNC : virsh vncdisplay Windows-LTSC-test" >&2
  exit 1
fi
```

Pendant cette boucle, `winrm_exec.py` qui répond « cannot reach guest » ou
« impossible de trouver le fichier » est **normal** tant que le
provisionnement tourne (le port est fermé, ou le fichier n'existe pas
encore) — ce n'est un échec réel que si la boucle épuise son budget. Un
`marker` qui contient `provision_version=` avec une **autre** valeur que
`B1` est un échec silencieux différent et plus grave : la charge utile
installée n'est pas celle que `build.py` vient de fabriquer — ne pas
continuer, vérifier `payload.PROVISION_VERSION` côté hôte et refaire la
construction.

---

## 4. Test 1 — HDR de bout en bout

Un flux **depuis la TV**, jamais depuis le Moonlight logiciel de l'hôte : la
sonde qui a fondé ce sous-projet a justement montré que le décodeur logiciel
de l'hôte fait osciller sa demande de profondeur de couleur, ce qui produirait
un faux négatif indiscernable d'un vrai.

Cette instance Apollo est neuve — ses appairages (`D:\state\apollo`) viennent
d'être créés à l'étape 25, vides. La TV doit donc être appairée à **ce**
domaine de test comme un nouvel hôte, distinct de la production (une IP
différente de `192.168.3.2`) : ouvrir Moonlight sur la TV, ajouter l'hôte
`$GUEST_IP`, saisir le code PIN affiché sur la TV dans l'IHM web d'Apollo
(HTTPS, port **47990** — établi par la route de production
`/opt/nivuus/Pomerium/config.yaml`, `from: https://game.allanic.me` →
`to: https://192.168.3.2:47990`, identifiants `--apollo-user`/le contenu de
`apollo-ui.pass`). Cette même route porte `tls_skip_verify: true` : Apollo y
présente un certificat auto-signé, donc toute commande utilisée pour
atteindre l'IHM pendant cette recette doit tolérer ce certificat (`curl -k`,
ou accepter l'avertissement du navigateur) — sans quoi l'échec de validation
TLS se lit à tort comme un problème d'Apollo lui-même.

⚠️ **Ne PAS passer par `game.allanic.me` pour cette recette.** Apollo 0.4.6
rejette l'authentification HTTP Basic sur `/api/*` (mesuré le 2026-08-22 :
`GET /api/config` et `POST /api/pin` renvoient tous deux 401 ; l'IHM
authentifie désormais via `POST /api/login`, qui pose un cookie `auth`). La
route Pomerium ci-dessus injecte un en-tête `Authorization: Basic` sur
`game.allanic.me` — un opérateur qui passerait par là pendant cette recette
verrait donc un 401 qui n'a rien à voir avec ses identifiants. Atteindre
l'IHM **directement sur l'invité** (`https://$GUEST_IP:47990`) pendant
l'acceptation ; corriger la route Pomerium appartient à la bascule, pas à
cette recette. Lancer ensuite l'application « Desktop » ou « Steam Big
Picture » depuis la TV.

```bash
python3 winrm_exec.py cmd 'type D:\state\apollo\sunshine.log' > /tmp/sunshine-test1.log
grep -c 'Client dynamicRange: 1' /tmp/sunshine-test1.log
grep -n 'Display is HDR: true' /tmp/sunshine-test1.log
```

Critère : `Client dynamicRange: 1` **soutenu** pendant tout le flux de la TV —
pas une seule occurrence perdue au milieu d'un `0`/`1` qui oscille, ce qui
serait le même symptôme que le décodeur logiciel de l'hôte — et au moins une
ligne `Display is HDR: true`.

Puis la sonde, **en session 1**, jamais par WinRM (qui voit zéro chemin
d'affichage même en plein flux) :

```bash
python3 winrm_exec.py cmd 'schtasks /create /tn nivuus-probe /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\nivuus\probe\advanced-color.ps1" /sc once /st 00:00 /ru Administrator /it /f'
python3 winrm_exec.py cmd 'schtasks /run /tn nivuus-probe'
sleep 200   # Add-Type compile du C# à la volée : budget ~3 min (CLAUDE.md)
python3 winrm_exec.py cmd 'type C:\nivuus\state\advanced-color.txt'
```

`installer/windows-guest/probe/AdvancedColor.cs` n'appelle jamais
`DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE` — il ne fait que lire
(`DisplayConfigGetDeviceInfo`), contrairement à la mesure du 2026-08-22 qui a
fondé ce sous-projet. C'est ce qui rend ce test-ci une mesure de ce que **le
client a réellement obtenu**, pas de ce que Windows sait faire quand on le lui
force.

Attendu : la première ligne `sizes rc=0 paths=1 …` (un seul chemin
d'affichage actif — la preuve que `ensure_only_display` a bien désactivé
tout le reste), puis, sur la ligne correspondant à l'écran SudoVDA (à
identifier par élimination parmi les cibles listées — QEMU VGA, HDMI factice
du GPU s'il est branché, SudoVDA — via `outputTechnology`/`adapterLuid`/`name` ;
en cas de doute, relancer la sonde une fois hors flux pour avoir une ligne de
référence) : `rc=0 supported=1 enabled=1 bpc=10`.

| Signification d'un résultat qui n'est PAS ça | Lecture |
| --- | --- |
| `rc` ≠ 0 sur toute cible | l'API Advanced Color échoue encore — régression vers le symptôme Server 2022 que ce sous-projet devait clore |
| `enabled=0 bpc=8` malgré `rc=0 supported=1` | l'API fonctionne mais rien ne l'a activée : vérifier que le HDR est bien coché côté TV avant de relancer |
| `paths=0` ou plusieurs chemins actifs | la sonde a tourné hors flux, ou `ensure_only_display`/la jonction de config n'ont pas pris — relire `sunshine.log` avant de conclure à un échec HDR |

---

## 4b. Ouverture de session automatique après redémarrage

Seul contrôle qui prouve que les valeurs de registre posées par
`50-power.ps1` sont aux chemins que Windows honore réellement — relire une
valeur qu'on vient d'écrire n'atteste que l'écriture, jamais que Windows la
lit au bon moment du démarrage.

```bash
# Avant de redémarrer, vérifier que l'hibernation a de quoi fonctionner :
# sinon le minuteur d'inactivité de l'hôte tenterait plus tard d'hiberner un
# invité qui ne sait pas le faire, et la VM resterait allumée en permanence.
python3 winrm_exec.py cmd 'if exist C:\hiberfil.sys (echo hiberfil.sys OK) else (echo MANQUANT)'
python3 winrm_exec.py cmd 'powercfg /availablesleepstates'
```

Attendu : `hiberfil.sys OK`, et `S4` listé parmi les états disponibles (pas
seulement « décrit mais indisponible pour la raison suivante… »).

```bash
python3 winrm_exec.py cmd 'del C:\nivuus\state\agent-session.txt'
python3 winrm_exec.py cmd 'shutdown /r /t 5'
```

L'appel WinRM du redémarrage n'a pas besoin de retourner proprement — c'est
`domstate`/le port qui compte ensuite :

```bash
for i in $(seq 1 60); do
  [ "$(LC_ALL=C virsh domstate Windows-LTSC-test)" = "running" ] && break
  sleep 5
done
export GUEST_IP=$(python3 testdomain.py wait-ready)   # le bail DHCP peut avoir changé
sleep 60   # laisser l'ouverture de session automatique et le déclencheur AtLogOn se produire

python3 winrm_exec.py cmd 'type C:\nivuus\state\agent-session.txt'
```

Attendu : le fichier existe et vaut `1`, **sans qu'aucune commande de ce
script n'ait ouvert de session** — c'est la tâche planifiée `guacamole-agent`
(déclencheur `AtLogOn`) qui l'a écrit, ce qui ne peut arriver que si Windows
s'est reconnecté seul sur le bureau de `Administrator` en session 1. Un
fichier absent après plusieurs minutes signifie soit que l'ouverture de
session automatique n'a pas eu lieu (revoir `50-power.ps1` : `AutoAdminLogon`,
`DefaultPassword`, absence d'`AutoLogonCount`), soit qu'elle a eu lieu mais
sur un bureau verrouillé — auquel cas le plus sûr est de **regarder** :
`virsh vncdisplay Windows-LTSC-test` donne le port VNC (`127.0.0.1` uniquement,
donc root-sur-l'hôte) pour vérifier à l'œil que l'écran de verrouillage n'est
pas affiché.

---

## 5. Test 2 — l'agent en session 1

⚠️ Le script `check-session.sh` de Guacamole **ne s'applique pas ici** : il
exige un montage CIFS (`/media/vm`) que la bascule vers l'appliance supprime,
et il lance un binaire de développement (`C:\dev\target\debug\agent.exe`) qui
n'existe pas sur cette machine — seul `C:\nivuus\agent\agent.exe`, l'artefact
de charge utile, y est présent.

```bash
python3 winrm_exec.py cmd 'type C:\nivuus\state\PROVISION.done'
python3 winrm_exec.py ps 'Get-Content C:\nivuus\agent.log -Tail 30'
python3 winrm_exec.py ps 'Get-Process agent -ErrorAction SilentlyContinue'
```

Critère : `PROVISION.done` porte `agent_session=1` (posé à la toute fin du
provisionnement, avant même ce redémarrage — la preuve que
`99-marker.ps1` exige), `agent.log` montre une activité récente et
cohérente (pas une erreur en boucle), et le processus `agent` est vivant.

---

## 6. Test 3 — reconstruction préservant D:

🔴 **Le piège de cette section : une erreur ici échoue silencieusement.**
`testdomain.py define` refuse de continuer si le disque qcow2 existe déjà
(« run teardown first ») — un opérateur pressé qui « nettoie » en lançant
`teardown` avant de relancer `define` supprimerait le disque entier, D: y
compris, et le test suivant réinstallerait un Windows tout neuf qui
« réussirait » sans jamais avoir rien préservé. Rien ne le signale au moment
où ça se produit — Windows Setup en mode `rebuild` échouerait certes s'il n'y
a vraiment aucune partition 3 à modifier sur un disque vierge, mais un disque
recréé à la même taille peut très bien en avoir une. **Le seul filet est la
comparaison de valeurs enregistrées avant/après**, ci-dessous : ne pas la
sauter.

🔴 **Second piège, plus insidieux : ce test peut passer au vert sans avoir
rien prouvé si personne ne s'est connecté à Steam.**
`D:\Steam\config\loginusers.vdf` — le jeton de session, la pièce maîtresse de
tout l'argument « C: jetable / D: persistant » de cette branche — n'existe
qu'après une connexion Steam réussie. `Get-Item` sur un chemin absent échoue
de façon déterministe, mais `winrm_exec.py` imprime sa sortie standard (vide)
et envoie l'erreur sur **stderr** ; un `tee` ou une substitution de processus
`<(...)` sans `2>&1` ne capture ni l'un ni l'autre. Si le fichier n'existe ni
avant ni après, les deux côtés du `diff` valent la chaîne vide et **le
critère « diff vide » est satisfait sans avoir rien mesuré**. Les commandes
ci-dessous capturent donc systématiquement stderr, et une étape explicite,
avant toute autre chose, garantit qu'il y a bien quelque chose à comparer.

**Avant de toucher à quoi que ce soit : se connecter à Steam et installer un
petit jeu.** Depuis la TV (streaming Apollo, comme au test 1), ouvrir Steam
(client normal ou Big Picture), se connecter avec un compte réel, et
installer un jeu de petite taille — l'objectif est d'exercer une écriture
réelle sur `D:\Steam`, pas la taille du téléchargement. Puis vérifier que la
connexion a bien laissé une trace **avant** de continuer :

```bash
python3 winrm_exec.py ps \
  '(Get-Item D:\Steam\config\loginusers.vdf -ErrorAction Stop).Length' 2>&1 | tee /tmp/loginusers-length.txt
if ! grep -qE '^[1-9][0-9]*$' /tmp/loginusers-length.txt; then
  echo "ABORT: loginusers.vdf est absent ou vide - se connecter à Steam avant de continuer, sinon tout ce test ne prouve rien" >&2
  exit 1
fi
```

Enregistrer ensuite l'état actuel (stderr capturé, pour la même raison) :

```bash
python3 winrm_exec.py cmd 'type D:\state\NIVUUS-DATA.id' 2>&1 | tee /tmp/nivuus-data-before.txt
python3 winrm_exec.py ps 'Get-Item D:\Steam\config\loginusers.vdf | Select LastWriteTime,Length' 2>&1 | tee /tmp/loginusers-before.txt
python3 winrm_exec.py ps 'Get-Item D:\state\apollo\sunshine_state.json | Select LastWriteTime,Length' 2>&1 | tee /tmp/sunshine-state-before.txt
```

Construire un **second** ISO, sans toucher au premier :

```bash
sudo python3 build.py \
  --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
  --drivers-dir /media/data/nivuus-win-payload \
  --output /media/data/iso/nivuus-unattend-rebuild.iso \
  --disk-mode rebuild --target-disk-verified
```

Sans `--target-disk-verified`, `build.py` refuse avec un message qui explique
pourquoi : rien en aval ne peut protéger la partition de jeux, parce que
Windows Setup repartitionne dès la passe `windowsPE`, bien avant qu'aucun
script côté invité ne s'exécute — la seule garde possible est procédurale, le
drapeau sur cette ligne de commande disant que l'opérateur a vérifié.
`--target-disk-verified` vient d'être vérifié juste au-dessus (précondition 3
+ le disque déjà en service depuis les tests 1/2/4b).

**Ne PAS relancer `testdomain.py define` ni `teardown`.** Rattacher
uniquement le nouvel ISO à l'instance existante, en laissant le disque
intact :

```bash
virsh destroy Windows-LTSC-test
python3 testdomain.py xml --unattend-iso /media/data/iso/nivuus-unattend-rebuild.iso > /tmp/rebuild-domain.xml
virsh define /tmp/rebuild-domain.xml
virsh start Windows-LTSC-test
```

Nouvelle attente au clavier (même média), puis nouvelle attente du marqueur —
c'est une réinstallation complète de C:, exactement comme aux étapes 1 et 2
ci-dessus :

```bash
for i in $(seq 1 40); do
  virsh send-key Windows-LTSC-test --codeset linux KEY_ENTER >/dev/null 2>&1
  sleep 1
done
export GUEST_IP=$(python3 testdomain.py wait-ready)
# ... boucle d'attente du marqueur identique à l'étape 2 ...
```

Puis, **sans aucun geste manuel côté invité** :

```bash
python3 winrm_exec.py cmd 'type D:\state\NIVUUS-DATA.id' 2>&1 | tee /tmp/nivuus-data-after.txt
diff /tmp/nivuus-data-before.txt /tmp/nivuus-data-after.txt

python3 winrm_exec.py ps 'Get-Item D:\Steam\config\loginusers.vdf | Select LastWriteTime,Length' 2>&1 | tee /tmp/loginusers-after.txt
diff /tmp/loginusers-before.txt /tmp/loginusers-after.txt

python3 winrm_exec.py ps 'Get-Item D:\state\apollo\sunshine_state.json | Select LastWriteTime,Length' 2>&1 | tee /tmp/sunshine-state-after.txt
diff /tmp/sunshine-state-before.txt /tmp/sunshine-state-after.txt

# The "before" side is not trusted blind either - it was asserted non-empty
# above, right after logging into Steam. Re-assert the "after" side the same
# way: a diff of two empty captures would still be vacuously empty.
for f in /tmp/loginusers-after.txt /tmp/sunshine-state-after.txt; do
  grep -q '.' "$f" || { echo "ABORT: $f is empty - the file is missing after rebuild, not merely unchanged" >&2; exit 1; }
done
```

Critère : les trois `diff` sont **vides**, ET les fichiers `*-after.txt` ne
sont pas vides — le marqueur porte sa date de création d'origine (`created=`
inchangé), `loginusers.vdf` et `sunshine_state.json` ont exactement les mêmes
horodatage et taille qu'avant la reconstruction. **Sans l'étape de connexion
Steam en tout début de section, ce critère est vide de sens** : un `diff`
vide entre deux absences se lit comme un succès sans jamais avoir mesuré
quoi que ce soit. Refaire ensuite le test 1 (sans repasser par l'IHM
d'appairage) : si la TV se reconnecte et diffuse sans redemander de PIN,
l'appairage a survécu à la reconstruction de C:.

---

## 7. Ce que cette recette ne prouve pas

- **Le démarrage à froid sans aucun écran.** Le domaine `Windows-LTSC-test`
  porte une VGA émulée (`<video><model type='vga'.../></video>` dans
  `templates/domain-test.xml.j2`) exactement comme l'invité jetable qui a
  fondé ce sous-projet le 2026-08-22. Tant que cette recette tourne sur ce
  domaine, elle ne peut donc pas dire si l'appliance démarre correctement
  sans aucun périphérique vidéo présent avant qu'Apollo ne crée l'écran
  virtuel — question que seul le domaine de production (sous-projet C, qui
  conserve délibérément un périphérique vidéo émulé pour cette raison) referme.
- **La pérennité de la jonction de configuration à travers une mise à jour
  d'Apollo.** `25-apollo.ps1` jonctionne `C:\Program Files\Apollo\config` vers
  `D:\state\apollo` une fois, à l'installation. Un Apollo qui se met à jour —
  hors du contrôle de cette recette, qui installe toujours la même version
  0.4.6 — peut recréer ce répertoire en place et casser la jonction, ce qui
  perdrait silencieusement les appairages au prochain redémarrage du service.
  Le risque est nommé dans la spec de conception (`docs/superpowers/specs/
  2026-08-22-windows-guest-provisionnement-design.md`, tableau des risques) ;
  cette recette ne l'exerce jamais et il devra être revérifié — relire la
  jonction avec `Get-Item 'C:\Program Files\Apollo\config'` et son attribut
  `ReparsePoint` — après chaque montée de version d'Apollo, pas seulement à
  l'installation initiale.

---

## 8. Nettoyage

```bash
sudo python3 installer/windows-guest/testdomain.py teardown

# Rendre le GPU à l'hôte.
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.StartUnit string:"nvidia-persistenced.service" string:"replace"
nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
docker start nivuus-ollama mediamanager-tdarr-1 \
             mediamanager-tdarr-node-1 mediamanager-tdarr-node-nvenc-1

# Réarmer les automatismes désactivés à l'étape 0. Sans ça, le réveil à la
# demande et l'hibernation auto restent cassés en permanence, pas seulement
# pendant la recette.
dbus-send $M.UnmaskUnitFiles array:string:"vm-idle-shutdown.timer","vm-trigger-47984.socket","vm-trigger-47989.socket" boolean:false
dbus-send $M.Reload
dbus-send $M.StartUnit string:"vm-idle-shutdown.timer" string:"replace"
dbus-send $M.StartUnit string:"vm-trigger-47984.socket" string:"replace"
dbus-send $M.StartUnit string:"vm-trigger-47989.socket" string:"replace"
```

⚠️ La régénération CDI n'est pas facultative : `nvidia_uvm` reçoit un majeur
**dynamique**, et une spécification figée fait renvoyer 999 à tout CUDA
pendant que `nvidia-smi` continue de fonctionner (voir CLAUDE.md).

`testdomain.py teardown` supprime le domaine, son varstore (`--nvram`) et le
fichier qcow2 — les deux ISO construits en §2 et §6
(`/media/data/iso/nivuus-unattend.iso` et `…-rebuild.iso`) ne sont **pas**
supprimés par ce nettoyage : ils portent trois secrets en clair (mode 0600) et
pourraient servir de base à la vraie bascule de production plus tard — décider
au cas par cas s'il faut les conserver ou les effacer, ce n'est pas cette
recette qui en décide.

---

## Résultat de la première exécution (2026-08-25)

### Ce qui est mesuré et passe

| Étape | Résultat |
| --- | --- |
| Précondition 1 — `agent.exe` dans la charge utile | `payload complete` — extrait de la VM de production le jour même (`/media/backup/agent-msvc-20260825/`, compilation MSVC depuis les sources à jour) |
| Précondition 2 — recette S4 | passée le 2026-08-25, voir `recette-s4.md` |
| Préconditions 3 et 4 | disque jetable absent au départ, 981 G libres, VM `Windows` hibernée |
| Construction — `fetch_payload.py` | Steam, WinFsp, virtio-win récupérés ; `netkvm`+`viofs` extraits |
| Construction — `build.py` | ISO 993 MiB, mode 600, **exactement trois** fichiers sous `/nivuus/config`, dix étages + trois ressources |
| Installation — OOBE | **franchi sans aucune intervention** |
| Étapes 15 à 55 | NetKVM lié, WinFsp+viofs installés, `D:` 140 GiB NTFS initialisé, Apollo 0.4.6 installé, SudoVDA `ROOT\DISPLAY\0001` statut OK, config jonctionnée vers `D:\state\apollo`, Steam installé dans `D:\Steam`, autologon permanent, mises à jour de pilotes exclues |
| Marqueur | `provision_version=B1`, `agent_session=1` |
| Test 4b — ouverture de session automatique | **passe** : `agent-session.txt` supprimé, redémarrage, réapparu à `1` en 3 min 40 sans qu'aucune commande n'ouvre de session ; `query session` montre la console 1 `Active` / `Administrator` |
| Hibernation | `hiberfil.sys` 6,86 Go présent, `powercfg /a` liste `Hibernate` |
| Pare-feu après le marqueur | les trois profils `ON` et 5985 joignable — la réouverture ciblée de `99-marker.ps1` fonctionne |

**Le correctif OOBE de 24H2 est désormais VÉRIFIÉ.** L'ISO du 22/08 s'arrêtait
sur « Is this the right country or region? » puis sur le clavier, et il a fallu
trois `virsh send-key KEY_ENTER` pour la franchir. L'ISO reconstruite avec
`Microsoft-Windows-International-Core` dans la passe `oobeSystem` va d'un trait
jusqu'au bureau, locale française appliquée.

### 🔴 Défaut bloquant trouvé et corrigé : `Test-Path` ne voit pas `hiberfil.sys`

`99-marker.ps1` a **refusé de certifier une appliance parfaitement saine**, une
heure après le début du provisionnement :

```
=== 99-marker.ps1 ===
PS>TerminatingError(): "hiberfil.sys is absent: hibernation is unavailable, and
the host would silently never be able to sleep this guest"
```

Or le fichier était là — 6 863 724 544 octets, créé à 14:50, cinq minutes avant
le contrôle. Mesure sur l'invité :

```
Test-Path 'C:\hiberfil.sys'                        -> False
[System.IO.File]::GetAttributes('C:\hiberfil.sys') -> Hidden, System, Archive, NotContentIndexed
dir /a C:\hiberfil.sys                             -> 6 863 724 544 octets
```

**Le fournisseur `FileSystem` de PowerShell filtre les éléments `Hidden` +
`System`, et `Test-Path` n'a pas de `-Force` pour passer outre** (le paramètre
n'existe pas : le lier lève `NamedParameterNotFound`). Le piège est d'autant
plus vicieux que le `if exist` de cmd, lui, voit le fichier — donc le contrôle
que cette recette prescrit à l'étape 4b passait au vert pendant que celui de
l'invité échouait sur le même fichier. Les deux sites (`50-power.ps1` en
avertissement, `99-marker.ps1` en refus fatal) emploient désormais
`[System.IO.File]::Exists`, avec une assertion de non-régression dans
`scripts/tests/test_windows_guest_provision.py`.

Reprise sans réinstaller : ISO reconstruite, média réinséré à chaud
(`virsh change-media … --eject` puis `--insert`), puis `resume.cmd` relancé en
session 1 par tâche planifiée — `run-all.ps1` saute les étages déjà faits et
ne rejoue que le dernier.

### 🔴 Test 2 — l'agent démarre en session 1 puis meurt aussitôt

Le critère est à moitié tenu. Ce qui passe : `PROVISION.done` porte bien
`agent_session=1`, et `agent.log` prouve que l'agent a été **invoqué dans la
session interactive**. Ce qui échoue : `Get-Process agent` ne rend rien, à
chaque ouverture de session. La cause est dans le journal de l'agent :

```
Error: aucune fenêtre visible dont le titre contient « firefox »
WARN agent: AGENT_VM ou AGENT_SECRET absent, et aucun AGENT_JETON hérité :
     aucun jeton d'agent. La plateforme REFUSERA la poignée de main et aucune
     session ne s'établira.
```

Deux manques distincts, aucun n'est une coquille :

1. **`assets/run-agent.ps1` ne pose ni `WINDOW_TITLE` ni `SUPERVISEUR`.**
   `agent/src/demarrage/source.rs` retombe alors sur le défaut `"firefox"` et
   `find_window_by_title` échoue — il n'y a pas de Firefox sur une appliance.
   L'agent sait pourtant fonctionner sans titre figé : le mode `SUPERVISEUR`
   découvre les fenêtres et lance un enfant par fenêtre, ce qui est le
   comportement qu'une appliance veut.
2. **Aucune identité de plateforme n'est provisionnée.** `AGENT_VM` et
   `AGENT_SECRET` sont des secrets par-VM ; rien dans B ne les dépose, donc
   même avec une fenêtre l'agent serait refusé à la poignée de main.

Choisir la cible de capture et la source de ces secrets est une décision de
conception, pas un correctif mécanique : elle appartient à la spec de bascule.
**Consigné comme dette, non corrigé à l'aveugle.**

### Mineur : la détection « owner changes » d'Apollo faux-positive à la première installation

```
WARNING: D:\statepollo\sunshine.conf had owner changes … - backed up to
         sunshine.conf.bak-20260825-145433 before overwriting it
```

Émis sur une installation **neuve**, où aucun propriétaire n'a rien pu modifier.
La sauvegarde est inoffensive (elle préserve au lieu d'écraser), mais le message
apprend à l'opérateur à ignorer un avertissement qui, lui, comptera lors d'une
reconstruction. À rendre silencieux quand `D:` vient d'être initialisé.

### Restent dus — ils exigent une présence humaine

- **Test 1 — HDR de bout en bout.** Il faut appairer la TV à *ce* domaine de
  test (PIN dans l'IHM Apollo `https://<GUEST_IP>:47990`), lancer un flux, puis
  relever `Client dynamicRange: 1` soutenu et la sonde `enabled=1 bpc=10`.
  L'infrastructure est prête et vérifiée : `ApolloService` tourne,
  `dd_configuration_option = ensure_only_display`,
  `isolated_virtual_display_option = disabled`, `dd_hdr_option = auto`, SudoVDA
  statut OK.
- **Test 3 — reconstruction préservant `D:`.** Il exige d'abord une connexion
  Steam réelle depuis la TV, sans quoi `loginusers.vdf` n'existe pas et le test
  passe au vert sans rien avoir mesuré (le document le dit déjà).

---

## Test 3 — ÉCHEC : `D:` n'est pas préservé (2026-08-25)

Exécuté avec toute la matière que le test exige : une connexion Steam réelle
(`loginusers.vdf`, 224 octets), un vrai jeu installé (Don't Starve Together,
appid 322330, 4,25 Go dans `steamapps\common`) et l'état enregistré avant.

| | Avant | Après |
| --- | --- | --- |
| `D:\state\NIVUUS-DATA.id` | `created=2026-08-25T14:53:19` | `created=2026-08-25T21:51:58` |
| `loginusers.vdf` | 224 octets | **absent** |
| `steamapps\common` | 4,25 Go | **0 Go** |
| appmanifests | 228980, 322330 | **aucun** |

**La partition de données a été effacée et recréée.** L'argument central de
cette branche — `C:` jetable, `D:` persistant — n'est pas tenu en l'état.

### La cause, et ce qu'elle n'est pas

Ni le fichier de réponses ni les scripts de l'invité n'en sont responsables,
c'est vérifié :

- L'autounattend de rebuild est correct : `WillWipeDisk=false`, **zéro**
  `CreatePartition`, un seul `ModifyPartition` visant `PartitionID 3`, et la
  seule lettre nommée est `C`. Comparé au mode wipe (5 créations, partitions
  1 à 4, lettres C et D), le contraste est net.
- `20-disk.ps1` ne formate rien : il crée `D:\state` et `D:\Steam` s'ils
  manquent et pose le témoin s'il est absent. Il a d'ailleurs journalisé
  « D: is 140 GiB NTFS » puis « D: initialised (first install) » — la partition
  lui est donc arrivée **déjà vide**, avec la bonne taille.

La cause est **Windows Setup lui-même**, dans la passe windowsPE.
`C:\Windows\Panther\setupact.log` montre l'opération « Setup the recovery
partition » rejouée pendant ce rebuild, et la table de partitions finale
explique le reste :

```
1  System      0,3 GiB
2  Reserved    0   GiB
3  C         199,4 GiB
4  Recovery    0,6 GiB
5  D         139,7 GiB
```

**La partition de données est la 5, pas la 4.** Setup rétrécit la partition
Windows pour glisser sa partition Recovery juste après elle (199,4 + 0,6 = les
200 GiB demandés), ce qui décale la partition de données. À la reconstruction,
Setup recrée cette Recovery — et l'opération emporte ce qui la suit.

### Le remède, non appliqué

Le correctif ne peut pas être dans un script d'invité : tout se joue avant
qu'aucun ne s'exécute. Il faut que la partition de données **précède** la
partition Windows, puisque Setup ne réorganise jamais ce qui est en amont
d'elle — disposition `Data, System EFI, MSR, Windows`, Setup ajoutant sa
Recovery en dernier, où elle ne gêne personne.

C'est un changement de la disposition des partitions dans `autounattend.py`,
donc du mode wipe autant que du mode rebuild, et il exige un cycle complet
d'installation pour être vérifié. **Décision du propriétaire requise avant de
l'appliquer** — il touche du partitionnement destructif.

⚠️ Tant qu'il n'est pas fait et vérifié, **le mode rebuild ne doit pas être
présenté comme préservant quoi que ce soit**, et la bascule ne doit pas
s'appuyer sur cette propriété.

## Test 1 — HDR : PASSÉ (2026-08-25)

Depuis la TV, sur un flux réel, sans rien forcer :

```
sizes rc=0 paths=1 modes=2
target=261 rc=0 supported=1 enabled=1 bpc=10  name=TV (2)
```

et côté hôte, `Client dynamicRange: 1, Display is HDR: true`,
`Color coding: HDR (Rec. 2020 + SMPTE 2084 PQ)`, `Color depth: 10-bit`,
`NvEnc: created encoder AV1 P1 async 10-bit two-pass rfi`, écran 3840x2160 à
60 Hz, luminance maximale 3805 nits.

`paths=1` prouve qu'`ensure_only_display` a bien désactivé tout le reste.
`rc=0` prouve que l'API Advanced Color répond, contre le `rc=31
ERROR_GEN_FAILURE` que Server 2022 rendait systématiquement. Et
**`enabled=1 bpc=10`** est du 10 bits réel, pas une capacité annoncée. La
sonde ne fait que lire — elle n'appelle jamais
`DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE` —, donc c'est ce que le client a
obtenu, pas ce que Windows sait faire quand on le lui impose. C'est la
différence avec la mesure du 2026-08-22 qui avait fondé ce sous-projet.

⚠️ Une session ultérieure est retombée en SDR (`dynamicRange: 0`, Rec. 709,
8 bits) avec `Display is HDR: true` inchangé : c'est le client qui demande du
SDR, pas l'hôte qui échoue. Vérifier le réglage HDR côté TV avant de conclure.
