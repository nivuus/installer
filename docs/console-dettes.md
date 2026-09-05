# Console — dettes connues (côté invité Windows)

Ce que l'invité Windows de la console ne fait pas encore, et que personne n'a
planifié. Un plan sous `docs/superpowers/` décrit un travail engagé ; ce fichier
décrit un manque constaté, pour qu'il ne se redécouvre pas trois fois.

Le pendant côté bibliothèque de jeux vit dans `nivuus/retro` :
`docs/dettes.md`. Les deux fichiers se citent l'un l'autre parce que
trois de ces dettes traversent la frontière des deux dépôts — la manette est
créée ici et consommée là-bas.

---

## C1 — Aucune vibration ne remonte au client Moonlight

**Constaté le 2026-08-28.** La manette ne vibre ni dans les jeux Steam, ni dans
les émulateurs. Le maillon qui appartient à ce dépôt est le retour **Apollo →
client** : le rumble voyage à contresens du reste du flux, et c'est le seul
chemin de la chaîne qui remonte.

**Ce qui est déjà établi et n'est donc pas en cause :** ViGEmBus est installé et
vérifié au provisionnement (`console/guest/provision/assets/apollo-drivers.ps1`
échoue bruyamment si le matériel `Nefarius\ViGEmBus\Gen1` manque), et le pad
virtuel est bien créé à chaque session (`sunshine.log` : `Gamepad 0 will be
Xbox 360 controller (default)`).

**Ce qu'il reste à mesurer, dans cet ordre :**

1. Le client Moonlight utilisé a-t-il le retour de force activé ? C'est un
   réglage du client, sur l'appareil du salon — pas du serveur.
2. Apollo émet-il l'événement ? Il le journalise ; `min_log_level = info` dans
   `console/guest/templates/sunshine.conf.j2` devrait suffire à le voir.
3. Le jeu l'émet-il ? À isoler avec un titre dont on sait qu'il vibre sur un pad
   physique — sinon on débogue trois inconnues à la fois.

**Ce que ça coûte :** rien ne bloque une partie. Mais c'est la différence entre
une console et un émulateur en plein écran.

### Ce qui a été fait le 2026-08-29, et ce qui ne l'a pas été

Rien n'a été mesuré. Aucune des trois questions ci-dessus n'a de réponse, et
aucun maillon n'est réparé. Ce qui a changé est plus modeste : le défaut est
désormais **observable**, et le gabarit le dit.

- `console/guest/templates/sunshine.conf.j2` posait déjà `min_log_level = info`
  — la précondition de l'étape 2 est donc satisfaite **dans le dépôt**. Elle ne
  portait en revanche aucune justification, seule clé du fichier dans ce cas :
  elle se relisait comme un reliquat de mise au point, et l'abaisser « pour
  alléger le journal » n'aurait montré aucun symptôme. Le gabarit dit
  maintenant à quoi ce niveau sert, et ce qui n'est pas mesuré à son sujet.
- `console/tests/test_windows_guest_apollo.py` épingle la valeur **et** la
  phrase qui l'explique. Le test a été écrit avant la phrase et vu rouge ;
  ramené à `warning`, le gabarit le fait échouer.
- **Aucune clé n'a été ajoutée.** La règle de l'en-tête du gabarit — vérifiée
  présente dans la version d'Apollo installée, jamais recopiée d'une recette —
  interdit d'en poser une sans avoir lu le binaire de l'invité.
- Côté `nivuus/retro`, la moitié D1 du même défaut a reçu le même traitement :
  aucun réglage de rumble n'y est posé — le nom de la clé dépend, émulateur par
  émulateur, d'un relevé jamais fait — mais les neuf profils livrés hors
  DuckStation disent désormais, dans leur bloc `[input]`, qu'il manque, où il
  ira, et ce qu'il reste à relever.

⚠️ **Ce qui reste non vérifié côté invité :** que le `sunshine.conf`
effectivement rendu dans la machine porte bien `min_log_level = info`. Il est
rendu au provisionnement, et l'invité peut tourner sur une version de payload
antérieure à celle du dépôt. **Relever la version de provisionnement de
l'invité avant de conclure quoi que ce soit d'une absence dans le journal.**

### Procédure de mesure

À jouer une fois, dans cet ordre, sans en sauter. L'ordre est celui de la liste
ci-dessus et il n'est pas négociable : chaque étape n'a de sens que si la
précédente a répondu.

**À noter avant de commencer**, sinon la mesure ne vaudra rien : quel client
Moonlight (application, version, appareil du salon), quelle manette physique,
quel jeu, et quelle version de provisionnement porte l'invité.

**Étape 0 — ne pas remesurer ce qui est acquis.** ViGEmBus est installé et
vérifié (`console/guest/provision/assets/apollo-drivers.ps1` échoue bruyamment
sans `Nefarius\ViGEmBus\Gen1`), et le pad virtuel est créé à chaque session
(`Gamepad 0 will be Xbox 360 controller (default)`). Ces deux maillons ne sont
pas en cause.

**Étape 1 — le client Moonlight. C'est au propriétaire, et c'est gratuit.**
C'est le seul des trois qui se teste **sans rien modifier sur le serveur**.

1. Sur l'appareil du salon, ouvrir les réglages du client Moonlight et trouver
   l'interrupteur de vibration / retour de force. Son libellé varie d'un client
   à l'autre (Android, iOS, PC, TV) et **n'est pas relevé ici** : le noter tel
   qu'il est écrit à l'écran.
2. Noter son état **avant** d'y toucher — c'est cela, la mesure.
3. S'il était éteint : l'allumer, puis rejouer le titre de référence de
   l'étape 3. S'il vibre, C1 est close, et D1 perd son maillon Steam du même
   coup.
4. S'il était déjà allumé : le noter, ne rien changer, passer à l'étape 2.

**Étape 2 — Apollo émet-il l'événement ?** Session ouverte, manette branchée au
client, lancer le titre de référence et jouer jusqu'à un moment qui **doit**
faire vibrer. Puis lire le journal d'Apollo sur l'invité.

- ⚠️ **RECTIFIÉ le 2026-08-29 — cette étape envoyait vers un fichier VIDE.**
  Elle disait de chercher `sunshine.log` dans le dossier de configuration
  d'Apollo, jonction vers `D:\state\apollo`
  (`console/guest/provision/assets/apollo-junction.ps1`), en précisant que le
  chemin n'avait pas été vérifié. Il l'est maintenant, et il était faux :

  | Chemin | Ce qui y est |
  |---|---|
  | `C:\Windows\Temp\sunshine.log` | **le vrai journal** — 12 Ko, vivant |
  | `D:\state\apollo\sunshine.log` | **0 octet** |

  Le second existe, ce qui est le piège : une lecture y trouve un fichier, pas
  une erreur. Zéro occurrence de rumble dans un fichier de zéro octet ne prouve
  rien du tout, et se lirait pourtant comme la réponse à l'étape 2. **Lire
  `C:\Windows\Temp\sunshine.log`.**

  Ce qui n'est PAS su : pourquoi Apollo écrit là plutôt que dans son dossier de
  configuration, si c'est un défaut de compilation ou une conséquence du
  service, et si `D:\state\apollo\sunshine.log` se remplit dans d'autres
  conditions. Le chemin est **relevé**, pas expliqué — à re-mesurer avant de le
  figer dans un gabarit.
- Vérifier d'abord, dans ce `sunshine.conf`-là — celui de la machine, pas celui
  du dépôt — que `min_log_level` vaut bien `info`.
- **Compter les occurrences sur le fichier COMPLET, jamais sur ses premières
  lignes.** Le piège a déjà été payé le 2026-08-28 côté `nivuus/retro` : une
  absence lue deux secondes après le démarrage a été prise pour un succès alors
  que le fichier en comptait 272 une minute plus tard.
- **Rien dans le journal ne prouve rien à lui seul.** Il n'est pas mesuré que
  l'événement de rumble soit journalisé à `info` plutôt qu'à `debug`. Une
  absence à `info` peut ne prouver que la verbosité. Rejouer une session avec
  `min_log_level = debug` tranche — sauvegarde nommée du fichier avant,
  restauration après.

**Étape 3 — le jeu émet-il ?** À isoler avec **un titre dont on sait qu'il
vibre sur un pad physique** — vérifié sur ce pad, sur une autre machine,
**avant** la session. Sans cette vérification préalable, le jeu est une
quatrième inconnue et on débogue trois inconnues à la fois : c'est exactement ce
que cette dette existe pour éviter.

**Où écrire le résultat :** ici, dans cette entrée. Une mesure qui n'est pas
écrite se refait.

---

## C2 — Apollo jette le gyroscope que la manette du salon lui envoie

> 🚪 **PORTE : C2 EST BLOQUÉE DERRIÈRE C7. Ne bascule pas le type de manette
> tant que la console n'a pas été reconstruite en B4.** Arbitré le 2026-09-05,
> et ce n'est pas une précaution de principe : le filet censé rattraper la
> bascule — garde anti-GUID, `pad_releve`, `lire_pads`, `pads.txt` écrit par
> `joyGetDevCapsW` — est livré sur `main` de `nivuus/retro`, **dans le dépôt**,
> et le dépôt n'est pas sur la console (C7 : elle tourne encore sur
> `PROVISION_VERSION=B1`, du 2026-08-26). Retirer l'épingle `x360` maintenant,
> ce serait le faire sur une machine **dépourvue du filet**, avec une
> configuration RPCS3 posée à la main qui ne vit dans aucun fichier versionné —
> elle mourrait en silence. Et ce dépôt sait où ça mène : *une manette muette
> n'a jamais une seule cause possible* (leçon de D3, payée deux fois).
>
> **L'ordre imposé est donc :**
> 1. **C7** — reconstruire l'invité jusqu'à B4 ;
> 2. **prouver le filet de `nivuus/retro` VIVANT sur la console**, pas
>    seulement présent dans son dépôt ;
> 3. **puis** basculer, en suivant le runbook du § C2.6 ci-dessous — qui est la
>    procédure de la **troisième** marche, jamais de la première.

**Constaté le 2026-08-28, et le titre a été corrigé le 2026-09-05 :** il
promettait « ni gyroscope, ni tactile », c'est-à-dire une moitié que C2 ne peut
pas livrer. `sunshine.log` disait `Gamepad 0 will be Xbox 360 controller
(default)` à chaque session. Un pad X360 émulé **n'a pas de capteur de
mouvement**, pas de pavé tactile, pas de retour haptique fin — et les émulateurs
qui en dépendent (PPSSPP, RPCS3, Vita3K) ne recevront jamais rien, quoi qu'on
règle de leur côté.

**Ce que C2 peut livrer, et c'est mesuré :** le **gyroscope**. La manette du
salon envoie déjà ses capteurs — Apollo le dit dans ses propres mots, voir le
dossier au § C2.3 — et c'est l'épinglage `x360` qui les jette.

**Ce que C2 ne peut PAS livrer, et ce n'est pas son ressort :** le **pavé
tactile**. Aucun avertissement `has a touchpad, but it is not usable` n'apparaît
dans le journal, alors qu'Apollo en émet un quand le cas se présente : **ce pad
n'a pas de pavé tactile**. C'est du matériel de salon, pas un réglage de
l'invité. Aucune bascule de C2 ne le fera apparaître, et une dette qui promet
l'impossible ne se clôt jamais.

**PCSX2 sort aussi de cette liste, corrigé le 2026-08-29 côté `nivuus/retro` :**
la PS2 n'avait aucun capteur de mouvement, `pcsx2/SIO/Pad/` ne contient aucun
périphérique de capteur. Il n'y a rien à y régler, et ce n'est pas un manque.

**Où ça se joue :** `console/guest/templates/sunshine.conf.j2` ne pose **aucune**
clé de type de manette ; Apollo retombe donc sur son défaut. La clé existe dans
Apollo 0.4.6 et permet d'annoncer une DualShock/DualSense. Toute clé ajoutée à
ce gabarit suit la règle de son en-tête : **vérifiée présente dans la version
d'Apollo installée**, jamais recopiée d'une recette.

**Ce que ce changement casse ailleurs, et qui interdit de le faire seul :**
changer le type de pad change son VID/PID, **donc son GUID SDL**, donc tous les
identifiants que les configurations d'entrée des émulateurs contiennent. Le plan
des manettes de `nivuus/retro` en a fait son fait n° 2 : « cet identifiant n'est
pas prévisible ». Une console qui bascule en DualShock avec des gabarits écrits
pour un X360 redevient muette **partout**, sans un message.

**Ordre imposé :** d'abord une manette qui répond dans chaque émulateur (dette
D3 de `nivuus/retro` : DuckStation reste muet), ensuite le type de pad, ensuite
le mouvement. Cette bascule est un point de synchronisation entre les deux
dépôts, pas un réglage local.

### C2 EST DÉBLOQUÉE — 2026-08-29

**D3 est close côté `nivuus/retro` : la manette répond dans DuckStation.**
Crash Team Racing, confirmé par le propriétaire. La première marche de l'ordre
imposé ci-dessus est franchie, et C2 n'attend donc plus une autre dette : elle
est la prochaine.

Il a fallu deux choses, et pas une, ce qui vaut d'être su ici parce que ça dit
à quoi ressemble le prochain débogage : les vingt-sept liaisons relevées, **et**
`[Pad1] ForceAnalogOnReset = false`. Les liaisons étaient justes pendant que le
jeu restait muet. **Une manette muette n'a jamais une seule cause possible.**

**Ce que la clôture NE change PAS, et qui reste le coût de C2 :** basculer
Apollo en DualShock change le VID/PID, donc le GUID SDL, donc les identifiants
des configurations d'entrée des émulateurs. Une seule nuance, mesurée le
2026-08-29 : les liaisons de DuckStation ne portent qu'un **index** (`SDL-0`),
jamais un GUID — elles ne se cassent donc pas mécaniquement au changement de
type de pad. Ce qui les casse, c'est un pad **de plus** énuméré avant celui
d'Apollo. Les huit autres émulateurs n'ont aucun relevé et restent entiers.

**Ce qui n'est toujours pas vérifiable dans les conditions réelles :** aucune
session Moonlight ne s'ouvre — `403 Permission denied` au `/launch`, le client
appairé portant `perm=0x3000000` là où les clients fonctionnels portent
`0x7131f00`. Toute mesure de C2 au flux passe d'abord par ce `perm`.

### Le dossier de la bascule — mesuré le 2026-09-04, sur l'invité de production

Ce qui suit ne décide rien : c'est ce qu'il fallait mesurer **avant** de
décider. La bascule n'a **pas** été jouée. Elle appartient au propriétaire,
parce qu'elle se joue à deux dépôts.

#### 1. La demi-marche déjà franchie, et ce qu'elle a déjà réglé

Le 2026-08-30, `sunshine.conf.j2` a reçu `gamepad = x360` — **épingler**, pas
basculer. Vérifié le 2026-09-04 dans le journal de l'invité :

    config: [gamepad] -- [x360]
    Info: config: 'gamepad' = x360
    [2026-09-02 19:28:44] Info: Gamepad 0 will be Xbox 360 controller (manual selection)

`(manual selection)` a remplacé `(default)`. Ce que ça règle : le type ne
change plus tout seul (le 2026-08-30, il avait basculé seul en DualShock 4
« auto-selected by motion sensor presence » entre deux sessions). Ce que ça ne
règle pas : c'est toujours un X360, donc toujours sans capteur.

#### 2. La clé, ses valeurs, et la règle de l'en-tête — VÉRIFIÉES SUR LE BINAIRE

L'en-tête de `sunshine.conf.j2` exige qu'une clé soit vue présente dans
l'Apollo **installé**. Elle l'est, trois fois :

| Preuve | Ce qu'elle dit |
|---|---|
| `sunshine.exe` FileVersion | **0.4.6** |
| `assets\web\assets\config-*.js` | l'`<option>` `gamepad` n'offre, **sous `windows:`**, que `auto`, `ds4`, `x360` |
| `sunshine.log` | Apollo a **accepté et journalisé** `'gamepad' = x360` |

⚠️ **Le piège que le fichier de langue tend, et qu'il faut nommer** :
`locale\en.json` porte aussi `gamepad_ds5` (DualSense), `gamepad_switch` et
`gamepad_xone`. **Ce sont des valeurs Linux.** Le composant Vue ne les rend que
dans son créneau `linux:`, et une recherche de la chaîne `xone` dans
`sunshine.exe` rend **zéro** occurrence. Sur Windows, le choix est
binaire : **x360 ou ds4**. Recopier `ds5` d'une lecture du fichier de langue
donnerait une valeur ignorée en silence — exactement ce que la règle interdit.

#### 3. Ce que la bascule rapporterait — Apollo le dit lui-même

Les chaînes de `sunshine.exe` contiennent ce couple, et il est décisif :

    Gamepad %d has motion sensors, but they are not usable when emulating an Xbox 360 controller
    Gamepad %d has a touchpad, but it is not usable when emulating an Xbox 360 controller

Et le journal de la console, `sunshine.log.backup`, **2026-09-01 12:27:44 puis
12:28:30**, deux sessions :

    Info:    Gamepad 0 will be Xbox 360 controller (manual selection)
    Warning: Gamepad 0 has motion sensors, but they are not usable when
             emulating an Xbox 360 controller

**La manette du salon envoie déjà ses capteurs, et c'est l'épinglage x360 qui
les jette.** Le plan D4 de `nivuus/retro` (tâche 5) écrit que les trois
maillons du mouvement — (a) le client envoie, (b) Apollo reçoit, (c) le rapport
ViGEm porte les champs — sont **tous non mesurés**. Deux le sont désormais :
(a) et (b) sont établis par cet avertissement, qu'Apollo n'émet qu'après avoir
lu la présence des capteurs dans ce que le client lui a annoncé.

**Ce qui reste non mesuré, et qui ne l'est pas ici :** (c). Aucune session
DualShock n'a jamais tourné sur cette console.

**Et une deuxième chose que la bascule ne rapporterait PAS :** il n'y a **aucun**
avertissement `has a touchpad, but it is not usable` dans le journal. Le pad du
salon annonce des capteurs de mouvement, **pas** de pavé tactile. Le titre de
cette dette promet « ni gyroscope, ni tactile » : seul le premier est
récupérable avec ce pad-là.

#### 4. Ce que la bascule coûterait — et c'est ce qui appartient à `nivuus/retro`

`gamepad = ds4` fait créer par ViGEmBus un périphérique `VID_054C&PID_05C4` au
lieu de `VID_045E&PID_028E`. Conséquences, dans l'ordre de gravité :

1. **Un DualShock 4 ViGEm n'est pas énuméré par XInput.** Les émulateurs de
   cette console sont configurés en XInput, et l'index XInput **est** le numéro
   de port — c'est ce qui rend l'ordre des quatre manettes déterministe. On
   perd cela.
2. **RPCS3 meurt en silence.** Son `Default.yml` porte `Handler: XInput` +
   `Device: "XInput Pad #1"`, **posé à la main sur la machine**, donc dans
   aucun fichier versionné et sous aucune garde (`nivuus/retro`, dette D7).
   C'est le premier à casser, et rien ne le dira.
3. **DuckStation survit, probablement.** Ses vingt-sept liaisons ne portent
   qu'un **index** (`SDL-0`), jamais un GUID — relevé sur le binaire le
   2026-08-29, et resté « à confirmer ».

#### 5. L'état du filet, côté `nivuus/retro` — VÉRIFIÉ le 2026-09-04

Le plan D4 (`docs/superpowers/plans/2026-08-29-d4-mouvement-et-type-de-pad.md`)
impose : « les tâches 1 à 4 sont livrées **avant** que la bascule ait lieu. Un
filet posé après la casse ne sert qu'à la constater. » Elles **le sont**, sur
`main` de `nivuus/retro` :

| Tâche | Preuve dans le dépôt |
|---|---|
| 1 — garde anti-GUID | `tests/test_donnees.py` |
| 2 — `[input] pad_releve` | `retro/profiles.py` (`PADS_CONNUS`), et `duckstation.toml` + `rpcs3.toml` déclarent `pad_releve = "x360"` |
| 3 — lire le témoin | `retro/launcher.py` : `TEMOIN_PADS`, `lire_pads` ; `retro/status.py` |
| 4 — l'écrire | `retro/data/launcher/retro-launch.cs` : `joyGetNumDevs` / `JOYCAPSW` → `pads.txt` |

**Le filet est donc dans le dépôt. Il n'est pas prouvé sur la console** — le
lanceur installé sur l'invité n'a pas été relevé, et D5 a déjà montré une fois
que la console faisait tourner un `retro` périmé. **C'est exactement ce constat
qui a fait poser la porte C7 en tête de cette dette** : un filet qui n'est pas
sur la machine ne rattrape rien de ce qui y tombe.

#### 6. Le runbook de la bascule — TROISIÈME marche, jamais la première

⚠️ **Ne rien jouer de ce qui suit tant que la porte du haut de § C2 n'est pas
franchie** : C7 d'abord (l'invité reconstruit en B4), puis le filet de
`nivuus/retro` prouvé vivant *sur la console*. Ce runbook suppose ces deux
marches faites. À ne PAS jouer depuis ce dépôt : écrit pour le chantier `retro`.

