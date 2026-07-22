# Assistant vocal temps réel avec tool-calling (Unmute + voice-agent)

**Date** : 2026-07-22
**Statut** : design validé, prêt pour plan d'implémentation

## Objectif

Doter Nivuus d'un assistant vocal temps réel, auto-hébergé, capable d'**agir**
sur la domotique — pas seulement de converser. Conversation naturelle en
français, latence perçue faible, et accès à des outils Home Assistant via le
serveur MCP existant.

## Contexte et contraintes

| Contrainte | Valeur |
|---|---|
| GPU | RTX 4070, **12 Go de VRAM**, partagé avec la VM Windows (VFIO) |
| LLM en place | `qwen3:14b` Q4_K_M (8,64 Go) dans `nivuus-ollama`, `keep_alive=-1` |
| API LLM | proxy nginx `:11435`, OpenAI-compatible, gate Bearer |
| MCP disponible | `/opt/nivuus/Agent2/homeassistant-mcp/server.py`, stdio, ~186 outils |
| Port 80 hôte | déjà pris (`192.168.0.1:80`, python3) |

## Options écartées

**Speech-to-speech end-to-end** (Moshi, Qwen2.5-Omni, MiniCPM-o, Kimi-Audio).
Moshi n'a pas de tool-calling et son architecture — génération continue de
tokens audio sur deux canaux parallèles — n'offre aucun point d'insertion pour
un aller-retour d'outil. Les modèles omni ont un function-calling texte encore
immature en mode audio, et Qwen3-Omni ne tient pas en 12 Go. Un assistant qui ne
peut pas agir ne répond pas au besoin.

**Unmute seul.** Vérification faite dans le code : `unmute/llm/llm_utils.py:143`
appelle `chat.completions.create(model, messages, stream=True, temperature)`
sans paramètre `tools`, et la boucle de streaming ne lit que `delta.content`.
`grep -rniE "tool_call|\"tools\"|function_call|mcp"` sur le dépôt ne renvoie
rien. Unmute ne fait aucun tool-calling.

**Pipeline Assist de Home Assistant** (Whisper + Kokoro en Wyoming). Fonctionnel
sans écrire de code, mais interdit les MCP arbitraires et le full-duplex.

**Fork d'Unmute.** Coût de rebase permanent pour un gain identique à celui du
shim, avec un couplage bien plus fort.

## Architecture

```
Navigateur (LAN home) ──► Traefik :8090
                            ├─► frontend Unmute (Next.js)
                            └─► backend Unmute (WebSocket audio)
                                  ├─► STT  ws://stt:8080   moshi-server, ~2,5 Go VRAM
                                  ├─► TTS  ws://tts:8080   moshi-server, ~5,3 Go VRAM
                                  └─► KYUTAI_LLM_URL ─► voice-agent :11436
                                                          ├─► ollama :11435 (qwen3:4b)
                                                          └─► MCP HA (stdio, 12 outils)
```

Le **voice-agent** (« le shim ») est la seule pièce à écrire. Il se présente à
Unmute comme un LLM OpenAI-compatible alors qu'il est un agent. Cette frontière
est le choix structurant du design : elle rend l'agent entièrement testable au
`curl`, sans micro ni Unmute, et laisse remplacer indépendamment le LLM ou le
front vocal.

## Budget VRAM

STT 2,5 + TTS 5,3 = **7,8 Go**, laissant ~4 Go. `qwen3:14b` (8,64 Go + ~1 Go de
KV cache) est donc exclu du chemin vocal.

- **Cerveau vocal** : `qwen3:4b` (~2,6 Go), chargé en permanence, `keep_alive=-1`.
- **`qwen3:14b`** reste installé pour les usages texte (HA `conversation.qwen3_local`,
  API `/v1`), avec un `keep_alive` fini pour qu'il libère la VRAM entre deux usages.

Total en régime vocal : ~10,4 Go sur 12 Go.

