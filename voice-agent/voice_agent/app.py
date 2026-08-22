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
    # Mutable holder so /health can reflect live state (e.g. updated by
    # build()'s startup handler after mcp.start()) instead of a bool
    # captured by value at create_app time.
    app.state.mcp_available = mcp_available

    @app.get("/health")
    async def health():
        return {"status": "ok", "mcp": app.state.mcp_available}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        model = body.get("model", "qwen3:4b")
        messages = body["messages"]

        async def gen():
            try:
                async for delta in agent.stream(messages):
                    yield _chunk(delta, model)
            except Exception:  # never leave the voice silent
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
        # Reflect the real, live MCP availability on /health now that
        # start() has run (mcp_available=False above was just the
        # pre-startup default).
        app.state.mcp_available = mcp.available

    @app.on_event("shutdown")
    async def _shutdown():
        await mcp.aclose()

    return app
