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

## C2 — Apollo annonce un Xbox 360 : ni gyroscope, ni tactile

**Constaté le 2026-08-28.** `sunshine.log` dit `Gamepad 0 will be Xbox 360
controller (default)` à chaque session. Un pad X360 **n'a pas de capteur de
mouvement**, pas de pavé tactile, pas de retour haptique fin. Les jeux et les
émulateurs qui en dépendent — PPSSPP, PCSX2, RPCS3, et un futur émulateur
PS Vita — ne recevront jamais rien, quoi qu'on règle de leur côté.

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

### ⚠️ Le fond d'écran n'a PAS été déployé, et il ne peut pas l'être à chaud

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
