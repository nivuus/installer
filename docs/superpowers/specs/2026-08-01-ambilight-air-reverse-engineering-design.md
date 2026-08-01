# Reverse engineering d'Ambilight Air — design

**Date** : 2026-08-01
**Statut** : design validé, en attente de plan d'implémentation
**Portée** : phase 1 (protocole + pilotage des enceintes depuis Home Assistant)

## 1. Contexte et objectif

Deux enceintes Philips **TAW6205** présentes sur le LAN implémentent **Ambilight Air**, la
fonction par laquelle un téléviseur Philips diffuse les couleurs de son Ambilight vers des
appareils compatibles du réseau. Le protocole n'est pas documenté publiquement.

Trois objectifs ont été retenus, découpés en deux phases :

| Objectif | Phase |
|---|---|
| Piloter les LEDs des enceintes depuis Home Assistant | 1 |
| Documenter le protocole (spec publiable) | 1 |
| Relayer le flux Ambilight vers les lampes Yeelight/Meross du LAN | 2 |

La phase 2 fera l'objet de sa propre spec et réutilisera le décodeur éprouvé en phase 1.

**Le livrable est destiné à la publication** : une intégration Home Assistant publiée sur
HACS, écrite dès le départ en respectant les exigences du core HA afin de pouvoir être
soumise ensuite sans réécriture.

## 2. Découvertes de l'exploration

Cette section consigne ce qui a été **mesuré** sur le réseau le 2026-08-01. Elle constitue
le point de départ factuel du travail ; les hypothèses restant à valider sont explicitement
marquées comme telles.

### 2.1 Inventaire

| Rôle | IP | MAC | Identité |
|---|---|---|---|
| Enceinte A | `192.168.0.68` | `40:aa:56:d4:3f:bd` | mDNS `tpv_D43FBD`, hostname `mt7687.local` |
| Enceinte B | `192.168.0.214` | `04:39:26:1c:8c:0c` | mDNS `tpv_1C8C0C`, hostname `mt7687-2.local` |
| Émetteur | `192.168.0.183` | `78:8a:86:6b:7c:5f` | TV Philips (ports JointSpace 1925/1926 ouverts) |

Les trois appareils portent des OUI **China Dragon Technology** (TP Vision). Les enceintes
embarquent un module **MediaTek MT7697D** (Cortex-M4 + WiFi, stack lwIP) dédié à la fonction
Ambilight, distinct de la partie audio.

### 2.2 Découverte de service

Les enceintes s'annoncent en mDNS :

```
service  : _tpv_al._tcp        (TPV = TP Vision, AL = Ambilight)
port     : 2920
TXT      : seed=2  deviceid=MT7697D
```

Le port 2920 annoncé est **fermé en TCP** (RST) mais **ouvert en UDP** : les ports voisins
2919 et 2921 renvoient un ICMP *port unreachable*, pas 2920. L'annonce mDNS en `_tcp` est
donc erronée côté fabricant ; le transport réel est UDP.

Les enceintes exposent également un serveur **HTTP sur le port 80** qui répond à toute
requête GET, quel que soit le chemin, par une page unique de 66 octets :

```html
<html><body>
<title>AL Air</title>
<h1>OK_GET</h1>
</body></html>
```

Le titre confirme directement la fonction (« AL Air » = Ambilight Air). Le rôle exact de ce
serveur reste à déterminer (§5).

### 2.3 Transport

La TV diffuse en **multicast UDP vers `224.8.0.8:2920`**, à raison d'un datagramme de
**120 octets toutes les ~37 ms** (≈ 27 Hz). Le groupe est effectivement rejoint par IGMP :
la table MDB du bridge montre les deux ports WiFi abonnés.

```
dev localBridge port wlp10s0 grp 224.8.0.8 temp
dev localBridge port wlp11s0 grp 224.8.0.8 temp
```

En parallèle, la TV émet le **même payload en unicast vers le port 2930** de chaque enceinte,
qui répond systématiquement par un ICMP *port unreachable*. Ce canal est donc inactif ;
hypothèse : vestige d'une génération antérieure d'appareils. À documenter, sans impact.

### 2.4 Structure de la trame

Le protocole est **en clair, sans chiffrement ni authentification**. La structure suivante
a été déduite d'une capture et se vérifie arithmétiquement :