1. **Avant tout, et une seule fois : D1 mesure la vibration sous le pad
   ACTUEL**, sur DuckStation. C'est la seule fenêtre où ce maillon est
   mesurable sans deux inconnues (plan D4, « L'ordre avec D1 », point 1).
2. **Prouver le filet sur la console, pas seulement dans le dépôt** : que le
   lanceur installé sur l'invité soit bien celui qui écrit `pads.txt`
   (`retro identite` + `_launcher\pads.txt` présent après un lancement).
   ⚠️ WinRM ouvre une **session 0**, où aucune manette n'existe : toute
   vérification passe par `schtasks /it`, sinon elle rapportera zéro manette
   sur une console dont le pad est sain.
3. **Sauver RPCS3 AVANT la bascule** : son `Default.yml` (`Handler: XInput`,
   `Device: "XInput Pad #1"`) doit entrer dans un fichier versionné, sans quoi
   il casse sans laisser de trace. Le plan D4 le note déjà : sous SDL il
   deviendra `Handler: SDL`, ce qui change aussi `Device:` — et les valeurs
   d'`Axis` du bloc mouvement ne sont **pas relevées** et ne se devinent pas.
4. **Puis, seulement, dire à ce dépôt-ci de poser `gamepad = ds4`** dans
   `console/guest/templates/sunshine.conf.j2` — une ligne, plus son test
   (`test_windows_guest_apollo.py` épingle aujourd'hui la valeur `x360`, il
   faut le faire bouger dans le même geste).
5. **Relever, immédiatement après**, dans `C:\Windows\Temp\sunshine.log` (et
   **pas** `D:\state\apollo\sunshine.log`, qui fait 0 octet) :
   - `Gamepad 0 will be DualShock 4 controller (manual selection)` ;
   - l'**absence** du `has motion sensors, but they are not usable` ;
   - et si un `is emulating a DualShock 4 controller, but the client gamepad
     doesn't have motion sensors active` apparaît, la bascule n'a rien rapporté
     et il faut reculer.
6. **Puis les tâches 6 et 7 du plan D4** (compter les pads, confirmer
   l'exception DuckStation avec Crash Team Racing).

**Reculer tient en une ligne** : `gamepad = x360`. Ce qui ne recule pas tout
seul, ce sont les fichiers d'entrée que la bascule aura fait réécrire.

---

## C3 — Le fond d'écran de la console est hors charte — RÉGLÉ le 2026-08-28

**Ce que c'était :** `console/guest/assets/wallpaper.png` n'avait pas été dessiné
d'après la charte Nivuus et n'avait aucune source — un PNG versionné, sans le
document qui l'a produit. Il contredisait quatre règles : le nom en capitales,
un dégradé, un filet bleu, une graisse qui n'était pas Chillax.

**Ce qui a été fait :** la source vit désormais dans `nivuus/design`,
`assets/nivuus-fond-console.svg`, et sa décision est consignée en § 8 de
`docs/brand.md`. Le rendu 4K se refait d'une commande, sans navigateur ni fonte
installée — les tracés sont déjà vectorisés :

    rsvg-convert -w 3840 -h 2160 assets/nivuus-fond-console.svg -o wallpaper.png

Le chemin de destination n'a pas bougé : `payload.py` le liste toujours, le
provisionnement le copie toujours en `C:\nivuus\wallpaper.png`.

**Ce qui a changé dans CE dépôt, et qui n'est pas qu'une image :**
`steam-shell.ps1` peignait `#0E1117` à deux endroits — le fond de la fenêtre et
celui du bandeau de maintien. Les deux sont passés à `#000`. La raison est
mesurable, pas esthétique : l'image est affichée en `BackgroundImageLayout =
'Zoom'`, donc la couleur de fond de la fenêtre **apparaît en bandes latérales**
dès que le client n'est pas en 16:9 — le téléphone en 2410×1080 en est un. Un
fond d'écran noir pur dans une fenêtre gris-bleu a une couture visible sur ces
clients-là. Les deux valeurs sont désormais solidaires : changer l'une sans
l'autre ramène la couture.

**Rectification, pour qui lirait la version précédente de cette page :** il y
était écrit que le script « surimprime » ses messages au centre et qu'il fallait
donc un fond calme au milieu. C'est faux. `steam-shell.ps1` ancre un bandeau
**opaque** en bas (`Dock = 'Bottom'`, hauteur 64), et c'est le dixième INFÉRIEUR
de l'image qui doit rester vide — pas son centre. Le wordmark s'arrête à
y = 1238 sur 2160, ce qui tient même sur une session 720p, où le bandeau
recouvre l'équivalent de 192 px de l'image 4K.

---

## C4 — Le curseur de la souris reste posé sur Big Picture — RÉGLÉ le 2026-08-29

**Ce que c'était.** En mode `Steam Big Picture`, le pointeur restait visible
par-dessus l'interface, au milieu de l'écran de la TV, pendant toute la partie.
Big Picture se pilote entièrement à la manette : ce curseur ne servait à rien et
ne bougeait pas. Le défaut n'était pas neuf — l'`apps.json` écrit à la main du
2026-07-23 faisait porter à l'entrée `Steam Big Picture` un `nomousy` en plus de
`-bigpicture`, et le passage au gabarit `apps.json.j2` l'a laissé tomber sans
que rien ne le signale.

**Ce qui a été fait.** Le masquage est revenu **dans la boucle de surveillance**
de `console/guest/provision/assets/steam-session.ps1`, jamais devant elle, sur
le patron que la maximisation suivait déjà (`$maximized` / `Set-SteamMaximized`
→ `$cursorHidden` / `Set-CursorHidden`). Les deux fonctions vivent dans un
fichier voisin, `console/guest/provision/assets/steam-cursor.ps1`, qui porte
l'en-tête complet ; le découpage n'a pas d'autre motif que la limite de 200
lignes par fichier, la même qui avait sorti `apollo-drivers.ps1` de
`25-apollo.ps1`. `25-apollo.ps1` le copie désormais à côté de
`steam-session.ps1` (il est dot-sourcé par `$PSScriptRoot`) et
`payload.verify_staged` l'exige au staging — sans quoi il aurait pu disparaître
exactement comme le `nomousy` de 2026-07-23.

Le mécanisme : `CreateCursor` fabrique un curseur **entièrement transparent**
(plan AND à 1, plan XOR à 0), que `SetSystemCursor` pose sur les treize
identifiants `OCR_*` de `winuser.h` — tous, pas seulement la flèche, Big Picture
affichant un sablier pendant ses chargements. Aucun fichier `.cur`, aucun
binaire : `user32.dll` est déjà là.

**Le masquage suit `-Mode`, et il est réversible dans les deux sens.** Le mode
`Desktop` lance le client Steam normal, qui se pilote à la souris : il ne masque
rien, **et il restaure les curseurs à chaque démarrage**. Ce filet n'est pas
décoratif — Apollo tue le groupe de processus de la commande suivie quand le
client se déconnecte, donc une session Big Picture peut mourir sans jamais
atteindre sa propre restauration (`SPI_SETCURSORS`, en bas du script). Le mode
qui a besoin de la souris repart donc toujours de curseurs relus du registre.

**Les deux autres leviers ont été écartés sur mesure, pas par principe :**

- **`nomousy`** : cherché sur l'invité le 2026-08-29 (`C:\nivuus`, `C:\Program
  Files\Apollo`, `C:\Windows`, `D:\`, et `Get-Command`) — **absent**. Il aurait
  donc fallu l'embarquer, l'empreindre et le vérifier dans une charge utile qui
  s'installe hors ligne, pour ce que trois appels `user32` font déjà. **Et
  l'arbitrage ne bascule pas si cette lecture était fausse** : même présent
  quelque part sur l'invité, il resterait un exécutable tiers non versionné,
  qu'aucune étape de provisionnement ne pose ni ne vérifie — donc à ajouter au
  payload de toute façon pour qu'une reconstruction le retrouve.
- **Une clé Apollo de capture du curseur : elle n'existe pas dans 0.4.6.** La
  table des options que le binaire lit a été extraite des chaînes de
  `C:\Program Files\Apollo\sunshine.exe` (version relevée : 0.4.6) — elle va de
  `qp` à `locale`, se termine sur le message `Warning: Unrecognized configurable
  option [`, et **ne contient aucune clé de curseur**. Les plus proches sont
  `mouse`, `keyboard`, `controller`, qui activent des périphériques d'entrée.
  Rien non plus côté IHM web (`assets/web/.../config-*.js` et `locale/en.json`
  n'ont pas une occurrence de `cursor`). `sunshine.conf.j2` n'a donc **pas** été
  touché : une clé inventée y serait ignorée en silence, ce que son en-tête
  interdit.

**Ce que ça ne fait pas, et qu'il faut savoir avant de s'en étonner.** La dette
posait la distinction : *cacher le curseur dans l'invité n'est pas cesser de le
capturer dans le flux*. Elle est tranchée par le point précédent — la seconde
voie n'existe pas dans Apollo 0.4.6. Le prix du levier retenu est donc réel :
pendant une session Big Picture, une prise de main par le bureau distant (agent
Guacamole) voit un pointeur **invisible, qui pointe quand même**. Il redevient
visible dès la fin de la session Big Picture, ou dès le lancement suivant de
l'application `Desktop`. La voie Apollo reste ouverte pour la version qui
introduirait la clé.

**Ce qui a été vérifié sur l'invité, et ce qui ne l'a pas été.** Vérifié le
2026-08-29 par WinRM, dans la **session 0** (jamais la session interactive, qui
appartient au streaming) : le `Add-Type` compile, `GetSystemMetrics` rend
32 × 32, `Set-CursorHidden` rend `True` — les treize `SetSystemCursor` passent —
et `Restore-SystemCursors` s'exécute sans erreur ; les deux `.ps1` finaux ont
été transmis octet pour octet (9423 et 5314) et analysés par le parseur
PowerShell de l'invité, **zéro erreur de syntaxe**.

**Le défaut est confirmé sur la machine réelle, pas seulement dans le dépôt.**
L'`apps.json` que l'invité exécute aujourd'hui est bien celui rendu depuis le
gabarit — deux entrées, `-Mode Desktop` et `-Mode BigPicture`, et **aucune trace
de `nomousy`**. C'est exactement la régression décrite plus haut, lue sur le
fichier déployé.

**Ce qui n'est pas vérifié, et ne peut pas l'être d'ici :** le rendu réel dans
un flux Big Picture, faute de session Moonlight ouverte.

### ⚠️ Le correctif a été DÉPLOYÉ À LA MAIN, et l'invité diverge de son payload

**2026-08-29.** L'entrée disait plus haut que le correctif « n'est pas encore
sur l'invité » et qu'il y arriverait au prochain provisionnement. Ce n'est plus
vrai : il y a été **posé à la main**, le même jour.

Ce qui a été fait sur l'invité :

- `steam-cursor.ps1` **ajouté** à côté de `steam-session.ps1` ;
- `steam-session.ps1` et `steam-shell.ps1` **remplacés** ;
- **empreintes vérifiées** après transfert.

**L'INVITÉ DIVERGE DONC DE SON `provision_version=B1`.**
`C:\nivuus\state\PROVISION.done` dit toujours `B1` (achevé le 2026-08-26), et
trois fichiers du disque ne sont plus ceux de ce payload. C'est écrit ici parce
que le témoin de version ne le dira pas : **quelqu'un qui compare la machine au
payload B1 trouvera trois différences, et elles sont légitimes.**

Le prochain provisionnement remettra les fichiers **du dépôt**, ce qui est
cohérent — ce sont les mêmes, à la version du dépôt près. La divergence est
donc temporaire et se referme toute seule ; elle n'a pas à être « corrigée » à
la main dans l'autre sens.

### ⚠️ ~~Le fond d'écran n'a PAS été déployé~~ — LEVÉ le 2026-09-04

**Cet avertissement est périmé sur ses deux moitiés.** Il est gardé pour qui
lirait une version antérieure de cette page, et parce que la cause de sa
levée est instructive.

1. **Le verrou n'existe plus, parce que le script qui le tenait n'existe
   plus.** `steam-shell.ps1` faisait `Image::FromFile` et gardait le handle
   ouvert toute la session ; il a été **supprimé** le 2026-08-30 quand
   `explorer.exe` est redevenu le shell. Ce n'est plus une fenêtre WinForms qui
   peint le fond mais **Windows lui-même**, depuis le registre
   (`desktop-chrome.ps1` pose `Wallpaper` + `SystemParametersInfo`) : Windows
   travaille sur sa copie transcodée, il ne verrouille pas le PNG source.
2. **Le fond est déployé.** Mesuré le 2026-09-04 sur l'invité :
   `C:\nivuus\wallpaper.png` fait 56 474 octets, SHA256
   `533f1d75bc0c591c84e87707abdaa8aa9d1ed11d524e55a85a7b68eb133fcb74` —
   **identique à l'octet** au `console/guest/assets/wallpaper.png` du dépôt.
   L'ancien est conservé à côté sous `wallpaper.png.avant-20260829`
   (43 374 octets), ce qui date l'échange. `HKCU\Control Panel\Desktop\Wallpaper`
   pointe bien dessus.

**C3 est donc close des deux côtés** : dans le dépôt et sur la machine.

**Ce que le changement de shell a déplacé, et qui n'était pas dans C3 :** les
deux `#000` de la charte ne venaient plus du même endroit. La fenêtre de
`steam-shell.ps1` posait son `BackColor` à `#000` ; Windows, lui, complète une
image en style **6 (Ajuster)** avec la **couleur de fond du bureau**,
`HKCU\Control Panel\Colors\Background`, que rien ne posait. Relevée le
2026-09-04 : `0 0 0` — la charte tenait donc **par chance**, sur le défaut de
Windows. `desktop-chrome.ps1` la pose désormais explicitement. Le bandeau de
64 px, lui, survit tel quel dans `steam-hold-notice.ps1` (`$form.Height = 64`,
ancré à `$screen.Bottom - 64`, fond `FromArgb(0,0,0)`) : le dixième inférieur
de l'image doit toujours rester vide.

⚠️ **Pour `nivuus/design`** : le § 8 de `docs/brand.md` décrit encore un fond
« peint par `steam-shell.ps1` dans une fenêtre plein écran qu'il tient
lui-même ». Ce mécanisme est mort. Le fond est aujourd'hui un **fond d'écran
Windows ordinaire**, posé par le registre ; les deux `#000` et le bandeau de
64 px, eux, sont intacts.

### Ce que disait l'avertissement, et pourquoi il était juste à sa date


Le remplacement de `wallpaper.png` a **échoué**, mesuré le 2026-08-29
(`MethodInvocationException`). La cause est dans `steam-shell.ps1` lui-même :
il fait `Image::FromFile` sur ce fichier et **garde le handle ouvert tant que
la session Windows dure**.

**Se déconnecter de Moonlight ne suffit pas.** La session Windows survit à la
déconnexion du client ; le fichier reste verrouillé. Il faut un **redémarrage
de l'invité**.

**L'ancien fond d'écran est donc toujours en place.** C3 se lit plus haut comme
réglée — elle l'est **dans le dépôt** (la source SVG, le rendu, le chemin de
destination, les deux `#000` de `steam-shell.ps1`), et elle ne l'est **pas sur
la machine**. Les deux moitiés ne se referment pas au même moment, et la
seconde attend un redémarrage.

Ce qui n'est PAS su : si un provisionnement complet contourne le verrou (il
tourne avant qu'une session Big Picture ait ouvert l'image) ou s'il échoue de
la même façon. Personne n'a mesuré.

**Provenance des mesures, et comment les refaire.** Tout ce qui est chiffré
ci-dessus a été lu sur l'invité par WinRM le 2026-08-29, en lecture seule, et
**re-confirmé après une coupure d'accès** — donc deux fois, à deux moments. La
commande est reproductible telle quelle :

    python3 console/guest/winrm_exec.py ps '<commande PowerShell>'

La seule écriture de toute l'opération a été un fichier temporaire
`C:\Windows\Temp\nivuus-c4.b64` (transfert des deux `.ps1` pour les faire
analyser par le parseur PowerShell de l'invité), supprimé dans la foulée et son
absence vérifiée. Aucun service, aucune VM, aucun fichier de l'invité n'a été
touché.

**Au passage :** `steam-session.ps1` et `25-apollo.ps1` sont désormais en ASCII
pur. Ni l'un ni l'autre ne porte de BOM, et un `.ps1` sans BOM est relu par
Windows PowerShell 5.1 dans la page de codes ANSI — les guillemets
typographiques et les tirets cadratins de leurs commentaires y changeaient de
sens. Le contrôle est passé dans les tests, pour ces deux fichiers.

---

## C5 — Ni glisser ni défilement au doigt depuis Moonlight

**Constaté le 2026-08-28.** Depuis un client tactile — le téléphone du salon,
celui qui streame en 2410x1080 — on ne peut ni faire glisser, ni faire défiler
au doigt. Ce qui manque n'est pas le pointage, c'est **le geste continu** :
maintenir et déplacer, et l'inertie de défilement.

**Ce que ça vise, et qui n'est pas Big Picture :** Big Picture se pilote à la
manette, et cette dette n'y change rien. Elle porte sur l'application `Desktop`
— le client Steam normal, maximisé — et sur toute interface d'émulateur ou de
jeu qui réclame une souris. Sur une machine sans clavier ni écran physique, le
doigt sur le téléphone est le seul dispositif de pointage qui existe.

**Les trois maillons, dont aucun n'a été isolé, dans l'ordre où il faut les
prendre :**

1. **Le client.** Moonlight distingue plusieurs traitements du tactile — pavé
   tactile émulé et toucher direct — et le geste de défilement n'existe que
   dans l'un d'eux. C'est un réglage sur l'appareil du salon, pas sur le
   serveur, et c'est le seul des trois qui se teste sans rien modifier ici.
2. **Apollo.** Ce qu'il transmet du tactile, et sous quelle forme (déplacement
   relatif, position absolue, événements tactiles natifs), décide de ce que
   l'invité peut reconstruire. Toute clé qui s'ajouterait à
   `console/guest/templates/sunshine.conf.j2` suit la règle de son en-tête :
   vérifiée présente dans la version d'Apollo installée.
3. **L'invité.** Windows ne fabrique un défilement que si quelque chose émet la
   roue ; un pointeur qui se déplace n'en produit aucun.

**Le piège de méthode :** c'est le même que pour C1. Trois maillons, un seul
symptôme, et rien dans le flux ne dit lequel a laissé tomber le geste. Régler
le client d'abord parce que c'est gratuit, puis relire `sunshine.log`, puis
seulement toucher au gabarit.

**Ce que ça n'est pas :** la manette. C2 parle du pad virtuel et de son type ;
ce chemin-ci est celui de la souris, et les deux ne partagent rien d'autre
qu'Apollo.

**Ce que ça coûte :** l'application `Desktop` existe pour les cas où la manette
ne suffit pas — et c'est précisément là que le seul client disponible ne sait
pas pointer correctement.

---

## C6 — Les partages virtiofs disparaissent en silence, et la console devient inutilisable

**Constaté le 2026-08-29, et pas pour la première fois.** Les quatre partages
virtiofs — `E:` Téléchargements, `F:` Jeux, **`G:` Console**, `H:` Sauvegardes —
**avaient disparu de l'invité**. Les quatre services `NivuusShare_*` se
déclaraient `Running`, et **aucune des quatre lettres n'existait**.

**Ce que ça a coûté, vécu le jour même :** « ROM introuvable » au lancement de
Crash Team Racing, et le dossier de BIOS de DuckStation (`G:\retro\bios`)
invisible. La console était inutilisable, sans qu'aucun message ne dise
pourquoi.

### Pourquoi ça ne se voit pas

**Côté hôte, tout était sain** : les quatre `<filesystem>` déclarés dans le
domaine, et un `virtiofsd` en marche pour chacun. Rien à réparer de ce côté.

**Côté invité, le service ment.** `virtiofs.exe` ne meurt pas et ne sort pas en
erreur : il reste vivant au-dessus d'un tuyau mort. Le gestionnaire de services
voit quatre services `Running` parfaitement sains, les volumes WinFsp sont
démontés, et `Test-Path G:\` rend `False`. **La panne est totalement
silencieuse** — aucun événement Service Control Manager, aucun événement
WinFsp.

C'est pour cela que le filet posé au provisionnement ne joue pas :
`failureflag= 1` étend la reprise aux sorties **en erreur**, et ici il n'y a
pas de sortie du tout.

### Le remède, appliqué et efficace

    Get-Service NivuusShare_* | Restart-Service -Force

Les quatre lettres sont revenues. Les `virtiofsd` de l'hôte attendaient déjà,
connectés, et les lettres étaient libres.

### Pourquoi cette entrée existe, alors que le script le dit déjà

`console/guest/provision/35-shares.ps1` **décrit déjà ce mode de panne** — la
mesure du 2026-08-28 après une hibernation de la VM, sa règle **« SURVEILLER LA
LETTRE, PAS LE SERVICE »**, et deux pistes de correctif durable :

1. une tâche planifiée sur événement **Kernel-Power 107** (« The system has
   resumed from sleep ») qui rejoue `Restart-Service` sur les quatre ;
2. une sonde côté hôte dans `vm-idle-shutdown.sh`, qui porte déjà trois blocs
   « Self-heal » : si `Test-Path` échoue alors que la VM tourne, redémarrer les
   services et le journaliser.

Le script dit lui-même que le correctif « reste À FAIRE », et qu'il n'a pas sa
place dans un fichier qui ne tourne qu'au provisionnement. **Il a raison sur les
deux points, et c'est exactement pourquoi ça devient une dette à part entière :
ce n'est plus une hypothèse écrite dans un commentaire, c'est arrivé, et ça a
rendu la console inutilisable jusqu'au remède.**

Une observation de méthode, pour qui reprendra le sujet : la panne du 2026-08-28
a été rattachée à l'hibernation de la VM, journaux à l'appui. **Celle du
2026-08-29 n'a pas été rattachée à un déclencheur** — personne n'a relevé, au
moment du constat, s'il y avait eu hibernation, reprise, ou autre chose.
L'hibernation est donc une cause **connue**, pas la cause **unique** ; le
supposer réduirait le correctif à un seul déclencheur.

### Ce qui reste à faire

**Le correctif durable, et il n'a pas bougé.** Les deux pistes ci-dessus sont
toujours les bonnes, et la règle qui les gouverne est écrite : surveiller la
lettre, jamais l'état du service. Ce qui manque, c'est que quelque chose la
tienne **APRÈS** le provisionnement.

**Ce que ça coûte aujourd'hui :** la bibliothèque de jeux disparaît sans
préavis et sans message. Vu du canapé, les jeux ne se lancent plus ; il n'y a
rien à lire, et le seul geste qui répare demande une console PowerShell.

---

## C7 — L'invité tourne trois versions de provisionnement en retard, et rien ne le crie

**Constaté le 2026-09-04, sur l'invité de production.**
`C:\nivuus\state\PROVISION.done` porte :

    provision_version=B1
    completed=2026-08-26T18:39:36.5576588+02:00
    computer=NIVUUS-WIN
    agent_session=2

Le dépôt était alors en **B3**, et il est en **B4** depuis ce jour. Le
répertoire des témoins d'étape confirme la lecture : `C:\nivuus\state` ne
contient que les douze `*.ps1.done` du 2026-08-26, de `00-bootstrap` à
`99-marker`. **Ni `32-retro`, ni `33-winget`, ni `34-gaming-services` n'y
figurent** — ces étapes n'ont jamais tourné sur cette machine.

**Ce que ça veut dire, et il faut le lire deux fois :** tout ce qui a été
commité dans ce dépôt depuis le 2026-08-26 — le masquage du curseur (C4), le
type de manette épinglé (C2), la chaîne winget/Xbox, le shell `explorer.exe` —
existe sur la console **uniquement parce que quelqu'un l'y a posé à la main**.
Rien ne l'y a porté. Et l'inverse est vrai aussi : la console porte des choses
qu'aucun provisionnement ne reposerait.

**Le piège que ça arme, et qui est le vrai objet de cette dette.**
`guest-ready-watch.py` compare le `provision_version` du marqueur à
`payload.PROVISION_VERSION` pour décider si l'invité est à jour. Tant que la
constante ne bouge pas quand la séquence bouge, **un invité provisionné avant
le changement se déclare à jour**. C'est exactement ce qui a failli arriver ici :
la chaîne winget ajoutait deux étapes, changeait le shell et trois assets, et
laissait `PROVISION_VERSION = "B3"`. Une console qui n'a jamais vu winget
passait pour une console qui l'avait.

**Fait le 2026-09-04 :** `PROVISION_VERSION` est passée à **B4**, dans les deux
langues du dépôt (`payload.py` et la chaîne écrite par `99-marker.ps1`), et le
test qui l'épingle porte désormais la mesure ci-dessus en commentaire, pour que
le prochain lecteur sache **pourquoi** il ne faut pas oublier de la bouger.

**Ce qui reste dû, et qui n'est pas dans ce dépôt :** rien ne signale à
l'utilisateur qu'une console est en retard de provisionnement. Le comparatif
existe (`guest-ready-watch.py`), mais il ne tourne qu'après un `activate` ; sur
une console déjà installée, personne ne lit jamais ce marqueur. Le geste qui
rattraperait C7 est une **reconstruction complète de l'invité**, ce qui n'est
pas un geste anodin — et c'est précisément pourquoi cette dette est écrite ici
plutôt que réparée en passant.

**La règle, en attendant :** *avant de conclure quoi que ce soit d'une absence
dans un journal de l'invité, relever `C:\nivuus\state\PROVISION.done` et le
comparer à `payload.PROVISION_VERSION`.* Une étape qui n'a jamais tourné ne
laisse aucune trace, et cette absence se lit exactement comme un échec.

**C7 GARDE C2, et c'est sa conséquence la plus lourde.** Arbitré le 2026-09-05 :
la bascule du type de manette ne se joue pas sur une console qui n'est pas celle
du dépôt. Le filet de `nivuus/retro` qui doit rattraper cette bascule est livré
sur `main` — donc dans le dépôt, donc pas sur cette machine. Tant que C7 tient,
C2 attend. Voir la porte en tête de C2.

### Tentative du 2026-09-05 : trois obstacles distincts, aucun levé

La reconstruction a été autorisée par le propriétaire et **n'a pas eu lieu**.
Elle s'est arrêtée avant toute action destructrice, sur la première
vérification. Rien n'a été détruit, rien n'a été sauvegardé, l'invité n'a pas
été touché. Ce qui suit vaut pour la prochaine tentative.

#### Obstacle 1 — le garde du mode disque ne tenait pas (RÉPARÉ le 2026-09-05)

`guest_steps.refuse_implicit_wipe()` avait été livré précisément pour empêcher
un `--disk-mode wipe` implicite d'emporter `D:`. **Éprouvé en vrai, il n'a pas
refusé.** Banc d'essai : vrai `virsh` en lecture seule, vrai `/sys/block`,
vraies réponses du wizard, exécuteur qui lève une sentinelle dès qu'un
sous-processus serait lancé.

    _disk_mode(answers)                        -> 'wipe', explicite=False
    build.run()  -> GuestBuildError: the Windows medium ... is not readable

L'erreur qui sortait était **le médium manquant**, pas le refus. Or le garde
est appelé *avant* `media_identity()` : il avait donc été traversé sans un mot.

**La cause.** Le garde tranchait avec `domain_matches_disk()`, qui résout le
disque par `hardware.pci_address_for_device()` — une lecture de `/sys/block`.
Le disque de la console est lié à **vfio-pci**, ce qui est le *but* du
passthrough et non un accident, et un disque ainsi lié n'a aucune entrée dans
`/sys/block` :

    /sys/block                                    -> nvme0n1 seul (disque HÔTE)
    pci_address_for_device('/dev/nvme1n1')        -> None
    hostdev du domaine (virsh dumpxml)            -> 0000:03:00.0  (LE disque)
    domain_matches_disk(xml_réel, '/dev/nvme1n1') -> False

Le docstring du garde **nommait ce piège** (« the dedicated NVMe is bound to
vfio-pci and exposes NO block device to the host ») puis s'appuyait sur une
fonction qui exige précisément ce périphérique bloc. Le garde voulait deux
preuves — le domaine défini *et* l'identité du disque ; la seconde n'existe
pas côté hôte.

**Pourquoi les tests ne l'ont pas vu, et c'est le vrai enseignement.** Les 24
suites étaient vertes, celle du garde comprise, parce que
`test_console_guest_steps.py` injectait partout `_default_pci_address_of`, un
double qui rend l'adresse **inconditionnellement**. Le double satisfaisait
exactement la précondition que le réel ne peut pas satisfaire. Le commentaire
du test décrivait correctement le problème vfio, puis le neutralisait dans le
montage — ce qui est pire qu'un oubli, parce que ça se relit comme si le cas
avait été traité. **Un garde de sûreté vert en test et inerte en production
est pire qu'un garde absent.**

**Réparé.** Le garde applique désormais la règle que le module revendique,
dans le bon sens : *en cas de doute, refuser*. Seul un disque **prouvé
différent** laisse passer un wipe ; une identité qui ne peut pas être établie
refuse — et c'est l'état **normal** ici, pas l'exception. `disk_pci_identity()`
sépare « autre disque » de « ne peut pas savoir », que `domain_matches_disk()`
repliait en un seul `False` légitime pour lui, faux pour ce garde.

Vérifié sur la machine de production après correction :

    a libvirt domain 'Windows' is already defined on this host, and
    /dev/nvme1n1's identity CANNOT BE ESTABLISHED from here [...]

Deux corollaires livrés avec :

* **Le raccourci qui contournait le garde est fermé.** `refuse_implicit_wipe()`
  n'était appelé que depuis `build_run()`, lui-même appelé seulement si
  `build_done()` rendait `False`. Une ISO déjà construite **et estampillée**
  fait rendre `True`, l'étape est sautée, et le garde n'est jamais traversé —
  alors que cette ISO *est* une ISO d'effacement, l'empreinte couvrant les
  réponses, qui re-valident donc leur propre image destructrice. Sans objet
  tant qu'aucune ISO n'existe ; réel le jour d'une reconstruction, c'est-à-dire
  le seul jour où ce garde sert. Le garde est maintenant traversé **avant** le
  raccourci, dans `build_done()`.
* **Le remède que le garde imprime est devenu atteignable.** `wizard.yaml` ne
  collectait ni `disk_mode` ni `target_disk_verified` : le garde refusait en
  nommant une sortie qui n'existait pas, ce qui n'enseigne qu'à le contourner
  en éditant les réponses à la main. Les deux questions existent désormais.
  `disk_mode` est un `choix` **requis et sans défaut** — délibérément : un
  défaut n'est pas une phrase, et `wipe` par défaut aurait rendu la réponse
  « explicite » au sens de `_disk_mode()`, donc aurait fait taire le garde pour
  tout le monde. `target_disk_verified` vaut faux par défaut : on ne présume
  jamais qu'un humain a vérifié de quel disque il s'agit.

Les tests éprouvent désormais le **résolveur réel** rendant `None`, pas
seulement le double qui répond juste. Le double reste pour le cas nominal — ce
n'est pas lui le fautif, c'est qu'il ait été le seul.

**Et un `libvirtd` injoignable n'est plus un alibi non plus.** La première
version se taisait dès que `defined_xml()` rendait `None` — « un démon mort ne
prouve rien ». C'est vrai du *démon*, faux de la *question* : une définition de
domaine est un **fichier**, `/etc/libvirt/qemu/<nom>.xml`, qui existe que le
démon tourne ou non. Mesuré le 2026-09-05 : `virsh uri` rend `qemu:///system`
(ni `LIBVIRT_DEFAULT_URI`, ni `uri_default`), `/etc/libvirt/qemu/Windows.xml`
est là (9188 octets, 0600, avec ses `<hostdev>`), et il n'y a aucun périmètre
session sur cet hôte. L'arbitrage n'était donc pas binaire :

| état | lecture | décision |
|---|---|---|
| démon muet **+** définition sur disque | une console est là, on ne sait plus la lire | **refuse** |
| démon muet **+** aucune définition | machine plausiblement neuve | passe |

Ça applique « en cas de doute, refuser » **sans** bloquer une installation
neuve sur une machine où libvirt n'est pas encore debout : le doute est levé
par un fait, pas par une politique. Vérifié sur l'hôte, avec le vrai lecteur
de disque et un `virsh` qui échoue.

Trois limites sont désormais **écrites dans le docstring** — c'est ce qui
manquait à la version précédente, dont le commentaire nommait le piège vfio
avant de s'y jeter : (1) seul le périmètre **système** est lu, un hôte en
`qemu:///session` doit fournir son propre lecteur ; (2) cette branche prouve
l'**existence**, jamais l'**identité** — sans démon il n'y a pas de XML, donc
elle peut refuser l'effacement d'un disque que la console définie n'utilisait
pas, ce qui coûte une réponse explicite là où l'inverse coûte la partition de
jeux ; (3) une console installée puis **`undefine`** est invisible — le disque
reste plein, le fichier a disparu, et aucun garde de ce module ne peut voir ça.

Corollaire pour les tests : `definition_on_disk` est **injecté partout**, à
« absente ». Sans ça la suite aurait conclu de l'état libvirt de la machine qui
l'exécute — et serait devenue rouge sur l'hôte de référence lui-même, où une VM
`Windows` est définie.

#### Obstacle 2 — aucun médium Windows sur l'hôte

`/var/lib/nivuus/guest/` ne contient que `payload/retro/wheels/` (4,4 Mo). Il
manque `windows-source.iso`, `secrets/`, `nivuus-unattend.iso`, et `agent.exe`
(le témoin de `payload_done`). `copy_windows_medium()` n'est appelé que par
`hooks/install.py`, au seul moment où le médium live et la cible coexistent —
il est parti depuis longtemps. Recherche sur tout l'hôte : aucun ISO Windows,
seuls les `live-image-amd64.hybrid.iso` de l'installeur.

#### Obstacle 3 — le paquet console n'a jamais été installé par le moteur ici

`/etc/nivuus/packages.json` est absent, donc **aucune réponse enregistrée** —
et `activate_cli.py` en dépend entièrement. `/opt/nivuus/installer` est absent.
`nivuus-guest-ready.timer` **n'existe pas** sur cette machine : le minuteur
censé lire le marqueur et faire passer à `ready` n'est pas là, donc le « ~1 h
jusqu'à ready » du runbook ne s'appliquerait pas tel quel.

Ces deux obstacles appartiennent au propriétaire, pas au dépôt.

#### Relevés d'avant, à comparer à la prochaine tentative

* `C:\nivuus\state\PROVISION.done` (**pas** à la racine, comme on pourrait le
  lire trop vite) : `provision_version=B1`, `completed=2026-08-26T18:39:36`,
  `computer=NIVUUS-WIN`, `agent_session=2`.
* Un `PROVISION.failed` cohabite avec lui : `stage=10-nvidia.ps1`,
  `error=NVIDIA installer exited -469762040`.
* `C:\nivuus\state` : les douze `.done` du 26/08, ni `32-retro`, ni
  `33-winget`, ni `34-gaming-services`. Le reste sont des relevés manuels.
* `C:\nivuus` porte aussi `lot28` et `lot29` (en plus de `lot27`/`lot30`/
  `lot31`), plus `agent/`, `apollo/`, `gaming/`.
* Disque, mesuré : **un seul**, `Samsung SSD 970 EVO Plus 2TB`, GPT, 1863 Go —
  `D:` 1750 Go (1355 Go libres) et `C:` 111,9 Go (80,9 libres), plus
  System/Reserved/Recovery. La règle du mode disque est confirmée par la
  mesure, pas supposée.
* Aucune session de streaming au moment du relevé : 474 puis 126 octets TX sur
  `vnet37` en 5 s, CPU invité ~0,6 cœur sur 14.
* Hôte : RTX 4070 (`01:00.0` + `01:00.1`) déjà sur `vfio-pci`, VM en cours ;
  `ollama`, `nvidia-persistenced`, `tdarr-node` déjà `inactive`. C'est l'état
  **trouvé**, pas un effet de la tentative.
* `//192.168.3.2/D$` est monté sur `/media/win-d` : `D:\state\apollo\credentials`
  est donc accessible depuis l'hôte sans passer par WinRM, ce qui simplifie la
  sauvegarde de la racine d'appairage le jour venu.

---

## La chaîne winget / Xbox — ce qui est mesuré, et ce qui ne l'est pas

Ajoutée le 2026-08-30, terminée et commitée le 2026-09-04. Ce n'est pas une
dette : c'est le relevé de ce que la chaîne fait vraiment, pour que la prochaine
panne ne se rediagnostique pas de zéro.

**Mesuré sur l'invité de production le 2026-09-04**, la tâche
`gaming-services-refresh` lancée par `schtasks /run` (donc en session 1) :

    services Xbox en Automatic et demarres (relu) : wlidsvc, XblAuthManager,
        XboxNetApiSvc, XblGameSave, LicenseManager, ClipSVC
    Services de jeu : deja a jour en 38.116.6003.0
    Fournisseur d identite Xbox : deja a jour en 12.130.16001.0
    Application Xbox : deja a jour en 2608.1001.17.0
    Microsoft Store : deja a jour en 22607.1401.8.0
    chaine Xbox complete (Services de jeu 38.116.6003.0)

puis, immédiatement rejouée :

    services Xbox en Automatic et demarres (relu) : ...
    Store non interroge : dernier controle il y a 0,0 h (seuil 20 h), version 38.116.6003.0

Les quatre paquets du Store sont présents, winget est en **1.29.290.0** —
l'épinglage `WINGET_VERSION = "v1.29.290"` du dépôt.

### Trois choses que la mesure a corrigées, et que la lecture seule ne donnait pas

1. **`XblGameSave` n'est plus forcé du tout — il est démarré et laissé tel
   quel, comme `ClipSVC`.** `Set-Service` **ne lève pas** et le démarrage repart
   à `Manual` (`HKLM\SYSTEM\CurrentControlSet\Services\XblGameSave\Start` = 3),
   observé sur trois passages sur quatre ; `sc qtriggerinfo XblGameSave` montre
   un déclencheur `NETWORK EVENT / RPC INTERFACE EVENT`, donc **Windows regère
   ce service et c'est le système qui fonctionne comme prévu, pas une panne**.
   Le forcer revenait à se battre contre le système d'exploitation pour obtenir
   un témoin **définitivement** `chaine-incomplete` — et un témoin toujours
   rouge cesse d'être lu, si bien que le prochain **vrai** défaut de la chaîne
   passerait inaperçu. `Manual` + déclencheur est donc inscrit dans le code
   comme l'état **attendu** ; ce qui est vérifié, c'est qu'ils soient `Running`.
   Mesuré après le changement, `XblGameSave` remis de force en `Manual` avant :
   témoin `status=ok`, `Start=3`, `Status=Running`. Les quatre services qui,
   eux, **tiennent** `Automatic` sont toujours réglés **et relus** — sans cette
   relecture, un réglage qui ne prend pas et un réglage qui prend écrivaient
   exactement la même ligne, la liste *voulue* : le patron même du faux oracle.
2. **`ClipSVC` n'a pas rendu « Access is denied ».** `CLAUDE.md` l'affirmait ;
   la mesure ne le confirme pas — il démarre, il n'est pas reconfiguré, et
   aucune erreur n'a été vue. Le traitement à part reste juste (c'est un service
   protégé, et il n'a pas besoin d'être en `Automatic`), mais **la raison écrite
   n'était pas la raison mesurée**.