## Composants

| Emplacement | Contenu |
|---|---|
| `/opt/nivuus/unmute/` | clone du dépôt amont + `docker-compose.override.yml` et `.env` — même motif que `/opt/nivuus/ollama/` |
| `voice-agent/` (repo Nivuus) | le shim, versionné, packagé en image Docker |

Le shim vit dans le dépôt git — c'est du code maison, il mérite historique et
tests. Le compose reste sous `/opt/nivuus/` comme le reste de l'infrastructure ;
l'override y déclare le service `voice-agent` avec le dépôt Nivuus comme
contexte de build, chemin donné par une variable d'environnement dans `.env`
(le dépôt est en `/home/mallanic/Projects/Nivuus` sur cette machine).

### Découpage du shim

| Fichier | Responsabilité | Dépend de |
|---|---|---|
| `app.py` | endpoint `POST /v1/chat/completions`, streaming SSE | `agent.py` |
| `agent.py` | boucle de tool-calling, cap d'itérations, mode dégradé | `mcp_client.py`, client OpenAI |
| `mcp_client.py` | process MCP stdio, handshake, filtre de whitelist, conversion des schémas MCP → format `tools` OpenAI | — |
| `config.yaml` | whitelist, modèle, URL/clé ollama, prompt système, timeouts | — |

Chaque module est utilisable et testable seul : `mcp_client` sans LLM,
`agent` avec un MCP simulé, `app` avec un agent simulé.

## Whitelist d'outils

12 outils sur les 186, choisis pour la conversation domotique :

*Lecture* — `search_entities`, `get_entity_state`, `get_areas`,
`get_entities_by_area`, `get_scenes`, `get_history`, `render_template`

*Action* — `call_service`, `activate_scene`, `trigger_automation`,
`toggle_automation`, `send_notification`

Sont **délibérément exclus** : `restart_homeassistant`, `stop_homeassistant`,
`set_state`, `websocket_call`, tous les `delete_*`, `update_dashboard_*` et
`hacs_*`. Un modèle 4B pilotant un canal audio bruité ne doit pas pouvoir
redémarrer Home Assistant ou détruire un dashboard sur un malentendu. La
whitelist est une liste dans `config.yaml`, élargie au besoin.

## Flux d'un tour de parole

1. Audio navigateur → backend → STT streaming ; le VAD sémantique détecte la fin
   du tour de parole
2. Backend → `POST /v1/chat/completions` (stream) vers le shim
3. Le shim appelle ollama avec les 12 `tools` :
   - **aucun `tool_call`** → relais des deltas tels quels, latence ajoutée quasi
     nulle (cas majoritaire)
   - **`tool_call`** → n'émet rien, exécute via MCP, ré-injecte le résultat dans
     les messages, rappelle ollama, relaie la réponse finale
4. Deltas texte → TTS streaming → audio

Le shim bufferise le premier delta pour discriminer les deux cas ; c'est le seul
endroit où il coûte de la latence. Tours conversationnels : 400–750 ms attendus.
Tours avec action : ~1–2 s.

## Gestion des erreurs

Règle directrice : **la voix ne reste jamais muette.** Un assistant vocal
silencieux est cassé même quand il a techniquement raison.

| Panne | Comportement |
|---|---|
| MCP indisponible au démarrage | mode dégradé sans outils, conversation préservée, log en warning |
| Outil en échec ou timeout (5 s) | l'erreur est ré-injectée dans le contexte, le modèle l'annonce à l'oral |
| ollama injoignable (VM a le GPU) | réponse parlée explicite, jamais un timeout silencieux |
| Boucle de `tool_call` | cap à 3 itérations, puis réponse textuelle forcée |
| MCP mort en cours de session | redémarrage du process, une tentative, sinon bascule en mode dégradé |

## Cycle GPU / VM

