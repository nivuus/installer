# Media center intégré au châssis — design

**Date** : 2026-08-03
**Statut** : design validé, en attente de plan d'implémentation
**Portée** : intégration matérielle, électrique et réseau d'un boîtier Google TV dans le
châssis Nivuus, sans modification structurelle du serveur

## 1. Contexte et objectif

Le besoin exprimé : afficher un media center sur la sortie HDMI principale de la machine —
explicitement **pas** celle de la RTX 4070, réservée au passthrough — capable de faire
tourner Netflix, Prime Video et Plex, ainsi que Moonlight pour le jeu en streaming depuis la
VM Windows. YouTube a été ajouté ensuite, et la **4K est exigée sur les applications de
streaming**. L'écran cible est une future TV 4K 120 Hz qui remplacera le moniteur actuel.

Contrainte d'architecture posée par l'utilisateur et réaffirmée deux fois : **tout doit
tenir dans le châssis de cette machine**. Un appareil séparé posé à côté de la TV est
refusé.

## 2. Contrainte structurante : la DRM matérielle

C'est elle qui détermine toute la solution, et elle est indépendante de la puissance
disponible.

Netflix, Prime Video et Disney+ n'accordent le 4K — et même le 1080p — qu'aux appareils
disposant d'une **DRM matérielle certifiée** : Widevine **L1** côté Google, PlayReady
**SL3000** côté Microsoft. Ces niveaux exigent que le déchiffrement et le décodage se
déroulent dans un **TEE**, avec un chemin vidéo sécurisé où les images déchiffrées ne
transitent jamais par de la mémoire lisible par le système. Le `libwidevinecdm.so` livré
avec Chrome sous Linux est purement logiciel, donc **L3 : plafond 720p**.

Points établis lors de l'étude, qui ferment les contournements envisagés :

- **Le GPU n'y change rien.** La RTX 4070 possède le silicium nécessaire — elle le fait sous
  Windows — mais aucun CDM Widevine L1 n'existe pour Linux desktop. La certification est
  accordée plateforme par plateforme par Google, et ne l'a jamais été pour un poste Linux
  assemblé, quelle que soit la carte graphique.
- **PlayReady SL3000 sous Linux existe mais ne s'applique pas ici.** Des implémentations
  réelles existent (NXP, via OP-TEE et des DMA buffers sécurisés), mais sur SoC embarqués ARM
  TrustZone, avec une pile signée par le fabricant. Rien d'équivalent sur x86 assemblé — et
  l'i9-12900K n'a même plus SGX, retiré par Intel des puces grand public depuis la 11e
  génération.
- **Le HDCP n'est pas le maillon manquant.** Le noyau expose bien la propriété
  `Content Protection` et `i915` implémente HDCP 1.4/2.2. Chiffrer le câble ne sert à rien
  si aucun CDM ne livre le flux 4K en amont.

### Options considérées

| Option | 4K Netflix/Prime | Dans le châssis | Retenu |
|---|---|---|---|
| Media center Linux sur l'iGPU (Kodi/labwc/Chromium) | Non — 720p | Oui | Non |
| Boîtier certifié posé près de la TV | Oui | Non | Non |
| Apps intégrées de la future TV | Oui | Non | Non |
| VM Windows pilotant la TV via la RTX | Oui | Oui | Non — RTX écartée, et ~200 W pour un film |
| **Boîtier Google TV monté dans le châssis** | **Oui** | **Oui** | **Oui** |

La solution retenue contourne la contrainte par le matériel au lieu de la subir : un
appareil certifié L1 est physiquement intégré au châssis, ce qui satisfait à la fois
l'exigence de 4K et l'exigence de boîtier unique.

## 3. Relevé de l'existant

Mesuré sur la machine le 2026-08-03. Ces constats justifient plusieurs choix du design.

