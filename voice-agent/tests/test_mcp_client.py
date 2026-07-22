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
    c._timeout = 5.0
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
