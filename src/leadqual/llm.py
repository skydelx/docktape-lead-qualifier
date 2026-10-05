"""The single place that talks to Claude.

Every LLM step in the pipeline is the same small agent loop: the model may call
the tools it was given, and must finish by calling `submit_result` with an
answer that validates against a pydantic model.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel, ValidationError

from leadqual.config import LlmSettings

log = logging.getLogger(__name__)

SUBMIT_TOOL = "submit_result"
MAX_TOKENS = 16_000
# If a safety classifier declines a request, the API re-runs it on Anthropic's
# recommended fallback model instead of failing the lead.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmError(RuntimeError):
    """The model produced no usable result; the caller must not treat this as a pass."""


class ToolError(Exception):
    """A tool could not do what the model asked; the message is shown to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Llm(Protocol):
    def run[T: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        result_type: type[T],
        tools: Sequence[Tool] = (),
        web_search: bool = False,
        search_urls: list[str] | None = None,
    ) -> T: ...


class ClaudeLlm:
    def __init__(self, settings: LlmSettings, client: anthropic.Anthropic | None = None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic()
        self.usage = Usage()

    def run[T: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        result_type: type[T],
        tools: Sequence[Tool] = (),
        web_search: bool = False,
        search_urls: list[str] | None = None,
    ) -> T:
        """Run the agent loop. Every URL the web search actually returned is appended to
        `search_urls`, so the caller can check a cited source against it."""
        handlers = {tool.name: tool.handler for tool in tools}
        definitions = self._tool_definitions(tools, result_type, web_search)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        for _ in range(self._settings.max_agent_steps):
            response = self._request(system, messages, definitions)
            messages.append({"role": "assistant", "content": response.content})
            if search_urls is not None:
                search_urls.extend(_search_result_urls(response.content))
            if response.stop_reason == "pause_turn":  # a server tool is still running
                continue

            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                nudge = f"Call the {SUBMIT_TOOL} tool with your final answer."
                messages.append({"role": "user", "content": nudge})
                continue

            results = []
            for call in calls:
                if call.name == SUBMIT_TOOL:
                    try:
                        return result_type.model_validate(call.input)
                    except ValidationError as error:
                        results.append(_tool_result(call.id, f"Invalid result: {error}", True))
                else:
                    results.append(_run_tool(handlers, call))
            messages.append({"role": "user", "content": results})

        raise LlmError(f"no valid result after {self._settings.max_agent_steps} steps")

    def _tool_definitions(
        self, tools: Sequence[Tool], result_type: type[BaseModel], web_search: bool
    ) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in tools
        ]
        definitions.append(
            {
                "name": SUBMIT_TOOL,
                "description": "Submit your final answer. Call this exactly once, when done.",
                "input_schema": result_type.model_json_schema(),
            }
        )
        if web_search:
            definitions.append(
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": self._settings.max_web_searches,
                }
            )
        return definitions

    def _request(
        self, system: str, messages: list[dict[str, Any]], definitions: list[dict[str, Any]]
    ) -> Any:
        try:
            response = self._client.beta.messages.create(
                model=self._settings.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=definitions,
                output_config={"effort": self._settings.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as error:
            raise LlmError(f"could not reach the Claude API: {type(error).__name__}") from error
        except anthropic.APIStatusError as error:
            # Rate limits and server errors are transient (the SDK already retried).
            # Anything else is a bug or a bad key and should stop the run loudly.
            if error.status_code == 429 or error.status_code >= 500:
                raise LlmError(f"Claude API unavailable: HTTP {error.status_code}") from error
            raise

        self.usage.requests += 1
        self.usage.input_tokens += response.usage.input_tokens
        self.usage.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise LlmError("the model declined the request")
        return response


def _search_result_urls(content: Sequence[Any]) -> list[str]:
    """The URLs of the web search results in one response, and of any citations of them."""
    urls = []
    for block in content:
        if block.type == "web_search_tool_result":
            results = block.content
            if isinstance(results, list):  # an error arrives as a single object instead
                urls.extend(result.url for result in results if getattr(result, "url", None))
        for citation in getattr(block, "citations", None) or ():
            if getattr(citation, "url", None):
                urls.append(citation.url)
    return urls


def _run_tool(handlers: dict[str, Callable[[dict[str, Any]], str]], call: Any) -> dict[str, Any]:
    handler = handlers.get(call.name)
    if handler is None:
        return _tool_result(call.id, f"Unknown tool: {call.name}", True)
    try:
        return _tool_result(call.id, handler(call.input), False)
    except ToolError as error:
        log.info("tool %s failed: %s", call.name, error)
        return _tool_result(call.id, str(error), True)


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }
