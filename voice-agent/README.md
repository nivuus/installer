# voice-agent

OpenAI-compatible shim that adds Home Assistant tool-calling in front of ollama,
for the Unmute voice stack. See
`docs/superpowers/specs/2026-07-22-unmute-voice-agent-design.md`.

## Run locally

    export OLLAMA_API_KEY=$(cat /root/.config/nivuus/ollama-api.key)
    export HA_TOKEN=<token from /opt/nivuus/HomeAssistant/data/.mcp.json>
    export OLLAMA_URL=http://127.0.0.1:11435
    cd voice-agent && uvicorn voice_agent.app:build --factory --port 11436

## Smoke test

    curl -sN http://127.0.0.1:11436/v1/chat/completions \
      -H 'content-type: application/json' \
      -d '{"model":"qwen3:4b","stream":true,
           "messages":[{"role":"user","content":"Quelles pièces as-tu ?"}]}'

## Build & run the image

    cd voice-agent
    docker build -t nivuus-voice-agent:latest .
    docker run --rm -p 11436:11436 \
      -e OLLAMA_API_KEY=$(cat /root/.config/nivuus/ollama-api.key) \
      -e HA_TOKEN=<token from /opt/nivuus/HomeAssistant/data/.mcp.json> \
      -e OLLAMA_URL=http://172.17.0.1:11435 \
      nivuus-voice-agent:latest

Note: `OLLAMA_URL` inside the container must reach the host's ollama proxy
(`172.17.0.1` is the default Docker bridge gateway, matching `config.yaml`'s
default). The container also needs the Home Assistant MCP server available
at the path configured in `config.yaml` (`mcp_command`) — on this host that
lives outside the image at `/opt/nivuus/Agent2/homeassistant-mcp`, so running
the container standalone requires bind-mounting it in, e.g.:

    -v /opt/nivuus/Agent2/homeassistant-mcp:/opt/nivuus/Agent2/homeassistant-mcp:ro

## Standalone end-to-end validation (2026-07-22)

Ran outside Docker (venv) against the live stack: `nivuus-ollama` (GPU, model
`qwen3:4b`) via the OpenAI-compatible proxy on `:11435`, and the real
Home Assistant MCP server (`/opt/nivuus/Agent2/homeassistant-mcp`) talking to
the live `home.allanic.me` instance.

- `GET /health` → `{"status":"ok","mcp":true}`
- **Read smoke test** (`"Quelles pièces connais-tu ?"`): the model called
  `get_areas` and then read out the returned French room names in a short
  spoken-style reply.
- **Action smoke test** (`"Éteins la lumière du salon."`): the model called
  `call_service` with `domain: light`, `service: turn_off`,
  `entity_id: light.lumiere_salon`, and the light's HA state flipped from
  `on` to `off`; the reply confirmed the action in French.

Full transcripts, exact tool-call arguments, and the reliability assessment
(hit rate across repeated runs) are in
`.superpowers/sdd/task-5-report.md`.
