from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from leadqual.config import LlmSettings
from leadqual.llm import SUBMIT_TOOL, ClaudeLlm, LlmError, Tool, ToolError

SETTINGS = LlmSettings(model="test-model", effort="low", max_agent_steps=3, max_web_searches=2)


class Answer(BaseModel):
    value: int


def tool_use(name: str, payload: dict, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id=call_id)


def text(content: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=content)


def reply(*blocks: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, usage=usage)


class FakeClient:
    """Stands in for anthropic.Anthropic: replays scripted replies and records requests."""

    def __init__(self, *replies) -> None:
        self._replies = list(replies)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        next_reply = self._replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


def run(client: FakeClient, **kwargs) -> Answer:
    llm = ClaudeLlm(SETTINGS, client=client)
    return llm.run(system="system", prompt="question", result_type=Answer, **kwargs)


def test_submitted_result_is_validated_and_returned():
    client = FakeClient(reply(tool_use(SUBMIT_TOOL, {"value": 42})))

    assert run(client) == Answer(value=42)


def test_tool_is_called_and_its_output_returned_to_the_model():
    echo = Tool("echo", "Echo.", {"type": "object"}, lambda args: f"echo:{args['text']}")
    client = FakeClient(
        reply(tool_use("echo", {"text": "hi"})),
        reply(tool_use(SUBMIT_TOOL, {"value": 1})),
    )

    run(client, tools=[echo])

    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["content"] == "echo:hi"
    assert tool_result["is_error"] is False


def test_failing_tool_reports_the_error_to_the_model_instead_of_crashing():
    def broken(_args: dict) -> str:
        raise ToolError("page not found")

    client = FakeClient(
        reply(tool_use("fetch", {})),
        reply(tool_use(SUBMIT_TOOL, {"value": 1})),
    )

    run(client, tools=[Tool("fetch", "Fetch.", {"type": "object"}, broken)])

    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result == {
        "type": "tool_result",
        "tool_use_id": "call_1",
        "content": "page not found",
        "is_error": True,
    }


def test_invalid_result_is_sent_back_for_correction():
    client = FakeClient(
        reply(tool_use(SUBMIT_TOOL, {"value": "not a number"})),
        reply(tool_use(SUBMIT_TOOL, {"value": 7})),
    )

    assert run(client) == Answer(value=7)
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_model_that_answers_in_prose_is_asked_to_submit():
    client = FakeClient(
        reply(text("The answer is 3."), stop_reason="end_turn"),
        reply(tool_use(SUBMIT_TOOL, {"value": 3})),
    )

    assert run(client) == Answer(value=3)
    assert SUBMIT_TOOL in client.requests[1]["messages"][-1]["content"]


def test_paused_server_tool_turn_is_resumed():
    client = FakeClient(
        reply(text("searching"), stop_reason="pause_turn"),
        reply(tool_use(SUBMIT_TOOL, {"value": 5})),
    )

    assert run(client) == Answer(value=5)
    assert client.requests[1]["messages"][-1]["role"] == "assistant"


def test_running_out_of_steps_is_an_error_not_a_pass():
    client = FakeClient(*[reply(text("hm"), stop_reason="end_turn")] * 3)

    with pytest.raises(LlmError, match="after 3 steps"):
        run(client)


def test_refusal_is_an_error_not_a_pass():
    client = FakeClient(reply(stop_reason="refusal"))

    with pytest.raises(LlmError, match="declined"):
        run(client)


def test_web_search_is_offered_only_when_asked_for():
    client = FakeClient(
        reply(tool_use(SUBMIT_TOOL, {"value": 1})),
        reply(tool_use(SUBMIT_TOOL, {"value": 1})),
    )

    run(client)
    run(client, web_search=True)

    without, with_search = (
        [tool["name"] for tool in request["tools"]] for request in client.requests
    )
    assert without == [SUBMIT_TOOL]
    assert with_search == [SUBMIT_TOOL, "web_search"]


def test_usage_is_accumulated_across_requests():
    client = FakeClient(
        reply(text("hm"), stop_reason="end_turn"),
        reply(tool_use(SUBMIT_TOOL, {"value": 1})),
    )
    llm = ClaudeLlm(SETTINGS, client=client)

    llm.run(system="s", prompt="p", result_type=Answer)

    assert (llm.usage.requests, llm.usage.input_tokens, llm.usage.output_tokens) == (2, 20, 10)


def api_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
        "error", response=httpx.Response(status, request=request), body=None
    )


@pytest.mark.parametrize("status", [429, 500, 529])
def test_transient_api_failure_becomes_an_llm_error(status):
    with pytest.raises(LlmError, match=str(status)):
        run(FakeClient(api_error(status)))


@pytest.mark.parametrize("status", [400, 401])
def test_configuration_errors_are_not_swallowed(status):
    with pytest.raises(anthropic.APIStatusError):
        run(FakeClient(api_error(status)))


def search_result(*urls: str) -> SimpleNamespace:
    results = [SimpleNamespace(type="web_search_result", url=url, title="t") for url in urls]
    return SimpleNamespace(type="web_search_tool_result", content=results)


def test_the_urls_the_web_search_returned_are_handed_back():
    cited = SimpleNamespace(type="web_search_result_location", url="https://c.example/page")
    client = FakeClient(
        reply(search_result("https://a.example/", "https://b.example/x"), stop_reason="pause_turn"),
        reply(
            SimpleNamespace(type="text", text="found it", citations=[cited]),
            tool_use(SUBMIT_TOOL, {"value": 1}),
        ),
    )
    urls: list[str] = []

    run(client, web_search=True, search_urls=urls)

    assert urls == ["https://a.example/", "https://b.example/x", "https://c.example/page"]


def test_a_failed_web_search_contributes_no_urls():
    failed = SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(
            type="web_search_tool_result_error", error_code="max_uses_exceeded"
        ),
    )
    client = FakeClient(reply(failed, tool_use(SUBMIT_TOOL, {"value": 1})))
    urls: list[str] = []

    run(client, web_search=True, search_urls=urls)

    assert urls == []
