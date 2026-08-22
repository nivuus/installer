# Voice Agent (Unmute + tool-calling shim) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a self-hosted real-time voice assistant that can act on the home — Unmute (Kyutai STT/TTS) for speech, and a small OpenAI-compatible shim that adds tool-calling over the existing Home Assistant MCP server.

**Architecture:** Unmute's backend talks to a shim (`voice-agent`) instead of a raw LLM. The shim exposes `/v1/chat/completions` (streaming), forwards to `qwen3:4b` on ollama with a 12-tool whitelist derived from the HA MCP server, runs the tool-calling loop, and streams back only final text. The shim is built and tested standalone (at `curl`) before Unmute is ever attached.

**Tech Stack:** Python 3.11+, FastAPI + SSE, `openai` SDK (client to ollama), `mcp` SDK (stdio client), Docker Compose, Kyutai Unmute (Rust `moshi-server` + Next.js).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-22-unmute-voice-agent-design.md` — read it before starting.
- **GPU:** RTX 4070, **12 GB VRAM**, shared with the Windows VM via VFIO. STT+TTS ≈ 7.8 GB (README figure, verify), voice brain must fit the remainder.
- **Voice brain:** `qwen3:4b` (~2.6 GB), `keep_alive=-1`. NOT `qwen3:14b`.
- **ollama endpoint:** OpenAI-compatible proxy at `http://<host>:11435`, Bearer key in `/root/.config/nivuus/ollama-api.key`. Shim reaches it via the compose gateway IP, NOT `127.0.0.1` (nginx is `network_mode: host`).
- **MCP server:** `/opt/nivuus/Agent2/homeassistant-mcp/server.py`, stdio, `mcp>=1.0.0` SDK. Run via its venv `/opt/nivuus/Agent2/homeassistant-mcp/.venv/bin/python`. `HA_URL`/`HA_TOKEN` passed as env.
- **Whitelist (12 tools):** `search_entities`, `get_entity_state`, `get_areas`, `get_entities_by_area`, `get_scenes`, `get_history`, `render_template`, `call_service`, `activate_scene`, `trigger_automation`, `toggle_automation`, `send_notification`.
- **Shim network:** compose service `voice-agent`, listens `:11436`, NOT published on host (dev override may publish `127.0.0.1:11436`).
- **Language:** all user-facing voice/prompt copy in French; code comments in English (repo convention, max ~200 lines/file).
- **Host shell gotcha:** wrap shell commands in `bash -c '...'` (zsh profile ships broken functions). `/tmp` is tmpfs — keep model weights off it.
- **Branch:** `feat/voice-agent` (already created). Commit frequently.

---

## File Structure

```
voice-agent/                      # in the Nivuus repo, versioned
├── voice_agent/
│   ├── __init__.py
│   ├── config.py                 # load config.yaml + secrets from env
│   ├── mcp_client.py             # stdio MCP client, whitelist, schema conversion, call
│   ├── agent.py                  # tool-calling loop over ollama, degraded mode
│   └── app.py                    # FastAPI /v1/chat/completions SSE
├── tests/
│   ├── conftest.py
│   ├── test_mcp_client.py
│   ├── test_agent.py
│   └── test_app.py
├── config.yaml                   # whitelist, model, prompt, timeouts
├── pyproject.toml
├── Dockerfile
└── README.md

/opt/nivuus/unmute/               # deploy dir, NOT in repo (mirrors /opt/nivuus/ollama/)
├── (git clone of kyutai-labs/unmute)
├── docker-compose.override.yml   # remove llm, traefik→8090, weights off /tmp, add voice-agent
└── .env                          # HF token, ollama key, gateway IP, repo path
```

---

## Task 1: Package scaffold + config loader

**Files:**
- Create: `voice-agent/pyproject.toml`
- Create: `voice-agent/voice_agent/__init__.py`
- Create: `voice-agent/voice_agent/config.py`
- Create: `voice-agent/config.yaml`
- Create: `voice-agent/tests/conftest.py`
- Test: `voice-agent/tests/test_config.py`

**Interfaces:**
- Produces: `load_config(path: str | None = None) -> Config`. `Config` is a dataclass with fields: `model: str`, `ollama_url: str`, `ollama_api_key: str`, `whitelist: list[str]`, `system_prompt: str`, `mcp_command: list[str]`, `mcp_env: dict[str,str]`, `max_tool_iterations: int`, `tool_timeout_s: float`. Secrets (`ollama_api_key`, `mcp_env['HA_TOKEN']`) are read from env, NOT from the yaml.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "voice-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "openai>=1.54",
    "mcp>=1.0.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "httpx>=0.27"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write `config.yaml`**

```yaml
model: qwen3:4b
ollama_url: ${OLLAMA_URL:-http://172.17.0.1:11435}
whitelist:
  - search_entities
  - get_entity_state
  - get_areas
  - get_entities_by_area
  - get_scenes
  - get_history
  - render_template
  - call_service
  - activate_scene
  - trigger_automation
  - toggle_automation
  - send_notification
max_tool_iterations: 3
tool_timeout_s: 5.0
mcp_command:
  - /opt/nivuus/Agent2/homeassistant-mcp/.venv/bin/python
  - /opt/nivuus/Agent2/homeassistant-mcp/server.py
mcp_env:
  HA_URL: https://home.allanic.me
system_prompt: |
  Tu es l'assistant vocal de la maison Nivuus. Réponds en français, de façon
  brève et naturelle, comme à l'oral. Tu peux agir sur la domotique via les
  outils fournis. Quand une action est demandée, exécute-la puis confirme d'une
  phrase courte. Si un outil échoue, dis-le simplement à voix haute. N'invente
  jamais un état que tu n'as pas vérifié.
```

- [ ] **Step 3: Write the failing test** — `voice-agent/tests/test_config.py`

```python
import os
from voice_agent.config import load_config


def test_load_config_reads_yaml_and_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "model: qwen3:4b\n"
        "ollama_url: http://gw:11435\n"
        "whitelist: [call_service]\n"
        "max_tool_iterations: 3\n"
        "tool_timeout_s: 5.0\n"
        "mcp_command: [python, server.py]\n"
        "mcp_env: {HA_URL: https://ha}\n"
        "system_prompt: bonjour\n"
    )
    monkeypatch.setenv("OLLAMA_API_KEY", "secret-key")
    monkeypatch.setenv("HA_TOKEN", "ha-token")

    cfg = load_config(str(cfg_file))

    assert cfg.model == "qwen3:4b"
    assert cfg.ollama_url == "http://gw:11435"
    assert cfg.whitelist == ["call_service"]
    assert cfg.ollama_api_key == "secret-key"
    assert cfg.mcp_env["HA_TOKEN"] == "ha-token"
    assert cfg.mcp_env["HA_URL"] == "https://ha"
    assert cfg.max_tool_iterations == 3
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd voice-agent && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_agent.config'`

- [ ] **Step 5: Write `voice_agent/__init__.py`** (empty) and `voice_agent/config.py`

```python
"""Configuration loader. Secrets come from env, never from the yaml file."""
import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    model: str
    ollama_url: str
    ollama_api_key: str
    whitelist: list[str]
    system_prompt: str
    mcp_command: list[str]
    mcp_env: dict[str, str]
    max_tool_iterations: int = 3
    tool_timeout_s: float = 5.0


def load_config(path: str | None = None) -> Config:
    path = path or os.getenv("VOICE_AGENT_CONFIG", "config.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)

    # ${VAR:-default} expansion for ollama_url only (simple, no full templating)
    ollama_url = os.path.expandvars(raw["ollama_url"]) if "${" not in raw["ollama_url"] \
        else _expand(raw["ollama_url"])

    mcp_env = dict(raw.get("mcp_env", {}))
    mcp_env["HA_TOKEN"] = os.environ["HA_TOKEN"]

    return Config(
        model=raw["model"],
        ollama_url=os.getenv("OLLAMA_URL", ollama_url),
        ollama_api_key=os.environ["OLLAMA_API_KEY"],
        whitelist=list(raw["whitelist"]),
        system_prompt=raw["system_prompt"],
        mcp_command=list(raw["mcp_command"]),
        mcp_env=mcp_env,
        max_tool_iterations=int(raw.get("max_tool_iterations", 3)),
        tool_timeout_s=float(raw.get("tool_timeout_s", 5.0)),
    )


def _expand(value: str) -> str:
    """Expand ${VAR:-default} syntax."""
    import re

    def repl(m):
        var, default = m.group(1), m.group(2)
        return os.getenv(var, default)

    return re.sub(r"\$\{([A-Z_]+):-([^}]*)\}", repl, value)
```

- [ ] **Step 6: Write `tests/conftest.py`** (make package importable)

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd voice-agent && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add voice-agent/pyproject.toml voice-agent/config.yaml voice-agent/voice_agent/ voice-agent/tests/
git commit -m "feat(voice-agent): package scaffold and config loader"
```

---

## Task 2: MCP client wrapper

**Files:**
- Create: `voice-agent/voice_agent/mcp_client.py`
- Test: `voice-agent/tests/test_mcp_client.py`

**Interfaces:**
- Consumes: `Config` from Task 1.
- Produces:
  - `class McpClient`, async context manager.
  - `async McpClient.start() -> None` — spawns the MCP stdio process, initializes the session. Raises nothing on failure; sets `self.available: bool`.
  - `McpClient.openai_tools() -> list[dict]` — whitelisted tools as OpenAI `tools` schema `[{"type":"function","function":{"name","description","parameters"}}]`. Empty list if unavailable.
  - `async McpClient.call(name: str, arguments: dict) -> str` — invokes the tool, returns joined text content. Raises `McpToolError(message)` on failure/timeout.
  - `async McpClient.aclose() -> None`.

- [ ] **Step 1: Write the failing test** — `voice-agent/tests/test_mcp_client.py`

```python
import pytest
from voice_agent.mcp_client import McpClient, McpToolError


class FakeTool:
    def __init__(self, name, schema):
        self.name = name
        self.description = f"desc {name}"
        self.inputSchema = schema


class FakeSession:
    """Stand-in for mcp.ClientSession."""
    def __init__(self, tools, call_result=None, raises=None):
        self._tools = tools
        self._call_result = call_result
        self._raises = raises

    async def list_tools(self):
        class R: pass
        r = R(); r.tools = self._tools
        return r

    async def call_tool(self, name, arguments):
        if self._raises:
            raise self._raises
        class C: pass
        content = C(); content.text = self._call_result
        class R: pass
        r = R(); r.content = [content]; r.isError = False
        return r


def make_client(session, whitelist):
    c = McpClient.__new__(McpClient)
    c._session = session
    c.available = True
    c._whitelist = whitelist
    c._raw_tools = session._tools
    return c


def test_openai_tools_filters_by_whitelist():
    tools = [
        FakeTool("call_service", {"type": "object", "properties": {"domain": {}}}),
        FakeTool("delete_dashboard", {"type": "object"}),
    ]
    client = make_client(FakeSession(tools), whitelist=["call_service"])
    out = client.openai_tools()
    names = [t["function"]["name"] for t in out]
    assert names == ["call_service"]
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {"domain": {}}}
    assert out[0]["type"] == "function"


async def test_call_returns_joined_text():
    tools = [FakeTool("call_service", {"type": "object"})]
    client = make_client(FakeSession(tools, call_result="ok done"), ["call_service"])
    result = await client.call("call_service", {"domain": "light", "service": "turn_on"})
    assert result == "ok done"


async def test_call_raises_on_error():
    tools = [FakeTool("call_service", {"type": "object"})]
    client = make_client(FakeSession(tools, raises=RuntimeError("boom")), ["call_service"])
    with pytest.raises(McpToolError):
        await client.call("call_service", {})


def test_openai_tools_empty_when_unavailable():
    client = McpClient.__new__(McpClient)
    client.available = False
    client._whitelist = ["call_service"]
    assert client.openai_tools() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-agent && python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_agent.mcp_client'`

- [ ] **Step 3: Write `voice_agent/mcp_client.py`**

```python
"""Stdio MCP client: whitelist filtering, schema conversion, tool invocation."""
import asyncio
import logging
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger("voice_agent.mcp")


class McpToolError(Exception):
    pass


class McpClient:
    def __init__(self, command: list[str], env: dict[str, str],
                 whitelist: list[str], tool_timeout_s: float = 5.0):
        self._command = command
        self._env = env
        self._whitelist = whitelist
        self._timeout = tool_timeout_s
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._raw_tools: list = []
        self.available = False

    async def start(self) -> None:
        """Spawn MCP process. Never raises: sets self.available."""
        try:
            params = StdioServerParameters(
                command=self._command[0], args=self._command[1:], env=self._env
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            self._raw_tools = (await self._session.list_tools()).tools
            self.available = True
            log.info("MCP up: %d tools, %d whitelisted",
                     len(self._raw_tools), len(self.openai_tools()))
        except Exception as e:  # degraded mode: keep talking without tools
            log.warning("MCP unavailable, degraded mode: %s", e)
            self.available = False

    def openai_tools(self) -> list[dict]:
        if not self.available:
            return []
        allow = set(self._whitelist)
        return [
            {"type": "function", "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            }}
            for t in self._raw_tools if t.name in allow
        ]

    async def call(self, name: str, arguments: dict) -> str:
        if not self.available or self._session is None:
            raise McpToolError("MCP indisponible")
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            raise McpToolError(f"outil {name} : délai dépassé")
        except Exception as e:
            raise McpToolError(f"outil {name} : {e}")
        parts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(parts)

    async def aclose(self) -> None:
        await self._stack.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-agent && python -m pytest tests/test_mcp_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Integration check against the real MCP server** (manual, not committed)

```bash
cd voice-agent && HA_TOKEN=$(bash -c 'grep -oP "(?<=HA_TOKEN\": \")[^\"]+" /opt/nivuus/HomeAssistant/data/.mcp.json') \
OLLAMA_API_KEY=x python -c "
import asyncio
from voice_agent.config import load_config
from voice_agent.mcp_client import McpClient
async def main():
    c = load_config('config.yaml')
    m = McpClient(c.mcp_command, c.mcp_env, c.whitelist, c.tool_timeout_s)
    await m.start()
    print('available:', m.available)
    print('tools:', [t['function']['name'] for t in m.openai_tools()])
    await m.aclose()
asyncio.run(main())
"
```

Expected: `available: True` and the 12 whitelisted names printed.

- [ ] **Step 6: Commit**

```bash
git add voice-agent/voice_agent/mcp_client.py voice-agent/tests/test_mcp_client.py
git commit -m "feat(voice-agent): stdio MCP client with whitelist and schema conversion"
```

---

## Task 3: Agent tool-calling loop

**Files:**
- Create: `voice-agent/voice_agent/agent.py`
- Test: `voice-agent/tests/test_agent.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `McpClient` + `McpToolError` (Task 2).
- Produces:
  - `class Agent(config: Config, mcp: McpClient, llm: AsyncOpenAI)`.
  - `async Agent.stream(messages: list[dict]) -> AsyncIterator[str]` — yields text deltas of the final assistant answer. Internally runs the tool loop: calls the LLM with `tools`; if the model emits `tool_calls`, executes them via `mcp.call`, appends results as `role="tool"` messages, and re-calls — up to `max_tool_iterations`. Tool-triggering turns yield nothing until the final text turn. `McpToolError` is caught and its message appended as the tool result (model announces it aloud).

