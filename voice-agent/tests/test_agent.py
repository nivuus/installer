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
        self.seen_messages = []
    async def create(self, **kwargs):
        # Snapshot the messages sent on this call so tests can assert on
        # what was actually relayed to the LLM (e.g. tool result reinjection).
        self.seen_messages.append(list(kwargs["messages"]))
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
    llm = FakeLLM([tool_stream, final_stream])
    agent = make_agent(llm, mcp)
    out = await _collect(agent, [{"role": "user", "content": "allume la lumière"}])
    assert out == "C'est allumé."
    assert mcp.calls == [("call_service", {"domain": "light", "service": "turn_on"})]

    # The re-call to the LLM must actually carry the assistant tool_calls
    # message and the tool's result, not just "not crash".
    second_call_messages = llm.seen_messages[1]
    assistant_msgs = [m for m in second_call_messages
                       if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant_msgs) == 1
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[0]["content"] == "success"


async def test_tool_error_reinjected_not_fatal():
    tool_stream = FakeStream([
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(0, id="c1", name="call_service", args='{}')]))]),
        Chunk([Choice(Delta(), finish_reason="tool_calls")]),
    ])
    final_stream = FakeStream([
        Chunk([Choice(Delta(content="Désolé, l'action a échoué."), finish_reason="stop")]),
    ])
    mcp = FakeMcp(raises=McpToolError("boom"))
    llm = FakeLLM([tool_stream, final_stream])
    agent = make_agent(llm, mcp)
    out = await _collect(agent, [{"role": "user", "content": "allume"}])
    assert "échoué" in out
    assert mcp.calls  # tool was attempted

    # Prove the error was actually re-injected as the tool result on the
    # re-call, not merely that the run didn't raise.
    second_call_messages = llm.seen_messages[1]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert "boom" in tool_msgs[0]["content"]


async def test_arguments_accumulated_across_chunks():
    tool_stream = FakeStream([
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(
            0, id="c1", name="call_service", args='{"domain":"light",')]))]),
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(
            0, args='"service":"turn_on"}')]))]),
        Chunk([Choice(Delta(), finish_reason="tool_calls")]),
    ])
    final_stream = FakeStream([
        Chunk([Choice(Delta(content="OK."), finish_reason="stop")]),
    ])
    mcp = FakeMcp(result="success")
    agent = make_agent(FakeLLM([tool_stream, final_stream]), mcp)
    out = await _collect(agent, [{"role": "user", "content": "allume la lumière"}])
    assert out == "OK."
    assert mcp.calls == [("call_service", {"domain": "light", "service": "turn_on"})]


async def test_iteration_cap_forces_final_answer():
    tool_stream = FakeStream([
        Chunk([Choice(Delta(tool_calls=[ToolCallDelta(0, id="c1", name="call_service", args='{}')]))]),
        Chunk([Choice(Delta(), finish_reason="tool_calls")]),
    ])
    final_stream = FakeStream([
        Chunk([Choice(Delta(content="Voici la réponse finale."), finish_reason="stop")]),
    ])
    mcp = FakeMcp(result="ok")
    from voice_agent.config import Config
    cfg = Config(model="m", ollama_url="u", ollama_api_key="k", whitelist=[],
                 system_prompt="sys", mcp_command=[], mcp_env={},
                 max_tool_iterations=1, tool_timeout_s=5.0)
    llm = FakeLLM([tool_stream, final_stream])
    agent = Agent(cfg, mcp, llm)
    out = await _collect(agent, [{"role": "user", "content": "allume"}])
    assert out == "Voici la réponse finale."
    assert len(mcp.calls) == 1
