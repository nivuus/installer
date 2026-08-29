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

## C4 — Le curseur de la souris reste posé sur Big Picture

**Constaté le 2026-08-28.** En mode `Steam Big Picture`, le pointeur de souris
reste visible par-dessus l'interface. Big Picture se pilote entièrement à la
manette : ce curseur ne sert à rien, il ne bouge pas, et il traîne au milieu de
l'écran de la TV pendant toute la partie.

**Ce que le dépôt dit déjà de ce défaut, et qui date d'avant le
provisionnement :** l'`apps.json` écrit à la main de 2026-07-23 faisait porter à
l'entrée `Steam Big Picture` un `nomousy` en plus de `-bigpicture` (CLAUDE.md,
§ *"Desktop" app auto-maximizes the Steam window*). Ce fichier a été remplacé
par le gabarit `console/guest/templates/apps.json.j2`, qui n'en porte **aucune
trace** — c'est la seule occurrence de `nomousy` dans tout le dépôt. La dette
n'est donc pas neuve : c'est un comportement qui existait, et que la
généralisation en gabarit a laissé tomber sans que rien ne le signale.

**Où ça se joue, et par quel chemin c'est interdit :** pas par un `prep-cmd`.
L'en-tête d'`apps.json.j2` en fait une règle, et la raison est mesurée — Apollo
attend la fin d'un `prep-cmd` avant de lancer l'application, ce qui avait coûté
182 s par session. Tout masquage doit donc se greffer sur la boucle de
surveillance de `console/guest/provision/assets/steam-session.ps1`, jamais
devant elle : c'est exactement la règle que la maximisation a déjà suivie
(`$maximized` / `Set-SteamMaximized`).

**La contrainte qui interdit de le faire globalement :** le mode `Desktop` lance
le client Steam normal, qui se pilote à la souris. Un masquage posé pour la
session entière rendrait ce mode-là inutilisable. `steam-session.ps1` reçoit
déjà `-Mode` et distingue déjà les deux cas — le masquage suit ce paramètre, ou
il casse l'autre application.

**Ce qu'il reste à mesurer, avant d'écrire une ligne :** ce que fait exactement
le levier retenu. Un utilitaire tiers (`nomousy`) est un binaire de plus à
embarquer dans le payload et à empreindre ; côté Apollo, une clé de capture du
curseur — si elle existe dans la version installée — se poserait dans
`sunshine.conf.j2`, sous la règle de son en-tête : **vérifiée présente dans
Apollo 0.4.6**, jamais recopiée d'une recette. Les deux ne portent d'ailleurs
pas sur la même chose : cacher le curseur *dans l'invité* n'est pas cesser de
le *capturer* dans le flux, et seule la seconde laisse l'invité utilisable en
VNC.

**Ce que ça coûte :** rien ne bloque une partie. Mais c'est le dernier élément
d'interface Windows visible sur une machine dont on a retiré `explorer.exe` et
caché les quatre fenêtres PowerShell précisément pour qu'il ne reste rien à
l'écran.

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