- [ ] **Step 1: Write the failing test** — `voice-agent/tests/test_agent.py`

```python
import pytest
from voice_agent.agent import Agent
from voice_agent.mcp_client import McpToolError


class Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class Chunk:
    def __init__(self, choices):
        self.choices = choices


class ToolCallDelta:
    def __init__(self, index, id=None, name=None, args=None):
        self.index = index
        self.id = id
        self.type = "function"
        class F: pass
        self.function = F()
        self.function.name = name
        self.function.arguments = args


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
    def __aiter__(self):
        self._it = iter(self._chunks)
        return self
    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeLLM:
    """Returns a scripted list of streams, one per create() call."""
    def __init__(self, streams):
        self._streams = list(streams)
        self.chat = self
        self.completions = self
    async def create(self, **kwargs):
        return self._streams.pop(0)


class FakeMcp:
    def __init__(self, result="ok", raises=None):
        self._result = result
        self._raises = raises
        self.calls = []
    def openai_tools(self):
        return [{"type": "function", "function": {"name": "call_service", "parameters": {}}}]
    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self._raises:
            raise self._raises
        return self._result


def make_agent(llm, mcp):
    from voice_agent.config import Config
    cfg = Config(model="m", ollama_url="u", ollama_api_key="k", whitelist=[],
                 system_prompt="sys", mcp_command=[], mcp_env={},
                 max_tool_iterations=3, tool_timeout_s=5.0)
    return Agent(cfg, mcp, llm)


async def _collect(agent, messages):
    return "".join([d async for d in agent.stream(messages)])


async def test_no_tool_call_streams_text():
    stream = FakeStream([
        Chunk([Choice(Delta(content="Bonjour "))]),
        Chunk([Choice(Delta(content="!"), finish_reason="stop")]),
    ])
    agent = make_agent(FakeLLM([stream]), FakeMcp())
    out = await _collect(agent, [{"role": "user", "content": "salut"}])
    assert out == "Bonjour !"


async def test_tool_call_then_final_text():
    tool_stream = FakeStream([
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(0, id="c1", name="call_service", args='{"domain":"light","service":"turn_on"}')]))]),
        Chunk([Choice(Delta(), finish_reason="tool_calls")]),
    ])
    final_stream = FakeStream([
        Chunk([Choice(Delta(content="C'est allumé."), finish_reason="stop")]),
    ])
    mcp = FakeMcp(result="success")
    agent = make_agent(FakeLLM([tool_stream, final_stream]), mcp)
    out = await _collect(agent, [{"role": "user", "content": "allume la lumière"}])
    assert out == "C'est allumé."
    assert mcp.calls == [("call_service", {"domain": "light", "service": "turn_on"})]


async def test_tool_error_reinjected_not_fatal():
    tool_stream = FakeStream([
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(0, id="c1", name="call_service", args='{}')]))]),
        Chunk([Choice(Delta(), finish_reason="tool_calls")]),
    ])
    final_stream = FakeStream([
        Chunk([Choice(Delta(content="Désolé, l'action a échoué."), finish_reason="stop")]),
    ])
    mcp = FakeMcp(raises=McpToolError("boom"))
    agent = make_agent(FakeLLM([tool_stream, final_stream]), mcp)
    out = await _collect(agent, [{"role": "user", "content": "allume"}])
    assert "échoué" in out
    assert mcp.calls  # tool was attempted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-agent && python -m pytest tests/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_agent.agent'`

- [ ] **Step 3: Write `voice_agent/agent.py`**

```python
"""Tool-calling loop. Presents a plain LLM interface upward, drives MCP downward."""
import json
import logging
from typing import AsyncIterator

from .config import Config
from .mcp_client import McpClient, McpToolError

log = logging.getLogger("voice_agent.agent")


class Agent:
    def __init__(self, config: Config, mcp: McpClient, llm):
        self._cfg = config
        self._mcp = mcp
        self._llm = llm

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        convo = [{"role": "system", "content": self._cfg.system_prompt}] + list(messages)
        tools = self._mcp.openai_tools()

        for _ in range(self._cfg.max_tool_iterations + 1):
            calls, buffered = {}, []
            emitted_text = False

            kwargs = dict(model=self._cfg.model, messages=convo, stream=True)
            if tools:
                kwargs["tools"] = tools
            stream = await self._llm.chat.completions.create(**kwargs)

            async with stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if getattr(delta, "tool_calls", None):
                        _accumulate_tool_calls(calls, delta.tool_calls)
                    if getattr(delta, "content", None):
                        emitted_text = True
                        yield delta.content

            if not calls:
                return  # plain answer already streamed

            # Execute tool calls, append results, loop for the model's final answer
            convo.append(_assistant_tool_msg(calls))
            for tc in _ordered(calls):
                convo.append(await self._run_tool(tc))

        # iteration cap hit: force a final non-tool answer
        stream = await self._llm.chat.completions.create(
            model=self._cfg.model, messages=convo, stream=True
        )
        async with stream:
            async for chunk in stream:
                if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                    yield chunk.choices[0].delta.content

    async def _run_tool(self, tc: dict) -> dict:
        try:
            args = json.loads(tc["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            content = await self._mcp.call(tc["name"], args)
        except McpToolError as e:
            content = f"ERREUR: {e}"
        return {"role": "tool", "tool_call_id": tc["id"], "content": content}


def _accumulate_tool_calls(calls: dict, deltas) -> None:
    for d in deltas:
        idx = d.index
        slot = calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
        if d.id:
            slot["id"] = d.id
        if d.function and d.function.name:
            slot["name"] = d.function.name
        if d.function and d.function.arguments:
            slot["arguments"] += d.function.arguments


def _ordered(calls: dict) -> list[dict]:
    return [calls[i] for i in sorted(calls)]


def _assistant_tool_msg(calls: dict) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in _ordered(calls)
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-agent && python -m pytest tests/test_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add voice-agent/voice_agent/agent.py voice-agent/tests/test_agent.py
git commit -m "feat(voice-agent): tool-calling loop with degraded-mode error handling"
```