`stt` et `tts` détiennent de la VRAM et suivent exactement le motif déjà en
place pour ollama :

- `bind-vfio-gpu.sh` → ajout de `stt` et `tts` au `docker compose stop`
- `rebind-host-gpu.sh` → ligne `up -d` sur le compose Unmute
- `vm-idle-shutdown.sh` → self-heal quand la VM est éteinte

`frontend`, `backend` et `voice-agent` n'utilisent pas le GPU et restent debout.

## Stockage des poids

Le compose amont monte `- /tmp/models/:/models` sur les services `stt` et `tts`.
Sur cette machine `/tmp` est une **tmpfs** : les poids Kyutai y résideraient en
RAM, seraient reperdus (donc re-téléchargés) à chaque reboot, et exposés au
remontage de tmpfs qui a déjà perturbé des sessions ici. L'override remappe ce
montage — ainsi que `./volumes/hf-cache` — vers du stockage persistant sous
`/opt/nivuus/unmute/`.

## Réseau

Traefik publie sur le **port 8090** (le 80 est occupé). Accessible depuis le LAN
home via `localBridge` ; la zone `external` étant déjà en REJECT, le service
n'est pas exposé au WAN. Pas d'authentification supplémentaire : même niveau de
confiance que Home Assistant sur ce LAN.

Le shim est un **service du même compose**, joignable par les autres containers
sous `http://voice-agent:11436` et **sans port publié sur l'hôte** : seul le
backend Unmute le consomme, il n'a aucune raison d'être atteignable depuis le
réseau. Pour le développement et les tests au `curl`, un override
(`voice-agent-expose.override.yml`, sur le modèle du `gpu-off.override.yml`
d'ollama) publie temporairement `127.0.0.1:11436`.

Le shim atteint ollama sur `http://<ip-hôte>:11435` — le proxy nginx tourne en
`network_mode: host`, donc `127.0.0.1` depuis un container bridge ne le joindrait
pas. L'IP de la passerelle du réseau compose est passée en variable
d'environnement.

## Tests

- `mcp_client` : handshake, filtre de whitelist, conversion de schémas, mort du
  process — contre le vrai serveur MCP
- `agent` : chemin sans outil, chemin avec outil, échec d'outil, cap
  d'itérations, mode dégradé — avec un MCP et un LLM simulés
- `app` : conformité du streaming SSE au format OpenAI
- Bout en bout au `curl` : « allume la lumière du salon » doit produire un
  `call_service` — **avant** tout branchement d'Unmute
- Validation finale au micro

## Séquencement

1. **Shim seul, avec tests.** Valide l'hypothèse centrale : `qwen3:4b` sait-il
   piloter ces 12 outils en français ? Indépendant d'Unmute — si ça échoue ici,
   rien n'est perdu.
2. **Déploiement d'Unmute.** Build, mesure de la VRAM réelle, branchement sur le
   shim, choix de la voix française.
3. **Hooks GPU et durcissement.**

## Risques

- Le build Rust de `moshi-server` est long et constitue le principal aléa du
  déploiement. Un token `HUGGING_FACE_HUB_TOKEN` est requis pour les poids.
- Les 7,8 Go de VRAM proviennent du README amont, pas d'une mesure sur cette
  carte. À vérifier à l'étape 2 ; si le réel dépasse, le cerveau vocal descend
  d'un cran.
- La capacité de `qwen3:4b` en tool-calling français est l'hypothèse critique,
  d'où sa validation en premier.
- Le compose amont est en mode hot-reloading (développement) ; il faudra
  s'aligner sur `bake_deploy_prod.sh`.

## Hors périmètre

Signalé sans être traité ici : le token Home Assistant est en clair dans
`/opt/nivuus/HomeAssistant/data/.mcp.json`, valide jusqu'en 2035. Le shim
n'aggrave pas la situation mais devrait consommer ce token via une variable
d'environnement, et le fichier mériterait le même traitement.