| Offset | Taille | Contenu |
|---|---|---|
| 0 | 12 | `"AmbilightAir"` — magic ASCII |
| 12 | 2 | `01 00` — version présumée |
| 14 | 16 | MAC de l'émetteur en ASCII majuscule + suffixe : `"788A866B7C5F0239"` |
| 30 | 90 | 10 enregistrements de 9 octets |

Chaque enregistrement :

```
00 00 <idx>  <R:u16 BE>  <G:u16 BE>  <B:u16 BE>
```

avec `idx` de `0x01` à `0x0a`, soit **10 zones**.

Le total fait `12 + 2 + 16 + 10 × 9 = 120` octets, exactement la taille observée : le
découpage est verrouillé, sans octet orphelin ni ambiguïté de cadrage.

Exemple décodé (premier datagramme capturé) :

| Zone | R | G | B |
|---|---|---|---|
| 1 | 256 | 256 | 256 |
| 2 | 595 | 513 | 367 |
| 3 | 735 | 613 | 428 |
| 4 | 256 | 256 | 256 |
| 5 | 522 | 394 | 295 |
| 6 | 380 | 308 | 250 |
| 7 | 1286 | 1043 | 855 |
| 8 | 7063 | 3650 | 2048 |
| 9 | 750 | 537 | 355 |
| 10 | 269 | 205 | 161 |

### 2.5 Validation sur l'intégralité de la capture

Le découpage ci-dessus a été rejoué sur les **909 trames** d'une capture de 12 secondes,
sans une seule anomalie :

- magic `AmbilightAir` présent sur toutes les trames ;
- octets de version constants à `0100` ;
- champ émetteur constant à `788A866B7C5F0239` ;
- indices de zone toujours `1..10`, dans l'ordre, sans trou ni doublon ;
- les deux octets de padding de chaque enregistrement **toujours** à `00 00`.

Le cadrage n'est donc pas une coïncidence observée sur quelques paquets : **la structure est
confirmée**.

Amplitude relevée par canal sur l'ensemble de la capture :

| Zone | valeurs distinctes | R min–max | G min–max | B min–max |
|---|---|---|---|---|
| 1 | 39 | 256 – 299 | 193 – 256 | 148 – 256 |
| 2 | 132 | 256 – 1058 | 251 – 713 | 206 – 488 |
| 3 | 119 | 256 – 871 | 248 – 711 | 203 – 493 |
| 4 | 10 | 256 – 264 | 244 – 256 | 192 – 256 |
| 5 | 178 | 256 – 522 | 222 – 410 | 174 – 322 |
| 6 | 145 | 256 – 701 | 227 – 443 | 182 – 318 |
| 7 | 147 | 256 – 2002 | 242 – 1238 | 200 – 855 |
| 8 | 159 | 256 – 16050 | 256 – 8991 | 226 – 5062 |
| 9 | 148 | 256 – 3242 | 250 – 2293 | 208 – 1402 |
| 10 | 108 | 256 – 1053 | 194 – 700 | 149 – 443 |

**Hypothèse sur l'échelle — explicitement non résolue.** Trois faits contraignent la
solution sans la déterminer :

1. le maximum atteint **16050** (`0x3eb2`), soit une dynamique de 1:63 par rapport à `0x0100` :
   ce n'est ni du 8 bits, ni une échelle 0-1 bornée ;
2. le canal R possède un **plancher exact à 256 (`0x0100`) sur les dix zones**, tandis que G
   descend à 193 et B à 148 — `0x0100` est donc une valeur remarquable, mais pas un « repos
   neutre » commun aux trois canaux ;
3. sur toute la capture, la relation `R > G > B` n'est jamais enfreinte.

L'hypothèse de travail reste une **virgule fixe où `0x0100` vaut 1.0**, portant des
intensités linéaires non bornées de type HDR. Mais le plancher asymétrique et la dominance
systématique du rouge admettent d'autres lectures — contenu uniformément chaud pendant la
capture, gain relatif normalisé sur R, ou espace colorimétrique autre que RGB.