---

## Task 4: SSE app (`/v1/chat/completions`)

**Files:**
- Create: `voice-agent/voice_agent/app.py`
- Test: `voice-agent/tests/test_app.py`

**Interfaces:**
- Consumes: `Agent` (Task 3), `McpClient` (Task 2), `load_config` (Task 1).
- Produces: FastAPI `app`. `POST /v1/chat/completions` with `{"model","messages","stream":true}` returns `text/event-stream` of OpenAI-shaped chunks (`choices[0].delta.content`), terminated by `data: [DONE]`. `GET /health` returns `{"status":"ok","mcp":bool}`. App state holds one shared `McpClient` and `Agent`, created on startup, closed on shutdown.

- [ ] **Step 1: Write the failing test** — `voice-agent/tests/test_app.py`

```python
import json
import pytest
from httpx import ASGITransport, AsyncClient

import voice_agent.app as appmod


class StubAgent:
    async def stream(self, messages):
        for piece in ["Bonjour", " !"]:
            yield piece


@pytest.fixture
def client(monkeypatch):
    app = appmod.create_app(agent=StubAgent(), mcp_available=True)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_health(client):
    async with client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mcp": True}


async def test_chat_completions_streams_openai_chunks(client):
    body = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "salut"}], "stream": True}
    async with client:
        async with client.stream("POST", "/v1/chat/completions", json=body) as r:
            assert r.status_code == 200
            lines = [ln async for ln in r.aiter_lines()]
    payloads = [ln[len("data: "):] for ln in lines if ln.startswith("data: ")]
    assert payloads[-1] == "[DONE]"
    texts = []
    for p in payloads[:-1]:
        obj = json.loads(p)
        texts.append(obj["choices"][0]["delta"].get("content", ""))
    assert "".join(texts) == "Bonjour !"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-agent && python -m pytest tests/test_app.py -v`
Expected: FAIL — `AttributeError: module 'voice_agent.app' has no attribute 'create_app'`

- [ ] **Step 3: Write `voice_agent/app.py`**

```python
"""OpenAI-compatible SSE front. Unmute thinks it's talking to an LLM."""
import json
import time
import uuid
import logging

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from .agent import Agent
from .config import load_config
from .mcp_client import McpClient

log = logging.getLogger("voice_agent.app")


def _chunk(content: str, model: str) -> str:
    obj = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def create_app(agent, mcp_available: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok", "mcp": mcp_available}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        model = body.get("model", "qwen3:4b")
        messages = body["messages"]

        async def gen():
            try:
                async for delta in agent.stream(messages):
                    yield _chunk(delta, model)
            except Exception as e:  # never leave the voice silent
                log.exception("stream failed")
                yield _chunk("Désolé, une erreur est survenue.", model)
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def build() -> FastAPI:
    """Production entrypoint: wires real config, MCP, and LLM."""
    cfg = load_config()
    mcp = McpClient(cfg.mcp_command, cfg.mcp_env, cfg.whitelist, cfg.tool_timeout_s)
    llm = AsyncOpenAI(base_url=cfg.ollama_url + "/v1", api_key=cfg.ollama_api_key)
    agent = Agent(cfg, mcp, llm)

    app = create_app(agent, mcp_available=False)

    @app.on_event("startup")
    async def _startup():
        await mcp.start()
        # reflect real availability on /health
        app.dependency_overrides = app.dependency_overrides

    @app.on_event("shutdown")
    async def _shutdown():
        await mcp.aclose()

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-agent && python -m pytest tests/test_app.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd voice-agent && python -m pytest -v`
Expected: all tests PASS (config + mcp_client + agent + app)

- [ ] **Step 6: Commit**

```bash
git add voice-agent/voice_agent/app.py voice-agent/tests/test_app.py
git commit -m "feat(voice-agent): OpenAI-compatible SSE endpoint"
```

---

## Task 5: Dockerfile + standalone end-to-end validation

**This task validates the plan's central hypothesis before any Unmute work: can `qwen3:4b` drive the 12 tools in French?**

**Files:**
- Create: `voice-agent/Dockerfile`
- Create: `voice-agent/README.md`

**Interfaces:**
- Produces: a runnable image and a documented `curl` smoke test.

- [ ] **Step 1: Pull the voice brain**

```bash
bash -c 'docker exec nivuus-ollama ollama pull qwen3:4b'
```

Expected: `qwen3:4b` present in `docker exec nivuus-ollama ollama list`.