| Constat | Mesure | Conséquence |
|---|---|---|
| Écran actuel | AOC `28E850`, 28", semaine 3 de 2017, EDID 1.3 sur `HDMI-A-2` ; modes CEA plafonnés à 3840×2160@60 (VIC 97) **ou** 1920×1080@120 (VIC 63) | Reste console locale ; la TV cible est un autre écran |
| **Aucune sortie audio** | `00:1f.3` **absent du bus PCI** (00:1f.0/.4/.5 présents) ; `/proc/asound/cards` → `no soundcards` ; seul périphérique audio = `01:00.1` (RTX), capté par `vfio-pci` | Un media center sur l'iGPU aurait imposé un changement BIOS + reboot. **Évité** : l'audio passe par le HDMI du boîtier |
| **Aucun CEC** | Pas de `/dev/cec*` ; aucun nœud `cec*` sous `0000:00:02.0`. Le module `cec` chargé n'est que le framework lié par `drm_display_helper`/`i915`/`xe` | Pas de CEC côté serveur. Le boîtier, lui, gère le CEC nativement |
| Décodage iGPU | H.264, HEVC jusqu'à Main12 et 422_10, VP9 profils 0-3. **Aucun profil AV1** | YouTube 4K en AV1 serait tombé en logiciel. **Évité** : le boîtier décode l'AV1 |
| Session graphique | `gdm` et `graphical.target` `inactive` (défaut `multi-user.target` depuis la consolidation CPU du 22/07) | Aucune session à créer, rien à réactiver |
| **Cartes réseau libres** | `enp14s0`, `enp15s0`, `enp16s0` : `down`, sans porteuse, sans bridge | Une NIC libre accueille le boîtier sans switch |
| Membres réels de `localBridge` | `enp17s0`, `wlp10s0`, `wlp11s0` | **CLAUDE.md à corriger** : il documente `enp14s0` comme membre |
| USB | Contrôleur `00:14.0` USB 3.2 Gen 2x2 ; bus 2 en 20 Gb/s | Le header Type-C de façade existe |

## 4. Architecture retenue

Un **Google TV Streamer 4K** est monté à l'intérieur du châssis, derrière le panneau en
verre trempé — transparent aux 2,4 GHz, contrairement à une façade mesh dont les
perforations blindent aussi bien qu'une tôle pleine — et **dans le flux d'air d'admission**,
à l'écart de l'échappement du GPU et des VRM.

| Fonction | Réalisation |
|---|---|
| Alimentation | Header interne USB 3.2 Gen 2 Type-C (Type-E, 20 broches, key-A) → adaptateur vers USB-C femelle. Le boîtier demande **5 V / 1,5 A, soit 7,5 W** |
| Réseau | RJ45 intégré du boîtier → câble court interne → `enp14s0`, ajoutée comme membre de `localBridge` (zone firewalld `home`, target ACCEPT) |
| Vidéo + audio | HDMI du boîtier → traversée femelle-femelle sur équerre PCI → TV |
| Pilotage | Télécommande Bluetooth LE fournie (portée assurée par le verre trempé), HDMI-CEC en secours, app Google TV du téléphone via le réseau |

Le modèle **Streamer** est préféré à un dongle précisément pour son **port Ethernet
intégré** : il exploite directement une NIC libre, sans adaptateur USB-C, sans switch, et
aucun câble ne sort du châssis hormis l'HDMI.

**La seule modification structurelle du serveur est l'ajout de `enp14s0` à `localBridge`**
(les autres changements listés en §6 sont des réglages annexes : bail DHCP, documentation,
intégration HA optionnelle). Pas de changement BIOS, pas de session graphique sur l'iGPU,
aucune interaction avec la RTX ni avec les hooks de passthrough, aucun impact sur le budget
RAPL ni sur le partitionnement cgroup des CPU pendant une session de jeu.

## 5. Répartition des usages