Une première hypothèse plus simple (« `0x0100` est le repos neutre des trois canaux ») a été
formulée à partir de quatre paquets, puis **infirmée** par le passage à 909. C'est
précisément ce qui motive le choix méthodologique de §5.2 : établir l'échelle par stimulus
contrôlé, et non par déduction sur des captures d'opportunité.

## 3. Décisions produit

**Cohabitation avec le téléviseur** : Home Assistant n'émet **que si aucune source
concurrente n'est détectée** sur le multicast depuis un délai donné. La TV reste maîtresse
quand elle est allumée ; HA récupère les enceintes le reste du temps. Ce choix élimine toute
course entre deux émetteurs et rend le comportement prévisible.

**Nom et périmètre** : domaine `ambilight_air`, et non « TAW6205 ». Le protocole est
générique — le magic ASCII et le service mDNS le désignent comme le protocole Ambilight Air
de TP Vision, dont les enceintes ne sont qu'un client. L'intégration accepte tout appareil
s'annonçant en `_tpv_al._tcp` ; les TAW6205 sont simplement le seul matériel disponible pour
la validation.

**Cible de publication** : dépôt GitHub dédié, publié comme *custom repository* HACS, en
respectant dès le premier commit les exigences du core Home Assistant (config flow, tests,
`hassfest`, traductions) pour permettre une soumission ultérieure sans réécriture.

## 4. Architecture

### 4.1 Deux livrables, un dépôt

```
tools/                              outillage de reverse engineering (Python)
custom_components/ambilight_air/    l'intégration Home Assistant
```

L'outillage n'est pas jetable au sens de « sans valeur » : il survit à la phase de RE pour
rejouer les captures et déboguer en production. Il n'est jamais chargé par Home Assistant.

### 4.2 Règle structurante : couche protocole sans dépendance à HA

La couche protocole est une **bibliothèque pure, sans le moindre import Home Assistant**.
Trois bénéfices : elle se teste sans HA ni event loop, l'outillage de RE la réutilise telle
quelle, et l'externalisation en package PyPI — que le core HA demande fréquemment pour la
logique device — est déjà préparée.

| Module | Responsabilité | Dépendances |
|---|---|---|
| `protocol/frame.py` | encode/decode d'une trame, aucune I/O | aucune |
| `protocol/transport.py` | socket multicast asyncio, join IGMP, émission | asyncio |
| `protocol/source_monitor.py` | détection d'une source concurrente (la TV) | `frame` |

### 4.3 Le point d'architecture non évident

Ce protocole n'est **pas** un modèle requête/réponse par appareil. Une **seule trame
multicast** porte les 10 zones et s'adresse simultanément à toutes les enceintes. Les
entités Home Assistant ne sont donc pas indépendantes : elles partagent un émetteur unique.

D'où un **coordinateur** qui détient le socket et l'état global, arbitre TV/HA et sérialise
l'émission. Les entités ne touchent jamais au socket ; elles soumettent une intention.

### 4.4 Une décision suspendue au résultat du RE

Le modèle d'entités dépend d'une inconnue : chaque enceinte consomme-t-elle **une zone
donnée** parmi les 10, ou toutes lisent-elles la même information ?

- **Si les enceintes sont adressables individuellement** : une entité `light` par enceinte.
- **Sinon** : une entité unique globale. Exposer deux entités indépendantes alors que le
  matériel ne le permet pas serait mentir à l'utilisateur.

Le coordinateur reste l'invariant dans les deux cas. Le mapping zone→entité est une sortie
explicite de la phase de RE.

## 5. Plan d'expériences du reverse engineering

### 5.1 Jalon 0 — go/no-go

**Peut-on faire réagir une enceinte avec une trame forgée ?** On rejoue une trame capturée
à l'identique, téléviseur éteint, et on observe.

Tout le volet « pilotage depuis HA » en dépend. Ce jalon passe **avant tout autre
investissement** : si les enceintes n'obéissent qu'au téléviseur appairé, on l'apprend en
quelques minutes plutôt qu'après avoir écrit une intégration. Repli en cas d'échec : la spec
et le décodage restent valides et la phase 2 (relais vers les lampes) reste entièrement
réalisable ; seule l'émission tombe.

### 5.2 Inconnues à lever, par ordre de valeur