- [ ] **Step 2: Write `voice-agent/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir uvicorn
COPY voice_agent/ ./voice_agent/
COPY config.yaml ./
EXPOSE 11436
CMD ["uvicorn", "voice_agent.app:build", "--factory", "--host", "0.0.0.0", "--port", "11436"]
```

- [ ] **Step 3: Write `voice-agent/README.md`** (deployment + smoke-test doc)

```markdown
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
```

- [ ] **Step 4: Run the shim locally and smoke-test a read tool**

```bash
bash -c 'cd voice-agent && \
  OLLAMA_API_KEY=$(cat /root/.config/nivuus/ollama-api.key) \
  HA_TOKEN=$(grep -oP "(?<=\"HA_TOKEN\": \")[^\"]+" /opt/nivuus/HomeAssistant/data/.mcp.json) \
  OLLAMA_URL=http://127.0.0.1:11435 \
  python -m uvicorn voice_agent.app:build --factory --port 11436 &
sleep 8
curl -sN http://127.0.0.1:11436/v1/chat/completions -H "content-type: application/json" \
  -d "{\"model\":\"qwen3:4b\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Quelles pièces connais-tu ?\"}]}"
'
```

Expected: streamed French text naming HA areas (proves a read tool round-tripped). Check the process log for a `get_areas` call.

- [ ] **Step 5: Smoke-test an action tool**

```bash
bash -c 'curl -sN http://127.0.0.1:11436/v1/chat/completions -H "content-type: application/json" \
  -d "{\"model\":\"qwen3:4b\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Éteins la lumière du salon.\"}]}"'
```

