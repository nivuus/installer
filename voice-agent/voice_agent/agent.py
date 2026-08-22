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

        for _ in range(self._cfg.max_tool_iterations):
            calls: dict = {}
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
                        continue
                    # Latency-first design decision: stream content as it arrives
                    # while no tool call has appeared this turn. Once a tool call
                    # is seen, suppress any further content (tool turns normally
                    # carry no content). Fully buffering the turn would delay the
                    # TTS, which is the whole point of this shim.
                    if calls:
                        continue
                    if getattr(delta, "content", None):
                        yield delta.content

            if not calls:
                return  # plain answer already streamed

            convo.append(_assistant_tool_msg(calls))
            for tc in _ordered(calls):
                convo.append(await self._run_tool(tc))

        # iteration cap reached: force a final non-tool answer
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
            log.warning("tool %s: invalid JSON args, using {}", tc["name"])
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