**Échelle des `u16`.** Hypothèse de travail : virgule fixe `0x0100` = 1.0, intensités
linéaires non bornées (§2.5). La méthode retenue n'est pas la déduction mais le **stimulus
contrôlé** : afficher sur le téléviseur du blanc, du noir, puis du rouge, du vert et du bleu
purs en plein écran, capturer le flux correspondant, et établir la table de conversion.

Deux questions que ce protocole expérimental tranche immédiatement, et que la seule
observation passive ne peut pas départager :

- **Un bleu pur plein écran conserve-t-il `R > G > B` ?** Si oui, les trois `u16` ne sont pas
  des canaux RGB et il faut chercher un autre espace colorimétrique. Si non, la dominance du
  rouge relevée en §2.5 n'était qu'une propriété du contenu diffusé pendant la capture.
- **Le noir plein écran produit-il exactement `0x0100` sur les trois canaux ?** Cela
  identifierait la valeur de repos et expliquerait le plancher asymétrique observé.

Ces deux réponses suffisent à choisir entre les lectures concurrentes de §2.5.

**Mapping zone→enceinte.** Injection d'une seule zone à intensité vive, les neuf autres à
zéro, et relevé de l'enceinte qui réagit. Cette expérience tranche la décision suspendue de
§4.4.

**Filtrage de l'émetteur.** Trois essais : MAC du téléviseur usurpée, MAC du serveur, MAC
arbitraire. Détermine si l'usurpation d'identité est nécessaire pour être accepté. Elle est
sans risque de conflit puisqu'on n'émet que téléviseur silencieux.

