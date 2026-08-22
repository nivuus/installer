import json
import pytest
from httpx import ASGITransport, AsyncClient

import voice_agent.app as appmod


class StubAgent:
    async def stream(self, messages):
        for piece in ["Bonjour", " !"]:
            yield piece


class BoomAgent:
    async def stream(self, messages):
        yield "partial"
        raise RuntimeError("llm exploded")
        yield "unreachable"  # noqa


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


async def test_health_reflects_false_when_mcp_unavailable():
    app = appmod.create_app(agent=StubAgent(), mcp_available=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.json() == {"status": "ok", "mcp": False}


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


async def test_chat_completions_falls_back_to_french_on_agent_error():
    app = appmod.create_app(agent=BoomAgent(), mcp_available=True)
    transport = ASGITransport(app=app)
    body = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "salut"}], "stream": True}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/v1/chat/completions", json=body) as r:
            assert r.status_code == 200
            lines = [ln async for ln in r.aiter_lines()]
    payloads = [ln[len("data: "):] for ln in lines if ln.startswith("data: ")]
    assert payloads[-1] == "[DONE]"
    texts = []
    for p in payloads[:-1]:
        obj = json.loads(p)
        texts.append(obj["choices"][0]["delta"].get("content", ""))
    full = "".join(texts)
    assert "partial" in full
    assert "Désolé, une erreur est survenue." in full


async def test_build_wires_health_to_live_mcp_state(monkeypatch):
    """build() must reflect the REAL mcp.available after startup, not a
    hardcoded/stale value captured at create_app time (brief bug fix)."""

    class FakeMcp:
        def __init__(self, *a, **kw):
            self.available = False

        async def start(self):
            self.available = True  # simulate MCP coming up successfully

        async def aclose(self):
            pass

    class FakeConfig:
        model = "qwen3:4b"
        ollama_url = "http://127.0.0.1:11435"
        ollama_api_key = "x"
        mcp_command = ["true"]
        mcp_env = {}
        whitelist = []
        tool_timeout_s = 5.0

    monkeypatch.setattr(appmod, "load_config", lambda: FakeConfig())
    monkeypatch.setattr(appmod, "McpClient", FakeMcp)
    monkeypatch.setattr(appmod, "AsyncOpenAI", lambda **kw: object())
    monkeypatch.setattr(appmod, "Agent", lambda cfg, mcp, llm: StubAgent())

    app = appmod.build()
    transport = ASGITransport(app=app)
    # httpx's ASGITransport does not drive the ASGI lifespan protocol, so
    # startup must be triggered explicitly to exercise the real @app.on_event
    # ("startup") handler that calls mcp.start().
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/health")
            assert r.json() == {"status": "ok", "mcp": True}
