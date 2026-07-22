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