3. **L'étranglement de 20 h ne couvre plus que le Store.** Il couvrait aussi les
   services, ce qui étranglait la moitié qui ne coûte rien — et, pire,
   l'horodatage n'étant écrit qu'en cas de succès **complet**, un service qui
   refuse de tenir empêchait de l'écrire à jamais : les quatre appels winget
   repartaient alors à chaque ouverture de session et tous les jours,
   indéfiniment. Les deux moitiés sont séparées.

### Deux pièges d'exécution payés le 2026-09-04, à ne pas repayer

- **`ExecutionTimeLimit` est une chaîne, pas un `TimeSpan`.**
  `(New-ScheduledTaskSettingsSet).ExecutionTimeLimit` est un `System.String`
  valant `PT72H` ; c'est le **paramètre du cmdlet** qui convertit un `TimeSpan`
  en durée ISO-8601, jamais l'affectation à la propriété. Y affecter un
  `TimeSpan` dépose `00:05:00`, et `Register-ScheduledTask` refuse : *« The task
  XML contains a value which is incorrectly formatted or out of range.
  (37,36):ExecutionTimeLimit:00:05:00 »*. Sous `$ErrorActionPreference = 'Stop'`,
  l'étape 30 mourait là — et avec elle **le provisionnement entier**.
- **`$ErrorActionPreference` est à portée DYNAMIQUE, donc un asset dot-sourcé
  hérite du `Stop` de l'étape.** `gaming-services.ps1` promet « NE LÈVE PAS » ;
  cette promesse était fausse du seul fait d'être appelée depuis l'étape 34, où
  `& $winget ... 2>&1` rendait terminante la moindre ligne d'erreur native. Et
  la tâche planifiée, elle, tourne sous le `Continue` par défaut : **les deux
  chemins ne se comportaient pas pareil en panne**, alors que l'en-tête du
  fichier affirme le contraire. Le mode est rétabli **dans la fonction**, jamais
  au niveau du fichier (qui désarmerait le `Stop` du reste de l'étape 34).

### Ce qui n'est PAS mesuré

- **Aucun jeu GDK n'a été lancé.** La chaîne est complète et ses services
  tournent ; que Forza Horizon 6 démarre n'a pas été revérifié le 2026-09-04.
- **Les deux tâches `AtLogOn` de l'étape 30 ont été posées à la main le
  2026-09-05**, l'étape 30 n'ayant jamais tourné sur cette machine (C7).
  `desktop-chrome` (`PT5M`) et `steam-hold-notice` (`PT0S`) sont `Ready`, et
  leur enregistrement est au passage une **deuxième confirmation indépendante**
  du correctif `ExecutionTimeLimit`. Effet vérifié après un
  `schtasks /run desktop-chrome` : `StuckRects3\Settings[8]` passe de `0x02` à
  `0x03` (bit d'auto-masquage posé), `HideIcons = 1`, `Colors\Background`
  épinglé à `0 0 0`, Explorer redémarré proprement (un seul processus).
  ⚠️ Elles ne survivront pas à une reconstruction de C: **par ce geste-là** :
  c'est l'étape 30 qui les repose, et C7 reste la vraie réponse.

---

# Dettes de CI — le contrôle plutôt que le code

Les cinq dettes qui suivent ne décrivent pas un manque de l'invité mais un
défaut des **contrôles** qui gardent ce dépôt. Elles sont ici, et pas dans un
plan, parce qu'elles ont été constatées en réparant la CI de la PR #8 le
2026-09-05 et qu'elles se seraient sinon redécouvertes à la PR suivante.

Le fait qui les relie : **`policy` et `python` échouaient sur du code qu'aucune
de ces PR n'avait écrit.** Les cinq fichiers longs, les 399 lignes françaises et
les 52 sujets de commit refusés viennent tous des 133 commits hérités de la
PR #7, ouverte depuis le 2026-08-27.

---

## CI-1 — Cinq fichiers dépassent 500 lignes, et l'exception est posée, pas payée

**Arbitré le 2026-09-05.** `policy / Coding rules` refusait cinq fichiers :

    console/guest_steps.py            1318 lignes
    console/guest/retro_sync.py       1096
    console/hardware.py                769
    console/guest/fetch_payload.py     624
    console/host/guest-ready-watch.py  538

Chacun porte désormais `policy: allow-long-file` **avec un motif écrit**, qui est
l'échappatoire que le socle prévoit — le script `check-file-size.sh` l'annonce
lui-même dans son message de refus. **Ce n'est pas le contrôle qu'on baisse :
c'est une exception nommée, datée et suivie ici.** La nuance est essentielle, et
c'est pourquoi le motif est obligatoire : une exception sans raison écrite est
indistinguable d'un contournement.

**Ce qui reste dû :** le découpage réel. Scinder 4 245 lignes de code hérité pour
débloquer une PR de 162 commits était le mauvais ordre — une PR de cette taille
qui reste ouverte une semaine de plus pourrit, et le découpage mérite son propre
chantier, avec ses propres tests.

**Le piège à ne pas répéter :** `guest_steps.py` est le plus gros et le moins
divisible en l'état. Ses prédicats (« cette étape peut-elle être sautée ? »)
portent chacun en commentaire la panne réelle qu'ils empêchent. Les séparer des
étapes qu'ils gardent perdrait exactement le couplage que le fichier rend
lisible.

---

## CI-2 — `ruff format --check` reformaterait 76 fichiers sur 77

**Constaté le 2026-09-05.** L'étape ruff du socle enchaîne deux commandes :
`ruff check` puis `ruff format --check`. La première est réparée — `ruff.toml`
épingle désormais la sélection, et `requirements.txt` la version de ruff ; la
seconde ne l'est pas :

    76 files would be reformatted, 1 file already formatted

**Ce dépôt n'a jamais adopté le formateur de ruff.** Son style est délibéré :
des blocs de commentaires longs, alignés, qui *sont* sa documentation. Leur
appliquer le formateur au milieu d'une PR de réparation de CI réécrirait
précisément ces blocs.

**Ce qui reste dû, et c'est une décision de projet, pas un geste de passage :**
soit adopter le formateur et reformater le dépôt d'un coup, dans un commit qui
ne fait que ça ; soit demander au socle une entrée qui rende cette moitié
optionnelle, comme `test-paths` et `test-dirs` le sont déjà pour pytest et bats.

**En attendant :** `python / Python checks` reste rouge sur cette moitié-là.
Ce n'est pas un contexte bloquant — voir « Ce que la protection de `main` exige
vraiment », en fin de fichier, pour la liste réelle.

---

## CI-3 — 399 lignes françaises exemptées, et la traduction reste à faire

**Arbitré le 2026-09-05.** `Enforce English in code` refusait 399 lignes
ajoutées, réparties sur quinze fichiers — 291 dans le seul
`console/guest/retro_sync.py`, dont l'en-tête est la référence de la séquence de
synchronisation rétro.

Les quinze fichiers portent désormais `policy: allow-fr-file` **avec un motif
écrit**, l'échappatoire que `check-english.sh` annonce lui-même. Même nuance
qu'en CI-1 : exception nommée, pas contrôle baissé.

**Ce qui reste dû :** la traduction. Elle n'a pas été faite ici parce que ces
commentaires portent des **mesures** — des dates, des versions, des observations
faites sur cette machine (« `fuser` ne voit PAS les process conteneurisés,
testé : muet sur llama-server »). Les traduire en réparant une CI, c'est risquer
d'en altérer le sens sans que personne ne le relise pour ce qu'il dit.

**Un faux positif est déjà identifié, et ne se traduira jamais :**
`console/guest/winrm_exec.py` est signalé pour une ligne **en anglais** dont le
seul français est dans la chaîne citée en exemple — `"; Ecrit par << retro >>"`
— qui est la **donnée** que le fichier décrit. Le marqueur par ligne
`policy: allow-fr` est sans effet dans une chaîne (le socle le dit), d'où
l'exemption de fichier.

---

## CI-4 — Le contrôle des sujets de commit vérifie le format de ce qui n'existera pas

**Constaté le 2026-09-05. Cette dette-ci ne vit pas dans ce dépôt : elle est
dans `nivuus/.github`, partagé par les dix dépôts de la suite.**

`Enforce conventional commits` lance `check-commits.sh "$BASE" HEAD`, qui exige
que **chaque sujet de commit de la branche** soit conventionnel et anglais. Sur
la PR #8, 52 sujets sur 151 étaient refusés (18 non conventionnels, 34 non
anglais), dont 32 hérités de la PR #7.

**Le fait mesuré qui rend ce contrôle sans objet :**

    gh api repos/nivuus/installer
      allow_squash_merge:          true
      allow_merge_commit:          false
      allow_rebase_merge:          false
      squash_merge_commit_title:   PR_TITLE
      squash_merge_commit_message: COMMIT_MESSAGES

Le dépôt est **squash-only**. Le commit qui atterrit sur `main` porte pour sujet
**le titre de la PR** — que l'étape suivante, `Enforce the pull request title`,
vérifie déjà, séparément et correctement. Les sujets individuels de la branche
finissent dans le **corps** de ce commit unique : ils ne sont jamais des sujets.

**Le contrôle vérifie donc le format conventionnel de lignes qui ne seront
jamais des sujets de commit.** Et il fait payer ce format à des branches longues
en exigeant une réécriture d'historique — qui, ici, aurait cassé l'ascendance de
la PR #7 et l'aurait empêchée de se fermer d'elle-même à la fusion.

**Ce qu'il faudrait à la place**, et c'est au propriétaire de trancher puisque
ça touche un dépôt partagé :

- sur un dépôt squash-only, ne vérifier que le **titre de la PR** (l'étape
  existe déjà) ;
- ou n'exiger le format par commit que là où `allow_merge_commit` ou
  `allow_rebase_merge` est vrai — la donnée est lisible depuis l'API, comme
  ci-dessus ;
- ou garder le contrôle en **avertissement** sur les commits, sans faire échouer
  le job.

**En attendant :** aucune réécriture d'historique n'a été faite. `policy` reste
rouge sur cette seule étape.

---

## CI-5 — `test_webapp_models` ne peut pas tourner sur cet hôte, et il masque ce qui le suit

**Constaté le 2026-09-05, en jouant `make test-packages`.**

```
--- test_webapp_models
ModuleNotFoundError: No module named 'pydantic'
make: *** [Makefile:81: test-packages] Error 1
```

Mesuré : `pydantic` n'existe que sous
`/usr/local/lib/python3.11/dist-packages/`, et **`/usr/bin/python3.11` n'existe
plus** — la base est en 3.13.5. Le paquet appartient donc à un interpréteur
disparu, et la suite est structurellement injouable ici sans un venv dédié.

**Ce n'est pas la dette ; la dette est ce que ça cache.** La boucle du
`Makefile` fait `|| exit 1`, donc l'échec **arrête l'agrégateur**, et les deux
suites qui viennent après — `test_common_hardware` et
`test_install_engine_features` — ne sont jamais jouées. Un échec d'environnement
sur une suite se lit alors comme un silence sur les suivantes, ce qui est
exactement la classe de faux témoin que ce fichier documente ailleurs.

**Contournement appliqué ce jour :** les deux suites suivantes ont été jouées à
la main, vertes toutes les deux. Ce n'est pas une réparation.

**Remèdes possibles**, aucun tranché : construire le venv pydantic que
`iso-build/hook 0500-nivuus-venv` sait déjà faire et pointer `PYTHON=` dessus ;
ou faire tomber la boucle en collectant les échecs plutôt qu'en sortant au
premier, de sorte qu'une suite injouable ne masque plus les autres.

---

## Ce que la protection de `main` exige vraiment

Relevé le 2026-09-05, parce que trois contrôles rouges donnaient à croire que
trois contrôles bloquaient :

    gh api repos/nivuus/installer/branches/main/protection
      contexts:        ["policy / Coding rules",
                        "security / Secrets and dependencies"]
      enforce_admins:  true
      required_approving_review_count: 0

**Seuls `policy` et `security` tiennent la porte.** `python`, `shell` et
`codeql` tournent et doivent être réparés — un contrôle rouge qu'on prend
l'habitude de ne pas lire cesse d'être un contrôle — mais ils ne bloquent pas la
fusion. Le savoir change l'ordre dans lequel on répare, pas le fait qu'on répare.