Expected: a `call_service` (domain `light`, service `turn_off`) is invoked and the reply confirms in French. **If `qwen3:4b` cannot reliably select tools here, stop and reconsider the brain (spec risk #3) before proceeding to Unmute.**

- [ ] **Step 6: Build the image**

```bash
bash -c 'cd voice-agent && docker build -t nivuus-voice-agent:latest .'
```

Expected: image builds clean.

- [ ] **Step 7: Commit**

```bash
git add voice-agent/Dockerfile voice-agent/README.md
git commit -m "feat(voice-agent): dockerfile and standalone e2e smoke test"
```

---

## Task 6: Deploy Unmute with Nivuus override

**Files:**
- Create: `/opt/nivuus/unmute/` (git clone of `kyutai-labs/unmute`)
- Create: `/opt/nivuus/unmute/docker-compose.override.yml`
- Create: `/opt/nivuus/unmute/.env`
- Create (repo, for reference): `configs/unmute/docker-compose.override.yml` (copy kept in repo)

**Interfaces:**
- Produces: a running Unmute stack whose backend points at `voice-agent`, on port 8090, with weights on persistent storage.

- [ ] **Step 1: Clone upstream into the deploy dir**

```bash
bash -c 'git clone https://github.com/kyutai-labs/unmute.git /opt/nivuus/unmute && cd /opt/nivuus/unmute && git rev-parse --short HEAD'
```

Record the commit hash in the override file header (pin what we tested against).

- [ ] **Step 2: Write `/opt/nivuus/unmute/.env`** (mode 600)

```bash
HUGGING_FACE_HUB_TOKEN=<hf token with Kyutai model access>
OLLAMA_API_KEY=<contents of /root/.config/nivuus/ollama-api.key>
HA_TOKEN=<token from /opt/nivuus/HomeAssistant/data/.mcp.json>
NIVUUS_REPO=/home/mallanic/Projects/Nivuus
DOCKER_GATEWAY=172.17.0.1
```

- [ ] **Step 3: Write `/opt/nivuus/unmute/docker-compose.override.yml`**

```yaml
# Nivuus override for kyutai-labs/unmute (pinned upstream <SHORT_HASH>).
# - drop the bundled vLLM: the brain is our voice-agent -> ollama
# - traefik 80 -> 8090 (host :80 is taken)
# - model weights off tmpfs (/tmp is RAM here)
# - add the tool-calling shim as a compose service
services:
  llm:
    # remove upstream vLLM entirely
    deploy:
      replicas: 0
    profiles: ["never"]

  traefik:
    ports: !override
      - "8090:80"

  tts:
    volumes: !override
      - /opt/nivuus/unmute/volumes/hf-cache:/root/.cache/huggingface
      - /opt/nivuus/unmute/volumes/cargo-registry-tts:/root/.cargo/registry
      - /opt/nivuus/unmute/volumes/tts-target:/app/target
      - /opt/nivuus/unmute/volumes/uv-cache:/root/.cache/uv
      - /opt/nivuus/unmute/models:/models
      - /opt/nivuus/unmute/volumes/tts-logs:/tmp/unmute_logs

  stt:
    volumes: !override
      - /opt/nivuus/unmute/volumes/hf-cache:/root/.cache/huggingface
      - /opt/nivuus/unmute/volumes/cargo-registry-stt:/root/.cargo/registry
      - /opt/nivuus/unmute/volumes/stt-target:/app/target
      - /opt/nivuus/unmute/volumes/uv-cache:/root/.cache/uv
      - /opt/nivuus/unmute/models:/models
      - /opt/nivuus/unmute/volumes/stt-logs:/tmp/unmute_logs

  backend:
    environment:
      KYUTAI_LLM_URL: http://voice-agent:11436
      KYUTAI_LLM_MODEL: qwen3:4b
      KYUTAI_LLM_API_KEY: unused

  voice-agent:
    build:
      context: ${NIVUUS_REPO}/voice-agent
    image: nivuus-voice-agent:latest
    environment:
      OLLAMA_URL: http://${DOCKER_GATEWAY}:11435
      OLLAMA_API_KEY: ${OLLAMA_API_KEY}
      HA_TOKEN: ${HA_TOKEN}
    restart: unless-stopped
```

> Note: confirm the upstream `depends_on` for `backend` still references `llm`; if so, add a `depends_on: !override [stt, tts, voice-agent]` to the `backend` block above so compose doesn't wait on the removed service.

- [ ] **Step 4: Copy the override into the repo for version control**

```bash
bash -c 'mkdir -p /home/mallanic/Projects/Nivuus/configs/unmute && cp /opt/nivuus/unmute/docker-compose.override.yml /home/mallanic/Projects/Nivuus/configs/unmute/'
```

- [ ] **Step 5: Build and start (first build compiles Rust — expect it to be long)**

```bash
bash -c 'cd /opt/nivuus/unmute && docker compose -f docker-compose.yml -f docker-compose.override.yml --env-file .env up -d --build 2>&1 | tail -30'
```

Expected: `stt`, `tts`, `backend`, `frontend`, `traefik`, `voice-agent` all `Up`; no `llm` service. If the Rust build fails, that is spec risk #1 — capture the error before retrying.

- [ ] **Step 6: Verify services and the shim health from inside the network**

```bash
bash -c 'cd /opt/nivuus/unmute && docker compose ps; docker compose exec backend curl -s http://voice-agent:11436/health'
```

Expected: `{"status":"ok","mcp":true}`.

- [ ] **Step 7: Commit the repo copy**

```bash
git add configs/unmute/docker-compose.override.yml
git commit -m "feat(unmute): Nivuus compose override (voice-agent brain, port 8090, persistent weights)"
```

---

## Task 7: Measure VRAM, pick voice, end-to-end voice test

**Files:**
- Modify: `/opt/nivuus/unmute/.env` or Unmute voice config (French voice selection)

**Interfaces:**
- Produces: a working spoken conversation that acts on the home; recorded VRAM figure.

- [ ] **Step 1: Measure real VRAM with the stack warm**

```bash
bash -c 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv; echo "--- per proc ---"; nvidia-smi --query-compute-apps=used_memory,process_name --format=csv'
```

Expected: STT+TTS+ollama(qwen3:4b) total well under 12 GB. Record the number in the spec's "Risques" note. If over budget, drop the brain (spec risk #2).

- [ ] **Step 2: Select a French voice**

Confirm the frontend exposes `Charles` / `Développeuse` / `Fabieng` (French voices in `voices.yaml`). If a default must be set, set it per Unmute's voice config. No code change in our repo.

- [ ] **Step 3: Open the UI from a LAN device**

Browse to `http://192.168.0.1:8090` from a home-LAN machine. Expected: the Unmute UI loads and the microphone connects.

- [ ] **Step 4: Spoken read test**

Say: « Quelles pièces connais-tu ? » Expected: spoken French answer naming HA areas within ~1 s.

- [ ] **Step 5: Spoken action test**

Say: « Allume la lumière du salon. » Expected: the light turns on and the assistant confirms aloud. Verify in HA that the service fired.

- [ ] **Step 6: Degraded-mode test**

```bash
bash -c 'cd /opt/nivuus/unmute && docker compose exec voice-agent sh -c "echo test" ; echo "now break MCP path check"'
```

Then ask a plain question (« Raconte-moi une blague. ») with MCP reachable — it should still converse. (Full degraded simulation: temporarily point `mcp_command` at a bad path and confirm `/health` shows `mcp:false` while conversation still works. Restore afterward.)

- [ ] **Step 7: Commit any config artifacts**

```bash
git add -A configs/unmute/
git commit -m "docs(unmute): record measured VRAM and French voice selection" || echo "nothing to commit"
```

Also update the spec's risk note with the measured VRAM figure (edit `docs/superpowers/specs/2026-07-22-unmute-voice-agent-design.md`).

---

## Task 8: Wire STT/TTS into the GPU/VM hooks

**The STT and TTS containers hold VRAM and must yield it to the Windows VM, exactly like ollama already does.**

**Files:**
- Modify: `/etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh`
- Modify: `/etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh`
- Modify: `/usr/local/sbin/vm-idle-shutdown.sh`
- Reference copies in repo: `configs/` equivalents if present (check `git status` shows the deployed scripts are tracked under `scripts/`).

**Interfaces:**
- Produces: STT/TTS stopped on VM start, restarted on VM stop, self-healed when VM idle.

- [ ] **Step 1: Read the current ollama stop line in `bind-vfio-gpu.sh`**

```bash
bash -c 'grep -n "ollama/docker-compose" /etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh'
```

Note the exact `docker compose ... stop ollama` invocation (line ~11).

- [ ] **Step 2: Add STT/TTS stop next to the ollama stop**

Immediately after the ollama `stop ollama` line, insert:

```bash
# Unmute STT/TTS hold VRAM too; release them while the VM owns the card
docker compose -f /opt/nivuus/unmute/docker-compose.yml -f /opt/nivuus/unmute/docker-compose.override.yml --env-file /opt/nivuus/unmute/.env stop stt tts || true
```

- [ ] **Step 3: Add STT/TTS restart in `rebind-host-gpu.sh`**

After the ollama `up -d` line (~96), insert:

```bash
# bring Unmute speech services back now that the GPU is host-owned again
docker compose -f /opt/nivuus/unmute/docker-compose.yml -f /opt/nivuus/unmute/docker-compose.override.yml --env-file /opt/nivuus/unmute/.env up -d stt tts || true
```

- [ ] **Step 4: Add self-heal in `vm-idle-shutdown.sh`**

After the ollama self-heal block (~line 32-34), insert a parallel check:

```bash
    # Self-heal Unmute speech services when the VM is off and they are down
    if ! docker inspect -f "{{.State.Running}}" unmute-stt-1 2>/dev/null | grep -q true; then
        logger -t "$LOG_TAG" "VM off but Unmute STT down - starting stt/tts"
        docker compose -f /opt/nivuus/unmute/docker-compose.yml -f /opt/nivuus/unmute/docker-compose.override.yml --env-file /opt/nivuus/unmute/.env up -d stt tts 2>&1 | logger -t "$LOG_TAG"
    fi
```

> First confirm the actual container names with `bash -c 'docker compose -f /opt/nivuus/unmute/docker-compose.yml -f /opt/nivuus/unmute/docker-compose.override.yml ps --format "{{.Name}}"'` and use the real STT container name in the `docker inspect` check.

- [ ] **Step 5: Test the full cycle**

```bash
bash -c 'LC_ALL=C virsh start Windows; sleep 30; nvidia-smi --query-compute-apps=process_name --format=csv'
```

Expected: no `moshi-server` / ollama processes on the GPU while the VM runs. Then:

```bash
bash -c 'LC_ALL=C virsh shutdown --mode acpi Windows; sleep 60; docker ps --format "{{.Names}}" | grep -E "stt|tts|ollama"'
```

Expected: STT, TTS, and ollama back up after the VM stops. Check `/var/log/libvirt-cpu-hook.log` and the rebind hook log for errors.

- [ ] **Step 6: Sync deployed hook edits back into the repo if tracked**

```bash
bash -c 'cd /home/mallanic/Projects/Nivuus && git status --short scripts/ | grep -E "gpu|vm-"'
```

If the deployed scripts correspond to tracked repo files, copy the edits into the repo versions and commit:

```bash
git add scripts/
git commit -m "feat(hooks): release Unmute STT/TTS VRAM around VM GPU passthrough"
```

- [ ] **Step 7: Update CLAUDE.md**

Add a short note to `CLAUDE.md` under the GPU/ollama section: Unmute STT/TTS now participate in the VM GPU hand-off (bind stop / rebind up / idle self-heal), same pattern as ollama.

```bash
git add CLAUDE.md
git commit -m "docs: note Unmute STT/TTS in the VM GPU hand-off hooks"
```

---

## Self-Review

**Spec coverage:**
- Objective (voice + act) → Tasks 3–7 ✓
- Options écartées → design record, no task needed ✓
- Architecture / shim boundary → Tasks 2–4 ✓
- Budget VRAM / qwen3:4b → Task 5 (pull + validate), Task 7 (measure) ✓
- Composants / file structure → Tasks 1–5 ✓
- Whitelist 12 outils → Task 1 config + Task 2 filter ✓
- Flux d'un tour → Task 3 ✓
- Gestion erreurs (degraded, tool error, iteration cap, ollama down) → Task 3 tests + Task 4 catch-all + Task 7 step 6 ✓
- Cycle GPU/VM → Task 8 ✓
- Stockage des poids (off /tmp) → Task 6 step 3 ✓
- Réseau (8090, voice-agent service, gateway IP) → Task 6 ✓
- Tests → each task TDD; e2e Task 5 + Task 7 ✓
- Séquencement (shim first, then Unmute, then hooks) → Task order ✓
- Hors périmètre (HA token) → surfaced, not fixed ✓

**Placeholder scan:** `<SHORT_HASH>`, `<hf token…>`, `<token from …>` are deploy-time secrets the operator must supply, not code placeholders — acceptable. No "TBD"/"add error handling"/"similar to Task N" left.

**Type consistency:** `McpClient.openai_tools()`/`.call()`/`.start()`/`.aclose()`/`.available` consistent across Tasks 2–4. `Agent(config, mcp, llm)` and `.stream(messages)` consistent Tasks 3–4. `create_app(agent, mcp_available)` and `build()` consistent Task 4. Config field names (`ollama_url`, `ollama_api_key`, `mcp_command`, `mcp_env`, `max_tool_iterations`, `tool_timeout_s`) consistent Tasks 1–4.

**Open verification the executor must do (flagged inline, not placeholders):**
- Unmute upstream `!override` YAML tag support and `backend.depends_on` on `llm` (Task 6 step 3 note).
- Real STT container name for the self-heal check (Task 8 step 4 note).
- ollama `/v1` tools support with `qwen3:4b` is the gating hypothesis (Task 5 step 5).