| Usage | Exécuté par | Résultat |
|---|---|---|
| Netflix, Prime Video, Disney+ | Boîtier (Widevine L1) | **4K HDR**, Dolby Vision/Atmos |
| YouTube | Boîtier | 4K, AV1 décodé matériellement |
| Plex | Client du boîtier → `mediamanager-plex-1` | Direct play ; le flux ne quitte pas la machine, il transite par `localBridge` |
| Moonlight | Boîtier → `192.168.0.1` | 4K60 ; la wake-gate du port 47989 réveille la VM Windows à l'ouverture de l'app |
| Console locale Linux | iGPU `HDMI-A-2` → AOC 28E850 | Inchangé |

Moonlight vise **`192.168.0.1`, pas l'IP de la VM** : c'est le scénario pour lequel
`vm-wake-gate.py` a été écrit, et son discriminant HTTP `GET /serverinfo` est exactement ce
qu'émet Moonlight au démarrage.

## 6. Modifications côté serveur

1. Profil NetworkManager persistant ajoutant `enp14s0` comme esclave de `localBridge`.
2. Réservation DHCP d'une adresse fixe pour le boîtier.
3. Correction de CLAUDE.md sur les membres réels de `localBridge`.
4. Optionnel : intégration `androidtv_remote` de Home Assistant, pour obtenir une entité
   `media_player` et pouvoir scénariser l'ensemble.

## 7. Configuration du boîtier

1. **Désactiver le routeur de bordure Thread et le hub Matter.** Laissés actifs, ils
   formeraient un second réseau Thread en concurrence avec l'OTBR du canal 21 et ruineraient
   la carte RF calibrée (WiFi ch 6 / Thread ch 21 / Zigbee ch 25, sans chevauchement).
2. Activer le HDMI-CEC.
3. Moonlight pointé sur `192.168.0.1`, appairé une fois contre cette entrée.
4. Client Plex pointé sur le serveur local.

## 8. Points de vérification pour le plan d'implémentation

| Point | Risque si négligé | Vérification |
|---|---|---|
| Courant réellement délivré par le header | Sous-alimentation → reboots aléatoires du boîtier, symptôme trompeur | Ce sont les résistances CC de l'adaptateur Type-E→USB-C qui annoncent le courant ; un adaptateur bas de gamme n'annonce que 900 mA. Premier suspect en cas d'instabilité |
| Forward-ports Moonlight en zone `home` | Moonlight ne joint pas la VM une fois réveillée | Vérifier que `started/begin/rules.sh` pose bien les forward-ports vers 192.168.3.2 dans la zone `home` et pas seulement dans `external` ; sinon viser 192.168.3.2 directement après réveil |
| Stabilité thermique | Throttling ou plantage du boîtier | Contrôle après une **vraie session de jeu**, quand l'air interne est au plus chaud — le châssis est déjà saturé (PECI 86-93 °C en jeu, ventilateurs sans marge) |
| Portée du Bluetooth LE | Télécommande fournie inutilisable | Test d'appairage une fois le boîtier monté derrière le verre |

## 9. Limites assumées

- **4K60 maximum.** Aucun boîtier TV du marché ne dépasse 60 fps ; les 120 Hz de la future
  TV resteront inexploités par ce chemin, en vidéo comme en jeu. Sans conséquence pour la
  vidéo, où aucun service ne dépasse 60 fps.
- **Le boîtier redémarre à chaque extinction du serveur**, dont il tire son courant. Sans
  conséquence pratique sur une machine allumée en permanence.
- L'AOC et la sortie iGPU restent une console locale, sans son.

## 10. Coût

| Élément | Prix indicatif |
|---|---|
| Google TV Streamer 4K | ~120 € |
| Adaptateur Type-E (20 broches) → USB-C femelle | ~10 € |
| Traversée HDMI femelle-femelle sur équerre PCI | ~12 € |
| Câble Ethernet court interne | ~5 € |
| **Total** | **~150 €** |
