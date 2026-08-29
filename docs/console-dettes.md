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
un flux Big Picture, faute de session Moonlight ouverte. Et le correctif **n'est
pas encore sur l'invité** : `C:\nivuus\state\PROVISION.done` dit
`provision_version=B1` (achevé le 2026-08-26) quand le payload courant est `B3`,
le `steam-session.ps1` déployé date du 2026-08-27 et ne connaît pas
`SetSystemCursor`, et `steam-cursor.ps1` n'est pas encore à côté de lui. Il y
arrivera au prochain provisionnement — la vérification ci-dessus porte sur les
API et la syntaxe, jamais sur un déploiement.

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
