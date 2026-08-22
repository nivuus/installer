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
        if name not in self._whitelist:
            raise McpToolError(f"outil {name} non autorisé")
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

        if getattr(result, "isError", False):
            try:
                parts = [c.text for c in result.content if getattr(c, "text", None)]
            except Exception:
                parts = []
            message = "\n".join(parts) or f"outil {name} : échec"
            raise McpToolError(message)

        try:
            parts = [c.text for c in result.content if getattr(c, "text", None)]
        except Exception as e:
            raise McpToolError(f"outil {name} : réponse invalide ({e})")
        return "\n".join(parts)

    async def __aenter__(self) -> "McpClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._stack.aclose()