**Comportement en l'absence de flux.** Quand la source se tait, les enceintes s'éteignent-elles,
et après quel délai ? Ce résultat fixe la cadence de keep-alive et la manière d'éteindre
proprement (émission de zéros, ou arrêt de l'émission).

**Cadence utile.** 27 Hz est ce que fait le téléviseur, pas nécessairement le minimum
requis. Descendre autant que possible sans saccade visible est un gain direct sur un WiFi
2,4 GHz déjà chargé.

**Champs non identifiés.** Rôle du serveur HTTP `:80` (appairage ?), du suffixe `0239` dans
l'identifiant émetteur, du `seed=2` annoncé en mDNS, et des octets `01 00` en offset 12.
Utiles pour la complétude de la spec, non bloquants pour le pilotage.

**Canal unicast 2930.** À documenter comme vestige inactif.

### 5.3 Principes de méthode

**Chaque capture `.pcap` devient une fixture de test permanente** du décodeur. Le travail de
RE produit ainsi directement le harnais de tests de l'intégration, plutôt qu'un artefact
jeté après usage.

**Aucune écriture persistante dans les enceintes** : uniquement des trames de rendu, aucun
firmware, aucune modification de configuration. Le risque matériel est nul et le seul effet
de bord est visuel — les expériences se font donc téléviseur éteint.

## 6. Design de l'intégration Home Assistant

### 6.1 Config flow

Découverte **zeroconf** sur `_tpv_al._tcp` déclarée dans le `manifest.json` : chaque enceinte
allumée fait apparaître spontanément une proposition d'ajout. Ajout manuel par adresse IP en
secours. Déduplication sur l'identifiant mDNS.

Modèle par défaut : une entrée de configuration par enceinte, avec un **coordinateur unique
partagé** dans `hass.data[DOMAIN]` puisqu'il n'existe qu'un seul flux multicast. Bascule vers
une entrée unique si §4.4 le commande.

### 6.2 Entités et état

L'entité `light` n'est pas seulement un émetteur : elle **décode en permanence le flux
multicast**, y compris celui émis par le téléviseur. Conséquence — quand le téléviseur a la
main, l'entité affiche la couleur réellement produite par l'enceinte, au lieu d'un état
inventé ou d'un `unavailable` qui casserait les automatisations. **L'état affiché est
toujours la vérité du terrain.**

Si l'utilisateur tente de piloter une enceinte pendant que le téléviseur émet, la commande
est refusée par une `HomeAssistantError` explicite plutôt qu'ignorée en silence.

Un `binary_sensor` « source TV active » expose l'état de l'arbitrage pour que les
automatisations puissent s'y adosser.

Le mapping `brightness` + couleur HA vers les `u16` du protocole découle de la table de
conversion établie en §5.2.

### 6.3 Flux de données

Le coordinateur détient le socket.

- **Réception** (permanente) : décodage de chaque trame → mise à jour de l'état des entités
  et alimentation du détecteur de source.
- **Émission** (seulement si le téléviseur se tait) : assemblage de l'état voulu des 10 zones
  en une trame unique, émise à la cadence retenue.

### 6.4 Gestion d'erreurs

**Multicast inaccessible.** Sur une installation Docker en réseau bridgé, le groupe
`224.8.0.8` n'arrivera jamais. Sans traitement, l'intégration resterait muette et
l'utilisateur ouvrirait un ticket. On détecte l'absence totale de trafic après un délai et
on lève `ConfigEntryNotReady` avec un message nommant explicitement la cause et la
condition requise (`network_mode: host` ou HAOS).

**Hôte multi-interfaces.** Le join IGMP doit cibler la bonne interface — Nivuus en est
l'illustration avec ses trois bridges. Par défaut, l'interface est déduite de la route vers
l'appareil découvert ; une option permet de la forcer.

**Déchargement.** `async_unload_entry` ferme le socket, quitte le groupe multicast et annule
les tâches de fond.

## 7. Tests

Le découpage de §4.2 rend le codec testable à couverture complète sans Home Assistant ni
event loop.

| Niveau | Contenu |
|---|---|
| Codec | Décodage des vecteurs `.pcap` réels ; **round-trip** decode→encode identique à l'octet près |
| Config flow | Découverte zeroconf, doublon, ajout manuel, appareil injoignable |
| Coordinateur | Arbitrage TV/HA, expiration du détecteur de source, refus de commande |
| Erreurs | Absence de multicast → `ConfigEntryNotReady` |

Le test de round-trip est celui qui garantit qu'on n'a pas seulement *cru* comprendre le
protocole : tout octet mal interprété le fait échouer.

Outillage : `pytest-homeassistant-custom-component`.

## 8. Publication

- Dépôt GitHub dédié, `hacs.json`, licence **Apache-2.0** (celle du core HA, pour éviter un
  changement de licence lors d'une soumission).
- CI GitHub Actions : **`hassfest`** et l'action de validation **HACS** dès le premier commit
  — ce sont les validateurs officiels qui déterminent objectivement si l'intégration est
  publiable. Plus `ruff`, `mypy`, et `pytest` avec seuil de couverture.
- Traductions `en` et `fr` (`strings.json` + `translations/`).
- `quality_scale.yaml` renseigné dès le départ, rendant visible ce qui manque pour monter en
  niveau.
- Releases sémantiques taguées — HACS s'appuie sur les releases GitHub.

**La spec du protocole est un livrable à part entière**, publiée dans le dépôt et rédigée en
anglais. Elle répond au deuxième objectif, a la valeur de long terme la plus élevée (le code
vieillit, une spec correcte reste) et fonde directement la phase 2.

## 9. Risques

| Risque | Impact | Traitement |
|---|---|---|
| Les enceintes refusent toute trame non émise par le téléviseur appairé | Le pilotage depuis HA devient impossible | Jalon 0 en tout premier (§5.1) ; repli documenté sur spec + phase 2 |
| L'échelle des `u16` ne se réduit pas à une conversion simple | Rendu des couleurs imprécis | Calibration par stimulus contrôlé plutôt que par déduction (§5.2) |
| Les enceintes ne sont pas adressables individuellement | Modèle d'entités plus pauvre qu'espéré | Décision explicitement suspendue au RE (§4.4), pas de promesse d'interface prématurée |
| Le multicast ne passe pas chez les utilisateurs en Docker bridgé | Tickets et mauvaise réputation | Détection et message d'erreur nommant la cause (§6.4) |
| Un seul modèle matériel disponible pour la validation | Comportement inconnu sur d'autres appareils | Périmètre annoncé honnêtement dans le README ; retours terrain via HACS avant toute soumission au core |

## 10. Points ouverts

- Nom exact et emplacement du dépôt GitHub, à trancher au moment du plan.
- Laquelle des deux TV Philips du réseau (« Salle TV », « Salon ») correspond à
  `192.168.0.183` — sans impact sur le design, à confirmer pendant le RE.
